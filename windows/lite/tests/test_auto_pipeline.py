import os
import queue
import sys
import time
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).parents[1]))
from PySide6.QtWidgets import QApplication
from liteapp.ui import MainWindow

RESULT = {'intent': '咨询', 'need': '价格', 'action': '先确认规格', 'caution': '测试数据',
          'replies': ['请问您需要什么规格？', '我先核实价格。', '可以，请告诉我数量。']}


class Capture:
    def is_alive(self): return True
    def join(self, timeout=None): pass
    def terminate(self): pass


class FrameClock(list):
    def get_lock(self): return nullcontext()


class QueueHandle:
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


class QueueStub:
    def __init__(self):
        self.requests = []

    def submit(self, request, scope_id=None):
        handle = QueueHandle(request)
        self.requests.append(handle)
        return handle

    def stop(self):
        pass

    def resume(self):
        pass


class AutoPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config={
            'wechat_path': '', 'backend': 'api', 'base_url': 'https://example.test/v1',
            'model': 'test', 'api_model': 'test', 'api_key': 'synthetic',
            'auto_connect': False, 'auto_analysis': True, 'subscription_models': [],
            'favorite_contacts': ['测试客户'],
        }, scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.child = Capture()
        self.window.window_item = {'hwnd': 9999}
        self.window.capture_session = 'source'
        self.window.capture_q = queue.Queue()
        self.window.state.connect('source')
        self.messages = [('her', '', '请问价格是多少？')]
        self.window.state.update('测试客户', self.messages)
        self.window.set_editor(self.window.state.text)
        self.window.changed_at = 0
        self.token = self.window.state.token

    def tearDown(self):
        self.window.capture_q = None
        self.window.close()
        self.app.processEvents()

    def test_confirmed_text_automatically_produces_suggestions(self):
        from liteapp.inference_schemas import InferenceResult
        self.window.inference_queue = QueueStub()
        self.window.tick()
        deadline = time.monotonic() + 2
        while not self.window.inference_queue.requests and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertEqual(len(self.window.inference_queue.requests), 1)
        handle = self.window.inference_queue.requests[0]
        handle.result = InferenceResult(validated_data=dict(RESULT),
                                        request_id="req", elapsed=0.1)
        handle.callback(handle)
        self.assertEqual(self.window.displayed_token, self.token)
        self.assertTrue(self.window.copy_buttons[0].isEnabled())

    def _pending_frame(self):
        self.window.frame_clock = FrameClock([2, 0])
        self.window.frame_freshness.latest = 1
        with patch.object(self.window, 'backend_ready', return_value=False):
            self.window.tick()

    def _confirm_frame(self, messages):
        self.window.capture_q.put({'kind': 'snapshot', 'session': 'source', 'frame_seq': 2,
                                  'title': '测试客户', 'messages': messages, 'ocr_ms': 2})
        with patch.object(self.window, 'backend_ready', return_value=False):
            self.window.tick()

    def test_visual_recheck_restores_suggestions_when_confirmed_text_is_unchanged(self):
        self.window.analysis_done(self.token, RESULT, '')
        self.window.last_auto_token = self.token
        self._pending_frame()
        self.assertFalse(self.window.copy_buttons[0].isEnabled())
        self._confirm_frame(self.messages)
        self.assertEqual(self.window.displayed_token, self.token)
        self.assertTrue(self.window.copy_buttons[0].isEnabled())

    def test_result_finished_during_recheck_is_held_until_matching_text_is_confirmed(self):
        self._pending_frame()
        self.window.analysis_done(self.token, RESULT, '')
        self.assertFalse(self.window.copy_buttons[0].isEnabled())
        self._confirm_frame(self.messages)
        self.assertTrue(self.window.copy_buttons[0].isEnabled())
        self.assertEqual(self.window.displayed_token, self.token)

    def test_changed_text_never_restores_previous_suggestions(self):
        self.window.analysis_done(self.token, RESULT, '')
        self._pending_frame()
        self._confirm_frame([('her', '', '现在不需要了。')])
        self.assertFalse(self.window.copy_buttons[0].isEnabled())
        self.assertIsNone(self.window.displayed_token)

    def test_missing_model_is_explained_after_text_is_recognized(self):
        self.window.config['api_key'] = ''
        self.window.tick()
        self.assertIn('模型', self.window.notice.text())


if __name__ == '__main__':
    unittest.main()
