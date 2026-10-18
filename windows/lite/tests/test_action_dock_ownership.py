import os
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from liteapp.ui import MainWindow
from tests.test_window_manager_ui import FakeWindowManager, fake_target, make_config
from tests.test_wechat_find import FakeBackend, FakeReader, WECHAT_HWND, frame, script
from liteapp.wechat_find import find_conversation


class ActionDockOwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_busy_action_prevents_timer_from_reattaching_native_window(self):
        manager = FakeWindowManager()
        window = MainWindow(config=make_config(dock_wechat=True), scanning=False,
                            window_manager=manager)
        window.dock_timer.stop()
        window.window_item = {'id': 'window-1', 'hwnd': fake_target()}
        window.capture_session = 'session-1'
        window.show()
        self.app.processEvents()
        manager.detach()  # stop_capture() releases docking before the action.
        window.wechat_busy = True
        manager.calls.clear()
        try:
            with patch.object(window, '_aperture_rect', return_value=(120,120,780,600)), \
                 patch.object(window, '_host_hwnd', return_value=1001), \
                 patch.object(window, '_aperture_hwnd', return_value=1002):
                window.sync_dock()
            self.assertEqual(manager.named('attach'), [])
            self.assertEqual(manager.named('sync'), [])
            self.assertEqual(manager.named('set_view'), [])
            self.assertFalse(window._aperture_open)
        finally:
            window.close()
            self.app.processEvents()


class FinderNoSendTests(unittest.TestCase):
    def test_selected_result_never_presses_enter_into_existing_draft(self):
        backend = FakeBackend()
        reader = FakeReader(script([('Q搜索',98,58)], [('田子夜',145,115)],
                                   [('田子夜',390,56)]))
        with patch('liteapp.wechat_find.time.sleep'), \
             patch('liteapp.wechat_find._foreground_ok', return_value=True):
            result = find_conversation(WECHAT_HWND, '田子夜', backend=backend,
                reader=reader, capture=lambda hwnd:(frame(),1200,800), settle=0)
        self.assertTrue(result.ok, result.reason)
        self.assertNotIn(('keys',(0x0D,)), backend.calls)
