import os
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtWidgets import QApplication

from liteapp.ui import MainWindow, SettingsPage

RESULT = {"intent": "咨询", "need": "价格", "action": "先确认规格", "caution": "测试数据",
          "replies": ["请问需要什么规格？", "我先核实价格。", "可以，请告诉我数量。"]}

BASE_CONFIG = {
    "wechat_path": "", "backend": "api", "base_url": "https://example.test/v1",
    "model": "test-model", "api_model": "test-model", "api_key": "synthetic",
    "auto_connect": False, "auto_analysis": True, "subscription": None,
    "subscription_models": [], "reading_backend": "api",
    "reading_base_url": "https://vision.example.test/v1", "reading_model": "vision-model",
    "reading_api_key": "synthetic-reading", "codex_path": "", "style": "自然",
}


class Capture:
    def is_alive(self):
        return True

    def join(self, timeout=None):
        pass

    def terminate(self):
        pass


class RemoteReadingUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(BASE_CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.child = Capture()
        self.window.window_item = {"hwnd": 9999}
        self.window.capture_session = "source"
        self.window.state.connect("source")
        self.window.state.update("OCR 会话", [("her", "", "OCR 旧文本")])
        self.window.set_editor(self.window.state.text)
        self.window.remote_reading = True
        self.window.reading_ok_token = None
        self.window.frame_freshness.latest = 5
        self.window.changed_at = 0.0
        self.window.last_analysis = 0.0
        self.window.last_auto_token = None

    def tearDown(self):
        self.window.capture_q = None
        self.window.close()
        self.app.processEvents()

    def test_settings_page_exposes_two_independent_backend_lists(self):
        page = self.window.settings_page
        reading = [page.reading_backend.itemData(i) for i in range(page.reading_backend.count())]
        analysis = [page.backend.itemData(i) for i in range(page.backend.count())]
        self.assertEqual(reading, ["ocr", "api", "codex"])
        self.assertEqual(analysis, ["chatgpt", "api", "local", "codex"])
        self.assertEqual(page.reading_backend.currentData(), "api")
        self.assertEqual(page.reading_model.text(), "vision-model")

    def test_remote_reading_result_replaces_ocr_snapshot_and_confirms_gate(self):
        self.assertFalse(self.window.reading_snapshot_ready())
        self.window.reading_done(5, ("模型会话", [("me", "", "你好"),
                                                ("other", "", "价格多少")]), "")
        self.assertEqual(self.window.state.title, "模型会话")
        self.assertIn("对方：价格多少", self.window.editor.toPlainText())
        self.assertTrue(self.window.reading_snapshot_ready())
        self.assertEqual(self.window.state.token, self.window.reading_ok_token)
        self.assertFalse(self.window.copy_buttons[0].isEnabled())

    def test_remote_reading_failure_falls_back_to_local_ocr(self):
        self.window.reading_done(5, None, "读取接口不可用")
        self.assertTrue(self.window.reading_snapshot_ready())
        self.assertEqual(self.window.reading_ok_token, self.window.state.token)
        self.assertIn("OCR", self.window.notice.text())

    def test_stale_remote_reading_result_is_dropped(self):
        self.window.reading_done(4, ("旧会话", [("other", "", "旧结果")]), "")
        self.assertEqual(self.window.state.title, "OCR 会话")
        self.assertFalse(self.window.reading_snapshot_ready())

    def test_auto_analysis_is_gated_until_remote_reading_confirms_ocr_text(self):
        with patch("liteapp.ui.analyze", return_value=RESULT) as analyze:
            self.window.tick()
            self.assertFalse(analyze.called)
            self.assertFalse(self.window.busy)
            self.window.reading_done(5, ("OCR 会话", [("her", "", "OCR 旧文本")]), "")
            self.window.changed_at = 0.0
            self.window.last_analysis = 0.0
            self.window.tick()
            deadline = time.monotonic() + 2
            while self.window.busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertTrue(analyze.called)

    def test_ocr_snapshot_does_not_override_confirmed_remote_text(self):
        self.window.reading_done(5, ("模型会话", [("other", "", "远程文本")]), "")
        token = self.window.state.token
        self.window.frame_freshness.observe(6)
        import queue as queue_module
        self.window.capture_q = queue_module.Queue()
        self.window.capture_q.put({"kind": "snapshot", "session": "source", "frame_seq": 6,
                                  "title": "OCR 标题", "messages": [("her", "", "OCR 文本")]})
        self.window.tick()
        self.assertEqual(self.window.state.title, "模型会话")
        self.assertEqual(self.window.state.token, token)
        self.assertIn("远程文本", self.window.editor.toPlainText())

    def test_analysis_uses_selected_codex_backend(self):
        self.window.config["backend"] = "codex"
        self.window.reading_ok_token = self.window.state.token
        with patch("liteapp.codex_backend.available", return_value=True), \
                patch("liteapp.ui.codex_backend.analyze", return_value=RESULT) as codex:
            self.window.start_analysis()
            deadline = time.monotonic() + 2
            while self.window.busy and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertTrue(codex.called)

    def test_reading_backend_change_reconnects_capture_and_resets_codex(self):
        item = self.window.window_item
        changed = dict(self.window.config, reading_backend="ocr", codex_path="C:\\codex.exe")
        with patch.object(self.window, "scan"), \
                patch.object(self.window, "stop_capture"), \
                patch.object(self.window, "connect_item") as connect_item, \
                patch("liteapp.ui.codex_backend.shutdown") as shutdown:
            self.window.settings_saved(changed)
        connect_item.assert_called_once_with(item)
        shutdown.assert_called_once()
        self.assertFalse(self.window.remote_reading)


class SettingsPageReadingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_commit_saves_separate_reading_and_analysis_configuration(self):
        config = dict(BASE_CONFIG)
        page = SettingsPage(config, None)
        page.reading_backend.setCurrentIndex(page.reading_backend.findData("api"))
        page.reading_base.setText("https://vision.example.test/v1")
        page.reading_model.setText("vision-model")
        page.reading_key.setText("synthetic-reading")
        page.base.setText("http://127.0.0.1:11434/v1")
        page.backend.setCurrentIndex(page.backend.findData("local"))
        page.model.setEditText("local-chat")
        with patch("liteapp.ui.settings.save") as save:
            page.commit()
        page.close()
        self.assertEqual(page.error.text(), "")
        save.assert_called_once()
        saved = save.call_args[0][0]
        self.assertEqual(saved["reading_backend"], "api")
        self.assertEqual(saved["reading_model"], "vision-model")
        self.assertEqual(saved["reading_api_key"], "synthetic-reading")
        self.assertEqual(saved["backend"], "local")
        self.assertEqual(saved["model"], "local-chat")

    def test_commit_rejects_remote_vision_without_key(self):
        config = dict(BASE_CONFIG, reading_api_key="")
        page = SettingsPage(config, None)
        page.reading_backend.setCurrentIndex(page.reading_backend.findData("api"))
        page.reading_base.setText("https://vision.example.test/v1")
        page.reading_model.setText("vision-model")
        page.reading_key.setText("")
        with patch("liteapp.ui.settings.save") as save:
            page.commit()
        page.close()
        self.assertIn("读取 API Key", page.error.text())
        save.assert_not_called()

    def test_switching_analysis_backend_to_codex_arms_editable_model(self):
        page = SettingsPage(dict(BASE_CONFIG, backend="api"), None)
        page.backend.setCurrentIndex(page.backend.findData("codex"))
        self.app.processEvents()
        self.assertTrue(page.model.isEditable())
        page.close()


if __name__ == "__main__":
    unittest.main()
