import os
import unittest

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
    "chatgpt_model": "",
}
RESULT = {"intent": "x", "need": "y", "action": "z", "caution": "",
          "replies": ["第一条建议", "第二条建议", "第三条建议"]}


class AutoWechatUiTests(unittest.TestCase):
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

    def test_find_requires_target_and_connected_window(self):
        self.window.find_wechat_conversation()
        self.assertIn("联系人名称", self.window.notice.text())
        self.window.settings_page.auto_target.setText("田子夜")
        self.window.window_item = None
        self.window.find_wechat_conversation()
        self.assertIn("连接微信窗口", self.window.notice.text())

    def test_send_button_requires_permission(self):
        self.window.send_reply_to_wechat(0)
        self.assertIn("允许把建议发送到微信", self.window.notice.text())

    def test_send_buttons_follow_analysis_visibility(self):
        self.assertFalse(self.window.send_buttons[0].isEnabled())
        self.window.state.connect("session")
        self.window.state.update("客户", [("her", "", "你好")])
        self.window._cached_analysis = (self.window.state.token, dict(RESULT))
        self.window.restore_analysis()
        self.assertTrue(self.window.send_buttons[0].isEnabled())
        self.window.clear_results()
        self.assertFalse(self.window.send_buttons[0].isEnabled())

    def test_reason_mapping_is_actionable(self):
        class Result:
            reason = "title_mismatch"

        self.assertIn("不一致", self.window._wechat_reason_text(Result(), "失败"))
        Result.reason = "unknown"
        self.assertIn("失败", self.window._wechat_reason_text(Result(), "失败"))


if __name__ == "__main__":
    unittest.main()
