"""Reversible visual docking of an unchanged top-level WeChat window.

Only the native rectangle and Z order are touched: the target stays a normal
top-level window (no reparenting, no style changes, no input hooks).  Every move
is verified within :data:`GEOMETRY_TOLERANCE_PX` physical pixels, Z order is
checked separately from geometry, and a covering native popup pauses the
position loop instead of being covered.

T4 hardening over 0.2:

* fail closed on a reused HWND / changed process identity before any operation;
* tolerate at most 2 physical pixels (cross-DPI rounding), roll back on failure;
* verify/repair Z order even when the rectangle itself did not change
  (2026-10-17 "central area exposed another desktop window" issue);
* do not fight the user while the target is being dragged;
* restore the pre-takeover geometry *and* minimized state on detach.
"""

import ctypes
from ctypes import wintypes

GEOMETRY_TOLERANCE_PX = 2
MIN_APERTURE_WIDTH = 450
MIN_APERTURE_HEIGHT = 300

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOOWNERZORDER = 0x0200

SW_MINIMIZE = 6
SW_SHOWNOACTIVATE = 4


class _Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class _WindowPlacement(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("rcNormalPosition", _Rect),
    ]


class NativeWindows:
    """Thin Win32 adapter; deliberately limited to geometry/show-state calls."""

    SWP_NOSIZE = SWP_NOSIZE
    SWP_NOMOVE = SWP_NOMOVE
    SWP_NOZORDER = SWP_NOZORDER
    SWP_NOACTIVATE = SWP_NOACTIVATE
    SWP_SHOWWINDOW = SWP_SHOWWINDOW
    SWP_NOOWNERZORDER = SWP_NOOWNERZORDER

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.GetWindowPlacement.argtypes = [wintypes.HWND,
                                                   ctypes.POINTER(_WindowPlacement)]
        self.user32.GetWindowPlacement.restype = wintypes.BOOL
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                              ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                              wintypes.UINT]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        for name in ("IsWindow", "IsIconic"):
            getattr(self.user32, name).argtypes = [wintypes.HWND]
            getattr(self.user32, name).restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL
        self.user32.GetForegroundWindow.restype = wintypes.HWND

    @staticmethod
    def placement_flags(after):
        """Flags used for every placement: never activate, never show, keep Z order
        unless an explicit insert-after window was requested."""
        flags = SWP_NOACTIVATE | SWP_NOOWNERZORDER
        if after is None:
            flags |= SWP_NOZORDER
        return flags

    def is_window(self, hwnd):
        return bool(self.user32.IsWindow(int(hwnd)))

    def is_minimized(self, hwnd):
        return bool(self.user32.IsIconic(int(hwnd)))

    def set_minimized(self, hwnd, minimized):
        # SW_SHOWNOACTIVATE restores without stealing focus; SW_MINIMIZE never
        # activates the minimized window.
        command = SW_MINIMIZE if minimized else SW_SHOWNOACTIVATE
        self.user32.ShowWindow(int(hwnd), command)
        return self.is_minimized(hwnd) == bool(minimized)

    def rect(self, hwnd):
        result = _Rect()
        if not self.user32.GetWindowRect(int(hwnd), ctypes.byref(result)):
            raise OSError("无法读取窗口位置。")
        return result.left, result.top, result.right, result.bottom

    def restored_rect(self, hwnd):
        """Geometry to restore to; reads the normal placement while minimized."""
        if self.is_minimized(hwnd):
            placement = _WindowPlacement()
            placement.length = ctypes.sizeof(_WindowPlacement)
            if self.user32.GetWindowPlacement(int(hwnd), ctypes.byref(placement)):
                rect = placement.rcNormalPosition
                return rect.left, rect.top, rect.right, rect.bottom
        return self.rect(hwnd)

    def set_pos(self, hwnd, after, rect):
        left, top, right, bottom = rect
        return bool(self.user32.SetWindowPos(
            int(hwnd), int(after or 0), left, top, right - left, bottom - top,
            self.placement_flags(after)))

    def foreground_window(self):
        return int(self.user32.GetForegroundWindow() or 0)

    def zorder_ok(self, hwnd, host_hwnd):
        from .discovery import zorder_ok
        return zorder_ok(int(hwnd), int(host_hwnd))

    def popup_covering(self, hwnd, area):
        from .discovery import popup_covering
        return popup_covering(int(hwnd), tuple(area))


class WindowDock:
    """Moves only the native rectangle and Z order; never reparents or changes styles."""

    def __init__(self, api=None, validate=None):
        if validate is None:
            from .discovery import valid
            validate = valid
        self.api = api if api is not None else NativeWindows()
        self.validate = validate
        self.target = None
        self.host_hwnd = None
        self.aperture_hwnd = None
        self.original_rect = None
        self.original_minimized = False
        self.paused = False
        self.last_error = ""

    @property
    def active(self):
        return self.target is not None

    # -- optional api probes -------------------------------------------------
    def _popup_probe(self, hwnd, area):
        probe = getattr(self.api, "popup_covering", None)
        if callable(probe):
            try:
                return probe(int(hwnd), tuple(area))
            except OSError:
                return None
        return None

    def _zorder_ok(self, hwnd, host_hwnd):
        probe = getattr(self.api, "zorder_ok_for", None) or getattr(self.api, "zorder_ok", None)
        if callable(probe):
            try:
                return bool(probe(int(hwnd), int(host_hwnd)))
            except OSError:
                return False
        return False

    def _foreground(self):
        probe = getattr(self.api, "foreground_window", None)
        if callable(probe):
            try:
                return int(probe() or 0)
            except OSError:
                return 0
        return 0

    # -- operations ----------------------------------------------------------
    def set_minimized(self, target, minimized):
        if not self.validate(target) or not self.api.is_window(int(target)):
            return False
        return self.api.set_minimized(int(target), minimized)

    def attach(self, target, host_hwnd, aperture_hwnd):
        self.detach()
        self.last_error = ""
        if not self.validate(target):
            self.last_error = "identity"
            return False
        hwnd = int(target)
        if not all(self.api.is_window(value) for value in (hwnd, host_hwnd, aperture_hwnd)):
            self.last_error = "missing_window"
            return False
        original = None
        original_minimized = False
        try:
            desired = self.api.rect(aperture_hwnd)
            if (desired[2] - desired[0] < MIN_APERTURE_WIDTH
                    or desired[3] - desired[1] < MIN_APERTURE_HEIGHT):
                self.last_error = "aperture_too_small"
                return False
            if self._popup_probe(hwnd, desired) is not None:
                self.last_error = "popup_open"
                return False
            original = self._restored_rect(hwnd)
            original_minimized = bool(self.api.is_minimized(hwnd))
            if original_minimized and not self.api.set_minimized(hwnd, False):
                self.last_error = "restore_failed"
                return False
            if not self.api.set_pos(hwnd, host_hwnd, desired):
                self._restore_original(hwnd, original, original_minimized)
                self.last_error = "move_failed"
                return False
            actual = self.api.rect(hwnd)
            if any(abs(a - b) > GEOMETRY_TOLERANCE_PX for a, b in zip(actual, desired)):
                self._restore_original(hwnd, original, original_minimized)
                self.last_error = "geometry_mismatch"
                return False
            if not self._verify_or_repair_zorder(hwnd, host_hwnd, desired):
                self._restore_original(hwnd, original, original_minimized)
                self.last_error = "zorder_unverified"
                return False
        except OSError:
            if original is not None:
                self._restore_original(hwnd, original, original_minimized)
            self.last_error = "os_error"
            return False
        self.target = target
        self.host_hwnd = int(host_hwnd)
        self.aperture_hwnd = int(aperture_hwnd)
        self.original_rect = original
        self.original_minimized = original_minimized
        self.paused = False
        return True

    def _verify_or_repair_zorder(self, hwnd, host_hwnd, desired):
        """One explicit repair, never a loop: geometry unchanged must not skip Z order."""
        if host_hwnd is None:
            return True
        if self._zorder_ok(hwnd, host_hwnd):
            return True
        if not self.api.set_pos(hwnd, host_hwnd, desired):
            return False
        return self._zorder_ok(hwnd, host_hwnd)

    def align(self):
        if not self.active:
            return False
        if not self.validate(self.target) or not self.api.is_window(int(self.target)):
            self.target = None
            self.last_error = "identity"
            return False
        try:
            hwnd = int(self.target)
            desired = self.api.rect(self.aperture_hwnd)
            if self._popup_probe(hwnd, desired) is not None:
                # Keep the native popup on top and untouched; the caller shows a
                # read-only frame instead of continuing the position loop.
                self.paused = True
                self.last_error = "popup_open"
                return False
            foreground = self._foreground()
            if foreground and foreground == hwnd:
                # The user is dragging/using the native window; do not fight it.
                self.paused = True
                return True
            self.paused = False
            if self.api.rect(hwnd) != desired:
                if not self.api.set_pos(hwnd, self.host_hwnd, desired):
                    self.last_error = "move_failed"
                    return False
                if any(abs(a - b) > GEOMETRY_TOLERANCE_PX
                       for a, b in zip(self.api.rect(hwnd), desired)):
                    self.last_error = "geometry_mismatch"
                    return False
            if self.host_hwnd is not None and not self._verify_or_repair_zorder(
                    hwnd, self.host_hwnd, desired):
                self.last_error = "zorder_unverified"
                return False
            return True
        except OSError:
            self.last_error = "os_error"
            return False

    def detach(self):
        target, original_rect = self.target, self.original_rect
        original_minimized = self.original_minimized
        if target is not None and original_rect is not None:
            if self.validate(target) and self.api.is_window(int(target)):
                try:
                    if self.api.is_minimized(int(target)) and not original_minimized:
                        self.api.set_minimized(int(target), False)
                    self.api.set_pos(int(target), None, original_rect)
                    if original_minimized:
                        self.api.set_minimized(int(target), True)
                except OSError:
                    pass
        self.target = self.host_hwnd = self.aperture_hwnd = None
        self.original_rect = None
        self.original_minimized = False
        self.paused = False

    # -- helpers -------------------------------------------------------------
    def _restored_rect(self, hwnd):
        probe = getattr(self.api, "restored_rect", None)
        if callable(probe):
            return tuple(probe(int(hwnd)))
        return self.api.rect(int(hwnd))

    def _restore_original(self, hwnd, original, original_minimized):
        try:
            self.api.set_pos(hwnd, None, original)
            if original_minimized:
                self.api.set_minimized(hwnd, True)
        except OSError:
            pass
