"""WeChat conversation selection and message sending for an explicitly chosen chat.

S-AUTO-SEND (task-19). The user explicitly authorized one acceptance run on a
chosen conversation; this module provides the reusable, testable primitives:

* :func:`select_conversation` -- focus the search box, paste the contact name,
  click the first result, then **verify that the visible conversation title
  equals the target**; on mismatch it stops and returns a structured failure
  (no send is possible afterwards);
* :func:`send_message` -- click the input box, ``Ctrl+A`` (optional), paste the
  text through the clipboard (input-method safe), then Enter or the send button;
  ``dry_run=True`` fills the input but never sends;
* :func:`make_ocr_verifier` -- optional in-memory OCR title verifier (no
  screenshot is written to disk);
* :func:`estimate_layout_rects` -- logical client-coordinate rectangles derived
  from window ratios, so the caller can calibrate rects for a specific WeChat
  build/size/DPI.

Safety rules (hard):

* no SetParent, no hooks, no DLL injection, no reading the WeChat database;
* no real chat content or screenshot is ever written to disk or logged; OCR
  runs on an in-memory frame only;
* every operation is bounded by a timeout and returns :class:`ActionResult`
  instead of leaking Win32 exceptions to the UI;
* coordinates are **logical client-area pixels at a 96-DPI baseline** and are
  converted with the window DPI before clicking;
* window input uses ``SendInput`` after an explicit best-effort activation of
  the already discovered window (no synthetic ``PostMessage`` clicks, which Qt
  apps frequently ignore);
* failures never trigger retry loops or extra sends.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes as W
from dataclasses import dataclass
from typing import Callable

from . import discovery
from .discovery import WindowTarget

__all__ = [
    "ActionResult",
    "REASONS",
    "WeChatActions",
    "Win32Backend",
    "client_rect",
    "dpi_scale",
    "estimate_layout_rects",
    "logical_point_to_physical",
    "logical_rect_to_physical",
    "make_ocr_verifier",
    "physical_point_to_logical",
    "plan_for_window",
    "rect_center",
    "select_conversation",
    "send_message",
]

u32 = ctypes.WinDLL("user32", use_last_error=True)
k32 = ctypes.WinDLL("kernel32", use_last_error=True)

VK_CONTROL = 0x11
VK_A = 0x41
VK_V = 0x56
VK_RETURN = 0x0D
KEYEVENTF_KEYUP = 0x0002
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002
SW_SHOWNOACTIVATE = 4
SW_RESTORE = 9

# A single automated message is intentionally small; this also prevents an
# accidental huge paste. Real chat content is never stored by this module.
MAX_TEXT_CHARS = 2000

u32.IsWindow.argtypes = [W.HWND]
u32.IsWindow.restype = W.BOOL
u32.IsIconic.argtypes = [W.HWND]
u32.IsIconic.restype = W.BOOL
u32.IsWindowVisible.argtypes = [W.HWND]
u32.IsWindowVisible.restype = W.BOOL
u32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
u32.ShowWindow.restype = W.BOOL
u32.SetForegroundWindow.argtypes = [W.HWND]
u32.SetForegroundWindow.restype = W.BOOL
u32.GetForegroundWindow.restype = W.HWND
u32.GetDpiForWindow.argtypes = [W.HWND]
u32.GetDpiForWindow.restype = W.UINT
u32.GetClientRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u32.GetClientRect.restype = W.BOOL
u32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u32.GetWindowRect.restype = W.BOOL
u32.ClientToScreen.argtypes = [W.HWND, ctypes.POINTER(W.POINT)]
u32.ClientToScreen.restype = W.BOOL
u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
u32.SetCursorPos.restype = W.BOOL
u32.mouse_event.argtypes = [W.DWORD, W.DWORD, W.DWORD, W.DWORD, ctypes.c_void_p]
u32.keybd_event.argtypes = [W.BYTE, W.BYTE, W.DWORD, ctypes.c_void_p]
u32.OpenClipboard.argtypes = [W.HWND]
u32.OpenClipboard.restype = W.BOOL
u32.EmptyClipboard.restype = W.BOOL
u32.SetClipboardData.argtypes = [W.UINT, W.HANDLE]
u32.SetClipboardData.restype = W.HANDLE
u32.CloseClipboard.restype = W.BOOL
k32.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
k32.GlobalAlloc.restype = W.HANDLE
k32.GlobalLock.argtypes = [W.HANDLE]
k32.GlobalLock.restype = ctypes.c_void_p
k32.GlobalUnlock.argtypes = [W.HANDLE]
k32.GlobalFree.argtypes = [W.HANDLE]

# Structured failure/success reasons (never include chat content).
REASONS = {
    "ok": "ok",
    "invalid_window": "invalid_window",
    "stale_window": "stale_window",
    "invalid_title": "invalid_title",
    "invalid_rect": "invalid_rect",
    "verification_required": "verification_required",
    "activate_failed": "activate_failed",
    "search_failed": "search_failed",
    "clipboard_failed": "clipboard_failed",
    "input_failed": "input_failed",
    "title_unverified": "title_unverified",
    "title_mismatch": "title_mismatch",
    "timeout": "timeout",
    "backend_error": "backend_error",
    "empty_text": "empty_text",
    "text_too_long": "text_too_long",
    "send_failed": "send_failed",
    "send_verify_failed": "send_verify_failed",
}

# Ratio-based layout for the current WeChat main window (logical client rects).
# These are *starting estimates*; the caller should calibrate them once against
# the actual WeChat version/DPI/window size (see report §坐标假设).
DEFAULT_LAYOUT = {
    "search_rect": (0.02, 0.04, 0.28, 0.10),
    "result_rect": (0.02, 0.12, 0.28, 0.18),
    "title_rect": (0.34, 0.02, 0.75, 0.08),
    "input_rect": (0.34, 0.76, 0.92, 0.88),
    "send_rect": (0.92, 0.88, 0.99, 0.96),
}


# ---------------------------------------------------------------------------
# Result type and coordinate helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActionResult:
    """Structured result; ``detail`` only contains reason/step names, no chat text."""

    ok: bool
    reason: str
    detail: str = ""
    selected: bool = False
    sent: bool = False
    verified: bool = False
    dry_run: bool = False
    steps: tuple[str, ...] = ()
    elapsed: float = 0.0

    def __post_init__(self):
        if not isinstance(self.ok, bool) or not self.reason:
            raise ValueError("ActionResult needs an ok flag and a reason")
        object.__setattr__(self, "steps", tuple(str(step) for step in self.steps))


def dpi_scale(dpi_value):
    """Scale factor from the 96-DPI logical baseline."""
    if isinstance(dpi_value, bool) or not isinstance(dpi_value, (int, float)):
        raise TypeError("dpi must be int or float")
    if dpi_value <= 0:
        raise ValueError("dpi must be > 0")
    return float(dpi_value) / 96.0


def logical_point_to_physical(point, dpi_value):
    scale = dpi_scale(dpi_value)
    return (int(round(point[0] * scale)), int(round(point[1] * scale)))


def physical_point_to_logical(point, dpi_value):
    scale = dpi_scale(dpi_value)
    return (int(round(point[0] / scale)), int(round(point[1] / scale)))


def logical_rect_to_physical(rect, dpi_value):
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        raise TypeError("rect must be a 4-tuple")
    scale = dpi_scale(dpi_value)
    return tuple(int(round(value * scale)) for value in rect)


def rect_center(rect):
    """Integer center of a ``(left, top, right, bottom)`` rectangle."""
    left, top, right, bottom = (int(value) for value in rect)
    return ((left + right) // 2, (top + bottom) // 2)


def estimate_layout_rects(width, height):
    """Starting logical client rects from ratios (calibrate for the real build)."""
    width, height = int(width), int(height)
    if width <= 0 or height <= 0:
        raise ValueError("client size must be positive")
    result = {}
    for name, ratios in DEFAULT_LAYOUT.items():
        left, top, right, bottom = ratios
        x0, y0 = int(round(left * width)), int(round(top * height))
        x1, y1 = int(round(right * width)), int(round(bottom * height))
        x1 = max(x0 + 2, min(width, x1))
        y1 = max(y0 + 2, min(height, y1))
        result[name] = (max(0, x0), max(0, y0), x1, y1)
    return result


def client_rect(hwnd):
    """Physical client rectangle ``(0, 0, width, height)`` of the window."""
    rect = W.RECT()
    if not u32.GetClientRect(int(hwnd), ctypes.byref(rect)):
        return None
    return (0, 0, rect.right, rect.bottom)


def plan_for_window(hwnd, *, dpi=None):
    """Convenience for the Lead real-window script: DPI + logical client rects."""
    hwnd = int(hwnd)
    dpi_value = int(dpi or u32.GetDpiForWindow(hwnd) or 96)
    rect = client_rect(hwnd)
    if rect is None:
        return None
    scale = dpi_scale(dpi_value)
    logical = (int(round(rect[2] / scale)), int(round(rect[3] / scale)))
    layout = estimate_layout_rects(*logical)
    layout["dpi"] = dpi_value
    layout["client_logical"] = logical
    return layout


def make_ocr_verifier(hwnd, title_rect, *, dpi=None):
    """Build a verifier reading the visible conversation title via in-memory OCR.

    ``title_rect`` is in logical client coordinates. The frame is captured
    in memory (Windows Graphics Capture) and OCR'd locally; nothing is written to
    disk. Returns a callable ``verify() -> str | None`` suitable for
    :func:`select_conversation`. Calibrate ``title_rect`` once per WeChat
    build/DPI; otherwise pass an explicit verifier from the existing OCR path.
    """
    measure_dpi = int(dpi or u32.GetDpiForWindow(int(hwnd)) or 96)

    def verify():
        return _read_title_via_ocr(hwnd, title_rect, dpi=measure_dpi)

    return verify


# ---------------------------------------------------------------------------
# Win32 backend (all input goes through explicit SendInput after activation)
# ---------------------------------------------------------------------------

class Win32Backend:
    """Thin, replaceable Win32 adapter used by :class:`WeChatActions`."""

    def is_window(self, hwnd):
        return bool(u32.IsWindow(int(hwnd)))

    def dpi(self, hwnd):
        return int(u32.GetDpiForWindow(int(hwnd)) or 96)

    def activate(self, hwnd):
        """Best-effort: make the already-running WeChat window foreground.

        The user explicitly initiated the action; this never loops and never
        touches any other window. Returns True only when the window is the
        foreground window afterwards.
        """
        hwnd = int(hwnd)
        try:
            if u32.IsIconic(hwnd):
                u32.ShowWindow(hwnd, SW_RESTORE)
            if not u32.IsWindowVisible(hwnd):
                u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            if not u32.SetForegroundWindow(hwnd):
                return False
            return int(u32.GetForegroundWindow() or 0) == hwnd
        except (OSError, ValueError):
            return False

    def click(self, hwnd, x, y):
        """Physical client-coordinate click through SendInput (cursor moves)."""
        hwnd = int(hwnd)
        point = W.POINT(int(x), int(y))
        try:
            if not u32.ClientToScreen(hwnd, ctypes.byref(point)):
                return False
            u32.SetCursorPos(point.x, point.y)
            u32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, None)
            u32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, None)
            return True
        except (OSError, ValueError):
            return False

    def key_combo(self, keys):
        """Send one keyboard chord (keys pressed in order, released reversed)."""
        keys = tuple(int(key) for key in keys)
        if not keys:
            return False
        try:
            for key in keys:
                u32.keybd_event(key, 0, 0, None)
            for key in reversed(keys):
                u32.keybd_event(key, 0, KEYEVENTF_KEYUP, None)
            return True
        except (OSError, ValueError):
            return False

    def press_enter(self):
        return self.key_combo((VK_RETURN,))

    def set_clipboard_text(self, text):
        """Put the text on the clipboard (the only place the text leaves memory).

        The previous clipboard content is intentionally not read (it could hold
        personal data); the caller's synthetic text replaces it.
        """
        data = str(text).encode("utf-16-le") + b"\x00\x00"
        if not u32.OpenClipboard(None):
            return False
        allocated = False
        try:
            if not u32.EmptyClipboard():
                return False
            handle = k32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                return False
            pointer = k32.GlobalLock(handle)
            if not pointer:
                k32.GlobalFree(handle)
                return False
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                k32.GlobalUnlock(handle)
            if not u32.SetClipboardData(CF_UNICODETEXT, handle):
                k32.GlobalFree(handle)
                return False
            allocated = True
            return True
        except (OSError, ValueError):
            return False
        finally:
            if not allocated:
                pass
            u32.CloseClipboard()

    def sleep(self, seconds):
        time.sleep(max(0.0, float(seconds)))


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def _normalize_title(value):
    return " ".join(str(value or "").split()).casefold()


def _rect_ok(rect):
    if rect is None:
        return False
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        return False
    try:
        left, top, right, bottom = (int(value) for value in rect)
    except (TypeError, ValueError):
        return False
    return right > left and bottom > top


class WeChatActions:
    """Time-bounded, structured conversation selection and message sending."""

    def __init__(self, backend=None, *, clock=None, sleep=None,
                 settle=0.5, settle_after_search=0.8):
        self.backend = backend if backend is not None else Win32Backend()
        self._clock = clock if clock is not None else time.monotonic
        self._sleep = sleep if sleep is not None else self.backend.sleep
        self.settle = float(settle)
        self.settle_after_search = float(settle_after_search)

    def _resolve_window(self, window):
        """Return ``(hwnd, failure_reason_or_None)``; backend-aware for tests."""
        if isinstance(window, dict):
            window = window.get("hwnd", window.get("window"))
        if isinstance(window, WindowTarget):
            # Identity-verified handle: exe + PID + process start time + HWND.
            return (int(window), None) if discovery.valid(window) else (None, "stale_window")
        if isinstance(window, bool):
            return None, "invalid_window"
        if isinstance(window, int):
            if window and self.backend.is_window(window):
                return window, None
            return None, "invalid_window"
        return None, "invalid_window"

    # -- select --------------------------------------------------------------
    def select_conversation(self, window, title, *, search_rect, result_rect,
                            input_rect=None, send_rect=None, verify=None,
                            title_rect=None, dry_run=False, timeout=8.0, dpi=None):
        """Select the conversation titled ``title``; fail closed on mismatch.

        ``verify`` is a zero-argument callable returning the *currently visible*
        conversation title (``str``) or ``None`` when it cannot be read (e.g. the
        Lead's existing OCR path). Without ``verify`` or ``title_rect`` the
        action fails with ``verification_required`` before touching the window.
        """
        started = self._clock()
        steps = []

        def fail(reason, detail=""):
            return ActionResult(ok=False, reason=reason, detail=detail,
                                selected=False, verified=False,
                                dry_run=bool(dry_run), steps=tuple(steps),
                                elapsed=self._clock() - started)

        hwnd, window_reason = self._resolve_window(window)
        if hwnd is None:
            return fail(window_reason or "invalid_window")
        normalized = _normalize_title(title)
        if not normalized:
            return fail("invalid_title")
        if not _rect_ok(search_rect) or not _rect_ok(result_rect):
            return fail("invalid_rect")
        if verify is None:
            if title_rect is None or not _rect_ok(title_rect):
                return fail("verification_required")
            verifier = make_ocr_verifier(hwnd, title_rect, dpi=dpi)
        elif callable(verify):
            verifier = verify
        else:
            return fail("verification_required")

        try:
            dpi_value = int(dpi or self.backend.dpi(hwnd) or 96)
            if dpi_value <= 0:
                return fail("backend_error", "dpi")
            deadline = started + float(timeout)

            def expired():
                return self._clock() >= deadline

            steps.append("activate")
            if expired():
                return fail("timeout", "activate")
            if not self.backend.activate(hwnd):
                return fail("activate_failed")
            self._sleep(0.15)

            steps.append("click_search")
            if expired():
                return fail("timeout", "click_search")
            if not self.backend.click(
                    hwnd, *logical_point_to_physical(rect_center(search_rect), dpi_value)):
                return fail("search_failed", "click_search")
            self._sleep(0.15)

            steps.append("clear_search")
            self.backend.key_combo((VK_CONTROL, VK_A))
            steps.append("paste_title")
            if not self.backend.set_clipboard_text(str(title)):
                return fail("clipboard_failed")
            if not self.backend.key_combo((VK_CONTROL, VK_V)):
                return fail("input_failed", "paste_title")
            self._sleep(self.settle_after_search)

            steps.append("click_result")
            if expired():
                return fail("timeout", "click_result")
            if not self.backend.click(
                    hwnd, *logical_point_to_physical(rect_center(result_rect), dpi_value)):
                return fail("search_failed", "click_result")
            self._sleep(self.settle)

            steps.append("verify")
            if expired():
                return fail("timeout", "verify")
            try:
                current = verifier()
            except TimeoutError:
                return fail("timeout", "verify")
            except (OSError, RuntimeError, ValueError):
                return fail("title_unverified")
            if current is None or not str(current).strip():
                return fail("title_unverified")
            if _normalize_title(current) != normalized:
                # Fail closed: never send after landing on the wrong chat.
                return fail("title_mismatch")
            return ActionResult(ok=True, reason="ok", selected=True,
                                verified=True, dry_run=bool(dry_run),
                                steps=tuple(steps), elapsed=self._clock() - started)
        except (OSError, RuntimeError) as exc:
            return fail("backend_error", type(exc).__name__)

    # -- send ----------------------------------------------------------------
    def send_message(self, window, text, *, input_rect, send_rect=None,
                     select_all=True, dry_run=False, use_enter=None,
                     verify_sent=None, timeout=8.0, dpi=None):
        """Fill ``text`` into the current chat and send it (unless ``dry_run``).

        ``use_enter=None`` chooses Enter when no ``send_rect`` is given, else the
        send button. ``verify_sent`` (optional callable ``(text) -> bool``)
        verifies the outgoing bubble after sending.
        """
        started = self._clock()
        steps = []

        def fail(reason, detail="", sent=False):
            return ActionResult(ok=False, reason=reason, detail=detail,
                                sent=bool(sent), verified=False,
                                dry_run=bool(dry_run), steps=tuple(steps),
                                elapsed=self._clock() - started)

        hwnd, window_reason = self._resolve_window(window)
        if hwnd is None:
            return fail(window_reason or "invalid_window")
        text = str(text or "")
        if not text.strip():
            return fail("empty_text")
        if len(text) > MAX_TEXT_CHARS:
            return fail("text_too_long")
        if not _rect_ok(input_rect):
            return fail("invalid_rect")
        if use_enter is None:
            use_enter = send_rect is None
        if not use_enter and not _rect_ok(send_rect):
            return fail("invalid_rect", "send_rect")

        try:
            dpi_value = int(dpi or self.backend.dpi(hwnd) or 96)
            if dpi_value <= 0:
                return fail("backend_error", "dpi")
            deadline = started + float(timeout)

            def expired():
                return self._clock() >= deadline

            steps.append("activate")
            if expired():
                return fail("timeout", "activate")
            if not self.backend.activate(hwnd):
                return fail("activate_failed")
            self._sleep(0.15)

            steps.append("click_input")
            if expired():
                return fail("timeout", "click_input")
            if not self.backend.click(
                    hwnd, *logical_point_to_physical(rect_center(input_rect), dpi_value)):
                return fail("input_failed", "click_input")
            self._sleep(0.1)

            if select_all:
                steps.append("select_all")
                self.backend.key_combo((VK_CONTROL, VK_A))

            steps.append("paste_text")
            if not self.backend.set_clipboard_text(text):
                return fail("clipboard_failed")
            if not self.backend.key_combo((VK_CONTROL, VK_V)):
                return fail("input_failed", "paste_text")
            self._sleep(0.15)

            if dry_run:
                return ActionResult(ok=True, reason="ok", sent=False,
                                    selected=True, verified=False, dry_run=True,
                                    steps=tuple(steps),
                                    elapsed=self._clock() - started)

            if expired():
                return fail("timeout", "send")
            if use_enter:
                steps.append("press_enter")
                if not self.backend.press_enter():
                    return fail("send_failed", "press_enter")
            else:
                steps.append("click_send")
                if not self.backend.click(
                        hwnd, *logical_point_to_physical(rect_center(send_rect),
                                                        dpi_value)):
                    return fail("send_failed", "click_send")

            if verify_sent is not None:
                steps.append("verify_sent")
                try:
                    verified = bool(verify_sent(text))
                except (OSError, RuntimeError, ValueError):
                    verified = False
                if not verified:
                    # The message may already be sent; report it explicitly.
                    return fail("send_verify_failed", "after_send", sent=True)
                return ActionResult(ok=True, reason="ok", sent=True, selected=True,
                                    verified=True, dry_run=False,
                                    steps=tuple(steps),
                                    elapsed=self._clock() - started)
            return ActionResult(ok=True, reason="ok", sent=True, selected=True,
                                verified=False, dry_run=False, steps=tuple(steps),
                                elapsed=self._clock() - started)
        except (OSError, RuntimeError) as exc:
            return fail("backend_error", type(exc).__name__)


# ---------------------------------------------------------------------------
# Module-level convenience functions (used by the Lead real-window script)
# ---------------------------------------------------------------------------

def select_conversation(window, title, *, search_rect, result_rect, input_rect=None,
                        send_rect=None, verify=None, title_rect=None, dry_run=False,
                        timeout=8.0, dpi=None, backend=None, clock=None, sleep=None):
    actions = WeChatActions(backend, clock=clock, sleep=sleep)
    return actions.select_conversation(
        window, title, search_rect=search_rect, result_rect=result_rect,
        input_rect=input_rect, send_rect=send_rect, verify=verify,
        title_rect=title_rect, dry_run=dry_run, timeout=timeout, dpi=dpi)


def send_message(window, text, *, input_rect, send_rect=None, select_all=True,
                 dry_run=False, use_enter=None, verify_sent=None, timeout=8.0,
                 dpi=None, backend=None, clock=None, sleep=None):
    actions = WeChatActions(backend, clock=clock, sleep=sleep)
    return actions.send_message(
        window, text, input_rect=input_rect, send_rect=send_rect,
        select_all=select_all, dry_run=dry_run, use_enter=use_enter,
        verify_sent=verify_sent, timeout=timeout, dpi=dpi)


# ---------------------------------------------------------------------------
# Default in-memory OCR title verifier (optional; no screenshot persisted)
# ---------------------------------------------------------------------------

def _read_title_via_ocr(hwnd, title_rect, *, dpi=96):
    """Capture this HWND in memory and OCR the title rectangle; ``None`` on failure.

    The frame lives in memory only. The crop is mapped from the logical client
    rectangle using the window DPI and the client-area origin; WGC window frames
    may include window chrome, so calibrate ``title_rect`` on the real client
    (the Lead's existing worker/OCR path can also be passed as ``verify``).
    """
    try:
        import numpy as np
        from windows_capture import WindowsCapture
        from vendor.ocr import read_title
    except ImportError:
        return None
    frames, closed = [], []

    try:
        capture = WindowsCapture(cursor_capture=None, draw_border=None,
                                 window_hwnd=int(hwnd))

        @capture.event
        def on_frame_arrived(frame, control):
            frames.append(np.array(frame.frame_buffer[:, :, :3], copy=True))
            control.stop()

        @capture.event
        def on_closed():
            closed.append(True)

        control = capture.start_free_threaded()
        deadline = time.monotonic() + 4.0
        while not frames and not closed and time.monotonic() < deadline:
            time.sleep(0.05)
        try:
            control.stop()
        except Exception:  # noqa: BLE001 - capture teardown must not mask result
            pass
        if not frames:
            return None
        image = frames[0][:, :, ::-1]  # WGC gives BGRA; OCR expects RGB
        scale = dpi_scale(dpi)
        if title_rect is None:
            # Reuse the proven app OCR path: detect the chat header, then OCR it.
            try:
                from vendor.area import chat_area
            except ImportError:
                return None
            area = chat_area(np.ascontiguousarray(image),
                             max(45, round(60 * scale)))
            if area is None:
                return None
            x0, y0, x1, y1, _bg, top = area
            crop = image[top:y0, x0:x1]
        else:
            left, top, right, bottom = (
                int(round(value * scale)) for value in (int(v) for v in title_rect))
            height, width = image.shape[0], image.shape[1]
            left, top = max(0, left), max(0, top)
            right, bottom = min(width, right), min(height, bottom)
            if right <= left or bottom <= top:
                return None
            crop = image[top:bottom, left:right]
        if not crop.size:
            return None
        title = read_title(np.ascontiguousarray(crop))
        return title or None
    except (OSError, RuntimeError, ValueError):
        return None
