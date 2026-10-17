import hashlib
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.history_models import ImportedRecord, ImportSource
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


class FakeHandle:
    def __init__(self, request):
        self.request = request
        self.callback = None
        self.cancelled = False
        self.error = None
        self.result = None

    def set_callback(self, callback):
        self.callback = callback

    def cancel(self):
        self.cancelled = True


class FakeQueue:
    def __init__(self):
        self.requests = []

    def submit(self, request, scope_id=None):
        self.requests.append(request)
        return FakeHandle(request)

    def stop(self):
        pass

    def resume(self):
        pass


class T9BindingMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.window_item = {"id": "w1", "hwnd": 4242, "pid": 777,
                                   "path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
                                   "created": 987654321}
        self.window.capture_session = "session-1"
        self.window.state.connect("session-1")
        self.window.state.update("测试客户", [("her", "", "你们报价多少？")])
        self.window.set_editor(self.window.state.text)
        self.window._remember_recent("测试客户", [("her", "", "你们报价多少？")])
        self.window._note_conversation_title("测试客户")
        self.queue = FakeQueue()
        self.window.inference_queue = self.queue

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def _import_memory(self):
        scope = self.window.history_page.store.create_person("测试客户", "default")
        digest = hashlib.sha256(b"memory-source").hexdigest()
        self.window.history_page.store.import_records(
            scope, ImportSource(digest=digest, kind="html", name="synthetic.html"),
            [ImportedRecord(text="之前答应过下周三发报价单", ordinal=0)])
        return scope

    def test_binding_only_resolves_for_current_conversation_epoch(self):
        scope = self._import_memory()
        self.window.history_page.set_current_scope(scope)
        self.window.bind_current_conversation()
        self.assertEqual(self.window._bound_scope(), scope)
        self.window.conversation_epoch += 1  # title/conversation changed
        self.assertIsNone(self.window._bound_scope())

    def test_bound_conversation_adds_history_reference_to_prompt(self):
        scope = self._import_memory()
        self.window.history_page.set_current_scope(scope)
        self.window.bind_current_conversation()
        with patch("liteapp.ui.settings.save"):
            self.window.start_analysis()
        self.assertEqual(len(self.queue.requests), 1)
        content = self.queue.requests[0].input_data["messages"][-1]["content"]
        self.assertIn("[历史参考", content)
        self.assertIn("报价单", content)
        self.assertIn("参考历史", self.window.analysis_status.text())
        self.assertTrue(self.window.memory_button.isEnabled())
        self.assertIn("参考历史", self.window.memory_button.text())

    def test_unbound_conversation_has_no_history_reference(self):
        self.window.history_page.set_current_scope(None)
        with patch("liteapp.ui.settings.save"):
            self.window.start_analysis()
        self.assertEqual(len(self.queue.requests), 1)
        content = self.queue.requests[0].input_data["messages"][-1]["content"]
        self.assertNotIn("[历史参考", content)
        self.assertFalse(self.window.memory_button.isEnabled())
        self.assertIn("未关联", self.window.memory_button.text())


if __name__ == "__main__":
    unittest.main()
