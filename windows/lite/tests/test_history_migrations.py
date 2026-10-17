"""T5 验收用例：0.2 加密 SQLite -> V2 的事务迁移、备份与 schema 门禁。

覆盖 PLAN T5 / 设计 §7.1：
- 构造真实的 0.2 schema（people/sources/messages）与旧加密内容，迁移前后
  原文数量、顺序、摘要一致；旧片段保留为 `fragment` 类型，不伪造为标准消息。
- 迁移中途失败回滚，不留下部分新表数据；迁移前生成同用户可解密的受控备份。
- `PRAGMA user_version` 门禁：更高/未知 schema 只读失败，不写入；再次迁移幂等。
- 迁移后持久内容仍由 DPAPI（或注入的等价 protect）保护。

只使用合成数据与临时目录。
"""

import hashlib
from contextlib import contextmanager
import sqlite3
import tempfile
import unittest
from pathlib import Path

from liteapp import settings
from liteapp.history import HistoryImportError, HistoryStore
from liteapp.history_migrations import (
    MigrationResult,
    SchemaVersionError,
    detect_schema_version,
    migrate_database,
)

OLD_SCHEMA = """
CREATE TABLE people (
    id TEXT PRIMARY KEY, account_hash TEXT NOT NULL, display_name BLOB NOT NULL);
CREATE TABLE sources (
    id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id),
    digest TEXT NOT NULL, kind TEXT NOT NULL, name BLOB NOT NULL,
    section_count INTEGER NOT NULL, char_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(person_id, digest));
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL, label BLOB NOT NULL, body BLOB NOT NULL);
CREATE INDEX messages_source ON messages(source_id, sequence);
"""

# 旧库账号/联系人/来源：两个账号，含同名联系人；每人 2 个来源 x 3 条片段。
OLD_ACCOUNTS = (
    ("account-a", (("person-a1", "示例客户"), ("person-a2", "同名联系人"))),
    ("account-b", (("person-b1", "同名联系人"), ("person-b2", "示例客户"))),
)


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


xor_unprotect = xor_protect


@contextmanager
def open_db(path):
    """sqlite3.Connection 的 ``with`` 只提交事务，不会关闭连接；显式关闭。"""
    db = sqlite3.connect(path)
    try:
        yield db
    finally:
        db.close()


def _sha256_text(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_v02_database(path, protect=xor_protect):
    """写入与 0.2 HistoryStore 完全一致的 schema 和合成加密内容。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    people = []
    expected = {}
    texts_in_order = []
    record_count = 0
    char_count = 0
    source_count = 0
    with open_db(path) as db:
        db.executescript(OLD_SCHEMA)
        for account_key, persons in OLD_ACCOUNTS:
            account_hash = _sha256_text(account_key)
            for person_id, display_name in persons:
                db.execute(
                    "INSERT INTO people(id,account_hash,display_name) VALUES(?,?,?)",
                    (person_id, account_hash, protect(display_name.encode("utf-8"))))
                person_expected = []
                people.append({"id": person_id, "account_key": account_key,
                               "display_name": display_name})
                for source_index in range(2):
                    source_id = f"src-{person_id}-{source_index}"
                    created_at = f"2025-01-{source_index + 1:02d} 10:0{source_index}:00"
                    texts = [
                        f"{display_name} 合成消息 {source_index}-{sequence}："
                        f"订单编号 {source_index * 10 + sequence + 1}，金额 {100 + sequence} 元"
                        for sequence in range(3)
                    ]
                    section_chars = sum(len(text) for text in texts)
                    db.execute(
                        "INSERT INTO sources(id,person_id,digest,kind,name,section_count,"
                        "char_count,created_at) VALUES(?,?,?,?,?,?,?,?)",
                        (source_id, person_id, "legacy-digest-" + hashlib.sha256(
                            f"{source_id}".encode()).hexdigest(),
                         "pdf" if source_index else "html",
                         protect(f"旧文件-{person_id}-{source_index}.{('pdf' if source_index else 'html')}".encode()),
                         len(texts), section_chars, created_at))
                    for sequence, text in enumerate(texts):
                        db.execute(
                            "INSERT INTO messages(source_id,sequence,label,body) VALUES(?,?,?,?)",
                            (source_id, sequence,
                             protect(f"片段 {sequence + 1}".encode("utf-8")),
                             protect(text.encode("utf-8"))))
                        person_expected.append(text)
                        texts_in_order.append(text)
                    record_count += len(texts)
                    char_count += section_chars
                    source_count += 1
                expected[person_id] = person_expected
        db.commit()
    legacy_digest = hashlib.sha256("\x00".join(texts_in_order).encode("utf-8")).hexdigest()
    return {
        "path": path,
        "people": people,
        "expected": expected,
        "digest": legacy_digest,
        "record_count": record_count,
        "char_count": char_count,
        "source_count": source_count,
    }


def scopes_by_person(store):
    result = {}
    for account_key, _persons in OLD_ACCOUNTS:
        for person in store.list_people(account_key):
            result[person.person_id] = person.scope
    return result


class MigrationTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder / "memory.sqlite3"
        self.backup_dir = self.folder / "history-backups"

    def migrate(self, **kwargs):
        options = {"protect": xor_protect, "unprotect": xor_protect,
                   "backup_dir": self.backup_dir}
        options.update(kwargs)
        return migrate_database(self.db, **options)

    def old_tables(self):
        with open_db(self.db) as db:
            return {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'")}

    def old_user_version(self):
        with open_db(self.db) as db:
            return db.execute("PRAGMA user_version").fetchone()[0]


class SuccessfulMigrationTests(MigrationTestCase):
    def test_v02_database_migrates_with_same_counts_order_digest_and_backup(self):
        fixture = build_v02_database(self.db)
        before_bytes = self.db.read_bytes()
        result = self.migrate()
        self.assertIsInstance(result, MigrationResult)
        self.assertTrue(result.migrated)
        self.assertFalse(result.created)
        self.assertEqual((result.from_version, result.to_version), (0, 2))
        self.assertEqual(result.record_count_before, fixture["record_count"])
        self.assertEqual(result.record_count_after, fixture["record_count"])
        self.assertEqual(result.source_count_before, fixture["source_count"])
        self.assertEqual(result.source_count_after, fixture["source_count"])
        self.assertEqual(result.char_count_before, fixture["char_count"])
        self.assertEqual(result.char_count_after, fixture["char_count"])
        self.assertEqual(result.digest_before, fixture["digest"])
        self.assertEqual(result.digest_after, fixture["digest"])
        self.assertNotEqual(before_bytes, self.db.read_bytes())
        self.assertEqual(self.old_user_version(), 2)

        # 受控备份：位于 data 目录内的受控子目录，且同用户可解密读取。
        self.assertIsNotNone(result.backup_path)
        self.assertTrue(result.backup_path.exists())
        self.assertEqual(result.backup_path.parent, self.backup_dir)
        self.assertIn(self.db.stem, result.backup_path.name)
        with open_db(result.backup_path) as backup:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 0)
            backup_count = backup.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            self.assertEqual(backup_count, fixture["record_count"])
            first_body = backup.execute(
                "SELECT body FROM messages ORDER BY id LIMIT 1").fetchone()[0]
            self.assertEqual(
                xor_protect(first_body).decode("utf-8"),
                fixture["expected"][fixture["people"][0]["id"]][0])

        # 迁移后按范围读取：数量、顺序、正文与旧库完全一致；旧片段保留 fragment。
        store = HistoryStore(self.db, protect=xor_protect, unprotect=xor_protect)
        scopes = scopes_by_person(store)
        self.assertEqual(set(scopes), {person["id"] for person in fixture["people"]})
        for person in fixture["people"]:
            scope = scopes[person["id"]]
            self.assertEqual(scope.person_id, person["id"])
            rows = list(store.iter_records(scope, 1))
            self.assertEqual([row.text for row in rows], fixture["expected"][person["id"]])
            self.assertEqual({row.record_type for row in rows}, {"fragment"},
                             "旧片段必须保留 fragment 类型，不伪造为标准消息")
            self.assertTrue(all(row.sender is None and row.sent_at is None for row in rows))
            sources = store.list_sources(scope)
            self.assertEqual([source.record_count for source in sources], [3, 3])
            self.assertEqual(sources[0].kind, "html")
            self.assertEqual(sources[1].kind, "pdf")
            self.assertEqual(sources[0].parser_version, "0.2")
            self.assertEqual(store.stats(scope).source_version, 1)
            self.assertEqual(store.stats(scope).memory_version, 1)
        # 同名联系人跨账号仍是不同的 ScopeKey，且记录互不可见。
        same_name = [scopes[person["id"]] for person in fixture["people"]
                     if person["display_name"] == "同名联系人"]
        self.assertEqual(len({scope.person_id for scope in same_name}), 2)
        self.assertNotEqual(same_name[0].wechat_account_id, same_name[1].wechat_account_id)

        # 迁移后数据库文件仍不含合成样本明文标识。
        raw = self.db.read_bytes()
        for text in fixture["expected"][fixture["people"][0]["id"]]:
            self.assertNotIn(text.encode("utf-8"), raw)
        self.assertNotIn("旧文件-".encode("utf-8"), raw)

    def test_migration_is_idempotent_and_does_not_create_more_backups(self):
        build_v02_database(self.db)
        first = self.migrate()
        self.assertTrue(first.migrated)
        backups_after_first = sorted(self.backup_dir.glob("*"))
        self.assertEqual(len(backups_after_first), 1)
        after_first = self.db.read_bytes()
        second = self.migrate()
        self.assertFalse(second.migrated)
        self.assertEqual(second.from_version, 2)
        self.assertIsNone(second.backup_path)
        self.assertEqual(sorted(self.backup_dir.glob("*")), backups_after_first)
        self.assertEqual(self.db.read_bytes(), after_first,
                         "已迁移数据库再次迁移不得改写内容")

    def test_history_store_auto_migrates_legacy_database_on_open(self):
        fixture = build_v02_database(self.db)
        store = HistoryStore(self.db, protect=xor_protect, unprotect=xor_unprotect)
        self.assertEqual(self.old_user_version(), 2)
        self.assertTrue((self.db.parent / "history-backups").is_dir(),
                        "默认备份目录应位于同一数据目录内")
        scopes = scopes_by_person(store)
        for person in fixture["people"]:
            scope = scopes[person["id"]]
            rows = store.list_messages(scope)
            self.assertEqual([row["text"] for row in rows], fixture["expected"][person["id"]])
            self.assertTrue(all(row["record_type"] == "fragment" for row in rows))
            self.assertEqual(rows[0]["label"], "片段 1")
            self.assertEqual(rows[0]["sequence"], 0)


class FailedMigrationTests(MigrationTestCase):
    def test_midway_failure_rolls_back_without_partial_new_tables(self):
        fixture = build_v02_database(self.db)
        before_bytes = self.db.read_bytes()

        def explode(stage):
            if stage == "after_records":
                raise RuntimeError("注入的迁移失败")

        with self.assertRaises(RuntimeError):
            self.migrate(hook=explode)
        # 旧库原样保留：版本、表、行数、字节全部不变；没有任何 V2 表残留。
        self.assertEqual(self.old_user_version(), 0)
        self.assertEqual(self.old_tables(), {"people", "sources", "messages"})
        self.assertEqual(self.db.read_bytes(), before_bytes)
        with open_db(self.db) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                             fixture["record_count"])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
                             fixture["source_count"])
            body = db.execute("SELECT body FROM messages ORDER BY id LIMIT 1").fetchone()[0]
            self.assertEqual(xor_protect(body).decode("utf-8"),
                             fixture["expected"][fixture["people"][0]["id"]][0])
        # 迁移前的受控备份仍然可用。
        backups = list(self.backup_dir.glob("*"))
        self.assertEqual(len(backups), 1)
        with open_db(backups[0]) as backup:
            self.assertEqual(backup.execute("SELECT COUNT(*) FROM messages").fetchone()[0],
                             fixture["record_count"])

        # 故障修复后重试可以完整迁移。
        result = self.migrate()
        self.assertTrue(result.migrated)
        store = HistoryStore(self.db, protect=xor_protect, unprotect=xor_unprotect)
        scope = scopes_by_person(store)[fixture["people"][0]["id"]]
        self.assertEqual([row.text for row in store.iter_records(scope, 1)],
                         fixture["expected"][fixture["people"][0]["id"]])

    def test_undecryptable_legacy_database_fails_before_any_write(self):
        build_v02_database(self.db)
        before_bytes = self.db.read_bytes()

        def wrong_unprotect(raw):
            return bytes(value ^ 0xFF for value in raw)

        with self.assertRaises(HistoryImportError):
            self.migrate(unprotect=wrong_unprotect)
        self.assertEqual(self.db.read_bytes(), before_bytes)
        self.assertEqual(self.old_user_version(), 0)
        self.assertEqual(self.old_tables(), {"people", "sources", "messages"})
        self.assertFalse(list(self.backup_dir.glob("*")) if self.backup_dir.exists() else [])


class SchemaGateTests(MigrationTestCase):
    def test_newer_or_unknown_schema_version_is_rejected_without_writing(self):
        for version in (1, 3, 99):
            with self.subTest(version=version):
                path = self.folder / f"schema-{version}.sqlite3"
                with open_db(path) as db:
                    db.execute("CREATE TABLE marker (id INTEGER)")
                    db.execute(f"PRAGMA user_version={version}")
                    db.commit()
                before = path.read_bytes()
                with self.assertRaises(SchemaVersionError):
                    migrate_database(path, protect=xor_protect, unprotect=xor_protect,
                                     backup_dir=self.backup_dir)
                self.assertEqual(path.read_bytes(), before)
                with self.assertRaises(SchemaVersionError):
                    HistoryStore(path, protect=xor_protect, unprotect=xor_unprotect)
                self.assertEqual(path.read_bytes(), before)

    def test_partial_or_mixed_legacy_schema_is_rejected(self):
        partial = self.folder / "partial.sqlite3"
        with open_db(partial) as db:
            db.execute("CREATE TABLE people (id TEXT PRIMARY KEY)")
        with self.assertRaises(SchemaVersionError):
            migrate_database(partial, protect=xor_protect, unprotect=xor_protect,
                             backup_dir=self.backup_dir)
        mixed = self.folder / "mixed.sqlite3"
        build_v02_database(mixed)
        with open_db(mixed) as db:
            db.execute("CREATE TABLE wechat_accounts (id TEXT PRIMARY KEY)")
            db.commit()
        with self.assertRaises(SchemaVersionError):
            migrate_database(mixed, protect=xor_protect, unprotect=xor_protect,
                             backup_dir=self.backup_dir)

    def test_open_store_stops_reading_when_newer_schema_appears(self):
        store = HistoryStore(self.db, protect=xor_protect, unprotect=xor_unprotect)
        scope = store.create_person("示例客户", "account-a")
        with open_db(self.db) as db:
            db.execute("PRAGMA user_version=99")
            db.commit()
        with self.assertRaises(SchemaVersionError):
            store.stats(scope)
        with self.assertRaises(SchemaVersionError):
            store.import_records(scope, _empty_source(), [])


def _empty_source():
    from liteapp.history_models import ImportSource
    return ImportSource(digest=hashlib.sha256(b"x").hexdigest(), kind="html", name="a.html")


class EmptyDatabaseTests(MigrationTestCase):
    def test_missing_or_empty_database_initializes_v2(self):
        missing = self.folder / "new.sqlite3"
        result = migrate_database(missing, protect=xor_protect, unprotect=xor_protect,
                                  backup_dir=self.backup_dir)
        self.assertFalse(result.migrated)
        self.assertTrue(result.created)
        self.assertEqual((result.from_version, result.to_version), (0, 2))
        self.assertIsNone(result.backup_path)
        self.assertEqual(detect_schema_version(missing), 2)
        store = HistoryStore(missing, protect=xor_protect, unprotect=xor_unprotect)
        scope = store.create_person("示例客户", "account-a")
        self.assertEqual(store.stats(scope).record_count, 0)

        empty = self.folder / "empty.sqlite3"
        empty.write_bytes(b"")
        created = migrate_database(empty, protect=xor_protect, unprotect=xor_protect,
                                   backup_dir=self.backup_dir)
        self.assertTrue(created.created)
        self.assertEqual(detect_schema_version(empty), 2)


class DpapiMigrationTests(MigrationTestCase):
    def test_dpapi_encrypted_legacy_database_migrates_and_stays_encrypted(self):
        marker = "SYNTHETIC-LEGACY-MARKER-7c3d"
        build_v02_database(self.db, protect=settings.crypt)
        # 注入一条带唯一标识的真实 DPAPI 加密片段。
        with open_db(self.db) as db:
            db.execute(
                "INSERT INTO messages(source_id,sequence,label,body) VALUES(?,?,?,?)",
                ("src-person-a1-0", 3,
                 settings.crypt("片段 4".encode("utf-8")),
                 settings.crypt((marker + " 合成迁移标记").encode("utf-8"))))
            db.execute("UPDATE sources SET section_count=section_count+1,"
                       "char_count=char_count+? WHERE id=?",
                       (len(marker + " 合成迁移标记"), "src-person-a1-0"))
            db.commit()
        result = migrate_database(
            self.db, protect=settings.crypt,
            unprotect=lambda raw: settings.crypt(raw, True),
            backup_dir=self.backup_dir)
        self.assertTrue(result.migrated)
        raw = self.db.read_bytes()
        self.assertNotIn(marker.encode("utf-8"), raw)
        store = HistoryStore(self.db)  # 默认 DPAPI
        scope = scopes_by_person(store)["person-a1"]
        rows = list(store.iter_records(scope, 1))
        self.assertIn(marker, "\n".join(row.text for row in rows))
        # 备份同样保持 DPAPI 密文而不是明文。
        self.assertNotIn(marker.encode("utf-8"), result.backup_path.read_bytes())


if __name__ == "__main__":
    unittest.main()
