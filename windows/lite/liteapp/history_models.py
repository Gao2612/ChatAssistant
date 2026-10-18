"""Immutable data models for the V2 (scope-enforced) history store.

T5 of ``docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md`` and design §7.1.
Every model is frozen and validates at construction time so an unowned, empty or
inverted identifier cannot even be represented. Display names, message bodies,
file names, locators and issue details are *content* and are persisted encrypted
by :mod:`liteapp.history`; IDs, digests, counts and version integers stay plain.

The module is free of I/O and Qt; tests, importers and the future analysis jobs
share it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

from .contracts import EvidenceRef, ScopeKey

__all__ = [
    "MAX_NAME_CHARS",
    "MAX_TEXT_CHARS",
    "PARSER_VERSION",
    "RECORD_TYPES",
    "SCHEMA_VERSION",
    "SOURCE_KINDS",
    "DerivedMemory",
    "EvidenceError",
    "EvidenceText",
    "HistoryError",
    "HistoryImportError",
    "HistoryRecord",
    "HistoryAccount",
    "ImportResult",
    "ImportSource",
    "ImportedRecord",
    "PageIssue",
    "PersonInfo",
    "ScopeMismatchError",
    "ScopeStats",
    "SchemaVersionError",
    "SourceInfo",
    "SourceNotFoundError",
    "SourceSnapshot",
    "StaleSourceVersionError",
    "decode_page_issues",
    "encode_page_issues",
    "from_json_text",
    "to_json_text",
]

SCHEMA_VERSION = 2
"""V2 schema version written to ``PRAGMA user_version``."""

RECORD_TYPES = ("message", "fragment")
SOURCE_KINDS = ("html", "pdf", "exportwechat_html")
PARSER_VERSION = "1"
MAX_TEXT_CHARS = 2_000_000
MAX_NAME_CHARS = 260
MAX_LABEL_CHARS = 200

_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class HistoryError(Exception):
    """Base class for history store failures."""


class HistoryImportError(HistoryError, ValueError):
    """Import/parse failure shown to the user (must stay a ValueError)."""


class EvidenceError(HistoryError, ValueError):
    """An evidence reference does not exist or its character range is invalid."""


class ScopeMismatchError(HistoryError, ValueError):
    """The requested person/source/record does not belong to the supplied scope."""


class SourceNotFoundError(HistoryError, ValueError):
    """The requested source does not exist inside the supplied scope."""


class StaleSourceVersionError(HistoryError, ValueError):
    """Caller frozen a source version that is no longer the current one."""


class SchemaVersionError(HistoryError, RuntimeError):
    """Database schema is newer/unknown or cannot be upgraded safely."""


def _require_text(value: Any, name: str, max_chars: int | None = None) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} 必须是字符串。")
    if "\x00" in value:
        raise ValueError(f"{name} 不能包含 NUL。")
    if not value.strip():
        raise ValueError(f"{name} 不能为空。")
    if max_chars is not None and len(value) > max_chars:
        raise ValueError(f"{name} 超过 {max_chars} 个字符。")
    return value


def _optional_text(value: Any, name: str, max_chars: int) -> str | None:
    if value is None:
        return None
    return _require_text(value, name, max_chars)


def _require_int(value: Any, name: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} 必须是整数。")
    if value < minimum:
        raise ValueError(f"{name} 必须 >= {minimum}。")
    return value


def _copy_mapping(value: Any, name: str) -> dict[str, Any] | None:
    """Copy an optional flat JSON-compatible mapping (locator/quality metadata)."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise TypeError(f"{name} 必须是映射或 None。")
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 80:
            raise ValueError(f"{name} 的键必须是 1..80 字符字符串。")
        if isinstance(item, (dict, list, tuple, set)):
            raise TypeError(f"{name} 只允许标量值，不支持嵌套结构。")
        copied[key] = item
    return copied


def to_json_text(value: Any) -> str:
    """Serialize scope-safe metadata deterministically for encrypted storage."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def from_json_text(raw: str | bytes | None) -> Any:
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(raw)


@dataclass(frozen=True)
class PageIssue:
    """One page-level parse/OCR problem. Diagnostics only, never chat content."""

    page: int
    code: str
    detail: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.page, bool) or not isinstance(self.page, int) or self.page < 1:
            raise ValueError("page 必须 >= 1。")
        _require_text(self.code, "code", 64)
        if not isinstance(self.detail, str):
            raise TypeError("detail 必须是字符串。")
        if "\x00" in self.detail or len(self.detail) > 300:
            raise ValueError("detail 过长或包含非法字符。")

    def as_dict(self) -> dict[str, Any]:
        return {"page": self.page, "code": self.code, "detail": self.detail}


def encode_page_issues(issues: tuple[PageIssue, ...]) -> str:
    return to_json_text([issue.as_dict() for issue in issues])


def decode_page_issues(raw: str | bytes | None) -> tuple[PageIssue, ...]:
    if not raw:
        return ()
    data = from_json_text(raw) or []
    return tuple(PageIssue(page=int(item["page"]), code=str(item["code"]),
                           detail=str(item.get("detail", ""))) for item in data)


@dataclass(frozen=True)
class ImportedRecord:
    """One parser output record, before it is assigned a stored record ID.

    ``ordinal`` is the original order inside the imported file (0-based).
    ``locator``/``quality`` are flat JSON-ish metadata: for PDF the page number,
    for HTML the paragraph/section position, plus quality flags. They never carry
    the body text itself.
    """

    text: str
    ordinal: int
    record_type: str = "message"
    sender: str | None = None
    sent_at: str | None = None
    locator: Mapping[str, Any] | None = None
    quality: Mapping[str, Any] | None = None
    record_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError("text 必须是字符串。")
        if not self.text.strip():
            raise HistoryImportError("记录正文不能为空。")
        if len(self.text) > MAX_TEXT_CHARS:
            raise HistoryImportError(f"单条记录超过 {MAX_TEXT_CHARS} 个字符。")
        _require_int(self.ordinal, "ordinal")
        if self.record_type not in RECORD_TYPES:
            raise HistoryImportError(f"未知记录类型：{self.record_type!r}")
        _optional_text(self.sender, "sender", MAX_LABEL_CHARS)
        _optional_text(self.sent_at, "sent_at", 80)
        object.__setattr__(self, "locator", _copy_mapping(self.locator, "locator"))
        object.__setattr__(self, "quality", _copy_mapping(self.quality, "quality"))
        if self.record_id is not None:
            _require_text(self.record_id, "record_id", 128)

    @property
    def label(self) -> str:
        return _record_label(self.record_type, self.ordinal, self.sender)


def _record_label(record_type: str, ordinal: int, sender: str | None) -> str:
    if record_type == "fragment":
        return f"片段 {ordinal + 1}"
    return sender or "消息"


@dataclass(frozen=True)
class ImportSource:
    """Metadata of one imported file.

    ``digest`` is the SHA-256 of the file bytes; the same digest in the same scope
    is a duplicate import. ``source_id`` is only set when revising an existing
    source (re-import) or in migration tests; a new import lets the store generate
    a UUID.
    """

    digest: str
    kind: str
    name: str
    parser_version: str = PARSER_VERSION
    import_version: int = 1
    source_id: str | None = None
    page_count: int = 0
    page_issues: tuple[PageIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.digest, str) or not _DIGEST_PATTERN.match(self.digest.lower()):
            raise ValueError("digest 必须是 64 位十六进制 SHA-256。")
        object.__setattr__(self, "digest", self.digest.lower())
        if self.kind not in SOURCE_KINDS:
            raise ValueError(f"未知来源格式：{self.kind!r}")
        _require_text(self.name, "name", MAX_NAME_CHARS)
        _require_text(self.parser_version, "parser_version", 40)
        _require_int(self.import_version, "import_version", minimum=1)
        if self.source_id is not None:
            _require_text(self.source_id, "source_id", 128)
        _require_int(self.page_count, "page_count")
        issues = tuple(self.page_issues or ())
        for issue in issues:
            if not isinstance(issue, PageIssue):
                raise TypeError("page_issues 只能包含 PageIssue。")
            if self.page_count and issue.page > self.page_count:
                raise ValueError("page_issues 的页码超出 page_count。")
        object.__setattr__(self, "page_issues", issues)


@dataclass(frozen=True)
class HistoryRecord:
    """A stored, decrypted record with its stable ID and character range size."""

    record_id: str
    source_id: str
    ordinal: int
    record_type: str
    sender: str | None
    sent_at: str | None
    text: str
    locator: Mapping[str, Any] | None
    quality: Mapping[str, Any] | None
    char_count: int

    def __post_init__(self) -> None:
        _require_text(self.record_id, "record_id", 128)
        _require_text(self.source_id, "source_id", 128)
        _require_int(self.ordinal, "ordinal")
        if self.record_type not in RECORD_TYPES:
            raise ValueError(f"未知记录类型：{self.record_type!r}")
        if not isinstance(self.text, str):
            raise TypeError("text 必须是字符串。")
        _require_int(self.char_count, "char_count")
        if self.char_count != len(self.text):
            raise ValueError("char_count 与正文长度不一致。")
        _optional_text(self.sender, "sender", MAX_LABEL_CHARS)
        _optional_text(self.sent_at, "sent_at", 80)

    @property
    def label(self) -> str:
        return _record_label(self.record_type, self.ordinal, self.sender)


@dataclass(frozen=True)
class PersonInfo:
    """A contact/person owned by one local WeChat account record."""

    account_id: str
    person_id: str
    display_name: str

    def __post_init__(self) -> None:
        _require_text(self.account_id, "account_id", 128)
        _require_text(self.person_id, "person_id", 128)
        _require_text(self.display_name, "display_name", MAX_LABEL_CHARS)

    @property
    def scope(self) -> ScopeKey:
        return ScopeKey(self.account_id, self.person_id)


@dataclass(frozen=True)
class HistoryAccount:
    """An app-owned local history bucket; label is display-only metadata."""

    account_id: str
    label: str
    legacy: bool = False

    def __post_init__(self) -> None:
        _require_text(self.account_id, "account_id", 128)
        _require_text(self.label, "label", MAX_LABEL_CHARS)
        if not isinstance(self.legacy, bool):
            raise TypeError("legacy 必须是 bool。")


@dataclass(frozen=True)
class SourceInfo:
    """Stored metadata of one import source. ``name``/``page_issues`` decrypted."""

    source_id: str
    account_id: str
    person_id: str
    digest: str
    kind: str
    parser_version: str
    import_version: int
    name: str
    page_count: int
    record_count: int
    char_count: int
    failure_count: int
    page_issues: tuple[PageIssue, ...]
    created_at: str

    def __post_init__(self) -> None:
        _require_text(self.source_id, "source_id", 128)
        _require_text(self.account_id, "account_id", 128)
        _require_text(self.person_id, "person_id", 128)
        _require_text(self.name, "name", MAX_NAME_CHARS)
        _require_int(self.import_version, "import_version", minimum=1)
        for name in ("page_count", "record_count", "char_count", "failure_count"):
            _require_int(getattr(self, name), name)
        object.__setattr__(self, "page_issues", tuple(self.page_issues or ()))


@dataclass(frozen=True)
class SourceSnapshot:
    """Frozen manifest: source IDs, versions and record/character ranges.

    Analysis and reports freeze this snapshot instead of re-reading "whatever is
    current": deleting or importing a source changes the version and makes an old
    snapshot unusable for new results.
    """

    scope: ScopeKey
    source_version: int
    memory_version: int
    sources: tuple[SourceInfo, ...]
    ranges: tuple[EvidenceRef, ...]
    record_count: int
    char_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        _require_int(self.source_version, "source_version")
        _require_int(self.memory_version, "memory_version")
        _require_int(self.record_count, "record_count")
        _require_int(self.char_count, "char_count")
        sources = tuple(self.sources or ())
        ranges = tuple(self.ranges or ())
        for source in sources:
            if not isinstance(source, SourceInfo):
                raise TypeError("sources 只能包含 SourceInfo。")
        for ref in ranges:
            if not isinstance(ref, EvidenceRef):
                raise TypeError("ranges 只能包含 EvidenceRef。")
            if ref.scope != self.scope:
                raise ValueError("快照范围不得包含其它 ScopeKey 的证据引用。")
        object.__setattr__(self, "sources", sources)
        object.__setattr__(self, "ranges", ranges)
        if len(ranges) != self.record_count:
            raise ValueError("ranges 数量必须等于 record_count。")
        if sum(ref.end - ref.start for ref in ranges) != self.char_count:
            raise ValueError("ranges 字符数必须等于 char_count。")

    @property
    def source_ids(self) -> tuple[str, ...]:
        return tuple(source.source_id for source in self.sources)


@dataclass(frozen=True)
class ImportResult:
    source_id: str
    duplicate: bool
    record_count: int
    char_count: int
    page_count: int
    failure_count: int
    source_version: int
    memory_version: int
    page_issues: tuple[PageIssue, ...] = ()
    retried: bool = False
    """True when this import retried failed pages of an existing source (R-04)."""

    def __post_init__(self) -> None:
        _require_text(self.source_id, "source_id", 128)
        if not isinstance(self.duplicate, bool):
            raise TypeError("duplicate 必须是布尔值。")
        for name in ("record_count", "char_count", "page_count", "failure_count",
                     "source_version", "memory_version"):
            _require_int(getattr(self, name), name)
        if not isinstance(self.retried, bool):
            raise TypeError("retried 必须是布尔值。")
        object.__setattr__(self, "page_issues", tuple(self.page_issues or ()))


@dataclass(frozen=True)
class EvidenceText:
    """Decrypted evidence slice plus enough metadata to jump back to the source."""

    ref: EvidenceRef
    text: str
    source_name: str
    record_type: str
    sender: str | None
    sent_at: str | None
    locator: Mapping[str, Any] | None
    record_char_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.ref, EvidenceRef):
            raise TypeError("ref 必须是 EvidenceRef。")
        if not isinstance(self.text, str):
            raise TypeError("text 必须是字符串。")
        _require_text(self.source_name, "source_name", MAX_NAME_CHARS)
        if self.record_type not in RECORD_TYPES:
            raise ValueError(f"未知记录类型：{self.record_type!r}")
        _require_int(self.record_char_count, "record_char_count")
        if self.ref.end > self.record_char_count:
            raise ValueError("证据范围超出记录长度。")


@dataclass(frozen=True)
class ScopeStats:
    """Deterministic per-scope counts; fragments are not inflated into messages."""

    source_count: int
    record_count: int
    char_count: int
    page_count: int
    failure_count: int
    source_version: int
    memory_version: int

    def __post_init__(self) -> None:
        for name in ("source_count", "record_count", "char_count", "page_count",
                     "failure_count", "source_version", "memory_version"):
            _require_int(getattr(self, name), name)


@dataclass(frozen=True)
class DerivedMemory:
    """Derived/published memory payload bound to the memory_version it was built from."""

    id: str
    kind: str
    memory_version: int
    payload: str
    created_at: str

    def __post_init__(self) -> None:
        _require_text(self.id, "id", 128)
        _require_text(self.kind, "kind", 64)
        _require_int(self.memory_version, "memory_version")
        if not isinstance(self.payload, str):
            raise TypeError("payload 必须是字符串。")
