import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.affection import estimate_affection
from liteapp.ui import MainWindow

CONFIG = {
    "wechat_path": "", "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "synthetic",
    "style": "自然、简短", "auto_connect": False, "auto_analysis": True,
    "auto_send_allowed": False, "subscription": None, "subscription_models": [],
    "_warning": "", "reading_backend": "ocr", "reading_api_key": "",
    "reading_base_url": "", "reading_model": "", "codex_path": "",
    "favorite_contacts": [], "floating_ball": False,
}


class FavoriteAndAffectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.state.title = "田子夜"
        self.window.chat_title.setText("田子夜")

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_toggle_favorite_matches_normalized_title(self):
        self.assertFalse(self.window.is_favorite_contact("田子夜"))
        self.assertTrue(self.window.toggle_favorite_contact())
        self.assertTrue(self.window.is_favorite_contact("  田 子夜 "))
        self.assertIn("田子夜", self.window.config["favorite_contacts"])
        self.assertFalse(self.window.toggle_favorite_contact())
        self.assertFalse(self.window.is_favorite_contact("田子夜"))

    def test_affection_for_current_uses_recent_messages(self):
        self.window._recent_messages = [
            ("her", "田子夜", "你今天辛苦啦，注意休息，别太累"),
            ("me", "我", "好的"),
            ("her", "田子夜", "周末一起吃饭吧，我请你，想见你"),
            ("her", "田子夜", "记得带伞，我担心你"),
        ]
        profile = self.window.affection_for_current()
        self.assertIsNotNone(profile)
        self.assertGreater(profile.score, 30)
        self.assertEqual(profile.dimensions[0].label, "主动延续")
        # cache: same messages return the same object
        self.assertIs(self.window.affection_for_current(), profile)

    def test_affection_none_without_messages(self):
        self.window._recent_messages = []
        self.assertIsNone(self.window.affection_for_current())

    def test_ball_shows_affection_and_fixed_state(self):
        self.window.toggle_favorite_contact()
        self.window._recent_messages = [
            ("her", "田子夜", "谢谢你，今天和你聊天很开心，晚安"),
            ("her", "田子夜", "明天一起吃饭吗，我请你"),
            ("her", "田子夜", "注意休息，别熬夜"),
        ]
        self.window.enter_ball_mode()
        self.app.processEvents()
        ball = self.window.floating
        ball.expand()
        self.app.processEvents()
        self.assertIn("好感度", ball.affection_label.text())
        self.assertNotEqual(ball.affection_label.text(), "好感度 —")
        self.assertEqual(ball.favorite_button.text(), "已固定")
        self.assertIn(ball.profile_badge.text(), ("未建立", "基础档案", "模型档案"))
        self.assertTrue(ball.profile_label.text())


if __name__ == "__main__":
    unittest.main()
