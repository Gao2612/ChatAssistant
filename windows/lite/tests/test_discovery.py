import ctypes
import pickle
import sys
import threading
import types
import unittest
from unittest.mock import patch

from liteapp import discovery
from liteapp import worker
from liteapp.state import normalize_path


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
                discovery, "valid", side_effect=valid_results), patch.object(
                ctypes.windll.user32, "SetProcessDpiAwarenessContext", return_value=False):
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
