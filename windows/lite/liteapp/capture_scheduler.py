"""OCR scheduling for multiple WeChat sources (T10).

Only one OCR attempt runs at a time (the model queue already has a global limit of
one). The active source has priority, background sources are opt-in, minimized or
hidden sources are paused, and a source with no newer frame is never re-OCR'd.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from .contracts import SourceKey
from .source_registry import source_sort_key

ACTIVE_INTERVAL = 0.8
BACKGROUND_INTERVAL = 2.5


@dataclass(frozen=True)
class CaptureJob:
    source: SourceKey
    priority: str  # "active" | "background"
    frame_seq: int
    reason: str


class CaptureScheduler:
    """Decide which source may run OCR next; never touches windows itself."""

    def __init__(self, registry, *, clock=None, active_interval: float = ACTIVE_INTERVAL,
                 background_interval: float = BACKGROUND_INTERVAL) -> None:
        if active_interval <= 0 or background_interval <= 0:
            raise ValueError("调度间隔必须为正数。")
        self.registry = registry
        self.clock = clock or time.monotonic
        self.active_interval = float(active_interval)
        self.background_interval = float(background_interval)
        self._done_seq: dict[SourceKey, int] = {}
        self._last_done: dict[SourceKey, float] = {}
        self._scheduled: SourceKey | None = None

    # --------------------------------------------------------------- inputs
    def observe_frame(self, source: SourceKey, frame_seq: int, *, at: float | None = None) -> None:
        self.registry.observe_frame(source, frame_seq)

    def enable(self, source: SourceKey, enabled: bool) -> bool:
        return self.registry.enable(source, enabled)

    def activate(self, source: SourceKey | None) -> None:
        self.registry.set_active(source)

    def set_minimized(self, source: SourceKey, minimized: bool) -> None:
        self.registry.mark_minimized(source, minimized)

    # ------------------------------------------------------------- schedule
    def pending(self, *, now: float | None = None) -> list[CaptureJob]:
        moment = self.clock() if now is None else float(now)
        jobs: list[CaptureJob] = []
        for info in self.registry.managed_sources():
            if not info.present or info.minimised:
                continue
            if not info.active and not info.enabled:
                continue  # background monitoring is opt-in
            if info.frame_seq <= self._done_seq.get(info.source, 0):
                continue  # no newer frame
            interval = self.active_interval if info.active else self.background_interval
            last = self._last_done.get(info.source)
            if last is not None and moment - last < interval:
                continue
            jobs.append(CaptureJob(source=info.source,
                                   priority="active" if info.active else "background",
                                   frame_seq=info.frame_seq,
                                   reason="active_priority" if info.active else "background_enabled"))
        jobs.sort(key=lambda job: (job.priority != "active",
                                   self._last_done.get(job.source, -1.0),
                                   job.source))
        return jobs

    def next_job(self, *, now: float | None = None) -> CaptureJob | None:
        jobs = self.pending(now=now)
        return jobs[0] if jobs else None

    def take_next(self, *, now: float | None = None) -> CaptureJob | None:
        job = self.next_job(now=now)
        if job is not None:
            self._scheduled = job.source
        return job

    def complete(self, source: SourceKey, *, frame_seq: int | None = None,
                 now: float | None = None) -> None:
        moment = self.clock() if now is None else float(now)
        info = self.registry.get(source)
        seq = info.frame_seq if frame_seq is None and info is not None else int(frame_seq or 0)
        self._done_seq[source] = max(self._done_seq.get(source, 0), seq)
        self._last_done[source] = moment
        self.registry.mark_ocr(source)
        if self._scheduled == source:
            self._scheduled = None

    def cancel_scheduled(self) -> None:
        self._scheduled = None

    @property
    def scheduled(self) -> SourceKey | None:
        return self._scheduled

    def last_done_at(self, source: SourceKey) -> float | None:
        return self._last_done.get(source)
