"""Local, per-person encrypted memory store (history data V2, scope-enforced).

PLAN T5 / design §7.1:

* Every persistent read, search, import, report and delete takes an immutable
  :class:`~liteapp.contracts.ScopeKey`; the store verifies in SQLite that the
  person belongs to the supplied WeChat account. There is no ``person_id``-only
  shortcut left that could silently read another account's history.
* Bodies, contact/file names, sender/time labels, locators, quality flags and
  page-issue details are encrypted with the injected ``protect`` transform
  (DPAPI by default); IDs, digests, counts and version integers stay plaintext.
* ``source_set_version`` increases on every import/revision/delete;
  ``memory_version`` additionally increases when derived memory (reports/index)
  changes. Deleting a source deletes its derived memory so stale analysis can
  never be reused.
* The SQLite schema is created or migrated transactionally by
  :mod:`liteapp.history_migrations`; a newer/unknown schema stops all reads and
  writes instead of being guessed.

Display names are never identity: the same visible name under two accounts (or
twice under one account) yields independent UUID scopes.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

from .contracts import EvidenceRef, ScopeKey
from .history_migrations import SCHEMA_VERSION, MigrationResult, migrate_database
from .history_models import (
    MAX_NAME_CHARS,
    DerivedMemory,
    EvidenceError,
    EvidenceText,
    HistoryError,
    HistoryImportError,
    HistoryAccount,
    HistoryRecord,
    ImportResult,
    ImportSource,
    ImportedRecord,
    PersonInfo,
    ScopeMismatchError,
    ScopeStats,
    SchemaVersionError,
    SourceInfo,
    SourceNotFoundError,
    SourceSnapshot,
    StaleSourceVersionError,
    decode_page_issues,
    encode_page_issues,
    from_json_text,
    to_json_text,
)

__all__ = [
    "HistoryError",
    "HistoryImportError",
    "HistoryStore",
    "ScopeMismatchError",
    "SchemaVersionError",
    "SourceNotFoundError",
    "StaleSourceVersionError",
]

MAX_IMPORT_FILE_BYTES = 100 * 1024 * 1024
RETRYABLE_PAGE_CODES = ("timeout", "worker_error")
"""Page-level issue codes that a retry import may try to recover (R-04)."""


def _retryable_pages(issues: Iterable[Any]) -> set[int]:
    return {issue.page for issue in issues if issue.code in RETRYABLE_PAGE_CODES}


def _record_page(record: ImportedRecord) -> int | None:
    locator = record.locator if isinstance(record.locator, Mapping) else None
    page = locator.get("page") if locator is not None else None
    return page if isinstance(page, int) and not isinstance(page, bool) else None


class _PlainHTML(HTMLParser):
    """Safe text fallback: never executes scripts and never fetches remote content."""

    BLOCKS = {"address", "article", "blockquote", "br", "dd", "div", "dt", "h1", "h2",
              "h3", "h4", "h5", "h6", "li", "p", "section", "td", "th", "tr"}
    SKIP = {"script", "style", "noscript", "svg", "iframe", "template", "head"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skipping = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skipping.append(tag)
        elif not self.skipping and tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.skipping:
            if tag == self.skipping[-1]:
                self.skipping.pop()
        elif tag in self.BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skipping:
            self.parts.append(data)


def _decode_html_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise HistoryImportError("HTML 编码无法识别。")


def _sections_from_html(raw: bytes) -> list[str]:
    parser = _PlainHTML()
    parser.feed(_decode_html_text(raw))
    return [" ".join(line.split()) for line in "".join(parser.parts).splitlines()
            if line.strip()]


def _sections_from_pdf(path: Path) -> list[tuple[int, str]]:
    """Return ``(page_number, text)`` lines; page one-based, order preserved."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise HistoryImportError("缺少 PDF 解析组件，请重新安装应用。") from None
    try:
        reader = PdfReader(str(path), strict=False)
        if reader.is_encrypted:
            raise HistoryImportError("PDF 有密码保护，请先导出无密码版本。")
        sections: list[tuple[int, str]] = []
        for page_number, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            sections.extend((page_number, " ".join(line.split()))
                            for line in text.splitlines() if line.strip())
    except HistoryImportError:
        raise
    except Exception:
        raise HistoryImportError("PDF 损坏或无法读取。") from None
    if not sections:
        raise HistoryImportError("PDF 没有可提取文字；扫描件需要先做 OCR。")
    return sections


class HistoryStore:
    """Encrypted V2 text store. Scope association is explicit, never inferred."""

    def __init__(self, path: Path | str | None = None, protect=None, unprotect=None,
                 backup_dir: Path | str | None = None):
        from . import settings
        self.path = Path(path) if path else settings.data_dir() / "memory.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.protect = protect or settings.crypt
        self.unprotect = unprotect or (lambda raw: settings.crypt(raw, True))
        self.backup_dir = (Path(backup_dir) if backup_dir is not None
                           else self.path.parent / "history-backups")
        # Creates a new V2 database or transactionally migrates a 0.2 database
        # (with a controlled backup). A newer/unknown schema raises here.
        self.last_migration: MigrationResult = migrate_database(
            self.path, protect=self.protect, unprotect=self.unprotect,
            backup_dir=self.backup_dir)
        self.schema_version = SCHEMA_VERSION

    # ------------------------------------------------------------------ plumbing
    @contextmanager
    def _connect(self):
        try:
            db = sqlite3.connect(self.path)
        except sqlite3.Error as error:
            raise SchemaVersionError(f"无法打开历史数据库：{error}") from None
        db.execute("PRAGMA foreign_keys=ON")
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise SchemaVersionError(
                    f"历史数据库 schema 版本为 {version}，程序要求 {SCHEMA_VERSION}；"
                    "为防止旧程序写坏新库，已停止读写。")
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
            raise HistoryImportError("加密内容缺失。")
        return self.unprotect(blob).decode("utf-8")

    def _open_optional(self, blob: bytes | None) -> str | None:
        return None if blob is None else self._open(blob)

    def _seal_json(self, value: Any) -> bytes:
        return self._seal(to_json_text(value))

    def _open_json(self, blob: bytes | None) -> Any:
        return None if blob is None else from_json_text(self.unprotect(blob))

    @staticmethod
    def _require_scope(scope: Any) -> ScopeKey:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey；历史数据 V2 不接受 person_id 快捷入口。")
        return scope

    def _validate_scope(self, db: sqlite3.Connection, scope: ScopeKey) -> None:
        row = db.execute(
            "SELECT 1 FROM people p JOIN wechat_accounts a ON a.id=p.account_id "
            "WHERE a.id=? AND p.id=?",
            (scope.wechat_account_id, scope.person_id)).fetchone()
        if row is None:
            raise ScopeMismatchError("范围校验失败：联系人不存在或不属于该微信账号。")

    @staticmethod
    def _account_hash(account_key: str) -> str:
        return hashlib.sha256(account_key.encode("utf-8")).hexdigest()

    def _ensure_account(self, db: sqlite3.Connection, account_key: str) -> str:
        if not isinstance(account_key, str):
            raise TypeError("account_key 必须是字符串。")
        account_key = account_key.strip()
        if not account_key or len(account_key) > MAX_NAME_CHARS:
            raise ValueError("请输入有效的微信账号标识。")
        account_hash = self._account_hash(account_key)
        row = db.execute("SELECT id FROM wechat_accounts WHERE legacy_hash=?",
                         (account_hash,)).fetchone()
        if row is not None:
            return row[0]
        account_id = uuid.uuid4().hex
        db.execute(
            "INSERT INTO wechat_accounts(id, legacy_hash, label) VALUES(?,?,?)",
            (account_id, account_hash, self._seal(account_key)))
        return account_id

    def _scope_state(self, db: sqlite3.Connection, scope: ScopeKey) -> tuple[int, int]:
        row = db.execute(
            "SELECT source_set_version, memory_version FROM scope_state "
            "WHERE account_id=? AND person_id=?",
            (scope.wechat_account_id, scope.person_id)).fetchone()
        if row is None:
            raise ScopeMismatchError("范围校验失败：联系人不存在或不属于该微信账号。")
        return int(row[0]), int(row[1])

    # ------------------------------------------------------------- account/people
    def list_accounts(self) -> list[HistoryAccount]:
        """List local history buckets; labels never serve as stable identity."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, legacy_hash, label FROM wechat_accounts ORDER BY rowid"
            ).fetchall()
        return [HistoryAccount(account_id=row[0], label=self._open(row[2]),
                               legacy=row[1] is not None) for row in rows]

    def ensure_legacy_account(self, label: str) -> HistoryAccount:
        """Return the old label-backed bucket, creating it only when absent."""
        with self._connect() as db:
            account_id = self._ensure_account(db, label)
            row = db.execute(
                "SELECT legacy_hash,label FROM wechat_accounts WHERE id=?",
                (account_id,)).fetchone()
        return HistoryAccount(account_id, self._open(row[1]), row[0] is not None)

    def create_account(self, label: str) -> HistoryAccount:
        """Create an explicitly named app-owned account bucket."""
        if not isinstance(label, str):
            raise TypeError("资料名称必须是字符串。")
        label = label.strip()
        if not label or len(label) > 200 or "\x00" in label:
            raise ValueError("请输入 1 至 200 个字符的资料名称。")
        with self._connect() as db:
            rows = db.execute("SELECT label FROM wechat_accounts").fetchall()
            if any(self._open(row[0]).casefold() == label.casefold() for row in rows):
                raise ValueError("资料名称已存在，请使用不同名称。")
            account_id = uuid.uuid4().hex
            db.execute("INSERT INTO wechat_accounts(id,legacy_hash,label) VALUES(?,NULL,?)",
                       (account_id, self._seal(label)))
        return HistoryAccount(account_id, label, False)

    def rename_account(self, account_id: str, label: str) -> HistoryAccount:
        """Rename a user-created bucket without changing its identity."""
        if not isinstance(account_id, str) or not account_id.strip():
            raise ValueError("资料 ID 无效。")
        if not isinstance(label, str):
            raise TypeError("资料名称必须是字符串。")
        label = label.strip()
        if not label or len(label) > 200 or "\x00" in label:
            raise ValueError("请输入 1 至 200 个字符的资料名称。")
        with self._connect() as db:
            row = db.execute(
                "SELECT legacy_hash FROM wechat_accounts WHERE id=?", (account_id,)
            ).fetchone()
            if row is None:
                raise ValueError("资料不存在。")
            if row[0] is not None:
                raise ValueError("旧的未区分资料不能重命名，避免误认历史归属。")
            labels = db.execute(
                "SELECT id,label FROM wechat_accounts WHERE id<>?", (account_id,)
            ).fetchall()
            if any(self._open(other_label).casefold() == label.casefold()
                   for _, other_label in labels):
                raise ValueError("资料名称已存在，请使用不同名称。")
            db.execute("UPDATE wechat_accounts SET label=? WHERE id=?",
                       (self._seal(label), account_id))
        return HistoryAccount(account_id, label, False)

    def create_person_in_account(self, account_id: str,
                                 display_name: str) -> ScopeKey:
        """Create a contact under an explicitly selected account ID."""
        if not isinstance(display_name, str):
            raise TypeError("display_name 必须是字符串。")
        display_name = display_name.strip()
        if not display_name or len(display_name) > 200:
            raise ValueError("请输入有效的联系人名称。")
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM wechat_accounts WHERE id=?",
                                (account_id,)).fetchone()
            if exists is None:
                raise ValueError("所选资料不存在，请刷新后重试。")
            person_id = uuid.uuid4().hex
            db.execute("INSERT INTO people(id,account_id,display_name) VALUES(?,?,?)",
                       (person_id, account_id, self._seal(display_name)))
            db.execute(
                "INSERT INTO scope_state(account_id,person_id,source_set_version,"
                "memory_version) VALUES(?,?,0,0)", (account_id, person_id))
        return ScopeKey(account_id, person_id)

    def list_people_in_account(self, account_id: str) -> list[PersonInfo]:
        """List contacts using a stable account ID rather than a display label."""
        if not isinstance(account_id, str) or not account_id.strip():
            return []
        with self._connect() as db:
            rows = db.execute(
                "SELECT id,display_name FROM people WHERE account_id=? ORDER BY rowid",
                (account_id,)).fetchall()
        return [PersonInfo(account_id, person_id, self._open(blob))
                for person_id, blob in rows]

    def create_person(self, display_name: str,
                      account_key: str = "default") -> ScopeKey:
        """Create one contact under an account; returns its immutable ScopeKey.

        The same display name under the same or another account creates a new
        independent UUID; names are never merged into an identity.
        """
        if not isinstance(display_name, str):
            raise TypeError("display_name 必须是字符串。")
        display_name = display_name.strip()
        if not display_name or len(display_name) > 200:
            raise ValueError("请输入有效的联系人名称。")
        with self._connect() as db:
            account_id = self._ensure_account(db, account_key)
            person_id = uuid.uuid4().hex
            db.execute("INSERT INTO people(id,account_id,display_name) VALUES(?,?,?)",
                       (person_id, account_id, self._seal(display_name)))
            db.execute(
                "INSERT INTO scope_state(account_id,person_id,source_set_version,"
                "memory_version) VALUES(?,?,0,0)", (account_id, person_id))
        return ScopeKey(account_id, person_id)

    def list_people(self, account_key: str = "default") -> list[PersonInfo]:
        """List contacts of one local account (account-scoped, not person-scoped)."""
        if not isinstance(account_key, str):
            raise TypeError("account_key 必须是字符串。")
        account_key = account_key.strip()
        if not account_key:
            return []
        with self._connect() as db:
            account = db.execute("SELECT id FROM wechat_accounts WHERE legacy_hash=?",
                                 (self._account_hash(account_key),)).fetchone()
            if account is None:
                return []
            rows = db.execute(
                "SELECT id, display_name FROM people WHERE account_id=? ORDER BY rowid",
                (account[0],)).fetchall()
            return [PersonInfo(account_id=account[0], person_id=person_id,
                               display_name=self._open(blob))
                    for person_id, blob in rows]

    # ------------------------------------------------------------------- import
    @staticmethod
    def _materialize_records(records: Iterable[ImportedRecord]) -> list[ImportedRecord]:
        try:
            materialized = list(records)
        except TypeError:
            raise TypeError("records 必须是 ImportedRecord 的可迭代对象。") from None
        if not materialized:
            raise HistoryImportError("没有可导入的记录。")
        ordinals: set[int] = set()
        record_ids: set[str] = set()
        for item in materialized:
            if not isinstance(item, ImportedRecord):
                raise TypeError("records 必须只包含 ImportedRecord。")
            if item.ordinal in ordinals:
                raise HistoryImportError("导入记录的原始顺序号重复，已拒绝导入。")
            ordinals.add(item.ordinal)
            if item.record_id is not None:
                if item.record_id in record_ids:
                    raise HistoryImportError("导入记录的 record_id 重复，已拒绝导入。")
                record_ids.add(item.record_id)
        return materialized

    def _source_result(self, db: sqlite3.Connection, scope: ScopeKey,
                       source_id: str, duplicate: bool,
                       *, retried: bool = False) -> ImportResult:
        row = db.execute(
            "SELECT s.record_count, s.char_count, s.page_count, s.failure_count, "
            "s.page_issues FROM sources s WHERE s.id=?", (source_id,)).fetchone()
        if row is None:
            raise SourceNotFoundError("来源不存在。")
        source_version, memory_version = self._scope_state(db, scope)
        return ImportResult(
            source_id=source_id, duplicate=duplicate, record_count=row[0],
            char_count=row[1], page_count=row[2], failure_count=row[3],
            source_version=source_version, memory_version=memory_version,
            page_issues=decode_page_issues(self._open(row[4]) if row[4] else None),
            retried=retried)

    def import_records(self, scope: ScopeKey, source: ImportSource,
                       records: Iterable[ImportedRecord], *,
                       retry: bool = False) -> ImportResult:
        """Transactionally store parser output for one file inside one scope.

        Same file digest in the same scope is an idempotent duplicate (no new
        records and no version bump). Supplying an existing ``source.source_id``
        inside this scope revises that source atomically; a source ID owned by
        another scope is rejected.

        ``retry=True`` is the T6/R-04 recovery path for an existing source whose
        recorded ``page_issues`` contain ``timeout``/``worker_error``: pages parsed
        again replace their old records, while pages still failing keep the records
        recovered by the previous import (never duplicated, never silently lost).
        """
        self._require_scope(scope)
        if not isinstance(source, ImportSource):
            raise TypeError("source 必须是 ImportSource。")
        if not isinstance(retry, bool):
            raise TypeError("retry 必须是布尔值。")
        with self._connect() as db:
            self._validate_scope(db, scope)
            materialized = self._materialize_records(records)
            if retry:
                return self._retry_import(db, scope, source, materialized)
            duplicate = db.execute(
                "SELECT id FROM sources WHERE person_id=? AND digest=?",
                (scope.person_id, source.digest)).fetchone()
            if duplicate is not None:
                return self._source_result(db, scope, duplicate[0], duplicate=True)

            revision = None
            if source.source_id is not None:
                revision = db.execute(
                    "SELECT account_id, person_id FROM sources WHERE id=?",
                    (source.source_id,)).fetchone()
                if revision is not None and (
                        revision[0] != scope.wechat_account_id
                        or revision[1] != scope.person_id):
                    raise ScopeMismatchError(
                        "跨账号导入被拒绝：来源不属于当前微信账号与联系人。")
            source_id = source.source_id or uuid.uuid4().hex
            char_count = sum(len(item.text) for item in materialized)
            failure_count = len(source.page_issues)
            page_issues_blob = self._seal(encode_page_issues(source.page_issues))
            try:
                if revision is not None:
                    db.execute("DELETE FROM records WHERE source_id=?", (source_id,))
                    db.execute(
                        "UPDATE sources SET digest=?, kind=?, parser_version=?, "
                        "import_version=import_version+1, name=?, page_count=?, "
                        "record_count=?, char_count=?, failure_count=?, page_issues=? "
                        "WHERE id=?",
                        (source.digest, source.kind, source.parser_version,
                         self._seal(source.name), source.page_count,
                         len(materialized), char_count, failure_count,
                         page_issues_blob, source_id))
                else:
                    db.execute(
                        "INSERT INTO sources(id, account_id, person_id, digest, kind, "
                        "parser_version, import_version, name, page_count, "
                        "record_count, char_count, failure_count, page_issues) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (source_id, scope.wechat_account_id, scope.person_id,
                         source.digest, source.kind, source.parser_version,
                         source.import_version, self._seal(source.name),
                         source.page_count, len(materialized), char_count,
                         failure_count, page_issues_blob))
                for item in materialized:
                    record_id = item.record_id or uuid.uuid4().hex
                    db.execute(
                        "INSERT INTO records(record_id, source_id, account_id, "
                        "person_id, ordinal, record_type, sender, sent_at, body, "
                        "locator, quality, char_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (record_id, source_id, scope.wechat_account_id,
                         scope.person_id, item.ordinal, item.record_type,
                         self._seal(item.sender) if item.sender is not None else None,
                         self._seal(item.sent_at) if item.sent_at is not None else None,
                         self._seal(item.text),
                         self._seal_json(item.locator) if item.locator is not None else None,
                         self._seal_json(item.quality) if item.quality is not None else None,
                         len(item.text)))
            except sqlite3.IntegrityError as error:
                raise HistoryImportError("导入数据与现有记录冲突，已回滚。") from error
            source_version, memory_version = self._bump_versions(db, scope)
            db.execute(
                "DELETE FROM derived_memory WHERE account_id=? AND person_id=?",
                (scope.wechat_account_id, scope.person_id))
            return ImportResult(
                source_id=source_id, duplicate=False, record_count=len(materialized),
                char_count=char_count, page_count=source.page_count,
                failure_count=failure_count, source_version=source_version,
                memory_version=memory_version, page_issues=source.page_issues)

    # -------------------------------------------------- retry failed pages (R-04)
    def _retry_import(self, db: sqlite3.Connection, scope: ScopeKey,
                      source: ImportSource,
                      materialized: list[ImportedRecord]) -> ImportResult:
        """Recover ``timeout``/``worker_error`` pages of one existing source.

        The re-parsed file must be the same file (same digest). Pages that parsed
        again replace their previous records; pages that still fail keep the rows
        recovered by the previous import, so a retry can never duplicate successful
        pages or silently drop them.
        """
        source_id = source.source_id
        if source_id is None:
            raise HistoryImportError("重试导入必须指定已有来源 source_id。")
        row = db.execute(
            "SELECT account_id, person_id, digest, page_issues FROM sources WHERE id=?",
            (source_id,)).fetchone()
        if row is None:
            raise SourceNotFoundError("要重试的来源不存在。")
        if row[0] != scope.wechat_account_id or row[1] != scope.person_id:
            raise ScopeMismatchError(
                "跨账号重试被拒绝：来源不属于当前微信账号与联系人。")
        if row[2] != source.digest:
            raise HistoryImportError(
                "重试的文件内容与已导入来源不一致；请按新的文件修订导入。")
        existing_issues = decode_page_issues(self._open(row[3]) if row[3] else None)
        if not _retryable_pages(existing_issues):
            raise HistoryImportError("该来源没有可重试的超时/解析失败页。")

        old_rows = db.execute(
            "SELECT record_id, ordinal, record_type, sender, sent_at, body, locator, "
            "quality, char_count FROM records WHERE source_id=? ORDER BY ordinal",
            (source_id,)).fetchall()
        old_by_page: dict[int, list[tuple]] = {}
        old_unpaged: list[tuple] = []
        for old_row in old_rows:
            locator = self._open_json(old_row[6])
            page = locator.get("page") if isinstance(locator, dict) else None
            if isinstance(page, int) and not isinstance(page, bool):
                old_by_page.setdefault(page, []).append(old_row)
            else:
                old_unpaged.append(old_row)

        new_by_page: dict[int, list[ImportedRecord]] = {}
        new_unpaged: list[ImportedRecord] = []
        for item in materialized:
            page = _record_page(item)
            if page is None:
                new_unpaged.append(item)
            else:
                new_by_page.setdefault(page, []).append(item)

        if not new_by_page:
            # Non-paged formats (HTML/legacy) can only be replaced as a whole.
            if old_unpaged and len(old_unpaged) == len(old_rows):
                rows = [self._new_record_row(item, scope, source_id, ordinal)
                        for ordinal, item in enumerate(materialized)]
                return self._commit_merged_rows(
                    db, scope, source, rows,
                    sum(len(item.text) for item in materialized))
            raise HistoryImportError(
                "重试的文件没有页码定位，无法与已有 PDF 记录安全合并。")
        if new_unpaged:
            raise HistoryImportError(
                "重试的文件混合了有页码和无页码记录，无法安全合并。")

        rows: list[tuple] = []
        char_count = 0
        for page in sorted(set(new_by_page) | set(old_by_page)):
            if page in new_by_page:
                for item in new_by_page[page]:
                    rows.append(self._new_record_row(item, scope, source_id, len(rows)))
                    char_count += len(item.text)
            else:
                # This page still failed (or was not parsed again): keep the
                # records recovered by the previous import instead of losing them.
                for old_row in old_by_page.get(page, ()):
                    rows.append(self._existing_record_row(
                        old_row, scope, source_id, len(rows)))
                    char_count += int(old_row[8])
        return self._commit_merged_rows(db, scope, source, rows, char_count)

    def _new_record_row(self, item: ImportedRecord, scope: ScopeKey,
                        source_id: str, ordinal: int) -> tuple:
        return (
            item.record_id or uuid.uuid4().hex, source_id,
            scope.wechat_account_id, scope.person_id, ordinal, item.record_type,
            self._seal(item.sender) if item.sender is not None else None,
            self._seal(item.sent_at) if item.sent_at is not None else None,
            self._seal(item.text),
            self._seal_json(item.locator) if item.locator is not None else None,
            self._seal_json(item.quality) if item.quality is not None else None,
            len(item.text))

    def _existing_record_row(self, old_row: tuple, scope: ScopeKey,
                             source_id: str, ordinal: int) -> tuple:
        """Re-insert one already-encrypted row with a new sequential ordinal."""
        return (
            old_row[0], source_id, scope.wechat_account_id, scope.person_id, ordinal,
            old_row[2], old_row[3], old_row[4], old_row[5], old_row[6], old_row[7],
            old_row[8])

    def _commit_merged_rows(self, db: sqlite3.Connection, scope: ScopeKey,
                            source: ImportSource, rows: list[tuple],
                            char_count: int) -> ImportResult:
        source_id = source.source_id
        assert source_id is not None
        db.execute("DELETE FROM records WHERE source_id=?", (source_id,))
        db.executemany(
            "INSERT INTO records(record_id, source_id, account_id, person_id, "
            "ordinal, record_type, sender, sent_at, body, locator, quality, "
            "char_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.execute(
            "UPDATE sources SET kind=?, parser_version=?, import_version=import_version+1, "
            "name=?, page_count=?, record_count=?, char_count=?, failure_count=?, "
            "page_issues=? WHERE id=?",
            (source.kind, source.parser_version, self._seal(source.name),
             source.page_count, len(rows), char_count, len(source.page_issues),
             self._seal(encode_page_issues(source.page_issues)), source_id))
        source_version, memory_version = self._bump_versions(db, scope)
        db.execute("DELETE FROM derived_memory WHERE account_id=? AND person_id=?",
                   (scope.wechat_account_id, scope.person_id))
        return ImportResult(
            source_id=source_id, duplicate=False, record_count=len(rows),
            char_count=char_count, page_count=source.page_count,
            failure_count=len(source.page_issues), source_version=source_version,
            memory_version=memory_version, page_issues=source.page_issues,
            retried=True)

    def import_file(self, scope: ScopeKey, path: Path | str) -> dict[str, Any]:
        """Interim convenience import (HTML/PDF) kept for the current history UI.

        T6 routes the UI through :mod:`liteapp.importers` and ``ImportJobs``; this
        method still enforces the same V2 scope rules and transaction.
        """
        self._require_scope(scope)
        path = Path(path)
        kind = path.suffix.lower().lstrip(".")
        if kind not in ("html", "htm", "pdf"):
            raise HistoryImportError("只支持 HTML 或 PDF 文件。")
        try:
            size = path.stat().st_size
            if size > MAX_IMPORT_FILE_BYTES:
                raise HistoryImportError("文件超过 100 MB，请分批导入。")
            raw = path.read_bytes()
        except HistoryImportError:
            raise
        except OSError:
            raise HistoryImportError("文件无法读取。") from None
        digest = hashlib.sha256(raw).hexdigest()
        records: list[ImportedRecord] = []
        page_count = 0
        if kind == "pdf":
            sections = _sections_from_pdf(path)
            page_count = max((page for page, _text in sections), default=0)
            for ordinal, (page, text) in enumerate(sections):
                records.append(ImportedRecord(
                    text=text, ordinal=ordinal, record_type="fragment",
                    locator={"kind": "pdf", "page": page}))
        else:
            sections = _sections_from_html(raw)
            for ordinal, text in enumerate(sections):
                records.append(ImportedRecord(
                    text=text, ordinal=ordinal, record_type="fragment",
                    locator={"kind": "html", "section": ordinal + 1}))
        if not records:
            raise HistoryImportError("文件中没有可导入的聊天文字。")
        source = ImportSource(digest=digest, kind="pdf" if kind == "pdf" else "html",
                              name=path.name, page_count=page_count)
        result = self.import_records(scope, source, records)
        return {"source_id": result.source_id, "kind": source.kind,
                "section_count": result.record_count, "char_count": result.char_count,
                "duplicate": result.duplicate,
                "source_version": result.source_version,
                "memory_version": result.memory_version}

    # -------------------------------------------------------------------- reads
    def list_sources(self, scope: ScopeKey) -> list[SourceInfo]:
        self._require_scope(scope)
        with self._connect() as db:
            self._validate_scope(db, scope)
            rows = db.execute(
                "SELECT id, account_id, person_id, digest, kind, parser_version, "
                "import_version, name, page_count, record_count, char_count, "
                "failure_count, page_issues, created_at FROM sources "
                "WHERE account_id=? AND person_id=? ORDER BY rowid",
                (scope.wechat_account_id, scope.person_id)).fetchall()
            return [SourceInfo(
                source_id=row[0], account_id=row[1], person_id=row[2], digest=row[3],
                kind=row[4], parser_version=row[5], import_version=row[6],
                name=self._open(row[7]), page_count=row[8], record_count=row[9],
                char_count=row[10], failure_count=row[11],
                page_issues=decode_page_issues(self._open(row[12]) if row[12] else None),
                created_at=row[13]) for row in rows]

    def _record_from_row(self, row: tuple) -> HistoryRecord:
        return HistoryRecord(
            record_id=row[0], source_id=row[1], ordinal=row[2], record_type=row[3],
            sender=self._open_optional(row[4]), sent_at=self._open_optional(row[5]),
            text=self._open(row[6]), locator=self._open_json(row[7]),
            quality=self._open_json(row[8]), char_count=row[9])

    def iter_records(self, scope: ScopeKey, source_version: int) -> Iterator[HistoryRecord]:
        """Iterate every record frozen at ``source_version`` in original order.

        A stale version (a source was imported/deleted after the caller froze it)
        raises :class:`StaleSourceVersionError` instead of silently mixing data.
        """
        self._require_scope(scope)
        if isinstance(source_version, bool) or not isinstance(source_version, int):
            raise TypeError("source_version 必须是整数。")
        with self._connect() as db:
            self._validate_scope(db, scope)
            current, _memory_version = self._scope_state(db, scope)
            if source_version != current:
                raise StaleSourceVersionError(
                    f"来源版本已变化：请求 {source_version}，当前 {current}。")
            rows = db.execute(
                "SELECT r.record_id, r.source_id, r.ordinal, r.record_type, "
                "r.sender, r.sent_at, r.body, r.locator, r.quality, r.char_count "
                "FROM records r JOIN sources s ON s.id=r.source_id "
                "WHERE r.account_id=? AND r.person_id=? AND s.person_id=? "
                "ORDER BY s.rowid, r.ordinal",
                (scope.wechat_account_id, scope.person_id,
                 scope.person_id)).fetchall()
        return (self._record_from_row(row) for row in rows)

    def list_messages(self, scope: ScopeKey, offset: int = 0,
                      limit: int = 100) -> list[dict[str, Any]]:
        """Paged record listing for the UI (kept dict-shaped for compatibility)."""
        self._require_scope(scope)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 500))
        with self._connect() as db:
            self._validate_scope(db, scope)
            rows = db.execute(
                "SELECT r.record_id, r.source_id, s.name, r.ordinal, r.record_type, "
                "r.sender, r.sent_at, r.body, r.locator, r.quality, r.char_count "
                "FROM records r JOIN sources s ON s.id=r.source_id "
                "WHERE r.account_id=? AND r.person_id=? "
                "ORDER BY s.rowid, r.ordinal LIMIT ? OFFSET ?",
                (scope.wechat_account_id, scope.person_id, limit, offset)).fetchall()
            result = []
            for row in rows:
                record = HistoryRecord(
                    record_id=row[0], source_id=row[1], ordinal=row[3],
                    record_type=row[4], sender=self._open_optional(row[5]),
                    sent_at=self._open_optional(row[6]), text=self._open(row[7]),
                    locator=self._open_json(row[8]), quality=self._open_json(row[9]),
                    char_count=row[10])
                result.append({
                    "id": record.record_id, "record_id": record.record_id,
                    "source_id": record.source_id,
                    "source_name": self._open(row[2]),
                    "sequence": record.ordinal, "label": record.label,
                    "text": record.text, "record_type": record.record_type,
                    "sender": record.sender, "sent_at": record.sent_at,
                    "locator": record.locator, "quality": record.quality,
                })
            return result

    def search(self, scope: ScopeKey, query: str, limit: int = 50) -> list[dict[str, Any]]:
        self._require_scope(scope)
        if not isinstance(query, str):
            raise TypeError("query 必须是字符串。")
        query = query.strip().casefold()
        if not query:
            return []
        matches: list[dict[str, Any]] = []
        offset = 0
        while len(matches) < limit:
            page = self.list_messages(scope, offset=offset, limit=500)
            if not page:
                break
            matches.extend(row for row in page if query in row["text"].casefold())
            offset += len(page)
        return matches[:limit]

    def stats(self, scope: ScopeKey) -> ScopeStats:
        self._require_scope(scope)
        with self._connect() as db:
            self._validate_scope(db, scope)
            source_count, record_count, char_count, page_count, failure_count = db.execute(
                "SELECT COUNT(*), COALESCE(SUM(record_count),0), "
                "COALESCE(SUM(char_count),0), COALESCE(SUM(page_count),0), "
                "COALESCE(SUM(failure_count),0) FROM sources "
                "WHERE account_id=? AND person_id=?",
                (scope.wechat_account_id, scope.person_id)).fetchone()
            source_version, memory_version = self._scope_state(db, scope)
        return ScopeStats(source_count=int(source_count), record_count=int(record_count),
                          char_count=int(char_count), page_count=int(page_count),
                          failure_count=int(failure_count),
                          source_version=source_version, memory_version=memory_version)

    def build_context(self, scope: ScopeKey, max_chars: int = 8000) -> str:
        """Most recent imported evidence; never claims complete coverage."""
        self._require_scope(scope)
        totals = self.stats(scope)
        rows = self.list_messages(scope, offset=max(0, totals.record_count - 500),
                                  limit=500)
        chosen: list[str] = []
        remaining = max(0, int(max_chars))
        for row in reversed(rows):
            line = f"[{row['source_name']} / {row['label']}] {row['text']}"
            if len(line) > remaining:
                if not chosen and remaining:
                    chosen.append(line[-remaining:])
                break
            chosen.append(line)
            remaining -= len(line) + 1
        return "\n".join(reversed(chosen))

    def read_evidence(self, ref: EvidenceRef) -> EvidenceText:
        """Decrypt one scope-checked character range of a stored record."""
        if not isinstance(ref, EvidenceRef):
            raise TypeError("ref 必须是 EvidenceRef。")
        with self._connect() as db:
            self._validate_scope(db, ref.scope)
            row = db.execute(
                "SELECT r.record_id, r.source_id, r.account_id, r.person_id, "
                "r.record_type, r.sender, r.sent_at, r.body, r.locator, "
                "r.char_count, s.name FROM records r JOIN sources s "
                "ON s.id=r.source_id WHERE r.record_id=? AND r.source_id=?",
                (ref.record_id, ref.source_id)).fetchone()
            if row is None:
                raise EvidenceError("证据记录不存在或不属于该来源。")
            if (row[2] != ref.scope.wechat_account_id
                    or row[3] != ref.scope.person_id):
                raise ScopeMismatchError("跨账号证据读取被拒绝：记录不属于当前范围。")
            body = self._open(row[7])
            char_count = int(row[9])
            if ref.start > char_count or ref.end > char_count:
                raise EvidenceError(
                    f"证据范围越界：记录长度 {char_count}，请求 [{ref.start},{ref.end})。")
            return EvidenceText(
                ref=ref, text=body[ref.start:ref.end], source_name=self._open(row[10]),
                record_type=row[4], sender=self._open_optional(row[5]),
                sent_at=self._open_optional(row[6]),
                locator=self._open_json(row[8]), record_char_count=char_count)

    def snapshot(self, scope: ScopeKey) -> SourceSnapshot:
        """Freeze source IDs, versions and the record/character range manifest."""
        self._require_scope(scope)
        with self._connect() as db:
            self._validate_scope(db, scope)
            source_version, memory_version = self._scope_state(db, scope)
            source_rows = db.execute(
                "SELECT id, account_id, person_id, digest, kind, parser_version, "
                "import_version, name, page_count, record_count, char_count, "
                "failure_count, page_issues, created_at FROM sources "
                "WHERE account_id=? AND person_id=? ORDER BY rowid",
                (scope.wechat_account_id, scope.person_id)).fetchall()
            sources = tuple(SourceInfo(
                source_id=row[0], account_id=row[1], person_id=row[2], digest=row[3],
                kind=row[4], parser_version=row[5], import_version=row[6],
                name=self._open(row[7]), page_count=row[8], record_count=row[9],
                char_count=row[10], failure_count=row[11],
                page_issues=decode_page_issues(self._open(row[12]) if row[12] else None),
                created_at=row[13]) for row in source_rows)
            record_rows = db.execute(
                "SELECT r.record_id, r.source_id, r.char_count FROM records r "
                "JOIN sources s ON s.id=r.source_id "
                "WHERE r.account_id=? AND r.person_id=? "
                "ORDER BY s.rowid, r.ordinal",
                (scope.wechat_account_id, scope.person_id)).fetchall()
        ranges = tuple(EvidenceRef(scope=scope, source_id=row[1],
                                   record_id=row[0], start=0, end=int(row[2]))
                       for row in record_rows)
        return SourceSnapshot(
            scope=scope, source_version=source_version, memory_version=memory_version,
            sources=sources, ranges=ranges, record_count=len(ranges),
            char_count=sum(ref.end - ref.start for ref in ranges))

    # ------------------------------------------------------------------- delete
    def _bump_versions(self, db: sqlite3.Connection, scope: ScopeKey) -> tuple[int, int]:
        db.execute(
            "UPDATE scope_state SET source_set_version=source_set_version+1, "
            "memory_version=memory_version+1, updated_at=CURRENT_TIMESTAMP "
            "WHERE account_id=? AND person_id=?",
            (scope.wechat_account_id, scope.person_id))
        return self._scope_state(db, scope)

    def delete_source(self, scope: ScopeKey, source_id: str) -> int:
        """Delete one owned source and its derived memory; returns new memory_version."""
        self._require_scope(scope)
        if not isinstance(source_id, str) or not source_id.strip():
            raise TypeError("source_id 必须是非空字符串。")
        with self._connect() as db:
            self._validate_scope(db, scope)
            row = db.execute("SELECT account_id, person_id FROM sources WHERE id=?",
                             (source_id,)).fetchone()
            if row is None:
                raise SourceNotFoundError("来源不存在。")
            if (row[0] != scope.wechat_account_id or row[1] != scope.person_id):
                raise ScopeMismatchError(
                    "跨账号删除被拒绝：来源不属于当前微信账号与联系人。")
            db.execute("DELETE FROM sources WHERE id=?", (source_id,))
            db.execute("DELETE FROM derived_memory WHERE account_id=? AND person_id=?",
                       (scope.wechat_account_id, scope.person_id))
            _, memory_version = self._bump_versions(db, scope)
            return memory_version

    # --------------------------------------------------------- derived memory
    def save_derived_memory(self, scope: ScopeKey, kind: str, payload: str,
                            memory_version: int | None = None) -> DerivedMemory:
        """Store a derived report/index payload bound to a memory_version."""
        self._require_scope(scope)
        if not isinstance(kind, str) or not kind.strip() or len(kind) > 64:
            raise ValueError("kind 必须是 1..64 字符字符串。")
        if not isinstance(payload, str):
            raise TypeError("payload 必须是字符串。")
        with self._connect() as db:
            self._validate_scope(db, scope)
            current, _ = self._scope_state(db, scope)
            version = current if memory_version is None else int(memory_version)
            if version < 0:
                raise ValueError("memory_version 必须 >= 0。")
            memory_id = uuid.uuid4().hex
            db.execute(
                "INSERT INTO derived_memory(id, account_id, person_id, kind, "
                "memory_version, payload) VALUES(?,?,?,?,?,?)",
                (memory_id, scope.wechat_account_id, scope.person_id, kind,
                 version, self._seal(payload)))
            created_at = db.execute(
                "SELECT created_at FROM derived_memory WHERE id=?",
                (memory_id,)).fetchone()[0]
        return DerivedMemory(id=memory_id, kind=kind.strip(),
                             memory_version=version, payload=payload,
                             created_at=created_at)

    def list_derived_memory(self, scope: ScopeKey) -> list[DerivedMemory]:
        """Only payloads built from the *current* memory_version are returned."""
        self._require_scope(scope)
        with self._connect() as db:
            self._validate_scope(db, scope)
            _, memory_version = self._scope_state(db, scope)
            rows = db.execute(
                "SELECT id, kind, memory_version, payload, created_at "
                "FROM derived_memory WHERE account_id=? AND person_id=? "
                "AND memory_version=? ORDER BY created_at, id",
                (scope.wechat_account_id, scope.person_id, memory_version)).fetchall()
            return [DerivedMemory(id=row[0], kind=row[1], memory_version=row[2],
                                  payload=self._open(row[3]), created_at=row[4])
                    for row in rows]
