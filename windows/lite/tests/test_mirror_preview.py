import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.ui import MainWindow
from tests.test_window_manager_ui import (
    FakeWindowManager, fake_target, make_config, patch_capture_process,
)


class MirrorPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = FakeWindowManager()
        self.window = MainWindow(config=make_config(dock_wechat=False), scanning=False,
                                 window_manager=self.manager)
        self.app.processEvents()
        self.window.dock_timer.stop()
        self.window.window_item = {"id": "window-1", "hwnd": fake_target()}
        self.window.capture_session = "session-1"
        self.window.show()

    def tearDown(self):
        try:
            self.window.close()
        except Exception:  # noqa: BLE001
            pass
        self.app.processEvents()

    def _sync(self):
        with patch.object(self.window, "_aperture_rect", return_value=(120, 120, 780, 600)), \
                patch.object(self.window, "_host_hwnd", return_value=1001), \
                patch.object(self.window, "_aperture_hwnd", return_value=1002):
            self.app.processEvents()
            self.window.sync_dock()

    def test_mirror_mode_never_attaches_or_moves_native_window(self):
        self._sync()
        self.assertEqual(self.manager.named("attach"), [])
        self.assertEqual(self.manager.named("sync"), [])
        self.assertEqual(self.manager.named("set_view"), [])
        self.assertFalse(self.window._aperture_open)

    def test_turning_docking_off_detaches_once(self):
        self.manager.attached = True
        self._sync()
        self.assertEqual(len(self.manager.named("detach")), 1)

    def test_legacy_config_without_key_keeps_docking_behaviour(self):
        manager = FakeWindowManager()
        window = MainWindow(config=make_config(), scanning=False,
                            window_manager=manager)
        self.app.processEvents()
        window.dock_timer.stop()
        window.window_item = {"id": "window-1", "hwnd": fake_target()}
        window.capture_session = "session-1"
        window.show()
        try:
            with patch.object(window, "_aperture_rect", return_value=(120, 120, 780, 600)), \
                    patch.object(window, "_host_hwnd", return_value=1001), \
                    patch.object(window, "_aperture_hwnd", return_value=1002):
                self.app.processEvents()
                window.sync_dock()
            self.assertEqual(len(manager.named("attach")), 1)
        finally:
            window.close()
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
