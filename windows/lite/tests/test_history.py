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

    def test_explicit_account_profiles_keep_identity_and_isolate_contacts(self):
        profiles = self.store.list_accounts()
        legacy = next(profile for profile in profiles
                      if profile.account_id == self.scope.wechat_account_id)
        self.assertTrue(legacy.legacy)
        self.assertEqual(legacy.label, "account-a")

        account = self.store.create_account("工作微信")
        self.assertFalse(account.legacy)
        contact = self.store.create_person_in_account(account.account_id, "同名客户")
        self.assertEqual(contact.wechat_account_id, account.account_id)
        account_people = self.store.list_people_in_account(account.account_id)
        self.assertEqual(len(account_people), 1)
        self.assertEqual(account_people[0].scope, contact)
        self.assertEqual(self.store.list_people_in_account(
            self.scope.wechat_account_id)[0].scope, self.scope)

        renamed = self.store.rename_account(account.account_id, "个人微信")
        self.assertEqual(renamed.account_id, account.account_id)
        self.assertEqual(renamed.label, "个人微信")
        self.assertFalse(renamed.legacy)
        self.assertEqual(self.store.list_people_in_account(account.account_id)[0].scope,
                         contact)

    def test_explicit_account_profile_rejects_duplicate_or_legacy_rename(self):
        account = self.store.create_account("工作微信")
        with self.assertRaises(ValueError):
            self.store.create_account(" 工作微信 ")
        legacy = next(profile for profile in self.store.list_accounts()
                      if profile.account_id == self.scope.wechat_account_id)
        with self.assertRaises(ValueError):
            self.store.rename_account(legacy.account_id, "旧数据")

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

    def test_same_digest_with_new_parser_identity_replaces_partial_source(self):
        """A parser fix (same file bytes) replaces the partial source in place."""
        from liteapp.history_models import ImportSource, ImportedRecord

        source = ImportSource(digest="a" * 64, kind="html", name="田子夜.html",
                              parser_version="old_truncated_html")
        first = self.store.import_records(self.scope, source, [
            ImportedRecord(text="残缺占位一", ordinal=0, record_type="message",
                           sender="客户"),
        ])
        self.assertFalse(first.duplicate)
        old_source_version = self.store.stats(self.scope).source_version
        self.assertEqual(self.store.stats(self.scope).record_count, 1)

        fixed_source = ImportSource(digest="a" * 64, kind="html", name="田子夜.html",
                                    parser_version="exportwechat_json")
        fixed_records = [
            ImportedRecord(text=f"完整消息 {index}", ordinal=index,
                           record_type="message", sender="客户",
                           sent_at=f"2026-01-0{index % 9 + 1} 10:00")
            for index in range(1854)
        ]
        fixed = self.store.import_records(self.scope, fixed_source, fixed_records)
        self.assertFalse(fixed.duplicate)
        self.assertEqual(fixed.source_id, first.source_id)
        self.assertEqual(fixed.record_count, 1854)
        stats = self.store.stats(self.scope)
        self.assertEqual(stats.source_count, 1)
        self.assertEqual(stats.record_count, 1854)
        self.assertGreater(stats.source_version, old_source_version)
        rows = list(self.store.iter_records(self.scope, stats.source_version))
        self.assertEqual(len(rows), 1854)
        self.assertEqual(rows[0].text, "完整消息 0")
        self.assertEqual(rows[-1].text, "完整消息 1853")
        self.assertEqual(rows[0].sent_at, "2026-01-01 10:00")
        self.assertEqual(self.store.list_sources(self.scope)[0].parser_version,
                         "exportwechat_json")

        # Re-running the same fixed parser remains idempotent.
        again = self.store.import_records(self.scope, fixed_source, fixed_records)
        self.assertTrue(again.duplicate)
        self.assertEqual(self.store.stats(self.scope).record_count, 1854)

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
