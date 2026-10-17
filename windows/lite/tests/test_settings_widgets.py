import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication, QComboBox

from liteapp.ui import NoWheelComboBox, SettingsPage

CONFIG = {
    "wechat_path": "", "backend": "api", "base_url": "https://example.test/v1",
    "model": "test-model", "api_model": "test-model", "api_key": "synthetic",
    "style": "自然", "auto_connect": False, "auto_analysis": True,
    "subscription": None, "subscription_models": [],
    "reading_backend": "ocr", "reading_base_url": "", "reading_model": "",
    "reading_api_key": "", "codex_path": "",
}


def wheel_event():
    return QWheelEvent(QPointF(4, 4), QPointF(4, 4), QPoint(0, 0), QPoint(0, 120),
                       Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                       Qt.ScrollPhase.NoScrollPhase, False)


class NoWheelComboTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_all_settings_dropdowns_ignore_wheel_and_show_all_rows(self):
        page = SettingsPage(dict(CONFIG), None)
        combos = page.findChildren(QComboBox)
        self.assertGreaterEqual(len(combos), 4)
        for combo in combos:
            self.assertIsInstance(combo, NoWheelComboBox)
            self.assertGreaterEqual(combo.maxVisibleItems(), 20)
        page.close()

    def test_wheel_does_not_change_selection(self):
        combo = NoWheelComboBox()
        combo.addItem("A", "a")
        combo.addItem("B", "b")
        combo.setCurrentIndex(0)
        for _ in range(4):
            combo.wheelEvent(wheel_event())
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(combo.currentData(), "a")


if __name__ == "__main__":
    unittest.main()
