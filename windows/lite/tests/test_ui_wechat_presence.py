import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp.ui import MainWindow

CONFIG = {
    "wechat_path": "", "base_url": "https://example.test/v1", "backend": "api",
    "model": "api-model", "api_model": "api-model", "api_key": "synthetic",
    "style": "自然", "auto_connect": False, "auto_analysis": False,
    "subscription": None, "subscription_models": [], "_warning": "",
    "reading_backend": "ocr", "reading_api_key": "", "reading_base_url": "",
    "reading_model": "", "codex_path": "", "chatgpt_model": "",
}


class LaunchWechatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.exe = Path(self.folder.name) / "Weixin.exe"
        self.exe.write_bytes(b"synthetic")
        self.window = MainWindow(config=dict(CONFIG, wechat_path=str(self.exe)),
                                 scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()
        self.window.scanner.stop()
        self.window.settings_page.path.setText(str(self.exe))

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_running_wechat_is_restored_and_never_launched_twice(self):
        with patch("liteapp.ui.wechat_presence",
                   return_value={"running": True, "restorable": True}), \
                patch("liteapp.ui.find_wechat_windows",
                      return_value=[{"hidden": True, "hwnd": 1}]), \
                patch("liteapp.ui.restore_window", return_value=True) as restore, \
                patch("liteapp.ui.subprocess.Popen") as popen:
            self.window.launch_wechat()
        popen.assert_not_called()
        restore.assert_called_once()
        self.assertIn("已恢复", self.window.notice.text())

    def test_running_but_not_restorable_prompts_tray_without_launch(self):
        with patch("liteapp.ui.wechat_presence", return_value={"running": True}), \
                patch("liteapp.ui.find_wechat_windows", return_value=[]), \
                patch("liteapp.ui.restore_window", return_value=False), \
                patch("liteapp.ui.subprocess.Popen") as popen:
            self.window.launch_wechat()
        popen.assert_not_called()
        self.assertIn("不会重复启动", self.window.notice.text())

    def test_no_running_process_starts_wechat_once(self):
        with patch("liteapp.ui.wechat_presence", return_value={"running": False}), \
                patch("liteapp.ui.subprocess.Popen") as popen:
            self.window.launch_wechat()
        popen.assert_called_once()
        self.assertIn("已启动", self.window.notice.text())

    def test_scan_reports_hidden_running_wechat_instead_of_waiting(self):
        with patch("liteapp.ui.discover", return_value=[]), \
                patch("liteapp.ui.wechat_presence", return_value={"running": True}):
            self.window.scan()
        self.assertIn("窗口不可见", self.window.connection_status.text())


if __name__ == "__main__":
    unittest.main()
