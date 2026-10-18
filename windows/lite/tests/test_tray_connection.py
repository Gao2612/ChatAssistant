import os
import unittest
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
}


def window_item(hwnd=123, status="visible"):
    return {"hwnd": hwnd, "id": hwnd, "pid": 1, "created": 1, "title": "微信",
            "path": CONFIG["wechat_path"], "status": status, "visible": status == "visible",
            "minimized": False, "tool_window": False, "client_rect": (0, 0, 100, 100),
            "frame_rect": (0, 0, 100, 100)}


class TrayConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.window_item = window_item()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_hidden_window_keeps_capture_alive(self):
        hidden = window_item(status="hidden")
        with patch("liteapp.ui.discover", return_value=[]), \
                patch("liteapp.ui.find_wechat_windows", return_value=[hidden]), \
                patch("liteapp.ui.wechat_presence", return_value={"running": True}), \
                patch.object(self.window, "stop_capture") as stop_capture:
            self.window.scan()
        stop_capture.assert_not_called()
        self.assertIsNotNone(self.window.window_item)
        self.assertIn("隐藏到托盘", self.window.connection_status.text())

    def test_gone_window_disconnects(self):
        with patch("liteapp.ui.discover", return_value=[]), \
                patch("liteapp.ui.find_wechat_windows", return_value=[]), \
                patch("liteapp.ui.wechat_presence", return_value={"running": True}), \
                patch.object(self.window, "stop_capture") as stop_capture:
            self.window.scan()
        stop_capture.assert_called_once()
        self.assertIsNone(self.window.window_item)


if __name__ == "__main__":
    unittest.main()
