import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.ui import MainWindow

CONFIG = {
    "wechat_path": "", "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "synthetic",
    "style": "自然、简短", "auto_connect": False, "auto_analysis": False,
    "auto_send_allowed": False, "subscription": None, "subscription_models": [],
    "_warning": "", "reading_backend": "ocr", "reading_api_key": "",
    "reading_base_url": "", "reading_model": "", "codex_path": "",
}


class FloatingAssistantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        floating = getattr(self.window, "floating", None)
        if floating is not None:
            floating.close()
            self.window.floating = None
        self.window.close()
        self.app.processEvents()

    def test_toggle_hides_main_and_shows_floating(self):
        self.window.toggle_floating()
        self.app.processEvents()
        floating = self.window.floating
        self.assertIsNotNone(floating)
        self.assertTrue(floating.isVisible())
        self.assertFalse(self.window.isVisible())

    def test_analyze_button_calls_host_and_returns_to_main(self):
        self.window.toggle_floating()
        self.app.processEvents()
        floating = self.window.floating
        with patch.object(self.window, "start_analysis") as analyze:
            floating.analyze_button.setEnabled(True)
            floating.analyze_button.click()
        analyze.assert_called_once()
        floating.show_main()
        self.app.processEvents()
        self.assertFalse(floating.isVisible())
        self.assertTrue(self.window.isVisible())

    def test_floating_header_button_exists(self):
        self.assertEqual(self.window.floating_button.text(), "悬浮窗")
        self.assertFalse(self.window.floating_button.isHidden())


if __name__ == "__main__":
    unittest.main()
