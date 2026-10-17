"""T8 验收用例：确定性本地历史统计（不调用模型）。

覆盖 PLAN T8：
- `HistoryStatistics.compute(scope, snapshot) -> StatisticsReport`；片段只有片段统计，
  不把片段数冒充微信原始消息数。
- 无可靠发送人/时间时，回复时延与双方主动比例显示“不可计算”，不猜。
- 不同来源正文重复只标记“重叠来源候选”，不当作确认重复。
- 时间范围只取可靠 `sent_at`；文本中的日期只作“推断/线索”标签。
- 过期快照/跨范围快照 fail-closed；解析缺页在覆盖说明中单列。
- 报告标签区分“事实 / 推断 / 不可计算”。

仅使用合成数据与临时目录。
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from liteapp.contracts import ScopeKey
from liteapp.history import HistoryStore, ScopeMismatchError, StaleSourceVersionError
from liteapp.history_models import (
    ImportSource,
    ImportedRecord,
    PageIssue,
    SourceSnapshot,
)
from liteapp.history_statistics import (
    FACT,
    INFERENCE,
    UNAVAILABLE,
    HistoryStatistics,
    Metric,
    SourceOverlap,
    StatisticsReport,
)


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def digest(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class StatisticsTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = HistoryStore(self.folder / "memory.sqlite3",
                                  protect=xor_protect, unprotect=xor_unprotect)
        self.scope = self.store.create_person("示例客户", "account-t8")
        self.statistics = HistoryStatistics(self.store)

    def import_messages(self, scope=None, *, seed="messages", records=None,
                        page_count=0, page_issues=()):
        scope = scope or self.scope
        source = ImportSource(digest=digest(seed), kind="html", name=f"{seed}.html",
                              page_count=page_count, page_issues=page_issues)
        return self.store.import_records(scope, source, records or [])

    def compute(self, scope=None):
        scope = scope or self.scope
        snapshot = self.store.snapshot(scope)
        return self.statistics.compute(scope, snapshot)

    def message(self, ordinal, text, sender=None, sent_at=None):
        return ImportedRecord(text=text, ordinal=ordinal, record_type="message",
                              sender=sender, sent_at=sent_at)


class FragmentOnlyTests(StatisticsTestCase):
    def test_fragments_are_counted_as_fragments_not_messages(self):
        self.import_messages(records=[
            ImportedRecord(text="片段正文一：预算 3000 元", ordinal=0,
                           record_type="fragment"),
            ImportedRecord(text="片段正文二：周五交期", ordinal=1,
                           record_type="fragment"),
        ])
        report = self.compute()
        self.assertIsInstance(report, StatisticsReport)
        self.assertEqual(report.record_count, 2)
        self.assertEqual(report.fragment_count, 2)
        self.assertEqual(report.message_count, 0)
        self.assertTrue(report.fragment_only)
        self.assertEqual(report.char_count,
                         len("片段正文一：预算 3000 元") + len("片段正文二：周五交期"))
        metric = report.metric("records")
        self.assertIsNotNone(metric)
        self.assertIn("片段", metric.note)
        lines = "\n".join(report.as_lines())
        self.assertIn("片段", lines)
        self.assertIn("不等于微信原始消息数", lines)

    def test_mixed_fragments_and_messages_are_both_reported(self):
        self.import_messages(records=[
            self.message(0, "标准消息", sender="我", sent_at="2025-01-02 10:00:00"),
            ImportedRecord(text="迁移片段", ordinal=1, record_type="fragment"),
        ])
        report = self.compute()
        self.assertEqual((report.record_count, report.fragment_count,
                          report.message_count), (2, 1, 1))
        self.assertFalse(report.fragment_only)


class UncomputableMetricTests(StatisticsTestCase):
    def test_missing_sender_and_time_marks_latency_and_initiative_unavailable(self):
        self.import_messages(records=[
            ImportedRecord(text="没有发送人和时间", ordinal=0),
            ImportedRecord(text="同样没有可靠字段", ordinal=1),
        ])
        report = self.compute()
        self.assertIsNone(report.reply_latency_seconds)
        self.assertIsNone(report.initiative_ratio)
        self.assertEqual(report.sender_unknown_count, 2)
        latency = report.metric("reply_latency")
        initiative = report.metric("initiative")
        self.assertEqual(latency.kind, UNAVAILABLE)
        self.assertEqual(initiative.kind, UNAVAILABLE)
        self.assertIn("不可计算", latency.value)
        self.assertIn("不可计算", initiative.value)
        self.assertIn("发送人", latency.note)
        lines = "\n".join(report.as_lines())
        self.assertIn("[不可计算]", lines)

    def test_messages_with_sides_and_times_compute_latency_and_initiative(self):
        self.import_messages(seed="timed", records=[
            self.message(0, "你好", sender="我", sent_at="2025-01-02 10:00:00"),
            self.message(1, "您好", sender="对方", sent_at="2025-01-02 10:00:30"),
            self.message(2, "报价多少", sender="我", sent_at="2025-01-02 10:02:00"),
            self.message(3, "3000 元", sender="对方", sent_at="2025-01-02 10:03:00"),
        ])
        report = self.compute()
        self.assertEqual(report.sender_known_count, 4)
        self.assertEqual(report.sender_unknown_count, 0)
        self.assertIsNotNone(report.reply_latency_seconds)
        self.assertAlmostEqual(report.reply_latency_seconds, 60.0, places=1,
                               msg="换边时间差 30s/90s/60s 的中位数应为 60s")
        self.assertEqual(report.reply_latency_samples, 3)
        self.assertAlmostEqual(report.initiative_ratio, 0.5, places=3)
        self.assertEqual(report.speaker_shares,
                         (("我", 0.5), ("对方", 0.5)))
        self.assertEqual(report.metric("reply_latency").kind, FACT)
        self.assertEqual(report.metric("initiative").kind, FACT)

    def test_sender_without_parseable_time_still_cannot_compute_latency(self):
        self.import_messages(records=[
            self.message(0, "早", sender="我", sent_at=None),
            self.message(1, "早", sender="对方", sent_at=None),
        ])
        report = self.compute()
        self.assertIsNone(report.reply_latency_seconds)
        self.assertIn("时间", report.reply_latency_note)
        # 双方已知时“主动比例”仍可计算（不依赖时间）。
        self.assertAlmostEqual(report.initiative_ratio, 0.5, places=3)


class TimeRangeAndHintTests(StatisticsTestCase):
    def test_time_range_uses_reliable_sent_at_and_text_dates_are_only_hints(self):
        self.import_messages(seed="dated", records=[
            self.message(0, "一月消息", sender="我", sent_at="2025-01-05 09:00:00"),
            self.message(1, "二月消息 2025-02-10", sender="对方",
                         sent_at="2025-02-10 09:00:00"),
            ImportedRecord(text="片段里提到 2025-03-01 但不带可靠时间", ordinal=2),
        ])
        report = self.compute()
        self.assertEqual(report.dated_record_count, 2)
        self.assertEqual(report.undated_record_count, 1)
        self.assertEqual(report.date_hint_count, 1)
        self.assertEqual(report.time_range, ("2025-01", "2025-02"))
        self.assertEqual(report.months, (("2025-01", 1), ("2025-02", 1)))
        hint_metric = report.metric("date_hints")
        self.assertEqual(hint_metric.kind, INFERENCE)
        self.assertIn("线索", hint_metric.note)
        self.assertIn("2025-03", hint_metric.value)

    def test_no_reliable_time_marks_time_range_unavailable(self):
        self.import_messages(records=[ImportedRecord(text="没有时间的片段", ordinal=0)])
        report = self.compute()
        metric = report.metric("time_range")
        self.assertEqual(metric.kind, UNAVAILABLE)
        self.assertIn("不可计算", metric.value)


class OverlapTests(StatisticsTestCase):
    def test_duplicate_text_across_sources_is_only_a_candidate(self):
        shared = "同一段正文在两个来源里重复出现的内容"
        self.import_messages(seed="overlap-a", records=[
            ImportedRecord(text=shared, ordinal=0),
            ImportedRecord(text="甲来源独有正文", ordinal=1),
        ])
        self.import_messages(seed="overlap-b", records=[
            ImportedRecord(text=shared, ordinal=0),
            ImportedRecord(text="乙来源独有正文", ordinal=1),
        ])
        report = self.compute()
        self.assertEqual(len(report.overlaps), 1)
        overlap = report.overlaps[0]
        self.assertIsInstance(overlap, SourceOverlap)
        self.assertEqual(len(overlap.source_ids), 2)
        self.assertGreaterEqual(overlap.record_count, 2)
        self.assertIn("候选", report.overlap_note)
        self.assertIn("不是确认重复", report.overlap_note)
        metric = report.metric("overlaps")
        self.assertEqual(metric.kind, FACT)
        self.assertIn("候选", metric.note)

    def test_duplicate_text_inside_one_source_is_not_cross_source_overlap(self):
        shared = "同一来源里重复两次的正文内容"
        self.import_messages(records=[
            ImportedRecord(text=shared, ordinal=0),
            ImportedRecord(text=shared, ordinal=1),
        ])
        report = self.compute()
        self.assertEqual(report.overlaps, ())
        self.assertIn("未发现", report.overlap_note)


class CoverageAndQualityTests(StatisticsTestCase):
    def test_page_failures_and_ocr_quality_are_listed_separately(self):
        self.import_messages(
            seed="scanned", page_count=5,
            page_issues=(PageIssue(page=2, code="corrupt_page", detail="页面结构损坏"),
                         PageIssue(page=4, code="no_text", detail="无文字层")),
            records=[ImportedRecord(text="可提取页的合成正文", ordinal=0,
                                    record_type="fragment")])
        report = self.compute()
        self.assertEqual(report.page_count, 5)
        self.assertEqual(report.page_failure_count, 2)
        self.assertEqual(report.quality_note.count("2"), 1)
        self.assertIn("解析失败", report.quality_note)
        self.assertIn("不包含", report.coverage_note)
        metric = report.metric("page_failures")
        self.assertEqual(metric.kind, FACT)
        self.assertIn("2", metric.value)

    def test_empty_scope_report_is_explicit(self):
        report = self.compute()
        self.assertFalse(report.has_records)
        self.assertEqual(report.record_count, 0)
        self.assertIn("尚未导入", "\n".join(report.as_lines()))
        for key in ("records", "time_range", "reply_latency", "initiative", "overlaps"):
            self.assertIsNotNone(report.metric(key), key)


class SnapshotValidationTests(StatisticsTestCase):
    def test_stale_snapshot_is_rejected(self):
        self.import_messages(seed="stale-1", records=[
            ImportedRecord(text="第一版正文", ordinal=0)])
        stale = self.store.snapshot(self.scope)
        self.import_messages(seed="stale-2", records=[
            ImportedRecord(text="第二版正文", ordinal=0)])
        with self.assertRaises(StaleSourceVersionError):
            self.statistics.compute(self.scope, stale)

    def test_foreign_scope_snapshot_is_rejected(self):
        other = self.store.create_person("示例客户", "account-other")
        foreign = self.store.snapshot(other)
        with self.assertRaises(ScopeMismatchError):
            self.statistics.compute(self.scope, foreign)

    def test_wrong_types_are_rejected(self):
        snapshot = self.store.snapshot(self.scope)
        with self.assertRaises(TypeError):
            self.statistics.compute(self.scope.person_id, snapshot)
        with self.assertRaises(TypeError):
            self.statistics.compute(self.scope, {"not": "snapshot"})


class ReportShapeTests(StatisticsTestCase):
    def test_report_exposes_labeled_metrics_and_deterministic_lines(self):
        self.import_messages(seed="shape", records=[
            self.message(0, "可靠消息", sender="我", sent_at="2025-04-01 08:00:00"),
            ImportedRecord(text="片段正文", ordinal=1),
        ])
        report = self.compute()
        self.assertIsInstance(report, StatisticsReport)
        self.assertTrue(all(isinstance(metric, Metric) for metric in report.metrics))
        keys = [metric.key for metric in report.metrics]
        for required in ("sources", "records", "fragments", "messages", "chars",
                         "time_range", "date_hints", "speakers", "reply_latency",
                         "initiative", "overlaps", "page_failures"):
            self.assertIn(required, keys)
        lines = report.as_lines()
        self.assertTrue(any("[事实]" in line for line in lines))
        self.assertIn("2025-04", "\n".join(lines))
        self.assertEqual(report.metrics, self.compute().metrics,
                         "同一快照重复计算应完全一致")
        self.assertEqual(report.source_ids, tuple(
            source.source_id for source in self.store.snapshot(self.scope).sources))


if __name__ == "__main__":
    unittest.main()
