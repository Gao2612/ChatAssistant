"""Read-only snapshot capture for a WeChat window hidden in the tray.

WeChat keeps the main window when the user closes it to the tray, but a purely
hidden window has no DWM surface (PrintWindow returns black) and WGC cannot start.
This module briefly moves the window off-screen, shows it *without activation* for
one render, grabs it with PrintWindow, then restores its original position and
hidden/minimized state. No focus change, no SetParent, no chat content is written
to disk.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import time

import numpy as np

from .discovery import WindowTarget, valid, window_status

_u32 = ctypes.windll.user32
_gdi32 = ctypes.windll.gdi32

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_MINIMIZE = 6
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_HIDEWINDOW = 0x0080
OFFSCREEN_X = -30000


class _BitmapInfoHeader(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


def _rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wt.RECT()
    if not _u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError("GetWindowRect failed")
    return rect.left, rect.top, rect.right, rect.bottom


def _print_window_bgr(hwnd: int) -> np.ndarray | None:
    left, top, right, bottom = _rect(hwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)
    window_dc = _u32.GetWindowDC(hwnd)
    if not window_dc:
        return None
    memory_dc = _gdi32.CreateCompatibleDC(window_dc)
    bitmap = _gdi32.CreateCompatibleBitmap(window_dc, width, height)
    try:
        _gdi32.SelectObject(memory_dc, bitmap)
        header = _BitmapInfoHeader()
        header.biSize = ctypes.sizeof(_BitmapInfoHeader)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        ok = _u32.PrintWindow(hwnd, memory_dc, 2)
        _gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer,
                         ctypes.byref(header), 0)
        image = np.frombuffer(buffer.raw, dtype=np.uint8).reshape(height, width, 4)
        if not ok:
            return None
        return np.ascontiguousarray(image[:, :, :3])
    finally:
        _gdi32.DeleteObject(bitmap)
        _gdi32.DeleteDC(memory_dc)
        _u32.ReleaseDC(hwnd, window_dc)


def capture_hidden_window(window, *, settle: float = 0.85,
                          minimum_ink: float = 0.01) -> np.ndarray | None:
    """Return a BGR frame for a hidden/minimized window, or None.

    ``window`` must be a verified :class:`WindowTarget`; a visible window is not
    handled here (the normal WGC path must be used). The helper never activates the
    window nor changes which monitor it belongs to.
    """
    if not isinstance(window, WindowTarget) or not valid(window):
        return None
    hwnd = int(window)
    # A window closed to the tray is often DWM-cloaked yet still reports
    # IsWindowVisible=True (WeChat just parks it off-screen); use the richer
    # lifecycle status so the hidden path is reachable. Only a genuinely visible
    # window is left to the normal WGC path.
    if window_status(hwnd) == "visible":
        return None
    was_minimized = bool(_u32.IsIconic(hwnd))
    left, top, right, bottom = _rect(hwnd)
    width = max(1, right - left)
    height = max(1, bottom - top)
    try:
        # Hide first so moving/showing never paints at the old position.
        _u32.ShowWindow(hwnd, SW_HIDE)
        _u32.SetWindowPos(hwnd, 0, OFFSCREEN_X, top, 0, 0,
                          SWP_NOZORDER | SWP_NOACTIVATE)
        _u32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
        time.sleep(max(0.0, settle))
        # WeChat may restore its last normal size asynchronously after the first
        # show; force the saved geometry before grabbing the surface.
        cur_left, cur_top, cur_right, cur_bottom = _rect(hwnd)
        if (cur_right - cur_left, cur_bottom - cur_top) != (width, height):
            _u32.SetWindowPos(hwnd, 0, OFFSCREEN_X, top, width, height,
                              SWP_NOZORDER | SWP_NOACTIVATE)
            time.sleep(0.25)
        image = _print_window_bgr(hwnd)
        if image is None:
            return None
        ink = float((image.max(axis=2) > 16).mean())
        return image if ink >= minimum_ink else None
    finally:
        # Restore geometry and hide atomically: a separate ShowWindow(SW_HIDE) can
        # be intercepted by WeChat's tray state machine. Verify and hide again if
        # the window became visible during the off-screen render.
        _u32.SetWindowPos(hwnd, 0, left, top, width, height,
                          SWP_NOZORDER | SWP_NOACTIVATE | SWP_HIDEWINDOW)
        if _u32.IsWindowVisible(hwnd):
            _u32.ShowWindow(hwnd, SW_HIDE)
        if was_minimized:
            _u32.ShowWindow(hwnd, SW_MINIMIZE)
