import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.ui import MainWindow

CONFIG = {
    "wechat_path": "", "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "synthetic",
    "style": "自然、简短", "auto_connect": False, "auto_analysis": True,
    "subscription": None, "subscription_models": [], "_warning": "",
    "reading_backend": "ocr", "reading_api_key": "", "reading_base_url": "",
    "reading_model": "", "codex_path": "", "chatgpt_model": "",
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
        self.last = None

    def submit(self, request, scope_id=None):
        self.requests.append(request)
        self.last = FakeHandle(request)
        return self.last

    def stop(self):
        pass

    def resume(self):
        pass


class RecentFullContextTests(unittest.TestCase):
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

    def test_overlapping_snapshots_merge_without_duplicates(self):
        self.window._remember_recent("客户", [
            ("her", "客户", "第一句"), ("me", "", "第二句")])
        self.window._remember_recent("客户", [
            ("me", "", "第二句"), ("her", "客户", "第三句")])
        transcript = self.window._recent_transcript()
        self.assertEqual(transcript.count("第二句"), 1)
        for text in ("第一句", "第二句", "第三句"):
            self.assertIn(text, transcript)

    def test_different_conversation_resets_context(self):
        self.window._remember_recent("客户 A", [("her", "客户 A", "旧话题")])
        self.window._remember_recent("客户 B", [("her", "客户 B", "新话题")])
        transcript = self.window._recent_transcript()
        self.assertNotIn("旧话题", transcript)
        self.assertIn("新话题", transcript)

    def test_recent_context_is_bounded(self):
        for index in range(500):
            self.window._remember_recent("客户", [("her", "客户", f"消息{index}" * 20)])
        self.assertLessEqual(len(self.window._recent_messages),
                             self.window.REALTIME_MAX_MESSAGES)
        self.assertLessEqual(sum(len(item[2]) for item in self.window._recent_messages),
                             self.window.REALTIME_MAX_CHARS)

    def test_analysis_uses_accumulated_full_context_not_only_viewport(self):
        self.window.state.connect("session")
        self.window.state.update("客户", [("her", "客户", "第一句"), ("me", "", "第二句")])
        self.window._remember_recent("客户", [("her", "客户", "第一句"),
                                              ("me", "", "第二句")])
        self.window._remember_recent("客户", [("her", "客户", "窗口外较早内容"),
                                              ("me", "", "第二句")])
        # The editor only holds the current viewport; the request must include the
        # earlier accumulated message too.
        self.window.set_editor("对方：第二句")
        self.window.remote_reading = False
        self.window.reading_ok_token = None
        queue = FakeQueue()
        self.window.inference_queue = queue
        self.window.start_analysis()
        self.assertEqual(len(queue.requests), 1)
        content = queue.requests[0].input_data["messages"][-1]["content"]
        self.assertIn("窗口外较早内容", content)
        self.assertIn("第一句", content)


if __name__ == "__main__":
    unittest.main()
