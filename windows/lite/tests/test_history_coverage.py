"""T7 acceptance tests: deterministic batch planning and coverage accounting.

Pure-function tests only (no Qt, no network, no real chat). They pin the plan
contract used by ``history_jobs``:

* 12288-byte body batches, at most 2 overlap records / 2048 overlap bytes and a
  32768-byte request budget; limits are injected so the rules are exercised with
  small deterministic inputs;
* UTF-8 bytes (not characters) drive the limits; long records are split on
  character boundaries and keep their original record offsets;
* coverage is the union of succeeded batch ranges: overlap never double counts,
  failed/pending batches never count, and an interrupted job is never reported
  as 100%.
"""

import unittest

from liteapp.contracts import ScopeKey
from liteapp.history_jobs import (
    MAX_BATCH_BYTES,
    MAX_OVERLAP_BYTES,
    MAX_OVERLAP_RECORDS,
    MAX_REQUEST_BYTES,
    BatchCoverage,
    PlanLimits,
    batch_bytes,
    coverage_from_batches,
    plan_batches,
)
from liteapp.history_models import HistoryRecord

SCOPE = ScopeKey("account-a", "person-1")


def record(record_id, text, *, ordinal=0, source_id="source-1"):
    return HistoryRecord(record_id=record_id, source_id=source_id, ordinal=ordinal,
                         record_type="message", sender=None, sent_at=None,
                         text=text, locator=None, quality=None,
                         char_count=len(text))


def records(*texts, prefix="record"):
    return [record(f"{prefix}-{index}", text, ordinal=index)
            for index, text in enumerate(texts)]


def flattened_ids(plans):
    return [item.record_id for plan in plans for item in plan.items]


def covered_ids(plans):
    return {item.record_id for plan in plans for item in plan.items}


class PlanLimitTests(unittest.TestCase):
    def test_design_defaults_are_pinned(self):
        self.assertEqual(MAX_BATCH_BYTES, 12288)
        self.assertEqual(MAX_OVERLAP_RECORDS, 2)
        self.assertEqual(MAX_OVERLAP_BYTES, 2048)
        self.assertEqual(MAX_REQUEST_BYTES, 32768)

    def test_default_limits_cover_every_record_once_per_batch_position(self):
        items = records(*[f"第{index}条合成消息，" + "内容" * 30 for index in range(20)])
        plans = plan_batches(items)
        self.assertGreaterEqual(len(plans), 1)
        self.assertEqual([plan.batch_index for plan in plans], list(range(len(plans))))
        self.assertEqual(covered_ids(plans), {item.record_id for item in items})
        for plan in plans:
            self.assertLessEqual(plan.text_bytes, MAX_BATCH_BYTES)
            self.assertLessEqual(plan.request_bytes, MAX_REQUEST_BYTES)
            self.assertGreater(len(plan.items), 0)

    def test_utf8_bytes_drive_the_body_limit(self):
        # 100 Chinese characters = 300 UTF-8 bytes, so only one record fits in 400 bytes.
        text = "中" * 100
        items = records(*[text for _ in range(5)], prefix="zh")
        plans = plan_batches(items, limits=PlanLimits(max_batch_bytes=400,
                                                      max_overlap_records=0))
        for plan in plans:
            self.assertLessEqual(plan.text_bytes, 400)
            self.assertEqual(len(plan.items), 1)

    def test_overlap_is_capped_to_two_records_and_2048_bytes(self):
        items = records(*[f"消息{index}：" + "字" * 60 for index in range(8)])
        limits = PlanLimits(max_batch_bytes=520, max_overlap_records=2,
                            max_overlap_bytes=2048)
        plans = plan_batches(items, limits=limits)
        self.assertGreater(len(plans), 2)
        for previous, current in zip(plans, plans[1:]):
            previous_ids = {item.record_id for item in previous.items}
            shared = [item for item in current.items if item.record_id in previous_ids]
            self.assertLessEqual(len(shared), 2)
            self.assertLessEqual(batch_bytes(shared), 2048)

    def test_one_record_never_advances_to_an_endless_overlap(self):
        items = records("只有一条合成记录")
        plans = plan_batches(items, limits=PlanLimits(max_batch_bytes=1000,
                                                      max_overlap_records=2,
                                                      max_overlap_bytes=2048))
        self.assertEqual(len(plans), 1)
        self.assertEqual(len(plans[0].items), 1)

    def test_long_record_is_split_on_character_boundaries_without_gaps(self):
        text = "".join(chr(0x4E00 + (index % 500)) for index in range(1200))
        item = record("long-record", text)
        plans = plan_batches([item], limits=PlanLimits(max_batch_bytes=320,
                                                       max_overlap_records=0))
        segments = [entry for plan in plans for entry in plan.items
                    if entry.record_id == "long-record"]
        self.assertGreater(len(segments), 1)
        self.assertEqual("".join(segment.text for segment in segments), text)
        self.assertEqual(segments[0].start, 0)
        self.assertEqual(segments[-1].end, len(text))
        for previous, current in zip(segments, segments[1:]):
            self.assertEqual(previous.end, current.start)
        for segment in segments:
            self.assertEqual(segment.record_char_count, len(text))
            self.assertEqual(segment.text, text[segment.start:segment.end])
            self.assertLessEqual(len(segment.text.encode("utf-8")), 320)

    def test_shrinking_for_the_request_budget_never_drops_records(self):
        items = records(*[f"记录{index}：" + "内容" * 15 for index in range(10)])
        limits = PlanLimits(max_batch_bytes=MAX_BATCH_BYTES,
                            max_request_bytes=1200, max_overlap_records=0)
        plans = plan_batches(items, limits=limits)
        self.assertGreater(len(plans), 1)
        for plan in plans:
            self.assertLessEqual(plan.request_bytes, 1200)
        self.assertEqual(covered_ids(plans), {item.record_id for item in items})
        for item in items:
            ranges = sorted((entry.start, entry.end) for plan in plans
                            for entry in plan.items if entry.record_id == item.record_id)
            joined = 0
            for start, end in ranges:
                self.assertLessEqual(start, joined)
                joined = max(joined, end)
            self.assertEqual(joined, len(item.text))

    def test_every_item_stays_inside_its_record_range(self):
        items = records(*[f"范围检查{index}：" + "字" * 80 for index in range(4)])
        plans = plan_batches(items, limits=PlanLimits(max_batch_bytes=300,
                                                      max_overlap_records=0))
        by_id = {item.record_id: item for item in items}
        for plan in plans:
            for entry in plan.items:
                source = by_id[entry.record_id]
                self.assertGreaterEqual(entry.start, 0)
                self.assertLessEqual(entry.end, len(source.text))
                self.assertLess(entry.start, entry.end)
                self.assertEqual(entry.text, source.text[entry.start:entry.end])

    def test_plan_is_deterministic_for_the_same_input(self):
        items = records(*[f"确定性{index}：" + "字" * 50 for index in range(5)])
        limits = PlanLimits(max_batch_bytes=400, max_overlap_records=1)
        first = plan_batches(items, limits=limits)
        second = plan_batches(items, limits=limits)
        self.assertEqual(len(first), len(second))
        for left, right in zip(first, second):
            self.assertEqual([(item.record_id, item.start, item.end, item.text)
                              for item in left.items],
                             [(item.record_id, item.start, item.end, item.text)
                              for item in right.items])

    def test_first_middle_and_last_planted_facts_land_in_a_batch(self):
        texts = [f"第{index}条普通记录" + "填充" * 10 for index in range(1000)]
        texts[0] = "首部事实A：客户要求周五前报价。" + "填充" * 10
        texts[500] = "中部事实B：客户预算大约五万元。" + "填充" * 10
        texts[999] = "尾部事实C：客户下周安排见面。" + "填充" * 10
        items = records(*texts)
        plans = plan_batches(items, limits=PlanLimits(max_batch_bytes=600,
                                                      max_overlap_records=0))
        for token, index in (("首部事实A", 0), ("中部事实B", 500), ("尾部事实C", 999)):
            record_id = f"record-{index}"
            containing = [plan for plan in plans
                          if any(entry.record_id == record_id and token in entry.text
                                 for entry in plan.items)]
            self.assertEqual(len(containing), 1, token)


class CoverageTests(unittest.TestCase):
    def test_overlapping_ranges_are_not_double_counted(self):
        record_chars = {"r1": 10, "r2": 10, "r3": 5}
        batches = [
            BatchCoverage("succeeded", (("r1", 0, 10), ("r2", 0, 10))),
            BatchCoverage("succeeded", (("r2", 0, 10), ("r3", 0, 5))),
        ]
        coverage = coverage_from_batches(record_chars, batches)
        self.assertEqual(coverage.covered_chars, 25)
        self.assertEqual(coverage.total_chars, 25)
        self.assertEqual(coverage.succeeded_records, 3)
        self.assertAlmostEqual(coverage.ratio, 1.0)

    def test_partial_ranges_only_count_the_succeeded_union(self):
        record_chars = {"r1": 10, "r2": 10}
        batches = [
            BatchCoverage("succeeded", (("r1", 0, 10),)),
            BatchCoverage("failed", (("r2", 0, 10),)),
            BatchCoverage("pending", ()),
        ]
        coverage = coverage_from_batches(record_chars, batches)
        self.assertEqual(coverage.covered_chars, 10)
        self.assertEqual(coverage.succeeded_records, 1)
        self.assertAlmostEqual(coverage.ratio, 0.5)

    def test_interrupted_job_never_reports_one_hundred_percent(self):
        record_chars = {f"r{index}": 20 for index in range(5)}
        batches = [BatchCoverage("succeeded", ((f"r{index}", 0, 20),))
                   for index in range(2)]
        batches += [BatchCoverage("failed", (("r2", 0, 20),)),
                    BatchCoverage("pending", ())]
        coverage = coverage_from_batches(record_chars, batches)
        self.assertLess(coverage.ratio, 1.0)
        self.assertEqual(coverage.succeeded_records, 2)

    def test_no_batches_means_zero_coverage(self):
        coverage = coverage_from_batches({"r1": 5}, [])
        self.assertEqual(coverage.covered_chars, 0)
        self.assertEqual(coverage.ratio, 0.0)
        self.assertEqual(coverage.succeeded_records, 0)

    def test_ten_thousand_record_plan_covers_every_character(self):
        items = [record(f"bulk-{index}", f"第{index}条" + "字" * 30, ordinal=index)
                 for index in range(10_000)]
        plans = plan_batches(items, limits=PlanLimits(max_batch_bytes=4096,
                                                      max_overlap_records=0))
        total_chars = sum(len(item.text) for item in items)
        batches = [BatchCoverage("succeeded", tuple(
            (entry.record_id, entry.start, entry.end) for entry in plan.items))
            for plan in plans]
        coverage = coverage_from_batches({item.record_id: len(item.text) for item in items},
                                         batches)
        self.assertEqual(coverage.total_chars, total_chars)
        self.assertEqual(coverage.covered_chars, total_chars)
        self.assertEqual(coverage.succeeded_records, len(items))
        self.assertAlmostEqual(coverage.ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
