"""验收用例：解析器身份变化后，同一字节内容的“残缺导入”可被完整修复。

用户报告：真实导出 `田子夜.html`（1.29MB）只有 5 条占位/模板错误，完整 1854
条在伴生 `田子夜.html.json`。新增伴生 JSON 解析器后，同一个 HTML 文件字节未变，
旧逻辑会按 digest 判定“重复导入”并整份跳过，用户无法补齐历史。

本测试锁定两层修复：
* `ImportJobs` 把 probe 选中的 parser identity 写入来源（而不是共享的格式版本）；
* `HistoryStore.import_records` 在 digest 相同但 parser identity 不同时，原地
  替换旧来源的残缺记录（同 source_id、版本递增、派生物失效），而不是静默跳过；
  相同 parser 身份仍然幂等去重。
"""

import tempfile
import unittest
from pathlib import Path

from liteapp.history import HistoryStore
from liteapp.history_models import ImportedRecord
from liteapp.import_jobs import ImportJobs
from liteapp.importers.base import FormatMatch, Parser


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xA5 for value in raw)


class TruncatedHtmlParser(Parser):
    """Synthetic 'before the fix' parser: only 1 placeholder record."""

    kind = "html"
    parser_name = "truncated_html"

    def parse(self, path, cancel):
        self.page_count = 1
        self.pages_done = 1
        yield ImportedRecord(text="模板错误占位", ordinal=0, record_type="message",
                             sender="客户", sent_at="2026-01-01 10:00")


class CompanionJsonParser(Parser):
    """Synthetic 'after the fix': companion JSON carries the full messages."""

    kind = "html"
    parser_name = "exportwechat_json"

    def parse(self, path, cancel):
        self.page_count = 1
        self.pages_done = 1
        for index in range(1854):
            yield ImportedRecord(
                text=f"完整消息 {index}", ordinal=index, record_type="message",
                sender="客户" if index % 2 else "我",
                sent_at="2026-01-02 11:00",
                locator={"kind": "json", "index": index})


class ParserIdentityFallbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = HistoryStore(self.folder / "memory.sqlite3",
                                  protect=xor_protect, unprotect=xor_unprotect)
        self.scope = self.store.create_person("田子夜", "account-import")
        self.path = self.folder / "田子夜.html"
        self.path.write_text("<html><body>模板错误</body></html>", encoding="utf-8")

    def match(self, parser):
        return FormatMatch(kind="html", parser_name=parser.parser_name,
                           parser=parser, support_level="generic",
                           confidence="synthetic")

    def jobs(self, *matches):
        queue = list(matches)
        return ImportJobs(self.store, probe=lambda _path: queue.pop(0))

    def test_new_parser_identity_replaces_partial_import_and_keeps_senders(self):
        jobs = self.jobs(self.match(TruncatedHtmlParser),
                         self.match(CompanionJsonParser),
                         self.match(CompanionJsonParser))
        first = jobs.run_sync(self.scope, self.path)
        self.assertEqual(first.status, "succeeded", first.error)
        self.assertFalse(first.duplicate)
        self.assertEqual(first.record_count, 1)
        first_stats = self.store.stats(self.scope)
        self.assertEqual(first_stats.source_count, 1)
        self.assertEqual(first_stats.record_count, 1)

        fixed = jobs.run_sync(self.scope, self.path)
        self.assertEqual(fixed.status, "succeeded", fixed.error)
        self.assertFalse(fixed.duplicate)
        self.assertEqual(fixed.record_count, 1854)
        self.assertEqual(fixed.source_id, first.source_id)
        stats = self.store.stats(self.scope)
        self.assertEqual(stats.source_count, 1)
        self.assertEqual(stats.record_count, 1854)
        self.assertGreater(stats.source_version, first_stats.source_version)

        records = list(self.store.iter_records(self.scope, stats.source_version))
        self.assertEqual(len(records), 1854)
        self.assertEqual(records[0].text, "完整消息 0")
        self.assertEqual(records[-1].text, "完整消息 1853")
        self.assertEqual(records[0].sender, "我")
        self.assertEqual(records[1].sender, "客户")
        self.assertEqual(records[0].sent_at, "2026-01-02 11:00")
        self.assertEqual(records[0].locator["index"], 0)

        # Same parser identity on the same bytes remains an idempotent duplicate.
        again = jobs.run_sync(self.scope, self.path)
        self.assertEqual(again.status, "succeeded", again.error)
        self.assertTrue(again.duplicate)
        self.assertEqual(self.store.stats(self.scope).record_count, 1854)
        self.assertEqual(self.store.list_sources(self.scope)[0].parser_version,
                         "exportwechat_json")

    def test_same_parser_duplicate_is_still_skipped(self):
        jobs = self.jobs(self.match(TruncatedHtmlParser),
                         self.match(TruncatedHtmlParser))
        first = jobs.run_sync(self.scope, self.path)
        duplicate = jobs.run_sync(self.scope, self.path)
        self.assertEqual(first.status, "succeeded", first.error)
        self.assertEqual(duplicate.status, "succeeded", duplicate.error)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.source_version, first.source_version)
        self.assertEqual(self.store.stats(self.scope).record_count, 1)


if __name__ == "__main__":
    unittest.main()
