"""Transactional 0.2 -> V2 migration for the encrypted history SQLite database.

Design §7.1 / PLAN T5:

* ``PRAGMA user_version`` is the schema gate. A database written by a newer app is
  rejected before any write; an unknown version is rejected instead of guessed.
* The 0.2 schema (``people`` / ``sources`` / ``messages``) is migrated inside a
  single transaction: counts, record order and the SHA-256 digest over the
  decrypted texts are identical before and after. Legacy sections keep the
  ``fragment`` record type instead of being presented as standard messages.
* A controlled, same-user-decryptable backup is created and verified before the
  transaction starts. Any failure rolls the transaction back, so no partial V2
  table/data is left behind.
* Ciphertext blobs are copied byte-for-byte; the migration never re-encrypts
  content, so DPAPI-protected material stays DPAPI-protected.

No Qt, no chat content in logs: only counts, codes and version integers.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from .history_models import (
    SCHEMA_VERSION,
    HistoryImportError,
    SchemaVersionError,
    encode_page_issues,
    to_json_text,
)

__all__ = [
    "MigrationResult",
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "create_v2_schema",
    "detect_schema_version",
    "migrate_database",
]

LEGACY_TABLES = ("people", "sources", "messages")
V2_TABLES = ("wechat_accounts", "people", "sources", "records", "scope_state",
             "derived_memory")
# Tables that exist only in V2; used to detect a partial/foreign V2 schema without
# confusing it with the legacy tables that share the people/sources names.
V2_ONLY_TABLES = ("wechat_accounts", "records", "scope_state", "derived_memory")
LEGACY_PARSER_VERSION = "0.2"

_SCHEMA_STATEMENTS = (
    """CREATE TABLE IF NOT EXISTS wechat_accounts (
        id TEXT PRIMARY KEY,
        legacy_hash TEXT,
        label BLOB NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    "CREATE UNIQUE INDEX IF NOT EXISTS wechat_accounts_legacy_hash "
    "ON wechat_accounts(legacy_hash)",
    """CREATE TABLE IF NOT EXISTS people (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES wechat_accounts(id) ON DELETE CASCADE,
        display_name BLOB NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    "CREATE INDEX IF NOT EXISTS people_account ON people(account_id)",
    """CREATE TABLE IF NOT EXISTS sources (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL REFERENCES wechat_accounts(id),
        person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        digest TEXT NOT NULL,
        kind TEXT NOT NULL,
        parser_version TEXT NOT NULL,
        import_version INTEGER NOT NULL DEFAULT 1,
        name BLOB NOT NULL,
        page_count INTEGER NOT NULL DEFAULT 0,
        record_count INTEGER NOT NULL,
        char_count INTEGER NOT NULL,
        failure_count INTEGER NOT NULL DEFAULT 0,
        page_issues BLOB,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(person_id, digest))""",
    "CREATE INDEX IF NOT EXISTS sources_scope ON sources(account_id, person_id)",
    """CREATE TABLE IF NOT EXISTS records (
        record_id TEXT PRIMARY KEY,
        source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        account_id TEXT NOT NULL,
        person_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        record_type TEXT NOT NULL,
        sender BLOB,
        sent_at BLOB,
        body BLOB NOT NULL,
        locator BLOB,
        quality BLOB,
        char_count INTEGER NOT NULL,
        UNIQUE(source_id, ordinal))""",
    "CREATE INDEX IF NOT EXISTS records_scope ON records(account_id, person_id)",
    "CREATE INDEX IF NOT EXISTS records_source ON records(source_id, ordinal)",
    """CREATE TABLE IF NOT EXISTS scope_state (
        account_id TEXT NOT NULL REFERENCES wechat_accounts(id) ON DELETE CASCADE,
        person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        source_set_version INTEGER NOT NULL DEFAULT 0,
        memory_version INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY(account_id, person_id))""",
    """CREATE TABLE IF NOT EXISTS derived_memory (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        memory_version INTEGER NOT NULL,
        payload BLOB NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)""",
    "CREATE INDEX IF NOT EXISTS derived_memory_scope "
    "ON derived_memory(account_id, person_id)",
)

@dataclass(frozen=True)
class MigrationResult:
    """Outcome of :func:`migrate_database`; counts/digests are real measurements."""

    migrated: bool
    created: bool
    from_version: int
    to_version: int
    backup_path: Path | None = None
    record_count_before: int | None = None
    record_count_after: int | None = None
    source_count_before: int | None = None
    source_count_after: int | None = None
    char_count_before: int | None = None
    char_count_after: int | None = None
    digest_before: str | None = None
    digest_after: str | None = None


@dataclass
class _LegacyPeopleRow:
    person_id: str
    account_hash: str
    display_name: str
    display_name_blob: bytes


@dataclass
class _LegacySourceRow:
    source_id: str
    person_id: str
    digest: str
    kind: str
    name_blob: bytes
    section_count: int
    char_count: int
    created_at: str


@dataclass
class _LegacyDatabase:
    people: list[_LegacyPeopleRow]
    sources: list[_LegacySourceRow]
    # person_id -> [texts in original order]
    texts_by_person: dict[str, list[str]]
    # source_id -> [(sequence, text, label_blob, body_blob)]
    messages_by_source: dict[str, list[tuple[int, str, bytes, bytes]]]
    digest: str
    record_count: int
    char_count: int


def _require_callable(value: Any, name: str) -> Callable:
    if not callable(value):
        raise TypeError(f"{name} 必须是可调用对象。")
    return value


def _notify(hook: Callable[[str], None] | None, stage: str) -> None:
    if hook is not None:
        hook(stage)


def _seal(protect: Callable[[bytes], bytes], text: str) -> bytes:
    return protect(text.encode("utf-8"))


def _open_text(unprotect: Callable[[bytes], bytes], blob: bytes | None) -> str:
    if not blob:
        return ""
    return unprotect(blob).decode("utf-8")


def _legacy_digest(texts: list[str]) -> str:
    return hashlib.sha256("\x00".join(texts).encode("utf-8")).hexdigest()


def _internal_tables(db: sqlite3.Connection) -> set[str]:
    return {row[0] for row in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


@contextmanager
def _db_connect(path: Path | str) -> Iterator[sqlite3.Connection]:
    """Explicitly close every connection (``with sqlite3.connect`` only commits)."""
    db = sqlite3.connect(path)
    try:
        yield db
    finally:
        db.close()


def detect_schema_version(path: Path | str) -> int:
    """Return ``PRAGMA user_version`` (0 for a missing/empty file)."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        with _db_connect(path) as db:
            row = db.execute("PRAGMA user_version").fetchone()
    except sqlite3.DatabaseError as error:
        raise SchemaVersionError(f"数据库文件无法识别：{error}") from None
    return int(row[0]) if row else 0


def create_v2_schema(db: sqlite3.Connection) -> None:
    """Create every V2 table/index inside the caller's transaction.

    Deliberately uses individual ``execute`` calls: ``executescript`` would issue
    an implicit COMMIT and could leave a partially migrated database.
    """
    for statement in _SCHEMA_STATEMENTS:
        db.execute(statement)


def _initialize_new_database(path: Path, protect: Callable[[bytes], bytes]) -> None:
    _require_callable(protect, "protect")
    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            create_v2_schema(db)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
    finally:
        db.close()


def _read_legacy_database(path: Path,
                          unprotect: Callable[[bytes], bytes]) -> _LegacyDatabase:
    """Read and validate every legacy row before modifying anything."""
    try:
        with _db_connect(path) as db:
            db.execute("PRAGMA foreign_keys=ON")
            people_rows = db.execute(
                "SELECT id, account_hash, display_name FROM people ORDER BY rowid").fetchall()
            source_rows = db.execute(
                "SELECT id, person_id, digest, kind, name, section_count, char_count, "
                "created_at FROM sources ORDER BY rowid").fetchall()
            message_rows = db.execute(
                "SELECT source_id, sequence, label, body FROM messages "
                "ORDER BY source_id, sequence").fetchall()
    except sqlite3.DatabaseError as error:
        raise HistoryImportError(f"旧数据库无法读取：{error}") from None

    people: list[_LegacyPeopleRow] = []
    person_ids: set[str] = set()
    try:
        for person_id, account_hash, display_blob in people_rows:
            display_name = _open_text(unprotect, display_blob)
            if not display_name.strip():
                raise HistoryImportError("旧数据库联系人名称为空，无法安全迁移。")
            if not isinstance(account_hash, str) or not account_hash.strip():
                raise HistoryImportError("旧数据库账号摘要为空，无法安全迁移。")
            if person_id in person_ids:
                raise HistoryImportError("旧数据库联系人 ID 重复，无法安全迁移。")
            person_ids.add(person_id)
            people.append(_LegacyPeopleRow(person_id=person_id,
                                           account_hash=account_hash,
                                           display_name=display_name,
                                           display_name_blob=display_blob))
        sources: list[_LegacySourceRow] = []
        source_ids: set[str] = set()
        for (source_id, person_id, digest, kind, name_blob, section_count,
             char_count, created_at) in source_rows:
            if person_id not in person_ids:
                raise HistoryImportError("旧数据库来源引用了不存在的联系人。")
            if source_id in source_ids:
                raise HistoryImportError("旧数据库来源 ID 重复，无法安全迁移。")
            source_ids.add(source_id)
            _open_text(unprotect, name_blob)  # 先验证文件名可解密
            sources.append(_LegacySourceRow(
                source_id=source_id, person_id=person_id, digest=digest, kind=kind,
                name_blob=name_blob, section_count=int(section_count),
                char_count=int(char_count), created_at=str(created_at)))
        messages_by_source: dict[str, list[tuple[int, str, bytes, bytes]]] = {
            source.source_id: [] for source in sources}
        for source_id, sequence, label_blob, body_blob in message_rows:
            if source_id not in messages_by_source:
                raise HistoryImportError("旧数据库片段引用了不存在的来源。")
            text = _open_text(unprotect, body_blob)
            _open_text(unprotect, label_blob)
            messages_by_source[source_id].append(
                (int(sequence), text, label_blob, body_blob))
    except (UnicodeDecodeError, OSError) as error:
        raise HistoryImportError("旧数据库内容无法用当前用户密钥解密，未做任何修改。") from None

    # 每个来源：序号必须严格递增，数量/字符数与旧元数据一致。
    for source in sources:
        messages = messages_by_source[source.source_id]
        sequences = [item[0] for item in messages]
        if sequences != sorted(set(sequences)):
            raise HistoryImportError("旧数据库片段顺序异常，未做任何修改。")
        if len(messages) != source.section_count:
            raise HistoryImportError("旧数据库片段数量与来源元数据不一致，未做任何修改。")
        actual_chars = sum(len(item[1]) for item in messages)
        if actual_chars != source.char_count:
            raise HistoryImportError("旧数据库字符数与来源元数据不一致，未做任何修改。")

    texts_by_person: dict[str, list[str]] = {person.person_id: [] for person in people}
    sources_by_person: dict[str, list[_LegacySourceRow]] = {
        person.person_id: [] for person in people}
    for source in sources:
        sources_by_person[source.person_id].append(source)
    texts_in_order: list[str] = []
    for person in people:
        for source in sources_by_person[person.person_id]:
            for _sequence, text, _label, _body in messages_by_source[source.source_id]:
                texts_by_person[person.person_id].append(text)
                texts_in_order.append(text)
    return _LegacyDatabase(
        people=people, sources=sources, texts_by_person=texts_by_person,
        messages_by_source=messages_by_source,
        digest=_legacy_digest(texts_in_order),
        record_count=len(texts_in_order),
        char_count=sum(len(text) for text in texts_in_order),
    )


def _make_backup(path: Path, backup_dir: Path | None) -> Path:
    """Copy the legacy database with SQLite's online backup API, then verify it."""
    folder = Path(backup_dir) if backup_dir else path.parent / "history-backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    suffix = path.suffix or ".sqlite3"
    destination = folder / f"{path.stem}-v0-{stamp}-{time.time_ns() % 1_000_000:06d}{suffix}"
    source_db = sqlite3.connect(path)
    try:
        target_db = sqlite3.connect(destination)
        try:
            source_db.backup(target_db)
        finally:
            target_db.close()
    finally:
        source_db.close()
    try:
        with _db_connect(destination) as backup:
            version = backup.execute("PRAGMA user_version").fetchone()[0]
            message_count = backup.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            source_count = backup.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    except sqlite3.DatabaseError as error:
        raise HistoryImportError(f"迁移备份无法校验：{error}") from None
    if version != 0 or message_count is None or source_count is None:
        raise HistoryImportError("迁移备份校验失败，已停止迁移。")
    return destination


def _account_id(account_hash: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL,
                      f"chatassistant-lite:legacy-account:{account_hash}").hex


def _record_id(source_id: str, sequence: int) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL,
                      f"chatassistant-lite:legacy-record:{source_id}:{sequence}").hex


def _insert_legacy_rows(db: sqlite3.Connection, legacy: _LegacyDatabase,
                        protect: Callable[[bytes], bytes]) -> dict[str, str]:
    """Insert the migrated rows; returns old person_id -> new account_id mapping."""
    account_ids: dict[str, str] = {}
    person_account: dict[str, str] = {}
    for person in legacy.people:
        account_hash = person.account_hash
        if account_hash not in account_ids:
            account_id = _account_id(account_hash)
            account_ids[account_hash] = account_id
            db.execute(
                "INSERT INTO wechat_accounts(id, legacy_hash, label, created_at) "
                "VALUES(?,?,?,CURRENT_TIMESTAMP)",
                (account_id, account_hash,
                 _seal(protect, "旧账号 " + account_hash[:8])))
        person_account[person.person_id] = account_ids[account_hash]
    for person in legacy.people:
        db.execute(
            "INSERT INTO people(id, account_id, display_name, created_at) "
            "VALUES(?,?,?,CURRENT_TIMESTAMP)",
            (person.person_id, person_account[person.person_id],
             person.display_name_blob))
    for source in legacy.sources:
        db.execute(
            "INSERT INTO sources(id, account_id, person_id, digest, kind, "
            "parser_version, import_version, name, page_count, record_count, "
            "char_count, failure_count, page_issues, created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (source.source_id, person_account[source.person_id], source.person_id,
             source.digest, source.kind, LEGACY_PARSER_VERSION, 1, source.name_blob,
             0, source.section_count, source.char_count, 0,
             _seal(protect, encode_page_issues(())), source.created_at))
        for sequence, text, _label, body_blob in legacy.messages_by_source[source.source_id]:
            locator = to_json_text({"kind": "legacy_section", "section": sequence + 1})
            quality = to_json_text({"legacy": True, "fragment": True})
            db.execute(
                "INSERT INTO records(record_id, source_id, account_id, person_id, "
                "ordinal, record_type, sender, sent_at, body, locator, quality, "
                "char_count) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (_record_id(source.source_id, sequence), source.source_id,
                 person_account[source.person_id], source.person_id, sequence,
                 "fragment", None, None, body_blob, _seal(protect, locator),
                 _seal(protect, quality), len(text)))
    for person in legacy.people:
        has_sources = any(source.person_id == person.person_id
                          for source in legacy.sources)
        version = 1 if has_sources else 0
        db.execute(
            "INSERT INTO scope_state(account_id, person_id, source_set_version, "
            "memory_version, updated_at) VALUES(?,?,?,?,CURRENT_TIMESTAMP)",
            (person_account[person.person_id], person.person_id, version, version))
    return person_account


def _verify_v2_data(db: sqlite3.Connection, legacy: _LegacyDatabase,
                    unprotect: Callable[[bytes], bytes]) -> None:
    """Compare the new tables against the validated legacy snapshot (inside txn)."""
    record_count = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    source_count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    people_count = db.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    if record_count != legacy.record_count or source_count != len(legacy.sources) \
            or people_count != len(legacy.people):
        raise HistoryImportError("迁移校验失败：记录/来源/联系人数量不一致。")
    rows = db.execute(
        "SELECT p.id, s.id, r.ordinal, r.record_type, r.body, r.char_count "
        "FROM people p JOIN sources s ON s.person_id=p.id "
        "JOIN records r ON r.source_id=s.id "
        "ORDER BY p.rowid, s.rowid, r.ordinal").fetchall()
    texts_by_person: dict[str, list[str]] = {person.person_id: [] for person in legacy.people}
    texts_in_order: list[str] = []
    for person_id, _source_id, _ordinal, record_type, body, char_count in rows:
        if record_type != "fragment":
            raise HistoryImportError("迁移校验失败：旧片段未保留 fragment 类型。")
        try:
            text = _open_text(unprotect, body)
        except (UnicodeDecodeError, OSError):
            raise HistoryImportError("迁移校验失败：迁移后正文无法解密。") from None
        if len(text) != char_count:
            raise HistoryImportError("迁移校验失败：记录字符数不一致。")
        texts_by_person[person_id].append(text)
        texts_in_order.append(text)
    for person_id, expected in legacy.texts_by_person.items():
        if texts_by_person.get(person_id) != expected:
            raise HistoryImportError("迁移校验失败：记录顺序或正文与旧库不一致。")
    if _legacy_digest(texts_in_order) != legacy.digest:
        raise HistoryImportError("迁移校验失败：摘要与旧库不一致。")


def _drop_legacy_tables(db: sqlite3.Connection) -> None:
    for table in ("legacy_messages", "legacy_sources", "legacy_people"):
        db.execute(f"DROP TABLE IF EXISTS {table}")


def _post_commit_verify(path: Path, legacy: _LegacyDatabase,
                        unprotect: Callable[[bytes], bytes]) -> None:
    try:
        with _db_connect(path) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version != SCHEMA_VERSION:
                raise SchemaVersionError("迁移后 schema 版本校验失败。")
            _verify_v2_data(db, legacy, unprotect)
    except sqlite3.DatabaseError as error:
        raise SchemaVersionError(f"迁移后数据库无法校验：{error}") from None


def _restore_backup(path: Path, backup_path: Path | None) -> None:
    if backup_path is None or not backup_path.exists():
        return
    try:
        shutil.copy2(backup_path, path)
    except OSError:
        pass


def migrate_database(path: Path | str, *, protect: Callable[[bytes], bytes],
                     unprotect: Callable[[bytes], bytes],
                     backup_dir: Path | str | None = None,
                     hook: Callable[[str], None] | None = None) -> MigrationResult:
    """Create or transactionally migrate the history database to V2.

    ``protect``/``unprotect`` are the store's DPAPI-compatible byte transforms;
    injected test transforms are supported. ``hook`` receives migration stage
    names and exists so tests can inject a mid-transaction failure.
    """
    _require_callable(protect, "protect")
    _require_callable(unprotect, "unprotect")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path = Path(backup_dir) if backup_dir is not None else None

    if not path.exists() or path.stat().st_size == 0:
        _initialize_new_database(path, protect)
        return MigrationResult(migrated=False, created=True, from_version=0,
                               to_version=SCHEMA_VERSION, backup_path=None,
                               record_count_before=0, record_count_after=0,
                               source_count_before=0, source_count_after=0,
                               char_count_before=0, char_count_after=0,
                               digest_before=_legacy_digest([]),
                               digest_after=_legacy_digest([]))

    version = detect_schema_version(path)
    if version == SCHEMA_VERSION:
        with _db_connect(path) as db:
            tables = _internal_tables(db)
            missing = [table for table in V2_TABLES if table not in tables]
            if missing:
                raise SchemaVersionError(
                    "数据库标记为 V2 但缺少表：" + ", ".join(missing))
            record_count = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            source_count = db.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
            char_count = db.execute(
                "SELECT COALESCE(SUM(char_count),0) FROM records").fetchone()[0]
        return MigrationResult(migrated=False, created=False,
                               from_version=version, to_version=SCHEMA_VERSION,
                               backup_path=None,
                               record_count_before=record_count,
                               record_count_after=record_count,
                               source_count_before=source_count,
                               source_count_after=source_count,
                               char_count_before=char_count,
                               char_count_after=char_count)
    if version != 0:
        raise SchemaVersionError(
            f"未知或不支持的数据库 schema 版本 {version}；已停止写入，请使用匹配版本。")

    with _db_connect(path) as probe:
        tables = _internal_tables(probe)
    present_v2 = [table for table in V2_ONLY_TABLES if table in tables]
    present_legacy = [table for table in LEGACY_TABLES if table in tables]
    extra = tables - set(LEGACY_TABLES)
    if present_v2:
        raise SchemaVersionError("数据库包含部分 V2 表，拒绝自动迁移以免覆盖。")
    if not tables:
        _initialize_new_database(path, protect)
        return MigrationResult(migrated=False, created=True, from_version=0,
                               to_version=SCHEMA_VERSION, backup_path=None,
                               record_count_before=0, record_count_after=0,
                               source_count_before=0, source_count_after=0,
                               char_count_before=0, char_count_after=0,
                               digest_before=_legacy_digest([]),
                               digest_after=_legacy_digest([]))
    if present_legacy != list(LEGACY_TABLES):
        raise SchemaVersionError(
            "检测到不完整或未知的旧 schema（表：" + ", ".join(sorted(tables))
            + "），拒绝自动迁移。")
    if extra:
        raise SchemaVersionError(
            "旧数据库包含未知表（" + ", ".join(sorted(extra)) + "），拒绝自动迁移。")

    legacy = _read_legacy_database(path, unprotect)
    controlled_backup = _make_backup(path, backup_path)
    texts_before = [text for person in legacy.people
                    for text in legacy.texts_by_person[person.person_id]]
    result = MigrationResult(
        migrated=True, created=False, from_version=0, to_version=SCHEMA_VERSION,
        backup_path=controlled_backup,
        record_count_before=legacy.record_count, record_count_after=legacy.record_count,
        source_count_before=len(legacy.sources), source_count_after=len(legacy.sources),
        char_count_before=legacy.char_count, char_count_after=legacy.char_count,
        digest_before=_legacy_digest(texts_before),
        digest_after=_legacy_digest(texts_before),
    )

    db = sqlite3.connect(path, isolation_level=None)
    try:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE")
        try:
            _notify(hook, "before_rename")
            for table in ("messages", "sources", "people"):
                db.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
            _notify(hook, "after_rename")
            create_v2_schema(db)
            _notify(hook, "after_schema")
            _insert_legacy_rows(db, legacy, protect)
            _notify(hook, "after_records")
            _verify_v2_data(db, legacy, unprotect)
            _drop_legacy_tables(db)
            _notify(hook, "before_commit")
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.execute("COMMIT")
        except BaseException:
            try:
                db.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        db.close()

    try:
        _post_commit_verify(path, legacy, unprotect)
    except BaseException:
        _restore_backup(path, controlled_backup)
        raise
    return result
