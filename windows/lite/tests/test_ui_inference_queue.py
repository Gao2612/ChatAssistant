import os
from unittest.mock import patch
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.inference_schemas import InferenceQuotaError, InferenceResult
from liteapp.ui import MainWindow

BASE_CONFIG = {
    "wechat_path": "", "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "test-key",
    "style": "自然简短", "auto_connect": False, "auto_analysis": True,
    "subscription": None, "subscription_models": [], "_warning": "",
    "reading_backend": "ocr", "reading_api_key": "", "reading_base_url": "",
    "reading_model": "", "codex_path": "", "chatgpt_model": "",
}
REPLY = {"intent": "想咨询价格", "need": "报价单", "action": "先确认数量",
         "caution": "不要承诺未确认的折扣",
         "replies": ["可以，我先确认数量。", "收到，稍后发报价。", "方便说下型号吗？"]}


class FakeHandle:
    def __init__(self, request):
        self.request = request
        self.callback = None
        self.error = None
        self.result = None
        self.cancelled = False

    def set_callback(self, callback):
        self.callback = callback

    def cancel(self):
        self.cancelled = True


class FakeQueue:
    def __init__(self):
        self.submitted = []
        self.last = None
        self.stopped = False
        self.resumed = 0

    def submit(self, request, scope_id=None):
        self.last = FakeHandle(request)
        self.submitted.append((request, scope_id))
        return self.last

    def resume(self):
        self.resumed += 1

    def stop(self):
        self.stopped = True


class InferenceQueueUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(BASE_CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.queue = FakeQueue()
        self.window.inference_queue = self.queue
        self.window.state.connect("source")
        self.window.state.update("Alice", [("her", "", "你们这个多少钱？")])
        self.window.set_editor("对方：你们这个多少钱？")

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_api_request_uses_gateway_contract(self):
        request = self.window._analysis_request(dict(BASE_CONFIG), "对方：多少钱")
        self.assertEqual(request.kind, "realtime_reply")
        self.assertEqual(request.profile_id, "analysis")
        self.assertEqual(request.version_key, "api:api-model")
        self.assertIn("messages", request.input_data)

    def test_chatgpt_request_uses_instructions_and_input(self):
        config = dict(BASE_CONFIG, backend="chatgpt", model="gpt-6.1-sol",
                      chatgpt_model="gpt-6.1-sol",
                      subscription={"access_token": "token"},
                      subscription_models=[["GPT", "gpt-6.1-sol"]])
        request = self.window._analysis_request(config, "对方：多少钱")
        self.assertEqual(request.profile_id, "chatgpt")
        self.assertEqual(request.version_key, "chatgpt:gpt-6.1-sol")
        self.assertIn("instructions", request.input_data)
        self.assertIn("input", request.input_data)

    def test_start_analysis_submits_to_queue_and_renders_validated_result(self):
        self.window.start_analysis()
        self.assertEqual(len(self.queue.submitted), 1)
        request, scope_id = self.queue.submitted[0]
        self.assertEqual(request.kind, "realtime_reply")
        self.assertEqual(scope_id, "visible-chat")
        handle = self.queue.last
        self.assertIsNotNone(handle.callback)
        handle.result = InferenceResult(validated_data=dict(REPLY),
                                        request_id="req-1", elapsed=0.2)
        handle.callback(handle)
        self.assertFalse(self.window.busy)
        self.assertIn("想咨询价格", self.window.summary.text())
        self.assertEqual(self.window.analysis_status.text(), "已生成 3 条建议")

    def test_quota_error_pauses_automatic_analysis_status(self):
        self.window.start_analysis()
        handle = self.queue.last
        handle.error = InferenceQuotaError("模型额度已用尽")
        handle.callback(handle)
        self.assertFalse(self.window.busy)
        self.assertIn("额度受限", self.window.analysis_status.text())

    def test_reenabling_auto_analysis_resumes_queue(self):
        with patch("liteapp.ui.settings.save"):
            self.window.auto_toggled(True)
        self.assertEqual(self.queue.resumed, 1)


if __name__ == "__main__":
    unittest.main()
