"""T7 acceptance tests: resumable full-history model analysis jobs.

A deterministic ``FakeGateway`` behaves like the T3 gateway (it validates output
through ``inference_schemas.validate_content`` with the frozen batch records), so
evidence ranges, batch boundaries and coverage are checked exactly. A small
loopback model server is used for one end-to-end run through the real
``InferenceGateway``. Only synthetic conversations are used; a run with a real
configured model is recorded separately in ``_reports/T7_report.md``.
"""
import hashlib
import json
import tempfile
import threading
import time
import unittest
from dataclasses import fields
from pathlib import Path

from liteapp import inference_schemas
from liteapp.contracts import CancelledError, EvidenceRef, ScopeKey
from liteapp.history import HistoryStore, StaleSourceVersionError
from liteapp.history_models import ImportSource, ImportedRecord, PageIssue
from liteapp.history_analysis_store import AnalysisStore
from liteapp.history_jobs import (
    HistoryJobError,
    HistoryJobs,
    HistoryReport,
    JobProgress,
    PlanLimits,
    ReportItem,
)
from liteapp.inference import GatewayProfile, InferenceGateway
from liteapp.inference_queue import InferenceQueue


def xor_protect(raw):
    return bytes(value ^ 0x5A for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0x5A for value in raw)


def digest(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


TOKEN_CATEGORIES = (("事实A", "fact"), ("事实B", "fact"), ("事实C", "fact"),
                    ("推断A", "inference"), ("待确认A", "open_question"))


class FakeGateway:
    """Deterministic model double that validates output exactly like T3 does."""

    def __init__(self):
        self.calls = []
        self.fail_once = {}
        self.fail_always = {}
        self.bad_evidence_calls = set()
        self.lock = threading.Lock()

    def fail_on_call(self, index, error):
        self.fail_once[index] = error

    def run(self, request, cancel=None):
        if cancel is not None:
            cancel.raise_if_cancelled()
        with self.lock:
            index = len(self.calls)
            self.calls.append(request)
        error = self.fail_once.pop(index, None) or self.fail_always.get(index)
        if error is not None:
            raise error
        content = self._content(request, bad_evidence=index in self.bad_evidence_calls)
        validated = inference_schemas.validate_content(request.kind, content, request)
        return inference_schemas.InferenceResult(
            validated_data=validated, request_id=f"fake-{index}", elapsed=0.0,
            usage=None)

    def extract_calls(self):
        return [request for request in self.calls
                if request.kind == inference_schemas.KIND_HISTORY_EXTRACT]

    @staticmethod
    def _content(request, *, bad_evidence=False):
        if request.kind == inference_schemas.KIND_HISTORY_EXTRACT:
            items = []
            for row in request.input_data["records"]:
                record_id, text = row["record_id"], row["text"]
                for token, category in TOKEN_CATEGORIES:
                    position = text.find(token)
                    if position < 0:
                        continue
                    if bad_evidence:
                        items.append({"category": category, "text": token + "（合成）",
                                      "evidence": [{"record_id": "record-outside",
                                                    "start": 0, "end": 2}]})
                    else:
                        items.append({"category": category, "text": token + "（合成）",
                                      "evidence": [{"record_id": record_id,
                                                    "start": position,
                                                    "end": position + len(token)}]})
            if not items:
                row = request.input_data["records"][0]
                items.append({"category": "fact", "text": "合成事实",
                              "evidence": [{"record_id": row["record_id"],
                                            "start": 0, "end": len(row["text"])}]})
            return json.dumps({"items": items}, ensure_ascii=False)
        payload = json.loads(request.input_data["input"])
        items = payload.get("items", [])
        return json.dumps({"summary": f"合成档案（{len(items)} 条）",
                           "items": [{"category": item["category"],
                                      "text": item["text"],
                                      "evidence": item["evidence"]} for item in items]},
                          ensure_ascii=False)


class _JobTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = HistoryStore(self.folder / "memory.sqlite3",
                                  protect=xor_protect, unprotect=xor_unprotect)
        self.scope = self.store.create_person("合成客户", "account-a")
        self.analysis_store = AnalysisStore(self.folder / "analysis.sqlite3",
                                            protect=xor_protect,
                                            unprotect=xor_unprotect)
        self.gateway = FakeGateway()

    def import_records(self, *texts, seed="file-1", page_issues=(), page_count=0):
        source = ImportSource(digest=digest(seed), kind="html", name="合成记录.html",
                              page_issues=page_issues, page_count=page_count)
        records = [ImportedRecord(text=text, ordinal=index)
                   for index, text in enumerate(texts)]
        return self.store.import_records(self.scope, source, records)

    def jobs(self, gateway=None, *, limits=None, **kwargs):
        kwargs.setdefault("auto_start", False)
        kwargs.setdefault("model_version", "model-a")
        gateway = gateway or self.gateway
        if "queue" not in kwargs:
            return HistoryJobs(self.store, self.analysis_store, gateway,
                               limits=limits, **kwargs)
        queue = kwargs.pop("queue")
        return HistoryJobs(self.store, self.analysis_store, gateway, queue=queue,
                           limits=limits, **kwargs)

    def source_version(self):
        return self.store.snapshot(self.scope).source_version

    def run_job(self, jobs, *, profile="profile-1"):
        job_id = jobs.start(self.scope, profile)
        jobs.run_pending()
        return job_id

    def assert_completed(self, jobs, job_id):
        progress = jobs.progress(job_id)
        self.assertEqual(progress.status, "completed", progress.message)
        self.assertEqual(progress.failed_batches, 0)
        self.assertEqual(progress.pending_batches, 0)
        return progress


TEXTS = [
    "合成对话1：客户说事实A，请周五前报价。",
    "合成对话2：推断A客户预算充足，可能还要样品。",
    "合成对话3：待确认A是否需要开发票。",
    "合成对话4：事实B客户要求包邮到杭州。",
    "合成对话5：事实C客户下周三安排线下见面。",
    "合成对话6：收尾寒暄，没有新承诺。",
]


class JobCompletionTests(_JobTestCase):
    def test_cancel_and_publish_are_serialized_without_overwriting_terminal_state(self):
        snapshot = self.store.snapshot(self.scope)
        payload = {"summary": "synthetic", "items": [], "timeline": []}
        jobs = self.jobs()

        for index in range(8):
            job = self.analysis_store.create_job(
                self.scope, f"profile-{index}", "model-a", "prompt-a",
                snapshot.source_version, snapshot.memory_version,
                total_batches=0, total_records=0, total_chars=0,
                parse_missing_pages=0, page_issue_count=0, records=[],
                status="running", stage="reduce")
            gate = threading.Barrier(3)
            results = {}

            def cancel_job():
                gate.wait()
                results["cancel"] = jobs.cancel(job.job_id)

            def publish_job():
                gate.wait()
                results["publish"] = self.analysis_store.save_report_and_complete(
                    job.job_id, payload)

            threads = [threading.Thread(target=cancel_job),
                       threading.Thread(target=publish_job)]
            for thread in threads:
                thread.start()
            gate.wait()
            for thread in threads:
                thread.join(timeout=5)
                self.assertFalse(thread.is_alive())

            final = self.analysis_store.get_job(job.job_id)
            self.assertIn(final.status, {"cancelled", "completed"})
            self.assertEqual(results["cancel"].status, final.status)
            self.assertEqual(results["publish"].status, final.status)
            with self.analysis_store._connect() as db:
                report_count = db.execute(
                    "SELECT COUNT(*) FROM analysis_reports WHERE job_id=?",
                    (job.job_id,)).fetchone()[0]
            if final.status == "cancelled":
                self.assertEqual(report_count, 0)
            else:
                self.assertEqual(report_count, 1)

    def test_late_worker_pause_cannot_overwrite_cancellation(self):
        snapshot = self.store.snapshot(self.scope)
        job = self.analysis_store.create_job(
            self.scope, "pause-race", "model-a", "prompt-a",
            snapshot.source_version, snapshot.memory_version,
            total_batches=0, total_records=0, total_chars=0,
            parse_missing_pages=0, page_issue_count=0, records=[],
            status="running", stage="extract")
        jobs = self.jobs()
        gate = threading.Barrier(3)
        outcomes = {}

        def cancel():
            gate.wait()
            outcomes["cancel"] = jobs.cancel(job.job_id)

        def pause_from_worker_failure():
            gate.wait()
            outcomes["pause"] = self.analysis_store.pause_job(
                job.job_id, "模拟模型请求晚到失败。")

        threads = [threading.Thread(target=cancel),
                   threading.Thread(target=pause_from_worker_failure)]
        for thread in threads:
            thread.start()
        gate.wait()
        for thread in threads:
            thread.join(timeout=5)
            self.assertFalse(thread.is_alive())

        self.assertEqual(self.analysis_store.get_job(job.job_id).status, "cancelled")
        self.assertEqual(outcomes["cancel"].status, "cancelled")

    def test_cancelled_job_rejects_late_batch_commit(self):
        self.import_records("客户：合成待办消息")
        jobs = self.jobs()
        job_id = jobs.start(self.scope, "cancel-batch")
        self.analysis_store.start_job(job_id)
        batch = self.analysis_store.next_pending_batch(job_id)
        record = next(self.store.iter_records(self.scope, self.source_version()))

        jobs.cancel(job_id)
        accepted = self.analysis_store.complete_batch(
            job_id, batch.batch_index,
            covered=[(record.record_id, 0, len(record.text))],
            items=[("fact", "不应写入", [(record.record_id, 0, 2)])])

        self.assertFalse(accepted)
        self.assertFalse(tuple(self.analysis_store.iter_items(job_id)))

    def test_complete_job_reports_full_coverage_and_readable_evidence(self):
        self.import_records(*TEXTS)
        jobs = self.jobs(limits=PlanLimits(max_batch_bytes=140, max_overlap_records=0))
        job_id = self.run_job(jobs)
        progress = self.assert_completed(jobs, job_id)
        self.assertEqual(progress.total_records, 6)
        self.assertEqual(progress.records_done, 6)
        self.assertGreaterEqual(progress.total_batches, 3)
        self.assertEqual(progress.completed_batches, progress.total_batches)
        self.assertAlmostEqual(progress.coverage, 1.0)
        self.assertEqual(progress.parse_missing_pages, 0)

        report = jobs.report(self.scope, self.source_version())
        self.assertIsInstance(report, HistoryReport)
        self.assertEqual(report.job_id, job_id)
        self.assertEqual(report.source_version, self.source_version())
        self.assertEqual(report.records_done, 6)
        self.assertAlmostEqual(report.coverage, 1.0)
        self.assertIn("合成档案", report.summary)
        categories = {item.category for item in report.facts}
        self.assertIn("fact", categories)
        self.assertGreaterEqual(len(report.facts) + len(report.inferences)
                                + len(report.open_questions), 3)
        for item in list(report.facts) + list(report.inferences) + list(report.open_questions):
            self.assertIsInstance(item, ReportItem)
            self.assertIsInstance(item.text, str)
            self.assertGreater(len(item.evidence), 0)
            for ref in item.evidence:
                self.assertIsInstance(ref, EvidenceRef)
                self.assertEqual(ref.scope, self.scope)
                evidence = self.store.read_evidence(ref)
                self.assertGreater(len(evidence.text), 0)

    def test_batches_respect_body_request_and_overlap_limits(self):
        texts = [f"第{index}条合成消息，" + "字" * 8 for index in range(8)]
        self.import_records(*texts, seed="limits-file")
        limits = PlanLimits(max_batch_bytes=200, max_overlap_records=1,
                            max_overlap_bytes=60, max_request_bytes=2400)
        jobs = self.jobs(limits=limits)
        job_id = self.run_job(jobs)
        self.assert_completed(jobs, job_id)
        body_sizes, request_sizes, shared_counts = [], [], []
        previous_ids = set()
        for request in self.gateway.extract_calls():
            records = request.input_data["records"]
            body_sizes.append(sum(len(row["text"].encode("utf-8")) for row in records))
            text = request.input_data["instructions"] + request.input_data["input"]
            request_sizes.append(len(text.encode("utf-8")))
            current_ids = {row["record_id"] for row in records}
            shared_counts.append(len(current_ids & previous_ids))
            previous_ids = current_ids
            self.assertLessEqual(len(records), 8)
        self.assertTrue(all(size <= 200 for size in body_sizes))
        self.assertTrue(all(size <= 2400 for size in request_sizes))
        self.assertLessEqual(max(shared_counts[1:], default=0), 1)

    def test_long_record_split_evidence_keeps_original_offset(self):
        prefix = "前置文字" * 88
        text = prefix + "中部事实B" + "后续文字" * 60
        self.import_records(text, seed="long-file")
        jobs = self.jobs(limits=PlanLimits(max_batch_bytes=220, max_overlap_records=0))
        job_id = self.run_job(jobs)
        self.assert_completed(jobs, job_id)
        report = jobs.report(self.scope, self.source_version())
        matching = [item for item in report.facts if item.text == "事实B（合成）"]
        self.assertEqual(len(matching), 1)
        ref = matching[0].evidence[0]
        self.assertEqual(self.store.read_evidence(ref).text, "事实B")
        self.assertEqual(ref.start, text.index("事实B"))

    def test_report_lists_all_three_categories(self):
        self.import_records(*TEXTS)
        jobs = self.jobs(limits=PlanLimits(max_batch_bytes=400, max_overlap_records=0))
        job_id = self.run_job(jobs)
        self.assert_completed(jobs, job_id)
        report = jobs.report(self.scope, self.source_version())
        self.assertTrue(report.facts)
        self.assertTrue(report.inferences)
        self.assertTrue(report.open_questions)

    def test_overlap_context_does_not_duplicate_report_items(self):
        self.import_records(*TEXTS, seed="overlap-file")
        jobs = self.jobs(limits=PlanLimits(max_batch_bytes=150,
                                           max_overlap_records=1,
                                           max_overlap_bytes=120))
        job_id = self.run_job(jobs)
        progress = self.assert_completed(jobs, job_id)
        self.assertAlmostEqual(progress.coverage, 1.0)
        report = jobs.report(self.scope, self.source_version())
        overlap_facts = [item for item in report.facts
                         if item.text == "事实B（合成）"]
        self.assertEqual(len(overlap_facts), 1)
        evidence_keys = {(ref.record_id, ref.start, ref.end)
                         for item in report.facts for ref in item.evidence}
        self.assertEqual(len(evidence_keys), len(
            [(ref.record_id, ref.start, ref.end)
             for item in report.facts for ref in item.evidence]))

    def test_hierarchical_reduce_uses_multiple_passes_when_items_are_chunked(self):
        from unittest import mock
        self.import_records(*TEXTS, seed="hier-file")
        with mock.patch("liteapp.history_jobs.MAX_REDUCE_ITEMS", 2):
            jobs = self.jobs(limits=PlanLimits(max_batch_bytes=200,
                                               max_overlap_records=0))
            job_id = self.run_job(jobs)
            self.assert_completed(jobs, job_id)
            report = jobs.report(self.scope, self.source_version())
        reduce_calls = [request for request in self.gateway.calls
                        if request.kind == inference_schemas.KIND_HISTORY_REDUCE]
        self.assertGreaterEqual(len(reduce_calls), 2)
        self.assertTrue(report.facts or report.inferences or report.open_questions)

    def test_progress_contract_fields(self):
        names = {field.name for field in fields(JobProgress)}
        for required in ("job_id", "status", "total_records", "records_done",
                         "coverage", "pending_batches", "failed_batches",
                         "completed_batches", "total_batches", "parse_missing_pages",
                         "page_issue_count"):
            self.assertIn(required, names)


class FailureAndResumeTests(_JobTestCase):
    SMALL = PlanLimits(max_batch_bytes=140, max_overlap_records=0)

    def test_third_batch_failure_pauses_then_resume_runs_only_unfinished_batches(self):
        self.import_records(*[f"合成消息{index}：事实B客户询问第{index}项。" for index in range(8)],
                            seed="resume-file")
        limits = PlanLimits(max_batch_bytes=70, max_overlap_records=0)
        self.gateway.fail_on_call(2, inference_schemas.InferenceTransientError("流中断"))
        jobs = self.jobs(limits=limits)
        job_id = self.run_job(jobs)
        progress = jobs.progress(job_id)
        self.assertEqual(progress.status, "paused")
        self.assertEqual(progress.failed_batches, 1)
        self.assertEqual(progress.completed_batches, 2)
        self.assertGreater(progress.pending_batches, 0)
        self.assertEqual(progress.records_done, 2)
        self.assertLess(progress.coverage, 1.0)
        self.assertTrue(progress.message)
        self.assertEqual(len(self.gateway.extract_calls()), 3)
        calls_before_resume = len(self.gateway.extract_calls())
        total_batches = progress.total_batches

        resumed = self.jobs(limits=limits)  # new instance simulates an app restart
        resumed.resume(job_id)
        resumed.run_pending()
        final = self.assert_completed(resumed, job_id)
        self.assertEqual(final.records_done, 8)
        self.assertAlmostEqual(final.coverage, 1.0)
        new_calls = len(self.gateway.extract_calls()) - calls_before_resume
        self.assertEqual(new_calls, total_batches - 2)  # succeeded batches are not repeated
        report = resumed.report(self.scope, self.source_version())
        self.assertEqual(report.job_id, job_id)

    def test_cancel_before_processing_leaves_no_partial_success(self):
        self.import_records(*TEXTS)
        jobs = self.jobs(limits=self.SMALL)
        job_id = jobs.start(self.scope, "profile-1")
        jobs.cancel(job_id)
        jobs.run_pending()
        progress = jobs.progress(job_id)
        self.assertEqual(progress.status, "cancelled")
        self.assertEqual(progress.completed_batches, 0)
        self.assertEqual(self.gateway.extract_calls(), [])
        with self.assertRaises(HistoryJobError):
            jobs.report(self.scope, self.source_version())

    def test_pause_then_resume_completes(self):
        self.import_records(*TEXTS)
        jobs = self.jobs(limits=self.SMALL)
        job_id = jobs.start(self.scope, "profile-1")
        jobs.pause(job_id)
        self.assertEqual(jobs.progress(job_id).status, "paused")
        jobs.run_pending()
        self.assertEqual(self.gateway.extract_calls(), [])
        jobs.resume(job_id)
        jobs.run_pending()
        self.assert_completed(jobs, job_id)

    def test_quota_error_pauses_job_with_reason(self):
        self.import_records(*TEXTS)
        self.gateway.fail_on_call(0, inference_schemas.InferenceQuotaError(
            "ChatGPT 套餐当前无法继续处理。", code="usage_unavailable"))
        jobs = self.jobs(limits=self.SMALL)
        job_id = self.run_job(jobs)
        progress = jobs.progress(job_id)
        self.assertEqual(progress.status, "paused")
        self.assertIn("额度", progress.message)
        self.assertEqual(progress.failed_batches, 1)
        jobs.resume(job_id)
        jobs.run_pending()
        self.assert_completed(jobs, job_id)

    def test_report_without_valid_evidence_is_rejected(self):
        self.import_records(*TEXTS)
        self.gateway.bad_evidence_calls.add(0)
        jobs = self.jobs(limits=self.SMALL)
        job_id = self.run_job(jobs)
        progress = jobs.progress(job_id)
        self.assertEqual(progress.status, "paused")
        self.assertEqual(progress.failed_batches, 1)
        with self.assertRaises(HistoryJobError):
            jobs.report(self.scope, self.source_version())


class CacheAndInvalidationTests(_JobTestCase):
    SMALL = PlanLimits(max_batch_bytes=300, max_overlap_records=0)

    def test_duplicate_import_reuses_completed_job_without_new_model_calls(self):
        self.import_records(*TEXTS, seed="cache-file")
        jobs = self.jobs(limits=self.SMALL)
        job_id = self.run_job(jobs)
        self.assert_completed(jobs, job_id)
        calls = len(self.gateway.calls)
        duplicate = self.store.import_records(
            self.scope, ImportSource(digest=digest("cache-file"), kind="html",
                                     name="合成记录.html"),
            [ImportedRecord(text=text, ordinal=index)
             for index, text in enumerate(TEXTS)])
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(duplicate.source_version, self.source_version())
        self.assertEqual(jobs.start(self.scope, "profile-1"), job_id)
        jobs.run_pending()
        self.assertEqual(len(self.gateway.calls), calls)
        self.assert_completed(jobs, job_id)

    def test_model_version_change_runs_a_new_job(self):
        self.import_records(*TEXTS, seed="model-file")
        first = self.jobs(limits=self.SMALL)
        first_id = self.run_job(first)
        self.assert_completed(first, first_id)
        calls = len(self.gateway.calls)
        second = self.jobs(limits=self.SMALL, model_version="model-b")
        second_id = second.start(self.scope, "profile-1")
        self.assertNotEqual(second_id, first_id)
        second.run_pending()
        self.assert_completed(second, second_id)
        self.assertGreater(len(self.gateway.calls), calls)
        report = second.report(self.scope, self.source_version())
        self.assertEqual(report.model_version, "model-b")
        self.assertEqual(report.job_id, second_id)

    def test_prompt_version_change_runs_a_new_job(self):
        self.import_records(*TEXTS, seed="prompt-file")
        first = self.jobs(limits=self.SMALL)
        first_id = self.run_job(first)
        self.assert_completed(first, first_id)
        second = self.jobs(limits=self.SMALL, prompt_version="history-v2")
        second_id = second.start(self.scope, "profile-1")
        self.assertNotEqual(second_id, first_id)
        second.run_pending()
        self.assert_completed(second, second_id)

    def test_delete_source_invalidates_report_and_progress(self):
        result = self.import_records(*TEXTS, seed="delete-file")
        jobs = self.jobs(limits=self.SMALL)
        job_id = self.run_job(jobs)
        self.assert_completed(jobs, job_id)
        old_version = self.source_version()
        self.store.delete_source(self.scope, result.source_id)
        with self.assertRaises(StaleSourceVersionError):
            jobs.report(self.scope, old_version)
        with self.assertRaises(StaleSourceVersionError):
            jobs.progress(job_id)
        with self.assertRaises(HistoryJobError):
            jobs.report(self.scope, self.source_version())

    def test_report_before_completion_is_rejected(self):
        self.import_records(*TEXTS, seed="early-file")
        jobs = self.jobs(limits=self.SMALL)
        job_id = jobs.start(self.scope, "profile-1")
        with self.assertRaises(HistoryJobError):
            jobs.report(self.scope, self.source_version())
        self.assertEqual(jobs.progress(job_id).status, "pending")

    def test_start_requires_records_and_scope_key(self):
        with self.assertRaises(HistoryJobError):
            self.jobs().start(self.scope, "profile-1")
        with self.assertRaises(TypeError):
            self.jobs().start(self.scope.person_id, "profile-1")

    def test_parse_missing_pages_are_reported(self):
        self.import_records(*TEXTS, seed="pages-file", page_count=4,
                            page_issues=(PageIssue(page=3, code="ocr_missing"),))
        jobs = self.jobs(limits=self.SMALL)
        job_id = self.run_job(jobs)
        progress = self.assert_completed(jobs, job_id)
        self.assertEqual(progress.parse_missing_pages, 1)
        self.assertEqual(progress.page_issue_count, 1)
        report = jobs.report(self.scope, self.source_version())
        self.assertEqual(report.parse_missing_pages, 1)
        self.assertEqual(report.page_issue_count, 1)


class StorageEncryptionTests(_JobTestCase):
    def test_analysis_store_never_persists_plaintext_item_or_chat_text(self):
        self.import_records(*TEXTS, seed="encrypt-file")
        jobs = self.jobs(limits=PlanLimits(max_batch_bytes=140, max_overlap_records=0))
        job_id = self.run_job(jobs)
        self.assert_completed(jobs, job_id)
        raw = (self.folder / "analysis.sqlite3").read_bytes()
        for secret in ("事实A", "合成档案", "合成对话1"):
            self.assertNotIn(secret.encode("utf-8"), raw)


class QueueAndGatewayIntegrationTests(_JobTestCase):
    def test_queue_runs_job_with_history_priority(self):
        self.import_records(*TEXTS, seed="queue-file")
        queue = InferenceQueue(self.gateway, auto_start=True)
        self.addCleanup(queue.stop)
        jobs = self.jobs(limits=PlanLimits(max_batch_bytes=200, max_overlap_records=0),
                         auto_start=True, queue=queue)
        job_id = jobs.start(self.scope, "profile-1")
        self.assertTrue(jobs.wait(job_id, timeout=15))
        progress = self.assert_completed(jobs, job_id)
        self.assertGreaterEqual(len(queue.served_order), 2)
        self.assertEqual(len(queue.served_order), len(self.gateway.calls))
        self.assertTrue(all(request.kind in inference_schemas.HISTORY_KINDS
                            for request in self.gateway.calls))
        self.assertFalse(queue.paused)
        self.assertEqual(progress.failed_batches, 0)

    def test_real_inference_gateway_end_to_end_with_fake_http_model(self):
        self.import_records(*TEXTS, seed="http-file")
        server = _FakeModelServer()
        self.addCleanup(server.close)
        gateway = InferenceGateway()
        gateway.register_profile("profile-http", GatewayProfile(
            backend="compatible", url=server.url + "/v1/chat/completions",
            model="synthetic-history-model", api_key="synthetic-key"))
        jobs = HistoryJobs(self.store, self.analysis_store, gateway,
                           auto_start=False, model_version="model-http",
                           limits=PlanLimits(max_batch_bytes=300,
                                             max_overlap_records=0))
        job_id = jobs.start(self.scope, "profile-http")
        jobs.run_pending()
        progress = self.assert_completed(jobs, job_id)
        self.assertEqual(progress.records_done, 6)
        report = jobs.report(self.scope, self.source_version())
        self.assertTrue(report.facts)
        self.assertTrue(server.requests)
        # The compatible route keeps its own Chat Completions protocol.
        body = server.requests[0]["json"]
        self.assertEqual(body["model"], "synthetic-history-model")
        self.assertEqual(body["messages"][0]["role"], "system")


class _FakeModelServer:
    """Loopback Chat Completions fake that derives deterministic JSON from the prompt."""

    def __init__(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    body = {}
                outer.requests.append({"path": self.path, "json": body})
                user = ""
                for message in body.get("messages", []):
                    if message.get("role") == "user":
                        user = message.get("content", "")
                try:
                    prompt = json.loads(user)
                except (ValueError, TypeError):
                    prompt = {}
                if "records" in prompt:
                    items = []
                    for row in prompt["records"]:
                        record_id, text = row.get("record_id"), row.get("text", "")
                        for token, category in TOKEN_CATEGORIES:
                            position = text.find(token)
                            if position >= 0:
                                items.append({"category": category,
                                              "text": token + "（合成）",
                                              "evidence": [{"record_id": record_id,
                                                            "start": position,
                                                            "end": position + len(token)}]})
                    if not items and prompt["records"]:
                        row = prompt["records"][0]
                        items.append({"category": "fact", "text": "合成事实",
                                      "evidence": [{"record_id": row["record_id"],
                                                    "start": 0,
                                                    "end": len(row.get("text", ""))}]})
                    content = json.dumps({"items": items}, ensure_ascii=False)
                else:
                    items = prompt.get("items", [])
                    content = json.dumps({"summary": f"合成档案（{len(items)} 条）",
                                          "items": items}, ensure_ascii=False)
                payload = json.dumps({"id": "chatcmpl-synthetic",
                                      "choices": [{"message": {"content": content}}],
                                      "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                                "total_tokens": 2}},
                                     ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


if __name__ == "__main__":
    unittest.main()
