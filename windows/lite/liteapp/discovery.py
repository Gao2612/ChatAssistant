"""Window discovery and native window identity inspection.

A window is never identified by its handle alone: :class:`WindowTarget` carries
the executable path, PID and process creation time captured during discovery and
is re-validated before every operation.  T4 adds conservative classification
(login / chat / popup), native popup enumeration, Z-order inspection, foreground
checks and the :class:`liteapp.contracts.SourceKey` adapter used by
``WindowManager`` and ``RecoveryGuard``.

S-WECHAT (task-17) adds read-only detection of *running* WeChat processes and of
hidden/tray/minimized top-level windows (``IsWindowVisible == false``), plus an
explicitly user-triggered :func:`restore_window` that shows such a window
without stealing focus.  This lets the UI refuse to launch a second WeChat
instance when one is already running in the tray.

All functions are read-only except ``restore_window`` (only called on an
explicit user click); this module never launches processes, reparents or hooks
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
u32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
u32.ShowWindow.restype = W.BOOL
u32.MonitorFromRect.argtypes = [ctypes.POINTER(W.RECT), W.DWORD]
u32.MonitorFromRect.restype = W.HANDLE
u32.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int,
                            ctypes.c_int, ctypes.c_int, W.UINT]
u32.SetWindowPos.restype = W.BOOL
u32.SystemParametersInfoW.argtypes = [W.UINT, W.UINT, ctypes.c_void_p, W.UINT]
u32.SystemParametersInfoW.restype = W.BOOL
k32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
k32.OpenProcess.restype = W.HANDLE
k32.QueryFullProcessImageNameW.argtypes = [W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
k32.QueryFullProcessImageNameW.restype = W.BOOL
k32.GetProcessTimes.argtypes = [W.HANDLE, ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME),
                                ctypes.POINTER(W.FILETIME), ctypes.POINTER(W.FILETIME)]
k32.GetProcessTimes.restype = W.BOOL
k32.CloseHandle.argtypes = [W.HANDLE]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", W.DWORD),
        ("cntUsage", W.DWORD),
        ("th32ProcessID", W.DWORD),
        ("th32DefaultHeapID", ctypes.c_void_p),
        ("th32ModuleID", W.DWORD),
        ("cntThreads", W.DWORD),
        ("th32ParentProcessID", W.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", W.DWORD),
        ("szExeFile", W.WCHAR * 260),
    ]


TH32CS_SNAPPROCESS = 0x00000002
k32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
k32.CreateToolhelp32Snapshot.restype = W.HANDLE
k32.Process32FirstW.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32FirstW.restype = W.BOOL
k32.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
k32.Process32NextW.restype = W.BOOL

GW_OWNER = 4
GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14

# Explicit show/restore commands; only restore_window uses them, and the default
# path never activates the target (user requirement: 不抢焦点).
SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_SHOWNA = 8
SW_RESTORE = 9

# WeChat ships under one of these executable names (Weixin.exe on current
# builds, WeChat.exe on older ones). Used only to *detect* running processes.
WECHAT_EXE_NAMES = ("weixin.exe", "wechat.exe")

# Lifecycle status of a discovered top-level window, for UI hints.
STATUS_VISIBLE = "visible"
STATUS_MINIMIZED = "minimized"
STATUS_HIDDEN = "hidden"
STATUS_CLOAKED = "cloaked"
WINDOW_STATUSES = (STATUS_VISIBLE, STATUS_MINIMIZED, STATUS_HIDDEN, STATUS_CLOAKED)

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


_dwmapi = None
_dwm_attempted = False


def _dwm():
    """Lazy dwmapi binding used only for the DWMWA_CLOAKED window attribute."""
    global _dwmapi, _dwm_attempted
    if not _dwm_attempted:
        _dwm_attempted = True
        try:
            _dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
            _dwmapi.DwmGetWindowAttribute.argtypes = [W.HWND, W.DWORD,
                                                      ctypes.c_void_p, W.DWORD]
            _dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long
        except (OSError, AttributeError):
            _dwmapi = None
    return _dwmapi


def window_cloaked(hwnd):
    """True when DWM cloaks the window (e.g. another virtual desktop / suspended UWP).

    A cloaked window is not recoverable with ShowWindow; reporting it separately
    keeps the UI from pretending it restored the window.
    """
    hwnd = int(hwnd)
    if not hwnd:
        return False
    dwm = _dwm()
    if dwm is None:
        return False
    try:
        value = W.DWORD(0)
        result = dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED,
                                           ctypes.byref(value), ctypes.sizeof(value))
        return bool(result == 0 and value.value)
    except (OSError, ValueError):
        return False


def window_offscreen(hwnd):
    """True only when a readable rectangle intersects no actual monitor."""
    bounds = window_rect(hwnd)
    if bounds is None:
        return False
    rect = W.RECT(*bounds)
    return not bool(u32.MonitorFromRect(ctypes.byref(rect), 0))


def _recover_offscreen(hwnd):
    if not window_offscreen(hwnd):
        return True
    work = W.RECT()
    if not u32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work), 0):
        return False
    # Primary work area; preserve size/Z-order and never activate while moving.
    return bool(u32.SetWindowPos(hwnd, 0, work.left, work.top, 0, 0, 0x0015))


def window_status(hwnd):
    """Lifecycle status string for one window (visible/minimized/hidden/cloaked)."""
    hwnd = int(hwnd)
    if window_cloaked(hwnd):
        return STATUS_CLOAKED
    if u32.IsIconic(hwnd):
        return STATUS_MINIMIZED
    if u32.IsWindowVisible(hwnd) and not window_offscreen(hwnd):
        return STATUS_VISIBLE
    return STATUS_HIDDEN


def window_info(hwnd):
    """Read-only snapshot of one window; returns None when the handle is gone.

    Includes hidden (``IsWindowVisible == false``) top-level windows, their
    lifecycle ``status`` and the DWM-cloaked flag.
    """
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
    visible = bool(u32.IsWindowVisible(hwnd))
    offscreen = window_offscreen(hwnd)
    minimized = bool(u32.IsIconic(hwnd))
    visible = visible and (minimized or not offscreen)
    cloaked = window_cloaked(hwnd)
    if cloaked:
        status = STATUS_CLOAKED
    elif minimized:
        status = STATUS_MINIMIZED
    elif visible:
        status = STATUS_VISIBLE
    else:
        status = STATUS_HIDDEN
    return {
        "hwnd": hwnd,
        "pid": pid,
        "title": title,
        "class": class_name,
        "rect": rect,
        "owner": owner,
        "visible": visible,
        "hidden": not visible,
        "offscreen": offscreen,
        "minimized": minimized,
        "cloaked": cloaked,
        "status": status,
        "tool_window": bool(u32.GetWindowLongW(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW),
        "dpi": dpi(hwnd),
        "kind": classify_window(title, class_name, rect, owner),
    }


def _window_item(info, identity):
    """Public item shape for one verified window (superset of the 0.2 fields)."""
    hwnd = info["hwnd"]
    target = WindowTarget(hwnd, identity["pid"], identity["path"], identity["created"])
    return {
        "id": f"{identity['pid']}:{hwnd}:{identity['created']}",
        "hwnd": target,
        "pid": identity["pid"],
        "path": identity["path"],
        "created": identity["created"],
        "title": info["title"],
        "class": info["class"],
        "kind": info["kind"],
        "dpi": info["dpi"],
        "minimized": info["minimized"],
        "visible": info["visible"],
        "hidden": info["hidden"],
        "cloaked": info["cloaked"],
        "status": info["status"],
        "tool_window": info["tool_window"],
        "owner": info["owner"],
        "rect": info["rect"],
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


def discover(path, include_hidden=False):
    """Enumerate chat/login top-level windows of the executable at ``path``.

    ``include_hidden=False`` keeps the 0.2 behaviour (visible windows only).
    ``include_hidden=True`` additionally returns windows hidden from the taskbar
    / tray (``IsWindowVisible == false``) so the UI can offer to restore them.
    Items carry ``visible/hidden/minimized/cloaked/status`` for the UI decision.
    """
    expected = normalize_path(path)
    result = []
    identities = {}

    def collect(hwnd):
        if not include_hidden and not u32.IsWindowVisible(hwnd):
            return
        if u32.GetWindow(hwnd, GW_OWNER):
            return  # owned popups/dialogs are never the main/login window
        pid = window_pid(hwnd)
        if not pid:
            return
        if pid not in identities:
            identities[pid] = process_identity(pid)
        identity = identities[pid]
        if not identity or identity["path"] != expected:
            return
        info = window_info(hwnd)
        if info is None or info["kind"] not in (KIND_CHAT, KIND_LOGIN):
            return
        if not include_hidden and info.get("offscreen") and not info["minimized"]:
            return
        result.append(_window_item(info, identity))
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


# ---------------------------------------------------------------------------
# S-WECHAT (task-17): running-process detection, hidden/tray windows, restore
# ---------------------------------------------------------------------------

def _valid_snapshot_handle(handle):
    if not handle:
        return False
    try:
        return int(handle) not in (-1, 0xFFFFFFFFFFFFFFFF)
    except (TypeError, ValueError):
        return False


def enumerate_processes():
    """Read-only snapshot of running processes: ``pid``, ``exe_name``, ``path``.

    ``path`` is the normalized full image path (empty when Windows denies the
    query). Nothing is launched, opened for writing or terminated.
    """
    result = []
    try:
        snapshot = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    except (OSError, ValueError):
        return result
    if not _valid_snapshot_handle(snapshot):
        return result
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snapshot, ctypes.byref(entry)):
            return result
        while True:
            pid = int(entry.th32ProcessID)
            if pid:
                result.append({"pid": pid,
                               "exe_name": entry.szExeFile or "",
                               "path": process_path(pid)})
            entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
            if not k32.Process32NextW(snapshot, ctypes.byref(entry)):
                break
    except (OSError, ValueError):
        pass
    finally:
        try:
            k32.CloseHandle(snapshot)
        except (OSError, ValueError):
            pass
    return result


def _entry_is_wechat(entry):
    exe_name = (entry.get("exe_name") or "").strip().casefold()
    if exe_name in WECHAT_EXE_NAMES:
        return True
    path = entry.get("path") or ""
    if path:
        basename = path.replace("/", "\\").rsplit("\\", 1)[-1].casefold()
        return basename in WECHAT_EXE_NAMES
    return False


def running_wechat_paths():
    """Normalized executable paths of every running official WeChat process.

    Used by the UI to decide "already running -> never Popen a second copy".
    Detection uses the process snapshot only; no WeChat state is modified.
    """
    paths = {entry["path"] for entry in enumerate_processes()
             if entry.get("path") and _entry_is_wechat(entry)}
    return sorted(paths)


def process_running(path):
    """True when a process with this exact (normalized) executable path runs."""
    try:
        expected = normalize_path(path) if path else ""
    except (AttributeError, TypeError, ValueError):
        return False
    if not expected or expected == ".":
        return False
    return any(entry.get("path") == expected for entry in enumerate_processes())


def _top_level_windows_for_process(pid):
    """Main/login top-level windows of one process, including hidden ones."""
    pid = int(pid)
    found = []
    if not pid:
        return found

    def collect(hwnd):
        if window_pid(hwnd) != pid:
            return
        if window_owner(hwnd):
            return  # popups/dialogs are not main/login candidates
        info = window_info(hwnd)
        if info is None or info["kind"] not in (KIND_CHAT, KIND_LOGIN):
            return
        found.append(info)
    _collect_windows(collect)
    found.sort(key=lambda item: (item["status"] == STATUS_CLOAKED,
                                 not item["visible"], item["minimized"],
                                 item["kind"] != KIND_CHAT, item["hwnd"]))
    return found


def find_wechat_processes(path, *, include_hidden=True):
    """Running processes of the configured WeChat exe plus their window candidates.

    Returns one dict per matching process: ``pid``, ``path``, ``created``,
    ``exe_name``, ``windows`` (items shaped like :func:`discover` results,
    including ``IsWindowVisible == false`` windows), ``has_window`` and
    ``hidden_windows``. Unrelated processes and windows are never included.
    """
    expected = normalize_path(path)
    result = []
    for entry in enumerate_processes():
        if entry.get("path") != expected:
            continue
        identity = process_identity(entry["pid"])
        if not identity:
            continue
        infos = _top_level_windows_for_process(identity["pid"])
        windows = [_window_item(info, identity) for info in infos
                   if include_hidden or info["visible"]]
        result.append({
            "pid": identity["pid"],
            "path": identity["path"],
            "created": identity["created"],
            "exe_name": entry.get("exe_name") or "",
            "windows": windows,
            "has_window": bool(windows),
            "hidden_windows": sum(1 for item in windows if not item["visible"]),
        })
    return result


def find_wechat_windows(path, *, include_hidden=True):
    """Flattened window candidates, ordered visible -> minimized -> hidden/cloaked."""
    windows = [window
               for process in find_wechat_processes(path, include_hidden=include_hidden)
               for window in process["windows"]]
    windows.sort(key=lambda item: (item["status"] == STATUS_CLOAKED,
                                   not item["visible"], item["minimized"],
                                   item["kind"] != KIND_CHAT, int(item["hwnd"])))
    return windows


def wechat_presence(path, *, include_hidden=True):
    """One aggregate answer for the launch/connect button (read-only).

    Keys: ``running`` (a process exists), ``processes``/``windows`` (detail),
    ``hidden_windows``, ``visible_windows``, ``restorable`` (a non-cloaked
    window can be shown), ``tray_only`` (process alive but no top-level window).
    """
    try:
        expected = normalize_path(path) if path else ""
    except (AttributeError, TypeError, ValueError):
        expected = ""
    if not expected or expected == ".":
        return {"path": "", "running": False, "processes": [], "windows": [],
                "window_count": 0, "hidden_windows": 0, "visible_windows": 0,
                "restorable": False, "tray_only": False}
    processes = find_wechat_processes(expected, include_hidden=include_hidden)
    windows = [window for process in processes for window in process["windows"]]
    hidden = [window for window in windows if not window["visible"]]
    restorable = any(window["status"] in (STATUS_VISIBLE, STATUS_MINIMIZED, STATUS_HIDDEN)
                     for window in windows)
    return {
        "path": expected,
        "running": bool(processes),
        "processes": processes,
        "windows": windows,
        "window_count": len(windows),
        "hidden_windows": len(hidden),
        "visible_windows": len(windows) - len(hidden),
        "restorable": restorable,
        "tray_only": bool(processes) and not windows,
    }


def _restore_target(window):
    """Resolve restore_window input; fail closed for stale identities/bad handles."""
    if isinstance(window, dict):
        window = window.get("hwnd", window.get("window"))
    if isinstance(window, WindowTarget):
        return int(window) if valid(window) else None
    if isinstance(window, bool):
        return None
    if isinstance(window, int):
        if window and u32.IsWindow(window):
            return window
        return None
    return None


def restore_window(window, *, activate=False):
    """Show a hidden/minimized WeChat window on an explicit user action.

    ``window`` may be an item returned by :func:`discover` /
    :func:`find_wechat_windows`, its ``WindowTarget`` or a raw HWND.
    ``WindowTarget``/dict input is identity-verified first, so a reused HWND
    from a different process instance is never shown.

    Default behaviour never calls a force-foreground API: hidden windows are
    shown with ``SW_SHOWNOACTIVATE`` (verified to keep the foreground window
    unchanged) and there is no looping. Only the explicit "启动/连接" button may
    pass ``activate=True``; it first moves wholly offscreen windows to the
    primary work area, then uses native ``SW_RESTORE`` semantics.
    Note: Windows itself may activate a *minimized* window while un-minimizing
    it even with ``SW_SHOWNOACTIVATE``; this module still never calls
    SetForegroundWindow and never raises a window on its own.
    Returns False and touches nothing for cloaked windows, stale identities,
    invalid handles or when the window stays hidden. No SetParent, no style
    change, no input injection.
    """
    target = _restore_target(window)
    if target is None:
        return False
    hwnd = int(target)
    try:
        if window_cloaked(hwnd):
            # Cloaked windows (other virtual desktop / suspended) cannot be
            # shown with ShowWindow; do not pretend they were restored.
            return False
        if activate:
            if not _recover_offscreen(hwnd):
                return False
            u32.ShowWindow(hwnd, SW_RESTORE)
            if not u32.IsWindowVisible(hwnd):
                u32.ShowWindow(hwnd, SW_SHOW)
            # Un-minimizing may restore a separate, previously parked rectangle.
            if not _recover_offscreen(hwnd):
                return False
            return bool(u32.IsWindowVisible(hwnd) and not u32.IsIconic(hwnd)
                        and not window_offscreen(hwnd))
        u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        if not u32.IsWindowVisible(hwnd):
            # SW_SHOWNA ("show, keep active window active") for windows hidden
            # with SW_HIDE; still no activation.
            u32.ShowWindow(hwnd, SW_SHOWNA)
        return bool(u32.IsWindowVisible(hwnd))
    except (OSError, ValueError):
        return False
