"""Realtime snapshot controller (T9): stability wait, throttling, frozen keys.

The Word/WeChat capture layer produces snapshots faster than an analysis request
can run. This controller decides *when* a realtime analysis may start:

* a semantic change must be stable for ``min_stable`` seconds;
* one source may start at most one request per ``min_interval`` seconds;
* the contact binding must resolve for the exact ``(SourceKey,
  conversation_epoch)`` pair — an unconfirmed or replaced conversation inherits
  nothing;
* every request freezes an :class:`AnalysisKey` so a late result cannot be shown
  after the conversation or memory changed.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from threading import RLock

from .contracts import AnalysisKey, CancellationToken, ScopeKey, SourceKey
from .conversation_binding import Binding

READY_STATUSES = ("live_ready", "manual_ready")


@dataclass(frozen=True)
class ChatSnapshot:
    """One immutable "what is visible right now" packet from the capture layer."""

    source: SourceKey
    conversation_epoch: int
    frame_seq: int
    title: str
    messages: tuple[tuple[str, str, str], ...]
    status: str = "live_ready"

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceKey):
            raise TypeError("source 必须是 SourceKey。")
        if isinstance(self.conversation_epoch, bool) or not isinstance(self.conversation_epoch, int):
            raise TypeError("conversation_epoch 必须是整数。")
        if self.conversation_epoch < 0:
            raise ValueError("conversation_epoch 必须 >= 0。")
        if isinstance(self.frame_seq, bool) or not isinstance(self.frame_seq, int) or self.frame_seq < 0:
            raise ValueError("frame_seq 必须 >= 0。")
        if not isinstance(self.title, str):
            raise TypeError("title 必须是字符串。")
        if not isinstance(self.messages, tuple) or not (
                all(isinstance(item, tuple) and len(item) == 3 for item in self.messages)):
            raise TypeError("messages 必须是 (side, name, text) 元组序列。")

    @property
    def text(self) -> str:
        if not self.title:
            return ""
        return "\n".join(
            f"{'我' if who == 'me' else (name or '对方')}：{body}"
            for who, name, body in self.messages)

    @property
    def ready(self) -> bool:
        return self.status in READY_STATUSES and bool(self.title) and bool(self.text.strip())

    def digest(self) -> str:
        payload = f"{self.title}\x00{self.text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class RealtimeRequest:
    snapshot: ChatSnapshot
    scope: ScopeKey
    analysis_key: AnalysisKey
    binding: Binding


@dataclass
class _Pending:
    snapshot: ChatSnapshot
    first_seen: float


class RealtimeController:
    """Turn a stream of snapshots into rate-limited, version-frozen requests."""

    def __init__(self, bindings, *, min_stable: float = 2.0, min_interval: float = 10.0,
                 clock=None, key_factory=None, cancel_scope=None,
                 memory_version_provider=None, inference_revision_provider=None):
        self.bindings = bindings
        self.min_stable = max(0.0, float(min_stable))
        self.min_interval = max(0.0, float(min_interval))
        self.clock = clock or time.monotonic
        self.key_factory = key_factory
        self.cancel_scope = cancel_scope
        self.memory_version_provider = memory_version_provider or (lambda scope: 0)
        self.inference_revision_provider = inference_revision_provider or (lambda scope: 0)
        self._lock = RLock()
        self._pending: dict[SourceKey, _Pending] = {}
        self._last_request: dict[SourceKey, float] = {}

    # ------------------------------------------------------------- snapshots
    def on_snapshot(self, snapshot: ChatSnapshot) -> None:
        if not isinstance(snapshot, ChatSnapshot):
            raise TypeError("snapshot 必须是 ChatSnapshot。")
        with self._lock:
            previous = self._pending.get(snapshot.source)
            if not snapshot.ready:
                self._pending.pop(snapshot.source, None)
                return
            if (previous is not None
                    and previous.snapshot.conversation_epoch == snapshot.conversation_epoch
                    and previous.snapshot.text == snapshot.text):
                first_seen = previous.first_seen
            else:
                if previous is not None and previous.snapshot.conversation_epoch != snapshot.conversation_epoch:
                    self._cancel_old_binding(previous.snapshot)
                first_seen = self.clock()
            self._pending[snapshot.source] = _Pending(snapshot=snapshot, first_seen=first_seen)

    def _cancel_old_binding(self, snapshot: ChatSnapshot) -> None:
        binding = self.bindings.resolve(snapshot.source, snapshot.conversation_epoch)
        if binding is not None and self.cancel_scope is not None:
            try:
                self.cancel_scope(binding.scope)
            except Exception:  # noqa: BLE001 - cancellation is best effort
                pass

    # ------------------------------------------------------------------ due
    def due(self, now: float | None = None) -> list[RealtimeRequest]:
        moment = self.clock() if now is None else float(now)
        requests: list[RealtimeRequest] = []
        with self._lock:
            for source, pending in list(self._pending.items()):
                snapshot = pending.snapshot
                if moment - pending.first_seen < self.min_stable:
                    continue
                if moment - self._last_request.get(source, -10**12) < self.min_interval:
                    continue
                binding = self.bindings.resolve(source, snapshot.conversation_epoch)
                if binding is None:
                    # No confirmed contact profile: never guess by display name.
                    continue
                analysis_key = self._analysis_key(snapshot, binding)
                requests.append(RealtimeRequest(snapshot=snapshot, scope=binding.scope,
                                                analysis_key=analysis_key, binding=binding))
                self._last_request[source] = moment
                del self._pending[source]
        return requests

    def _analysis_key(self, snapshot: ChatSnapshot, binding: Binding) -> AnalysisKey:
        if self.key_factory is not None:
            return self.key_factory(snapshot, binding)
        return AnalysisKey(source=snapshot.source,
                           conversation_epoch=snapshot.conversation_epoch,
                           snapshot_digest=snapshot.digest(),
                           binding_revision=binding.revision,
                           memory_version=int(self.memory_version_provider(binding.scope)),
                           inference_revision=int(self.inference_revision_provider(binding.scope)))

    # --------------------------------------------------------------- cancel
    def cancel(self, source: SourceKey | None = None) -> None:
        with self._lock:
            if source is None:
                self._pending.clear()
                return
            self._pending.pop(source, None)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def last_request_at(self, source: SourceKey) -> float | None:
        with self._lock:
            return self._last_request.get(source)
