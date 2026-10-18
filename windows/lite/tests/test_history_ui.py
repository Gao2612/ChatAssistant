import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtWidgets import QApplication
from liteapp.history import HistoryStore
from liteapp.history_ui import DEFAULT_ACCOUNT_KEY, HistoryPage


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

    def test_same_name_profiles_can_be_selected_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            first = store.create_person("合成同名联系人", DEFAULT_ACCOUNT_KEY)
            second = store.create_person("合成同名联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            offered = []

            def pick_second(title, prompt, items):
                offered.extend(items)
                return items[1], True

            page._ask_choice = pick_second
            people = store.list_people(DEFAULT_ACCOUNT_KEY)
            self.assertEqual({p.scope for p in people}, {first, second})
            selected = page.choose_other_person()
            self.assertEqual(selected, people[1].scope)
            self.assertEqual(page.current_scope(), people[1].scope)
            self.assertEqual(len(set(offered)), 2)

    def test_clearing_current_contact_clears_previous_report_and_statistics(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.create_person("合成历史联系人")
            source = Path(folder) / "synthetic.html"
            source.write_text("<p>synthetic private history</p>", encoding="utf-8")
            page.import_path(source)
            page.generate_report()
            self.assertTrue(page.report.toPlainText())
            self.assertIsNotNone(page.statistics_panel.metric_row("records"))
            page.set_current_scope(None)
            self.assertIsNone(page.current_scope())
            self.assertEqual(page.records.toPlainText(), "")
            self.assertEqual(page.report.toPlainText(), "")
            self.assertIsNone(page.statistics_panel.metric_row("records"))
            self.assertEqual(page.trend_panel.tree.topLevelItemCount(), 0)


if __name__ == "__main__":
    unittest.main()
