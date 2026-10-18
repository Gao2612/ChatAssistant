"""OCR-located conversation finder for the real WeChat window (T10/T12).

The fixed-ratio layout estimate is not stable across WeChat versions; this module
locates the search box, clicks the **exact-name** search-result row and verifies
the conversation header before returning.

Safety rules (hard):

* the search-result dropdown is rendered outside the main window's WGC surface,
  so the finder grabs the foreground window from the screen (in memory only,
  never written to disk); the window must already be visible and activated;
* only a row whose normalized OCR text equals the target name is clicked — a
  substring match could open a message-preview or network-search row and switch
  to the wrong conversation; an ambiguous (duplicate) row fails closed;
* the header is accepted only on an exact normalized match (three crops/readers);
  a single-character difference is reported as ``title_mismatch``, never accepted;
* no Enter is ever pressed after the result click: if focus returns to an
  existing draft, Enter would send a message. Builds that keep the overlay open
  must fail closed instead.
"""
from __future__ import annotations

import ctypes
import time
from dataclasses import dataclass, field

from . import wechat_actions as wa
from .discovery import window_status

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


def _foreground_ok(hwnd: int) -> bool:
    """Visible and actually foreground; screen capture/click is only safe then."""
    import ctypes
    try:
        if window_status(int(hwnd)) != "visible":
            return False
        return int(ctypes.windll.user32.GetForegroundWindow() or 0) == int(hwnd)
    except (OSError, ValueError):
        return False


def capture_client_bgr(hwnd: int, *, timeout: float = 5.0):
    """Return (RGB frame cropped to the visible client area, width, height) or None.

    WeChat 4.x renders the search-result dropdown in a separate layered surface:
    a Windows Graphics Capture of the main HWND **does not contain it** (measured
    2026-10-18), so result rows could never be seen that way and the older flow
    clicked stale sidebar rows instead. The foreground window is therefore
    grabbed from the screen (in memory only, never written to disk) and cropped
    to the client area with ``ClientToScreen``. Safety: only a visible window that
    is the real foreground window is captured; otherwise this returns None so the
    finder fails closed instead of reading/clicking another window's pixels.
    """
    import ctypes.wintypes as wt

    import numpy as np

    if not _foreground_ok(hwnd):
        return None
    client = wt.RECT()
    window = wt.RECT()
    if not ctypes.windll.user32.GetClientRect(hwnd, ctypes.byref(client)):
        return None
    width, height = int(client.right), int(client.bottom)
    if width <= 0 or height <= 0:
        return None
    try:
        from PIL import ImageGrab

        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(window)):
            raise OSError("GetWindowRect")
        origin = wt.POINT(0, 0)
        if not ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(origin)):
            raise OSError("ClientToScreen")
        offset_x = int(origin.x) - int(window.left)
        offset_y = int(origin.y) - int(window.top)
        grabbed = np.array(ImageGrab.grab(
            bbox=(int(window.left), int(window.top),
                  int(window.right), int(window.bottom)),
            all_screens=True))[:, :, :3]
        left, top = max(0, offset_x), max(0, offset_y)
        right = min(grabbed.shape[1], offset_x + width)
        bottom = min(grabbed.shape[0], offset_y + height)
        if right - left >= 40 and bottom - top >= 40:
            return grabbed[top:bottom, left:right], width, height
    except (ImportError, OSError, RuntimeError, ValueError):
        pass
    return _capture_wgc_client_bgr(hwnd, width, height, timeout)


def _capture_wgc_client_bgr(hwnd: int, width: int, height: int,
                            timeout: float = 5.0):
    """Fallback: main-window WGC surface (does not include the search dropdown)."""
    import numpy as np
    try:
        from windows_capture import WindowsCapture
    except ImportError:
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
    return (image[:min(image.shape[0], height + 2),
                  :min(image.shape[1], width + 2)], width, height)


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


def _result_rows(rows, title, width, height):
    """Exact-name search-result rows only; message previews/inputs never qualify.

    WeChat's search panel mixes the contact row (name only) with message-history
    rows whose text merely contains the query (e.g. ``包含：部亚禹``), network
    results (``Q...`` prefix) and the search input itself. A substring check used
    to click those rows and opened the wrong conversation, which only the header
    check caught. Only a row whose normalized text equals the target name *and*
    which sits below the search input band is a safe click target.
    """
    min_y = int(height * 0.10)
    return [row for row in rows
            if row[3] < int(width * 0.33) and row[2] >= min_y
            and _title_matches(row[0], title)]


def _header_secondary_match(reader, frame, width, height, title):
    """Second, tighter header crop + 2x upscale, still requiring an exact match.

    No fuzzy tolerance: a header that differs by even one character can belong to
    a different contact, so a controlled tolerance is only reporting evidence,
    never an acceptance path.
    """
    import numpy as np
    crop = frame[int(height * 0.02):int(height * 0.11),
                 int(width * 0.24):int(width * 0.62)]
    if crop.size == 0:
        return False
    images = [np.ascontiguousarray(crop)]
    try:
        import cv2
        images.append(cv2.resize(images[0], None, fx=2.0, fy=2.0,
                                 interpolation=cv2.INTER_CUBIC))
    except Exception:  # noqa: BLE001 - optional OCR aid
        pass
    for image in images:
        try:
            rows = _ocr_rows(reader, image)
        except (OSError, RuntimeError, ValueError):
            continue
        if any(_title_matches(row[0], title) for row in rows):
            return True
    try:
        if _title_matches(_read_title_crop(crop), title):
            return True
    except (ImportError, OSError, RuntimeError, ValueError):
        pass
    return False


def _read_title_crop(crop):
    """App's own title reader on an RGB crop (imported lazily; tests may patch)."""
    import numpy as np
    from vendor.ocr import read_title
    return read_title(np.ascontiguousarray(crop))


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
            if not _foreground_ok(hwnd):
                return fail("not_foreground")
            backend.click(hwnd, (sx0 + sx1) // 2, (sy0 + sy1) // 2)
            if not _foreground_ok(hwnd):
                return fail("not_foreground")
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
                sidebar = frame[:, :max(1, int(width * 0.33))]
                candidates = _result_rows(_ocr_rows(reader, sidebar), title,
                                          width, height)
                if candidates:
                    break
                time.sleep(0.5)
            if candidates:
                break
        if not candidates:
            return fail("result_not_found")
        if len(candidates) > 1:
            # Two rows carry the identical name: the click target is ambiguous.
            _escape(backend, hwnd)
            return fail("result_ambiguous")
        _text, x0, y0, x1, y1 = candidates[0]
        if not _foreground_ok(hwnd):
            _escape(backend, hwnd)
            return fail("not_foreground")
        backend.click(hwnd, (x0 + x1) // 2, (y0 + y1) // 2)
        time.sleep(settle)
        # Never press Enter here: once the result opens, focus can return to an
        # existing draft, and Enter would send it. Verify the clicked result;
        # builds that keep the overlay open must fail closed instead.
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
            # Second, tighter header crop (measured x≈0.25w, title band) with an
            # exact match under 1x/2x and the app's own read_title path.
            if _header_secondary_match(reader, frame, width, height, title):
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
