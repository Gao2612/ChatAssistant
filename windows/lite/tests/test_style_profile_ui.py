import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.contracts import ScopeKey
from liteapp.ui import MainWindow

CONFIG = {
    "wechat_path": "", "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "synthetic",
    "style": "自然、简短", "auto_connect": False, "auto_analysis": False,
    "subscription": None, "subscription_models": [], "_warning": "",
    "reading_backend": "ocr", "reading_api_key": "", "reading_base_url": "",
    "reading_model": "", "codex_path": "", "chatgpt_model": "",
}


def fake_profile():
    return SimpleNamespace(
        scope=ScopeKey("acct", "person"),
        prompt_style="你通常用短句、先回应再补充，礼貌结尾，避免夸张承诺。" * 6,
        display_report="[事实] 平均句长 12 字；[推断] 语气偏自然；[不确定] 样本有限。" * 12,
        sample_count=42, message_count=30, fragment_count=12,
        sources_with_me=2,
    )


class StyleProfileUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.close_capture = lambda *a, **k: None

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_generate_requires_selected_contact(self):
        self.window.history_page.set_current_scope(None)
        self.window.generate_style_profile()
        self.assertIn("历史页", self.window.notice.text())

    def test_generate_then_apply_prompt_style(self):
        scope = self.window.history_page.store.create_person("测试联系人", "default")
        self.window.history_page.set_current_scope(scope)
        profile = fake_profile()
        self.window._show_style_profile_dialog = lambda _profile: None
        with patch("liteapp.style_profile.StyleProfileBuilder") as builder_cls:
            builder_cls.return_value.save.return_value = profile
            with patch("liteapp.ui.settings.save") as save:
                self.window.generate_style_profile()
                deadline = time.monotonic() + 3
                while self.window.style_busy and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.app.processEvents()
                self.assertIs(self.window._style_profile, profile)
                self.window.apply_style_profile(profile)
        self.assertEqual(self.window.config["style"], profile.prompt_style)
        self.assertEqual(self.window.settings_page.style.currentText(),
                         profile.prompt_style)
        self.assertIn("已选用", self.window.notice.text())
        save.assert_called_once()


if __name__ == "__main__":
    unittest.main()
