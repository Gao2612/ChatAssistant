import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
from liteapp.ui import MainWindow
from tests.test_mirror_preview import FakeWindowManager, make_config


class PreviewZoomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_preview_can_show_readable_full_size_and_fit_without_native_resize(self):
        manager = FakeWindowManager()
        window = MainWindow(config=make_config(dock_wechat=False), scanning=False, window_manager=manager)
        self.addCleanup(window.close)
        window.poll.stop()
        window.dock_timer.stop()
        window.resize(1100,600)
        window.show()
        self.app.processEvents()
        image = QImage(1000,700,QImage.Format.Format_RGB32)
        image.fill(QColor('white'))
        data = QBuffer()
        data.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(data,'PNG')
        window.show_preview(bytes(data.data()))
        self.app.processEvents()
        self.assertEqual(window.preview.pixmap().width(),1000)
        self.assertGreater(window.preview_scroll.verticalScrollBar().maximum(),0)
        window.preview_zoom.setCurrentIndex(window.preview_zoom.findData(0))
        self.app.processEvents()
        self.assertLessEqual(window.preview.pixmap().height(),window.preview_scroll.viewport().height())
        self.assertEqual(manager.named('attach'),[])
