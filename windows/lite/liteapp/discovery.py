import ctypes
from ctypes import wintypes as W
from .state import normalize_path

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
CALLBACK = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
u32.EnumWindows.argtypes = [CALLBACK, W.LPARAM]
u32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
u32.GetWindowThreadProcessId.restype = W.DWORD
u32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
u32.GetClassNameW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
u32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u32.GetWindow.argtypes = [W.HWND, W.UINT]
u32.GetWindow.restype = W.HWND
for name in ("IsWindowVisible", "IsWindow", "IsIconic"):
    getattr(u32, name).argtypes = [W.HWND]
    getattr(u32, name).restype = W.BOOL
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.OpenProcess.restype = W.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
k32.QueryFullProcessImageNameW.restype = W.BOOL
k32.GetProcessTimes.argtypes = [W.HANDLE, ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME),
                                ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME)]
k32.GetProcessTimes.restype = W.BOOL
k32.CloseHandle.argtypes = [W.HANDLE]
u32.GetForegroundWindow.restype = W.HWND
u32.GetDpiForWindow.argtypes = [W.HWND]
u32.GetDpiForWindow.restype = W.UINT


class WindowTarget(int):
    """An HWND plus the process instance discovered for it, safe to pass to a child process."""
    def __new__(cls, hwnd, pid, path, created):
        target = int.__new__(cls, int(hwnd))
        target.pid = int(pid)
        target.path = normalize_path(path)
        target.created = int(created)
        return target

    def __reduce__(self):
        return (type(self), (int(self), self.pid, self.path, self.created))


def process_identity(pid):
    """Read executable path and creation time from one process handle; fail closed."""
    handle = k32.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    identity = None
    try:
        buffer, size = ctypes.create_unicode_buffer(32768), W.DWORD(32768)
        created, exited = W.FILETIME(), W.FILETIME()
        kernel, user = W.FILETIME(), W.FILETIME()
        if k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) and buffer.value:
            if k32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                   ctypes.byref(kernel), ctypes.byref(user)):
                created_at = (created.dwHighDateTime << 32) | created.dwLowDateTime
                if created_at:
                    identity = {"pid": int(pid), "path": normalize_path(buffer.value), "created": created_at}
    except (OSError, ValueError, TypeError):
        identity = None
    finally:
        try:
            if not k32.CloseHandle(handle):
                identity = None
        except (OSError, ValueError, TypeError):
            identity = None
    return identity


def process_path(pid):
    identity = process_identity(pid)
    return identity["path"] if identity else ""


def window_identity(hwnd):
    pid = W.DWORD()
    if not u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
        return None
    return process_identity(pid.value)


def discover(path):
    expected = normalize_path(path)
    result = []
    identities = {}
    @CALLBACK
    def collect(hwnd, _):
        if not u32.IsWindowVisible(hwnd) or u32.GetWindow(hwnd, 4):
            return True
        pid = W.DWORD()
        if not u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return True
        if pid.value not in identities:
            identities[pid.value] = process_identity(pid.value)
        identity = identities[pid.value]
        if not identity or identity["path"] != expected:
            return True
        title, cls = ctypes.create_unicode_buffer(512), ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, title, 512)
        u32.GetClassNameW(hwnd, cls, 256)
        rect = W.RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
        # Main/login windows only. Separate chat/image dialogs require explicit future support.
        if title.value not in ("微信", "Weixin", "WeChat"):
            return True
        target = WindowTarget(hwnd, identity["pid"], identity["path"], identity["created"])
        result.append({"id": f"{pid.value}:{hwnd}:{identity['created']}", "hwnd": target,
                       "pid": pid.value, "path": identity["path"], "created": identity["created"],
                       "title": title.value, "class": cls.value,
                       "minimized": bool(u32.IsIconic(hwnd))})
        return True
    u32.EnumWindows(collect, 0)
    return result


def valid(hwnd):
    if not u32.IsWindow(hwnd):
        return False
    if not isinstance(hwnd, WindowTarget):
        return False
    identity = window_identity(hwnd)
    return bool(identity and identity["pid"] == hwnd.pid
                and identity["path"] == hwnd.path and identity["created"] == hwnd.created)


def minimized(hwnd):
    return bool(u32.IsIconic(hwnd))


def dpi(hwnd):
    return u32.GetDpiForWindow(hwnd) or 96
