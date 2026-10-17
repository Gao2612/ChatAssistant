"""OCR-located conversation finder for the real WeChat window (T10/T12).

The fixed-ratio layout estimate is not stable across WeChat versions; this module
locates the search box and the matching result row by OCR inside the window's
client frame, then verifies the conversation header before returning. It is the
same flow validated manually in the real-window acceptance, packaged for the UI.
"""
from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field

from . import wechat_actions as wa

try:  # make WGC frames and click coordinates share one physical coordinate space
    ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
except Exception:  # noqa: BLE001 - older Windows / already configured
    pass


@dataclass(frozen=True)
class FindResult:
    ok: bool
    reason: str
    title: str = ""
    steps: tuple[str, ...] = field(default=())
    elapsed: float = 0.0


def _resolve_hwnd(window) -> int | None:
    if window is None:
        return None
    if isinstance(window, int):
        return window
    if hasattr(window, "hwnd"):
        try:
            return int(window.hwnd)
        except (TypeError, ValueError):
            return None
    if isinstance(window, dict):
        try:
            return int(window["hwnd"])
        except (KeyError, TypeError, ValueError):
            return None
    return None


def capture_client_bgr(hwnd: int, *, timeout: float = 5.0):
    """Return (BGR frame cropped to the client area, width, height) or None."""
    import ctypes.wintypes as wt

    import numpy as np
    from windows_capture import WindowsCapture

    client = wt.RECT()
    if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(client)):
        return None
    frames = []
    capture = WindowsCapture(cursor_capture=None, draw_border=None,
                             window_hwnd=int(hwnd))

    @capture.event
    def on_frame_arrived(frame, control):
        frames.append(np.array(frame.frame_buffer[:, :, :3], copy=True))
        control.stop()

    @capture.event
    def on_closed():
        pass

    control = capture.start_free_threaded()
    deadline = time.monotonic() + timeout
    while not frames and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        control.stop()
    except Exception:  # noqa: BLE001 - teardown must not mask result
        pass
    if not frames:
        return None
    image = frames[0][:, :, ::-1]
    height = min(image.shape[0], client.bottom + 2)
    width = min(image.shape[1], client.right + 2)
    return image[:height, :width], client.right, client.bottom


def _ocr_rows(reader, image, top: int = 0):
    result, _ = reader.ocr(image, use_cls=False)
    rows = []
    for box, text, _conf in sorted(result or [],
                                   key=lambda row: (row[0][0][1], row[0][0][0])):
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        rows.append((str(text), int(min(xs)), int(min(ys)) + top,
                     int(max(xs)), int(max(ys)) + top))
    return rows


def find_conversation(window, title, *, backend=None, capture=None, reader=None,
                      settle: float = 0.9, timeout: float = 20.0) -> FindResult:
    """Activate WeChat, search ``title``, click the matching row and verify it."""
    started = time.monotonic()
    steps = []

    def fail(reason):
        return FindResult(ok=False, reason=reason, steps=tuple(steps),
                          elapsed=time.monotonic() - started)

    hwnd = _resolve_hwnd(window)
    if hwnd is None:
        return fail("invalid_window")
    title = str(title or "").strip()
    if not title:
        return fail("invalid_title")
    backend = backend or wa.Win32Backend()
    capture = capture or capture_client_bgr
    if reader is None:
        from vendor.ocr import Reader
        reader = Reader()
    if not backend.is_window(hwnd):
        return fail("invalid_window")
    steps.append("activate")
    if not backend.activate(hwnd):
        return fail("activate_failed")
    try:
        steps.append("capture")
        captured = capture(hwnd)
        if captured is None:
            return fail("capture_failed")
        frame, width, height = captured
        steps.append("find_search")
        sidebar = frame[:, :max(1, int(width * 0.32))]
        rows = _ocr_rows(reader, sidebar)
        search_rows = [row for row in rows
                       if row[0].strip() in ("搜索", "Q搜索") or row[0].endswith("搜索")]
        if not search_rows:
            return fail("search_not_found")
        _text, sx0, sy0, sx1, sy1 = search_rows[0]
        steps.append("paste_title")
        candidates = []
        for attempt in range(2):
            if attempt:
                # A transient overlay/focus state can swallow the first paste;
                # close it and repeat the search interaction once.
                try:
                    backend.key_combo((0x1B,))
                except (OSError, RuntimeError):
                    pass
                time.sleep(0.3)
                captured = capture(hwnd)
                if captured is None:
                    continue
                frame, width, _height = captured
                sidebar = frame[:, :max(1, int(width * 0.32))]
                rows = _ocr_rows(reader, sidebar)
                search_rows = [row for row in rows
                               if row[0].strip() in ("搜索", "Q搜索")
                               or row[0].endswith("搜索")]
                if not search_rows:
                    continue
                _text, sx0, sy0, sx1, sy1 = search_rows[0]
            backend.click(hwnd, (sx0 + sx1) // 2, (sy0 + sy1) // 2)
            backend.key_combo((wa.VK_CONTROL, wa.VK_A))
            if not backend.set_clipboard_text(title):
                return fail("clipboard_failed")
            backend.key_combo((wa.VK_CONTROL, wa.VK_V))
            time.sleep(settle + attempt * 0.35)
            steps.append("click_result")
            for _frame_attempt in range(4):
                captured = capture(hwnd)
                if captured is None:
                    time.sleep(0.4)
                    continue
                frame, width, height = captured
                sidebar = frame[:, :max(1, int(width * 0.32))]
                candidates = [row for row in _ocr_rows(reader, sidebar)
                              if title in row[0] and row[2] > 90]
                if candidates:
                    break
                time.sleep(0.5)
            if candidates:
                break
        if not candidates:
            return fail("result_not_found")
        _text, x0, y0, x1, y1 = candidates[0]
        backend.click(hwnd, (x0 + x1) // 2, (y0 + y1) // 2)
        time.sleep(settle)
        # Some WeChat builds select the result but keep the search overlay until
        # Enter; harmless when the overlay is already closed.
        try:
            backend.key_combo((0x0D,))
        except (OSError, RuntimeError):
            pass
        time.sleep(settle)
        steps.append("verify")
        matched = False
        for _attempt in range(4):
            captured = capture(hwnd)
            if captured is None:
                time.sleep(0.35)
                continue
            frame, width, height = captured
            header = frame[:max(1, int(height * 0.14)), int(width * 0.22):]
            if any(_title_matches(row[0], title) for row in _ocr_rows(reader, header)):
                matched = True
                break
            # Header OCR sometimes misreads one character (e.g. 郜→部) that the
            # sidebar result read correctly; try a 2x upscaled crop as well.
            try:
                import cv2
                enlarged = cv2.resize(header, None, fx=2.0, fy=2.0,
                                      interpolation=cv2.INTER_CUBIC)
                if any(_title_matches(row[0], title)
                       for row in _ocr_rows(reader, enlarged)):
                    matched = True
                    break
            except Exception:  # noqa: BLE001 - optional OCR aid
                pass
            time.sleep(0.35)
        if not matched:
            _escape(backend, hwnd)
            return fail("title_mismatch")
        return FindResult(ok=True, reason="ok", title=title, steps=tuple(steps),
                          elapsed=time.monotonic() - started)
    except (OSError, RuntimeError, ValueError):
        _escape(backend, hwnd)
        return fail("backend_error")


def _title_matches(ocr_text: str, title: str) -> bool:
    """Exact normalized match only: switching to the wrong contact is worse than failing."""
    clean = "".join(ch for ch in str(ocr_text or "")
                    if ch.strip() and ch not in "：:·.|")
    return clean == title


def _escape(backend, hwnd) -> None:
    try:
        backend.key_combo((0x1B,))  # ESC: close the search overlay, never send
    except (OSError, RuntimeError):
        pass
