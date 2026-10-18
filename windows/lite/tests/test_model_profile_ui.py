import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.contact_profile import ContactProfile
from liteapp.ui import MainWindow

CONFIG = {
    "wechat_path": "", "base_url": "https://example.test/v1", "backend": "api",
    "model": "test-model", "api_model": "test-model", "api_key": "synthetic",
    "style": "自然、简短", "auto_connect": False, "auto_analysis": False,
    "auto_send_allowed": False, "subscription": None, "subscription_models": [],
    "_warning": "", "reading_backend": "ocr", "reading_api_key": "",
    "reading_base_url": "", "reading_model": "", "codex_path": "",
    "favorite_contacts": [], "floating_ball": False,
    "auto_model_profile": True,
}


class FakeResult:
    score = 68
    chunk_summaries = ("客户在问报价，语气友好。",)
    completed_chunks = 1
    chunk_count = 1
    failed_chunks = 0
    partial = False
    cancelled = False


class ModelProfileUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.history_page.refresh_accounts()
        self.scope = self.window.history_page.create_person("测试客户")

    def tearDown(self):
        floating = getattr(self.window, "floating", None)
        if floating is not None:
            floating.close()
            self.window.floating = None
        self.window.close()
        self.app.processEvents()

    def test_model_analysis_saves_fields_and_marked_profile(self):
        fields = {"relation": "客户/合作方", "topics": ["报价", "交付"],
                  "preferences": ["简短"], "taboos": ["催促"],
                  "reply_style": "简短直接", "important_dates": ["生日：5月20日"],
                  "persona": "关注交付的客户"}

        def fake_affection(config, messages, *, progress=None, cancel=None, **kwargs):
            if progress:
                progress(1, 1, 20)
            return FakeResult()

        with patch.object(self.window, "_scope_messages",
                          return_value=[{"sender": "her", "text": "报价多少"}]):
            with patch("liteapp.model_affection.analyze_model_affection",
                       side_effect=fake_affection):
                with patch("liteapp.model_affection.analyze_model_profile",
                           return_value=fields):
                    with patch.object(self.window, "backend_ready", return_value=True):
                        self.assertTrue(self.window.start_model_profile_analysis(
                            scope=self.scope))
                        deadline = time.monotonic() + 3
                        while self.window._model_busy and time.monotonic() < deadline:
                            self.app.processEvents()
                            time.sleep(0.01)
        self.app.processEvents()
        self.assertFalse(self.window._model_busy)
        profile = self.window._contact_profiles.get("测试客户", self.scope.person_id)
        self.assertIsNotNone(profile)
        self.assertTrue(profile.model_analyzed)
        self.assertEqual(profile.relation, "客户/合作方")
        self.assertIn("报价", profile.topics)
        self.assertEqual(profile.affection_score, 68.0)
        self.assertIn("模型档案", self.window.notice.text())

    def test_import_completed_triggers_model_analysis(self):
        with patch.object(self.window, "start_model_profile_analysis") as start:
            with patch.object(self.window, "backend_ready", return_value=True):
                self.window._history_import_completed(self.scope)
                deadline = time.monotonic() + 1
                while time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
        start.assert_called_once_with(self.scope, automatic=True)

    def test_bind_current_without_selection_opens_named_profile(self):
        self.window.history_page.set_current_scope(None)
        self.window._epoch_title = "田子夜"
        self.window.chat_title.setText("田子夜")
        self.window.window_item = {"hwnd": 4321, "pid": 4322, "created": 1.0,
                                   "path": "C:/Program Files/Tencent/Weixin/Weixin.exe"}
        self.window.bind_current_conversation()
        self.app.processEvents()
        scope = self.window.history_page.current_scope()
        self.assertIsNotNone(scope)
        self.assertEqual(self.window.history_page.tabs.currentIndex(), 1)
        self.assertIn("田子夜", self.window.history_page.contact_label.text())
        self.assertIn("田子夜", self.window.notice.text())
        profile = self.window._contact_profiles.get("田子夜", scope.person_id)
        self.assertIsNotNone(profile)

    def test_ball_shows_model_profile_fields(self):
        profile = ContactProfile(
            title="测试客户", scope_person_id=self.scope.person_id,
            relation="客户/合作方", topics="报价、交付",
            preferences="简短", taboos="催促", important_dates="生日：5月20日",
            model_analyzed=True)
        with patch.object(self.window, "contact_profile_for_current",
                          return_value=profile):
            with patch.object(self.window, "current_contact_title",
                              return_value="测试客户"):
                self.window.enter_ball_mode()
                self.app.processEvents()
                ball = self.window.floating
                ball.expand()
                self.app.processEvents()
                self.assertIn("模型档案", ball.profile_label.text())
                self.assertIn("报价", ball.profile_label.text())
                self.assertTrue(ball.model_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
