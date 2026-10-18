"""Multi-source registry for user-started WeChat windows (T10).

The registry never opens WeChat or forces multi-instance: it only remembers the
official windows returned by ``discovery``, their independent state and account
label. Background monitoring is opt-in per source; at most ``max_sources``
windows are managed, the rest stay in an "unmonitored" list.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import discovery
from .contracts import SourceKey

MAX_SOURCES_DEFAULT = 3
WINDOW_STATUSES = ("visible", "minimized", "hidden", "cloaked")


def source_sort_key(source: SourceKey):
    """Deterministic sort key (SourceKey itself is not orderable)."""
    return (source.pid, source.hwnd, source.process_started, source.exe_path)


def source_from_window(item) -> SourceKey | None:
    """Build a :class:`SourceKey` from a discovery window item, or None."""
    try:
        return SourceKey(exe_path=str(item.get("path") or ""),
                         pid=int(item["pid"]),
                         process_started=float(item["created"]),
                         hwnd=int(item["hwnd"]))
    except (KeyError, TypeError, ValueError):
        return None


@dataclass
class SourceInfo:
    source: SourceKey
    title: str = ""
    window_status: str = "visible"
    account_id: str | None = None
    enabled: bool = False
    active: bool = False
    managed: bool = True
    present: bool = True
    frame_seq: int = 0
    last_frame_at: float | None = None
    last_ocr_at: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceKey):
            raise TypeError("source 必须是 SourceKey。")
        if self.window_status not in WINDOW_STATUSES:
            raise ValueError(f"未知窗口状态：{self.window_status!r}")

    @property
    def minimised(self) -> bool:
        return self.window_status in ("minimized", "hidden")


class SourceRegistry:
    """Per-source state for every user-started official WeChat window."""

    def __init__(self, max_sources: int = MAX_SOURCES_DEFAULT, clock=None) -> None:
        if isinstance(max_sources, bool) or not isinstance(max_sources, int):
            raise TypeError("max_sources 必须是整数。")
        if max_sources < 1:
            raise ValueError("max_sources 必须 >= 1。")
        self.max_sources = max_sources
        self.clock = clock or time.monotonic
        self._entries: dict[SourceKey, SourceInfo] = {}
        self._active: SourceKey | None = None

    # ------------------------------------------------------------- discovery
    def discover(self, exe_path: str, *, include_hidden: bool = False) -> list[SourceInfo]:
        """Refresh window state from discovery; keeps per-source labels and flags."""
        now = self.clock()
        items = discovery.discover(exe_path, include_hidden=include_hidden)
        seen: set[SourceKey] = set()
        for item in items:
            source = source_from_window(item)
            if source is None:
                continue
            status = str(item.get("status") or ("visible" if item.get("visible") else "hidden"))
            if status not in WINDOW_STATUSES:
                status = "hidden"
            info = self._entries.get(source)
            if info is None:
                info = SourceInfo(source=source)
                self._entries[source] = info
            info.title = str(item.get("title") or info.title)
            info.window_status = status
            info.present = True
            seen.add(source)
        for source, info in self._entries.items():
            if source not in seen:
                info.present = False
                info.active = False
                info.last_frame_at = None
                if self._active == source:
                    self._active = None
        self._rebalance(now)
        return self.sources()

    def _rebalance(self, now: float) -> None:
        present = [info for info in self._entries.values() if info.present]
        present.sort(key=lambda info: (not info.active, source_sort_key(info.source)))
        managed: list[SourceKey] = []
        if self._active in self._entries and self._entries[self._active].present:
            managed.append(self._active)
        for info in present:
            if info.source in managed:
                continue
            if len(managed) >= self.max_sources:
                break
            managed.append(info.source)
        for info in self._entries.values():
            info.managed = info.present and info.source in managed
            if not info.managed:
                info.enabled = False

    # --------------------------------------------------------------- state
    def sources(self, *, include_absent: bool = False) -> list[SourceInfo]:
        values = [info for info in self._entries.values()
                  if include_absent or info.present]
        values.sort(key=lambda info: (not info.active, source_sort_key(info.source)))
        return values

    def managed_sources(self) -> list[SourceInfo]:
        return [info for info in self.sources() if info.managed]

    def unmonitored_sources(self) -> list[SourceInfo]:
        return [info for info in self.sources() if not info.managed]

    def active_source(self) -> SourceKey | None:
        return self._active

    def get(self, source: SourceKey) -> SourceInfo | None:
        return self._entries.get(source)

    def assign_account(self, source: SourceKey, wechat_account_id: str) -> None:
        if not isinstance(source, SourceKey):
            raise TypeError("source 必须是 SourceKey。")
        info = self._entries.get(source)
        if info is None or not info.present:
            raise ValueError("来源不存在或已关闭，无法分配账号。")
        account = str(wechat_account_id or "").strip()
        if not account:
            raise ValueError("wechat_account_id 不能为空。")
        info.account_id = account

    def enable(self, source: SourceKey, enabled: bool) -> bool:
        """Enable/disable background monitoring for a *managed* source."""
        info = self._entries.get(source)
        if info is None or not info.present:
            return False
        if enabled and not info.managed:
            return False  # beyond max_sources: unmonitored by design
        info.enabled = bool(enabled)
        return True

    def set_active(self, source: SourceKey | None) -> None:
        if source is not None and (source not in self._entries
                                   or not self._entries[source].present):
            raise ValueError("来源不存在或已关闭，无法设为活动来源。")
        self._active = source
        for info in self._entries.values():
            info.active = info.source == source
        self._rebalance(self.clock())

    def mark_minimized(self, source: SourceKey, minimized: bool) -> None:
        info = self._entries.get(source)
        if info is None:
            return
        info.window_status = "minimized" if minimized else "visible"

    def observe_frame(self, source: SourceKey, frame_seq: int) -> None:
        info = self._entries.get(source)
        if info is None:
            return
        seq = int(frame_seq)
        if seq >= info.frame_seq:
            info.frame_seq = seq
            info.last_frame_at = self.clock()

    def mark_ocr(self, source: SourceKey) -> None:
        info = self._entries.get(source)
        if info is not None:
            info.last_ocr_at = self.clock()

    def close(self, source: SourceKey) -> None:
        info = self._entries.get(source)
        if info is None:
            return
        info.present = False
        info.active = False
        info.enabled = False
        if self._active == source:
            self._active = None
        self._rebalance(self.clock())
