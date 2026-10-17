"""Persistent, scope-aware state for T7 full-history model analysis jobs.

T5's history database owns imported chat content and must not be modified by T7
(its schema is frozen), so analysis state lives in its own SQLite database next to
the history database (``analysis_jobs.sqlite3`` by default). The tables mirror the
design's ``analysis_jobs / batches`` objects:

* ``analysis_jobs``   - one full-history analysis run with frozen scope, source /
  memory version, profile/model/prompt version, status/stage and aggregate counts;
* ``analysis_batches`` - one row per planned model batch with attempts, status and
  the covered record character ranges (so coverage is computable after restart);
* ``analysis_records`` - record_id -> source_id / char_count manifest frozen at
  job start (progress and report do not depend on the mutable history DB);
* ``analysis_items``  - validated extracted items (body text encrypted) with their
  evidence ranges;
* ``analysis_reports`` - published report payload (encrypted) bound to the frozen
  source/memory/model/prompt versions.

Only IDs, counts and version numbers are stored in plain text; item text and the
report payload are protected with the same Windows DPAPI (or an injected
equivalent in tests) as the history store.
"""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from . import settings
from .contracts import ScopeKey

SCHEMA_VERSION = 1
JOB_STATUSES = ("pending", "running", "paused", "cancelled", "completed")
BATCH_STATUSES = ("pending", "succeeded", "failed")
JOB_STAGES = ("extract", "reduce", "done")


class AnalysisError(ValueError):
    """Base class for analysis state errors (user-displayable)."""


class AnalysisJobError(AnalysisError):
    """A job/report is missing or in an unexpected state."""


class AnalysisSchemaError(RuntimeError):
    """The analysis database schema is newer/unknown and must not be written."""


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True)
class AnalysisJob:
    job_id: str
    scope: ScopeKey
    profile_id: str
    model_version: str
    prompt_version: str
    source_version: int
    memory_version: int
    status: str
    stage: str
    total_batches: int
    total_records: int
    total_chars: int
    parse_missing_pages: int
    page_issue_count: int
    message: str
    created_at: str
    updated_at: str

    @property
    def done(self) -> bool:
        return self.status in ("completed", "cancelled")


@dataclass(frozen=True)
class BatchState:
    job_id: str
    batch_index: int
    status: str
    attempts: int
    record_count: int
    covered: tuple[tuple[str, int, int], ...]
    error_code: str


@dataclass(frozen=True)
class StoredItem:
    job_id: str
    batch_index: int
    category: str
    text: str
    evidence: tuple[tuple[str, int, int], ...]


@dataclass(frozen=True)
class StoredReport:
    job_id: str
    scope: ScopeKey
    source_version: int
    memory_version: int
    profile_id: str
    model_version: str
    prompt_version: str
    payload: dict
    created_at: str


_TABLES = """
CREATE TABLE IF NOT EXISTS analysis_jobs(
    job_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    profile_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    memory_version INTEGER NOT NULL,
    status TEXT NOT NULL,
    stage TEXT NOT NULL,
    total_batches INTEGER NOT NULL,
    total_records INTEGER NOT NULL,
    total_chars INTEGER NOT NULL,
    parse_missing_pages INTEGER NOT NULL,
    page_issue_count INTEGER NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS analysis_batches(
    job_id TEXT NOT NULL,
    batch_index INTEGER NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    record_count INTEGER NOT NULL DEFAULT 0,
    covered TEXT NOT NULL DEFAULT '[]',
    error_code TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(job_id, batch_index),
    FOREIGN KEY(job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS analysis_records(
    job_id TEXT NOT NULL,
    record_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    char_count INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    PRIMARY KEY(job_id, record_id),
    FOREIGN KEY(job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS analysis_items(
    item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    batch_index INTEGER NOT NULL,
    category TEXT NOT NULL,
    body BLOB NOT NULL,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(job_id) REFERENCES analysis_jobs(job_id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS analysis_reports(
    report_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    source_version INTEGER NOT NULL,
    memory_version INTEGER NOT NULL,
    profile_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    payload BLOB NOT NULL,
    created_at TEXT NOT NULL);
"""


class AnalysisStore:
    """Scope-checked single-file store for history analysis jobs and reports."""

    def __init__(self, path: Path | str | None = None, protect=None, unprotect=None):
        self.path = Path(path) if path else settings.data_dir() / "analysis_jobs.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.protect = protect or settings.crypt
        self.unprotect = unprotect or (lambda raw: settings.crypt(raw, True))
        self._init_schema()

    # ------------------------------------------------------------------ plumbing
    def _init_schema(self) -> None:
        db = sqlite3.connect(self.path)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                db.executescript(_TABLES)
                db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
                db.commit()
            elif version != SCHEMA_VERSION:
                raise AnalysisSchemaError(
                    f"历史分析数据库 schema 版本为 {version}，程序要求 {SCHEMA_VERSION}；"
                    "已停止读写以免旧程序写坏新库。")
        finally:
            db.close()

    @contextmanager
    def _connect(self):
        try:
            db = sqlite3.connect(self.path, timeout=10)
        except sqlite3.Error as error:
            raise AnalysisSchemaError(f"无法打开历史分析数据库：{error}") from None
        db.execute("PRAGMA foreign_keys=ON")
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise AnalysisSchemaError(
                    f"历史分析数据库 schema 版本为 {version}，程序要求 {SCHEMA_VERSION}。")
            yield db
            db.commit()
        except BaseException:
            try:
                db.rollback()
            except sqlite3.Error:
                pass
            raise
        finally:
            db.close()

    def _seal(self, text: str) -> bytes:
        return self.protect(text.encode("utf-8"))

    def _open(self, blob: bytes | None) -> str:
        if blob is None:
            raise AnalysisError("历史分析加密内容缺失。")
        return self.unprotect(blob).decode("utf-8")

    @staticmethod
    def _row_to_job(row: tuple) -> AnalysisJob:
        return AnalysisJob(
            job_id=row[0], scope=ScopeKey(row[1], row[2]), profile_id=row[3],
            model_version=row[4], prompt_version=row[5], source_version=row[6],
            memory_version=row[7], status=row[8], stage=row[9],
            total_batches=row[10], total_records=row[11], total_chars=row[12],
            parse_missing_pages=row[13], page_issue_count=row[14], message=row[15],
            created_at=row[16], updated_at=row[17])

    @staticmethod
    def _row_to_batch(row: tuple) -> BatchState:
        covered = tuple((str(record_id), int(start), int(end))
                        for record_id, start, end in json.loads(row[5] or "[]"))
        return BatchState(job_id=row[0], batch_index=row[1], status=row[2],
                          attempts=row[3], record_count=row[4],
                          covered=covered, error_code=row[6] or "")

    # --------------------------------------------------------------------- jobs
    def create_job(self, scope: ScopeKey, profile_id: str, model_version: str,
                   prompt_version: str, source_version: int, memory_version: int,
                   *, total_batches: int, total_records: int, total_chars: int,
                   parse_missing_pages: int, page_issue_count: int,
                   records: Sequence[tuple[str, str, int, int]],
                   status: str = "pending", stage: str = "extract") -> AnalysisJob:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        if status not in JOB_STATUSES:
            raise ValueError(f"未知任务状态：{status!r}")
        if stage not in JOB_STAGES:
            raise ValueError(f"未知任务阶段：{stage!r}")
        for value, name in ((total_batches, "total_batches"),
                            (total_records, "total_records"),
                            (total_chars, "total_chars"),
                            (parse_missing_pages, "parse_missing_pages"),
                            (page_issue_count, "page_issue_count")):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} 必须是非负整数。")
        job_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as db:
            db.execute(
                "INSERT INTO analysis_jobs(job_id, account_id, person_id, profile_id, "
                "model_version, prompt_version, source_version, memory_version, "
                "status, stage, total_batches, total_records, total_chars, "
                "parse_missing_pages, page_issue_count, message, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'',?,?)",
                (job_id, scope.wechat_account_id, scope.person_id, profile_id,
                 model_version, prompt_version, source_version, memory_version,
                 status, stage, total_batches, total_records, total_chars,
                 parse_missing_pages, page_issue_count, now, now))
            db.executemany(
                "INSERT INTO analysis_records(job_id, record_id, source_id, char_count, "
                "ordinal) VALUES(?,?,?,?,?)",
                [(job_id, record_id, source_id, char_count, ordinal)
                 for record_id, source_id, char_count, ordinal in records])
            db.executemany(
                "INSERT INTO analysis_batches(job_id, batch_index, status, attempts, "
                "record_count, covered, error_code, updated_at) "
                "VALUES(?,?,'pending',0,0,'[]','',?)",
                [(job_id, index, now) for index in range(total_batches)])
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> AnalysisJob:
        if not isinstance(job_id, str) or not job_id:
            raise TypeError("job_id 必须是非空字符串。")
        with self._connect() as db:
            row = db.execute(
                "SELECT job_id, account_id, person_id, profile_id, model_version, "
                "prompt_version, source_version, memory_version, status, stage, "
                "total_batches, total_records, total_chars, parse_missing_pages, "
                "page_issue_count, message, created_at, updated_at FROM analysis_jobs "
                "WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            raise AnalysisJobError("历史分析任务不存在。")
        return self._row_to_job(row)

    def update_job(self, job_id: str, *, status: str | None = None,
                   stage: str | None = None, message: str | None = None) -> AnalysisJob:
        changes, values = [], []
        if status is not None:
            if status not in JOB_STATUSES:
                raise ValueError(f"未知任务状态：{status!r}")
            changes.append("status=?")
            values.append(status)
        if stage is not None:
            if stage not in JOB_STAGES:
                raise ValueError(f"未知任务阶段：{stage!r}")
            changes.append("stage=?")
            values.append(stage)
        if message is not None:
            changes.append("message=?")
            values.append(str(message)[:500])
        if not changes:
            return self.get_job(job_id)
        changes.append("updated_at=?")
        values.append(_now())
        values.append(job_id)
        with self._connect() as db:
            cursor = db.execute(
                f"UPDATE analysis_jobs SET {', '.join(changes)} WHERE job_id=?", values)
            if cursor.rowcount == 0:
                raise AnalysisJobError("历史分析任务不存在。")
        return self.get_job(job_id)

    def find_latest_job(self, scope: ScopeKey, profile_id: str, model_version: str,
                        prompt_version: str, source_version: int,
                        memory_version: int) -> AnalysisJob | None:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        with self._connect() as db:
            row = db.execute(
                "SELECT job_id, account_id, person_id, profile_id, model_version, "
                "prompt_version, source_version, memory_version, status, stage, "
                "total_batches, total_records, total_chars, parse_missing_pages, "
                "page_issue_count, message, created_at, updated_at FROM analysis_jobs "
                "WHERE account_id=? AND person_id=? AND profile_id=? AND model_version=? "
                "AND prompt_version=? AND source_version=? AND memory_version=? "
                "ORDER BY rowid DESC LIMIT 1",
                (scope.wechat_account_id, scope.person_id, profile_id, model_version,
                 prompt_version, source_version, memory_version)).fetchone()
        return None if row is None else self._row_to_job(row)

    def find_completed_job(self, scope: ScopeKey, profile_id: str, model_version: str,
                           prompt_version: str, source_version: int,
                           memory_version: int) -> AnalysisJob | None:
        job = self.find_latest_job(scope, profile_id, model_version, prompt_version,
                                   source_version, memory_version)
        return job if job is not None and job.status == "completed" else None

    # ------------------------------------------------------------------- batches
    def list_batches(self, job_id: str) -> list[BatchState]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT job_id, batch_index, status, attempts, record_count, covered, "
                "error_code FROM analysis_batches WHERE job_id=? ORDER BY batch_index",
                (job_id,)).fetchall()
        return [self._row_to_batch(row) for row in rows]

    def get_batch(self, job_id: str, batch_index: int) -> BatchState:
        with self._connect() as db:
            row = db.execute(
                "SELECT job_id, batch_index, status, attempts, record_count, covered, "
                "error_code FROM analysis_batches WHERE job_id=? AND batch_index=?",
                (job_id, batch_index)).fetchone()
        if row is None:
            raise AnalysisJobError("历史分析批次不存在。")
        return self._row_to_batch(row)

    def next_pending_batch(self, job_id: str) -> BatchState | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT job_id, batch_index, status, attempts, record_count, covered, "
                "error_code FROM analysis_batches WHERE job_id=? AND status='pending' "
                "ORDER BY batch_index LIMIT 1", (job_id,)).fetchone()
        return None if row is None else self._row_to_batch(row)

    def mark_batch_failed(self, job_id: str, batch_index: int, error_code: str,
                          message: str = "") -> None:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE analysis_batches SET status='failed', attempts=attempts+1, "
                "error_code=?, updated_at=? WHERE job_id=? AND batch_index=?",
                (str(error_code)[:120], _now(), job_id, batch_index))
            if cursor.rowcount == 0:
                raise AnalysisJobError("历史分析批次不存在。")

    def reset_failed_batches(self, job_id: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "UPDATE analysis_batches SET status='pending', error_code='', "
                "updated_at=? WHERE job_id=? AND status='failed'", (_now(), job_id))
            return cursor.rowcount

    def complete_batch(self, job_id: str, batch_index: int, *,
                       covered: Sequence[tuple[str, int, int]],
                       items: Sequence[tuple[str, str, Sequence[tuple[str, int, int]]]]) -> None:
        """Transactionally record a succeeded batch: covered ranges + validated items."""
        covered_rows = [(str(record_id), int(start), int(end))
                        for record_id, start, end in covered]
        if not covered_rows:
            raise ValueError("完成批次必须记录覆盖范围。")
        record_ids = {record_id for record_id, _start, _end in covered_rows}
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT status FROM analysis_batches WHERE job_id=? AND batch_index=?",
                (job_id, batch_index)).fetchone()
            if row is None:
                raise AnalysisJobError("历史分析批次不存在。")
            if row[0] == "succeeded":
                return
            for category, text, evidence in items:
                evidence_rows = [[str(record_id), int(start), int(end)]
                                 for record_id, start, end in evidence]
                db.execute(
                    "INSERT INTO analysis_items(job_id, batch_index, category, body, "
                    "evidence, created_at) VALUES(?,?,?,?,?,?)",
                    (job_id, batch_index, str(category)[:40], self._seal(str(text)),
                     json.dumps(evidence_rows, ensure_ascii=False), now))
            db.execute(
                "UPDATE analysis_batches SET status='succeeded', attempts=attempts+1, "
                "record_count=?, covered=?, error_code='', updated_at=? "
                "WHERE job_id=? AND batch_index=?",
                (len(record_ids),
                 json.dumps([list(row) for row in covered_rows], ensure_ascii=False),
                 now, job_id, batch_index))

    # ------------------------------------------------------------------ manifest
    def record_char_counts(self, job_id: str) -> dict[str, int]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT record_id, char_count FROM analysis_records WHERE job_id=?",
                (job_id,)).fetchall()
        return {record_id: int(char_count) for record_id, char_count in rows}

    def record_sources(self, job_id: str) -> dict[str, str]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT record_id, source_id FROM analysis_records WHERE job_id=?",
                (job_id,)).fetchall()
        return {record_id: source_id for record_id, source_id in rows}

    def source_ids(self, job_id: str) -> tuple[str, ...]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT DISTINCT source_id FROM analysis_records WHERE job_id=? "
                "ORDER BY source_id", (job_id,)).fetchall()
        return tuple(row[0] for row in rows)

    # -------------------------------------------------------------------- items
    def iter_items(self, job_id: str) -> Iterator[StoredItem]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT batch_index, category, body, evidence FROM analysis_items "
                "WHERE job_id=? ORDER BY item_id", (job_id,)).fetchall()
        for batch_index, category, body, evidence in rows:
            refs = tuple((str(record_id), int(start), int(end))
                         for record_id, start, end in json.loads(evidence or "[]"))
            yield StoredItem(job_id=job_id, batch_index=batch_index,
                             category=category, text=self._open(body), evidence=refs)

    # ------------------------------------------------------------------ reports
    def save_report(self, job_id: str, payload: dict) -> StoredReport:
        if not isinstance(payload, dict):
            raise TypeError("报告 payload 必须是字典。")
        job = self.get_job(job_id)
        now = _now()
        blob = self._seal(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        with self._connect() as db:
            db.execute(
                "INSERT INTO analysis_reports(job_id, account_id, person_id, "
                "source_version, memory_version, profile_id, model_version, "
                "prompt_version, payload, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (job_id, job.scope.wechat_account_id, job.scope.person_id,
                 job.source_version, job.memory_version, job.profile_id,
                 job.model_version, job.prompt_version, blob, now))
            report_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
        return StoredReport(job_id=job_id, scope=job.scope,
                            source_version=job.source_version,
                            memory_version=job.memory_version,
                            profile_id=job.profile_id, model_version=job.model_version,
                            prompt_version=job.prompt_version, payload=payload,
                            created_at=now)

    def find_report(self, scope: ScopeKey, source_version: int,
                    memory_version: int) -> StoredReport | None:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        with self._connect() as db:
            row = db.execute(
                "SELECT job_id, source_version, memory_version, profile_id, "
                "model_version, prompt_version, payload, created_at "
                "FROM analysis_reports WHERE account_id=? AND person_id=? "
                "AND source_version=? AND memory_version=? "
                "ORDER BY rowid DESC LIMIT 1",
                (scope.wechat_account_id, scope.person_id, source_version,
                 memory_version)).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(self._open(row[6]))
        except (ValueError, UnicodeDecodeError, AnalysisError):
            return None
        if not isinstance(payload, dict):
            return None
        return StoredReport(job_id=row[0], scope=scope, source_version=row[1],
                            memory_version=row[2], profile_id=row[3],
                            model_version=row[4], prompt_version=row[5],
                            payload=payload, created_at=row[7])
