import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QWidget

from liteapp import discovery
from liteapp.tray_capture import capture_hidden_window


class TrayCaptureGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _target_for(self, widget):
        hwnd = int(widget.winId())
        identity = discovery.process_identity(os.getpid())
        return discovery.WindowTarget(hwnd, identity["pid"], identity["path"],
                                      identity["created"])

    def test_visible_window_is_not_captured_by_tray_path(self):
        widget = QWidget()
        widget.resize(120, 80)
        widget.show()
        self.app.processEvents()
        try:
            target = self._target_for(widget)
            self.assertIsNone(capture_hidden_window(target))
        finally:
            widget.close()

    def test_invalid_target_fails_closed(self):
        self.assertIsNone(capture_hidden_window(None))
        self.assertIsNone(capture_hidden_window(12345))


if __name__ == "__main__":
    unittest.main()
