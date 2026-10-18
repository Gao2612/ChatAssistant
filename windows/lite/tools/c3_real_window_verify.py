"""C3 real-window acceptance probe for ChatAssistant Lite 0.5.8 source tree.

Scope / privacy
---------------
* Records only window rectangles, booleans, counts, and assistant *status* texts.
  The connected-chat editor is reduced to a length; reply/analysis text is never read.
* Never saves a screenshot, never reads chat content, never sends a WeChat message.
* The real WeChat window is only touched for the four accepted scenarios
  (dock on/off, temporary minimize, real popup, tray hide/restore).  Every script
  path ends with WeChat visible (finally-guarded) and the geometry restored to the
  preflight rectangle.

Acceptance target
-----------------
0.5.8 source tree at commit a3b2994 (run with the project venv; not the installed 0.5.7).

Safety preflight
----------------
The probe refuses to start unless ``discovery.window_status(wechat) == "visible"``.
It never moves/hides/minimizes the window before that check.

Usage
-----
    G:\\codex\\wechat-lite-venv\\Scripts\\python.exe tools\\c3_real_window_verify.py
    ... --steps dock,minimize,popup,tray   # subset of scenarios
    ... --evidence-dir test-output/c3-runs/run1
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

LITE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = LITE_ROOT.parents[1]
if str(LITE_ROOT) not in sys.path:
    sys.path.insert(0, str(LITE_ROOT))

SOURCE_TREE_VERSION = "0.5.8"
SOURCE_TREE_COMMIT = "a3b2994"
GEOMETRY_TOLERANCE_PX = 2
DEFAULT_APP_LIMIT_S = 40.0
GIT_CANDIDATES = (r"C:\Program Files\Git\cmd\git.exe",
                  r"C:\Program Files\Git\bin\git.exe")

# --- status protocol fragments only; NEVER chat/analysis content --------------
# Every accepted text must contain one of these assistant protocol messages (see
# liteapp/ui.py and liteapp/worker.py).  A chat message or generated suggestion that
# merely happens to contain "微信"/"识别" is therefore never written to evidence.
STATUS_FRAGMENTS = (
    "正在本地识别", "已连接微信，正在启动本地识别", "微信已停靠",
    "微信弹窗覆盖聊天区域", "微信已最小化", "微信已隐藏到托盘",
    "微信在运行但窗口不可见", "等待微信窗口", "发现多个微信窗口",
    "窗口层级未确认", "窗口句柄尚未就绪", "采集画面已过期",
    "采集进程已停止", "微信窗口识别已停止", "微信窗口状态异常",
    "微信处于登录窗口", "微信窗口身份已变化", "微信窗口已关闭",
    "没有收到可识别画面", "请打开一个聊天会话", "没有识别到会话名称",
    "会话名称未确认", "采集失败", "画面有更新，正在重新识别",
    "无法枚举窗口", "已断开微信连接", "设置已保存", "已恢复其窗口",
)
STATUS_MAX_CHARS = 80
BUTTON_NAMES = ("发送到微信", "复制", "连接所选窗口", "启动微信", "断开连接",
                "保存设置", "返回聊天", "刷新窗口", "自动查找并切换对话", "停止")

SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
WM_CLOSE = 0x0010
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_CANCELMODE = 0x001F
WM_MOUSEWHEEL = 0x020A
MK_LBUTTON, MK_RBUTTON = 0x0001, 0x0002
VK_ESCAPE = 0x1B

u32 = ctypes.windll.user32
k32 = ctypes.windll.kernel32
u32.PostMessageW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
u32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
u32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
u32.SetWindowPos.argtypes = [wt.HWND, wt.HWND, ctypes.c_int, ctypes.c_int,
                             ctypes.c_int, ctypes.c_int, wt.UINT]
u32.SetWindowPos.restype = wt.BOOL
u32.ShowWindow.argtypes = [wt.HWND, ctypes.c_int]
u32.ShowWindow.restype = wt.BOOL
u32.IsIconic.argtypes = [wt.HWND]
u32.IsIconic.restype = wt.BOOL
u32.IsWindowVisible.argtypes = [wt.HWND]
u32.IsWindowVisible.restype = wt.BOOL
u32.IsWindow.argtypes = [wt.HWND]
u32.IsWindow.restype = wt.BOOL
u32.GetForegroundWindow.restype = wt.HWND
u32.WindowFromPoint.argtypes = [wt.POINT]
u32.WindowFromPoint.restype = wt.HWND
u32.GetAncestor.argtypes = [wt.HWND, wt.UINT]
u32.GetAncestor.restype = wt.HWND
k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
k32.OpenProcess.restype = wt.HANDLE
k32.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
k32.CloseHandle.argtypes = [wt.HANDLE]

STILL_ACTIVE = 259


class _Point(ctypes.Structure):
    _fields_ = [("x", wt.LONG), ("y", wt.LONG)]


class _Rect32(ctypes.Structure):
    _fields_ = [("left", wt.LONG), ("top", wt.LONG),
                ("right", wt.LONG), ("bottom", wt.LONG)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wt.UINT), ("flags", wt.UINT), ("showCmd", wt.UINT),
                ("ptMinPosition", _Point), ("ptMaxPosition", _Point),
                ("rcNormalPosition", _Rect32)]


u32.GetWindowPlacement.argtypes = [wt.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
u32.GetWindowPlacement.restype = wt.BOOL


# ---------------------------------------------------------------------------
# pure helpers (unit-tested in tests/test_c3_evidence_helpers.py)
# ---------------------------------------------------------------------------

def status_texts(names):
    """Keep only known assistant *protocol/status* messages (never chat content).

    A text is accepted only when it contains a unique fragment of one of the
    status/notice strings emitted by the app (see ``STATUS_FRAGMENTS``).  Contact
    names, chat messages and generated suggestions are therefore dropped even if
    they contain words like "微信" or "识别".
    """
    seen, result = set(), []
    for name in names:
        text = " ".join(str(name or "").split())
        if not text or len(text) > STATUS_MAX_CHARS or text in seen:
            continue
        if not any(fragment in text for fragment in STATUS_FRAGMENTS):
            continue
        seen.add(text)
        result.append(text)
    return result


def rect_close(first, second, tolerance=GEOMETRY_TOLERANCE_PX):
    """True when both 4-tuples are within ``tolerance`` physical pixels."""
    if first is None or second is None or len(first) != 4 or len(second) != 4:
        return False
    return all(abs(int(a) - int(b)) <= tolerance for a, b in zip(first, second))


def rect_inside(inner, outer, margin=1):
    """True when ``inner`` is strictly inside ``outer`` with at least ``margin`` px."""
    if inner is None or outer is None:
        return False
    return (inner[0] >= outer[0] + margin and inner[1] >= outer[1] + margin
            and inner[2] <= outer[2] - margin and inner[3] <= outer[3] - margin)


def rect_size(rect):
    return None if rect is None else [rect[2] - rect[0], rect[3] - rect[1]]


# ---------------------------------------------------------------------------
# tiny Win32 wrappers
# ---------------------------------------------------------------------------

def process_alive(pid):
    if not pid:
        return False
    handle = k32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return False
    try:
        code = wt.DWORD(0)
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def foreground_window():
    return int(u32.GetForegroundWindow() or 0)


def window_from_point(x, y):
    return int(u32.WindowFromPoint(wt.POINT(int(x), int(y))) or 0)


def root_window(hwnd):
    hwnd = int(hwnd or 0)
    if not hwnd:
        return 0
    return int(u32.GetAncestor(hwnd, 2) or hwnd)  # GA_ROOT


def client_point(hwnd, screen_xy):
    origin = wt.POINT(0, 0)
    rect = wt.RECT()
    if not u32.GetClientRect(int(hwnd), ctypes.byref(rect)):
        return None
    if not u32.ClientToScreen(int(hwnd), ctypes.byref(origin)):
        return None
    return (screen_xy[0] - origin.x, screen_xy[1] - origin.y,
            rect.right, rect.bottom)


def post_mouse(hwnd, screen_xy, right=False):
    """Post one click to a top-level window without activating it (no focus steal)."""
    point = client_point(hwnd, screen_xy)
    if point is None:
        return {"ok": False, "reason": "no_client_point"}
    x, y, width, height = point
    if not (0 <= x < width and 0 <= y < height):
        return {"ok": False, "reason": "outside_client", "client": [x, y],
                "client_size": [width, height]}
    lparam = (y & 0xFFFF) << 16 | (x & 0xFFFF)
    down = WM_RBUTTONDOWN if right else WM_LBUTTONDOWN
    up = WM_RBUTTONUP if right else WM_LBUTTONUP
    mask = MK_RBUTTON if right else MK_LBUTTON
    first = bool(u32.PostMessageW(int(hwnd), down, mask, lparam))
    time.sleep(0.05)
    second = bool(u32.PostMessageW(int(hwnd), up, 0, lparam))
    return {"ok": first and second, "client": [x, y]}


def post_key(hwnd, vk=VK_ESCAPE):
    scan = u32.MapVirtualKeyW(int(vk), 0)
    lparam = 1 | (scan << 16)
    ok = bool(u32.PostMessageW(int(hwnd), WM_KEYDOWN, vk, lparam))
    time.sleep(0.03)
    ok = bool(u32.PostMessageW(int(hwnd), WM_KEYUP, vk,
                               lparam | (1 << 30) | (1 << 31))) and ok
    return ok


def post_wheel(hwnd, screen_xy, delta=-120, times=1):
    """Post mouse-wheel notches to a window without activating it (scroll only)."""
    lparam = ((int(screen_xy[1]) & 0xFFFF) << 16) | (int(screen_xy[0]) & 0xFFFF)
    wparam = (int(delta) & 0xFFFF) << 16
    for _ in range(int(times)):
        if not u32.PostMessageW(int(hwnd), WM_MOUSEWHEEL, wparam, lparam):
            return False
        time.sleep(0.12)
    return True


def git_executable():
    for candidate in GIT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    return "git"


def git_output(*arguments):
    """Run one read-only git command in the repository; empty string on failure."""
    try:
        result = subprocess.run([git_executable(), "-C", str(REPO_ROOT), *arguments],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=60)
        return (result.stdout or "").strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def repo_git_info():
    """Read-only provenance of the checkout the acceptance source was exported from."""
    head = git_output("rev-parse", "HEAD")
    branch = git_output("rev-parse", "--abbrev-ref", "HEAD")
    dirty = git_output("status", "--porcelain")
    ancestor = ""
    if head:
        result = subprocess.run(
            [git_executable(), "-C", str(REPO_ROOT), "merge-base", "--is-ancestor",
             SOURCE_TREE_COMMIT, head], capture_output=True, timeout=60)
        ancestor = "yes" if result.returncode == 0 else "no"
    return {"head": head, "branch": branch,
            "dirty_tracked": bool([line for line in dirty.splitlines()
                                   if line and not line.startswith("??")]),
            "untracked_count": sum(1 for line in dirty.splitlines()
                                   if line.startswith("??")),
            "pinned_commit": SOURCE_TREE_COMMIT,
            "pinned_is_ancestor_of_head": ancestor}


# ---------------------------------------------------------------------------
# UIA adapter (uiautomation is imported lazily so unit tests stay headless)
# ---------------------------------------------------------------------------

class Ui:
    def __init__(self, app):
        self.app = app
        self.hwnd = int(getattr(app, "NativeWindowHandle", 0) or 0)

    def walk(self, limit=9000):
        out = []
        stack = [(self.app, 0)]
        while stack and len(out) < limit:
            item, depth = stack.pop()
            out.append((item, depth))
            try:
                children = item.GetChildren()
            except Exception:
                children = []
            for child in reversed(children):
                stack.append((child, depth + 1))
        return out

    @staticmethod
    def name(item):
        try:
            return item.Name or ""
        except Exception:
            return ""

    @staticmethod
    def ctype(item):
        try:
            return item.ControlTypeName
        except Exception:
            return ""

    @staticmethod
    def rect(item):
        try:
            box = item.BoundingRectangle
            return [int(box.left), int(box.top), int(box.right), int(box.bottom)]
        except Exception:
            return None

    def center(self, item):
        rect = self.rect(item)
        if not rect:
            return None
        return ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)

    def find(self, predicate):
        for item, depth in self.walk():
            if predicate(self.name(item), self.ctype(item), item, depth):
                return item
        return None

    def find_all(self, predicate):
        return [item for item, depth in self.walk()
                if predicate(self.name(item), self.ctype(item), item, depth)]

    def click(self, item):
        point = self.center(item)
        if point is None:
            return {"ok": False, "reason": "no_rect"}
        return post_mouse(self.hwnd, point)

    def click_named(self, control_type=None, name_exact=None, name_contains=None):
        def match(name, ctype, item, depth):
            if control_type is not None and ctype != control_type:
                return False
            if name_exact is not None and name != name_exact:
                return False
            if name_contains is not None and name_contains not in name:
                return False
            return True
        item = self.find(match)
        return None if item is None else self.click(item)

    def toggle_state(self, item):
        try:
            return int(item.GetTogglePattern().ToggleState)
        except Exception:
            return None

    def find_checkbox(self, name_contains):
        return self.find(lambda n, t, i, d: t == "CheckBoxControl"
                         and name_contains in n)

    def find_button(self, name_exact):
        return self.find(lambda n, t, i, d: t == "ButtonControl"
                         and n == name_exact)

    def resize(self, width, height, left=120, top=80):
        ok = bool(u32.SetWindowPos(self.hwnd, 0, int(left), int(top),
                                   int(width), int(height),
                                   SWP_NOZORDER | SWP_NOACTIVATE))
        time.sleep(0.8)
        return ok

    def wheel_settings(self, notches=1, delta=-120):
        """Scroll the settings page without activating the assistant window."""
        rect = self.rect(self.app)
        if not rect:
            return False
        point = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
        return post_wheel(self.hwnd, point, delta, notches)

    @staticmethod
    def _clickable(rect, hwnd):
        if not rect:
            return False
        point = client_point(hwnd, ((rect[0] + rect[2]) // 2,
                                    (rect[1] + rect[3]) // 2))
        if point is None:
            return False
        x, y, width, height = point
        return 0 <= x < width and 8 <= y <= height - 8

    def scroll_to_button(self, name_exact, max_steps=40):
        """Wheel-scroll up until a settings button is clickable."""
        item = self.find_button(name_exact)
        for _ in range(max_steps):
            if item is None:
                item = self.find_button(name_exact)
            if item is not None and self._clickable(self.rect(item), self.hwnd):
                return item
            self.wheel_settings(delta=120)
            time.sleep(0.05)
            item = self.find_button(name_exact)
        return item

    def scroll_to_checkbox(self, name_contains, max_steps=40):
        """Wheel-scroll the settings page until the checkbox can be clicked."""
        item = self.find_checkbox(name_contains)
        for _ in range(max_steps):
            if item is None:
                item = self.find_checkbox(name_contains)
            if item is not None and self._clickable(self.rect(item), self.hwnd):
                return item
            self.wheel_settings()
            time.sleep(0.05)
            item = self.find_checkbox(name_contains)
        return item

    def set_checkbox(self, name_contains, checked):
        """Set one settings checkbox via its accessibility TogglePattern.

        Falls back to a posted mouse click on the indicator (never activates the
        assistant window).  Returns before/after state and the action used.
        """
        expected = 1 if checked else 0
        dock = self.scroll_to_checkbox(name_contains)
        if dock is None:
            return {"ok": False, "reason": "checkbox_missing"}
        before = self.toggle_state(dock)
        attempts = []
        if before == expected:
            return {"ok": True, "before": before, "after": before,
                    "action": "already", "attempts": attempts}
        try:
            dock.GetTogglePattern().Toggle()
            attempts.append("toggle_pattern")
        except Exception as exc:  # noqa: BLE001
            attempts.append("toggle_pattern:" + type(exc).__name__)
        time.sleep(0.7)
        dock = self.find_checkbox(name_contains)
        after = self.toggle_state(dock) if dock else None
        if after != expected and dock is not None:
            rect = self.rect(dock)
            if rect:
                point = (rect[0] + 8, (rect[1] + rect[3]) // 2)
                attempts.append("mouse_indicator")
                post_mouse(self.hwnd, point)
                time.sleep(0.7)
            dock = self.find_checkbox(name_contains)
            after = self.toggle_state(dock) if dock else None
        return {"ok": after == expected, "before": before, "after": after,
                "action": attempts[-1] if attempts else None, "attempts": attempts}

    def invoke(self, item):
        try:
            item.GetInvokePattern().Invoke()
            return "invoke"
        except Exception as exc:  # noqa: BLE001
            return type(exc).__name__

    # -- text/state snapshots --------------------------------------------
    def _main_editor_rect(self):
        """Rectangle of the big read-only chat editor (content must never be read)."""
        for item, depth in self.walk():
            if self.ctype(item) != "EditControl":
                continue
            rect = self.rect(item)
            if rect and (rect[3] - rect[1]) >= 100:
                return rect
        return None

    @staticmethod
    def _inside(inner, outer):
        if not inner or not outer:
            return False
        cx = (inner[0] + inner[2]) // 2
        cy = (inner[1] + inner[3]) // 2
        return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]

    def statuses(self):
        """Only single-line TextControl labels outside the chat-editor area."""
        editor = self._main_editor_rect()
        names = []
        for item, depth in self.walk():
            if self.ctype(item) != "TextControl":
                continue
            rect = self.rect(item)
            if not rect or (rect[3] - rect[1]) > 60:
                continue
            try:
                if bool(item.IsOffscreen):
                    continue  # hidden page (settings/history) keeps stale labels
            except Exception:
                pass
            if self._inside(rect, editor):
                continue
            names.append(self.name(item))
        return status_texts(names)

    def wait_status(self, needle, timeout=10.0, interval=0.4):
        return self.wait_status_any((needle,), timeout=timeout, interval=interval)

    def wait_status_any(self, needles, timeout=10.0, interval=0.4):
        deadline = time.monotonic() + float(timeout)
        seen = []
        while time.monotonic() < deadline:
            current = self.statuses()
            for text in current:
                if text not in seen:
                    seen.append(text)
            for needle in needles:
                hit = next((text for text in current if needle in text), None)
                if hit:
                    return {"ok": True, "text": hit, "seen": seen}
            time.sleep(interval)
        return {"ok": False, "seen": seen}

    def track_statuses(self, seconds, interval=0.4):
        deadline = time.monotonic() + float(seconds)
        seen = []
        while time.monotonic() < deadline:
            for text in self.statuses():
                if text not in seen:
                    seen.append(text)
            time.sleep(interval)
        return seen

    def edit_lengths(self):
        """Lengths of the large read-only chat editor only (never its content)."""
        lengths = []
        for item, depth in self.walk():
            if self.ctype(item) != "EditControl":
                continue
            rect = self.rect(item)
            if not rect or (rect[3] - rect[1]) < 100:
                continue
            try:
                lengths.append(len(item.GetValuePattern().Value or ""))
            except Exception:
                lengths.append(None)
        return lengths

    def buttons(self):
        result = []
        for item, depth in self.walk():
            name = self.name(item)
            if self.ctype(item) == "ButtonControl" and name in BUTTON_NAMES:
                try:
                    enabled = bool(item.IsEnabled)
                except Exception:
                    enabled = None
                result.append({"name": name, "enabled": enabled})
        return result


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

class C3Probe:
    def __init__(self, evidence_dir, steps, source_root=None):
        from liteapp import discovery  # import check + discovery primitives
        self.discovery = discovery
        self.steps = steps
        # Acceptance source tree: commit-bound export (git archive) or this checkout.
        self.source_root = (Path(source_root).resolve() if source_root is not None
                            else LITE_ROOT)
        self.evidence_dir = Path(evidence_dir)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.profile_root = self.evidence_dir / "localappdata"
        self.data_dir = self.profile_root / "ChatAssistantLite"
        self.records = []
        self.checks = []
        self.launcher_pid = None
        self.app_pid = None
        self.app_hwnd = 0
        self.ui = None
        self.wechat = None
        self.wechat_hwnd = 0
        self.baseline_rect = None
        self.dock_original_rect = None
        self.tray_original_rect = None
        self.stdout_path = None
        self.stderr_path = None
        self._stdout_file = None
        self._stderr_file = None
        self.environment_events = []
        self.version_info = {}
        self._wx_path = ""
        self.evidence_path = None
        self.started_at = datetime.now().astimezone().isoformat(timespec="seconds")

    # -- evidence ---------------------------------------------------------
    def record(self, step, **data):
        entry = {"step": step,
                 "at": datetime.now().astimezone().isoformat(timespec="seconds")}
        entry.update(data)
        self.records.append(entry)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        return entry

    def check(self, check_id, ok, **detail):
        entry = {"check": check_id,
                 "ok": bool(ok) if ok is not None else None}
        entry.update(detail)
        self.checks.append(entry)
        print("[check] " + json.dumps(entry, ensure_ascii=False), flush=True)
        return entry

    # -- environment ------------------------------------------------------
    def read_version(self):
        command = [sys.executable, str(self.source_root / "main.py"),
                   "--version-json"]
        result = subprocess.run(command, cwd=str(self.source_root), capture_output=True,
                                text=True, encoding="utf-8", errors="replace",
                                timeout=180)
        info = {}
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if line.startswith("{"):
                try:
                    info = json.loads(line)
                    break
                except ValueError:
                    continue
        info["returncode"] = result.returncode
        info["repo"] = repo_git_info()
        # Provenance of a git-archive exported source tree (if the caller staged one).
        provenance = self.source_root / "SOURCE_COMMIT.txt"
        if provenance.is_file():
            try:
                info["source_export"] = provenance.read_text(
                    encoding="utf-8", errors="replace").strip()
            except OSError:
                info["source_export"] = ""
        self.version_info = info
        return info

    def find_wechat_window(self):
        discovery = self.discovery
        paths = discovery.running_wechat_paths()
        if not paths:
            return None, {"running": False}
        self._wx_path = paths[0]
        windows = discovery.find_wechat_windows(paths[0], include_hidden=True)
        main = [item for item in windows
                if not item.get("tool_window") and item.get("title") == "微信"]
        detail = {"running": True, "candidate_count": len(windows),
                  "main_count": len(main),
                  "statuses": [item.get("status") for item in windows]}
        return (main[0] if len(main) == 1 else None), detail

    def prepare_profile(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        config = {
            "wechat_path": self._wx_path,
            "auto_connect": True,
            "auto_analysis": False,
            "dock_wechat": False,
            "background_monitor": False,
            "reading_backend": "ocr",
            # A local backend + model keeps the settings page's validation happy so
            # "保存设置" persists the dock flag; nothing is ever requested (offline
            # address, automatic analysis disabled, no fetch button pressed).
            "backend": "local",
            "base_url": "http://127.0.0.1:11434/v1",
            "local_model": "c3-probe-model",
            "model": "c3-probe-model",
            "auto_send_allowed": False,
        }
        (self.data_dir / "settings.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return config

    def app_window_candidates(self):
        """Top-level 0.5.8 source-run windows (installed 0.5.7/0.5.8 are excluded)."""
        import uiautomation as auto
        result = []
        try:
            windows = auto.GetRootControl().GetChildren()
        except Exception:
            return result
        for win in windows:
            try:
                name = win.Name or ""
            except Exception:
                continue
            if "微信实时助手" not in name or SOURCE_TREE_VERSION not in name:
                continue
            try:
                pid = int(win.ProcessId)
            except Exception:
                continue
            identity = self.discovery.process_identity(pid) or {}
            path = (identity.get("path") or "").lower()
            if "python" not in path.split("\\")[-1]:
                continue  # frozen installed build is not the acceptance target
            result.append({"window": win, "pid": pid,
                           "created": int(identity.get("created") or 0),
                           "path": path})
        return result

    def launch_app(self):
        env = dict(os.environ)
        env["LOCALAPPDATA"] = str(self.profile_root)
        env["PYTHONIOENCODING"] = "utf-8"
        creation = 0x08000000  # CREATE_NO_WINDOW
        # The project venv python.exe may be a launcher stub that starts the base
        # runtime as a child; therefore the Qt window owner is resolved by title +
        # process creation time instead of assuming launcher pid == window pid.
        self.stdout_path = self.evidence_dir / "app-stdout.log"
        self.stderr_path = self.evidence_dir / "app-stderr.log"
        self._stdout_file = open(self.stdout_path, "ab")
        self._stderr_file = open(self.stderr_path, "ab")
        self.launcher_pid = subprocess.Popen(
            [sys.executable, str(self.source_root / "main.py")],
            cwd=str(self.source_root), env=env, creationflags=creation,
            stdout=self._stdout_file, stderr=self._stderr_file).pid
        started = time.time()
        not_before = int((started + 11644473600) * 10_000_000) - 60_000_000
        deadline = time.monotonic() + DEFAULT_APP_LIMIT_S
        while time.monotonic() < deadline:
            candidates = [item for item in self.app_window_candidates()
                          if item["created"] >= not_before]
            if candidates:
                candidate = max(candidates, key=lambda item: item["created"])
                self.ui = Ui(candidate["window"])
                self.app_pid = candidate["pid"]
                self.app_hwnd = self.ui.hwnd
                return self.app_pid
            time.sleep(0.4)
        raise RuntimeError("主窗口未出现")

    # -- WeChat restore primitives ---------------------------------------
    def _restored_rect(self, hwnd=None):
        """Normal placement rectangle (GetWindowPlacement) for minimized windows."""
        hwnd = int(hwnd if hwnd is not None else self.wechat)
        if not u32.IsWindow(hwnd):
            return None
        placement = WINDOWPLACEMENT()
        placement.length = ctypes.sizeof(WINDOWPLACEMENT)
        if u32.GetWindowPlacement(hwnd, ctypes.byref(placement)):
            rect = placement.rcNormalPosition
            return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
        return self.discovery.window_rect(hwnd)

    def force_visible(self):
        """Restore WeChat visible and its preflight rectangle; retried, never raises."""
        discovery = self.discovery
        detail = {"attempts": 0}
        if not self.wechat:
            return {"ok": False, "reason": "no_target"}
        hwnd = int(self.wechat)
        for _ in range(6):
            detail["attempts"] += 1
            try:
                if not discovery.valid(self.wechat):
                    break
                if u32.IsIconic(hwnd):
                    u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                    time.sleep(0.4)
                if not u32.IsWindowVisible(hwnd):
                    discovery.restore_window(self.wechat, activate=False)
                    time.sleep(0.4)
                if u32.IsIconic(hwnd):
                    u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
                    time.sleep(0.3)
                rect = self._restored_rect(hwnd)
                if (self.baseline_rect is not None
                        and not rect_close(rect, self.baseline_rect)):
                    u32.SetWindowPos(hwnd, 0, self.baseline_rect[0],
                                     self.baseline_rect[1],
                                     self.baseline_rect[2] - self.baseline_rect[0],
                                     self.baseline_rect[3] - self.baseline_rect[1],
                                     SWP_NOZORDER | SWP_NOACTIVATE)
                    time.sleep(0.3)
                if (u32.IsWindowVisible(hwnd) and not u32.IsIconic(hwnd)
                        and rect_close(self._restored_rect(hwnd),
                                       self.baseline_rect)):
                    detail.update({"ok": True, "visible": True,
                                   "rect_restored": True,
                                   "status": discovery.window_status(hwnd)})
                    return detail
            except Exception as exc:  # noqa: BLE001 - cleanup must never raise
                detail.setdefault("errors", []).append(type(exc).__name__)
            time.sleep(0.5)
        try:
            detail.update({"ok": False,
                           "visible": bool(u32.IsWindowVisible(hwnd)),
                           "minimized": bool(u32.IsIconic(hwnd)),
                           "rect": discovery.window_rect(hwnd),
                           "status": discovery.window_status(hwnd)})
        except Exception:
            pass
        return detail

    def wait_connected(self, timeout=45.0):
        return self.ui.wait_status_any(
            ("正在本地识别", "微信已停靠", "已连接微信"), timeout=timeout)

    def snapshot(self, note=""):
        ui = self.ui
        wechat = self.wechat
        rect = self.discovery.window_rect(int(wechat)) if wechat else None
        return {
            "note": note,
            "wechat_rect": rect,
            "wechat_visible": bool(u32.IsWindowVisible(int(wechat))) if wechat else None,
            "wechat_minimized": bool(u32.IsIconic(int(wechat))) if wechat else None,
            "wechat_status": (self.discovery.window_status(int(wechat))
                              if wechat else None),
            "app_rect": ui.rect(ui.app) if ui else None,
            "app_visible": bool(u32.IsWindowVisible(ui.hwnd)) if ui else None,
            "app_alive": process_alive(self.app_pid) if self.app_pid else None,
            "foreground_is_wechat": (foreground_window() == int(wechat))
            if wechat else None,
            "foreground_is_app": (foreground_window() == ui.hwnd) if ui else None,
            "statuses": ui.statuses() if ui else [],
            "edit_lengths": ui.edit_lengths() if ui else [],
            "buttons": ui.buttons() if ui else [],
        }

    # -- diagnostics ------------------------------------------------------
    def app_log_tail(self, limit=1200):
        """Tail of the app's stdout/stderr (bounded, no chat content by design)."""
        tails = {}
        for name, path in (("stdout", self.stdout_path), ("stderr", self.stderr_path)):
            try:
                if path and path.exists():
                    data = path.read_bytes()[-limit:]
                    tails[name] = data.decode("utf-8", "replace")
                else:
                    tails[name] = ""
            except OSError:
                tails[name] = ""
        return tails

    def window_moved_externally(self, label, expected):
        current = self.discovery.window_rect(int(self.wechat))
        if current is None or not rect_close(current, expected):
            self.environment_events.append({"at": label, "expected": expected,
                                            "actual": current})
            self.record(label + ".environment_changed", expected=expected,
                        actual=current,
                        note="external geometry change; scenario aborted")
            return True
        return False

    def child_pids(self, parent_pid):
        """Direct child process ids via Toolhelp (read-only, best effort)."""
        import ctypes as _ct
        entry_type = getattr(self.discovery, "PROCESSENTRY32W", None)
        snapshot_type = getattr(self.discovery, "TH32CS_SNAPPROCESS", None)
        if entry_type is None or snapshot_type is None:
            return []
        k32 = _ct.windll.kernel32
        k32.CreateToolhelp32Snapshot.argtypes = [_ct.c_ulong, _ct.c_ulong]
        k32.CreateToolhelp32Snapshot.restype = _ct.c_void_p
        k32.Process32FirstW.argtypes = [_ct.c_void_p, _ct.POINTER(entry_type)]
        k32.Process32NextW.argtypes = [_ct.c_void_p, _ct.POINTER(entry_type)]
        k32.CloseHandle.argtypes = [_ct.c_void_p]
        handle = k32.CreateToolhelp32Snapshot(snapshot_type, 0)
        if not handle or int(handle) in (-1, 0xFFFFFFFFFFFFFFFF):
            return []
        found = []
        try:
            entry = entry_type()
            entry.dwSize = _ct.sizeof(entry_type)
            ok = k32.Process32FirstW(handle, _ct.byref(entry))
            while ok:
                if int(entry.th32ParentProcessID) == int(parent_pid):
                    found.append(int(entry.th32ProcessID))
                entry.dwSize = _ct.sizeof(entry_type)
                ok = k32.Process32NextW(handle, _ct.byref(entry))
        finally:
            k32.CloseHandle(handle)
        return found

    def step_env_ok(self, label):
        expected = self.dock_original_rect or self.baseline_rect
        if self.window_moved_externally(label, expected):
            self.check(label + ".environment_stable", False,
                       expected=expected,
                       actual=self.discovery.window_rect(int(self.wechat)))
            return False
        self.check(label + ".environment_stable", True)
        return True

    # -- scenarios --------------------------------------------------------
    def step_setup(self):
        version = self.read_version()
        repo = version.get("repo") or {}
        actual_commit = str(version.get("git", {}).get("commit", ""))
        ok_version = (version.get("app_version") == SOURCE_TREE_VERSION
                      and (actual_commit.startswith(SOURCE_TREE_COMMIT)
                           or repo.get("pinned_is_ancestor_of_head") == "yes"))
        self.check("setup.version", ok_version,
                   app_version=version.get("app_version"),
                   commit=actual_commit,
                   pinned_commit=SOURCE_TREE_COMMIT,
                   pinned_is_ancestor=repo.get("pinned_is_ancestor_of_head"),
                   dirty=version.get("git", {}).get("dirty"),
                   source_digest=version.get("source_digest"),
                   source_root=str(self.source_root),
                   repo_head=repo.get("head"))
        self.prepare_profile()
        self.launch_app()
        self.record("setup", profile=str(self.data_dir), app_pid=self.app_pid,
                    app_hwnd=self.app_hwnd)
        u32.SetWindowPos(self.app_hwnd, 0, 120, 20, 1200, 820,
                         SWP_NOZORDER | SWP_NOACTIVATE)
        time.sleep(0.8)
        connected = self.wait_connected()
        self.record("setup.connected", ok=connected["ok"],
                    statuses=self.ui.statuses(),
                    app_rect=self.ui.rect(self.ui.app),
                    wechat_rect=self.discovery.window_rect(int(self.wechat)),
                    wechat_visible=bool(u32.IsWindowVisible(int(self.wechat))))
        self.check("setup.connected", connected["ok"])
        current_rect = self.discovery.window_rect(int(self.wechat))
        self.dock_original_rect = current_rect
        moved_externally = not rect_close(current_rect, self.baseline_rect)
        self.record("setup.wechat_rect_stable",
                    preflight_rect=self.baseline_rect,
                    current_rect=current_rect,
                    changed_since_preflight=moved_externally)
        # If another process moved WeChat during startup this is interference, not
        # a product failure: record it and use the current rectangle as the
        # scenario's "original" geometry (still restored in finally).
        self.check("setup.wechat_untouched_in_mirror",
                   None if moved_externally else True,
                   preflight_rect=self.baseline_rect,
                   current_rect=current_rect)
        before = self.ui.edit_lengths()
        time.sleep(3.5)
        after = self.ui.edit_lengths()
        self.record("setup.reading", edit_lengths_before=before,
                    edit_lengths_after=after, statuses=self.ui.statuses())
        self.dock_original_rect = self.discovery.window_rect(int(self.wechat))
        self.record("setup.process_tree", app_pid=self.app_pid,
                    children=self.child_pids(self.app_pid),
                    app_alive=process_alive(self.app_pid))
        has_text = bool(after) and any(value for value in after if value)
        self.check("setup.reading_started", has_text if has_text else None,
                   edit_lengths=after, statuses=self.ui.statuses())


    def _saved_dock(self):
        try:
            saved = json.loads((self.data_dir / "settings.json").read_text(
                encoding="utf-8"))
            return saved.get("dock_wechat")
        except (OSError, ValueError):
            return None

    def _click_save(self, expected=None):
        """Persist the settings page; verify the isolated settings.json changed."""
        saves = self.ui.find_all(
            lambda n, t, i, d: t == "ButtonControl" and n == "保存设置")
        attempts = []
        if not saves:
            return {"ok": False, "reason": "save_missing", "attempts": attempts}
        for action in ("mouse", "invoke"):
            for index in (len(saves) - 1, 0):
                target = saves[index]
                if action == "mouse":
                    detail = self.ui.click(target)
                else:
                    detail = {"invoke": self.ui.invoke(target)}
                time.sleep(2.2)
                value = self._saved_dock()
                attempts.append({"action": action, "button_index": index,
                                 "detail": detail, "dock_wechat": value})
                if expected is None or value == expected:
                    return {"ok": True, "dock_wechat": value,
                            "attempts": attempts}
        return {"ok": False, "dock_wechat": self._saved_dock(),
                "attempts": attempts}

    def _back_to_chat(self):
        """Shrink first (settings view does not move WeChat), then return to chat."""
        self.ui.resize(1200, 820, left=120, top=20)
        time.sleep(0.8)
        return self.ui.click_named(control_type="CheckBoxControl", name_exact="聊天")

    def step_dock_on(self):
        label = "dock_on"
        if not self.step_env_ok(label):
            return
        self.ui.click_named(control_type="CheckBoxControl", name_exact="设置")
        time.sleep(1.2)
        self.ui.resize(1400, 900)
        toggle = self.ui.set_checkbox("停靠微信窗口", True)
        save = self._click_save(expected=True)
        self.record(label + ".settings", toggle=toggle, save=save,
                    saved_dock_wechat=save.get("dock_wechat"))
        self.check(label + ".checkbox_saved",
                   toggle["ok"] and save.get("dock_wechat") is True)
        self.dock_original_rect = self.discovery.window_rect(int(self.wechat))
        self.record(label + ".pre_attach_rect", rect=self.dock_original_rect)
        self._back_to_chat()
        attached = self.ui.wait_status("微信已停靠", timeout=25.0)
        if not attached["ok"]:
            self.record(label + ".attach_failed", statuses=self.ui.statuses(),
                        wechat_rect=self.discovery.window_rect(int(self.wechat)),
                        app_alive=process_alive(self.app_pid),
                        children=self.child_pids(self.app_pid),
                        app_log=self.app_log_tail())
        wechat_rect = self.discovery.window_rect(int(self.wechat))
        app_rect = self.ui.rect(self.ui.app)
        snapshot = self.snapshot("dock_on")
        time.sleep(0.9)
        wechat_rect_2 = self.discovery.window_rect(int(self.wechat))
        center = ((wechat_rect[0] + wechat_rect[2]) // 2,
                  (wechat_rect[1] + wechat_rect[3]) // 2)
        hit_root = root_window(window_from_point(*center))
        zorder = self.discovery.zorder_ok(int(self.wechat), self.app_hwnd)
        self.record(label, statuses=snapshot["statuses"], app_rect=app_rect,
                    wechat_rect=wechat_rect, wechat_rect_second=wechat_rect_2,
                    pre_attach_rect=self.dock_original_rect,
                    wechat_visible=snapshot["wechat_visible"],
                    wechat_status=snapshot["wechat_status"],
                    hit_test_root_is_wechat=hit_root == int(self.wechat),
                    zorder_below_assistant=zorder,
                    foreground_is_wechat=snapshot["foreground_is_wechat"],
                    edit_lengths=snapshot["edit_lengths"])
        self.check(label + ".attached_status", attached["ok"],
                   statuses=snapshot["statuses"])
        self.check(label + ".moved_into_assistant",
                   not rect_close(wechat_rect, self.dock_original_rect)
                   and rect_inside(wechat_rect, app_rect))
        self.check(label + ".rect_stable", rect_close(wechat_rect, wechat_rect_2))
        self.check(label + ".aperture_shows_wechat", hit_root == int(self.wechat))
        self.check(label + ".zorder_ok", zorder)
        self.check(label + ".never_foreground",
                   not snapshot["foreground_is_wechat"])

    def step_dock_off(self):
        label = "dock_off"
        if not self.step_env_ok(label):
            return
        self.ui.click_named(control_type="CheckBoxControl", name_exact="设置")
        time.sleep(1.2)
        self.record(label + ".settings_view",
                    statuses=self.ui.statuses(),
                    wechat_minimized=bool(u32.IsIconic(int(self.wechat))))
        self.ui.resize(1400, 900)
        toggle = self.ui.set_checkbox("停靠微信窗口", False)
        save = self._click_save(expected=False)
        self.record(label + ".settings", toggle=toggle, save=save,
                    saved_dock_wechat=save.get("dock_wechat"))
        self.check(label + ".checkbox_saved",
                   toggle["ok"] and save.get("dock_wechat") is False)
        self._back_to_chat()
        restored = self.ui.wait_status("正在本地识别", timeout=20.0)
        wechat_rect = self._restored_rect(int(self.wechat))
        snapshot = self.snapshot("dock_off")
        app_rect = self.ui.rect(self.ui.app)
        center = ((wechat_rect[0] + wechat_rect[2]) // 2,
                  (wechat_rect[1] + wechat_rect[3]) // 2)
        hit_root = root_window(window_from_point(*center))
        self.record(label, statuses=snapshot["statuses"], app_rect=app_rect,
                    wechat_rect=wechat_rect,
                    wechat_visible=snapshot["wechat_visible"],
                    wechat_minimized=snapshot["wechat_minimized"],
                    hit_test_root_is_wechat=hit_root == int(self.wechat),
                    hit_test_root_is_app=hit_root == self.app_hwnd,
                    edit_lengths=snapshot["edit_lengths"])
        self.check(label + ".geometry_restored",
                   rect_close(wechat_rect, self.baseline_rect))
        self.check(label + ".visible_and_unminimized",
                   snapshot["wechat_visible"]
                   and not snapshot["wechat_minimized"])
        self.check(label + ".identity_unchanged",
                   self.discovery.valid(self.wechat))
        self.check(label + ".mirror_hit_test", hit_root == self.app_hwnd)
        self.check(label + ".live_status", restored["ok"])

    def step_minimize(self):
        label = "minimize"
        if not self.step_env_ok(label):
            return
        hwnd = int(self.wechat)
        before = self.snapshot("minimize.before")
        u32.ShowWindow(hwnd, SW_MINIMIZE)
        time.sleep(1.0)
        process_status = self.ui.track_statuses(6.0)
        minimized = bool(u32.IsIconic(hwnd))
        auto_restored = False
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not u32.IsIconic(hwnd):
                auto_restored = True
                break
            time.sleep(0.3)
        during = self.snapshot("minimize.during")
        self.record(label + ".during", minimized=minimized,
                    auto_restored_by_app=auto_restored,
                    statuses=process_status,
                    edit_lengths=during["edit_lengths"],
                    app_alive=during["app_alive"],
                    foreground_is_wechat=during["foreground_is_wechat"])
        paused = any(("最小化" in text) or ("隐藏" in text)
                     or ("无法读取" in text) or ("只读" in text)
                     or ("托盘" in text) or ("暂停" in text)
                     for text in process_status)
        self.check(label + ".app_kept_window_minimized",
                   minimized and not auto_restored and bool(during["app_alive"]))
        self.check(label + ".status_degrades", paused, statuses=process_status)
        self.record(label + ".editor", before=before["edit_lengths"],
                    during=during["edit_lengths"])
        u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        time.sleep(1.2)
        recovered = self.ui.wait_status("正在本地识别", timeout=20.0)
        after = self.snapshot("minimize.after")
        self.record(label + ".after", statuses=after["statuses"],
                    wechat_visible=after["wechat_visible"],
                    wechat_minimized=after["wechat_minimized"],
                    wechat_rect=after["wechat_rect"],
                    edit_lengths=after["edit_lengths"])
        self.check(label + ".restored_visible",
                   after["wechat_visible"] and not after["wechat_minimized"]
                   and rect_close(after["wechat_rect"], self.baseline_rect))
        self.check(label + ".reading_resumed", recovered["ok"])

    def _wechat_center_point(self, fraction_x=0.6, fraction_y=0.45):
        rect = self.discovery.window_rect(int(self.wechat))
        return (rect[0] + int((rect[2] - rect[0]) * fraction_x),
                rect[1] + int((rect[3] - rect[1]) * fraction_y))

    def step_popup(self):
        label = "popup"
        if not self.step_env_ok(label):
            return
        hwnd = int(self.wechat)
        baseline_popups = self.discovery.popup_windows(hwnd)
        point = self._wechat_center_point()
        click = post_mouse(hwnd, point, right=True)
        time.sleep(1.2)
        popups = self.discovery.popup_windows(hwnd)
        covering = self.discovery.popup_covering(hwnd)
        popup_info = [{"hwnd": int(item["hwnd"]), "class": item["class"],
                       "visible": item["visible"],
                       "rect_size": rect_size(list(item["rect"]))}
                      for item in popups]
        statuses = self.ui.track_statuses(5.0)
        during = self.snapshot("popup.during")
        send_buttons = [b for b in during["buttons"] if b["name"] == "发送到微信"]
        self.record(label + ".opened", click=click,
                    baseline_popup_count=len(baseline_popups),
                    popup_count=len(popups), popups=popup_info,
                    covering_hwnd=int(covering) if covering else None,
                    statuses=statuses,
                    edit_lengths=during["edit_lengths"],
                    buttons=during["buttons"],
                    app_alive=during["app_alive"])
        detected = covering is not None
        unavailable = any(("弹窗" in text) or ("只读" in text)
                          for text in statuses)
        cleared = all(value in (0, None) for value in during["edit_lengths"]
                      if value is not None)
        no_send = not any(b["enabled"] for b in send_buttons)
        if detected:
            self.check(label + ".real_popup_detected", True)
            self.check(label + ".status_unavailable", unavailable,
                       statuses=statuses)
            self.check(label + ".no_misread_editor_empty", cleared,
                       edit_lengths=during["edit_lengths"])
            self.check(label + ".no_send_path", no_send,
                       send_buttons=send_buttons)
        else:
            # WeChat 4.x may render its menu inside the main window (no separate
            # same-process top-level window), so this scenario is then a manual
            # item instead of a product verdict.
            self.check(label + ".real_popup_detected", None,
                       reason="no same-process top-level popup detected",
                       statuses=statuses)
            self.check(label + ".status_unavailable", None)
            self.check(label + ".no_misread_editor_empty", None,
                       edit_lengths=during["edit_lengths"])
            self.check(label + ".no_send_path", None,
                       send_buttons=send_buttons)
        # close the popup safely: never click a menu item; ESC / WM_CANCELMODE / WM_CLOSE
        closed = False
        if detected:
            popup_hwnd = int(covering) if covering else hwnd
            for _ in range(4):
                post_key(popup_hwnd)
                post_key(hwnd)
                u32.PostMessageW(popup_hwnd, WM_CANCELMODE, 0, 0)
                time.sleep(0.5)
                if self.discovery.popup_covering(hwnd) is None:
                    closed = True
                    break
            if not closed:
                u32.PostMessageW(popup_hwnd, WM_CLOSE, 0, 0)
                time.sleep(1.0)
                closed = self.discovery.popup_covering(hwnd) is None
        recovered = (self.ui.wait_status("正在本地识别", timeout=20.0)
                     if closed else {"ok": False})
        after = self.snapshot("popup.after")
        self.record(label + ".closed", closed=closed, statuses=after["statuses"],
                    edit_lengths=after["edit_lengths"])
        self.check(label + ".popup_closed",
                   closed if detected else None)
        self.check(label + ".reading_resumed",
                   recovered["ok"] if detected else None)
        self.check(label + ".wechat_visible_after_popup",
                   after["wechat_visible"])

    def _wechat_process_count(self):
        return len([entry for entry in self.discovery.enumerate_processes()
                    if entry.get("path") == self._wx_path])

    def step_tray(self):
        label = "tray"
        if not self.step_env_ok(label):
            return
        hwnd = int(self.wechat)
        before = self.snapshot("tray.before")
        self.tray_original_rect = before["wechat_rect"] or self.baseline_rect
        running_before = self._wechat_process_count()
        self.record(label + ".before", statuses=before["statuses"],
                    wechat_rect=before["wechat_rect"],
                    wechat_processes=running_before)
        u32.ShowWindow(hwnd, 0)  # SW_HIDE == the Win32 state of "closed to tray"
        time.sleep(1.2)
        hidden_statuses = self.ui.track_statuses(8.0)
        during = self.snapshot("tray.during")
        running_during = self._wechat_process_count()
        self.record(label + ".hidden",
                    wechat_visible=during["wechat_visible"],
                    wechat_status=during["wechat_status"],
                    wechat_hwnd_still_exists=bool(u32.IsWindow(hwnd)),
                    process_count_same=bool(
                        running_before == running_during == 1),
                    statuses=hidden_statuses,
                    app_alive=during["app_alive"],
                    identity_valid=self.discovery.valid(self.wechat))
        tray_status = any(("托盘" in text) or ("隐藏" in text)
                          or ("无法读取" in text) or ("后台读取" in text)
                          or ("最小化" in text)
                          for text in hidden_statuses)
        self.check(label + ".hidden_state",
                   not during["wechat_visible"]
                   and during["wechat_status"] in ("hidden", "minimized"))
        self.check(label + ".app_not_killed", bool(during["app_alive"]))
        self.check(label + ".no_second_wechat",
                   running_before == running_during == 1)
        self.check(label + ".status_paused_or_degraded", tray_status,
                   statuses=hidden_statuses)
        # restore through the product's own "启动微信" button (explicit user path)
        nav = self.ui.click_named(control_type="CheckBoxControl", name_exact="设置")
        time.sleep(1.2)
        launch_button = self.ui.scroll_to_button("启动微信")
        restore = self.ui.click(launch_button) if launch_button else None
        time.sleep(1.5)
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline and not u32.IsWindowVisible(hwnd):
            time.sleep(0.4)
        restored_visible = bool(u32.IsWindowVisible(hwnd))
        after = self.snapshot("tray.after")
        notice = self.ui.statuses()
        self.record(label + ".restore", nav=nav, launch_click=restore,
                    visible=restored_visible,
                    wechat_status=after["wechat_status"],
                    wechat_rect=self._restored_rect(hwnd),
                    statuses=notice,
                    foreground_is_wechat=after["foreground_is_wechat"],
                    app_alive=after["app_alive"])
        self.check(label + ".restored_visible", restored_visible)
        self.check(label + ".geometry_restored_after_tray",
                   rect_close(self._restored_rect(hwnd), self.tray_original_rect),
                   expected_rect=self.tray_original_rect,
                   actual_rect=self._restored_rect(hwnd))
        self.check(label + ".identity_unchanged_after_tray",
                   self.discovery.valid(self.wechat))
        self.check(label + ".no_second_launch_after_restore",
                   self._wechat_process_count() == 1)
        self.check(label + ".reading_resumed",
                   self.ui.wait_status("正在本地识别", timeout=20.0)["ok"])

    # -- lifecycle --------------------------------------------------------
    def run(self):
        try:
            window, detail = self.find_wechat_window()
            status = (self.discovery.window_status(int(window["hwnd"]))
                      if window else None)
            self.record("preflight", running=detail.get("running"),
                        main_count=detail.get("main_count"), status=status,
                        visible=(status == "visible"))
            if window is None or status != "visible":
                self.check("preflight.visible", False, status=status,
                           detail=detail)
                return 2
            self.wechat = window["hwnd"]
            self.wechat_hwnd = int(self.wechat)
            self.baseline_rect = self._restored_rect(self.wechat_hwnd)
            self.record("baseline", hwnd=self.wechat_hwnd,
                        pid=self.wechat.pid, rect=self.baseline_rect,
                        rect_size=rect_size(self.baseline_rect),
                        status="visible")
            self.check("preflight.visible", True)
            stale = self.app_window_candidates()
            self.record("preflight.stale_app_instances",
                        count=len(stale), pids=[item["pid"] for item in stale])
            if stale:
                # A leftover 0.5.8 source-run window would make UIA evidence
                # ambiguous; refuse to start instead of guessing.
                self.check("preflight.no_stale_app_instance", False,
                           pids=[item["pid"] for item in stale])
                return 2
            self.check("preflight.no_stale_app_instance", True)
            if "setup" in self.steps:
                self.step_setup()
            if "dock" in self.steps:
                self.step_dock_on()
                self.step_dock_off()
            if "minimize" in self.steps:
                self.step_minimize()
            if "popup" in self.steps:
                self.step_popup()
            if "tray" in self.steps:
                self.step_tray()
        except Exception as exc:  # noqa: BLE001 - always reach cleanup
            import traceback
            self.check("run.exception", False, error=type(exc).__name__,
                       message=str(exc)[:300])
            self.record("traceback", text=traceback.format_exc()[-2000:])
        finally:
            cleanup = self.cleanup()
            self.record("cleanup", **cleanup)
            self.check("cleanup.wechat_visible", bool(cleanup.get("ok")),
                       **{key: value for key, value in cleanup.items()
                          if key not in ("ok",)})
            self.check("environment.stable", not self.environment_events,
                       events=self.environment_events)
            self.write_evidence()
        failed = [c for c in self.checks if c["ok"] is False]
        manual = [c["check"] for c in self.checks if c["ok"] is None]
        print(json.dumps({"checks": len(self.checks),
                          "passed": sum(1 for c in self.checks
                                        if c["ok"] is True),
                          "failed": len(failed),
                          "failed_ids": [c["check"] for c in failed],
                          "manual": manual}, ensure_ascii=False), flush=True)
        return 0 if not failed else 1

    def cleanup(self):
        detail = {"app_pid": self.app_pid, "launcher_pid": self.launcher_pid}
        try:
            if self.app_pid and process_alive(self.app_pid):
                u32.PostMessageW(self.app_hwnd, WM_CLOSE, 0, 0)
                deadline = time.monotonic() + 8.0
                while time.monotonic() < deadline and process_alive(self.app_pid):
                    time.sleep(0.3)
        except Exception as exc:  # noqa: BLE001
            detail["app_stop_error"] = type(exc).__name__
        for pid in (self.app_pid, self.launcher_pid):
            if pid and process_alive(pid):
                try:
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   capture_output=True, timeout=30)
                except Exception as exc:  # noqa: BLE001
                    detail.setdefault("kill_errors", []).append(type(exc).__name__)
        time.sleep(0.8)
        # Any 0.5.8 source-run window left over from this probe would make later
        # evidence ambiguous: remove only the ones we just created (recent).
        leftovers = []
        try:
            started = time.time()
            not_before = int((started + 11644473600) * 10_000_000) - 900_000_000
            for item in self.app_window_candidates():
                if item["created"] >= not_before:
                    leftovers.append(item["pid"])
            for pid in leftovers:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               capture_output=True, timeout=30)
        except Exception as exc:  # noqa: BLE001
            detail["leftover_scan_error"] = type(exc).__name__
        detail["leftover_killed"] = leftovers
        for handle in (self._stdout_file, self._stderr_file):
            try:
                if handle is not None:
                    handle.close()
            except OSError:
                pass
        detail["app_log_tail"] = self.app_log_tail(600)
        detail["children_after"] = self.child_pids(self.app_pid) if self.app_pid else []
        detail["app_alive_after"] = (process_alive(self.app_pid)
                                     if self.app_pid else None)
        detail["launcher_alive_after"] = (process_alive(self.launcher_pid)
                                          if self.launcher_pid else None)
        detail.update(self.force_visible())
        return detail

    def write_evidence(self):
        repo = (self.version_info or {}).get("repo") or repo_git_info()
        payload = {
            "probe": "c3_real_window_verify",
            "started_at": self.started_at,
            "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "target": {"app_version": SOURCE_TREE_VERSION,
                       "pinned_commit": SOURCE_TREE_COMMIT,
                       "source_root": str(self.source_root),
                       "repo": repo,
                       "version_json": self.version_info},
            "privacy": ("rects/booleans/counts/status labels only; "
                        "no chat content, no screenshots, no WeChat messages sent"),
            "preflight_visible": self.baseline_rect is not None,
            "baseline_rect": self.baseline_rect,
            "checks": self.checks,
            "records": self.records,
        }
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        self.evidence_path = self.evidence_dir / ("c3-evidence-" + stamp + ".json")
        self.evidence_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"evidence": str(self.evidence_path)}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description="C3 real-window acceptance probe")
    parser.add_argument("--steps", default="setup,dock,minimize,popup,tray",
                        help="comma separated: setup,dock,minimize,popup,tray")
    parser.add_argument("--evidence-dir", default=None)
    parser.add_argument("--source-root", default=None,
                        help="0.5.8 source tree to launch (default: this checkout)")
    args = parser.parse_args(argv)
    steps = [item.strip() for item in args.steps.split(",") if item.strip()]
    if args.source_root:
        # Import the window primitives from the acceptance snapshot itself, not from
        # a working tree that other agents may be editing concurrently.
        snapshot_root = str(Path(args.source_root).resolve())
        if snapshot_root in sys.path:
            sys.path.remove(snapshot_root)
        sys.path.insert(0, snapshot_root)
    evidence_dir = (Path(args.evidence_dir) if args.evidence_dir
                    else LITE_ROOT / "test-output" / "c3-runs" /
                    datetime.now().strftime("run-%Y%m%d-%H%M%S"))
    probe = C3Probe(evidence_dir, steps, source_root=args.source_root)
    return probe.run()


if __name__ == "__main__":
    raise SystemExit(main())
