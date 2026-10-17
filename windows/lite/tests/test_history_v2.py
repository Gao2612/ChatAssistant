"""T5 验收用例：历史数据 V2、强制 ScopeKey 与范围隔离。

覆盖 PLAN T5：
- 跨账号 person_id/source_id 的读、删、导入均被拒绝；同名联系人独立。
- 旧 `list/search/stats/list_messages/...` 不再接受 person_id 快捷入口，必须 ScopeKey。
- import_records 幂等（同文件摘要）、版本递增、事务性；delete_source 提高
  memory_version 并使派生记忆失效；snapshot 固定来源/记录/字符范围清单。
- read_evidence 校验范围与归属；持久内容仍由 DPAPI（或注入的等价 protect）加密。

仅使用合成数据；临时数据库放在临时目录，不接触真实聊天。
"""

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from liteapp import settings
from liteapp.contracts import EvidenceRef, ScopeKey
from liteapp.history import (
    HistoryImportError,
    HistoryStore,
    ScopeMismatchError,
    SourceNotFoundError,
    StaleSourceVersionError,
)
from liteapp.history_models import (
    SCHEMA_VERSION,
    EvidenceText,
    HistoryRecord,
    ImportResult,
    ImportedRecord,
    ImportSource,
    PageIssue,
)


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def file_digest(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


class _StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.db = self.folder / "memory.sqlite3"
        self.store = HistoryStore(
            self.db, protect=xor_protect, unprotect=xor_unprotect,
        )

    def scope(self, name="示例客户", account="account-a"):
        return self.store.create_person(name, account)

    def source(self, seed="file-1", *, kind="html", name="合成记录.html",
               source_id=None, page_count=1, page_issues=()):
        return ImportSource(
            digest=file_digest(seed), kind=kind, name=name,
            source_id=source_id, page_count=page_count, page_issues=page_issues,
        )

    @staticmethod
    def records(*texts, record_type="message"):
        return [ImportedRecord(text=text, ordinal=index, record_type=record_type)
                for index, text in enumerate(texts)]

    def import_basic(self, scope, *texts, seed="file-1"):
        return self.store.import_records(scope, self.source(seed), self.records(*texts))


class SchemaAndScopeTests(_StoreTestCase):
    def test_new_store_uses_v2_schema_and_create_person_returns_scope(self):
        first = self.scope("示例客户", "account-a")
        second = self.scope("示例客户", "account-a")
        other_account = self.scope("示例客户", "account-b")
        self.assertIsInstance(first, ScopeKey)
        self.assertIsInstance(other_account, ScopeKey)
        self.assertNotEqual(first.person_id, second.person_id,
                            "同一账号下同名联系人也必须是独立 UUID")
        self.assertNotEqual(first.wechat_account_id, other_account.wechat_account_id)
        self.assertNotEqual(first, other_account)
        db = sqlite3.connect(self.db)
        try:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            db.close()
        for required in ("wechat_accounts", "people", "sources", "records",
                         "scope_state", "derived_memory"):
            self.assertIn(required, tables)
        people = self.store.list_people("account-a")
        self.assertEqual([person.scope for person in people], [first, second])

    def test_scope_key_is_mandatory_for_every_read_and_search_entry_point(self):
        scope = self.scope()
        self.import_basic(scope, "合成消息")
        person_id = scope.person_id
        calls = [
            lambda: self.store.list_sources(person_id),
            lambda: self.store.list_messages(person_id),
            lambda: self.store.search(person_id, "合成"),
            lambda: self.store.stats(person_id),
            lambda: self.store.build_context(person_id),
            lambda: self.store.snapshot(person_id),
            lambda: list(self.store.iter_records(person_id, 1)),
            lambda: self.store.delete_source(person_id, "source"),
            lambda: self.store.read_evidence(person_id),
        ]
        for call in calls:
            with self.assertRaises(TypeError):
                call()
        # 旧的 person_id 跨账号快捷入口必须已不存在。
        self.assertFalse(hasattr(self.store, "list_people_by_person"))

    def test_scope_with_person_of_another_account_is_rejected(self):
        first = self.scope("同名联系人", "account-a")
        second = self.scope("同名联系人", "account-b")
        forged = ScopeKey(first.wechat_account_id, second.person_id)
        operations = [
            lambda: self.store.list_sources(forged),
            lambda: self.store.stats(forged),
            lambda: self.store.snapshot(forged),
            lambda: list(self.store.iter_records(forged, 0)),
            lambda: self.store.import_records(forged, self.source(), self.records("x")),
            lambda: self.store.delete_source(forged, "source"),
            lambda: self.store.save_derived_memory(forged, "profile", "伪造"),
            lambda: self.store.list_derived_memory(forged),
        ]
        for operation in operations:
            with self.assertRaises(ScopeMismatchError):
                operation()
        # 反向拼接同样被拒绝，且不产生任何记录。
        reverse = ScopeKey(second.wechat_account_id, first.person_id)
        with self.assertRaises(ScopeMismatchError):
            self.store.stats(reverse)
        self.assertEqual(self.store.stats(first).record_count, 0)
        self.assertEqual(self.store.stats(second).record_count, 0)


class ImportAndVersionTests(_StoreTestCase):
    def test_import_records_returns_versions_and_iterates_in_original_order(self):
        scope = self.scope()
        source = self.source("file-order", page_count=2)
        records = [
            ImportedRecord(text="第一条：预算 3000 元", ordinal=0, record_type="message",
                           sender="客户", sent_at="2025-01-02 10:00",
                           locator={"kind": "html", "paragraph": 3},
                           quality={"ocr": False}),
            ImportedRecord(text="第二条：周五交期可以吗？", ordinal=1, record_type="fragment",
                           locator={"kind": "page", "page": 2}),
        ]
        result = self.store.import_records(scope, source, records)
        self.assertIsInstance(result, ImportResult)
        self.assertFalse(result.duplicate)
        self.assertEqual(result.record_count, 2)
        self.assertEqual(result.char_count, sum(len(record.text) for record in records))
        self.assertEqual(result.page_count, 2)
        self.assertEqual(result.failure_count, 0)
        self.assertEqual((result.source_version, result.memory_version), (1, 1))

        rows = list(self.store.iter_records(scope, result.source_version))
        self.assertEqual([row.text for row in rows],
                         ["第一条：预算 3000 元", "第二条：周五交期可以吗？"])
        self.assertEqual([row.ordinal for row in rows], [0, 1])
        self.assertEqual([row.record_type for row in rows], ["message", "fragment"])
        self.assertIsInstance(rows[0], HistoryRecord)
        self.assertEqual(rows[0].sender, "客户")
        self.assertEqual(rows[0].sent_at, "2025-01-02 10:00")
        self.assertEqual(rows[0].locator["paragraph"], 3)
        self.assertEqual(rows[1].locator["page"], 2)
        self.assertEqual(rows[0].source_id, result.source_id)

        stats = self.store.stats(scope)
        self.assertEqual((stats.source_count, stats.record_count, stats.char_count), (1, 2, result.char_count))
        self.assertEqual((stats.source_version, stats.memory_version), (1, 1))

        snapshot = self.store.snapshot(scope)
        self.assertEqual(snapshot.source_version, 1)
        self.assertEqual(snapshot.record_count, 2)
        self.assertEqual(snapshot.source_ids, (result.source_id,))
        self.assertEqual([ref.record_id for ref in snapshot.ranges],
                         [row.record_id for row in rows])
        self.assertEqual([(ref.start, ref.end) for ref in snapshot.ranges],
                         [(0, len(rows[0].text)), (0, len(rows[1].text))])

    def test_same_file_digest_is_idempotent_without_bumping_versions(self):
        scope = self.scope()
        source = self.source("file-idempotent")
        first = self.store.import_records(scope, source, self.records("重复文件正文"))
        again = self.store.import_records(scope, source, self.records("重复文件正文"))
        self.assertTrue(again.duplicate)
        self.assertEqual(again.source_id, first.source_id)
        self.assertEqual((again.source_version, again.memory_version), (1, 1))
        self.assertEqual(self.store.stats(scope).source_version, 1)
        self.assertEqual(self.store.stats(scope).record_count, 1)

    def test_reimport_with_same_source_id_replaces_records_atomically(self):
        scope = self.scope()
        first = self.import_basic(scope, "旧正文一", "旧正文二", seed="file-rev-1")
        revision = ImportSource(
            digest=file_digest("file-rev-2"), kind="html", name="合成记录.html",
            source_id=first.source_id,
        )
        result = self.store.import_records(
            scope, revision,
            [ImportedRecord(text="新正文", ordinal=0, record_type="message")],
        )
        self.assertFalse(result.duplicate)
        self.assertEqual(result.source_id, first.source_id)
        self.assertEqual((result.source_version, result.memory_version), (2, 2))
        rows = list(self.store.iter_records(scope, result.source_version))
        self.assertEqual([row.text for row in rows], ["新正文"])
        sources = self.store.list_sources(scope)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].import_version, 2)
        self.assertEqual(sources[0].record_count, 1)

    def test_import_validation_failures_leave_no_partial_source_or_version(self):
        scope = self.scope()
        before = self.store.stats(scope)
        with self.assertRaises(HistoryImportError):
            self.store.import_records(scope, self.source(), [])
        with self.assertRaises(TypeError):
            self.store.import_records(scope, self.source(), ["裸字符串不是 ImportedRecord"])
        with self.assertRaises(ValueError):
            self.store.import_records(scope, self.source(), [ImportedRecord(text="   ", ordinal=0)])
        with self.assertRaises(HistoryImportError):
            self.store.import_records(scope, self.source(), [
                ImportedRecord(text="重复序号一", ordinal=0),
                ImportedRecord(text="重复序号二", ordinal=0),
            ])
        after = self.store.stats(scope)
        self.assertEqual(after, before)
        self.assertEqual(self.store.list_sources(scope), [])

    def test_stale_source_version_is_rejected(self):
        scope = self.scope()
        first = self.import_basic(scope, "第一版", seed="file-stale-1")
        self.import_basic(scope, "第二版", seed="file-stale-2")
        with self.assertRaises(StaleSourceVersionError):
            list(self.store.iter_records(scope, first.source_version))
        self.assertEqual([row.text for row in self.store.iter_records(scope, 2)],
                         ["第一版", "第二版"])


class CrossAccountIsolationTests(_StoreTestCase):
    def test_same_display_name_contacts_are_independent(self):
        first = self.scope("同名联系人", "account-a")
        second = self.scope("同名联系人", "account-b")
        self.import_basic(first, "只属于甲的证据", seed="same-digest")
        self.import_basic(second, "只属于乙的证据", seed="same-digest")
        self.assertEqual([row.text for row in self.store.iter_records(first, 1)], ["只属于甲的证据"])
        self.assertEqual([row.text for row in self.store.iter_records(second, 1)], ["只属于乙的证据"])
        self.assertEqual(len(self.store.search(first, "证据")), 1)
        self.assertIn("只属于甲", self.store.search(first, "甲")[0]["text"])
        self.assertEqual(self.store.search(second, "甲"), [])
        self.assertEqual(self.store.stats(first).source_count, 1)
        self.assertEqual(self.store.stats(second).source_count, 1)
        # 同一账号内两个同名联系人同样互不合并。
        third = self.scope("同名联系人", "account-a")
        self.assertNotEqual(third.person_id, first.person_id)
        self.assertEqual(self.store.stats(third).record_count, 0)

    def test_cross_account_evidence_and_delete_are_rejected(self):
        first = self.scope("甲", "account-a")
        second = self.scope("乙", "account-b")
        first_source = self.import_basic(first, "甲的证据正文", seed="file-cross-a")
        self.import_basic(second, "乙的证据正文", seed="file-cross-b")
        first_record = list(self.store.iter_records(first, 1))[0]
        cross_ref = EvidenceRef(scope=second, source_id=first_record.source_id,
                                record_id=first_record.record_id, start=0, end=2)
        with self.assertRaises(ScopeMismatchError):
            self.store.read_evidence(cross_ref)
        before_second = self.store.snapshot(second)
        with self.assertRaises(ScopeMismatchError):
            self.store.delete_source(second, first_source.source_id)
        self.assertEqual(self.store.snapshot(second), before_second,
                         "拒绝跨账号删除后不得改变目标范围版本")
        self.assertEqual(len(list(self.store.iter_records(first, 1))), 1,
                         "拒绝跨账号删除后原来源必须完好")

    def test_cross_account_import_with_foreign_source_id_is_rejected(self):
        first = self.scope("甲", "account-a")
        second = self.scope("乙", "account-b")
        first_source = self.import_basic(first, "甲", seed="file-import-a")
        foreign = ImportSource(
            digest=file_digest("file-import-b"), kind="html", name="乙.html",
            source_id=first_source.source_id,
        )
        with self.assertRaises(ScopeMismatchError):
            self.store.import_records(second, foreign, self.records("试图占用甲来源"))
        self.assertEqual(self.store.stats(second).record_count, 0)
        self.assertEqual(np_records(self.store, first), 1)


def np_records(store, scope):
    """读取当前版本记录数（测试辅助；不进入产品代码）。"""
    version = store.stats(scope).source_version
    return len(list(store.iter_records(scope, version)))


class EvidenceAndSnapshotTests(_StoreTestCase):
    def test_read_evidence_validates_range_source_and_scope(self):
        scope = self.scope()
        result = self.store.import_records(scope, self.source("file-evidence"), [
            ImportedRecord(text="0123456789", ordinal=0, record_type="message",
                           sender="客户", sent_at="2025-02-03 09:00",
                           locator={"kind": "pdf", "page": 3}),
        ])
        record = list(self.store.iter_records(scope, result.source_version))[0]
        evidence = self.store.read_evidence(
            EvidenceRef(scope, result.source_id, record.record_id, 2, 5))
        self.assertIsInstance(evidence, EvidenceText)
        self.assertEqual(evidence.text, "234")
        self.assertEqual(evidence.source_name, "合成记录.html")
        self.assertEqual(evidence.record_type, "message")
        self.assertEqual(evidence.sender, "客户")
        self.assertEqual(evidence.sent_at, "2025-02-03 09:00")
        self.assertEqual(evidence.locator["page"], 3)
        self.assertEqual(evidence.record_char_count, 10)
        with self.assertRaises(ValueError):
            self.store.read_evidence(
                EvidenceRef(scope, result.source_id, record.record_id, 0, 100))
        with self.assertRaises(ValueError):
            self.store.read_evidence(
                EvidenceRef(scope, result.source_id, "不存在的记录", 0, 1))
        with self.assertRaises(ValueError):
            self.store.read_evidence(
                EvidenceRef(scope, "不存在的来源", record.record_id, 0, 1))

    def test_snapshot_freezes_sources_records_and_ranges(self):
        scope = self.scope()
        first = self.store.import_records(scope, self.source("file-snap-1", page_count=3),
                                    self.records("甲一", "甲二"))
        second = self.store.import_records(scope, self.source("file-snap-2", page_count=1),
                                     self.records("乙一"))
        snapshot = self.store.snapshot(scope)
        self.assertEqual(snapshot.scope, scope)
        self.assertEqual(snapshot.source_version, 2)
        self.assertEqual(snapshot.memory_version, 2)
        self.assertEqual(snapshot.source_ids, (first.source_id, second.source_id))
        self.assertEqual(snapshot.record_count, 3)
        self.assertEqual(snapshot.char_count, len("甲一") + len("甲二") + len("乙一"))
        self.assertEqual([(source.source_id, source.page_count) for source in snapshot.sources],
                         [(first.source_id, 3), (second.source_id, 1)])
        self.assertEqual(len(snapshot.ranges), 3)
        self.assertTrue(all(ref.scope == scope for ref in snapshot.ranges))
        # 新导入不会改写已冻结的快照。
        later = self.store.import_records(scope, self.source("file-snap-3"), self.records("丙一"))
        self.assertEqual(snapshot.source_version, 2)
        self.assertNotEqual(self.store.snapshot(scope).source_version, snapshot.source_version)
        self.assertEqual(later.source_version, 3)

    def test_snapshot_of_unknown_scope_is_rejected(self):
        unknown = ScopeKey("account-x", "person-x")
        with self.assertRaises(ScopeMismatchError):
            self.store.snapshot(unknown)


class DeleteAndMemoryVersionTests(_StoreTestCase):
    def test_delete_source_bumps_memory_version_and_invalidates_derived_memory(self):
        scope = self.scope()
        result = self.import_basic(scope, "待删除来源正文", seed="file-delete")
        self.import_basic(scope, "保留来源正文", seed="file-keep")
        memory = self.store.save_derived_memory(scope, "profile", "合成派生记忆")
        self.assertEqual(memory.memory_version, 2)
        self.assertEqual(len(self.store.list_derived_memory(scope)), 1)

        new_memory_version = self.store.delete_source(scope, result.source_id)
        self.assertIsInstance(new_memory_version, int)
        self.assertEqual(new_memory_version, 3)
        stats = self.store.stats(scope)
        self.assertEqual((stats.source_version, stats.memory_version), (3, 3))
        self.assertEqual(stats.source_count, 1)
        self.assertEqual([row.text for row in self.store.iter_records(scope, 3)],
                         ["保留来源正文"])
        self.assertEqual(self.store.list_derived_memory(scope), [])
        snapshot = self.store.snapshot(scope)
        self.assertEqual(snapshot.memory_version, 3)
        self.assertEqual(snapshot.source_ids, (self.store.list_sources(scope)[0].source_id,))
        # 再删已不存在的来源必须明确失败，而不是静默成功。
        with self.assertRaises(SourceNotFoundError):
            self.store.delete_source(scope, result.source_id)

    def test_derived_memory_is_bound_to_current_memory_version(self):
        scope = self.scope()
        self.import_basic(scope, "正文", seed="file-memory")
        saved = self.store.save_derived_memory(scope, "report", "合成报告载荷")
        self.assertEqual(saved.memory_version, 1)
        self.assertEqual(self.store.list_derived_memory(scope)[0].payload, "合成报告载荷")
        # 新导入提高 memory_version 后，旧派生记忆不再作为当前结果返回。
        self.import_basic(scope, "新正文", seed="file-memory-2")
        self.assertEqual(self.store.list_derived_memory(scope), [])

    def test_evidence_after_source_delete_is_rejected(self):
        scope = self.scope()
        result = self.import_basic(scope, "删除后不可再读", seed="file-gone")
        record = list(self.store.iter_records(scope, 1))[0]
        ref = EvidenceRef(scope, result.source_id, record.record_id, 0, 2)
        self.assertEqual(self.store.read_evidence(ref).text, "删除")
        self.store.delete_source(scope, result.source_id)
        with self.assertRaises(ValueError):
            self.store.read_evidence(ref)


class ImportFileCompatibilityTests(_StoreTestCase):
    def test_import_file_requires_scope_strips_scripts_and_is_idempotent(self):
        scope = self.scope()
        html = self.folder / "sample.html"
        html.write_text(
            '<html><head><title>会话</title>'
            '<script>fetch("https://evil.test")</script></head>'
            '<body><div>张三 2025-01-02 10:00</div><div>请问设备还有现货吗？</div>'
            '<img alt="图片" src="https://evil.test/a.png"></body></html>',
            encoding="utf-8",
        )
        first = self.store.import_file(scope, html)
        again = self.store.import_file(scope, html)
        self.assertFalse(first["duplicate"])
        self.assertTrue(again["duplicate"])
        texts = [row["text"] for row in self.store.list_messages(scope)]
        joined = "\n".join(texts)
        self.assertIn("请问设备还有现货吗", joined)
        self.assertNotIn("fetch", joined)
        self.assertNotIn("https://evil.test", joined)
        with self.assertRaises(TypeError):
            self.store.import_file(scope.person_id, html)
        text_file = self.folder / "bad.txt"
        text_file.write_text("hello", encoding="utf-8")
        with self.assertRaises(HistoryImportError):
            self.store.import_file(scope, text_file)


class PersistenceEncryptionTests(unittest.TestCase):
    def test_persistent_content_stays_dpapi_encrypted(self):
        """使用真实 DPAPI（settings.crypt）验证正文/文件名/联系人名不落明文。"""
        marker = "SYNTHETIC-MARKER-4f8a2c7d"
        name_marker = "合成文件-9d1e"
        person_marker = "合成联系人标记"
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / "memory.sqlite3"
            store = HistoryStore(db)
            scope = store.create_person(person_marker, "account-dpapi")
            result = store.import_records(
                scope,
                ImportSource(digest=file_digest("dpapi"), kind="html", name=name_marker + ".html"),
                [ImportedRecord(text=f"{marker} 这是一条合成消息", ordinal=0)],
            )
            raw = db.read_bytes()
            for secret in (marker, name_marker, person_marker):
                self.assertNotIn(secret.encode("utf-8"), raw,
                                 f"数据库文件不得包含明文标识 {secret!r}")
            # 同一用户重新打开数据库仍能解密读取。
            reopened = HistoryStore(db)
            rows = list(reopened.iter_records(scope, result.source_version))
            self.assertEqual(len(rows), 1)
            self.assertIn(marker, rows[0].text)
            self.assertEqual(reopened.list_sources(scope)[0].name, name_marker + ".html")
            self.assertEqual(reopened.list_people("account-dpapi")[0].display_name,
                             person_marker)


class ImportedRecordModelTests(unittest.TestCase):
    def test_record_model_defaults_and_validation(self):
        record = ImportedRecord(text="正文", ordinal=0)
        self.assertEqual(record.record_type, "message")
        self.assertIsNone(record.sender)
        self.assertIsNone(record.sent_at)
        self.assertIsNone(record.locator)
        fragment = ImportedRecord(text="旧片段", ordinal=1, record_type="fragment",
                                  locator={"page": 2})
        self.assertEqual(fragment.locator, {"page": 2})
        self.assertEqual(fragment.label, "片段 2")
        with self.assertRaises(ValueError):
            ImportedRecord(text="", ordinal=0)
        with self.assertRaises(ValueError):
            ImportedRecord(text="正文", ordinal=-1)
        with self.assertRaises(ValueError):
            ImportedRecord(text="正文", ordinal=0, record_type="未知类型")

    def test_import_source_validation(self):
        source = ImportSource(digest=file_digest("x"), kind="html", name="a.html")
        self.assertEqual(source.parser_version, "1")
        with self.assertRaises(ValueError):
            ImportSource(digest="不是摘要", kind="html", name="a.html")
        with self.assertRaises(ValueError):
            ImportSource(digest=file_digest("x"), kind="未知格式", name="a.html")
        with self.assertRaises(ValueError):
            ImportSource(digest=file_digest("x"), kind="pdf", name="")


class PageIssueTests(_StoreTestCase):
    def test_page_level_issues_are_persisted_as_counts_and_metadata(self):
        scope = self.scope()
        issues = (PageIssue(page=2, code="encrypted_page", detail="页面需要密码"),
                  PageIssue(page=5, code="corrupt_page", detail="页面结构损坏"))
        source = self.source("file-issues", kind="pdf", name="扫描件.pdf",
                             page_count=5, page_issues=issues)
        result = self.store.import_records(scope, source, self.records("可提取的一页正文"))
        self.assertEqual(result.page_count, 5)
        self.assertEqual(result.failure_count, 2)
        self.assertEqual([(issue.page, issue.code) for issue in result.page_issues],
                         [(2, "encrypted_page"), (5, "corrupt_page")])
        info = self.store.list_sources(scope)[0]
        self.assertEqual(info.page_count, 5)
        self.assertEqual(info.failure_count, 2)
        self.assertEqual([(issue.page, issue.code) for issue in info.page_issues],
                         [(2, "encrypted_page"), (5, "corrupt_page")])


if __name__ == "__main__":
    unittest.main()
