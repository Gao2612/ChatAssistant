"""Discovery / window identity tests, including T4 native synthetic fixtures.

Existing 0.2 behaviour is preserved: a window handle carries a picklable
process snapshot and ``valid`` fails closed when the PID/HWND was reused by a
different process instance.  T4 adds ``valid_source`` (contracts.SourceKey),
window classification (login / chat / popup), native popup detection, Z-order
inspection, foreground checks and the worker popup/minimized states.
"""

import ctypes
import pickle
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liteapp import discovery  # noqa: E402
from liteapp import worker  # noqa: E402
from liteapp.contracts import SourceKey  # noqa: E402
from liteapp.state import normalize_path  # noqa: E402
from fixtures import native_chat_fixture as native  # noqa: E402


def _make_dpi_aware():
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


_make_dpi_aware()


def source_for(fixture):
    identity = discovery.process_identity(fixture.pid)
    if not identity:
        raise AssertionError("could not read synthetic window process identity")
    return SourceKey(identity["path"], identity["pid"], identity["created"], int(fixture.hwnd))


def set_zorder(hwnd, insert_after):
    from ctypes import wintypes as W

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, W.UINT]
    user32.SetWindowPos.restype = W.BOOL
    return bool(user32.SetWindowPos(int(hwnd), int(insert_after), 0, 0, 0, 0,
                                    0x0001 | 0x0002 | 0x0010))


class NativeFixtureTestCase(unittest.TestCase):
    def setUp(self):
        self._fixtures = []

    def tearDown(self):
        for fixture in reversed(self._fixtures):
            try:
                fixture.close()
            except Exception:  # noqa: BLE001
                pass

    def spawn(self, **kwargs):
        fixture = native.NativeChatFixture(**kwargs)
        try:
            fixture.start()
        except (TimeoutError, RuntimeError, OSError) as exc:
            raise unittest.SkipTest(f"interactive Win32 window unavailable: {exc}") from None
        self._fixtures.append(fixture)
        return fixture


class WindowIdentityTests(unittest.TestCase):
    def test_window_handle_carries_picklable_process_snapshot(self):
        target = discovery.WindowTarget(
            123, 456, r"C:\Program Files\Tencent\Weixin.exe", 987654321,
        )
        restored = pickle.loads(pickle.dumps(target))
        self.assertIsInstance(restored, int)
        self.assertEqual(int(restored), 123)
        self.assertEqual(restored.pid, 456)
        self.assertEqual(restored.path, normalize_path(r"c:/program files/tencent/weixin.EXE"))
        self.assertEqual(restored.created, 987654321)

    def test_valid_rejects_reused_hwnd_for_another_process_instance(self):
        target = discovery.WindowTarget(123, 456, r"C:\Weixin.exe", 987654321)
        replacements = [
            {"pid": 999, "path": normalize_path(r"C:\Weixin.exe"), "created": 987654321},
            {"pid": 456, "path": normalize_path(r"C:\Other.exe"), "created": 987654321},
            {"pid": 456, "path": normalize_path(r"C:\Weixin.exe"), "created": 987654322},
            None,
        ]
        with patch.object(discovery.u32, "IsWindow", return_value=True), patch.object(
                discovery, "window_identity", side_effect=replacements):
            for replacement in replacements:
                with self.subTest(replacement=replacement):
                    self.assertFalse(discovery.valid(target))

    def test_valid_accepts_matching_process_snapshot(self):
        target = discovery.WindowTarget(123, 456, r"C:\Weixin.exe", 987654321)
        current = {"pid": 456, "path": normalize_path(r"c:/weixin.exe"), "created": 987654321}
        with patch.object(discovery.u32, "IsWindow", return_value=True), patch.object(
                discovery, "window_identity", return_value=current):
            self.assertTrue(discovery.valid(target))

    def test_process_identity_closes_handle_and_fails_closed_on_query_error(self):
        handle = 12345

        def query_path(_handle, _flags, buffer, _size):
            buffer.value = r"C:\Weixin.exe"
            return True

        def query_times(_handle, created, exited, kernel, user):
            created_time = ctypes.cast(created, ctypes.POINTER(discovery.W.FILETIME)).contents
            created_time.dwLowDateTime = 321
            created_time.dwHighDateTime = 7
            return True

        with patch.object(discovery.k32, "OpenProcess", return_value=handle), \
                patch.object(discovery.k32, "QueryFullProcessImageNameW", side_effect=query_path), \
                patch.object(discovery.k32, "GetProcessTimes", side_effect=query_times), \
                patch.object(discovery.k32, "CloseHandle", return_value=True) as close:
            self.assertEqual(discovery.process_identity(456), {
                "pid": 456, "path": normalize_path(r"C:\Weixin.exe"), "created": (7 << 32) | 321,
            })
            close.assert_called_once_with(handle)

        with patch.object(discovery.k32, "OpenProcess", return_value=handle), \
                patch.object(discovery.k32, "QueryFullProcessImageNameW", side_effect=query_path), \
                patch.object(discovery.k32, "GetProcessTimes", side_effect=query_times), \
                patch.object(discovery.k32, "CloseHandle", return_value=False) as close:
            self.assertIsNone(discovery.process_identity(456))
            close.assert_called_once_with(handle)

        with patch.object(discovery.k32, "OpenProcess", return_value=handle), \
                patch.object(discovery.k32, "QueryFullProcessImageNameW", return_value=False), \
                patch.object(discovery.k32, "CloseHandle") as close:
            self.assertIsNone(discovery.process_identity(456))
            close.assert_called_once_with(handle)

        with patch.object(discovery.k32, "OpenProcess", return_value=handle), \
                patch.object(discovery.k32, "QueryFullProcessImageNameW", side_effect=query_path), \
                patch.object(discovery.k32, "GetProcessTimes", return_value=False), \
                patch.object(discovery.k32, "CloseHandle") as close:
            self.assertIsNone(discovery.process_identity(456))
            close.assert_called_once_with(handle)


class SourceValidationTests(unittest.TestCase):
    def test_valid_source_accepts_matching_process_instance(self):
        source = SourceKey(r"C:\Weixin.exe", 456, 987654321, 123)
        identity = {"pid": 456, "path": normalize_path(r"c:/weixin.exe"), "created": 987654321}
        with patch.object(discovery.u32, "IsWindow", return_value=True), \
                patch.object(discovery, "window_identity", return_value=identity):
            self.assertTrue(discovery.valid_source(source))

    def test_valid_source_rejects_reused_pid_or_hwnd(self):
        source = SourceKey(r"C:\Weixin.exe", 456, 987654321, 123)
        replacements = [
            {"pid": 999, "path": normalize_path(r"C:\Weixin.exe"), "created": 987654321},
            {"pid": 456, "path": normalize_path(r"C:\Other.exe"), "created": 987654321},
            {"pid": 456, "path": normalize_path(r"C:\Weixin.exe"), "created": 987654322},
            {"pid": 456, "path": normalize_path(r"C:\Weixin.exe"), "created": 987654320},
            None,
        ]
        with patch.object(discovery.u32, "IsWindow", return_value=True), patch.object(
                discovery, "window_identity", side_effect=replacements):
            for replacement in replacements:
                with self.subTest(replacement=replacement):
                    self.assertFalse(discovery.valid_source(source))

    def test_valid_source_rejects_wrong_type_and_closed_window(self):
        self.assertFalse(discovery.valid_source(123))
        self.assertFalse(discovery.valid_source(None))
        source = SourceKey(r"C:\Weixin.exe", 456, 987654321, 123)
        with patch.object(discovery.u32, "IsWindow", return_value=False):
            self.assertFalse(discovery.valid_source(source))


class WindowClassificationTests(unittest.TestCase):
    def test_login_window_is_never_treated_as_chat(self):
        self.assertEqual(discovery.classify_window("微信", "WeChatLoginWndForPC", (100, 100, 500, 600)),
                         "login")
        self.assertEqual(discovery.classify_window("微信登录", "Qt51514QWindowIcon", (0, 0, 400, 500)),
                         "login")

    def test_popup_windows_are_classified_as_popup(self):
        self.assertEqual(discovery.classify_window("图片", "WeChatImageViewer", (0, 0, 400, 500), 10),
                         "popup")
        self.assertEqual(discovery.classify_window("对话框", "#32770", (0, 0, 400, 500)),
                         "popup")
        self.assertEqual(discovery.classify_window("聊天", "WeChatMainWndForPC", (0, 0, 400, 500),
                                                   owner=77), "popup")

    def test_main_window_is_chat_and_unknown_is_conservative(self):
        self.assertEqual(discovery.classify_window("微信", "WeChatMainWndForPC", (0, 0, 900, 700)),
                         "chat")
        self.assertEqual(discovery.classify_window("微信", "Qt51514QWindowIcon", (0, 0, 900, 700)),
                         "chat")
        self.assertEqual(discovery.classify_window("计算器", "CalcFrame", (0, 0, 400, 500)),
                         "unknown")


class ForegroundTests(unittest.TestCase):
    def test_foreground_window_reports_handle(self):
        with patch.object(discovery.u32, "GetForegroundWindow", return_value=4242):
            self.assertEqual(discovery.foreground_window(), 4242)

    def test_foreground_is_foreign_detects_outside_windows(self):
        with patch.object(discovery.u32, "GetForegroundWindow", return_value=10):
            self.assertFalse(discovery.foreground_is_foreign((10, 20)))
        with patch.object(discovery.u32, "GetForegroundWindow", return_value=30):
            self.assertTrue(discovery.foreground_is_foreign((10, 20)))
        with patch.object(discovery.u32, "GetForegroundWindow", return_value=0):
            self.assertTrue(discovery.foreground_is_foreign((10, 20)))


class DiscoveryNativeTests(NativeFixtureTestCase):
    def test_discover_reports_kind_and_dpi_for_synthetic_chat_window(self):
        chat = self.spawn(mode="chat", title="微信", rect=(220, 220, 640, 520))
        identity = discovery.process_identity(chat.pid)
        items = discovery.discover(identity["path"])
        matches = [item for item in items if int(item["hwnd"]) == int(chat.hwnd)]
        self.assertEqual(len(matches), 1, "synthetic WeChat-like window must be discovered")
        item = matches[0]
        self.assertEqual(item["kind"], "chat")
        self.assertGreaterEqual(item["dpi"], 96)
        self.assertEqual(item["pid"], chat.pid)
        chat.command("popup")
        popup_hwnd = int(chat.state()["popup"])
        items = discovery.discover(identity["path"])
        self.assertFalse(any(int(item["hwnd"]) == popup_hwnd for item in items),
                         "an owned popup must not be listed as a main/login window")

    def test_discover_reports_login_kind_for_login_window(self):
        login = self.spawn(mode="login", rect=(200, 200, 420, 560))
        identity = discovery.process_identity(login.pid)
        items = discovery.discover(identity["path"])
        matches = [item for item in items if int(item["hwnd"]) == int(login.hwnd)]
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["kind"], "login")

    def test_marker_window_with_non_wechat_title_is_not_discovered(self):
        marker = self.spawn(mode="marker", color="#FF00FF", rect=(300, 300, 300, 300))
        identity = discovery.process_identity(marker.pid)
        items = discovery.discover(identity["path"])
        self.assertFalse(any(int(item["hwnd"]) == int(marker.hwnd) for item in items))

    def test_source_kind_matches_classification(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        login = self.spawn(mode="login", rect=(200, 200, 420, 560))
        self.assertEqual(discovery.source_kind(source_for(chat)), "chat")
        self.assertEqual(discovery.source_kind(source_for(login)), "login")

    def test_target_for_source_round_trips_without_precision_loss(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        source = source_for(chat)
        target = discovery.target_for_source(source)
        self.assertEqual(int(target), int(chat.hwnd))
        self.assertIsInstance(target, discovery.WindowTarget)
        self.assertTrue(discovery.valid(target),
                        "FILETIME ticks must survive SourceKey -> WindowTarget")
        self.assertEqual(SourceKey.from_window_target(target), source)


class PopupDetectionNativeTests(NativeFixtureTestCase):
    def test_popup_covering_detects_and_stops_covering(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        self.assertIsNone(discovery.popup_covering(int(chat.hwnd)))
        chat.command("popup")
        popup_hwnd = int(chat.state()["popup"])
        self.assertEqual(discovery.popup_covering(int(chat.hwnd)), popup_hwnd)
        windows = discovery.popup_windows(int(chat.hwnd))
        self.assertIn(popup_hwnd, [int(item["hwnd"]) for item in windows])
        chat.command("close_popup")
        self.assertIsNone(discovery.popup_covering(int(chat.hwnd)))

    def test_window_of_another_process_is_not_a_popup(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        marker = self.spawn(mode="marker", color="#FF00FF", rect=(300, 300, 400, 320))
        marker.command("set_rect 260 260 500 400")
        self.assertIsNone(discovery.popup_covering(int(chat.hwnd)),
                          "another application's window must not count as a WeChat popup")
        self.assertNotIn(int(marker.hwnd),
                         [int(item["hwnd"]) for item in discovery.popup_windows(int(chat.hwnd))])


class ZorderNativeTests(NativeFixtureTestCase):
    def test_zorder_ok_tracks_inserted_third_window(self):
        chat = self.spawn(mode="chat", rect=(200, 200, 620, 500))
        host = self.spawn(mode="marker", color="#336699", rect=(100, 100, 900, 700))
        third = self.spawn(mode="marker", color="#FF00FF", rect=(300, 300, 300, 300))
        self.assertTrue(set_zorder(chat.hwnd, host.hwnd))
        self.assertTrue(discovery.zorder_ok(int(chat.hwnd), int(host.hwnd)))
        self.assertTrue(set_zorder(third.hwnd, host.hwnd))
        self.assertFalse(discovery.zorder_ok(int(chat.hwnd), int(host.hwnd)))
        self.assertTrue(set_zorder(chat.hwnd, host.hwnd))
        self.assertTrue(discovery.zorder_ok(int(chat.hwnd), int(host.hwnd)))


# ---------------------------------------------------------------------------
# Worker: identity / popup / minimized states (existing behaviour preserved)
# ---------------------------------------------------------------------------

class _WorkerHarness:
    def __init__(self, *, valid_results=None, popup=111, minimized=False):
        self.packets = []
        self.events = []
        self.capture_ref = {}
        self.valid_results = list(valid_results or [])
        self.popup = popup
        self.minimized = minimized
        packets, events, capture_ref = self.packets, self.events, self.capture_ref
        harness = self

        class Sink:
            def put_nowait(self, packet):
                packets.append(packet)

        class Stop:
            def __init__(self):
                self.calls = 0

            def wait(self, _timeout):
                self.calls += 1
                return self.calls > 1

        class FrameClock:
            def __init__(self):
                self.values = [0, 0]
                self.lock = threading.Lock()

            def get_lock(self):
                return self.lock

            def __getitem__(self, index):
                return self.values[index]

            def __setitem__(self, index, value):
                self.values[index] = value

        class Capture:
            def __init__(self, **_kwargs):
                self.callbacks = {}

            def event(self, callback):
                self.callbacks[callback.__name__] = callback
                return callback

            def start_free_threaded(self):
                capture_ref["capture"] = self
                return types.SimpleNamespace(stop=lambda: events.append("stop"))

        class Numpy:
            @staticmethod
            def ascontiguousarray(value):
                return value

            @staticmethod
            def array_equal(left, right):
                return left is right

        modules = {
            "numpy": Numpy,
            "windows_capture": types.ModuleType("windows_capture"),
            "vendor.area": types.ModuleType("vendor.area"),
            "vendor.ocr": types.ModuleType("vendor.ocr"),
        }
        modules["windows_capture"].WindowsCapture = Capture
        modules["vendor.area"].chat_area = lambda *_args: None
        modules["vendor.ocr"].Reader = lambda: None
        modules["vendor.ocr"].read_title = lambda *_args: ""
        self.modules = modules
        self.frame_clock = FrameClock()
        self.sink = Sink()
        self.stop = Stop()
        self.harness = harness

    def run(self, target, discovery_patches):
        with patch.dict(sys.modules, self.modules), \
                patch.object(discovery, "valid", side_effect=self.valid_results), \
                patch.object(discovery, "minimized", return_value=self.minimized), \
                patch.object(discovery, "popup_covering", return_value=self.popup), \
                patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext",
                             return_value=False):
            worker.run(self.sink, self.stop, target, "session", self.frame_clock)


class WorkerStateTests(unittest.TestCase):
    def test_worker_marks_popup_unavailable_and_never_emits_snapshot(self):
        harness = _WorkerHarness(valid_results=[True] * 10, popup=111)
        target = discovery.WindowTarget(123, 456, r"C:\Weixin.exe", 987654321)
        harness.run(target, ())
        kinds = [packet["kind"] for packet in harness.packets]
        self.assertIn("unavailable", kinds)
        unavailable = next(packet for packet in harness.packets
                           if packet["kind"] == "unavailable")
        self.assertEqual(unavailable["state"], "popup")
        self.assertEqual(unavailable["popup_hwnd"], 111)
        self.assertNotIn("snapshot", kinds)
        self.assertNotIn("frame", kinds)

    def test_worker_reports_minimized_state_without_ocr(self):
        harness = _WorkerHarness(valid_results=[True] * 10, popup=None, minimized=True)
        target = discovery.WindowTarget(123, 456, r"C:\Weixin.exe", 987654321)
        harness.run(target, ())
        kinds = [packet["kind"] for packet in harness.packets]
        self.assertIn("unavailable", kinds)
        unavailable = next(packet for packet in harness.packets
                           if packet["kind"] == "unavailable")
        self.assertEqual(unavailable["state"], "minimized")
        self.assertNotIn("snapshot", kinds)

    def test_worker_rechecks_identity_and_stops_capture_after_reuse(self):
        target = discovery.WindowTarget(123, 456, r"C:\Weixin.exe", 987654321)
        packets = []
        events = []
        capture_ref = {}
        clock_during_stop = []

        class FrameData:
            shape = (1, 1, 3)

            def __getitem__(self, _key):
                return self

            def any(self):
                return True

        frame_data = FrameData()
        frame = types.SimpleNamespace(frame_buffer=frame_data)

        class Numpy:
            @staticmethod
            def ascontiguousarray(value):
                return value

            @staticmethod
            def array_equal(left, right):
                return left is right

        class FrameClock:
            def __init__(self):
                self.values = [0, 0]
                self.lock = threading.Lock()

            def get_lock(self):
                return self.lock

            def __getitem__(self, index):
                return self.values[index]

            def __setitem__(self, index, value):
                self.values[index] = value

        frame_clock = FrameClock()

        class Sink:
            def put_nowait(self, packet):
                packets.append(packet)
                if packet["kind"] == "closed":
                    events.append("closed")
                    capture_ref["capture"].callbacks["on_frame_arrived"](frame, None)

        class Stop:
            def wait(self, _timeout):
                return False

        class Capture:
            def __init__(self, **_kwargs):
                self.callbacks = {}

            def event(self, callback):
                self.callbacks[callback.__name__] = callback
                return callback

            def start_free_threaded(self):
                capture_ref["capture"] = self
                self.callbacks["on_frame_arrived"](frame, None)

                def stop():
                    events.append("stop")
                    before = frame_clock.values[:]
                    self.callbacks["on_frame_arrived"](frame, None)
                    clock_during_stop.append((before, frame_clock.values[:]))

                return types.SimpleNamespace(stop=stop)

        modules = {
            "numpy": Numpy,
            "windows_capture": types.ModuleType("windows_capture"),
            "vendor.area": types.ModuleType("vendor.area"),
            "vendor.ocr": types.ModuleType("vendor.ocr"),
        }
        modules["windows_capture"].WindowsCapture = Capture
        modules["vendor.area"].chat_area = lambda *_args: None
        modules["vendor.ocr"].Reader = lambda: None
        modules["vendor.ocr"].read_title = lambda *_args: ""
        valid_results = [True, True, False]
        with patch.dict(sys.modules, modules), patch.object(
                discovery, "valid", side_effect=valid_results), \
                patch.object(discovery, "popup_covering", return_value=None), \
                patch.object(ctypes.windll.user32, "SetProcessDpiAwarenessContext",
                             return_value=False):
            worker.run(Sink(), Stop(), target, "session", frame_clock)

        self.assertEqual([packet["kind"] for packet in packets], ["status", "closed"])
        self.assertIn("身份已变化", packets[-1]["message"])
        self.assertEqual(events, ["stop", "closed"])
        self.assertEqual(clock_during_stop[0][1], clock_during_stop[0][0])
        self.assertEqual(frame_clock.values, [0, 0])

    def test_worker_rejects_unverified_handle_before_loading_capture(self):
        packets = []

        class Sink:
            def put_nowait(self, packet):
                packets.append(packet)

        class Stop:
            def wait(self, _timeout):
                return False

        with patch.object(discovery, "valid", return_value=False):
            worker.run(Sink(), Stop(), 123, "session", None)
        self.assertEqual([packet["kind"] for packet in packets], ["closed"])
        self.assertIn("重新扫描", packets[0]["message"])


if __name__ == "__main__":
    unittest.main()
