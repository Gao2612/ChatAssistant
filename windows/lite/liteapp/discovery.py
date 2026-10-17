"""Window discovery and native window identity inspection.

A window is never identified by its handle alone: :class:`WindowTarget` carries
the executable path, PID and process creation time captured during discovery and
is re-validated before every operation.  T4 adds conservative classification
(login / chat / popup), native popup enumeration, Z-order inspection, foreground
checks and the :class:`liteapp.contracts.SourceKey` adapter used by
``WindowManager`` and ``RecoveryGuard``.

All calls are read-only; this module never moves, activates, reparents or hooks
another application's window.
"""

import ctypes
from ctypes import wintypes as W
from .contracts import SourceKey
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
u32.GetForegroundWindow.restype = W.HWND
u32.GetDpiForWindow.argtypes = [W.HWND]
u32.GetDpiForWindow.restype = W.UINT
u32.GetWindowLongW.argtypes = [W.HWND, ctypes.c_int]
u32.GetWindowLongW.restype = ctypes.c_long
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.OpenProcess.restype = W.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
k32.QueryFullProcessImageNameW.restype = W.BOOL
k32.GetProcessTimes.argtypes = [W.HANDLE, ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME),
                                ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME)]
k32.GetProcessTimes.restype = W.BOOL
k32.CloseHandle.argtypes = [W.HANDLE]

GW_OWNER = 4
GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080

# Conservative classification heuristics. Real WeChat 4.x class names are not
# yet verified on this machine (T12); the title fallback keeps the 0.2 behaviour
# of only surfacing windows titled WeChat/Weixin/WeChat.
MAIN_TITLES = ("微信", "Weixin", "WeChat")
LOGIN_HINTS = ("login", "qrcode", "scanlogin")
POPUP_HINTS = ("popup", "dialog", "preview", "image", "menu", "tooltip",
               "notification", "float", "#32770")
CHAT_HINTS = ("chat", "wechatmainwnd", "weixinmainwnd", "mainwnd")

KIND_LOGIN = "login"
KIND_CHAT = "chat"
KIND_POPUP = "popup"
KIND_UNKNOWN = "unknown"


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


def window_pid(hwnd):
    pid = W.DWORD()
    if not u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        return 0
    return int(pid.value)


def window_title(hwnd):
    title = ctypes.create_unicode_buffer(512)
    u32.GetWindowTextW(hwnd, title, 512)
    return title.value


def window_class_name(hwnd):
    name = ctypes.create_unicode_buffer(256)
    u32.GetClassNameW(hwnd, name, 256)
    return name.value


def window_owner(hwnd):
    return int(u32.GetWindow(hwnd, GW_OWNER) or 0)


def window_rect(hwnd):
    rect = W.RECT()
    if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


def window_info(hwnd):
    """Read-only snapshot of one window; returns None when the handle is gone."""
    hwnd = int(hwnd)
    if not u32.IsWindow(hwnd):
        return None
    rect = window_rect(hwnd)
    if rect is None:
        return None
    pid = window_pid(hwnd)
    title = window_title(hwnd)
    class_name = window_class_name(hwnd)
    owner = window_owner(hwnd)
    return {
        "hwnd": hwnd,
        "pid": pid,
        "title": title,
        "class": class_name,
        "rect": rect,
        "owner": owner,
        "visible": bool(u32.IsWindowVisible(hwnd)),
        "minimized": bool(u32.IsIconic(hwnd)),
        "dpi": dpi(hwnd),
        "kind": classify_window(title, class_name, rect, owner),
    }


def classify_window(title, class_name, rect=None, owner=0):
    """Classify a top-level window conservatively.

    Returns ``login`` / ``chat`` / ``popup`` / ``unknown``.  A window with an
    owner is always a popup/dialog, never the chat main window.  Unknown windows
    are not auto-connected by :func:`discover`.
    """
    if owner:
        return KIND_POPUP
    class_key = (class_name or "").casefold()
    title_text = (title or "").strip()
    if any(hint in class_key for hint in LOGIN_HINTS) or "登录" in title_text:
        return KIND_LOGIN
    if any(hint in class_key for hint in POPUP_HINTS) or class_key == "#32770":
        return KIND_POPUP
    if any(hint in class_key for hint in CHAT_HINTS):
        return KIND_CHAT
    if title_text in MAIN_TITLES:
        return KIND_CHAT
    return KIND_UNKNOWN


def _collect_windows(collector):
    """Enumerate top-level windows and hand each HWND to ``collector``."""
    @CALLBACK
    def callback(hwnd, _):
        collector(hwnd)
        return True
    u32.EnumWindows(callback, 0)


def discover(path):
    """Enumerate chat/login top-level windows of the executable at ``path``."""
    expected = normalize_path(path)
    result = []
    identities = {}

    def collect(hwnd):
        if not u32.IsWindowVisible(hwnd) or u32.GetWindow(hwnd, GW_OWNER):
            return
        pid = window_pid(hwnd)
        if not pid:
            return
        if pid not in identities:
            identities[pid] = process_identity(pid)
        identity = identities[pid]
        if not identity or identity["path"] != expected:
            return
        title, cls = window_title(hwnd), window_class_name(hwnd)
        rect = window_rect(hwnd)
        if rect is None:
            return
        kind = classify_window(title, cls, rect, window_owner(hwnd))
        if kind not in (KIND_CHAT, KIND_LOGIN):
            return
        target = WindowTarget(hwnd, identity["pid"], identity["path"], identity["created"])
        result.append({"id": f"{pid}:{hwnd}:{identity['created']}", "hwnd": target,
                       "pid": pid, "path": identity["path"], "created": identity["created"],
                       "title": title, "class": cls, "kind": kind,
                       "dpi": dpi(hwnd),
                       "minimized": bool(u32.IsIconic(hwnd))})
    _collect_windows(collect)
    return result


def valid(hwnd):
    if not u32.IsWindow(hwnd):
        return False
    if not isinstance(hwnd, WindowTarget):
        return False
    identity = window_identity(hwnd)
    return bool(identity and identity["pid"] == hwnd.pid
                and identity["path"] == hwnd.path and identity["created"] == hwnd.created)


def valid_source(source):
    """Fail-closed identity check for a shared :class:`SourceKey`."""
    if not isinstance(source, SourceKey):
        return False
    try:
        hwnd = int(source.hwnd)
    except (TypeError, ValueError):
        return False
    if not hwnd or not u32.IsWindow(hwnd):
        return False
    identity = window_identity(hwnd)
    if not identity:
        return False
    # SourceKey stores process_started as float (see contracts.SourceKey); the
    # comparison is therefore done in the float domain for both sides.
    return (identity["pid"] == source.pid
            and identity["path"] == source.exe_path
            and float(identity["created"]) == source.process_started)


def target_for_source(source):
    """Build a picklable :class:`WindowTarget` from a :class:`SourceKey`."""
    created = None
    identity = window_identity(source.hwnd)
    if identity and identity["pid"] == source.pid and identity["path"] == source.exe_path:
        created = identity["created"]
    if created is None:
        # Window is gone / changed: keep the contract value; callers must
        # re-validate before using it (valid() will then fail closed).
        created = int(round(source.process_started))
    return WindowTarget(source.hwnd, source.pid, source.exe_path, created)


def source_kind(source):
    """Classify the window behind ``source``; ``unknown`` when it cannot be read."""
    if not isinstance(source, SourceKey):
        return KIND_UNKNOWN
    info = window_info(source.hwnd)
    return info["kind"] if info else KIND_UNKNOWN


def popup_windows(hwnd):
    """Visible top-level windows of the same process, excluding ``hwnd`` itself."""
    hwnd = int(hwnd)
    pid = window_pid(hwnd)
    if not pid:
        return []
    result = []

    def collect(candidate):
        candidate = int(candidate)
        if candidate == hwnd or not u32.IsWindowVisible(candidate):
            return
        if window_pid(candidate) != pid:
            return
        info = window_info(candidate)
        if info is not None:
            result.append(info)
    _collect_windows(collect)
    return result


def _intersection_area(first, second):
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    if right <= left or bottom <= top:
        return 0
    return (right - left) * (bottom - top)


def popup_covering(hwnd, area=None):
    """Return the same-process popup that overlaps ``area`` (largest overlap).

    ``area`` defaults to the target window rectangle.  ``None`` means no
    covering native popup, so continuous geometry correction may continue.
    """
    hwnd = int(hwnd)
    if area is None:
        area = window_rect(hwnd)
    if area is None:
        return None
    best_hwnd, best_overlap = None, 0
    for item in popup_windows(hwnd):
        if not item["visible"]:
            continue
        overlap = _intersection_area(item["rect"], tuple(area))
        if overlap > best_overlap:
            best_hwnd, best_overlap = item["hwnd"], overlap
    return best_hwnd


def zorder_ok(target_hwnd, host_hwnd):
    """True when the first *visible* window above ``target`` is ``host``.

    Invisible IME/helper windows live between normal windows in the global Z
    order; they must be skipped or verification would always fail.
    """
    target_hwnd, host_hwnd = int(target_hwnd), int(host_hwnd)
    if not target_hwnd or not host_hwnd:
        return False
    current = u32.GetWindow(target_hwnd, GW_HWNDPREV)
    for _ in range(256):
        if not current:
            return False
        if u32.IsWindowVisible(current):
            return int(current) == host_hwnd
        current = u32.GetWindow(current, GW_HWNDPREV)
    return False


def foreground_window():
    return int(u32.GetForegroundWindow() or 0)


def foreground_is_foreign(group_hwnds):
    """True unless the foreground window belongs to the assistant/WeChat group."""
    current = foreground_window()
    if not current:
        return True
    group = {int(hwnd) for hwnd in group_hwnds if hwnd}
    return current not in group


def minimized(hwnd):
    return bool(u32.IsIconic(hwnd))


def dpi(hwnd):
    return u32.GetDpiForWindow(hwnd) or 96
