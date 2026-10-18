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

    def test_ball_mode_hides_main_and_keeps_settings_history_only(self):
        self.window.enter_ball_mode()
        self.app.processEvents()
        floating = self.window.floating
        self.assertIsNotNone(floating)
        self.assertTrue(floating.isVisible())
        self.assertFalse(floating._expanded)  # starts as a ball
        self.assertFalse(self.window.isVisible())
        # When the main window is reopened it is settings/history, not chat.
        self.assertTrue(self.window.nav_chat.isHidden())
        self.assertTrue(self.window.ai_toggle.isHidden())
        self.assertIs(self.window.page_stack.currentWidget(),
                      self.window.settings_page)

    def test_expand_shows_suggestions_and_analyze_calls_host(self):
        self.window.enter_ball_mode()
        self.app.processEvents()
        floating = self.window.floating
        floating.expand()
        self.app.processEvents()
        self.assertTrue(floating._expanded)
        self.assertTrue(floating.analyze_button.isVisible())
        with patch.object(self.window, "start_analysis") as analyze:
            floating.analyze_button.setEnabled(True)
            floating.analyze_button.click()
        analyze.assert_called_once()
        # three suggestion rows exist and copy/send are disabled without results
        self.assertEqual(len(floating.reply_labels), 3)
        self.assertFalse(floating.copy_buttons[0].isEnabled())

    def test_main_settings_keeps_ball_visible_collapsed(self):
        self.window.enter_ball_mode()
        self.app.processEvents()
        floating = self.window.floating
        floating.show_main_settings()
        self.app.processEvents()
        self.assertTrue(floating.isVisible())
        self.assertFalse(floating._expanded)
        self.assertTrue(self.window.isVisible())
        self.assertIs(self.window.page_stack.currentWidget(),
                      self.window.settings_page)

    def test_floating_header_button_exists(self):
        self.assertEqual(self.window.floating_button.text(), "悬浮球")
        self.assertFalse(self.window.floating_button.isHidden())

    def test_main_close_in_ball_mode_returns_to_ball(self):
        self.window.enter_ball_mode()
        self.app.processEvents()
        self.window.show()
        self.app.processEvents()
        self.window.close()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.floating.isVisible())


if __name__ == "__main__":
    unittest.main()
