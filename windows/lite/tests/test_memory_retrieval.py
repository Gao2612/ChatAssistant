import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from liteapp.history import HistoryStore
from liteapp.history_models import ImportedRecord, ImportSource
from liteapp.memory_retrieval import (
    MemoryRetriever, StaleMemoryVersionError, tokenize,
)


def fast_store(folder):
    return HistoryStore(path=Path(folder) / "memory.sqlite3",
                        protect=lambda raw: raw, unprotect=lambda raw: raw)


def make_source(digest="d1", name="chat.html"):
    real_digest = hashlib.sha256(str(digest).encode("utf-8")).hexdigest()
    return ImportSource(digest=real_digest, kind="html", name=name)


def import_texts(store, scope, texts, digest="d1"):
    return store.import_records(scope, make_source(digest),
                                [ImportedRecord(text=text, ordinal=index)
                                 for index, text in enumerate(texts)])


class MemoryRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = fast_store(self.folder.name)
        self.scope = self.store.create_person("Alice", "acct-1")

    def test_tokenize_handles_chinese_and_ascii(self):
        tokens = tokenize("报价 quote123 下周三")
        self.assertIn("报价", tokens)
        self.assertIn("quote123", tokens)
        self.assertIn("下周", tokens)

    def test_retrieves_early_fact_from_ten_thousand_records(self):
        texts = [f"第 {index} 条普通聊天，聊天气和吃饭。" for index in range(10000)]
        texts[0] = "我答应下周三给你报价单，价格按上次的折扣。"
        import_texts(self.store, self.scope, texts)
        snap = self.store.snapshot(self.scope)
        retriever = MemoryRetriever(self.store)
        context = retriever.retrieve(self.scope, "报价 答应 下周三", snap.memory_version)
        self.assertTrue(context.items)
        self.assertIn("报价单", context.items[0].text)
        self.assertEqual(context.items[0].ref.record_id, context.refs[0].record_id)
        evidence = self.store.read_evidence(context.items[0].ref)
        self.assertIn("下周三", evidence.text)
        self.assertLessEqual(context.bytes_used, 8192)

    def test_stale_memory_version_is_rejected(self):
        import_texts(self.store, self.scope, ["报价单"])
        snap = self.store.snapshot(self.scope)
        retriever = MemoryRetriever(self.store)
        with self.assertRaises(StaleMemoryVersionError):
            retriever.retrieve(self.scope, "报价", snap.memory_version - 1)
        sources = self.store.list_sources(self.scope)
        new_version = self.store.delete_source(self.scope, sources[0].source_id)
        with self.assertRaises(StaleMemoryVersionError):
            retriever.retrieve(self.scope, "报价", snap.memory_version)

    def test_retrieval_is_scope_isolated_even_with_same_display_name(self):
        other_scope = self.store.create_person("Alice", "acct-1")
        import_texts(self.store, self.scope, ["我答应下周三给 Alice 报价单"])
        import_texts(self.store, other_scope, ["完全无关的聊天内容"], digest="d2")
        snapshot = self.store.snapshot(self.scope)
        retriever = MemoryRetriever(self.store)
        context = retriever.retrieve(self.scope, "报价单", snapshot.memory_version)
        self.assertTrue(context.items)
        self.assertTrue(all(item.ref.scope == self.scope for item in context.items))
        for item in context.items:
            self.assertEqual(self.store.read_evidence(item.ref).ref.scope, self.scope)

    def test_bytes_and_item_limits_are_enforced(self):
        import_texts(self.store, self.scope, ["报价 " + "很长的内容" * 300 for _ in range(20)])
        snapshot = self.store.snapshot(self.scope)
        retriever = MemoryRetriever(self.store)
        context = retriever.retrieve(self.scope, "报价", snapshot.memory_version,
                                     max_items=3, max_bytes=512)
        self.assertLessEqual(len(context.items), 3)
        self.assertLessEqual(context.bytes_used, 512)

    def test_persist_uses_encrypted_derived_memory(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        store = HistoryStore(Path(folder.name) / "memory.sqlite3")
        scope = store.create_person("Bob", "acct-1")
        store.import_records(scope, make_source("dpapi-1"),
                             [ImportedRecord(text="答应周五发报价", ordinal=0)])
        snapshot = store.snapshot(scope)
        retriever = MemoryRetriever(store)
        context = retriever.retrieve(scope, "报价", snapshot.memory_version)
        derived = retriever.persist(context, store=store)
        self.assertEqual(derived.kind, "retrieval")
        rows = store.list_derived_memory(scope)
        self.assertEqual(len(rows), 1)
        payload = json.loads(rows[0].payload)
        self.assertEqual(payload["kind"], "retrieval_context")
        raw = (Path(folder.name) / "memory.sqlite3").read_bytes()
        self.assertNotIn("答应周五发报价".encode("utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
