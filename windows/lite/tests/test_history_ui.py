import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtWidgets import QApplication
from liteapp.history import HistoryStore
from liteapp.history_ui import HistoryPage


class HistoryPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_create_import_browse_and_search(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            store = HistoryStore(path / "memory.sqlite3", protect=lambda x: x[::-1], unprotect=lambda x: x[::-1])
            page = HistoryPage(store=store)
            person_id = page.create_person("示例客户", "账号 A")
            self.assertEqual(page.persons.currentData(), person_id)
            source = path / "chat.html"
            source.write_text("<html><body><p>客户：周五可以交货吗？</p><p>我：确认后回复。</p></body></html>", encoding="utf-8")
            page.import_path(source)
            self.assertIn("周五可以交货", page.records.toPlainText())
            self.assertIn("2", page.summary.text())
            page.query.setText("交货")
            page.search_records()
            self.assertIn("周五可以交货", page.records.toPlainText())
            self.assertNotIn("确认后回复", page.records.toPlainText())
            page.generate_report()
            self.assertIn("2 个文字片段", page.report.toPlainText())
            page.close()


if __name__ == "__main__":
    unittest.main()
