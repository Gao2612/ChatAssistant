"""Reversible visual docking of an unchanged top-level WeChat window."""
import ctypes
from ctypes import wintypes


class _Rect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class NativeWindows:
    SWP_NOZORDER = 0x0004
    SWP_NOACTIVATE = 0x0010
    SWP_NOOWNERZORDER = 0x0200

    def __init__(self):
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(_Rect)]
        self.user32.GetWindowRect.restype = wintypes.BOOL
        self.user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                              ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.user32.SetWindowPos.restype = wintypes.BOOL
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.IsIconic.argtypes = [wintypes.HWND]
        self.user32.IsIconic.restype = wintypes.BOOL
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.ShowWindow.restype = wintypes.BOOL

    def is_window(self, hwnd):
        return bool(self.user32.IsWindow(int(hwnd)))

    def is_minimized(self, hwnd):
        return bool(self.user32.IsIconic(int(hwnd)))

    def set_minimized(self, hwnd, minimized):
        self.user32.ShowWindow(int(hwnd), 6 if minimized else 9)
        return self.is_minimized(hwnd) == bool(minimized)

    def rect(self, hwnd):
        result = _Rect()
        if not self.user32.GetWindowRect(int(hwnd), ctypes.byref(result)):
            raise OSError("无法读取窗口位置。")
        return result.left, result.top, result.right, result.bottom

    def set_pos(self, hwnd, after, rect):
        left, top, right, bottom = rect
        flags = self.SWP_NOACTIVATE | self.SWP_NOOWNERZORDER
        if after is None:
            flags |= self.SWP_NOZORDER
        return bool(self.user32.SetWindowPos(int(hwnd), int(after or 0), left, top,
                                              right - left, bottom - top, flags))


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

    @property
    def active(self):
        return self.target is not None

    def set_minimized(self, target, minimized):
        if not self.validate(target) or not self.api.is_window(int(target)):
            return False
        return self.api.set_minimized(int(target), minimized)

    def attach(self, target, host_hwnd, aperture_hwnd):
        self.detach()
        if not self.validate(target):
            return False
        hwnd = int(target)
        if not all(self.api.is_window(value) for value in (hwnd, host_hwnd, aperture_hwnd)):
            return False
        try:
            desired = self.api.rect(aperture_hwnd)
            if desired[2] - desired[0] < 450 or desired[3] - desired[1] < 300:
                return False
            original = self.api.rect(hwnd)
            if not self.api.set_pos(hwnd, host_hwnd, desired):
                return False
            actual = self.api.rect(hwnd)
            if any(abs(a - b) > 12 for a, b in zip(actual, desired)):
                self.api.set_pos(hwnd, None, original)
                return False
        except OSError:
            return False
        self.target = target
        self.host_hwnd = int(host_hwnd)
        self.aperture_hwnd = int(aperture_hwnd)
        self.original_rect = original
        return True

    def align(self):
        if not self.active:
            return False
        if not self.validate(self.target) or not self.api.is_window(int(self.target)):
            self.target = None
            return False
        try:
            desired = self.api.rect(self.aperture_hwnd)
            if self.api.rect(int(self.target)) != desired:
                if not self.api.set_pos(int(self.target), self.host_hwnd, desired):
                    return False
            return True
        except OSError:
            return False

    def detach(self):
        if self.target is not None and self.original_rect is not None:
            if self.validate(self.target) and self.api.is_window(int(self.target)):
                try:
                    self.api.set_pos(int(self.target), None, self.original_rect)
                except OSError:
                    pass
        self.target = self.host_hwnd = self.aperture_hwnd = self.original_rect = None
