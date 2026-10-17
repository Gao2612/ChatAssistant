"""Native docking / focus / minimized / capture-health coordinator for T4.

``WindowManager`` is the single place that decides *whether* the assistant may
touch a top-level window, where it goes, whether the transparent aperture may
stay open and how fresh the captured picture is.  It deliberately does **not**
reparent windows, install hooks, force topmost windows or loop-reset the user's
focus: every placement uses a non-activating ``SetWindowPos`` and Z order is
only repaired when the assistant/WeChat group itself is active.

States follow design §5.1/§5.2:

* dock status: detached / attached / hidden / minimized / popup / refused /
  lost / error;
* capture health: disconnected / connecting / login / live / minimized /
  stale / popup / error (separating capture heartbeat, recent new frame,
  recent content change and recent OCR time).

The manager also writes/clears crash-recovery records through
``liteapp.recovery_guard`` (only records signed by this assistant are read back
by the recovery guard after an abnormal exit).
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .contracts import SourceKey

__all__ = [
    "CaptureHealth",
    "DockStatus",
    "GEOMETRY_TOLERANCE_PX",
    "WindowManager",
    "rect_round_trip_error",
    "rect_to_dip",
    "rect_to_physical",
    "to_dip",
    "to_physical",
]

GEOMETRY_TOLERANCE_PX = 2
MIN_APERTURE_WIDTH = 450
MIN_APERTURE_HEIGHT = 300

DOCK_STATES = ("detached", "attached", "hidden", "minimized", "popup",
               "refused", "lost", "error")
VIEWS = ("chat", "settings", "history", "minimized")

CAPTURE_STATES = ("disconnected", "connecting", "login", "live", "minimized",
                  "stale", "popup", "error")

DEFAULT_STALE_AFTER = 3.0
DEFAULT_CHANGE_WINDOW = 2.0


# ---------------------------------------------------------------------------
# Cross-DPI coordinate conversion
# ---------------------------------------------------------------------------

def _positive_dpi(dpi_value):
    if isinstance(dpi_value, bool) or not isinstance(dpi_value, (int, float)):
        raise TypeError("dpi must be int or float")
    if dpi_value <= 0:
        raise ValueError("dpi must be > 0")
    return float(dpi_value)


def to_physical(dip, dpi_value):
    """Convert a DIP (Qt logical) coordinate to a physical pixel coordinate."""
    return int(round(float(dip) * _positive_dpi(dpi_value) / 96.0))


def to_dip(physical, dpi_value):
    """Convert a physical pixel coordinate to DIP."""
    return float(physical) * 96.0 / _positive_dpi(dpi_value)


def rect_to_physical(rect, dpi_value):
    return tuple(to_physical(value, dpi_value) for value in rect)


def rect_to_dip(rect, dpi_value):
    return tuple(to_dip(value, dpi_value) for value in rect)


def rect_round_trip_error(rect, dpi_value):
    """Largest physical-pixel error after a DIP -> physical -> DIP round trip."""
    converted = rect_to_physical(rect_to_dip(rect, dpi_value), dpi_value)
    return max(abs(int(actual) - int(expected))
               for actual, expected in zip(converted, rect))


# ---------------------------------------------------------------------------
# Immutable result types
# ---------------------------------------------------------------------------

def _require_rect(value, name="rect"):
    if value is None:
        return None
    if not isinstance(value, (tuple, list)) or len(value) != 4:
        raise TypeError(f"{name} must be a 4-tuple")
    result = tuple(int(item) for item in value)
    if result[2] < result[0] or result[3] < result[1]:
        raise ValueError(f"{name} has inverted edges")
    return result


@dataclass(frozen=True)
class DockStatus:
    """Result of one attach/sync/detach decision."""

    state: str = "detached"
    ok: bool = False
    source: SourceKey | None = None
    hwnd: int | None = None
    rect: tuple[int, int, int, int] | None = None
    original_rect: tuple[int, int, int, int] | None = None
    original_minimized: bool = False
    zorder_ok: bool = False
    overlay_safe: bool = False
    read_only: bool = False
    popup_hwnd: int | None = None
    reason: str = ""

    def __post_init__(self):
        if self.state not in DOCK_STATES:
            raise ValueError(f"invalid dock state {self.state!r}")
        if self.source is not None and not isinstance(self.source, SourceKey):
            raise TypeError("source must be SourceKey or None")
        object.__setattr__(self, "rect", _require_rect(self.rect, "rect"))
        object.__setattr__(self, "original_rect", _require_rect(self.original_rect,
                                                               "original_rect"))


@dataclass(frozen=True)
class CaptureHealth:
    """Snapshot of capture/state freshness; no chat content is stored here."""

    source: SourceKey | None = None
    state: str = "disconnected"
    identity_ok: bool = False
    capture_ok: bool = False
    fresh_frame: bool = False
    changed: bool = False
    minimized: bool = False
    popup_hwnd: int | None = None
    zorder_ok: bool = False
    overlay_safe: bool = False
    read_only: bool = True
    heartbeat_age: float | None = None
    frame_age: float | None = None
    change_age: float | None = None
    ocr_age: float | None = None
    reason: str = ""

    def __post_init__(self):
        if self.state not in CAPTURE_STATES:
            raise ValueError(f"invalid capture state {self.state!r}")
        if self.source is not None and not isinstance(self.source, SourceKey):
            raise TypeError("source must be SourceKey or None")


# ---------------------------------------------------------------------------
# Internal attachment state
# ---------------------------------------------------------------------------

@dataclass
class _Attachment:
    source: SourceKey
    host_hwnd: int | None
    aperture_hwnd: int | None
    target_rect: tuple[int, int, int, int]
    original_rect: tuple[int, int, int, int]
    original_minimized: bool
    hidden_by_view: bool = False
    app_minimized: bool = False
    popup_hwnd: int | None = None
    zorder_ok: bool = False
    attached_at: float = 0.0


def _same_process(first: SourceKey, second: SourceKey) -> bool:
    return (first.pid == second.pid
            and first.exe_path == second.exe_path
            and first.process_started == second.process_started)


class WindowManager:
    """Owns the lifecycle of the one natively docked WeChat window."""

    def __init__(self, api=None, *, validate=None, popup_probe=None,
                 zorder_probe=None, classify_probe=None, foreground_probe=None,
                 recovery_store=None, clock=None,
                 stale_after=DEFAULT_STALE_AFTER,
                 change_window=DEFAULT_CHANGE_WINDOW):
        from . import discovery
        from .window_dock import NativeWindows

        self.api = api if api is not None else NativeWindows()
        self._validate = validate or discovery.valid_source
        self._popup = popup_probe or (lambda hwnd, area: discovery.popup_covering(hwnd, area))
        self._zorder = zorder_probe or (
            lambda hwnd, host: discovery.zorder_ok(int(hwnd), int(host)))
        self._classify = classify_probe or discovery.source_kind
        self._foreground = foreground_probe or discovery.foreground_window
        self.recovery_store = recovery_store
        self._clock = clock or time.monotonic
        self.stale_after = float(stale_after)
        self.change_window = float(change_window)
        self.view = "chat"
        self._attachment: _Attachment | None = None
        self._status = DockStatus(state="detached")
        self._samples: dict[SourceKey, dict[str, float]] = {}

    # -- introspection -------------------------------------------------------
    @property
    def attached(self) -> bool:
        return self._attachment is not None

    @property
    def active_source(self) -> SourceKey | None:
        return self._attachment.source if self._attachment is not None else None

    @property
    def status(self) -> DockStatus:
        return self._status

    @property
    def target_rect(self):
        return self._attachment.target_rect if self._attachment is not None else None

    @property
    def overlay_safe(self) -> bool:
        return self._status.overlay_safe

    # -- clock / samples -----------------------------------------------------
    def _now(self, explicit=None):
        return float(explicit) if explicit is not None else float(self._clock())

    def _samples_for(self, source: SourceKey) -> dict[str, float]:
        return self._samples.setdefault(source, {})

    def record_heartbeat(self, source, *, at=None):
        """The capture process is alive (packet arrived/static picture)."""
        self._require_source(source)
        self._samples_for(source)["heartbeat"] = self._now(at)

    def record_frame(self, source, *, at=None, changed=False):
        """A new captured frame arrived; ``changed`` means the content differs."""
        self._require_source(source)
        samples = self._samples_for(source)
        now = self._now(at)
        samples["heartbeat"] = now
        samples["frame"] = now
        if changed:
            samples["change"] = now

    def record_ocr(self, source, *, at=None):
        self._require_source(source)
        self._samples_for(source)["ocr"] = self._now(at)

    # -- attach / sync / detach ---------------------------------------------
    def attach(self, source, target_rect, *, host_hwnd=None, aperture_hwnd=None,
               now=None):
        """Dock ``source`` into ``target_rect``; returns a fail-closed status."""
        self._require_source(source)
        target_rect = _require_rect(tuple(int(value) for value in target_rect),
                                    "target_rect")
        if target_rect is None:
            raise TypeError("target_rect must be a 4-tuple")
        if (target_rect[2] - target_rect[0] < MIN_APERTURE_WIDTH
                or target_rect[3] - target_rect[1] < MIN_APERTURE_HEIGHT):
            return self._set(DockStatus(state="refused", source=source,
                                        hwnd=int(source.hwnd), rect=target_rect,
                                        reason="aperture_too_small"))
        if not self._validate(source) or not self.api.is_window(int(source.hwnd)):
            return self._set(DockStatus(state="refused", source=source,
                                        hwnd=int(source.hwnd), rect=target_rect,
                                        reason="identity"))

        if self._attachment is not None:
            current = self._attachment
            if current.source == source:
                current.target_rect = target_rect
                if host_hwnd is not None:
                    current.host_hwnd = int(host_hwnd)
                if aperture_hwnd is not None:
                    current.aperture_hwnd = int(aperture_hwnd)
                return self.sync(target_rect=target_rect, host_hwnd=host_hwnd, now=now)
            if _same_process(current.source, source):
                # Login -> chat main window handover of the same trusted process.
                self.detach(restore=True)
            elif self._validate(current.source):
                return self._set(DockStatus(state="refused", source=source,
                                            hwnd=int(source.hwnd),
                                            rect=target_rect,
                                            reason="untrusted_takeover"))
            else:
                # The old window is already gone/reused; never touch it again.
                self._drop_attachment(forget=True)

        hwnd = int(source.hwnd)
        popup = self._popup(hwnd, target_rect)
        if popup is not None:
            return self._set(DockStatus(state="refused", source=source, hwnd=hwnd,
                                        rect=target_rect, popup_hwnd=int(popup),
                                        read_only=True, reason="popup_open"))

        original_rect = self._restored_rect(hwnd)
        original_minimized = bool(self.api.is_minimized(hwnd))
        if original_minimized and not self.api.set_minimized(hwnd, False):
            return self._set(DockStatus(state="refused", source=source, hwnd=hwnd,
                                        rect=target_rect,
                                        reason="restore_minimized_failed"))

        if self.recovery_store is not None:
            try:
                self.recovery_store.remember(source, original_rect,
                                             original_minimized,
                                             at=self._now(now))
            except (OSError, ValueError, TypeError):
                if original_minimized:
                    self.api.set_minimized(hwnd, True)
                return self._set(DockStatus(state="refused", source=source, hwnd=hwnd,
                                            rect=target_rect,
                                            reason="recovery_unavailable"))

        if not self.api.set_pos(hwnd, host_hwnd, target_rect):
            self._rollback(hwnd, original_rect, original_minimized)
            self._forget_recovery(source)
            return self._set(DockStatus(state="error", source=source, hwnd=hwnd,
                                        rect=target_rect, reason="move_failed"))
        actual = self._rect(hwnd)
        if any(abs(a - b) > GEOMETRY_TOLERANCE_PX for a, b in zip(actual, target_rect)):
            self._rollback(hwnd, original_rect, original_minimized)
            self._forget_recovery(source)
            return self._set(DockStatus(state="error", source=source, hwnd=hwnd,
                                        rect=actual, reason="geometry_mismatch"))

        zorder_ok = self._verify_zorder(hwnd, host_hwnd, target_rect)
        attachment = _Attachment(
            source=source, host_hwnd=int(host_hwnd) if host_hwnd is not None else None,
            aperture_hwnd=int(aperture_hwnd) if aperture_hwnd is not None else None,
            target_rect=target_rect, original_rect=original_rect,
            original_minimized=original_minimized, zorder_ok=zorder_ok,
            attached_at=self._now(now))
        self._attachment = attachment
        overlay = self._overlay_safe(attachment)
        return self._set(DockStatus(
            state="attached", ok=True, source=source, hwnd=hwnd, rect=actual,
            original_rect=original_rect, original_minimized=original_minimized,
            zorder_ok=zorder_ok, overlay_safe=overlay, read_only=not overlay))

    def sync(self, *, target_rect=None, host_hwnd=None, now=None):
        """Event-driven geometry/state update; never fights the user or popups."""
        attachment = self._attachment
        if attachment is None:
            return self._set(DockStatus(state="detached"))
        if target_rect is not None:
            attachment.target_rect = _require_rect(
                tuple(int(value) for value in target_rect), "target_rect")
        if host_hwnd is not None:
            attachment.host_hwnd = int(host_hwnd)
        hwnd = int(attachment.source.hwnd)
        if not self._validate(attachment.source) or not self.api.is_window(hwnd):
            source = attachment.source
            self._drop_attachment(forget=True)
            return self._set(DockStatus(state="lost", source=source, hwnd=hwnd,
                                        reason="identity"))

        popup = None
        if not attachment.hidden_by_view and self.view == "chat":
            popup = self._popup(hwnd, attachment.target_rect)
        attachment.popup_hwnd = int(popup) if popup is not None else None
        if popup is not None:
            return self._set(DockStatus(
                state="popup", source=attachment.source, hwnd=hwnd,
                rect=self._rect(hwnd), popup_hwnd=int(popup),
                original_rect=attachment.original_rect,
                original_minimized=attachment.original_minimized,
                read_only=True, reason="popup_open"))

        if self.view != "chat":
            self._hide_for_view(attachment, hwnd)
            return self._set(DockStatus(
                state="hidden", ok=True, source=attachment.source, hwnd=hwnd,
                rect=attachment.target_rect, original_rect=attachment.original_rect,
                original_minimized=attachment.original_minimized, read_only=True,
                reason="view_" + self.view))

        if attachment.hidden_by_view:
            if attachment.app_minimized:
                self.api.set_minimized(hwnd, False)
                attachment.app_minimized = False
            attachment.hidden_by_view = False
        if bool(self.api.is_minimized(hwnd)):
            return self._set(DockStatus(
                state="minimized", ok=True, source=attachment.source, hwnd=hwnd,
                rect=attachment.target_rect, original_rect=attachment.original_rect,
                original_minimized=attachment.original_minimized, read_only=True,
                reason="minimized"))

        foreground = int(self._foreground() or 0)
        actual = self._rect(hwnd)
        if foreground == hwnd and actual != attachment.target_rect:
            # The user is moving/using the native window; pause correction.
            attachment.zorder_ok = self._zorder(hwnd, attachment.host_hwnd) \
                if attachment.host_hwnd else False
            return self._set(DockStatus(
                state="attached", ok=True, source=attachment.source, hwnd=hwnd,
                rect=actual, original_rect=attachment.original_rect,
                original_minimized=attachment.original_minimized,
                zorder_ok=attachment.zorder_ok,
                overlay_safe=self._overlay_safe(attachment),
                read_only=not self._overlay_safe(attachment), reason="user_dragging"))

        if actual != attachment.target_rect:
            if not self.api.set_pos(hwnd, attachment.host_hwnd, attachment.target_rect):
                return self._set(DockStatus(state="error", source=attachment.source,
                                            hwnd=hwnd, rect=actual,
                                            reason="move_failed"))
            actual = self._rect(hwnd)
            if any(abs(a - b) > GEOMETRY_TOLERANCE_PX
                   for a, b in zip(actual, attachment.target_rect)):
                return self._set(DockStatus(state="error", source=attachment.source,
                                            hwnd=hwnd, rect=actual,
                                            reason="geometry_mismatch"))
        attachment.zorder_ok = self._zorder_or_repair(attachment, hwnd, foreground)
        overlay = self._overlay_safe(attachment)
        return self._set(DockStatus(
            state="attached", ok=True, source=attachment.source, hwnd=hwnd,
            rect=actual, original_rect=attachment.original_rect,
            original_minimized=attachment.original_minimized,
            zorder_ok=attachment.zorder_ok, overlay_safe=overlay,
            read_only=not overlay))

    def set_view(self, mode):
        """chat / settings / history / minimized; hides the managed WeChat only."""
        if mode not in VIEWS:
            raise ValueError(f"invalid view mode {mode!r}")
        self.view = mode
        attachment = self._attachment
        if attachment is None:
            self._status = DockStatus(state="detached")
            return
        hwnd = int(attachment.source.hwnd)
        if mode != "chat":
            self._hide_for_view(attachment, hwnd)
            self._status = DockStatus(
                state="hidden", ok=True, source=attachment.source, hwnd=hwnd,
                rect=attachment.target_rect, original_rect=attachment.original_rect,
                original_minimized=attachment.original_minimized, read_only=True,
                reason="view_" + mode)
            return
        self.sync()

    def detach(self, restore=True):
        """Restore the managed window (geometry and minimized state) and forget it."""
        attachment = self._attachment
        if attachment is None:
            self._status = DockStatus(state="detached")
            return
        hwnd = int(attachment.source.hwnd)
        if (restore and self._validate(attachment.source)
                and self.api.is_window(hwnd)):
            try:
                if bool(self.api.is_minimized(hwnd)) and not attachment.original_minimized:
                    self.api.set_minimized(hwnd, False)
                self.api.set_pos(hwnd, None, attachment.original_rect)
                if attachment.original_minimized:
                    self.api.set_minimized(hwnd, True)
            except OSError:
                pass
        self._forget_recovery(attachment.source)
        self._attachment = None
        self._status = DockStatus(state="detached", ok=True)

    def on_activated(self):
        """The assistant group was raised by the user/second instance.

        Repairs the group Z order (the reported "central area shows another
        desktop window while geometry is unchanged" bug) without ever forcing
        the foreground or raising the group above a foreign foreground app.
        """
        attachment = self._attachment
        if attachment is None:
            return False
        hwnd = int(attachment.source.hwnd)
        if not self._validate(attachment.source) or not self.api.is_window(hwnd):
            self._drop_attachment(forget=True)
            self._status = DockStatus(state="lost", reason="identity")
            return False
        if self.view != "chat" or attachment.hidden_by_view:
            # Settings/history/minimized views keep the managed WeChat hidden.
            self._hide_for_view(attachment, hwnd)
            self._status = DockStatus(
                state="hidden", ok=True, source=attachment.source, hwnd=hwnd,
                rect=attachment.target_rect, original_rect=attachment.original_rect,
                original_minimized=attachment.original_minimized, read_only=True,
                reason="view_" + self.view)
            return False
        if bool(self.api.is_minimized(hwnd)):
            if not self.api.set_minimized(hwnd, False):
                return False
            attachment.app_minimized = False
        actual = self._rect(hwnd)
        if actual != attachment.target_rect:
            if not self.api.set_pos(hwnd, attachment.host_hwnd, attachment.target_rect):
                return False
        attachment.zorder_ok = self._zorder_or_repair(attachment, hwnd,
                                                      int(self._foreground() or 0),
                                                      force=True)
        overlay = self._overlay_safe(attachment)
        self._set(DockStatus(
            state="attached", ok=True, source=attachment.source, hwnd=hwnd,
            rect=self._rect(hwnd), original_rect=attachment.original_rect,
            original_minimized=attachment.original_minimized,
            zorder_ok=attachment.zorder_ok, overlay_safe=overlay,
            read_only=not overlay))
        return overlay

    # -- capture health ------------------------------------------------------
    def capture_health(self, source, *, now=None):
        """Distinguish heartbeat / new frame / content change / OCR freshness."""
        self._require_source(source)
        now = self._now(now)
        identity_ok = bool(self._validate(source))
        samples = self._samples.get(source, {})
        heartbeat_age = self._age(samples.get("heartbeat"), now)
        frame_age = self._age(samples.get("frame"), now)
        change_age = self._age(samples.get("change"), now)
        ocr_age = self._age(samples.get("ocr"), now)
        attachment = self._attachment

        if attachment is None or source != attachment.source:
            state = "error" if not identity_ok else "disconnected"
            return CaptureHealth(
                source=source, state=state, identity_ok=identity_ok,
                heartbeat_age=heartbeat_age, frame_age=frame_age,
                change_age=change_age, ocr_age=ocr_age, read_only=True,
                reason="not_attached")
        if not identity_ok:
            return CaptureHealth(
                source=source, state="error", identity_ok=False,
                heartbeat_age=heartbeat_age, frame_age=frame_age,
                change_age=change_age, ocr_age=ocr_age, read_only=True,
                reason="identity")

        hwnd = int(source.hwnd)
        hidden = attachment.hidden_by_view or self.view != "chat"
        minimized = bool(hidden or self.api.is_minimized(hwnd))
        popup = None if hidden else self._popup(hwnd, attachment.target_rect)
        kind = self._classify(source)
        zorder_ok = attachment.zorder_ok and not hidden and not minimized
        overlay_safe = bool(identity_ok and zorder_ok and popup is None
                            and not hidden and not minimized)
        capture_ok = self._is_recent(heartbeat_age) or self._is_recent(frame_age)
        fresh_frame = self._is_recent(frame_age)
        changed = change_age is not None and change_age <= self.change_window

        if kind == "login":
            state = "login"
            reason = "login_window"
        elif popup is not None:
            state = "popup"
            reason = "popup_open"
            attachment.popup_hwnd = int(popup)
        elif minimized:
            state = "minimized"
            reason = "minimized"
        elif heartbeat_age is None and frame_age is None:
            state = "connecting"
            reason = "no_frames_yet"
        elif capture_ok:
            state = "live"
            reason = "heartbeat" if fresh_frame else "static_picture"
        else:
            state = "stale"
            reason = "no_capture"

        return CaptureHealth(
            source=source, state=state, identity_ok=True,
            capture_ok=capture_ok, fresh_frame=fresh_frame, changed=changed,
            minimized=minimized,
            popup_hwnd=int(popup) if popup is not None else None,
            zorder_ok=zorder_ok, overlay_safe=overlay_safe,
            read_only=not overlay_safe, heartbeat_age=heartbeat_age,
            frame_age=frame_age, change_age=change_age, ocr_age=ocr_age,
            reason=reason)

    # -- internals -----------------------------------------------------------
    def _require_source(self, source):
        if not isinstance(source, SourceKey):
            raise TypeError("source must be SourceKey")

    def _set(self, status):
        self._status = status
        return status

    def _age(self, timestamp, now):
        if timestamp is None:
            return None
        return max(0.0, now - timestamp)

    def _is_recent(self, age):
        return age is not None and age <= self.stale_after

    def _rect(self, hwnd):
        return tuple(self.api.rect(int(hwnd)))

    def _restored_rect(self, hwnd):
        probe = getattr(self.api, "restored_rect", None)
        if callable(probe):
            return tuple(probe(int(hwnd)))
        return self._rect(hwnd)

    def _verify_zorder(self, hwnd, host_hwnd, rect):
        if host_hwnd is None:
            return False
        if self._zorder(hwnd, int(host_hwnd)):
            return True
        # exactly one repair attempt at attach time, never a loop
        if not self.api.set_pos(int(hwnd), int(host_hwnd), rect):
            return False
        return bool(self._zorder(hwnd, int(host_hwnd)))

    def _zorder_or_repair(self, attachment, hwnd, foreground, force=False):
        host = attachment.host_hwnd
        if host is None:
            return False
        if self._zorder(hwnd, host):
            return True
        group_active = force or foreground in (hwnd, host)
        if not group_active:
            # Another application is in front: do not raise/reorder the group.
            return False
        if not self.api.set_pos(hwnd, host, attachment.target_rect):
            return False
        return bool(self._zorder(hwnd, host))

    def _overlay_safe(self, attachment):
        if attachment.host_hwnd is None:
            return False
        return bool(attachment.zorder_ok and not attachment.hidden_by_view)

    def _hide_for_view(self, attachment, hwnd):
        if attachment.hidden_by_view:
            return
        if self._validate(attachment.source) and self.api.is_window(hwnd):
            if self.api.set_minimized(hwnd, True):
                attachment.app_minimized = True
                attachment.hidden_by_view = True

    def _rollback(self, hwnd, original_rect, original_minimized):
        try:
            self.api.set_pos(hwnd, None, original_rect)
            if original_minimized:
                self.api.set_minimized(hwnd, True)
        except OSError:
            pass

    def _forget_recovery(self, source):
        if self.recovery_store is None:
            return
        try:
            self.recovery_store.forget(source)
        except (OSError, ValueError, TypeError):
            pass

    def _drop_attachment(self, forget=True):
        attachment = self._attachment
        self._attachment = None
        if attachment is not None and forget:
            self._forget_recovery(attachment.source)
