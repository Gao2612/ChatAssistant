r"""C1 real-UI acceptance driver (current source version, isolated profile, no sending).

Launches a separate ChatAssistantLite from the source tree with an isolated
LOCALAPPDATA, then drives the settings page with native mouse/keyboard:
auto-connect -> set target -> click "自动查找并切换对话". Records only:
PID, app_version/commit, isolated profile, window hwnd/rect/title flags, WeChat
hwnd + preflight/postflight status and rect, boolean status categories and
timings. No contact name, chat text or screenshots are stored. No send button is
ever clicked. Only the child process started here is terminated.

Run:
    G:\codex\wechat-lite-venv\Scripts\python.exe tools\c1_ui_acceptance.py
"""

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes as W
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISOLATED = Path(tempfile.mkdtemp(prefix="c1-accept-profile-"))
os.environ["LOCALAPPDATA"] = str(ISOLATED)
os.environ.pop("QT_QPA_PLATFORM", None)

TARGET = os.environ.get("C1_TARGET", "田子夜")
WECHAT = os.environ.get("C1_WECHAT", r"C:\Program Files\Tencent\Weixin\Weixin.exe")
OUT_DIR = ROOT / "test-output" / "c1"
CREATE_NO_WINDOW = 0x08000000
PYTHON = sys.executable

from liteapp import build_info, discovery, settings  # noqa: E402
from liteapp.wechat_actions import Win32Backend  # noqa: E402

CURRENT_APP_VERSION = str(build_info.APP_VERSION)
SOURCE_WINDOW_TITLE = f"微信实时助手 · 便捷版 {CURRENT_APP_VERSION}"


def is_source_app_title(title):
    """Match the current source build's app window without accepting stale installs."""
    title = str(title or "")
    return "微信实时助手" in title and CURRENT_APP_VERSION in title

try:
    import uiautomation as auto
except ImportError:  # pragma: no cover - environment guard
    auto = None

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.GetForegroundWindow.restype = ctypes.c_void_p
u32.SetForegroundWindow.argtypes = [W.HWND]
u32.SetForegroundWindow.restype = W.BOOL
u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u32.mouse_event.argtypes = [W.DWORD, W.DWORD, W.DWORD, W.DWORD, ctypes.c_void_p]
u32.ClientToScreen.argtypes = [W.HWND, ctypes.POINTER(W.POINT)]
CALLBACK = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
u32.EnumWindows.argtypes = [CALLBACK, W.LPARAM]
u32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
u32.IsWindowVisible.argtypes = [W.HWND]
u32.IsWindowVisible.restype = W.BOOL
u32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u32.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]

MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_WHEEL = 0x0800

STATUS_FLAGS = ("已连接微信", "正在本地识别", "微信已停靠", "中央实时画面",
                "等待微信窗口", "发现多个微信窗口", "无法枚举窗口")
FIND_CATEGORIES = (
    ("success", ("已切换到目标对话",)),
    ("search_not_found", ("没有找到微信搜索框",)),
    ("result_not_found", ("搜索结果里没有找到",)),
    ("not_foreground", ("微信窗口不在前台",)),
    ("activate_failed", ("无法激活",)),
    ("capture_failed", ("无法获取微信画面",)),
    ("action_failed", ("自动查找对话失败", "自动操作未返回结果")),
)


def fg_window():
    return int(u32.GetForegroundWindow() or 0)


def window_metrics(hwnd):
    rect = W.RECT()
    u32.GetWindowRect(int(hwnd), ctypes.byref(rect))
    title = ctypes.create_unicode_buffer(256)
    u32.GetWindowTextW(hwnd, title, 256)
    return {"hwnd": int(hwnd),
            "rect": [rect.left, rect.top, rect.right, rect.bottom],
            "title_is_current_version": is_source_app_title(title.value),
            "status": discovery.window_status(int(hwnd))}


def window_pid(hwnd):
    pid = W.DWORD()
    u32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value)


TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", W.DWORD), ("cntUsage", W.DWORD), ("th32ProcessID", W.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", W.DWORD), ("cntThreads", W.DWORD),
        ("th32ParentProcessID", W.DWORD), ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", W.DWORD), ("szExeFile", W.WCHAR * 260),
    ]


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = W.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [W.DWORD, W.DWORD]
    kernel32.Process32FirstW.argtypes = [W.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = W.BOOL
    kernel32.Process32NextW.argtypes = [W.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = W.BOOL
    kernel32.CloseHandle.argtypes = [W.HANDLE]
    return kernel32


def _parent_map():
    kernel32 = _kernel32()
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == W.HANDLE(-1).value:
        return {}
    parents = {}
    entry = _PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def _descendants_of(root_pid, parents=None):
    """PIDs whose ancestor chain reaches ``root_pid`` (multiprocessing spawn children)."""
    parents = parents if parents is not None else _parent_map()
    out = set()
    for pid in parents:
        current, depth = pid, 0
        while current and current != root_pid and depth < 32:
            current = parents.get(current, 0)
            depth += 1
        if current == root_pid:
            out.add(pid)
    return out


def _window_pids_by_title(needle=None):
    if needle is None:
        needle = SOURCE_WINDOW_TITLE
    pids = []

    @CALLBACK
    def collect(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, title, 256)
        if needle in title.value:
            pid = window_pid(hwnd)
            if pid:
                pids.append(pid)
        return True

    u32.EnumWindows(collect, 0)
    return sorted(set(pids))


def _process_image(pid):
    """Exe path for ``pid`` via QueryFullProcessImageNameW; None if unreadable."""
    kernel32 = _kernel32()
    kernel32.OpenProcess.restype = W.HANDLE
    kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        W.HANDLE, W.DWORD, W.LPWSTR, ctypes.POINTER(W.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = W.BOOL
    handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(1024)
        size = W.DWORD(len(buffer))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return buffer.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def scan_stale_source_instances(record=None):
    """Record (never kill) foreign current-version source windows for review.

    Other agents can run isolated source instances too; a window title alone
    cannot prove ownership. Killing is therefore limited to PIDs this script
    itself spawned; anything else is only reported and left untouched.
    """
    seen = []
    for pid in _window_pids_by_title():
        seen.append({"pid": pid, "owner": "foreign", "image": _process_image(pid)})
    if record is not None and seen:
        record.setdefault("stale_instances_seen", []).extend(seen)
        print("STALE_SCAN", json.dumps(seen), flush=True)
    return seen


def _kill_own_tree(pid):
    """Terminate only a PID/tree we can prove we started (child.pid or descendant)."""
    subprocess.run(["taskkill", "/PID", str(int(pid)), "/T", "/F"],
                   capture_output=True)


def find_app_window(child_pid, timeout=70.0):
    """Visible current-version window owned by the process tree this run started."""
    deadline = time.monotonic() + timeout
    candidates = []

    @CALLBACK
    def collect(hwnd, _):
        if not u32.IsWindowVisible(hwnd):
            return True
        title = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, title, 256)
        if is_source_app_title(title.value):
            candidates.append(int(hwnd))
        return True

    while time.monotonic() < deadline:
        candidates.clear()
        u32.EnumWindows(collect, 0)
        if candidates:
            parents = _parent_map()
            mine = {child_pid} | _descendants_of(child_pid, parents)
            for hwnd in candidates:
                owner = window_pid(hwnd)
                if owner in mine:
                    return hwnd
        time.sleep(0.3)
    return None


def click_button_candidates(window_hwnd, control):
    """Click the find button trying screen/client/window-relative UIA coordinates."""
    rect = control.BoundingRectangle
    center_x = (rect.left + rect.right) // 2
    center_y = (rect.top + rect.bottom) // 2
    win = window_metrics(window_hwnd)
    wr = win["rect"]
    origin = W.POINT(0, 0)
    u32.ClientToScreen(int(window_hwnd), ctypes.byref(origin))
    candidates = [
        (center_x, center_y),                       # already screen coordinates
        (wr[0] + center_x, wr[1] + center_y),       # window-relative
        (origin.x + center_x, origin.y + center_y),  # client-area-relative
    ]
    for x, y in candidates:
        activate_app(window_hwnd)
        native_click(x, y)
        time.sleep(0.5)
        if fg_window() == int(window_hwnd):
            return True
    return False


def native_click(x, y):
    u32.SetCursorPos(int(x), int(y))
    time.sleep(0.12)
    u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
    u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
    time.sleep(0.25)


def wheel_down(times, x, y):
    u32.SetCursorPos(int(x), int(y))
    for _ in range(times):
        u32.mouse_event(MOUSEEVENTF_WHEEL, 0, 0, -120, None)
        time.sleep(0.15)


def app_node(hwnd):
    return auto.ControlFromHandle(int(hwnd))


def walk(node, limit=4000):
    out, stack = [], [node]
    while stack and len(out) < limit:
        item = stack.pop()
        out.append(item)
        try:
            stack.extend(reversed(item.GetChildren()))
        except Exception:  # noqa: BLE001
            continue
    return out


def texts(node):
    out = []
    for item in walk(node):
        try:
            name = item.Name or ""
        except Exception:  # noqa: BLE001
            continue
        if name:
            out.append(name)
    return out


def find_control(node, needle, control_type=None):
    for item in walk(node):
        try:
            name = item.Name or ""
            if control_type and item.ControlTypeName != control_type:
                continue
            if needle in name:
                return item
        except Exception:  # noqa: BLE001
            continue
    return None


def find_nav_control(app):
    for item in walk(app):
        try:
            name = item.Name or ""
            control_type = item.ControlTypeName
        except Exception:  # noqa: BLE001
            continue
        if control_type not in ("ButtonControl", "CheckBoxControl", "RadioButtonControl",
                                "ToolBarControl", "MenuItemControl"):
            continue
        if name in ("⚙", "设置"):
            return item
    return None


SETTINGS_MARKERS = ("微信连接", "刷新窗口", "连接所选窗口", "自动操作",
                    "自动查找并切换对话")


def is_settings_page(app):
    joined = "\n".join(texts(app))
    return any(marker in joined for marker in SETTINGS_MARKERS)


def control_center(control):
    rect = control.BoundingRectangle
    return int((rect.left + rect.right) / 2), int((rect.top + rect.bottom) / 2)


def click_control(control):
    x, y = control_center(control)
    native_click(x, y)


def click_control_in_window(control, window_hwnd):
    """Click a Qt control, tolerating UIA rectangles in client vs screen space."""
    rect = control.BoundingRectangle
    center_x = (rect.left + rect.right) // 2
    center_y = (rect.top + rect.bottom) // 2
    win = window_metrics(window_hwnd)
    wr = win["rect"]
    candidates = [(center_x, center_y),
                  (wr[0] + center_x, wr[1] + center_y)]
    # client-area origin (Qt reports client rects relative to it)
    client_x = wr[0] + (wr[2] - wr[0] - (win["rect"][2] - win["rect"][0])) // 2
    for x, y in candidates:
        activate_app(window_hwnd)
        native_click(x, y)
        time.sleep(0.4)
        if fg_window() == int(window_hwnd):
            return True
    return False


def flags(text_values):
    joined = "\n".join(text_values)
    return {flag: (flag in joined) for flag in STATUS_FLAGS}


def find_category(text_values):
    joined = "\n".join(text_values)
    for category, keywords in FIND_CATEGORIES:
        if any(keyword in joined for keyword in keywords):
            return category
    return "unknown"


def paste_text(hwnd, text):
    backend = Win32Backend()
    u32.SetForegroundWindow(int(hwnd))
    time.sleep(0.2)
    backend.key_combo((0x11, 0x41))  # Ctrl+A
    backend.set_clipboard_text(text)
    backend.key_combo((0x11, 0x56))  # Ctrl+V
    time.sleep(0.3)


def activate_app(window_hwnd):
    """Bring our isolated source window to the foreground without moving/resizing it."""
    try:
        Win32Backend().activate(int(window_hwnd))
    except (OSError, RuntimeError, ValueError):
        pass
    return fg_window() == int(window_hwnd)


def open_settings_page(app, window_hwnd):
    activate_app(window_hwnd)
    target = find_nav_control(app)
    if target is not None:
        try:
            click_control(target)
            time.sleep(0.8)
        except Exception:  # noqa: BLE001
            pass
    if is_settings_page(app):
        return True
    # Rail fallback: fixed rail width 62, brand 40 + spacing; settings is the 3rd
    # navigation button. Click a few plausible centers and verify the page.
    rect = W.RECT()
    u32.GetWindowRect(int(window_hwnd), ctypes.byref(rect))
    for offset in (185, 172, 199, 160, 145, 130, 112, 205):
        native_click(rect.left + 31, rect.top + offset)
        time.sleep(0.7)
        if is_settings_page(app):
            return True
    return False


def ensure_settings(app, window_hwnd, stage):
    if is_settings_page(app):
        return True
    opened = open_settings_page(app, window_hwnd)
    stage("settings_open", {"ok": bool(opened), "markers": list(SETTINGS_MARKERS)})
    return opened


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = {"target_len": len(TARGET),
              "app_version": build_info.collect().get("app_version"),
              "git": build_info.collect().get("git"),
              "isolated_localappdata": str(ISOLATED),
              "profile_dir": str(settings.data_dir()),
              "profile_isolated": str(settings.data_dir()).lower()
              .startswith(str(ISOLATED).lower()),
              "other_chatassistant_processes": [
                  entry["pid"] for entry in discovery.enumerate_processes()
                  if entry["exe_name"].lower() == "chatassistantlite.exe"],
              "screenshots_saved": False, "message_sent": False,
              "stages": [], "events": []}
    child = None
    app_hwnd = None
    wechat = None

    def stage(name, detail=None):
        entry = {"stage": name, "at": round(time.monotonic(), 3), "fg": fg_window()}
        if detail:
            entry["detail"] = detail
        report["stages"].append(entry)
        print("STAGE", json.dumps(entry, ensure_ascii=False), flush=True)

    def fail(reason):
        report["fatal"] = reason
        print("FATAL", reason, flush=True)

    try:
        if auto is None:
            fail("uiautomation_unavailable")
            return 2
        requested = os.environ.get("C1_HWND", "").strip()
        windows = discovery.find_wechat_windows(WECHAT, include_hidden=True)
        if requested:
            windows = [item for item in windows if int(item["hwnd"]) == int(requested)]
        wechat = next((item for item in windows if not item["tool_window"]), None)
        if wechat is None:
            fail("no_wechat_window")
            return 2
        report["wechat"] = window_metrics(wechat["hwnd"])
        report["wechat"]["preflight_status"] = discovery.window_status(int(wechat["hwnd"]))
        if report["wechat"]["preflight_status"] != "visible":
            report["aborted"] = "wechat_not_visible"
            print("ABORTED wechat_not_visible", flush=True)
            return 2
        stage("preflight_visible", {"wechat": report["wechat"]})
        # Record (do NOT kill) other current-version source instances; they may belong to
        # another takeover agent (e.g. C3). Killing is limited to our own PID tree.
        seen = scan_stale_source_instances(report)
        stage("stale_scan", {"seen": seen})

        cfg = settings.load()
        cfg.update({"wechat_path": WECHAT, "auto_connect": True,
                    "dock_wechat": False, "auto_analysis": False,
                    "auto_send_allowed": False})
        settings.save(cfg)
        env = dict(os.environ)
        env["LOCALAPPDATA"] = str(ISOLATED)
        child = subprocess.Popen([PYTHON, str(ROOT / "main.py")], cwd=str(ROOT),
                                 env=env, creationflags=CREATE_NO_WINDOW)
        stage("child_started", {"pid": child.pid})
        app_hwnd = find_app_window(child.pid, timeout=70.0)
        if app_hwnd is None:
            fail("current_source_app_window_not_found")
            return 2
        report["app_window"] = window_metrics(app_hwnd)
        report["app_window"]["pid"] = window_pid(app_hwnd)
        if not report["app_window"]["title_is_current_version"]:
            fail("source_app_version_mismatch")
            return 2
        app = app_node(app_hwnd)
        stage("app_window_found", {"hwnd": app_hwnd, "pid": report["app_window"]["pid"]})

        connected = False
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            current_flags = flags(texts(app))
            if current_flags["已连接微信"] or current_flags["正在本地识别"]:
                connected = True
                break
            time.sleep(0.7)
        report["connected"] = connected
        report["connect_flags"] = flags(texts(app))
        stage("after_auto_connect", {"connected": connected,
                                     "flags": report["connect_flags"]})
        if not connected:
            # Explicit fallback: refresh + connect through the real settings UI.
            if ensure_settings(app, app_hwnd, stage):
                refresh = find_control(app, "刷新窗口") or find_control(app, "刷新")
                if refresh is not None:
                    click_control(refresh)
                    time.sleep(2.0)
                    stage("clicked_refresh")
                connect = (find_control(app, "连接所选窗口")
                           or find_control(app, "连接"))
                if connect is not None:
                    click_control(connect)
                    time.sleep(1.0)
                    stage("clicked_connect")
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                current_flags = flags(texts(app))
                if current_flags["已连接微信"] or current_flags["正在本地识别"]:
                    connected = True
                    break
                time.sleep(0.7)
            report["connected"] = connected
            report["connect_flags"] = flags(texts(app))
            stage("after_explicit_connect", {"connected": connected,
                                             "flags": report["connect_flags"]})
        if not connected:
            fail("app_not_connected")
            return 2

        report["app_foreground"] = activate_app(app_hwnd)
        stage("app_activated", {"foreground": report["app_foreground"]})
        if not ensure_settings(app, app_hwnd, stage):
            fail("settings_page_not_found")
            return 2
        app_rect = window_metrics(app_hwnd)["rect"]
        cx = (app_rect[0] + app_rect[2]) // 2
        cy = (app_rect[1] + app_rect[3]) // 2
        wheel_down(6, cx, cy)
        button = find_control(app, "自动查找并切换对话", "ButtonControl")
        for _ in range(6):
            if button is not None:
                break
            wheel_down(4, cx, cy)
            button = find_control(app, "自动查找并切换对话", "ButtonControl")
        if button is None:
            fail("find_button_not_found")
            return 2
        stage("find_button_found")
        button_rect = button.BoundingRectangle
        edits = []
        for item in walk(app):
            try:
                if item.ControlTypeName != "EditControl":
                    continue
                rect = item.BoundingRectangle
                overlap = (min(rect.bottom, button_rect.bottom)
                           - max(rect.top, button_rect.top))
                if overlap > 0 and rect.right <= button_rect.left + 20:
                    edits.append((rect.right, item))
            except Exception:  # noqa: BLE001
                continue
        if not edits:
            fail("target_edit_not_found")
            return 2
        target_edit = sorted(edits, key=lambda pair: pair[0], reverse=True)[0][1]
        # Prefer the accessibility ValuePattern: it edits the control in-process and
        # is immune to the native-click coordinate/foreground issues.
        try:
            target_edit.GetValuePattern().SetValue(TARGET)
            report["target_filled_by"] = "value_pattern"
        except Exception:  # noqa: BLE001
            report["target_filled_by"] = "native_paste"
            activate_app(app_hwnd)
            click_control_in_window(target_edit, app_hwnd)
            paste_text(app_hwnd, TARGET)
        try:
            report["target_entered_matches"] = (
                target_edit.GetValuePattern().Value == TARGET)
        except Exception:  # noqa: BLE001
            report["target_entered_matches"] = None
        stage("target_entered", {"length": len(TARGET),
                                 "matches": report["target_entered_matches"]})
        # Prefer InvokePattern (in-process button activation) to native clicks; the
        # latter get stolen by other foreground windows on this machine.
        clicked = False
        try:
            button.GetInvokePattern().Invoke()
            clicked = True
            report["button_filled_by"] = "invoke_pattern"
        except Exception:  # noqa: BLE001
            report["button_filled_by"] = "native_click"
            clicked = click_control_in_window(button, app_hwnd)
        report["button_clicked"] = clicked
        stage("find_clicked", {"clicked": clicked, "fg": fg_window()})
        started_seen = False
        started_at = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if "正在查找微信对话" in "\n".join(texts(app)):
                started_seen = True
                started_at = time.monotonic()
                break
            time.sleep(0.3)
        if not started_seen:
            clicked = click_button_candidates(app_hwnd, button)
            report["button_clicked"] = clicked
            report["button_click_retried"] = True
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if "正在查找微信对话" in "\n".join(texts(app)):
                    started_seen = True
                    started_at = time.monotonic()
                    break
                time.sleep(0.3)
        report["find_started"] = started_seen
        stage("find_started", {"started": started_seen, "clicked": clicked})
        category = "unknown"
        find_finished = False
        if started_seen:
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                category = find_category(texts(app))
                if category != "unknown":
                    find_finished = True
                    break
                time.sleep(0.8)
        else:
            category = "click_not_delivered"
        report["find_finished"] = find_finished
        report["find_duration"] = (round(time.monotonic() - started_at, 2)
                                   if started_at else None)
        report["status_category"] = category
        report["switched"] = category == "success"
        report["title_seen"] = any(value == TARGET for value in texts(app))
        report["app_window_after"] = window_metrics(app_hwnd)
        report["button_rect"] = [button_rect.left, button_rect.top,
                                 button_rect.right, button_rect.bottom]
        stage("after_find", {"category": category, "switched": report["switched"],
                             "find_started": started_seen,
                             "find_finished": find_finished,
                             "duration": report["find_duration"]})
    except Exception as exc:  # noqa: BLE001
        report["fatal"] = {"type": type(exc).__name__, "text": str(exc)[:300],
                           "trace": traceback.format_exc()[-1500:]}
        print("EXCEPTION", type(exc).__name__, str(exc)[:200], flush=True)
    finally:
        if child is not None and child.poll() is None:
            subprocess.run(["taskkill", "/PID", str(child.pid), "/T", "/F"],
                           capture_output=True)
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
        # Terminate the GUI child only when it belongs to OUR launched PID tree;
        # never another agent's source instance or the installed user app.
        if child is not None and app_hwnd is not None and window_pid(app_hwnd) > 0:
            owner = window_pid(app_hwnd)
            parents = _parent_map()
            mine = {int(child.pid)} | _descendants_of(int(child.pid), parents)
            report["app_owner_pid"] = owner
            report["app_owner_in_own_tree"] = owner in mine
            if owner in mine:
                _kill_own_tree(owner)
        # Record other current-version source instances for review (never kill).
        scan_stale_source_instances(report)
        # P2-10 postflight: WeChat must remain visible and keep its preflight rect.
        try:
            wechat_after = window_metrics(int(report["wechat"]["hwnd"]))
            report["wechat_after"] = wechat_after
            report["wechat_visible_after"] = wechat_after["status"] == "visible"
            report["wechat_rect_unchanged"] = (
                wechat_after["rect"] == report["wechat"]["rect"])
            print("POSTFLIGHT", json.dumps(
                {"visible": report["wechat_visible_after"],
                 "rect_unchanged": report["wechat_rect_unchanged"]}), flush=True)
        except Exception as exc:  # noqa: BLE001
            report["wechat_after"] = {"error": type(exc).__name__}
        stamp = time.strftime("%Y%m%d-%H%M%S")
        out_path = OUT_DIR / f"c1_ui_acceptance_{stamp}.json"
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        report["saved"] = str(out_path)
        print(json.dumps({"saved": str(out_path),
                          "fatal": report.get("fatal"),
                          "aborted": report.get("aborted"),
                          "connected": report.get("connected"),
                          "status_category": report.get("status_category"),
                          "switched": report.get("switched"),
                          "title_seen": report.get("title_seen"),
                          "wechat_visible_after": report.get("wechat_visible_after"),
                          "wechat_rect_unchanged": report.get("wechat_rect_unchanged")},
                         ensure_ascii=False), flush=True)
    ok = (not report.get("fatal") and not report.get("aborted")
          and report.get("switched"))
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())






