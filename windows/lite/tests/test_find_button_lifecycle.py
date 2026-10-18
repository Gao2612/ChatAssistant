"""C1 button wiring and asynchronous restoration, with synthetic window I/O."""
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from liteapp.ui import MainWindow
from tests.test_find_pause_capture import CONFIG, item
from tests.test_wechat_find import FakeBackend, FakeReader, frame, script, WECHAT_HWND
from liteapp.wechat_find import find_conversation


class FindButtonLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def pump_until(self, predicate):
        deadline = time.monotonic() + 3
        while not predicate() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate())

    def run_button(self, title):
        window = MainWindow(config=dict(CONFIG), scanning=False)
        window.poll.stop()
        window.dock_timer.stop()
        window.scanner.stop()
        window.window_item = item()
        window.settings_page.auto_target.setText("田子夜")
        window._wechat_find_retries = 0
        backend = FakeBackend()
        reader = FakeReader(script([("Q搜索", 98, 58)],
                                   [("田子夜", 145, 115)],
                                   [(title, 390, 56)]))
        entered, release, restored = (threading.Event() for _ in range(3))
        events, results = [], []

        def lookup(hwnd, target):
            entered.set()
            self.assertTrue(release.wait(3))
            result = find_conversation(
                WECHAT_HWND, target, backend=backend, reader=reader,
                capture=lambda h: (frame(), 1200, 800), settle=0)
            results.append(result)
            return result

        def restore(*args):
            # Pumping the GUI during this delay exposes a premature done signal.
            time.sleep(0.15)
            events.append("restored")
            restored.set()

        try:
            with patch.object(window, "stop_capture"), \
                    patch.object(window, "connect_item",
                                 side_effect=lambda _: events.append("resumed")), \
                    patch.object(window, "_prepare_wechat_for_action", return_value=True), \
                    patch.object(window, "_finish_wechat_action", side_effect=restore), \
                    patch.object(window, "_wait_wechat_render_ready", return_value=True), \
                    patch.object(window, "_escape_wechat_search"), \
                    patch("liteapp.wechat_find._foreground_ok", return_value=True), \
                    patch("liteapp.wechat_find._read_title_crop", return_value=""), \
                    patch("liteapp.wechat_find.find_conversation", side_effect=lookup), \
                    patch("liteapp.wechat_actions.send_message") as send:
                QTest.mouseClick(window.settings_page.auto_find_button,
                                 Qt.MouseButton.LeftButton)
                self.pump_until(entered.is_set)
                self.assertTrue(window.wechat_busy)  # find_started=true
                self.assertIn("正在查找微信对话", window.notice.text())
                release.set()
                self.pump_until(lambda: restored.is_set() and not window.wechat_busy)
                self.assertEqual(events, ["restored", "resumed"])
                self.assertIsNone(window._resume_item_after_action)
                send.assert_not_called()
                self.assertNotIn((0x0D,), backend.key_combos())
                if title == "田子夜":
                    self.assertTrue(results[0].ok)
                    self.assertEqual(results[0].title, title)
                    self.assertIn("已切换到目标对话", window.notice.text())
                else:
                    self.assertFalse(results[0].ok)
                    self.assertEqual(results[0].reason, "title_mismatch")
                    self.assertIn("与目标不一致", window.notice.text())
        finally:
            release.set()
            restored.wait(3)
            window.close()
            self.app.processEvents()

    def test_button_success_restores_before_resuming_capture(self):
        self.run_button("田子夜")

    def test_button_similar_title_fails_closed_and_restores(self):
        self.run_button("田子夜2")
