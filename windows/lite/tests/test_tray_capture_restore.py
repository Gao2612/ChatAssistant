import ctypes
import ctypes.wintypes as wt
import os
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from liteapp import discovery
from liteapp.tray_capture import capture_hidden_window


def rect(hwnd):
    value = wt.RECT()
    ctypes.windll.user32.GetWindowRect(int(hwnd), ctypes.byref(value))
    return (value.left, value.top, value.right, value.bottom)


class TrayCaptureRestoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_hidden_window_geometry_and_visibility_are_restored(self):
        widget = QWidget()
        widget.resize(360, 260)
        widget.move(120, 120)
        widget.show()
        self.app.processEvents()
        widget.hide()
        self.app.processEvents()
        hwnd = int(widget.winId())
        identity = discovery.process_identity(os.getpid())
        target = discovery.WindowTarget(hwnd, identity["pid"], identity["path"],
                                        identity["created"])
        before = rect(hwnd)
        try:
            capture_hidden_window(target, settle=0.05)
            time.sleep(0.15)
            self.assertFalse(bool(ctypes.windll.user32.IsWindowVisible(hwnd)))
            self.assertEqual(rect(hwnd), before)
        finally:
            widget.close()


if __name__ == "__main__":
    unittest.main()
