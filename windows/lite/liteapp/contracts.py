"""Immutable contracts for Windows ChatAssistant scope, source and result versions.

T1 of ``docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md``: every persistent
read/write/import/report/delete must carry a :class:`ScopeKey`; every live analysis
freezes an :class:`AnalysisKey` and a result is accepted only while *all* frozen
version fields still match. Display names (window title, contact nickname) are never
stable identity.

The module is deliberately free of I/O, Qt and chat content so the UI, worker and
the future history/inference layers can share it. Validation fails closed: empty
account/contact IDs, reused window handles without a process start time, and
out-of-range evidence references raise instead of being silently normalized.
"""

from __future__ import annotations

import ntpath
import threading
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AnalysisKey",
    "CancelledError",
    "CancellationToken",
    "EvidenceRef",
    "ScopeKey",
    "SourceKey",
    "normalize_path",
]


class CancelledError(Exception):
    """Raised when a user-initiated cancellation (close/switch) stops an operation."""


def normalize_path(value: str) -> str:
    """Normalize an executable path exactly like window discovery does."""
    return ntpath.normcase(ntpath.normpath(value.strip().strip('"')))


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str, got {type(value).__name__}")
    if "\x00" in value:
        raise ValueError(f"{name} must not contain NUL")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    return value


def _require_int(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be int, got {type(value).__name__}")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _require_positive_number(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be int or float, got {type(value).__name__}")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value}")


@dataclass(frozen=True)
class ScopeKey:
    """Stable identity of one WeChat account plus one contact/person.

    ``wechat_account_id`` / ``person_id`` are application-owned stable IDs (UUIDs
    created and persisted locally), never the visible nickname, chat title or
    phone number. The same display name under two accounts yields two scopes.
    """

    wechat_account_id: str
    person_id: str

    def __post_init__(self) -> None:
        _require_text(self.wechat_account_id, "wechat_account_id")
        _require_text(self.person_id, "person_id")


@dataclass(frozen=True)
class SourceKey:
    """One running WeChat process instance plus one top-level window.

    ``process_started`` is the process creation time from the OS
    (``GetProcessTimes`` FILETIME ticks, integer or float as returned by
    ``discovery.process_identity``). Windows reuses PID and HWND values; the
    creation time keeps a restarted process a *different* source so stale capture
    or analysis results cannot be attributed to the new process.
    """

    exe_path: str
    pid: int
    process_started: float
    hwnd: int

    def __post_init__(self) -> None:
        normalized = normalize_path(_require_text(self.exe_path, "exe_path"))
        if not normalized or normalized == ".":
            raise ValueError("exe_path must not be empty")
        object.__setattr__(self, "exe_path", normalized)
        _require_int(self.pid, "pid", minimum=1)
        _require_int(self.hwnd, "hwnd", minimum=1)
        _require_positive_number(self.process_started, "process_started")
        # The plan types this field as float; canonicalize here so the discovery
        # FILETIME integer and an explicitly passed float describe one source.
        object.__setattr__(self, "process_started", float(self.process_started))

    @classmethod
    def from_window_target(cls, target: Any) -> "SourceKey":
        """Build a key from ``discovery.WindowTarget`` without importing Win32 here."""
        return cls(exe_path=target.path, pid=target.pid,
                   process_started=target.created, hwnd=int(target))


_ANALYSIS_FIELDS = (
    "source",
    "conversation_epoch",
    "snapshot_digest",
    "binding_revision",
    "memory_version",
    "inference_revision",
)


@dataclass(frozen=True)
class AnalysisKey:
    """Everything that must stay unchanged for one analysis result to be accepted.

    ``conversation_epoch`` comes from :class:`liteapp.state.ConversationState`
    (incremented on every semantic snapshot change), ``snapshot_digest`` freezes
    the visible text, ``binding_revision`` versions the contact binding,
    ``memory_version`` versions the history/memory store and
    ``inference_revision`` versions the configured model/prompt.
    """

    source: SourceKey
    conversation_epoch: int
    snapshot_digest: str
    binding_revision: int
    memory_version: int
    inference_revision: int

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceKey):
            raise TypeError("source must be SourceKey")
        _require_int(self.conversation_epoch, "conversation_epoch")
        _require_text(self.snapshot_digest, "snapshot_digest")
        _require_int(self.binding_revision, "binding_revision")
        _require_int(self.memory_version, "memory_version")
        _require_int(self.inference_revision, "inference_revision")

    def accepts(self, current: Any) -> bool:
        """True only when ``current`` still matches every frozen field.

        Foreign objects fail closed instead of being coerced.
        """
        return isinstance(current, AnalysisKey) and self == current

    def differences(self, current: "AnalysisKey") -> tuple[str, ...]:
        """Names of the changed components (for diagnostics, never chat content)."""
        if not isinstance(current, AnalysisKey):
            raise TypeError("current must be AnalysisKey")
        return tuple(name for name in _ANALYSIS_FIELDS
                     if getattr(self, name) != getattr(current, name))


@dataclass(frozen=True)
class EvidenceRef:
    """Scope-checked character range inside one imported record.

    The full source ownership check (record exists, belongs to ``scope``,
    ``source_id`` and the range is inside the stored text) happens in the history
    store; this contract guarantees callers cannot even represent an unowned or
    inverted reference.
    """

    scope: ScopeKey
    source_id: str
    record_id: str
    start: int
    end: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ScopeKey):
            raise TypeError("scope must be ScopeKey")
        _require_text(self.source_id, "source_id")
        _require_text(self.record_id, "record_id")
        _require_int(self.start, "start")
        _require_int(self.end, "end")
        if self.end < self.start:
            raise ValueError("end must be >= start")

    def belongs_to(self, scope: Any) -> bool:
        """True only when the reference was created inside ``scope``."""
        return isinstance(scope, ScopeKey) and self.scope == scope

    def matches(self, scope: Any, source_id: str) -> bool:
        """True only when both scope and import source match the request."""
        return self.belongs_to(scope) and self.source_id == source_id


class CancellationToken:
    """Thread-safe, one-way cancellation flag shared by UI and worker threads.

    Cancelling must stop local stream consumption and result delivery; it never
    claims that an already-sent request or its usage was withdrawn.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise CancelledError("operation cancelled")

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or timeout; returns True when cancelled."""
        return self._event.wait(timeout)
