"""Background import jobs with observable progress and clean cancellation.

PLAN T6: ``ImportJobs.start(scope, path, enable_page_ocr) -> job_id`` and
``ImportJobs.progress(job_id) -> ImportProgress``. Parsing happens on a worker
thread; the store commit happens only once, after the *whole* file was parsed and
only if the job was not cancelled — so a cancelled/failed import never leaves half
a file in the encrypted history database.

No chat content is logged: progress carries counts, phases, page-level issue codes
and a short error message; the full path is never included (only the file name).
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import CancellationToken, CancelledError, ScopeKey
from .history_models import (
    HistoryImportError,
    ImportSource,
    PageIssue,
    SchemaVersionError,
    ScopeMismatchError,
)
from .importers import create_parser, file_digest
from .importers import probe as default_probe
from .importers.base import FormatMatch, Parser, checked_file

__all__ = [
    "ImportJobError",
    "ImportJobs",
    "ImportProgress",
    "STATUS_CANCELLED",
    "STATUS_FAILED",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
]

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
TERMINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_CANCELLED)


class ImportJobError(ValueError):
    """Unknown job id or misuse of the job API."""


@dataclass(frozen=True)
class ImportProgress:
    """Immutable progress snapshot for one import job (safe to show in the UI)."""

    job_id: str
    scope: ScopeKey
    path_name: str
    status: str = STATUS_QUEUED
    phase: str = STATUS_QUEUED
    record_count: int = 0
    char_count: int = 0
    page_count: int = 0
    pages_done: int = 0
    page_issues: tuple[PageIssue, ...] = ()
    duplicate: bool = False
    retried: bool = False
    support_level: str = ""
    format_kind: str = ""
    parser_name: str = ""
    source_id: str | None = None
    source_version: int | None = None
    memory_version: int | None = None
    error: str | None = None
    elapsed_seconds: float = 0.0
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "page_issues", tuple(self.page_issues or ()))
        object.__setattr__(self, "notes", tuple(self.notes or ()))

    @property
    def finished(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def failure_count(self) -> int:
        return len(self.page_issues)


class _Job:
    _FIELDS = (
        "status", "phase", "record_count", "char_count", "page_count", "pages_done",
        "page_issues", "duplicate", "retried", "support_level", "format_kind",
        "parser_name", "source_id", "source_version", "memory_version", "error",
        "notes",
    )

    def __init__(self, *, scope: ScopeKey, path: Path, enable_page_ocr: bool,
                 clock: Callable[[], float], retry_source_id: str | None = None) -> None:
        self.job_id = uuid.uuid4().hex[:16]
        self.scope = scope
        self.path = Path(path)
        self.path_name = self.path.name
        self.enable_page_ocr = bool(enable_page_ocr)
        self.retry_source_id = retry_source_id
        self.cancel = CancellationToken()
        self._clock = clock
        self.started_at = clock()
        self.finished_at: float | None = None
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.values: dict[str, Any] = {
            "status": STATUS_QUEUED, "phase": STATUS_QUEUED,
            "record_count": 0, "char_count": 0, "page_count": 0, "pages_done": 0,
            "page_issues": (), "duplicate": False, "retried": False,
            "support_level": "", "format_kind": "", "parser_name": "",
            "source_id": None, "source_version": None, "memory_version": None,
            "error": None, "notes": (),
        }

    def update(self, **fields: Any) -> None:
        unknown = set(fields) - set(self._FIELDS)
        if unknown:
            raise KeyError(f"未知进度字段：{sorted(unknown)}")
        with self.lock:
            self.values.update(fields)

    def finish(self, status: str, phase: str, **fields: Any) -> None:
        with self.lock:
            self.finished_at = self._clock()
            self.values.update(fields)
            self.values["status"] = status
            self.values["phase"] = phase

    def snapshot(self) -> ImportProgress:
        with self.lock:
            values = dict(self.values)
            finished_at = self.finished_at
        elapsed = (finished_at if finished_at is not None else self._clock()) - self.started_at
        return ImportProgress(
            job_id=self.job_id, scope=self.scope, path_name=self.path_name,
            elapsed_seconds=max(0.0, elapsed), **values)


class ImportJobs:
    """Runs import jobs on background threads; owns the parser registry hook."""

    def __init__(self, store: Any, *, probe: Callable[[Path], FormatMatch | None] | None = None,
                 clock: Callable[[], float] | None = None,
                 page_timeout: float | None = None,
                 html_timeout: float | None = None,
                 python_executable: str | None = None,
                 worker_command: list[str] | None = None) -> None:
        self.store = store
        self._probe = probe or default_probe
        self.clock = clock or time.monotonic
        self.page_timeout = page_timeout
        self.html_timeout = html_timeout
        self.python_executable = python_executable
        self.worker_command = worker_command
        self._jobs: dict[str, _Job] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ public
    def start(self, scope: ScopeKey, path: Path | str,
              enable_page_ocr: bool = False, *,
              retry_source_id: str | None = None) -> str:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey；导入不接受 person_id 快捷入口。")
        if not isinstance(enable_page_ocr, bool):
            raise TypeError("enable_page_ocr 必须是布尔值。")
        if retry_source_id is not None:
            self._require_retry_source(scope, retry_source_id)
        job = _Job(scope=scope, path=Path(path), enable_page_ocr=enable_page_ocr,
                   clock=self.clock, retry_source_id=retry_source_id)
        with self._lock:
            self._jobs[job.job_id] = job
        job.thread = threading.Thread(target=self._execute, args=(job,), daemon=True,
                                      name=f"import-{job.job_id}")
        job.thread.start()
        return job.job_id

    def start_retry(self, scope: ScopeKey, source_id: str, path: Path | str,
                    enable_page_ocr: bool = False) -> str:
        """Retry the failed pages of one existing source (R-04).

        The whole file is parsed again in the controlled parser; the store merges
        the newly parsed pages with the records recovered by the previous import,
        so successful pages are neither duplicated nor lost.
        """
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey；导入不接受 person_id 快捷入口。")
        self._require_retry_source(scope, source_id)
        return self.start(scope, path, enable_page_ocr, retry_source_id=source_id)

    def progress(self, job_id: str) -> ImportProgress:
        return self._job(job_id).snapshot()

    def cancel(self, job_id: str) -> None:
        self._job(job_id).cancel.cancel()

    def wait(self, job_id: str, timeout: float | None = None) -> ImportProgress:
        job = self._job(job_id)
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        while True:
            progress = job.snapshot()
            if progress.finished:
                return progress
            if deadline is not None and time.monotonic() >= deadline:
                return progress
            time.sleep(0.01)

    def run_sync(self, scope: ScopeKey, path: Path | str,
                 enable_page_ocr: bool = False, *,
                 retry_source_id: str | None = None) -> ImportProgress:
        """Run one import on the calling thread (tests / explicit small-file path)."""
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey；导入不接受 person_id 快捷入口。")
        if not isinstance(enable_page_ocr, bool):
            raise TypeError("enable_page_ocr 必须是布尔值。")
        if retry_source_id is not None:
            self._require_retry_source(scope, retry_source_id)
        job = _Job(scope=scope, path=Path(path), enable_page_ocr=enable_page_ocr,
                   clock=self.clock, retry_source_id=retry_source_id)
        with self._lock:
            self._jobs[job.job_id] = job
        self._execute(job)
        return job.snapshot()

    def _require_retry_source(self, scope: ScopeKey, source_id: Any) -> None:
        if not isinstance(source_id, str) or not source_id.strip():
            raise ImportJobError("重试导入必须指定已有来源 source_id。")
        known = {source.source_id for source in self.store.list_sources(scope)}
        if source_id not in known:
            raise ImportJobError("要重试的来源不存在或不属于当前范围。")

    def active_job_ids(self) -> tuple[str, ...]:
        with self._lock:
            jobs = list(self._jobs.values())
        return tuple(job.job_id for job in jobs if job.snapshot().status not in TERMINAL_STATUSES)

    # ----------------------------------------------------------------- internal
    def _job(self, job_id: str) -> _Job:
        with self._lock:
            job = self._jobs.get(str(job_id))
        if job is None:
            raise ImportJobError("导入任务不存在或已过期。")
        return job

    def _execute(self, job: _Job) -> None:
        try:
            self._run_job(job)
        except CancelledError:
            job.finish(STATUS_CANCELLED, STATUS_CANCELLED)
        except (HistoryImportError, ScopeMismatchError, SchemaVersionError) as error:
            job.finish(STATUS_FAILED, "failed", error=str(error))
        except Exception as error:  # never leak content/tracebacks into the UI
            job.finish(STATUS_FAILED, "failed",
                       error=f"导入失败（{type(error).__name__}）。")

    def _run_job(self, job: _Job) -> None:
        job.update(status=STATUS_RUNNING, phase="probe")
        path = checked_file(job.path)
        match = self._probe(path)
        if match is None:
            raise HistoryImportError("不支持的文件格式；目前仅支持 HTML 与 PDF 文件。")
        job.update(format_kind=match.kind, parser_name=match.parser_name,
                   support_level=match.support_level, notes=match.notes)
        parser: Parser = create_parser(
            match, clock=self.clock, page_timeout=self.page_timeout,
            html_timeout=self.html_timeout, python_executable=self.python_executable,
            worker_command=self.worker_command)
        parser.enable_page_ocr = job.enable_page_ocr
        digest = file_digest(path)
        job.update(phase="parse")
        records: list[Any] = []
        char_count = 0
        pages_done = 0
        for record in parser.parse(path, job.cancel):
            records.append(record)
            char_count += len(record.text)
            pages_done = max(pages_done, parser.pages_done)
            if len(records) <= 100 or len(records) % 25 == 0:
                job.update(record_count=len(records), char_count=char_count,
                           page_count=parser.page_count, pages_done=pages_done)
            if job.cancel.is_cancelled():
                raise CancelledError("导入已取消")
        if job.cancel.is_cancelled():
            raise CancelledError("导入已取消")
        job.update(phase="commit", record_count=len(records), char_count=char_count,
                   page_count=parser.page_count,
                   pages_done=max(pages_done, parser.pages_done),
                   page_issues=tuple(parser.page_issues))
        source = ImportSource(digest=digest, kind=match.kind, name=path.name,
                              # Persist the parser identity, not just the shared
                              # format version: if probe selects a fixed parser
                              # (e.g. companion JSON instead of a truncated HTML
                              # body) for the same bytes, the store may replace a
                              # previously partial source instead of skipping it
                              # as an idempotent duplicate.
                              parser_version=match.parser_name or "unknown",
                              page_count=parser.page_count,
                              page_issues=tuple(parser.page_issues),
                              source_id=job.retry_source_id)
        result = self.store.import_records(job.scope, source, records,
                                           retry=job.retry_source_id is not None)
        job.finish(STATUS_SUCCEEDED, "done", record_count=result.record_count,
                   char_count=result.char_count, page_count=result.page_count,
                   pages_done=max(pages_done, parser.pages_done),
                   page_issues=result.page_issues, duplicate=result.duplicate,
                   retried=result.retried,
                   source_id=result.source_id, source_version=result.source_version,
                   memory_version=result.memory_version, error=None)
