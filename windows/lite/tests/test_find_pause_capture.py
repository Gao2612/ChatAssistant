import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.ui import MainWindow

CONFIG = {
    "wechat_path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "synthetic",
    "style": "自然、简短", "auto_connect": False, "auto_analysis": False,
    "auto_send_allowed": False, "subscription": None, "subscription_models": [],
    "_warning": "", "reading_backend": "ocr", "reading_api_key": "",
    "reading_base_url": "", "reading_model": "", "codex_path": "",
    "chatgpt_model": "",
}


def item():
    return {"hwnd": 777, "id": 777, "pid": 1, "created": 1, "title": "微信",
            "path": CONFIG["wechat_path"], "status": "visible", "visible": True,
            "minimized": False, "tool_window": False, "client_rect": (0, 0, 100, 100),
            "frame_rect": (0, 0, 100, 100)}


class FakeThread:
    def __init__(self, target, daemon=False):
        self.target = target

    def start(self):
        self.target()


class FindPausesCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.window_item = item()
        self.window.settings_page.auto_target.setText("田子夜")

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_find_pauses_and_resumes_capture(self):
        calls = []
        with patch.object(self.window, "stop_capture",
                          side_effect=lambda: calls.append("stop")), \
                patch.object(self.window, "connect_item",
                             side_effect=lambda target: calls.append(("connect", target))), \
                patch.object(self.window, "_prepare_wechat_for_action",
                             return_value=False), \
                patch.object(self.window, "_finish_wechat_action",
                             return_value=None), \
                patch("liteapp.wechat_find.find_conversation",
                      return_value=SimpleNamespace(ok=True, reason="ok", steps=())), \
                patch("liteapp.ui.threading.Thread", FakeThread):
            self.window.find_wechat_conversation()
        self.assertEqual(calls[0], "stop")
        self.assertEqual(calls[-1][0], "connect")
        self.assertEqual(calls[-1][1]["hwnd"], 777)
        self.assertIsNone(self.window._resume_item_after_action)

    def test_failed_find_still_resumes_capture(self):
        calls = []
        with patch.object(self.window, "stop_capture",
                          side_effect=lambda: calls.append("stop")), \
                patch.object(self.window, "connect_item",
                             side_effect=lambda target: calls.append(("connect", target))), \
                patch.object(self.window, "_prepare_wechat_for_action",
                             return_value=False), \
                patch.object(self.window, "_finish_wechat_action",
                             return_value=None), \
                patch("liteapp.wechat_find.find_conversation",
                      side_effect=RuntimeError("boom")), \
                patch("liteapp.ui.threading.Thread", FakeThread):
            self.window.find_wechat_conversation()
        self.assertEqual(calls[0], "stop")
        self.assertEqual(calls[-1][0], "connect")


if __name__ == "__main__":
    unittest.main()
