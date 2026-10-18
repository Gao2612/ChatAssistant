r"""C1 A/B diagnostic harness (real WeChat window, no message sending).

Compares the App-internal "auto find conversation" path against direct calls on
the SAME WeChat window, recording only metrics/booleans/coordinates/timing:

* per-capture WGC frame size, mean/std, dark fraction (no image is written);
* OCR row counts and whether a search row was present (no text/chat content);
* click/activate/key codes; foreground-window role samples (app/wechat/other);
* process DPI awareness, window/client rect, per-variant reason/steps/elapsed.

Hard preflight: the selected WeChat window must be already ``visible``. This
script never minimizes/hides/moves/restores the user's WeChat window, never
sends a message, and never touches the installed 0.5.7 profile (isolated
LOCALAPPDATA only). If WeChat is not visible it stops and reports.

Run:
    G:\codex\wechat-lite-venv\Scripts\python.exe tools\c1_find_ab_diag.py
Optional env: C1_TARGET (default 田子夜), C1_HWND (specific visible hwnd).
Evidence JSON: test-output/c1/c1_find_ab_<stamp>.json
"""

import ctypes
import json
import os
import sys
import tempfile
import threading
import time
import traceback
from ctypes import wintypes as W
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISOLATED = Path(tempfile.mkdtemp(prefix="c1-harness-profile-"))
os.environ["LOCALAPPDATA"] = str(ISOLATED)  # isolate from the real user profile
DRY = os.environ.get("C1_DRY") == "1"  # offline smoke: offscreen Qt, no real find
if not DRY:
    os.environ.pop("QT_QPA_PLATFORM", None)

try:  # a hard Qt/ctypes crash must still leave a stack trace in the run log
    import faulthandler
    faulthandler.enable()
except Exception:  # noqa: BLE001
    pass

TARGET = os.environ.get("C1_TARGET", "田子夜")
WECHAT = os.environ.get("C1_WECHAT", r"C:\Program Files\Tencent\Weixin\Weixin.exe")
OUT_DIR = ROOT / "test-output" / "c1"
_VARIANT_FILTER = tuple(
    item.strip() for item in os.environ.get("C1_VARIANTS", "").split(",")
    if item.strip())


def _write_report(report):
    """Persist evidence to one stable path (idempotent, called after every variant)."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = report.get("_saved")
    if not path:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        path = str(OUT_DIR / f"c1_find_ab_{stamp}.json")
        report["_saved"] = path
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2)
    return path

import numpy as np  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from liteapp import build_info, discovery, settings  # noqa: E402
import liteapp.wechat_find as wf  # noqa: E402
from liteapp.wechat_actions import Win32Backend  # noqa: E402
from liteapp.ui import MainWindow  # noqa: E402

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.GetForegroundWindow.restype = ctypes.c_void_p
u32.GetWindowRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u32.GetClientRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
u32.SetForegroundWindow.argtypes = [W.HWND]
u32.SetForegroundWindow.restype = W.BOOL

METRICS = {"captures": [], "ocr": [], "clicks": [], "activate": [], "keys": [],
           "notes": []}
FOREGROUND_SAMPLES = []
_FG_STOP = threading.Event()


def fg_window():
    return int(u32.GetForegroundWindow() or 0)


def process_dpi_awareness():
    try:
        shcore = ctypes.WinDLL("shcore")
        value = ctypes.c_int(-1)
        shcore.GetProcessDpiAwareness(None, ctypes.byref(value))
        return value.value
    except Exception:  # noqa: BLE001
        return None


def window_metrics(hwnd):
    rect, client = W.RECT(), W.RECT()
    u32.GetWindowRect(int(hwnd), ctypes.byref(rect))
    u32.GetClientRect(int(hwnd), ctypes.byref(client))
    return {"rect": [rect.left, rect.top, rect.right, rect.bottom],
            "client": [client.right, client.bottom],
            "status": discovery.window_status(int(hwnd)),
            "dpi": discovery.dpi(int(hwnd)),
            "foreground": fg_window()}


def _frame_stats(frame):
    arr = np.asarray(frame)
    flat = arr.reshape(-1, 3)
    return {"frame_w": int(arr.shape[1]), "frame_h": int(arr.shape[0]),
            "mean_bgr": [round(float(v), 1) for v in flat.mean(axis=0)],
            "std": round(float(arr.std()), 1),
            "dark_fraction": round(float((flat.max(axis=1) < 20).mean()), 3)}


_orig_capture = wf.capture_client_bgr
_orig_ocr_rows = wf._ocr_rows


def _capture(hwnd, timeout=5.0):
    started = time.monotonic()
    result = _orig_capture(hwnd, timeout=timeout)
    entry = {"elapsed": round(time.monotonic() - started, 3),
             "ok": result is not None, "foreground": fg_window()}
    if result is not None:
        frame, width, height = result
        entry["client_from_win32"] = [int(width), int(height)]
        entry.update(_frame_stats(frame))
    METRICS["captures"].append(entry)
    return result


def _ocr_rows(reader, image, top=0):
    rows = _orig_ocr_rows(reader, image, top)
    search = [row for row in rows
              if str(row[0]).strip() in ("搜索", "Q搜索") or str(row[0]).endswith("搜索")]
    METRICS["ocr"].append({"rows": len(rows), "search_row": bool(search),
                           "image_w": int(image.shape[1]),
                           "image_h": int(image.shape[0]), "top": int(top)})
    return rows


wf.capture_client_bgr = _capture
wf._ocr_rows = _ocr_rows

_orig_click = Win32Backend.click
_orig_activate = Win32Backend.activate
_orig_key = Win32Backend.key_combo


def _click(self, hwnd, x, y):
    METRICS["clicks"].append([int(hwnd), int(x), int(y), fg_window()])
    return _orig_click(self, hwnd, x, y)


def _activate(self, hwnd):
    entry = {"hwnd": int(hwnd), "fg_before": fg_window()}
    result = _orig_activate(self, hwnd)
    entry.update({"result": bool(result), "fg_after": fg_window()})
    METRICS["activate"].append(entry)
    return result


def _key_combo(self, keys):
    keys = tuple(int(key) for key in keys)
    METRICS["keys"].append(list(keys))
    return _orig_key(self, keys)


Win32Backend.click = _click
Win32Backend.activate = _activate
Win32Backend.key_combo = _key_combo


def _foreground_sampler(role_hwnds):
    while not _FG_STOP.is_set():
        current = fg_window()
        role = "other"
        for name, hwnd in role_hwnds.items():
            if hwnd and current == hwnd:
                role = name
                break
        FOREGROUND_SAMPLES.append([round(time.monotonic(), 3), role, current])
        time.sleep(0.05)


class Harness:
    def __init__(self):
        self.app = QApplication.instance() or QApplication([])
        cfg = settings.load()
        cfg.update({"wechat_path": WECHAT, "auto_connect": False,
                    "dock_wechat": False, "auto_analysis": False,
                    "auto_send_allowed": False})
        self.window = MainWindow(config=cfg, scanning=False)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()
        self._ensure_foreground(int(self.window.winId()))
        self.wechat_hwnd = 0
        self.wechat_item = None

    def _ensure_foreground(self, hwnd):
        for _ in range(25):
            if fg_window() == int(hwnd):
                return True
            u32.SetForegroundWindow(int(hwnd))
            time.sleep(0.04)
        return fg_window() == int(hwnd)

    def app_foreground(self):
        return self._ensure_foreground(int(self.window.winId()))

    def bind_wechat(self):
        requested = os.environ.get("C1_HWND", "").strip()
        windows = discovery.find_wechat_windows(WECHAT, include_hidden=True)
        if requested:
            windows = [item for item in windows if int(item["hwnd"]) == int(requested)]
        item = next((entry for entry in windows if not entry["tool_window"]), None)
        if item is None:
            return {"bound": False, "reason": "no_wechat_window"}
        hwnd = int(item["hwnd"])
        status = discovery.window_status(hwnd)
        preflight = {"bound": True, "hwnd": hwnd, "status": status,
                     "metrics": window_metrics(hwnd),
                     "other_windows": len(windows)}
        if status != "visible":
            # Hard rule: never minimize/hide/move/restore the user's WeChat.
            preflight["aborted"] = "wechat_not_visible"
            return preflight
        self.wechat_hwnd = hwnd
        self.wechat_item = item
        self.window.window_item = {"hwnd": item["hwnd"], "id": item["id"]}
        self.window.settings_page.auto_target.setText(TARGET)
        self.window.show_settings()
        self.app.processEvents()
        self._ensure_foreground(int(self.window.winId()))
        return preflight

    def start_sampling(self):
        FOREGROUND_SAMPLES.clear()
        _FG_STOP.clear()
        threading.Thread(target=_foreground_sampler,
                         args=({"app": int(self.window.winId()),
                                "wechat": self.wechat_hwnd},), daemon=True).start()

    def stop_sampling(self):
        _FG_STOP.set()

    def reset_metrics(self):
        for key in METRICS:
            METRICS[key].clear()

    def _collect(self, name, result, elapsed):
        roles = []
        for sample in FOREGROUND_SAMPLES:
            if not roles or roles[-1] != sample[1]:
                roles.append(sample[1])
        return {"name": name,
                "ok": bool(getattr(result, "ok", False)),
                "reason": str(getattr(result, "reason", "") or ""),
                "steps": list(getattr(result, "steps", ()) or ()),
                "elapsed": round(float(elapsed), 2),
                "captures": list(METRICS["captures"]),
                "ocr": list(METRICS["ocr"]),
                "clicks": list(METRICS["clicks"]),
                "activate": list(METRICS["activate"]),
                "keys": list(METRICS["keys"]),
                "foreground_roles": roles,
                "unexpected_enter": any(0x0D in keys for keys in METRICS["keys"]),
                "notice": self.window.notice.text(),
                "wechat_after": window_metrics(self.wechat_hwnd)}

    def direct_main(self):
        self.app_foreground()
        self.reset_metrics()
        self.start_sampling()
        started = time.monotonic()
        try:
            result = wf.find_conversation(self.wechat_item, TARGET)
        finally:
            elapsed = time.monotonic() - started
            self.stop_sampling()
        return self._collect("direct_main", result, elapsed)

    def direct_thread(self):
        self.app_foreground()
        self.reset_metrics()
        self.start_sampling()
        out = {}

        def run():
            out["result"] = wf.find_conversation(self.wechat_item, TARGET)

        started = time.monotonic()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(40)
        elapsed = time.monotonic() - started
        self.stop_sampling()
        return self._collect("direct_thread", out.get("result"), elapsed)

    def direct_after_stop(self):
        self.app_foreground()
        self.window.stop_capture()
        time.sleep(0.05)
        self.reset_metrics()
        self.start_sampling()
        started = time.monotonic()
        try:
            result = wf.find_conversation(self.wechat_item, TARGET)
        finally:
            elapsed = time.monotonic() - started
            self.stop_sampling()
        return self._collect("direct_after_stop_capture", result, elapsed)

    def direct_after_stop_settle(self):
        self.app_foreground()
        self.window.stop_capture()
        self.reset_metrics()
        deadline = time.monotonic() + 3.0
        ready = False
        while time.monotonic() < deadline:
            frame = _capture(self.wechat_hwnd)
            if frame is not None and float(np.asarray(frame[0]).std()) > 6:
                ready = True
                break
            time.sleep(0.12)
        METRICS["notes"].append({"settle_ready": ready})
        started = time.monotonic()
        self.start_sampling()
        try:
            result = wf.find_conversation(self.wechat_item, TARGET)
        finally:
            elapsed = time.monotonic() - started
            self.stop_sampling()
        return self._collect("direct_after_stop_settle", result, elapsed)

    def ui_path(self, patched_prepare=None, no_stop_capture=False, name="ui_path_fixed"):
        self.app_foreground()
        recorded = {}
        original_done = self.window.wechat_action_done
        original_prepare = self.window._prepare_wechat_for_action
        original_stop = self.window.stop_capture
        if no_stop_capture:
            def skip_stop():
                METRICS["notes"].append({"stop_capture_skipped": True})
            self.window.stop_capture = skip_stop

        def done_wrapper(action, result, error):
            recorded["action"] = action
            recorded["result_reason"] = str(getattr(result, "reason", "") or "")
            recorded["error"] = error
            recorded["is_result"] = bool(getattr(result, "ok", False))
            return original_done(action, result, error)

        self.window.wechat_action_done = done_wrapper
        if patched_prepare is not None:
            self.window._prepare_wechat_for_action = patched_prepare
        self.reset_metrics()
        self.start_sampling()
        started = time.monotonic()
        try:
            self.window.find_wechat_conversation()
            loop = QEventLoop()
            poll = QTimer()
            poll.timeout.connect(lambda: loop.quit() if not self.window.wechat_busy else None)
            poll.start(150)
            QTimer.singleShot(45000, loop.quit)
            loop.exec()
            poll.stop()
        finally:
            elapsed = time.monotonic() - started
            self.stop_sampling()
            self.window.wechat_action_done = original_done
            self.window._prepare_wechat_for_action = original_prepare
            self.window.stop_capture = original_stop
        collected = self._collect(name, None, elapsed)
        collected["ok"] = bool(recorded.get("is_result", False))
        collected["reason"] = recorded.get("result_reason", "")
        collected["error"] = recorded.get("error", "")
        return collected

    def ui_path_without_stop(self):
        """Same UI path but keep our own capture running (WGC contention check)."""
        return self.ui_path(no_stop_capture=True, name="ui_path_without_stop_capture")

    def ui_path_legacy(self):
        """Pre-C1 UI path: no readiness wait and no transient retry."""
        window = self.window
        wait, retries = window._wechat_find_ready_wait, window._wechat_find_retries
        window._wechat_find_ready_wait, window._wechat_find_retries = 0.0, 0
        try:
            return self.ui_path(name="ui_path_legacy")
        finally:
            window._wechat_find_ready_wait, window._wechat_find_retries = wait, retries

    def ui_path_with_settle(self):
        original_prepare = self.window._prepare_wechat_for_action

        def patched(hwnd):
            was_hidden = original_prepare(hwnd)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if (fg_window() == int(hwnd)
                        and discovery.window_status(int(hwnd)) == "visible"):
                    frame = _capture(int(hwnd))
                    if frame is not None and float(np.asarray(frame[0]).std()) > 6:
                        METRICS["notes"].append({"prepared_ready": True})
                        return was_hidden
                time.sleep(0.1)
            METRICS["notes"].append({"prepared_ready": False})
            return was_hidden

        return self.ui_path(patched_prepare=patched)

    def reconnect_capture(self):
        try:
            self.window.connect_item({"hwnd": self.wechat_item["hwnd"],
                                      "id": self.wechat_item["id"]})
            time.sleep(1.2)
        except Exception as exc:  # noqa: BLE001
            METRICS["notes"].append({"reconnect_error": type(exc).__name__})

    def close(self):
        try:
            self.stop_sampling()
            self.window.close()
        except Exception:  # noqa: BLE001
            pass


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    info = build_info.collect()
    report = {"target_len": len(TARGET), "pid": os.getpid(),
              "app_version": info.get("app_version"),
              "source_digest": info.get("source_digest"),
              "git": info.get("git"),
              "isolated_localappdata": str(ISOLATED),
              "profile_dir": str(settings.data_dir()),
              "profile_isolated": str(settings.data_dir()).lower()
              .startswith(str(ISOLATED).lower()),
              "process_dpi_awareness": process_dpi_awareness(),
              "other_chatassistant_processes": [
                  {"pid": entry["pid"], "exe": entry["exe_name"]}
                  for entry in discovery.enumerate_processes()
                  if entry["exe_name"].lower() == "chatassistantlite.exe"],
              "preflight": None, "runs": [], "dry_mode": DRY}
    harness = None

    def stage(name, detail=None):
        entry = {"stage": name, "at": round(time.monotonic(), 3)}
        if detail:
            entry["detail"] = detail
        report.setdefault("stages", []).append(entry)
        print("STAGE", json.dumps(entry, ensure_ascii=False), flush=True)

    try:
        stage("imports_ok")
        harness = Harness()
        stage("harness_created")
        preflight = harness.bind_wechat()
        report["preflight"] = preflight
        stage("bind_wechat", {"bound": preflight.get("bound"),
                              "status": preflight.get("status")})
        if not preflight.get("bound") or preflight.get("status") != "visible":
            report["aborted"] = "wechat_not_visible"
        elif DRY:
            stage("dry_mode_complete")
        else:
            variants = [
                ("direct_main", harness.direct_main),
                ("ui_path_legacy", harness.ui_path_legacy),
                ("ui_path_fixed", harness.ui_path),
                ("ui_path_without_stop_capture", harness.ui_path_without_stop),
                ("direct_thread", harness.direct_thread),
                ("direct_after_stop_capture", harness.direct_after_stop),
                ("direct_after_stop_settle", harness.direct_after_stop_settle),
                ("ui_path_fixed_with_settle", harness.ui_path_with_settle),
            ]
            selected = [item for item in variants
                        if not _VARIANT_FILTER or item[0] in _VARIANT_FILTER]
            for name, run_variant in selected:
                stage("run_start", {"name": name})
                try:
                    run = run_variant()
                    report["runs"].append(run)
                    stage("run_end", {"name": name, "ok": run.get("ok"),
                                      "reason": run.get("reason")})
                except Exception as exc:  # noqa: BLE001 - one variant must not abort all
                    report["runs"].append({
                        "name": name, "ok": False, "reason": "exception",
                        "error": type(exc).__name__,
                        "trace": traceback.format_exc()[-800:]})
                    stage("run_exception", {"name": name,
                                            "error": type(exc).__name__})
                # Persist after every variant: a later hard crash must not lose
                # already-collected A/B evidence.
                _write_report(report)
                time.sleep(0.8)
                if harness.window.child is None and name not in (
                        "direct_after_stop_capture", "direct_after_stop_settle"):
                    harness.reconnect_capture()
                    time.sleep(0.5)
    except Exception as exc:  # noqa: BLE001 - diagnostics must not leak raw errors
        report["fatal"] = {"type": type(exc).__name__, "text": str(exc)[:300],
                           "trace": traceback.format_exc()[-1500:]}
    finally:
        # Write evidence BEFORE any Qt teardown: a shutdown crash must not lose it.
        _write_report(report)
        if harness is not None:
            try:
                Win32Backend().key_combo((0x1B,))
                Win32Backend().key_combo((0x1B,))
            except Exception:  # noqa: BLE001
                pass
            try:
                harness.close()
            except Exception:  # noqa: BLE001
                pass
    out_path = _write_report(report)
    print(json.dumps({"saved": str(out_path),
                      "preflight": report.get("preflight"),
                      "aborted": report.get("aborted"),
                      "fatal": report.get("fatal"),
                      "runs": [(run["name"], run["ok"], run["reason"])
                               for run in report["runs"]]}, ensure_ascii=False))
    ok = not report.get("fatal") and not report.get("aborted")
    # Bypass Qt/Python teardown: this harness spawned a real Qt app + OCR stacks and
    # has been observed to hard-exit during interpreter shutdown.
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:  # noqa: BLE001
        pass
    os._exit(0 if ok else 2)


if __name__ == "__main__":
    raise SystemExit(main())





