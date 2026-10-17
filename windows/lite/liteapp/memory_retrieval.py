"""Local BM25 memory retrieval over one imported history scope (T9).

The retriever only ever reads inside an explicit :class:`ScopeKey`; the store
enforces ownership and the in-memory index is rebuilt when the scope's
``memory_version`` changes. Search is local Chinese word/character-fragment
matching plus BM25, no embedding service and no network.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import re
from threading import RLock

from .contracts import EvidenceRef, ScopeKey
from .history_models import (
    ImportedRecord, ImportSource, SourceNotFoundError, StaleSourceVersionError,
)

TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff]")
DEFAULT_MAX_ITEMS = 8
DEFAULT_MAX_BYTES = 8192


class StaleMemoryVersionError(ValueError):
    """Raised when a caller built a query against an outdated memory version."""


def tokenize(text: str) -> list[str]:
    """ASCII words plus CJK unigrams and bigrams for local matching."""
    lowered = (text or "").lower()
    tokens = TOKEN_RE.findall(lowered)
    cjk = [token for token in tokens if len(token) == 1 and "\u3400" <= token <= "\u9fff"]
    bigrams = [cjk[index] + cjk[index + 1] for index in range(len(cjk) - 1)]
    return tokens + bigrams


@dataclass(frozen=True)
class MemoryItem:
    text: str
    ref: EvidenceRef
    score: float
    source_name: str = ""
    sender: str = ""
    sent_at: str = ""


@dataclass(frozen=True)
class MemoryContext:
    scope: ScopeKey
    memory_version: int
    source_version: int
    items: tuple[MemoryItem, ...]
    bytes_used: int
    coverage: float
    query_terms: tuple[str, ...] = ()

    @property
    def refs(self) -> tuple[EvidenceRef, ...]:
        return tuple(item.ref for item in self.items)


class MemoryRetriever:
    """Per-scope in-memory BM25 index; switching scope drops the old index."""

    def __init__(self, store) -> None:
        self.store = store
        self._lock = RLock()
        self._scope: ScopeKey | None = None
        self._memory_version = -1
        self._source_version = -1
        self._documents: list[dict] = []
        self._doc_freq: dict[str, int] = {}
        self._total_chars = 0
        self._avg_len = 1.0

    # ---------------------------------------------------------------- index
    def _build_index(self, scope: ScopeKey):
        snapshot = self.store.snapshot(scope)
        with self._lock:
            if (self._scope == scope and self._memory_version == snapshot.memory_version
                    and self._source_version == snapshot.source_version):
                return snapshot
            source_names = {item.source_id: item.name for item in snapshot.sources}
            documents, doc_freq = [], {}
            total_chars = 0
            records = list(self.store.iter_records(scope, snapshot.source_version))
            for record in records:
                tokens = tokenize(record.text)
                counts: dict[str, int] = {}
                for token in tokens:
                    counts[token] = counts.get(token, 0) + 1
                for token in counts:
                    doc_freq[token] = doc_freq.get(token, 0) + 1
                documents.append({
                    "record": record,
                    "tokens": tokens,
                    "counts": counts,
                    "source_name": source_names.get(record.source_id, ""),
                })
                total_chars += len(record.text)
            self._scope = scope
            self._memory_version = snapshot.memory_version
            self._source_version = snapshot.source_version
            self._documents = documents
            self._doc_freq = doc_freq
            self._total_chars = total_chars
            self._avg_len = max(1.0, sum(len(d["tokens"]) for d in documents) / max(1, len(documents)))
            return snapshot

    def clear(self, scope: ScopeKey | None = None) -> None:
        with self._lock:
            if scope is None or scope == self._scope:
                self._scope = None
                self._memory_version = -1
                self._source_version = -1
                self._documents = []
                self._doc_freq = {}
                self._total_chars = 0
                self._avg_len = 1.0

    # -------------------------------------------------------------- retrieve
    def retrieve(self, scope: ScopeKey, query: str, memory_version: int,
                 max_items: int = DEFAULT_MAX_ITEMS,
                 max_bytes: int = DEFAULT_MAX_BYTES) -> MemoryContext:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        if not isinstance(query, str):
            raise TypeError("query 必须是字符串。")
        snapshot = self._build_index(scope)
        if int(memory_version) != snapshot.memory_version:
            raise StaleMemoryVersionError(
                f"记忆版本已变化：请求 {memory_version}，当前 {snapshot.memory_version}。")
        max_items = max(1, min(int(max_items), 20))
        max_bytes = max(256, min(int(max_bytes), 32768))
        terms = tokenize(query)
        query_counts: dict[str, int] = {}
        for token in terms:
            query_counts[token] = query_counts.get(token, 0) + 1
        total_docs = max(1, len(self._documents))
        scored = []
        for document in self._documents:
            score = 0.0
            length = max(1, len(document["tokens"]))
            for token, query_tf in query_counts.items():
                tf = document["counts"].get(token, 0)
                if not tf:
                    continue
                df = self._doc_freq.get(token, 0)
                idf = math.log((total_docs - df + 0.5) / (df + 0.5) + 1.0)
                denom = tf + 1.5 * (1.0 - 0.75 + 0.75 * length / self._avg_len)
                score += idf * (tf * 2.5 / denom) * (1.0 + math.log(query_tf))
            if score > 0:
                scored.append((score, document))
        scored.sort(key=lambda item: (-item[0], item[1]["record"].source_id,
                                      item[1]["record"].ordinal))
        return self._materialize(scope, snapshot, scored, terms, max_items, max_bytes)

    def _materialize(self, scope, snapshot, scored, terms, max_items, max_bytes):
        items: list[MemoryItem] = []
        used = 0
        for score, document in scored:
            record = document["record"]
            text = record.text
            remaining = max_bytes - used
            if remaining <= 0:
                break
            if remaining < 120 and items:
                break
            encoded = len(text.encode("utf-8"))
            if encoded > remaining:
                text = text.encode("utf-8")[:remaining].decode("utf-8", "ignore")
                encoded = len(text.encode("utf-8"))
            if not text:
                break
            ref = EvidenceRef(scope=scope, source_id=record.source_id,
                              record_id=record.record_id, start=0, end=len(text))
            items.append(MemoryItem(text=text, ref=ref, score=round(score, 4),
                                    source_name=document["source_name"],
                                    sender=record.sender or "",
                                    sent_at=record.sent_at or ""))
            used += encoded
            if len(items) >= max_items:
                break
        coverage = (used / self._total_chars) if self._total_chars else 0.0
        return MemoryContext(scope=scope, memory_version=snapshot.memory_version,
                             source_version=snapshot.source_version,
                             items=tuple(items), bytes_used=used,
                             coverage=round(min(1.0, coverage), 4),
                             query_terms=tuple(dict.fromkeys(terms))[:32])

    # --------------------------------------------------------------- persist
    def persist(self, context: MemoryContext, store=None) -> object:
        """Persist the retrieval result as an encrypted derived-memory payload."""
        target = store or self.store
        payload = json.dumps({
            "kind": "retrieval_context", "memory_version": context.memory_version,
            "source_version": context.source_version,
            "items": [{"record_id": item.ref.record_id, "source_id": item.ref.source_id,
                       "text": item.text, "score": item.score} for item in context.items],
        }, ensure_ascii=False)
        return target.save_derived_memory(context.scope, "retrieval", payload,
                                          memory_version=context.memory_version)


def import_records(store, scope: ScopeKey, records: list[str], *,
                   digest: str = "synthetic", name: str = "synthetic.txt") -> dict:
    """Test/utility helper: import plain text fragments (never used with real chats)."""
    imported = [ImportedRecord(text=text, ordinal=index)
                for index, text in enumerate(records)]
    return store.import_records(scope, ImportSource(digest=digest, kind="html", name=name),
                                imported)
