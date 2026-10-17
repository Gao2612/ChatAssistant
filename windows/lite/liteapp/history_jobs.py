"""Resumable full-history model analysis jobs (T7).

T7 of ``docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md`` and design §7.3.

The job freezes one :class:`~liteapp.history_models.SourceSnapshot` (scope, source
version, memory version) and processes **every** imported record in deterministic
batches through the T3 inference layer:

* batch body at most ``MAX_BATCH_BYTES`` (12288) UTF-8 bytes;
* at most ``MAX_OVERLAP_RECORDS`` (2) records / ``MAX_OVERLAP_BYTES`` (2048) bytes of
  overlap between consecutive batches for boundary context;
* one model request at most ``MAX_REQUEST_BYTES`` (32768) bytes; when the prompt does
  not fit, the batch shrinks instead of dropping records;
* long records are split on character boundaries and keep their original record
  offsets, so every reported :class:`~liteapp.contracts.EvidenceRef` can jump back
  to the stored text;
* batches are committed transactionally only after the model output completed and
  passed the T3 evidence/scope schema; a stream break never "fills in" coverage;
* job/batch state lives in :mod:`liteapp.history_analysis_store`, so an app restart
  resumes only failed/pending batches (succeeded ones are not re-sent);
* the final extraction results are merged hierarchically with
  ``history_reduce`` into a profile report whose items only carry scope-checked
  :class:`EvidenceRef` evidence. Deleting/importing a source changes the history
  version and invalidates old reports (:class:`StaleSourceVersionError`).

Only a UI/headless caller and tests see this module; it never touches Qt.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from . import history_prompts
from .contracts import CancellationToken, CancelledError, EvidenceRef, ScopeKey
from .history_analysis_store import (AnalysisJob, AnalysisJobError, AnalysisStore,
                                     BatchState, StoredItem)
from .history_models import HistoryRecord, StaleSourceVersionError
from .inference_schemas import (InferenceAuthError, InferenceError,
                                InferenceQuotaError, InferenceRequest,
                                KIND_HISTORY_EXTRACT, KIND_HISTORY_REDUCE)

MAX_BATCH_BYTES = 12288
MAX_OVERLAP_RECORDS = 2
MAX_OVERLAP_BYTES = 2048
MAX_REQUEST_BYTES = 32768
MAX_REDUCE_ITEMS = 30
MAX_REDUCE_LEVELS = 6
PROMPT_VERSION = history_prompts.PROMPT_VERSION


class HistoryJobError(ValueError):
    """User-displayable history analysis job/report failure."""


@dataclass(frozen=True)
class PlanLimits:
    max_batch_bytes: int = MAX_BATCH_BYTES
    max_overlap_records: int = MAX_OVERLAP_RECORDS
    max_overlap_bytes: int = MAX_OVERLAP_BYTES
    max_request_bytes: int = MAX_REQUEST_BYTES

    def __post_init__(self) -> None:
        for value, name in ((self.max_batch_bytes, "max_batch_bytes"),
                            (self.max_overlap_bytes, "max_overlap_bytes"),
                            (self.max_request_bytes, "max_request_bytes")):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} 必须是正整数。")
        if (isinstance(self.max_overlap_records, bool)
                or not isinstance(self.max_overlap_records, int)
                or self.max_overlap_records < 0):
            raise ValueError("max_overlap_records 必须是非负整数。")


@dataclass(frozen=True)
class BatchItem:
    """One record slice in a batch, with its original record character offsets."""

    record_id: str
    source_id: str
    label: str
    text: str
    start: int
    end: int
    record_char_count: int
    segment_index: int = 0
    segment_count: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, str) or not self.record_id:
            raise ValueError("record_id 不能为空。")
        if not isinstance(self.text, str) or not self.text:
            raise ValueError("批次正文不能为空。")
        if self.start < 0 or self.end <= self.start or self.end > self.record_char_count:
            raise ValueError("批次正文范围无效。")
        if len(self.text) != self.end - self.start:
            raise ValueError("批次正文长度与范围不一致。")


@dataclass(frozen=True)
class BatchPlan:
    batch_index: int
    items: tuple[BatchItem, ...]
    text_bytes: int
    request_bytes: int

    @property
    def record_ids(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.record_id for item in self.items))


@dataclass(frozen=True)
class BatchCoverage:
    """Coverage contribution of one batch (compatible with stored BatchState)."""

    status: str
    covered: tuple[tuple[str, int, int], ...] = ()


@dataclass(frozen=True)
class Coverage:
    total_records: int
    succeeded_records: int
    total_chars: int
    covered_chars: int

    @property
    def ratio(self) -> float:
        return self.covered_chars / self.total_chars if self.total_chars else 0.0


@dataclass(frozen=True)
class JobProgress:
    job_id: str
    status: str
    stage: str
    total_records: int
    records_done: int
    total_chars: int
    covered_chars: int
    coverage: float
    total_batches: int
    completed_batches: int
    pending_batches: int
    failed_batches: int
    parse_missing_pages: int
    page_issue_count: int
    message: str = ""

    @property
    def succeeded_records(self) -> int:
        return self.records_done

    @property
    def coverage_percent(self) -> float:
        return round(self.coverage * 100.0, 1)


@dataclass(frozen=True)
class ReportItem:
    category: str
    text: str
    evidence: tuple[EvidenceRef, ...]


@dataclass(frozen=True)
class HistoryReport:
    scope: ScopeKey
    job_id: str
    source_version: int
    memory_version: int
    profile_id: str
    model_version: str
    prompt_version: str
    generated_at: str
    summary: str
    facts: tuple[ReportItem, ...]
    inferences: tuple[ReportItem, ...]
    open_questions: tuple[ReportItem, ...]
    coverage: float
    covered_chars: int
    total_chars: int
    records_done: int
    total_records: int
    parse_missing_pages: int
    page_issue_count: int
    source_ids: tuple[str, ...] = ()
    timeline: tuple[str, ...] = ()


# -- deterministic batching --------------------------------------------------

def batch_bytes(items: Iterable[BatchItem]) -> int:
    return sum(len(item.text.encode("utf-8")) for item in items)


def _split_utf8(text: str, max_bytes: int) -> list[tuple[int, int]]:
    """Split text on character boundaries into UTF-8 ranges of at most max_bytes."""
    ranges: list[tuple[int, int]] = []
    start = 0
    used = 0
    for index, char in enumerate(text):
        size = len(char.encode("utf-8"))
        if used and used + size > max_bytes:
            ranges.append((start, index))
            start = index
            used = 0
        used += size
    if start < len(text):
        ranges.append((start, len(text)))
    return ranges


def _record_units(records: Sequence[HistoryRecord],
                  limits: PlanLimits) -> list[BatchItem]:
    units: list[BatchItem] = []
    for record in records:
        text = record.text
        if len(text.encode("utf-8")) <= limits.max_batch_bytes:
            units.append(BatchItem(
                record_id=record.record_id, source_id=record.source_id,
                label=record.label, text=text, start=0, end=len(text),
                record_char_count=len(text)))
            continue
        segments = _split_utf8(text, limits.max_batch_bytes)
        count = len(segments)
        for index, (start, end) in enumerate(segments):
            units.append(BatchItem(
                record_id=record.record_id, source_id=record.source_id,
                label=f"{record.label}（{index + 1}/{count}）",
                text=text[start:end], start=start, end=end,
                record_char_count=len(text), segment_index=index,
                segment_count=count))
    return units


def _default_measure(items: Sequence[BatchItem]) -> int:
    entries = [(item.record_id, item.label, item.text) for item in items]
    instructions, user_text = history_prompts.build_extract_request(entries)
    return history_prompts.request_bytes(instructions, user_text)


def plan_batches(records: Iterable[HistoryRecord], *,
                 limits: PlanLimits | None = None,
                 measure_request: Callable[[Sequence[BatchItem]], int] | None = None
                 ) -> list[BatchPlan]:
    """Deterministically plan every record into model batches.

    Every record character is covered by at least one batch item; overlap copies are
    only added for boundary context and are never required for coverage.
    """
    limits = limits or PlanLimits()
    if not isinstance(limits, PlanLimits):
        raise TypeError("limits 必须是 PlanLimits")
    measure = measure_request or _default_measure
    record_list = list(records)
    for record in record_list:
        if not isinstance(record, HistoryRecord):
            raise TypeError("records 只能包含 HistoryRecord")
    units = _record_units(record_list, limits)
    plans: list[BatchPlan] = []
    previous_new: list[BatchItem] = []
    index = 0
    while index < len(units):
        overlap: list[BatchItem] = []
        if previous_new and limits.max_overlap_records > 0 and limits.max_overlap_bytes > 0:
            used_ids: set[str] = set()
            used_bytes = 0
            for unit in reversed(previous_new):
                if unit.segment_count > 1:
                    break  # never mix slices of one long record into overlap
                if unit.record_id in used_ids:
                    continue
                if len(overlap) >= limits.max_overlap_records:
                    break
                size = len(unit.text.encode("utf-8"))
                if used_bytes + size > limits.max_overlap_bytes:
                    break
                overlap.append(unit)
                used_ids.add(unit.record_id)
                used_bytes += size
            overlap.reverse()
        batch = list(overlap)
        current = index
        added = 0
        while current < len(units):
            unit = units[current]
            candidate = batch + [unit]
            if added > 0:
                if batch_bytes(candidate) > limits.max_batch_bytes:
                    break
                if measure(candidate) > limits.max_request_bytes:
                    break
            batch.append(unit)
            current += 1
            added += 1
            if unit.segment_count > 1:
                break  # one slice of a long record per batch keeps offsets unambiguous
        if added == 0:  # limits are smaller than one unit: never drop a record
            batch.append(units[index])
            current = index + 1
        new_units = batch[len(overlap):]
        plans.append(BatchPlan(
            batch_index=len(plans), items=tuple(batch),
            text_bytes=batch_bytes(batch), request_bytes=measure(batch)))
        previous_new = new_units
        index = current
    return plans


# -- coverage ----------------------------------------------------------------

def _merge_ranges(ranges: Iterable[tuple[int, int]], total: int) -> list[tuple[int, int]]:
    cleaned = sorted((max(0, start), min(total, end))
                     for start, end in ranges if end > start and start < total)
    merged: list[tuple[int, int]] = []
    for start, end in cleaned:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def coverage_from_batches(record_chars: Mapping[str, int],
                          batches: Iterable[Any]) -> Coverage:
    """Union coverage: overlap is counted once; failed/pending batches never count."""
    record_chars = {str(record_id): int(count) for record_id, count in record_chars.items()}
    ranges: dict[str, list[tuple[int, int]]] = {}
    for batch in batches:
        if getattr(batch, "status", None) != "succeeded":
            continue
        for record_id, start, end in getattr(batch, "covered", ()) or ():
            ranges.setdefault(str(record_id), []).append((int(start), int(end)))
    covered_chars = 0
    succeeded_records = 0
    for record_id, total in record_chars.items():
        merged = _merge_ranges(ranges.get(record_id, ()), total)
        length = sum(end - start for start, end in merged)
        covered_chars += length
        if total > 0 and length >= total:
            succeeded_records += 1
    total_chars = sum(record_chars.values())
    return Coverage(total_records=len(record_chars),
                    succeeded_records=succeeded_records,
                    total_chars=total_chars, covered_chars=covered_chars)


# -- jobs --------------------------------------------------------------------

class HistoryJobs:
    """Run/resume full-history analysis jobs for one V2 history store."""

    def __init__(self, store: Any, analysis_store: AnalysisStore, gateway: Any, *,
                 queue: Any = None, auto_start: bool = True,
                 model_version: str = "", prompt_version: str = PROMPT_VERSION,
                 limits: PlanLimits | None = None) -> None:
        if not hasattr(store, "snapshot") or not hasattr(store, "iter_records"):
            raise TypeError("store 必须是 HistoryStore")
        if not isinstance(analysis_store, AnalysisStore):
            raise TypeError("analysis_store 必须是 AnalysisStore")
        if not hasattr(gateway, "run"):
            raise TypeError("gateway 必须提供 run(request, cancel)")
        if queue is not None and not hasattr(queue, "submit"):
            raise TypeError("queue 必须是 InferenceQueue")
        self.store = store
        self.analysis_store = analysis_store
        self.gateway = gateway
        self.queue = queue
        self.auto_start = bool(auto_start)
        self.model_version = str(model_version or "")
        self.prompt_version = str(prompt_version or PROMPT_VERSION)
        self.limits = limits or PlanLimits()
        if not isinstance(self.limits, PlanLimits):
            raise TypeError("limits 必须是 PlanLimits")
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}
        self._running: dict[str, bool] = {}
        self._tokens: dict[str, CancellationToken] = {}
        self._known: list[str] = []

    # -- public API ----------------------------------------------------------
    def start(self, scope: ScopeKey, profile_id: str) -> str:
        """Freeze the current snapshot and start (or reuse) a full analysis job."""
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey；历史分析必须携带范围。")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError("profile_id 不能为空。")
        snapshot = self.store.snapshot(scope)
        existing = self.analysis_store.find_latest_job(
            scope, profile_id, self.model_version, self.prompt_version,
            snapshot.source_version, snapshot.memory_version)
        if existing is not None:
            if existing.status != "cancelled":
                with self._lock:
                    if existing.job_id not in self._known:
                        self._known.append(existing.job_id)
                    self._tokens.setdefault(existing.job_id, CancellationToken())
                if existing.status in ("pending", "running"):
                    self._ensure_worker(existing.job_id)
                return existing.job_id
        records = list(self.store.iter_records(scope, snapshot.source_version))
        if not records:
            raise HistoryJobError("该范围还没有可分析的导入记录，请先导入聊天记录。")
        plans = plan_batches(records, limits=self.limits)
        manifest = [(record.record_id, record.source_id, len(record.text),
                     record.ordinal) for record in records]
        parse_missing = sum(int(source.failure_count) for source in snapshot.sources)
        page_issues = sum(len(source.page_issues) for source in snapshot.sources)
        job = self.analysis_store.create_job(
            scope, profile_id, self.model_version, self.prompt_version,
            snapshot.source_version, snapshot.memory_version,
            total_batches=len(plans), total_records=len(records),
            total_chars=snapshot.char_count, parse_missing_pages=parse_missing,
            page_issue_count=page_issues, records=manifest,
            status="pending", stage="extract")
        with self._lock:
            self._known.append(job.job_id)
            self._tokens[job.job_id] = CancellationToken()
        self._ensure_worker(job.job_id)
        return job.job_id

    def pause(self, job_id: str) -> None:
        job = self.analysis_store.get_job(job_id)
        if job.status in ("completed", "cancelled"):
            return
        self.analysis_store.update_job(job_id, status="paused",
                                       message="已暂停，可随时继续。")

    def resume(self, job_id: str) -> None:
        job = self.analysis_store.get_job(job_id)
        if job.status in ("completed", "cancelled"):
            return
        self.analysis_store.reset_failed_batches(job_id)
        self.analysis_store.update_job(job_id, status="running", message="")
        with self._lock:
            if job_id not in self._known:
                self._known.append(job_id)
            self._tokens[job_id] = CancellationToken()
        self._ensure_worker(job_id)

    def cancel(self, job_id: str) -> None:
        job = self.analysis_store.get_job(job_id)
        if job.status in ("completed", "cancelled"):
            return
        with self._lock:
            token = self._tokens.get(job_id)
        if token is not None:
            token.cancel()
        self.analysis_store.update_job(job_id, status="cancelled",
                                       message="任务已取消。")

    def progress(self, job_id: str) -> JobProgress:
        job = self.analysis_store.get_job(job_id)
        current = self.store.stats(job.scope).source_version
        if current != job.source_version:
            raise StaleSourceVersionError(
                "来源版本已变化（导入/删除/修订过），旧分析进度已失效，请重新开始分析。")
        return self._progress_for(job)

    def report(self, scope: ScopeKey, source_version: int) -> HistoryReport:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        if isinstance(source_version, bool) or not isinstance(source_version, int):
            raise TypeError("source_version 必须是整数。")
        stats = self.store.stats(scope)
        if source_version != stats.source_version:
            raise StaleSourceVersionError(
                "来源版本已变化（导入/删除/修订过），旧分析报告已失效，请重新分析。")
        stored = self.analysis_store.find_report(scope, source_version,
                                                 stats.memory_version)
        if stored is None:
            raise HistoryJobError("该范围尚未完成全量历史分析，请先运行分析任务。")
        job = self.analysis_store.get_job(stored.job_id)
        record_chars = self.analysis_store.record_char_counts(stored.job_id)
        record_sources = self.analysis_store.record_sources(stored.job_id)
        coverage = coverage_from_batches(record_chars,
                                        self.analysis_store.list_batches(stored.job_id))
        facts, inferences, open_questions = self._report_items(
            stored, scope, record_chars, record_sources)
        payload = stored.payload
        timeline = payload.get("timeline") if isinstance(payload.get("timeline"), list) else []
        return HistoryReport(
            scope=scope, job_id=stored.job_id, source_version=stored.source_version,
            memory_version=stored.memory_version, profile_id=stored.profile_id,
            model_version=stored.model_version, prompt_version=stored.prompt_version,
            generated_at=stored.created_at, summary=str(payload.get("summary") or ""),
            facts=facts, inferences=inferences, open_questions=open_questions,
            coverage=coverage.ratio, covered_chars=coverage.covered_chars,
            total_chars=coverage.total_chars, records_done=coverage.succeeded_records,
            total_records=coverage.total_records,
            parse_missing_pages=job.parse_missing_pages,
            page_issue_count=job.page_issue_count,
            source_ids=self.analysis_store.source_ids(stored.job_id),
            timeline=tuple(str(entry) for entry in timeline))

    def wait(self, job_id: str, timeout: float | None = None) -> bool:
        """Wait until the job is terminal; returns False on timeout (UI helper)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                progress = self.progress(job_id)
            except StaleSourceVersionError:
                return True
            if progress.status in ("completed", "cancelled"):
                return True
            if deadline is not None and time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def run_pending(self) -> None:
        """Process every known runnable job synchronously (tests/headless helper)."""
        with self._lock:
            job_ids = list(dict.fromkeys(self._known))
        for job_id in job_ids:
            try:
                job = self.analysis_store.get_job(job_id)
            except AnalysisJobError:
                continue
            if job.status in ("pending", "running"):
                self._run_job(job_id)

    # -- worker --------------------------------------------------------------
    def _ensure_worker(self, job_id: str) -> None:
        if not self.auto_start:
            return
        with self._lock:
            current = self._threads.get(job_id)
            if current is not None and current.is_alive():
                return
            thread = threading.Thread(target=self._run_job, args=(job_id,),
                                      name=f"history-job-{job_id[:8]}", daemon=True)
            self._threads[job_id] = thread
        thread.start()

    def _token(self, job_id: str) -> CancellationToken:
        with self._lock:
            token = self._tokens.get(job_id)
            if token is None:
                token = CancellationToken()
                self._tokens[job_id] = token
            return token

    def _run_job(self, job_id: str) -> None:
        with self._lock:
            if self._running.get(job_id):
                return
            self._running[job_id] = True
        try:
            while True:
                try:
                    job = self.analysis_store.get_job(job_id)
                except AnalysisJobError:
                    return
                if job.status == "pending":
                    job = self.analysis_store.update_job(job_id, status="running")
                if job.status != "running":
                    return
                if job.stage == "extract":
                    if not self._extract_one(job):
                        return
                elif job.stage == "reduce":
                    self._reduce_and_publish(job)
                    return
                else:
                    return
        finally:
            with self._lock:
                self._running.pop(job_id, None)
                self._threads.pop(job_id, None)

    def _extract_one(self, job: AnalysisJob) -> bool:
        batch = self.analysis_store.next_pending_batch(job.job_id)
        if batch is None:
            self.analysis_store.update_job(job.job_id, stage="reduce")
            return True
        try:
            records = list(self.store.iter_records(job.scope, job.source_version))
            plans = plan_batches(records, limits=self.limits)
            if batch.batch_index >= len(plans):
                raise HistoryJobError("批次计划与冻结来源版本不一致，请重新开始分析。")
            plan = plans[batch.batch_index]
            items = self._extract_plan(job, plan)
        except CancelledError:
            self.analysis_store.update_job(job.job_id, status="cancelled",
                                           message="任务已取消。")
            return False
        except StaleSourceVersionError:
            self.analysis_store.update_job(
                job.job_id, status="paused",
                message="来源版本已变化，旧分析已失效，请重新开始分析。")
            return False
        except InferenceQuotaError:
            self.analysis_store.mark_batch_failed(job.job_id, batch.batch_index, "quota")
            self.analysis_store.update_job(
                job.job_id, status="paused",
                message="模型额度受限，任务已暂停；恢复后可继续未完成批次。")
            return False
        except InferenceAuthError:
            self.analysis_store.mark_batch_failed(job.job_id, batch.batch_index, "auth")
            self.analysis_store.update_job(
                job.job_id, status="paused",
                message="模型授权已失效，请重新连接账号后继续。")
            return False
        except BaseException as exc:  # noqa: BLE001 - recorded as batch failure
            code = type(exc).__name__
            self.analysis_store.mark_batch_failed(job.job_id, batch.batch_index, code)
            self.analysis_store.update_job(
                job.job_id, status="paused",
                message=f"第 {batch.batch_index + 1} 批分析失败（{code}），恢复后只重试未完成批次。")
            return False
        covered = tuple((item.record_id, item.start, item.end) for item in plan.items)
        self.analysis_store.complete_batch(job.job_id, batch.batch_index,
                                           covered=covered, items=items)
        return True

    def _extract_plan(self, job: AnalysisJob,
                      plan: BatchPlan) -> list[tuple[str, str, tuple[tuple[str, int, int], ...]]]:
        entries = [(item.record_id, item.label, item.text) for item in plan.items]
        instructions, user_text = history_prompts.build_extract_request(entries)
        records_payload = [{"record_id": item.record_id, "text": item.text}
                           for item in plan.items]
        messages = [{"role": "system", "content": instructions},
                    {"role": "user", "content": user_text}]
        request = InferenceRequest(
            task_id=f"{job.job_id}:extract:{plan.batch_index}",
            kind=KIND_HISTORY_EXTRACT, profile_id=job.profile_id,
            input_data={"instructions": instructions, "input": user_text,
                        "messages": messages, "records": records_payload},
            schema_version="v1",
            version_key=f"{job.job_id}:{job.source_version}:{plan.batch_index}")
        result = self._run_model(request, self._token(job.job_id))
        base = {item.record_id: item.start for item in plan.items}
        stored = []
        for row in result.validated_data.get("items", []):
            evidence = []
            for ref in row.get("evidence", []):
                record_id = ref["record_id"]
                start = base[record_id] + int(ref["start"])
                end = base[record_id] + int(ref["end"])
                evidence.append((record_id, start, end))
            stored.append((str(row["category"]), str(row["text"]), tuple(evidence)))
        return stored

    # -- reduce/publish ------------------------------------------------------
    def _reduce_and_publish(self, job: AnalysisJob) -> None:
        try:
            items = list(self.analysis_store.iter_items(job.job_id))
            record_chars = self.analysis_store.record_char_counts(job.job_id)
            summary, reduced = self._hierarchical_reduce(job, items, record_chars)
            payload_items = []
            for row in reduced:
                evidence = []
                for record_id, start, end in row["evidence"]:
                    length = record_chars.get(record_id)
                    if length is None or start < 0 or end <= start or end > length:
                        raise HistoryJobError("模型报告包含无效证据引用，已拒绝发布。")
                    evidence.append({"record_id": record_id, "start": start, "end": end})
                if not evidence:
                    raise HistoryJobError("模型报告条目缺少证据引用，已拒绝发布。")
                payload_items.append({"category": row["category"],
                                      "text": row["text"], "evidence": evidence})
            self.analysis_store.save_report(job.job_id, {
                "summary": summary, "items": payload_items, "timeline": []})
            self.analysis_store.update_job(job.job_id, status="completed",
                                           stage="done", message="")
        except CancelledError:
            self.analysis_store.update_job(job.job_id, status="cancelled",
                                           message="任务已取消。")
        except StaleSourceVersionError:
            self.analysis_store.update_job(
                job.job_id, status="paused",
                message="来源版本已变化，旧分析已失效，请重新开始分析。")
        except InferenceQuotaError:
            self.analysis_store.update_job(
                job.job_id, status="paused",
                message="模型额度受限，档案合并阶段已暂停；恢复后重试。")
        except InferenceAuthError:
            self.analysis_store.update_job(
                job.job_id, status="paused",
                message="模型授权已失效，请重新连接账号后继续。")
        except BaseException as exc:  # noqa: BLE001 - job pause with diagnostic
            code = type(exc).__name__
            self.analysis_store.update_job(
                job.job_id, status="paused",
                message=f"档案合并失败（{code}），恢复后重试。")

    @staticmethod
    def _dedupe_items(items: Iterable[StoredItem]) -> list[dict]:
        seen: set[tuple] = set()
        result: list[dict] = []
        for item in items:
            key = (item.category, item.text, tuple(item.evidence))
            if key in seen:
                continue
            seen.add(key)
            result.append({"category": item.category, "text": item.text,
                           "evidence": tuple(item.evidence)})
        return result

    def _chunk_reduce_items(self, items: Sequence[dict]) -> list[list[dict]]:
        chunks: list[list[dict]] = []
        current: list[dict] = []
        for item in items:
            candidate = current + [item]
            payload = [{"category": row["category"], "text": row["text"],
                        "evidence": [{"record_id": record_id, "start": start, "end": end}
                                     for record_id, start, end in row["evidence"]]}
                       for row in candidate]
            instructions, user_text = history_prompts.build_reduce_request(payload)
            fits = (len(candidate) <= MAX_REDUCE_ITEMS
                    and history_prompts.request_bytes(instructions, user_text)
                    <= self.limits.max_request_bytes)
            if current and not fits:
                chunks.append(current)
                current = [item]
            else:
                current = candidate
        if current:
            chunks.append(current)
        return chunks

    def _hierarchical_reduce(self, job: AnalysisJob, items: Iterable[StoredItem],
                             record_chars: Mapping[str, int]) -> tuple[str, list[dict]]:
        current = self._dedupe_items(items)
        if not current:
            return "未提取到明确事实或待确认项。", []
        summaries: list[str] = []
        for _level in range(MAX_REDUCE_LEVELS):
            chunks = self._chunk_reduce_items(current)
            summaries = []
            next_items: list[dict] = []
            for chunk in chunks:
                summary, reduced = self._reduce_once(job, chunk, record_chars)
                summaries.append(summary)
                next_items.extend(reduced)
            if len(chunks) == 1:
                return summaries[0], next_items
            if not next_items:
                return "；".join(summary for summary in summaries if summary), []
            if len(next_items) >= len(current):
                # The model did not merge/deduplicate; a further pass would only
                # repeat the same items. Keep the conservative combined summary and
                # the evidence-backed items instead of looping.
                return ("；".join(summary for summary in summaries if summary),
                        next_items)
            current = next_items
        raise HistoryJobError("历史档案分层合并超过最大层数，已停止。")

    def _reduce_once(self, job: AnalysisJob, rows: Sequence[dict],
                     record_chars: Mapping[str, int]) -> tuple[str, list[dict]]:
        payload = [{"category": row["category"], "text": row["text"],
                    "evidence": [{"record_id": record_id, "start": start, "end": end}
                                 for record_id, start, end in row["evidence"]]}
                   for row in rows]
        instructions, user_text = history_prompts.build_reduce_request(payload)
        referenced = sorted({record_id for row in rows
                             for record_id, _start, _end in row["evidence"]})
        allowed = [{"record_id": record_id, "text": "x" * int(record_chars[record_id])}
                   for record_id in referenced]
        messages = [{"role": "system", "content": instructions},
                    {"role": "user", "content": user_text}]
        request = InferenceRequest(
            task_id=f"{job.job_id}:reduce:{uuid.uuid4().hex[:8]}",
            kind=KIND_HISTORY_REDUCE, profile_id=job.profile_id,
            input_data={"instructions": instructions, "input": user_text,
                        "messages": messages, "records": allowed},
            schema_version="v1", version_key=f"{job.job_id}:reduce")
        result = self._run_model(request, self._token(job.job_id))
        reduced = []
        for row in result.validated_data.get("items", []):
            evidence = tuple((str(ref["record_id"]), int(ref["start"]), int(ref["end"]))
                             for ref in row.get("evidence", []))
            reduced.append({"category": str(row["category"]), "text": str(row["text"]),
                            "evidence": evidence})
        return str(result.validated_data.get("summary") or ""), reduced

    # -- model execution -----------------------------------------------------
    def _run_model(self, request: InferenceRequest,
                   cancel: CancellationToken):
        if self.queue is None:
            return self.gateway.run(request, cancel)
        handle = self.queue.submit(request, priority="history",
                                   scope_id=request.task_id)
        try:
            while True:
                if cancel.is_cancelled():
                    handle.cancel()
                    raise CancelledError("history job cancelled")
                if handle.done:
                    break
                if self.queue.paused:
                    handle.cancel()
                    raise InferenceQuotaError(
                        self.queue.pause_reason or "模型队列已暂停。", code="queue_paused")
                handle.wait(0.05)
        finally:
            if not handle.done:
                handle.cancel()
        if handle.error is not None:
            raise handle.error
        if handle.result is None:
            raise InferenceError("模型任务未返回结果。")
        return handle.result

    # -- progress/report helpers --------------------------------------------
    def _progress_for(self, job: AnalysisJob) -> JobProgress:
        record_chars = self.analysis_store.record_char_counts(job.job_id)
        batches = self.analysis_store.list_batches(job.job_id)
        coverage = coverage_from_batches(record_chars, batches)
        completed = sum(1 for batch in batches if batch.status == "succeeded")
        failed = sum(1 for batch in batches if batch.status == "failed")
        pending = sum(1 for batch in batches if batch.status == "pending")
        return JobProgress(
            job_id=job.job_id, status=job.status, stage=job.stage,
            total_records=job.total_records, records_done=coverage.succeeded_records,
            total_chars=job.total_chars, covered_chars=coverage.covered_chars,
            coverage=coverage.ratio, total_batches=job.total_batches,
            completed_batches=completed, pending_batches=pending,
            failed_batches=failed, parse_missing_pages=job.parse_missing_pages,
            page_issue_count=job.page_issue_count, message=job.message)

    def _report_items(self, stored: Any, scope: ScopeKey,
                      record_chars: Mapping[str, int],
                      record_sources: Mapping[str, str]
                      ) -> tuple[tuple[ReportItem, ...], tuple[ReportItem, ...],
                                  tuple[ReportItem, ...]]:
        facts: list[ReportItem] = []
        inferences: list[ReportItem] = []
        open_questions: list[ReportItem] = []
        for row in stored.payload.get("items", []):
            refs = []
            for ref in row.get("evidence", []):
                record_id = str(ref.get("record_id"))
                start = int(ref.get("start", 0))
                end = int(ref.get("end", 0))
                length = record_chars.get(record_id)
                source_id = record_sources.get(record_id)
                if (length is None or source_id is None or start < 0 or end <= start
                        or end > length):
                    raise HistoryJobError("分析报告的证据引用无效，已拒绝返回；请重新分析。")
                refs.append(EvidenceRef(scope=scope, source_id=source_id,
                                        record_id=record_id, start=start, end=end))
            if not refs:
                raise HistoryJobError("分析报告条目缺少证据引用，已拒绝返回；请重新分析。")
            category = str(row.get("category") or "fact")
            item = ReportItem(category=category, text=str(row.get("text") or ""),
                              evidence=tuple(refs))
            if category == "inference":
                inferences.append(item)
            elif category == "open_question":
                open_questions.append(item)
            else:
                facts.append(item)
        return tuple(facts), tuple(inferences), tuple(open_questions)
