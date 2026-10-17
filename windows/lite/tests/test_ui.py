import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtGui import QPixmap

from liteapp.ui import MainWindow, SettingsDialog


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config={
            "wechat_path": "", "base_url": "https://example.test/v1",
            "backend": "api", "model": "api-model", "api_model": "api-model",
            "api_key": "test-key", "style": "自然简短", "auto_connect": False,
            "subscription": None, "subscription_models": [], "_warning": "",
        }, scanning=False)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_chat_settings_navigation_and_ai_panel_follow_product_layout(self):
        self.assertEqual(self.window.page_stack.currentWidget(), self.window.chat_page)
        self.assertTrue(self.window.ai_panel.isHidden())
        self.assertTrue(self.window.settings_page.isAncestorOf(self.window.windows))
        self.assertTrue(self.window.settings_page.isAncestorOf(self.window.connect_button))
        self.assertTrue(self.window.settings_page.isAncestorOf(self.window.chatgpt_button))
        self.assertGreaterEqual(self.window.page_stack.indexOf(self.window.history_page), 0)
        labels = [button.text() for button in self.window.findChildren(QPushButton)]
        self.assertNotIn("分析当前对话", labels)
        self.assertNotIn("暂停并校正", labels)
        self.assertNotIn("手动粘贴对话", labels)

        self.window.show_settings()
        self.assertEqual(self.window.page_stack.currentWidget(), self.window.settings_page)
        self.assertTrue(self.window.ai_toggle.isHidden())
        self.window.show_chat()
        self.assertFalse(self.window.ai_toggle.isHidden())
        with patch.object(self.window.history_page, "refresh_people"):
            self.window.nav_history.click()
        self.assertEqual(self.window.page_stack.currentWidget(), self.window.history_page)
        self.assertTrue(self.window.ai_toggle.isHidden())
        self.window.show_chat()
        self.window.ai_toggle.click()
        self.assertEqual(self.window.page_stack.currentWidget(), self.window.chat_page)
        self.assertFalse(self.window.ai_panel.isHidden())
        self.assertFalse(self.window.chat_page.isAncestorOf(self.window.ai_panel))
        self.assertGreaterEqual(self.window.width(), 1180 + self.window.ai_panel.width())

    def test_auto_analysis_defaults_on_and_waits_for_ready_model(self):
        self.assertTrue(self.window.auto_analysis.isChecked())
        self.window.config["api_key"] = ""
        self.window.state.connect("window-1")
        self.window.state.update("Alice", [("her", "", "你好")])
        self.window.changed_at = 0

        class FakeCapture:
            @staticmethod
            def is_alive():
                return True

            @staticmethod
            def join(timeout=None):
                pass

            @staticmethod
            def terminate():
                pass

        self.window.child = FakeCapture()
        with patch("liteapp.ui.analyze") as analyze:
            self.window.tick()

        analyze.assert_not_called()

    def test_live_frame_is_shown_in_central_view(self):
        image = QPixmap(80, 60)
        image.fill("red")
        from PySide6.QtCore import QBuffer, QIODevice
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "PNG")
        self.window.show_preview(bytes(buffer.data()))
        self.assertFalse(self.window.preview.pixmap().isNull())
        self.assertTrue(self.window.chat_page.isAncestorOf(self.window.preview))

    def test_failed_token_exchange_keeps_issued_client_for_retry(self):
        self.window.subscription_done(({"client_id": "oaiapp_test"}, []), None,
                                      "令牌交换失败")
        self.assertEqual(self.window.config["subscription"]["client_id"], "oaiapp_test")
        self.assertEqual(self.window.config["backend"], "api")
        self.assertIn("重试", self.window.notice.text())

    def test_successful_chatgpt_login_switches_settings_backend_and_model(self):
        credential = {"access_token": "synthetic-token", "refresh_token": "synthetic-refresh"}
        with patch("liteapp.ui.settings.save"):
            self.window.subscription_done((credential, [("Account model", "account-model")]), None, "")
        self.assertEqual(self.window.config["backend"], "chatgpt")
        self.assertEqual(self.window.settings_page.backend.currentData(), "chatgpt")
        self.assertEqual(self.window.settings_page.model.currentData(), "account-model")

    def test_connect_starts_capture_for_selected_window(self):
        class FakeQueue:
            def cancel_join_thread(self):
                pass

            def close(self):
                pass

        class FakeEvent:
            def set(self):
                pass

        class FakeProcess:
            def __init__(self, target, args, daemon):
                self.target = target
                self.args = args
                self.daemon = daemon
                self.started = False

            def start(self):
                self.started = True

            def join(self, timeout=None):
                pass

            def is_alive(self):
                return False

        selected = {"id": "window-1", "hwnd": 1234}
        capture_queue = FakeQueue()
        stop_event = FakeEvent()
        frame_clock = object()
        self.window.windows.addItem("微信窗口 1", selected)
        with patch("liteapp.ui.mp.Queue", return_value=capture_queue), \
                patch("liteapp.ui.mp.Event", return_value=stop_event), \
                patch("liteapp.ui.mp.Array", return_value=frame_clock), \
                patch("liteapp.ui.mp.Process", FakeProcess):
            self.window.connect_item(selected)

        process = self.window.child
        self.assertTrue(process.started)
        self.assertEqual(process.args[2], selected["hwnd"])
        self.assertEqual(self.window.state.session, process.args[3])
        self.assertEqual(self.window.state.source_status, "connecting")
        self.assertTrue(self.window.editor.isReadOnly())

    def test_new_snapshot_invalidates_visible_replies(self):
        self.window.state.connect("manual-1")
        self.window.state.set_manual("手动对话", "对方：你好")
        self.window.set_editor("对方：你好")
        self.window.editor.setReadOnly(False)
        token = self.window.state.token
        result = {"intent": "问候", "need": "回应", "action": "自然回复", "caution": "信息有限",
                  "replies": ["你好", "嗨", "在的"]}
        self.window.analysis_done(token, result, "")
        self.assertTrue(self.window.copy_buttons[0].isEnabled())

        self.window.state.update("Alice", [("her", "", "想咨询价格")])
        self.window.clear_results()

        self.assertFalse(self.window.copy_buttons[0].isEnabled())
        self.assertIsNone(self.window.displayed_token)
        self.assertEqual(self.window.state.text, "对方：想咨询价格")

        self.window.analysis_done(token, result, "")
        self.assertFalse(self.window.copy_buttons[0].isEnabled())
        self.assertIsNone(self.window.displayed_token)


class SettingsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_switching_back_to_api_restores_api_model_name(self):
        config = {
            "wechat_path": "", "base_url": "https://example.test/v1",
            "backend": "chatgpt", "model": "account-model", "api_model": "custom-api-model",
            "api_key": "test-key", "style": "自然简短", "auto_connect": False,
            "subscription": {"access_token": "token"},
            "subscription_models": [["Account model", "account-model"]],
        }
        dialog = SettingsDialog(config, None)

        dialog.backend.setCurrentIndex(dialog.backend.findData("api"))
        self.app.processEvents()

        self.assertEqual(dialog.model.currentText(), "custom-api-model")
        with patch("liteapp.ui.settings.save"):
            dialog.commit()
        self.assertEqual(dialog.config["backend"], "api")
        self.assertEqual(dialog.config["model"], "custom-api-model")
        self.assertEqual(dialog.config["api_model"], "custom-api-model")
        dialog.close()


if __name__ == "__main__":
    unittest.main()
