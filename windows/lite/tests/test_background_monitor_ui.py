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
    "auto_send_allowed": False, "background_monitor": False,
    "subscription": None, "subscription_models": [], "_warning": "",
    "reading_backend": "ocr", "reading_api_key": "", "reading_base_url": "",
    "reading_model": "", "codex_path": "", "chatgpt_model": "",
}


class BackgroundToggleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_toggle_defaults_off_and_is_saved_by_commit(self):
        page = self.window.settings_page
        self.assertFalse(page.background_monitor.isChecked())
        page.background_monitor.setChecked(True)
        emitted = []
        page.saved.connect(emitted.append)
        with patch("liteapp.ui.settings.save"):
            page.commit()
        self.assertTrue(emitted)
        self.assertTrue(emitted[0]["background_monitor"])

    def test_disabled_scan_does_not_create_monitor(self):
        with patch("liteapp.ui.discover", return_value=[]):
            with patch("liteapp.ui.wechat_presence", return_value={"running": False}):
                self.window.scan()
        self.assertIsNone(self.window._bg_monitor)
        self.assertFalse(self.window.bg_timer.isActive())


if __name__ == "__main__":
    unittest.main()
