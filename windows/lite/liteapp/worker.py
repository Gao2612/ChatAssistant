"""Read only capture process. Never reparent, activate or restore WeChat windows."""
import os
import queue
import threading
import time


def encode_preview(image, max_width=1100, quality=72):
    """Compress an in-memory RGB capture for the UI; never write frames to disk."""
    from io import BytesIO
    from PIL import Image

    frame = Image.fromarray(image, "RGB")
    if frame.width > max_width:
        frame.thumbnail((max_width, max(1, frame.height)), Image.Resampling.BILINEAR)
    output = BytesIO()
    frame.save(output, format="JPEG", quality=quality, optimize=False)
    return output.getvalue()


def encode_reading(image, max_width=1400):
    """Higher-quality crop for an explicitly selected remote reading backend."""
    return encode_preview(image, max_width=max_width, quality=85)


def significant_visual_change(previous, current, threshold=0.002):
    """Ignore tiny animated/caret pixels for OCR freshness; text still gets OCR'd."""
    import numpy as np
    if previous.shape != current.shape:
        return True
    old = previous[::4, ::4].astype(np.int16)
    new = current[::4, ::4].astype(np.int16)
    changed = np.any(np.abs(new - old) > 24, axis=2)
    return bool(np.count_nonzero(changed) / changed.size >= threshold)


def run(out, stop, hwnd, session, frame_clock, reading_backend="ocr"):
    import ctypes
    from .discovery import (WindowTarget, valid, minimized, dpi, popup_covering,
                            window_identity, window_status)
    latest = [None]
    latest_seq = [0]
    last_clock_image = [None]
    last_read_key = [None]
    remote_reading = reading_backend in ("api", "codex")
    closed = threading.Event()
    lock = threading.Lock()
    ctl = None

    def clear_target():
        nonlocal hwnd
        with lock:
            closed.set()
            hwnd = None
            latest[0] = None

    def stop_capture():
        nonlocal ctl
        # The callback checks the same gate under this lock, so no frame can be
        # published after this point even if WGC calls it while stop() is running.
        with lock:
            closed.set()
        current, ctl = ctl, None
        if current is not None:
            try:
                current.stop()
            except Exception:
                pass
        with lock:
            latest[0] = None
            latest_seq[0] = 0
        if frame_clock is not None:
            try:
                with frame_clock.get_lock():
                    frame_clock[0] = 0
                    frame_clock[1] = 0
            except Exception:
                pass

    def emit(kind, **data):
        packet = dict(kind=kind, session=session, **data)
        try:
            out.put_nowait(packet)
        except queue.Full:
            # A producer must not drain an mp.Queue while the UI consumes it:
            # concurrent get_nowait/get can corrupt its semaphore on Windows.
            # The next capture interval publishes a fresh packet after the UI drains.
            pass

    try:
        # The UI passes this int-compatible snapshot through multiprocessing. A bare/reused
        # HWND is not enough to identify the process selected during discovery.
        if not isinstance(hwnd, WindowTarget) or not valid(hwnd):
            clear_target()
            stop_capture()
            emit("closed", message="微信窗口身份已变化，请重新扫描后连接。")
            return
        hwnd_value = int(hwnd)
        import numpy as np
        from windows_capture import WindowsCapture
        from vendor.area import chat_area
        from vendor.ocr import Reader, read_title
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except Exception:
            pass
        emit("status", message="正在加载本地 OCR…")
        reader = Reader()
        if not valid(hwnd):
            clear_target()
            stop_capture()
            emit("closed", message="微信窗口身份已变化，请重新扫描后连接。")
            return
        wgc_gate = threading.Event()
        ctl = None

        def start_wgc():
            nonlocal ctl
            if ctl is not None:
                return
            cap = WindowsCapture(cursor_capture=None, draw_border=None,
                                 window_hwnd=hwnd_value)

            @cap.event
            def on_frame_arrived(frame, control):
                if closed.is_set() or not wgc_gate.is_set():
                    return
                image = np.ascontiguousarray(frame.frame_buffer[:, :, :3][:, :, ::-1])
                with lock:
                    if closed.is_set():
                        return
                    previous = last_clock_image[0]
                    if previous is None or significant_visual_change(previous, image):
                        latest_seq[0] += 1
                        last_clock_image[0] = image
                        with frame_clock.get_lock():
                            frame_clock[0] = latest_seq[0]
                            frame_clock[1] = time.monotonic_ns()
                    latest[0] = image

            @cap.event
            def on_closed():
                with lock:
                    closed.set()

            wgc_gate.set()
            ctl = cap.start_free_threaded()

        def stop_wgc():
            nonlocal ctl
            wgc_gate.clear()
            current, ctl = ctl, None
            if current is not None:
                try:
                    current.stop()
                except Exception:
                    pass

        # WGC cannot attach to a tray-hidden window; start it anyway (failure is
        # tolerated) so visible windows begin capturing immediately. Hidden windows
        # stop it and use the off-screen tray capture below until reopened.
        try:
            start_wgc()
        except Exception:
            stop_wgc()
        last_frame = None
        last_hidden_capture = [0.0]
        identity_changed = False
        while not stop.wait(1.2) and not closed.is_set():
            if not valid(hwnd):
                clear_target()
                identity_changed = True
                break
            # A native popup (image preview, file dialog, menu…) keeps its own native
            # top-level relationship; the snapshot is marked unavailable instead of
            # analysing popup text or fighting its position.
            popup_hwnd = popup_covering(hwnd_value)
            if popup_hwnd:
                emit("unavailable", state="popup", popup_hwnd=int(popup_hwnd),
                     message="微信弹窗覆盖聊天区域，已暂停分析，请先关闭弹窗。")
                last_frame = None
                with lock:
                    latest[0] = None
                continue
            try:
                lifecycle = window_status(hwnd_value)
                hidden_now = lifecycle != "visible"
            except Exception:
                lifecycle = "visible"
                hidden_now = False
            official_wechat = False
            if hidden_now:
                try:
                    identity = window_identity(hwnd_value) or {}
                    exe = str(identity.get("path") or "").lower()
                    official_wechat = exe.endswith(("weixin.exe", "wechat.exe"))
                except Exception:
                    official_wechat = False
            if hidden_now:
                if ctl is not None:
                    stop_wgc()
            elif ctl is None:
                try:
                    start_wgc()
                except Exception:
                    stop_wgc()
            if hidden_now and lifecycle == "minimized":
                # Never show/move a minimized WeChat to grab it: doing so fights the
                # user's attempt to restore it from the taskbar and can keep stealing
                # the window. Keep the state read-only and wait for a real restore.
                emit("unavailable", state="minimized",
                     message="微信已最小化，读取已暂停；还原微信窗口后自动继续。")
                last_frame = None
                with lock:
                    latest[0] = None
                stop.wait(0.8)
                continue
            if hidden_now:
                # Window closed to tray / fully hidden: capture it through a brief
                # off-screen, non-activating show (tray_capture restores its position
                # and hidden state) so the assistant keeps reading without stealing
                # focus. Throttled because each capture briefly shows the window
                # off-screen. A merely minimized window is skipped above instead.
                now = time.monotonic()
                if now - last_hidden_capture[0] < 2.5:
                    stop.wait(0.4)
                    continue
                last_hidden_capture[0] = now
                from .tray_capture import capture_hidden_window
                try:
                    tray_image = capture_hidden_window(hwnd) if official_wechat else None
                except Exception:
                    tray_image = None
                if tray_image is None:
                    emit("unavailable", state="minimized",
                         message="微信已隐藏到托盘，暂时无法读取画面；打开微信后可继续。")
                    last_frame = None
                    with lock:
                        latest[0] = None
                    continue
                latest_seq[0] += 1
                frame_seq = latest_seq[0]
                captured_at_ns = time.monotonic_ns()
                with frame_clock.get_lock():
                    frame_clock[0] = frame_seq
                    frame_clock[1] = captured_at_ns
                full = tray_image
                last_frame = None  # compare fresh tray frames normally below
            if not hidden_now:
                with lock:
                    full, latest[0] = latest[0], None
                    frame_seq = latest_seq[0]
                    captured_at_ns = int(frame_clock[1])
            if full is None:
                continue
            if last_frame is not None and full.shape == last_frame.shape and np.array_equal(full, last_frame):
                continue
            last_frame = full
            if not full.any():
                emit("unavailable", message="没有收到可识别画面，请打开微信聊天。")
                continue
            emit("frame", image=encode_preview(full), frame_seq=frame_seq)
            area = chat_area(full, max(45, round(60 * dpi(hwnd_value) / 96)))
            if area is None:
                emit("unavailable", message="请打开一个聊天会话，并适当放大微信窗口。")
                continue
            x0, y0, x1, y1, bg, top = area
            header = full[top:y0, x0:x1]
            if not header.size:
                emit("unavailable", message="没有识别到会话名称。")
                continue
            title = read_title(header)
            if not title or title in ("微信", "Weixin", "WeChat", "扫码登录"):
                emit("unavailable", message="会话名称未确认，请打开聊天或使用手动输入。")
                continue
            # Reset line-height inference for each visible snapshot; no fuzzy title merging.
            reader.lh = None
            lines = reader.read(full[y0:y1, x0:x1], bg)
            messages = [(w, n or "", t) for w, n, t, _ in lines]
            if not valid(hwnd):
                clear_target()
                identity_changed = True
                full = None
                break
            emit("snapshot", title=title, messages=messages, ocr_ms=reader.last_ms,
                 frame_seq=frame_seq, captured_at_ns=captured_at_ns)
            if remote_reading:
                # Only a confirmed text change triggers a paid/far reading call.
                key = (title, tuple(messages))
                crop = full[top:y1, x0:x1]
                if key != last_read_key[0] and crop.size:
                    last_read_key[0] = key
                    emit("read_frame", image=encode_reading(crop), title=title,
                         frame_seq=frame_seq, captured_at_ns=captured_at_ns)
        if identity_changed:
            clear_target()
        stop_capture()
        if identity_changed:
            emit("closed", message="微信窗口身份已变化，请重新扫描后连接。")
        else:
            emit("closed", message="微信窗口已关闭，等待重新连接。")
    except Exception as e:
        # Exception class is enough for diagnostics; never log frames, text or
        # credentials. Opt-in traceback only when explicitly debugging.
        if os.environ.get("CHATASSISTANT_DEBUG_TRACEBACK"):
            import traceback
            traceback.print_exc()
        clear_target()
        stop_capture()
        emit("error", message="采集失败（" + type(e).__name__ + "），请暂停后重试。")
    finally:
        if ctl is not None:
            try:
                ctl.stop()
            except Exception:
                pass
