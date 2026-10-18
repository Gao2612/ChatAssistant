import tempfile
import unittest
from pathlib import Path

from liteapp.history import HistoryStore
from liteapp.history_report import build_local_report


class HistoryReportTests(unittest.TestCase):
    def test_report_covers_every_imported_section_and_cites_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            store = HistoryStore(root / "memory.sqlite3", protect=lambda x: x[::-1], unprotect=lambda x: x[::-1])
            # T5：报告也必须在 ScopeKey 范围内生成。
            scope = store.create_person("示例客户")
            source = root / "chat.html"
            source.write_text("<p>2025-01-02 客户：预算 3000 元</p><p>2025-02-03 客户：周五交期可以吗？</p>", encoding="utf-8")
            store.import_file(scope, source)
            report = build_local_report(store, scope)
            self.assertIn("2 个文字片段", report)
            self.assertIn("2025-01", report)
            self.assertIn("2025-02", report)
            self.assertIn("预算 3000 元", report)
            self.assertIn("chat.html", report)


if __name__ == "__main__":
    unittest.main()
