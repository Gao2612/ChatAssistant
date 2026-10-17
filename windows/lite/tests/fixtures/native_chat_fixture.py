"""External synthetic chat window for T4 native-window tests.

The fixture runs in its **own Python process** and talks to the test through a
tiny line protocol on stdin/stdout.  It uses only the standard library plus
Win32 via ``ctypes``: it never imports ``liteapp``, never touches the real
WeChat client, never reads chat content and never sends messages.

Why a separate process: ``WindowManager`` / ``WindowDock`` act on a real HWND
and verify the *process instance* behind it (executable path, PID and process
creation time).  A window created inside the test process could not prove that
an HWND reused by another process is rejected, and it could not prove crash
recovery after the owning process exits.

Commands (one per line, UTF-8)::

    set_rect X Y W H      move/resize the main synthetic chat window
    move X Y              move only
    resize W H            resize only
    text TEXT             change the synthetic message text
    popup                 create one owned popup overlapping the chat window
    close_popup
    minimize
    restore
    state                 emit one state event
    close / quit          destroy windows and exit

Responses are one JSON object per line; every ready/ack/error carries enough
identity (pid, process creation ticks, hwnd) for the test to build a
``SourceKey``.  The fixture exits when its stdin reaches EOF so a crashed test
never leaves stray windows behind.

The second half of the module is a small parent-side controller used by the
test suite (``NativeChatFixture``); running the file as a script starts the
child window process.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
from ctypes import wintypes as W

FIXTURE_PATH = os.path.abspath(__file__)

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, W.HWND, W.UINT, W.WPARAM, W.LPARAM)
WNDCLASSEXW = None

# Window messages / styles used below.
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_PAINT = 0x000F
WM_ERASEBKGND = 0x0014
CS_HREDRAW = 0x0002
CS_VREDRAW = 0x0001
WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_POPUP = 0x80000000
WS_VISIBLE = 0x10000000
WS_EX_TOOLWINDOW = 0x00000080
GWL_EXSTYLE = -20
SW_MINIMIZE = 6
SW_RESTORE = 9
SW_SHOWNOACTIVATE = 4
DT_LEFT = 0x0000
DT_TOP = 0x0000
DT_WORDBREAK = 0x0010
TRANSPARENT = 1
PM_REMOVE = 0x0001
GW_OWNER = 4

MODE_CLASS = {
    "chat": "CASyntheticChat",
    "login": "CASyntheticLogin",
    "marker": "CASyntheticMarker",
}
MODE_TITLE = {
    "chat": "合成聊天窗口",
    "login": "微信",
    "marker": "合成遮挡窗口",
}


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", W.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", W.HINSTANCE),
        ("hIcon", W.HICON),
        ("hCursor", W.HANDLE),
        ("hbrBackground", W.HBRUSH),
        ("lpszMenuName", W.LPCWSTR),
        ("lpszClassName", W.LPCWSTR),
    ]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", W.HDC),
        ("fErase", W.BOOL),
        ("rcPaint", W.RECT),
        ("fRestore", W.BOOL),
        ("fIncUpdate", W.BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


if sys.platform == "win32":
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = W.ATOM
    user32.CreateWindowExW.argtypes = [
        W.DWORD, W.LPCWSTR, W.LPCWSTR, W.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        W.HWND, W.HMENU, W.HINSTANCE, W.LPVOID,
    ]
    user32.CreateWindowExW.restype = W.HWND
    user32.DefWindowProcW.argtypes = [W.HWND, W.UINT, W.WPARAM, W.LPARAM]
    user32.DefWindowProcW.restype = LRESULT
    user32.DestroyWindow.argtypes = [W.HWND]
    user32.DestroyWindow.restype = W.BOOL
    user32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
    user32.ShowWindow.restype = W.BOOL
    user32.UpdateWindow.argtypes = [W.HWND]
    user32.UpdateWindow.restype = W.BOOL
    user32.InvalidateRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT), W.BOOL]
    user32.InvalidateRect.restype = W.BOOL
    user32.GetClientRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    user32.GetClientRect.restype = W.BOOL
    user32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    user32.GetWindowRect.restype = W.BOOL
    user32.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, W.UINT]
    user32.SetWindowPos.restype = W.BOOL
    user32.IsIconic.argtypes = [W.HWND]
    user32.IsIconic.restype = W.BOOL
    user32.IsWindowVisible.argtypes = [W.HWND]
    user32.IsWindowVisible.restype = W.BOOL
    user32.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user32.GetWindowThreadProcessId.restype = W.DWORD
    user32.GetWindow.argtypes = [W.HWND, W.UINT]
    user32.GetWindow.restype = W.HWND
    user32.BeginPaint.argtypes = [W.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.BeginPaint.restype = W.HDC
    user32.EndPaint.argtypes = [W.HWND, ctypes.POINTER(PAINTSTRUCT)]
    user32.EndPaint.restype = W.BOOL
    user32.FillRect.argtypes = [W.HDC, ctypes.POINTER(W.RECT), W.HBRUSH]
    user32.FillRect.restype = ctypes.c_int
    user32.DrawTextW.argtypes = [W.HDC, W.LPCWSTR, ctypes.c_int, ctypes.POINTER(W.RECT), W.UINT]
    user32.DrawTextW.restype = ctypes.c_int
    user32.PeekMessageW.argtypes = [ctypes.POINTER(W.MSG), W.HWND, W.UINT, W.UINT, W.UINT]
    user32.PeekMessageW.restype = W.BOOL
    user32.TranslateMessage.argtypes = [ctypes.POINTER(W.MSG)]
    user32.DispatchMessageW.argtypes = [ctypes.POINTER(W.MSG)]
    user32.DispatchMessageW.restype = LRESULT
    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    gdi32.CreateSolidBrush.argtypes = [W.DWORD]
    gdi32.CreateSolidBrush.restype = W.HBRUSH
    gdi32.DeleteObject.argtypes = [W.HANDLE]
    gdi32.DeleteObject.restype = W.BOOL
    gdi32.SetBkMode.argtypes = [W.HDC, ctypes.c_int]
    gdi32.SetBkMode.restype = ctypes.c_int
    gdi32.SetTextColor.argtypes = [W.HDC, W.DWORD]
    gdi32.SetTextColor.restype = W.DWORD
    user32.LoadCursorW.argtypes = [W.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = W.HANDLE
    user32.SetProcessDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    user32.SetProcessDpiAwarenessContext.restype = W.BOOL
    kernel32.GetModuleHandleW.argtypes = [W.LPCWSTR]
    kernel32.GetModuleHandleW.restype = W.HMODULE
    kernel32.GetCurrentProcess.restype = W.HANDLE
    kernel32.GetProcessTimes.argtypes = [W.HANDLE, ctypes.POINTER(W.FILETIME),
                                         ctypes.POINTER(W.FILETIME),
                                         ctypes.POINTER(W.FILETIME),
                                         ctypes.POINTER(W.FILETIME)]
    kernel32.GetProcessTimes.restype = W.BOOL
    kernel32.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel32.OpenProcess.restype = W.HANDLE
    kernel32.CloseHandle.argtypes = [W.HANDLE]


def colorref(hex_color: str) -> int:
    """``#RRGGBB`` -> Win32 COLORREF (0x00BBGGRR)."""
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return r | (g << 8) | (b << 16)


def process_created() -> int:
    """Process creation FILETIME ticks of the current process."""
    if sys.platform != "win32":
        return 0
    created, exited = W.FILETIME(), W.FILETIME()
    kernel, user = W.FILETIME(), W.FILETIME()
    handle = kernel32.GetCurrentProcess()
    if kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                ctypes.byref(kernel), ctypes.byref(user)):
        return (created.dwHighDateTime << 32) | created.dwLowDateTime
    return 0


def window_rect(hwnd):
    rect = W.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return None
    return (rect.left, rect.top, rect.right, rect.bottom)


class _ChatWindow:
    """Owns one top-level window and its optional popup; pure Win32."""

    def __init__(self, *, mode="chat", title=None, color="#E9F1F7", text="",
                 rect=(120, 120, 820, 680)):
        self.mode = mode
        self.title = title or MODE_TITLE.get(mode, "合成聊天窗口")
        self.class_name = MODE_CLASS.get(mode, "CASyntheticChat")
        self.back_color = color
        self.text = text or "对方：你好\n我：在的"
        self.hwnd = None
        self.popup_hwnd = None
        self._wndproc_ref = None
        self._rect = rect
        self._marker_phase = 0
        self._replacing = False

    # -- creation ---------------------------------------------------------
    def create(self):
        instance = kernel32.GetModuleHandleW(None)
        self._wndproc_ref = WNDPROC(self._on_message)
        wc = WNDCLASSW()
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = self._wndproc_ref
        wc.hInstance = instance
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))  # IDC_ARROW
        wc.lpszClassName = self.class_name
        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom and ctypes.get_last_error() != 1410:  # 1410 = class already exists
            raise OSError(f"RegisterClassW failed: {ctypes.get_last_error()}")
        left, top, width, height = self._rect
        style = WS_POPUP | WS_VISIBLE if self.mode == "marker" else WS_OVERLAPPEDWINDOW | WS_VISIBLE
        self.hwnd = user32.CreateWindowExW(
            0, self.class_name, self.title, style,
            left, top, width, height, None, None, instance, None)
        if not self.hwnd:
            raise OSError(f"CreateWindowExW failed: {ctypes.get_last_error()}")
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        user32.UpdateWindow(self.hwnd)
        return self.hwnd

    def _on_message(self, hwnd, message, wparam, lparam):
        if message == WM_PAINT:
            self._paint(hwnd)
            return 0
        if message == WM_ERASEBKGND:
            return 1
        if message == WM_CLOSE:
            user32.DestroyWindow(hwnd)
            return 0
        if message == WM_DESTROY:
            if not self._replacing:
                user32.PostQuitMessage(0)
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _paint(self, hwnd):
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            client = W.RECT()
            user32.GetClientRect(hwnd, ctypes.byref(client))
            brush = gdi32.CreateSolidBrush(colorref(self.back_color))
            try:
                user32.FillRect(hdc, ctypes.byref(client), brush)
            finally:
                gdi32.DeleteObject(brush)
            gdi32.SetBkMode(hdc, TRANSPARENT)
            gdi32.SetTextColor(hdc, colorref("#1B2733"))
            # A phase-dependent block makes text changes visible as large,
            # deterministic pixel differences for the capture-change tests.
            block = W.RECT(20, 20, 20 + 60 + (self._marker_phase % 6) * 70, 90)
            block_brush = gdi32.CreateSolidBrush(colorref("#2F7DD1"))
            try:
                user32.FillRect(hdc, ctypes.byref(block), block_brush)
            finally:
                gdi32.DeleteObject(block_brush)
            area = W.RECT(20, 110, client.right - 24, client.bottom - 24)
            user32.DrawTextW(hdc, self.text, -1, ctypes.byref(area),
                             DT_LEFT | DT_TOP | DT_WORDBREAK)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    # -- commands ---------------------------------------------------------
    def set_rect(self, left, top, width, height):
        user32.SetWindowPos(self.hwnd, None, int(left), int(top), int(width), int(height),
                            0x0004 | 0x0010)  # SWP_NOZORDER | SWP_NOACTIVATE
        user32.UpdateWindow(self.hwnd)

    def move(self, left, top):
        user32.SetWindowPos(self.hwnd, None, int(left), int(top), 0, 0,
                            0x0001 | 0x0004 | 0x0010)  # NOSIZE | NOZORDER | NOACTIVATE

    def resize(self, width, height):
        user32.SetWindowPos(self.hwnd, None, 0, 0, int(width), int(height),
                            0x0002 | 0x0004 | 0x0010)  # NOMOVE | NOZORDER | NOACTIVATE

    def set_text(self, text):
        self.text = text
        self._marker_phase += 1
        user32.InvalidateRect(self.hwnd, None, True)
        user32.UpdateWindow(self.hwnd)

    def open_popup(self):
        if self.popup_hwnd:
            return self.popup_hwnd
        left, top, right, bottom = window_rect(self.hwnd)
        width, height = 260, 180
        x = left + max(0, ((right - left) - width) // 2)
        y = top + max(0, ((bottom - top) - height) // 2)
        instance = kernel32.GetModuleHandleW(None)
        popup_class = self.class_name + "Popup"
        self._popup_wndproc_ref = WNDPROC(self._on_popup_message)
        wc = WNDCLASSW()
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = self._popup_wndproc_ref
        wc.hInstance = instance
        wc.hCursor = user32.LoadCursorW(None, ctypes.c_void_p(32512))
        wc.lpszClassName = popup_class
        user32.RegisterClassW(ctypes.byref(wc))
        self.popup_hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW, popup_class, "合成弹窗", WS_POPUP | WS_VISIBLE,
            x, y, width, height, self.hwnd, None, instance, None)
        if self.popup_hwnd:
            user32.ShowWindow(self.popup_hwnd, SW_SHOWNOACTIVATE)
            user32.UpdateWindow(self.popup_hwnd)
        return self.popup_hwnd

    def _on_popup_message(self, hwnd, message, wparam, lparam):
        if message == WM_PAINT:
            ps = PAINTSTRUCT()
            hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
            try:
                client = W.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(client))
                brush = gdi32.CreateSolidBrush(colorref("#F2C14E"))
                user32.FillRect(hdc, ctypes.byref(client), brush)
                gdi32.DeleteObject(brush)
            finally:
                user32.EndPaint(hwnd, ctypes.byref(ps))
            return 0
        if message == WM_DESTROY:
            return 0
        return user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def close_popup(self):
        hwnd, self.popup_hwnd = self.popup_hwnd, None
        if hwnd:
            user32.DestroyWindow(hwnd)
        return hwnd

    def minimize(self):
        user32.ShowWindow(self.hwnd, SW_MINIMIZE)
        return bool(user32.IsIconic(self.hwnd))

    def restore(self):
        user32.ShowWindow(self.hwnd, SW_RESTORE)
        return not user32.IsIconic(self.hwnd)

    def destroy(self):
        self.close_popup()
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = None

    def replace_main(self):
        """Destroy the current window and create the chat main window in this process.

        Simulates the verified login-window -> chat-main-window handover: the new
        HWND belongs to the *same* process instance (same PID and creation time).
        """
        old_rect = window_rect(self.hwnd) if self.hwnd else None
        left, top, right, bottom = old_rect or (120, 120, 820, 680)
        self._replacing = True
        try:
            self.close_popup()
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)
                self.hwnd = None
        finally:
            self._replacing = False
        self.class_name = MODE_CLASS["chat"]
        self.title = MODE_TITLE["chat"]
        self._wndproc_ref = WNDPROC(self._on_message)
        self._rect = (left, top, right - left, bottom - top)
        self.create()
        return int(self.hwnd)

    def state(self):
        return {
            "event": "state",
            "mode": self.mode,
            "hwnd": int(self.hwnd) if self.hwnd else 0,
            "popup": int(self.popup_hwnd) if self.popup_hwnd else 0,
            "rect": window_rect(self.hwnd) if self.hwnd else None,
            "minimized": bool(user32.IsIconic(self.hwnd)) if self.hwnd else False,
            "visible": bool(user32.IsWindowVisible(self.hwnd)) if self.hwnd else False,
            "text": self.text,
            "pid": os.getpid(),
            "created": process_created(),
            "class": self.class_name,
            "title": self.title,
        }


def _emit(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def run_child(argv=None) -> int:
    parser = argparse.ArgumentParser(description="synthetic Win32 chat window fixture")
    parser.add_argument("--mode", choices=sorted(MODE_CLASS), default="chat")
    parser.add_argument("--title", default=None)
    parser.add_argument("--color", default="#E9F1F7")
    parser.add_argument("--text", default="")
    parser.add_argument("--x", type=int, default=120)
    parser.add_argument("--y", type=int, default=120)
    parser.add_argument("--w", type=int, default=700)
    parser.add_argument("--h", type=int, default=560)
    parser.add_argument("--exit-after", type=float, default=180.0,
                        help="safety auto-exit in seconds (0 = never)")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        _emit({"event": "error", "message": "Windows only"})
        return 2
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    # Match the DPI awareness of the real application (Per-Monitor V2): the
    # geometry the test reads is then physical pixels, not virtualized ones.
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (OSError, AttributeError):
        pass

    window = _ChatWindow(mode=args.mode, title=args.title, color=args.color,
                         text=args.text, rect=(args.x, args.y, args.w, args.h))
    try:
        window.create()
    except OSError as exc:
        _emit({"event": "error", "message": str(exc)})
        return 3
    _emit({
        "event": "ready",
        "hwnd": int(window.hwnd),
        "pid": os.getpid(),
        "created": process_created(),
        "class": window.class_name,
        "title": window.title,
        "rect": window_rect(window.hwnd),
    })

    commands: "queue.Queue[str]" = queue.Queue()
    eof = threading.Event()

    def read_stdin():
        try:
            for line in sys.stdin:
                commands.put(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            eof.set()

    threading.Thread(target=read_stdin, daemon=True).start()

    pending_quit = False
    started = time.monotonic()
    while not pending_quit:
        msg = W.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == 0x0012:  # WM_QUIT
                pending_quit = True
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if pending_quit:
            break
        if args.exit_after and time.monotonic() - started > args.exit_after:
            break
        if eof.is_set():
            break
        try:
            line = commands.get_nowait()
        except queue.Empty:
            line = None
        if line is not None:
            if not _handle_command(window, line):
                pending_quit = True
        else:
            time.sleep(0.005)
    window.destroy()
    _emit({"event": "exit", "pid": os.getpid()})
    return 0


def _handle_command(window: _ChatWindow, line: str) -> bool:
    """Execute one command line; returns False when the process should exit."""
    parts = line.split(" ", 1)
    command = parts[0].strip().lower()
    argument = parts[1] if len(parts) > 1 else ""
    try:
        if command == "set_rect":
            values = [int(value) for value in argument.split()]
            window.set_rect(*values[:4])
        elif command == "move":
            values = [int(value) for value in argument.split()]
            window.move(*values[:2])
        elif command == "resize":
            values = [int(value) for value in argument.split()]
            window.resize(*values[:2])
        elif command == "text":
            window.set_text(argument)
        elif command == "popup":
            window.open_popup()
        elif command == "close_popup":
            window.close_popup()
        elif command == "minimize":
            window.minimize()
        elif command == "restore":
            window.restore()
        elif command == "replace_main":
            window.replace_main()
        elif command == "state":
            _emit(window.state())
            return True
        elif command in ("close", "quit"):
            return False
        elif command in ("", "ping"):
            pass
        else:
            _emit({"event": "error", "command": command, "message": "unknown command"})
            return True
    except (OSError, ValueError, TypeError) as exc:
        _emit({"event": "error", "command": command, "message": str(exc)})
        return True
    ack = {"event": "ack", "command": command}
    if command == "popup":
        ack["popup"] = int(window.popup_hwnd) if window.popup_hwnd else 0
    if command == "close_popup":
        ack["popup"] = 0
    if command == "text":
        ack["text"] = window.text
    if command == "replace_main":
        ack["hwnd"] = int(window.hwnd) if window.hwnd else 0
        ack["rect"] = window_rect(window.hwnd) if window.hwnd else None
        ack["class"] = window.class_name
        ack["title"] = window.title
    _emit(ack)
    return True


# ---------------------------------------------------------------------------
# Parent-side controller used by the test suite.
# ---------------------------------------------------------------------------

_CREATE_NO_WINDOW = 0x08000000


class NativeChatFixture:
    """Spawn/control one synthetic window process from a test."""

    def __init__(self, *, mode="chat", title=None, color=None, text="",
                 rect=(160, 160, 860, 720), extra_args=()):
        self.mode = mode
        self.title = title
        self.color = color
        self.text = text
        self.start_rect = rect
        self.extra_args = list(extra_args)
        self.process = None
        self.ready = None
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._reader = None

    def start(self, timeout=15.0):
        command = [sys.executable, FIXTURE_PATH, "--mode", self.mode,
                   "--x", str(self.start_rect[0]), "--y", str(self.start_rect[1]),
                   "--w", str(self.start_rect[2]), "--h", str(self.start_rect[3])]
        if self.title is not None:
            command += ["--title", self.title]
        if self.color is not None:
            command += ["--color", self.color]
        if self.text:
            command += ["--text", self.text]
        command += self.extra_args
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self._reader = threading.Thread(target=self._read_events, daemon=True)
        self._reader.start()
        self.ready = self.wait_for("ready", timeout=timeout)
        return self.ready

    def _read_events(self):
        stream = self.process.stdout
        try:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    self._events.put(json.loads(line))
                except json.JSONDecodeError:
                    self._events.put({"event": "garbage", "line": line})
        except (OSError, ValueError):
            pass
        finally:
            self._events.put({"event": "eof"})

    def wait_for(self, event, timeout=10.0, **expected):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"fixture event {event!r} not received in {timeout}s")
            try:
                packet = self._events.get(timeout=remaining)
            except queue.Empty:
                raise TimeoutError(f"fixture event {event!r} not received in {timeout}s") from None
            if packet.get("event") != event:
                if packet.get("event") in ("eof", "error"):
                    raise RuntimeError(f"fixture failed: {packet}")
                continue
            if all(packet.get(key) == value for key, value in expected.items()):
                return packet
            # Different instance of the same event kind: keep waiting.

    @property
    def hwnd(self):
        return int(self.ready["hwnd"]) if self.ready else 0

    @property
    def pid(self):
        return int(self.ready["pid"]) if self.ready else 0

    @property
    def created(self):
        return int(self.ready["created"]) if self.ready else 0

    @property
    def class_name(self):
        return self.ready["class"] if self.ready else ""

    def send(self, command):
        if self.process is None or self.process.stdin is None:
            raise RuntimeError("fixture is not running")
        self.process.stdin.write(command + "\n")
        self.process.stdin.flush()

    def command(self, command, expect="ack", timeout=10.0):
        self.send(command)
        return self.wait_for(expect, timeout=timeout)

    def state(self, timeout=10.0):
        return self.command("state", expect="state", timeout=timeout)

    def rect(self):
        return tuple(self.state()["rect"])

    def close(self, timeout=8.0):
        process, self.process = self.process, None
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                process.stdin.write("close\n")
                process.stdin.flush()
                process.wait(timeout=timeout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)
            for stream in (process.stdin, process.stdout, process.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        self.ready = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, _exc_type, _exc, _tb):
        self.close()
        return False


if __name__ == "__main__":
    sys.exit(run_child())
