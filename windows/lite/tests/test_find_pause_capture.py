import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PySide6.QtWidgets import QApplication, QMessageBox

from liteapp.ui import MainWindow, normalize_title_for_match, titles_match

CONFIG = {
    "wechat_path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "synthetic",
    "style": "自然、简短", "auto_connect": False, "auto_analysis": False,
    "auto_send_allowed": False, "subscription": None, "subscription_models": [],
    "_warning": "", "reading_backend": "ocr", "reading_api_key": "",
    "reading_base_url": "", "reading_model": "", "codex_path": "",
    "chatgpt_model": "",
}


def item():
    return {"hwnd": 777, "id": 777, "pid": 1, "created": 1, "title": "微信",
            "path": CONFIG["wechat_path"], "status": "visible", "visible": True,
            "minimized": False, "tool_window": False, "client_rect": (0, 0, 100, 100),
            "frame_rect": (0, 0, 100, 100)}


class FakeThread:
    def __init__(self, target, daemon=False):
        self.target = target

    def start(self):
        self.target()


class FindPausesCaptureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.window_item = item()
        self.window.settings_page.auto_target.setText("田子夜")

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_find_pauses_and_resumes_capture(self):
        calls = []
        with patch.object(self.window, "stop_capture",
                          side_effect=lambda: calls.append("stop")), \
                patch.object(self.window, "connect_item",
                             side_effect=lambda target: calls.append(("connect", target))), \
                patch.object(self.window, "_prepare_wechat_for_action",
                             return_value=False), \
                patch.object(self.window, "_finish_wechat_action",
                             return_value=None), \
                patch("liteapp.wechat_find.find_conversation",
                      return_value=SimpleNamespace(ok=True, reason="ok", steps=())), \
                patch("liteapp.ui.threading.Thread", FakeThread):
            self.window.find_wechat_conversation()
        self.assertEqual(calls[0], "stop")
        self.assertEqual(calls[-1][0], "connect")
        self.assertEqual(calls[-1][1]["hwnd"], 777)
        self.assertIsNone(self.window._resume_item_after_action)

    def test_failed_find_still_resumes_capture(self):
        calls = []
        with patch.object(self.window, "stop_capture",
                          side_effect=lambda: calls.append("stop")), \
                patch.object(self.window, "connect_item",
                             side_effect=lambda target: calls.append(("connect", target))), \
                patch.object(self.window, "_prepare_wechat_for_action",
                             return_value=False), \
                patch.object(self.window, "_finish_wechat_action",
                             return_value=None), \
                patch("liteapp.wechat_find.find_conversation",
                      side_effect=RuntimeError("boom")), \
                patch("liteapp.ui.threading.Thread", FakeThread):
            self.window.find_wechat_conversation()
        self.assertEqual(calls[0], "stop")
        self.assertEqual(calls[-1][0], "connect")


class PrepareFailureRestoresTests(unittest.TestCase):
    """H4: an exception before the action try-block must not wedge the UI."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=dict(CONFIG), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.window_item = item()
        self.window.capture_session = "session-1"
        self.window.settings_page.auto_target.setText("田子夜")

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_find_prepare_exception_restores_busy_and_capture(self):
        calls = []
        with patch.object(self.window, "stop_capture",
                          side_effect=lambda: calls.append("stop")), \
                patch.object(self.window, "connect_item",
                             side_effect=lambda target: calls.append(("connect", target))), \
                patch.object(self.window, "_prepare_wechat_for_action",
                             side_effect=RuntimeError("prepare boom")), \
                patch("liteapp.wechat_find.find_conversation") as find, \
                patch("liteapp.ui.threading.Thread", FakeThread):
            self.window.find_wechat_conversation()
        find.assert_not_called()
        self.assertFalse(self.window.wechat_busy)
        self.assertEqual(calls[0], "stop")
        self.assertEqual(calls[-1][0], "connect")
        self.assertIn("自动查找对话失败", self.window.notice.text())

    def _make_send_ready(self):
        self.window.settings_page.auto_send_allowed.setChecked(True)
        self.window.state.connect("session")
        self.window.state.update("田子夜", [("her", "", "你好")])
        self.window.displayed_token = self.window.state.token
        self.window.reply_boxes[0].setText("测试回复")
        self.window.chat_title.setText("田子夜")

    def test_send_prepare_exception_restores_busy_and_capture(self):
        self._make_send_ready()
        calls = []
        with patch.object(self.window, "stop_capture",
                          side_effect=lambda: calls.append("stop")), \
                patch.object(self.window, "connect_item",
                             side_effect=lambda target: calls.append(("connect", target))), \
                patch.object(self.window, "_prepare_wechat_for_action",
                             side_effect=RuntimeError("prepare boom")), \
                patch.object(self.window, "_capture_client_frame") as capture, \
                patch("liteapp.wechat_actions.send_message") as send, \
                patch("liteapp.ui.QMessageBox.question",
                      return_value=QMessageBox.StandardButton.Yes), \
                patch("liteapp.ui.threading.Thread", FakeThread):
            self.window.send_reply_to_wechat(0)
        capture.assert_not_called()
        send.assert_not_called()
        self.assertFalse(self.window.wechat_busy)
        self.assertEqual(calls[0], "stop")
        self.assertEqual(calls[-1][0], "connect")


class TitleExactMatchTests(unittest.TestCase):
    """H3: the pre-send conversation check must be exact, never a substring."""

    def test_exact_normalized_match_only(self):
        self.assertTrue(titles_match("田子夜", "田子夜"))
        self.assertTrue(titles_match(" 田子夜 ", "田子夜"))
        self.assertTrue(titles_match("田子夜：", "田子夜"))
        self.assertFalse(titles_match("田子夜2", "田子夜"))
        self.assertFalse(titles_match("x田子夜", "田子夜"))
        self.assertFalse(titles_match("田子", "田子夜"))
        self.assertFalse(titles_match("", "田子夜"))
        self.assertFalse(titles_match("田子夜", ""))
        self.assertEqual(normalize_title_for_match(" 田 子 夜： "), "田子夜")
        self.assertEqual(normalize_title_for_match(None), "")

    def test_send_rejects_substring_title_and_never_touches_wechat(self):
        window = MainWindow(config=dict(CONFIG), scanning=False)
        try:
            window.poll.stop()
            window.dock_timer.stop()
            window.scanner.stop()
            window.window_item = item()
            window.settings_page.auto_send_allowed.setChecked(True)
            window.state.connect("session")
            window.state.update("田子夜", [("her", "", "你好")])
            window.displayed_token = window.state.token
            window.reply_boxes[0].setText("测试回复")
            window.chat_title.setText("田子夜")
            frame = np.zeros((800, 1200, 3), dtype=np.uint8)
            with patch.object(window, "_prepare_wechat_for_action",
                              return_value=False), \
                    patch.object(window, "stop_capture"), \
                    patch.object(window, "connect_item") as connect, \
                    patch.object(window, "_capture_client_frame",
                                 return_value=(frame, 1200, 800)), \
                    patch("vendor.ocr.read_title", return_value="田子夜2"), \
                    patch("vendor.ocr.Reader") as reader_cls, \
                    patch("liteapp.wechat_actions.send_message") as send, \
                    patch("liteapp.ui.QMessageBox.question",
                          return_value=QMessageBox.StandardButton.Yes), \
                    patch("liteapp.ui.threading.Thread", FakeThread):
                window.send_reply_to_wechat(0)
            send.assert_not_called()
            reader_cls.assert_not_called()
            self.assertFalse(window.wechat_busy)
            self.assertIn("不一致", window.notice.text())
            self.assertGreaterEqual(connect.call_count, 1)
        finally:
            window.close()
            QApplication.instance().processEvents()

    def test_send_accepts_exact_title(self):
        window = MainWindow(config=dict(CONFIG), scanning=False)
        try:
            window.poll.stop()
            window.dock_timer.stop()
            window.scanner.stop()
            window.window_item = item()
            window.settings_page.auto_send_allowed.setChecked(True)
            window.state.connect("session")
            window.state.update("田子夜", [("her", "", "你好")])
            window.displayed_token = window.state.token
            window.reply_boxes[0].setText("测试回复")
            window.chat_title.setText("田子夜")
            frame = np.zeros((800, 1200, 3), dtype=np.uint8)
            with patch.object(window, "_prepare_wechat_for_action",
                              return_value=False), \
                    patch.object(window, "stop_capture"), \
                    patch.object(window, "connect_item"), \
                    patch.object(window, "_capture_client_frame",
                                 return_value=(frame, 1200, 800)), \
                    patch.object(window, "_ocr_rows",
                                 return_value=[("发送", 900, 700, 950, 740)]), \
                    patch("vendor.ocr.read_title", return_value=" 田子夜 "), \
                    patch("vendor.ocr.Reader"), \
                    patch("liteapp.wechat_actions.send_message",
                          return_value=SimpleNamespace(sent=False, verified=False)) as send, \
                    patch("liteapp.ui.QMessageBox.question",
                          return_value=QMessageBox.StandardButton.Yes), \
                    patch("liteapp.ui.threading.Thread", FakeThread):
                window.send_reply_to_wechat(0)
            send.assert_called_once()
            self.assertFalse(window.wechat_busy)
        finally:
            window.close()
            QApplication.instance().processEvents()


class FinderNoEnterUiRegressionTests(unittest.TestCase):
    """Codex fix: never press Enter after clicking a search result (draft safety)."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        window = MainWindow(config=dict(CONFIG), scanning=False)
        window.poll.stop()
        window.dock_timer.stop()
        window.scanner.stop()
        window.window_item = item()
        window.capture_session = "session-1"
        window.settings_page.auto_target.setText("田子夜")
        return window

    def test_failed_find_sends_escape_and_restores_busy(self):
        window = self._window()
        keys = []
        try:
            with patch.object(window, "_prepare_wechat_for_action",
                              return_value=False), \
                    patch.object(window, "stop_capture"), \
                    patch.object(window, "connect_item") as connect, \
                    patch("liteapp.wechat_find.find_conversation",
                          return_value=SimpleNamespace(ok=False,
                                                       reason="search_not_found",
                                                       steps=())), \
                    patch("liteapp.ui.window_status", return_value="visible"), \
                    patch("liteapp.ui.foreground_window", return_value=777), \
                    patch("liteapp.wechat_actions.Win32Backend.key_combo",
                          autospec=True,
                          side_effect=lambda backend_self, combo: keys.append(tuple(combo))), \
                    patch("liteapp.ui.threading.Thread", FakeThread):
                window.find_wechat_conversation()
            self.assertIn((0x1B,), keys,
                          "failed lookup must ESC out of the search overlay")
            self.assertFalse(window.wechat_busy)
            self.assertGreaterEqual(connect.call_count, 1)
            self.assertIn("没有找到微信搜索框", window.notice.text())
        finally:
            window.close()
            QApplication.instance().processEvents()

    def test_successful_find_does_not_send_escape(self):
        window = self._window()
        keys = []
        try:
            with patch.object(window, "_prepare_wechat_for_action",
                              return_value=False), \
                    patch.object(window, "stop_capture"), \
                    patch.object(window, "connect_item"), \
                    patch("liteapp.wechat_find.find_conversation",
                          return_value=SimpleNamespace(ok=True, reason="ok",
                                                       steps=())), \
                    patch("liteapp.wechat_actions.Win32Backend.key_combo",
                          autospec=True,
                          side_effect=lambda backend_self, combo: keys.append(tuple(combo))), \
                    patch("liteapp.ui.threading.Thread", FakeThread):
                window.find_wechat_conversation()
            self.assertNotIn((0x1B,), keys)
            self.assertIn("已切换到目标对话", window.notice.text())
        finally:
            window.close()
            QApplication.instance().processEvents()

    def test_ui_find_success_never_presses_enter_or_sends(self):
        from liteapp.wechat_find import find_conversation
        from tests.test_wechat_find import (FakeBackend as FindBackend,
                                            FakeReader, WECHAT_HWND, frame, script)

        backend = FindBackend()
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("田子夜", 145, 115)],
            [("田子夜", 390, 56)],
        ))

        def fake_find(hwnd, target, **kwargs):
            return find_conversation(WECHAT_HWND, target, backend=backend,
                                     reader=reader,
                                     capture=lambda h: (frame(), 1200, 800),
                                     settle=0.0)

        window = MainWindow(config=dict(CONFIG), scanning=False)
        try:
            window.poll.stop()
            window.dock_timer.stop()
            window.scanner.stop()
            window.window_item = item()
            window.capture_session = "session-1"
            window.settings_page.auto_target.setText("田子夜")
            with patch.object(window, "_prepare_wechat_for_action",
                              return_value=False), \
                    patch.object(window, "stop_capture"), \
                    patch.object(window, "connect_item"), \
                    patch("liteapp.wechat_find.find_conversation",
                          side_effect=fake_find), \
                    patch("liteapp.wechat_find._foreground_ok", return_value=True), \
                    patch("liteapp.wechat_actions.send_message") as send, \
                    patch("liteapp.ui.threading.Thread", FakeThread):
                window.find_wechat_conversation()
            self.assertNotIn(("keys", (0x0D,)), backend.calls,
                             "the lookup must never press Enter into an existing draft")
            send.assert_not_called()
            self.assertFalse(window.wechat_busy)
            self.assertIn("已切换到目标对话", window.notice.text())
        finally:
            window.close()
            QApplication.instance().processEvents()


class FindReadinessRetryTests(unittest.TestCase):
    """C1: pre-lookup render readiness + one bounded retry for transient failures."""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _window(self):
        window = MainWindow(config=dict(CONFIG), scanning=False)
        window.poll.stop()
        window.dock_timer.stop()
        window.scanner.stop()
        window.window_item = item()
        window.capture_session = "session-1"
        window.settings_page.auto_target.setText("田子夜")
        return window

    def test_render_readiness_waits_for_non_blank_frame(self):
        window = self._window()
        blank = np.zeros((800, 1200, 3), dtype=np.uint8)
        good = np.full((800, 1200, 3), 120, dtype=np.uint8)
        good[::3, ::5] = 255
        try:
            with patch("liteapp.ui.window_status", return_value="visible"), \
                    patch("liteapp.ui.foreground_window", return_value=777), \
                    patch("liteapp.wechat_actions.Win32Backend.activate",
                          autospec=True, return_value=True), \
                    patch("liteapp.wechat_find.capture_client_bgr",
                          side_effect=[(blank, 1200, 800), (good, 1200, 800)]) as capture, \
                    patch("liteapp.ui.time.sleep"):
                self.assertTrue(window._wait_wechat_render_ready(777, timeout=1.0))
            self.assertEqual(capture.call_count, 2,
                             "a blank first frame must be skipped, not accepted")
        finally:
            window.close()
            self.app.processEvents()

    def test_render_readiness_timeout_is_fail_open_and_never_mutates_window(self):
        window = self._window()
        try:
            with patch("liteapp.ui.window_status", return_value="visible"), \
                    patch("liteapp.ui.foreground_window", return_value=777), \
                    patch("liteapp.wechat_actions.Win32Backend.activate",
                          autospec=True, return_value=True), \
                    patch("liteapp.wechat_find.capture_client_bgr", return_value=None), \
                    patch("liteapp.ui.time.sleep"), \
                    patch("liteapp.ui.restore_window") as restore:
                self.assertFalse(window._wait_wechat_render_ready(777, timeout=0.05))
            restore.assert_not_called()
            self.assertFalse(window.wechat_busy)
        finally:
            window.close()
            self.app.processEvents()

    def _run_find(self, window, results):
        calls, escapes = [], []

        def fake_find(hwnd, target):
            calls.append((hwnd, target))
            return results[min(len(calls) - 1, len(results) - 1)]

        with patch.object(window, "_prepare_wechat_for_action", return_value=False), \
                patch.object(window, "stop_capture"), \
                patch.object(window, "connect_item"), \
                patch.object(window, "_wait_wechat_render_ready", return_value=True), \
                patch.object(window, "_escape_wechat_search",
                             side_effect=lambda hwnd: escapes.append(hwnd)), \
                patch("liteapp.wechat_find.find_conversation", side_effect=fake_find), \
                patch("liteapp.ui.threading.Thread", FakeThread):
            window.find_wechat_conversation()
        return calls, escapes

    def test_transient_failure_is_retried_once_then_succeeds(self):
        window = self._window()
        try:
            calls, escapes = self._run_find(window, [
                SimpleNamespace(ok=False, reason="search_not_found", steps=()),
                SimpleNamespace(ok=True, reason="ok", steps=()),
            ])
            self.assertEqual(len(calls), 2, "transient lookup failure should retry once")
            self.assertEqual(len(escapes), 1, "retry starts by ESC-ing the stale search state")
            self.assertIn("已切换到目标对话", window.notice.text())
            self.assertFalse(window.wechat_busy)
        finally:
            window.close()
            self.app.processEvents()

    def test_non_transient_failure_is_not_retried(self):
        window = self._window()
        try:
            calls, escapes = self._run_find(window, [
                SimpleNamespace(ok=False, reason="activate_failed", steps=()),
            ])
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(escapes), 1, "failed lookup still ESC-safely exits")
            self.assertIn("无法激活", window.notice.text())
        finally:
            window.close()
            self.app.processEvents()

    def test_two_transient_failures_stay_bounded_and_report_reason(self):
        window = self._window()
        try:
            calls, escapes = self._run_find(window, [
                SimpleNamespace(ok=False, reason="search_not_found", steps=()),
                SimpleNamespace(ok=False, reason="result_not_found", steps=()),
            ])
            self.assertEqual(len(calls), 2, "retry must be bounded (no retry storm)")
            self.assertEqual(len(escapes), 1,
                             "retry + final failure must ESC at most once")
            self.assertIn("搜索结果里没有找到", window.notice.text())
            self.assertFalse(window.wechat_busy)
        finally:
            window.close()
            self.app.processEvents()

    def test_escape_is_skipped_when_wechat_is_not_foreground(self):
        window = self._window()
        keys = []
        try:
            with patch("liteapp.ui.window_status", return_value="visible"), \
                    patch("liteapp.ui.foreground_window", return_value=999), \
                    patch("liteapp.wechat_actions.Win32Backend.activate",
                          autospec=True, return_value=False), \
                    patch("liteapp.wechat_actions.Win32Backend.key_combo",
                          autospec=True,
                          side_effect=lambda backend_self, combo: keys.append(combo)):
                self.assertFalse(window._escape_wechat_search(777))
            self.assertEqual(keys, [],
                             "global ESC must never be sent to another foreground window")
        finally:
            window.close()
            self.app.processEvents()

    def test_escape_keeps_window_visible_and_restores_if_it_became_hidden(self):
        window = self._window()
        keys, shown = [], []
        try:
            with patch("liteapp.ui.window_status",
                       side_effect=["visible", "hidden"]), \
                    patch("liteapp.ui.foreground_window", return_value=777), \
                    patch("liteapp.wechat_actions.Win32Backend.key_combo",
                          autospec=True,
                          side_effect=lambda backend_self, combo: keys.append(combo)), \
                    patch("ctypes.windll.user32.ShowWindow",
                          side_effect=lambda hwnd, cmd: shown.append((hwnd, cmd))):
                self.assertTrue(window._escape_wechat_search(777))
            self.assertEqual(keys, [(0x1B,)])
            self.assertEqual(shown, [(777, 4)],
                             "a window hidden by our own ESC must be re-shown no-activate")
        finally:
            window.close()
            self.app.processEvents()

    def test_escape_leaves_visible_window_untouched(self):
        window = self._window()
        keys, shown = [], []
        try:
            with patch("liteapp.ui.window_status", return_value="visible"), \
                    patch("liteapp.ui.foreground_window", return_value=777), \
                    patch("liteapp.wechat_actions.Win32Backend.key_combo",
                          autospec=True,
                          side_effect=lambda backend_self, combo: keys.append(combo)), \
                    patch("ctypes.windll.user32.ShowWindow",
                          side_effect=lambda hwnd, cmd: shown.append((hwnd, cmd))):
                self.assertTrue(window._escape_wechat_search(777))
            self.assertEqual(keys, [(0x1B,)])
            self.assertEqual(shown, [], "a still-visible window must not be touched")
        finally:
            window.close()
            self.app.processEvents()

    def test_escape_does_not_activate_when_already_foreground(self):
        window = self._window()
        try:
            with patch("liteapp.ui.window_status", return_value="visible"), \
                    patch("liteapp.ui.foreground_window", return_value=777), \
                    patch("liteapp.wechat_actions.Win32Backend.activate",
                          autospec=True) as activate, \
                    patch("liteapp.wechat_actions.Win32Backend.key_combo",
                          autospec=True, return_value=True):
                self.assertTrue(window._escape_wechat_search(777))
            activate.assert_not_called()
        finally:
            window.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()

