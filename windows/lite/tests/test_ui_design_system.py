import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSizePolicy

from liteapp.ui import MainWindow

CONFIG = {
    "wechat_path": "", "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "synthetic",
    "style": "自然、简短", "auto_connect": False, "auto_analysis": False,
    "auto_send_allowed": False, "subscription": None, "subscription_models": [],
    "_warning": "", "reading_backend": "ocr", "reading_api_key": "",
    "reading_base_url": "", "reading_model": "", "codex_path": "",
}


class DesignSystemTests(unittest.TestCase):
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

    def test_nav_buttons_have_accessible_names(self):
        self.assertEqual(self.window.nav_chat.accessibleName(), "聊天")
        self.assertEqual(self.window.nav_history.accessibleName(), "历史记录")
        self.assertEqual(self.window.nav_settings.accessibleName(), "设置")
        self.assertEqual(self.window.ai_toggle.accessibleName(), "AI 建议面板")

    def test_page_stack_can_shrink_on_small_screens(self):
        policy = self.window.page_stack.sizePolicy()
        self.assertEqual(policy.horizontalPolicy(), QSizePolicy.Policy.Ignored)
        self.assertEqual(policy.verticalPolicy(), QSizePolicy.Policy.Ignored)
        self.assertEqual(self.window.minimumWidth(), 980)

    def test_chat_empty_state_and_preview_target(self):
        self.assertIn("当前聊天画面", self.window.preview.text())
        self.assertEqual(self.window.preview.minimumHeight(), 200)
        self.assertEqual(self.window.editor.maximumHeight(), 90)


if __name__ == "__main__":
    unittest.main()
