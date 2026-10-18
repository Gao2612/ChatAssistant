"""Single-flight model task scheduler.

T3 of ``docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md``. Design rules:

* at most **1** model request in flight for the whole application;
* same source (scope) keeps only the **latest** pending realtime task — an older
  pending task is superseded and its result is never delivered;
* realtime tasks from different sources rotate fairly (FIFO across sources);
* history tasks are lower priority but may not starve: after yielding to 5
  realtime tasks they get exactly one batch, then the counter resets;
* quota limits pause the queue instead of hammering the service; authorization
  loss cancels every pending task of that registration/profile;
* completion callbacks are **not** called from the scheduling thread directly —
  they go through the caller's ``dispatch`` hook (the Qt integration passes a
  hook that posts to the main thread).

The gateway is injected, so the scheduler is fully testable with a scripted
double and an injected clock (see ``tests/test_inference_queue.py``).
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Any, Callable

from .contracts import CancellationToken, CancelledError
from .inference_schemas import (HISTORY_KINDS, InferenceAuthError,
                                InferenceQuotaError, InferenceRequest)

PRIORITY_REALTIME = "realtime"
PRIORITY_HISTORY = "history"
PRIORITIES = (PRIORITY_REALTIME, PRIORITY_HISTORY)
_TERMINAL_STATES = ("completed", "failed", "cancelled", "superseded")


class TaskHandle:
    """Caller-facing handle: cancel + completion callback + validated result."""

    def __init__(self, request: InferenceRequest, priority: str, scope_id: str,
                 created_at: float) -> None:
        self.request = request
        self.priority = priority
        self.scope_id = scope_id
        self.created_at = created_at
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.cancel_token = CancellationToken()
        self.state = "pending"
        self.result: Any = None
        self.error: BaseException | None = None
        self._callback: Callable[["TaskHandle"], None] | None = None
        self._done = threading.Event()
        self._queue: "InferenceQueue | None" = None
        self._lock = threading.RLock()

    # -- state ---------------------------------------------------------------
    @property
    def task_id(self) -> str:
        return self.request.task_id

    @property
    def done(self) -> bool:
        return self.state in _TERMINAL_STATES

    @property
    def cancelled(self) -> bool:
        return self.cancel_token.is_cancelled() or self.state == "cancelled"

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    def set_callback(self, callback: Callable[["TaskHandle"], None] | None) -> None:
        """Register a completion callback; invoked once through the queue dispatcher."""
        with self._lock:
            self._callback = callback

    def cancel(self) -> None:
        """Cancel this task. A running request stops via the shared cancel token."""
        self.cancel_token.cancel()
        queue = self._queue
        if queue is not None:
            queue._cancel_handle(self)
            return
        with self._lock:
            if self.state == "pending":
                self.state = "cancelled"
                self.error = CancelledError("model task cancelled")
                self.finished_at = time.monotonic()
                self._done.set()


class InferenceQueue:
    """Priority queue with a global in-flight limit of one model request."""

    def __init__(self, gateway: Any, *, auto_start: bool = False,
                 dispatch: Callable[[Callable[[], None]], None] | None = None,
                 clock: Callable[[], float] | None = None,
                 history_yield_limit: int = 5,
                 poll_interval: float = 0.05) -> None:
        if not hasattr(gateway, "run"):
            raise TypeError("gateway 必须提供 run(request, cancel)")
        if isinstance(history_yield_limit, bool) or not isinstance(history_yield_limit, int):
            raise TypeError("history_yield_limit 必须是整数。")
        if history_yield_limit < 1:
            raise ValueError("history_yield_limit 必须 >= 1。")
        self._gateway = gateway
        self._dispatch = dispatch or (lambda action: action())
        self._clock = clock or time.monotonic
        self._history_yield_limit = history_yield_limit
        self._poll_interval = max(0.01, float(poll_interval))
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._realtime: "OrderedDict[str, TaskHandle]" = OrderedDict()
        self._history: "deque[TaskHandle]" = deque()
        self._history_yields = 0
        self._paused = False
        self._pause_reason = ""
        self._running_handle: TaskHandle | None = None
        self._served: list[str] = []
        self._execution_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker: threading.Thread | None = None
        if auto_start:
            self.start()

    # -- properties ----------------------------------------------------------
    @property
    def paused(self) -> bool:
        with self._lock:
            return self._paused

    @property
    def pause_reason(self) -> str:
        with self._lock:
            return self._pause_reason

    @property
    def in_flight_count(self) -> int:
        with self._lock:
            return 1 if self._running_handle is not None else 0

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._realtime) + len(self._history)

    @property
    def running(self) -> bool:
        worker = self._worker
        return worker is not None and worker.is_alive()

    @property
    def served_order(self) -> list[str]:
        with self._lock:
            return list(self._served)

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._stop_event.clear()
            self._worker = threading.Thread(target=self._worker_loop,
                                            name="inference-queue", daemon=True)
            self._worker.start()

    def stop(self) -> None:
        """Cancel pending/running tasks and stop the worker (idempotent)."""
        self._stop_event.set()
        cancelled: list[TaskHandle] = []
        with self._lock:
            running = self._running_handle
            if running is not None:
                running.cancel_token.cancel()
            for handle in list(self._realtime.values()) + list(self._history):
                self._finish_locked(handle, "cancelled",
                                    CancelledError("inference queue stopped"))
                cancelled.append(handle)
            self._realtime.clear()
            self._history.clear()
            self._wake.notify_all()
        for handle in cancelled:
            self._dispatch_done(handle)
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5)
        with self._lock:
            self._worker = None

    # -- submission ----------------------------------------------------------
    def submit(self, request: InferenceRequest, priority: str | None = None, *,
               scope_id: str | None = None) -> TaskHandle:
        if not isinstance(request, InferenceRequest):
            raise TypeError("request 必须是 InferenceRequest")
        expected_priority = (PRIORITY_HISTORY if request.kind in HISTORY_KINDS
                             else PRIORITY_REALTIME)
        if priority is None:
            priority = expected_priority
        if priority not in PRIORITIES:
            raise ValueError(f"未知的任务优先级：{priority!r}")
        if priority != expected_priority:
            raise ValueError("任务优先级与任务类型不匹配。")
        scope = scope_id
        if not isinstance(scope, str) or not scope.strip():
            candidate = request.input_data.get("scope_id")
            scope = candidate if isinstance(candidate, str) and candidate.strip() else request.version_key
        handle = TaskHandle(request, priority, scope, self._clock())
        handle._queue = self
        superseded: TaskHandle | None = None
        with self._lock:
            if priority == PRIORITY_REALTIME:
                superseded = self._realtime.get(scope)
                if superseded is not None:
                    self._realtime.pop(scope, None)
                    self._finish_locked(superseded, "superseded",
                                        CancelledError("同来源已提交更新任务，旧任务作废"))
                running = self._running_handle
                if (running is not None and running.priority == PRIORITY_REALTIME
                        and running.scope_id == scope):
                    # The previous frame's in-flight result must not be filled back.
                    running.cancel_token.cancel()
                self._realtime[scope] = handle
            else:
                self._history.append(handle)
            self._wake.notify_all()
        if superseded is not None:
            self._dispatch_done(superseded)
        return handle

    # -- scope cancellation --------------------------------------------------
    def cancel_scope(self, scope_id: str) -> None:
        """Cancel pending (and stop the running) task for one source/scope."""
        if not isinstance(scope_id, str):
            raise TypeError("scope_id 必须是字符串")
        cancelled: list[TaskHandle] = []
        with self._lock:
            handle = self._realtime.pop(scope_id, None)
            if handle is not None:
                self._finish_locked(handle, "cancelled", CancelledError("scope cancelled"))
                cancelled.append(handle)
            for handle in [item for item in self._history if item.scope_id == scope_id]:
                try:
                    self._history.remove(handle)
                except ValueError:
                    continue
                self._finish_locked(handle, "cancelled", CancelledError("scope cancelled"))
                cancelled.append(handle)
            running = self._running_handle
            if running is not None and running.scope_id == scope_id:
                running.cancel_token.cancel()
        for handle in cancelled:
            self._dispatch_done(handle)

    # -- pause/resume --------------------------------------------------------
    def pause(self, reason: str = "") -> None:
        with self._lock:
            self._paused = True
            self._pause_reason = str(reason or "")
            self._wake.notify_all()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            self._pause_reason = ""
            self._wake.notify_all()

    # -- execution -----------------------------------------------------------
    def run_pending(self, limit: int | None = None) -> list[TaskHandle]:
        """Synchronously execute pending tasks (tests and non-Qt callers)."""
        executed: list[TaskHandle] = []
        with self._execution_lock:
            while limit is None or len(executed) < limit:
                with self._lock:
                    if self._stop_event.is_set():
                        break
                    handle = self._take_next_locked()
                    if handle is None:
                        break
                    handle.state = "running"
                    handle.started_at = self._clock()
                    self._running_handle = handle
                result = None
                error: BaseException | None = None
                state = "completed"
                try:
                    result = self._gateway.run(handle.request, handle.cancel_token)
                except CancelledError as exc:
                    state, error = "cancelled", exc
                except InferenceQuotaError as exc:
                    state, error = "failed", exc
                    self.pause(str(exc))
                except InferenceAuthError as exc:
                    state, error = "failed", exc
                    self._cancel_profile(handle.request.profile_id,
                                         "该注册授权已终止，任务停止")
                except BaseException as exc:  # noqa: BLE001 - recorded on the handle
                    state, error = "failed", exc
                finally:
                    with self._lock:
                        self._running_handle = None
                        self._served.append(handle.request.task_id)
                        self._finish_locked(handle, state, error, result)
                self._dispatch_done(handle)
                executed.append(handle)
        return executed

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            with self._lock:
                if self._paused or not (self._realtime or self._history):
                    self._wake.wait(self._poll_interval)
                    continue
            self.run_pending(limit=1)

    # -- internals -----------------------------------------------------------
    def _take_next_locked(self) -> TaskHandle | None:
        if self._paused:
            return None
        while self._realtime or self._history:
            has_realtime = bool(self._realtime)
            has_history = bool(self._history)
            take_history = False
            if has_history and has_realtime:
                take_history = self._history_yields >= self._history_yield_limit
            elif has_history:
                take_history = True
            if take_history:
                handle = self._history.popleft()
                self._history_yields = 0
                return handle
            _, handle = self._realtime.popitem(last=False)
            if has_history:
                self._history_yields += 1
            return handle
        return None

    def _cancel_profile(self, profile_id: str, reason: str) -> None:
        cancelled: list[TaskHandle] = []
        with self._lock:
            for scope in [scope for scope, item in self._realtime.items()
                          if item.request.profile_id == profile_id]:
                handle = self._realtime.pop(scope, None)
                if handle is not None:
                    self._finish_locked(handle, "cancelled", InferenceAuthError(reason))
                    cancelled.append(handle)
            for handle in [item for item in self._history
                           if item.request.profile_id == profile_id]:
                try:
                    self._history.remove(handle)
                except ValueError:
                    continue
                self._finish_locked(handle, "cancelled", InferenceAuthError(reason))
                cancelled.append(handle)
        for handle in cancelled:
            self._dispatch_done(handle)

    def _cancel_handle(self, handle: TaskHandle) -> None:
        cancelled = False
        with self._lock:
            if handle.state == "pending":
                self._remove_pending_locked(handle)
                self._finish_locked(handle, "cancelled",
                                    CancelledError("model task cancelled"))
                cancelled = True
        if cancelled:
            self._dispatch_done(handle)

    def _remove_pending_locked(self, handle: TaskHandle) -> None:
        if handle.priority == PRIORITY_REALTIME:
            if self._realtime.get(handle.scope_id) is handle:
                self._realtime.pop(handle.scope_id, None)
            return
        try:
            self._history.remove(handle)
        except ValueError:
            pass

    def _finish_locked(self, handle: TaskHandle, state: str,
                       error: BaseException | None = None,
                       result: Any = None) -> None:
        handle.state = state
        handle.error = error
        handle.result = result
        handle.finished_at = self._clock()
        if state in ("cancelled", "superseded"):
            handle.cancel_token.cancel()

    def _dispatch_done(self, handle: TaskHandle) -> None:
        callback = handle._callback
        if callback is None:
            handle._done.set()
            return
        try:
            self._dispatch(lambda: callback(handle))
        except Exception:  # noqa: BLE001 - a broken UI callback must not stop the queue
            pass
        handle._done.set()
