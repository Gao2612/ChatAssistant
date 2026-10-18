"""Background capture workers for non-active WeChat sources (T10).

Only used when the user explicitly enables "background monitoring"; each monitored
source gets its own capture child process (the existing worker protocol) and its
own state in :class:`SourceRegistry`. This module never moves, activates or types
into a background window; it only drains its capture/OCR packets. The model queue
stays globally limited, so a background source does not create extra model load.
"""
from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass

from .contracts import SourceKey

MAX_BACKGROUND_WORKERS = 2


@dataclass(frozen=True)
class MonitorEvent:
    source: SourceKey
    kind: str
    packet: dict


class BackgroundSourceMonitor:
    """Manage one capture child process per enabled non-active source."""

    def __init__(self, registry, scheduler, *, worker_factory=None,
                 max_workers: int = MAX_BACKGROUND_WORKERS, clock=None) -> None:
        if worker_factory is not None and not callable(worker_factory):
            raise TypeError("worker_factory 必须可调用。")
        self.registry = registry
        self.scheduler = scheduler
        self.worker_factory = worker_factory
        self.max_workers = max(1, int(max_workers))
        self.clock = clock or time.monotonic
        self._workers: dict[SourceKey, dict] = {}
        self._events: "queue.Queue[MonitorEvent]" = queue.Queue()
        self._lock = threading.RLock()

    # ------------------------------------------------------------ lifecycle
    def running_sources(self) -> list[SourceKey]:
        with self._lock:
            return sorted(self._workers, key=lambda source: (source.pid, source.hwnd))

    def is_running(self, source: SourceKey) -> bool:
        with self._lock:
            return source in self._workers

    def enable(self, source: SourceKey, enabled: bool) -> bool:
        """Start/stop background capture for one managed source."""
        if not self.registry.enable(source, enabled):
            return False
        if enabled:
            return self.start(source)
        self.stop(source)
        return True

    def start(self, source: SourceKey) -> bool:
        info = self.registry.get(source)
        if info is None or not info.present or not info.managed:
            return False
        if info.active:
            return False  # the active source is captured by the foreground path
        with self._lock:
            if source in self._workers:
                return True
            if len(self._workers) >= self.max_workers:
                return False
            worker = self._spawn(source)
            if worker is None:
                return False
            self._workers[source] = worker
            return True

    def _spawn(self, source: SourceKey):
        """Create queue/process/pump; injection point for tests."""
        if self.worker_factory is not None:
            worker = self.worker_factory(source)
            if worker is None:
                return None
            if isinstance(worker, dict):
                return worker
            queue_obj, process = worker
            return {"queue": queue_obj, "process": process, "stop": threading.Event()}
        try:
            import multiprocessing as mp

            from .worker import run as capture_run
            from .discovery import WindowTarget
        except ImportError:
            return None
        hwnd = WindowTarget(source.hwnd, source.pid, source.exe_path,
                            source.process_started)
        packets = mp.Queue(maxsize=8)
        stop_event = mp.Event()
        process = mp.Process(target=capture_run,
                             args=(packets, stop_event, hwnd, f"bg-{source.hwnd}",
                                   None, "ocr"), daemon=True)
        process.start()
        return {"queue": packets, "process": process, "stop": stop_event}

    def stop(self, source: SourceKey) -> bool:
        with self._lock:
            worker = self._workers.pop(source, None)
        if worker is None:
            return False
        stop = worker.get("stop")
        try:
            if stop is not None:
                stop.set()
        except Exception:  # noqa: BLE001 - best-effort stop
            pass
        process = worker.get("process")
        try:
            if process is not None:
                if hasattr(process, "join"):
                    process.join(timeout=3)
                if hasattr(process, "is_alive") and process.is_alive():
                    process.terminate()
                    process.join(timeout=2)
        except Exception:  # noqa: BLE001 - teardown must not mask stop
            pass
        return True

    def stop_all(self) -> None:
        for source in list(self.running_sources()):
            self.stop(source)

    # ----------------------------------------------------------------- poll
    def poll(self, *, limit: int = 50) -> list[MonitorEvent]:
        """Drain worker packets, update registry/scheduler, return new events."""
        drained: list[MonitorEvent] = []
        with self._lock:
            workers = list(self._workers.items())
        for source, worker in workers:
            packets = worker.get("queue")
            while len(drained) < limit:
                try:
                    packet = packets.get_nowait()
                except Exception:  # noqa: BLE001 - Empty and closed queues
                    break
                if not isinstance(packet, dict):
                    continue
                kind = str(packet.get("kind") or "")
                if kind in ("frame", "snapshot", "read_frame"):
                    self.registry.observe_frame(source, int(packet.get("frame_seq") or 0))
                if kind == "snapshot":
                    self.registry.mark_ocr(source)
                    self.scheduler.observe_frame(
                        source, int(packet.get("frame_seq") or 0))
                    self.scheduler.complete(
                        source, frame_seq=int(packet.get("frame_seq") or 0))
                event = MonitorEvent(source=source, kind=kind, packet=packet)
                drained.append(event)
        return drained
