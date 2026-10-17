import tempfile
import unittest
from pathlib import Path

from liteapp.history import HistoryStore, HistoryImportError


class HistoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder / "memory.sqlite3"
        self.store = HistoryStore(
            self.db,
            protect=lambda raw: bytes(value ^ 0xA5 for value in raw),
            unprotect=lambda raw: bytes(value ^ 0xA5 for value in raw),
        )
        # T5：历史数据 V2 的所有读写都必须携带 ScopeKey。
        self.scope = self.store.create_person("示例客户", "account-a")

    def test_html_import_is_local_plain_text_and_repeat_is_idempotent(self):
        html = self.folder / "sample.html"
        html.write_text(
            '<html><head><title>会话</title><script>fetch("https://evil.test")</script></head>'
            '<body><div>张三 2025-01-02 10:00</div><div>请问设备还有现货吗？</div>'
            '<div>我：有现货。</div><img alt="图片" src="https://evil.test/a.png"></body></html>',
            encoding="utf-8",
        )
        first = self.store.import_file(self.scope, html)
        again = self.store.import_file(self.scope, html)
        self.assertFalse(first["duplicate"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(first["source_id"], again["source_id"])
        text = "\n".join(row["text"] for row in self.store.list_messages(self.scope))
        self.assertIn("请问设备还有现货吗", text)
        self.assertNotIn("fetch", text)
        self.assertNotIn("https://evil.test", text)
        self.assertNotIn("现货", self.db.read_bytes().decode("utf-8", errors="ignore"))
        self.assertEqual(self.store.stats(self.scope).source_count, 1)

    def test_person_and_account_isolation_search_and_delete(self):
        html = self.folder / "sample.html"
        html.write_text("<div>只属于甲：交付日期星期五</div>", encoding="utf-8")
        imported = self.store.import_file(self.scope, html)
        other = self.store.create_person("示例客户", "account-b")
        self.assertEqual(len(self.store.list_people("account-a")), 1)
        self.assertEqual(len(self.store.list_people("account-b")), 1)
        self.assertEqual(len(self.store.search(self.scope, "交付日期")), 1)
        self.assertEqual(self.store.search(other, "交付日期"), [])
        self.assertIn("交付日期", self.store.build_context(self.scope))
        self.assertEqual(self.store.build_context(other), "")
        self.store.delete_source(self.scope, imported["source_id"])
        self.assertEqual(self.store.list_messages(self.scope), [])

    def test_html_unsupported_or_empty_does_not_add_source(self):
        bad = self.folder / "bad.html"
        bad.write_text("<script>ignored()</script>", encoding="utf-8")
        with self.assertRaises(HistoryImportError):
            self.store.import_file(self.scope, bad)
        self.assertEqual(self.store.list_sources(self.scope), [])
        txt = self.folder / "bad.txt"
        txt.write_text("hello", encoding="utf-8")
        with self.assertRaises(HistoryImportError):
            self.store.import_file(self.scope, txt)

    def test_pdf_password_corrupt_and_scanned_are_explained(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf is installed by requirements.txt during product build")
        encrypted = self.folder / "encrypted.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        writer.encrypt("password")
        with encrypted.open("wb") as out:
            writer.write(out)
        with self.assertRaisesRegex(HistoryImportError, "密码"):
            self.store.import_file(self.scope, encrypted)
        scanned = self.folder / "scanned.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        with scanned.open("wb") as out:
            writer.write(out)
        with self.assertRaisesRegex(HistoryImportError, "文字|OCR"):
            self.store.import_file(self.scope, scanned)
        corrupt = self.folder / "corrupt.pdf"
        corrupt.write_bytes(b"not a pdf")
        with self.assertRaisesRegex(HistoryImportError, "损坏|无法"):
            self.store.import_file(self.scope, corrupt)


if __name__ == "__main__":
    unittest.main()
