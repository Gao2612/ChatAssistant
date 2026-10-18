"""ExportWeChat companion ``*.html.json`` parser (calibrated on a real export).

Rationale (root cause of a real user report): the newer ExportWeChat export
writes the *complete* conversation into a companion file next to the rendered
HTML — ``<name>.html`` is only a shell whose ``#chat-container`` holds a few
placeholder rows and the body is loaded by JavaScript from
``<name>.html.json``. The generic HTML parser (which deliberately never runs
scripts) could therefore only ever recover those few placeholder lines.

The companion file is a plain JSON array, so it can be read with the standard
library only: no script is executed, no remote resource is requested and nothing
is fetched from the network. Only the fields needed for a message record are
copied; ``avatar_src``/``path``/``url``/``xml_dict`` are never resolved, opened
or logged.

Safety / boundaries:

* every record keeps its original array index in ``ordinal`` so the evidence can
  be traced back without guessing; skipped entries leave a gap but never a
  duplicate index;
* only meaningful text becomes an :class:`ImportedRecord` (the model rejects an
  empty body outright), so images/emoji are counted and skipped instead of
  producing empty rows;
* ``MAX_IMPORT_FILE_BYTES``, ``HTML_MAX_RECORDS``, a wall-clock timeout and the
  cancellation token are all honoured; texts longer than ``MAX_TEXT_CHARS`` are
  truncated and counted;
* notes carry counts and type identifiers only — never chat content.
"""

from __future__ import annotations

import codecs
import html as html_module
import json
from pathlib import Path
from typing import Any, Callable, Iterator

from ..contracts import CancellationToken, CancelledError
from ..history_models import (
    MAX_LABEL_CHARS,
    MAX_TEXT_CHARS,
    HistoryImportError,
    ImportedRecord,
)
from .base import (
    HTML_MAX_RECORDS,
    HTML_PARSE_TIMEOUT_SECONDS,
    MAX_IMPORT_FILE_BYTES,
    FormatMatch,
    Parser,
    checked_file,
    read_limited_bytes,
)

__all__ = [
    "COMPANION_SUFFIX",
    "MESSAGE_TYPES",
    "PARSER_NAME",
    "ExportWeChatJSONParser",
    "companion_path",
    "decode_json_text",
    "probe",
]

PARSER_NAME = "exportwechat_json"
"""Stable parser identity persisted on the import source.

It deliberately differs from ``html_generic``/``exportwechat_html``: the store
uses it to recognise "same bytes, better parser" and replace a previously
partial import in place instead of skipping it as an idempotent duplicate.

``kind`` stays ``exportwechat_html`` because ``base.FORMAT_KINDS`` (and the V2
schema) only knows ``html``/``pdf``/``exportwechat_html``.
"""

COMPANION_SUFFIX = ".json"
"""The companion file is ``<rendered file name> + .json`` (``a.html`` -> ``a.html.json``)."""

SIGNATURE_KEY = "StrTime"
"""Kept next to :data:`IDENTITY_KEYS` as the primary message signature marker."""
IDENTITY_KEYS = ("StrTime", "type", "is_send")
CONFIDENCE = "signature"
SUPPORT_LEVEL = "exact"
PROBE_HEAD_BYTES = 16 * 1024
PROBE_MAX_BYTES = 512 * 1024
PROBE_SCAN_ELEMENTS = 3
HTTP_SUFFIXES = (".html", ".htm")

# ExportWeChat type identifiers observed in a real export (all JSON strings).
TYPE_TEXT = "1"
TYPE_IMAGE = "3"
TYPE_VOICE = "50"
TYPE_EMOJI = "47"
TYPE_LOCATION = "48"
TYPE_RECALL = "10000"
TYPE_RED_PACKET = "8594229559345"
TYPE_QUOTE = "244813135921"
TYPE_LINK_TYPES = frozenset({"21474836529", "154618822705", "141733920817"})
SKIPPED_TEXT_TYPES = frozenset({TYPE_IMAGE, TYPE_EMOJI})

MESSAGE_TYPES = frozenset({TYPE_TEXT, TYPE_VOICE, TYPE_LOCATION, TYPE_RECALL,
                           TYPE_RED_PACKET, TYPE_QUOTE, *TYPE_LINK_TYPES})
"""Types this parser knows how to turn into a text record."""

QUOTE_PREFIX = "〔引用〕"
SENDER_SELF = "我"
MAX_SENT_AT_CHARS = 80
COMMIT_EVERY_RECORDS = 200
COMMIT_EVERY_SECONDS = 0.5

_UNKNOWN_KEYS = ("text", "display_content", "desc", "title", "description")
"""Fallback order for an unrecognised type: first non-empty value wins."""


# --------------------------------------------------------------------------- probe
def companion_path(path: Path | str) -> Path:
    """``<dir>/<name>.html`` -> ``<dir>/<name>.html.json`` (a name, never opened here)."""
    return Path(f"{Path(path)}{COMPANION_SUFFIX}")


def probe(path: Path) -> FormatMatch | None:
    """Match only when the companion JSON exists *and* looks like ExportWeChat.

    The signature is checked against the first element of the top-level array:
    a JSON array whose first object carries ``StrTime``/``type``/``is_send``.
    Everything is read as bytes with a hard cap; nothing is executed.
    """
    path = checked_file(path)
    if path.suffix.lower() not in HTTP_SUFFIXES:
        return None
    companion = companion_path(path)
    head = _read_probe_head(companion)
    if head is None:
        return None
    if not _looks_like_exportwechat_array(head):
        return None
    return FormatMatch(
        kind="exportwechat_html", parser_name=PARSER_NAME,
        parser=ExportWeChatJSONParser, support_level=SUPPORT_LEVEL,
        confidence=CONFIDENCE, signature=("html.json",),
        notes=("检测到 ExportWeChat 伴生 JSON（<文件名>.html.json）："
               "HTML 只是渲染壳，完整聊天在伴生 JSON 中，已改用伴生 JSON 精确解析"
               "（仅本地读取，不执行脚本、不联网）。",))


def _read_probe_head(companion: Path) -> bytes | None:
    """Read the start of the companion file; grow the window if the first
    object is longer than the initial chunk (a huge first message)."""
    try:
        if not companion.is_file():
            return None
        with companion.open("rb") as handle:
            head = handle.read(PROBE_HEAD_BYTES)
            if _first_object_slice(_decode_for_sniff(head)) is None:
                head += handle.read(PROBE_MAX_BYTES - len(head))
            return head
    except OSError:
        return None


def _looks_like_exportwechat_array(head: bytes) -> bool:
    """True when any of the first few array elements looks like a message.

    The first element is normally a message; a leading empty/placeholder object
    (some exporter builds emit one) is tolerated by scanning a few elements. A
    ``head`` cut short mid-object still counts when its opening keys already
    carry the identity signature, so a huge first message cannot hide the format.
    """
    text = _decode_for_sniff(head)
    if not text:
        return False
    stripped = text.lstrip("\ufeff \t\r\n")
    if not stripped.startswith("["):
        return False
    for first_object in _iter_object_slices(stripped, limit=PROBE_SCAN_ELEMENTS):
        if all(f'"{key}"' in first_object for key in IDENTITY_KEYS):
            return True
    return _truncated_head_has_signature(stripped)


def _truncated_head_has_signature(stripped: str) -> bool:
    """Accept an unterminated first object when its opening keys prove the format.

    A single message body can easily exceed the probe window (a very long text is
    stored inline), so the tail keys may not be readable. Only used when the
    array literally starts with an unterminated ``[{``: ``StrTime`` plus at least
    one of ``type``/``is_send`` is required, and the parser re-validates the whole
    array afterwards anyway. Never used for a well-formed non-matching document.
    """
    body = stripped[1:].lstrip()
    if not body.startswith("{"):
        return False
    if "}" in body:  # the first object actually ended; nothing was truncated there
        return False
    first_object = _first_object_slice(stripped)
    candidate = first_object if first_object is not None else body
    if f'"{SIGNATURE_KEY}"' not in candidate:
        return False
    return any(f'"{key}"' in candidate for key in IDENTITY_KEYS if key != SIGNATURE_KEY)


def _iter_object_slices(text: str, *, limit: int) -> Iterator[str]:
    """Yield up to ``limit`` top-level array element ``{...}`` slices."""
    index = text.find("[")
    if index < 0:
        return
    search_from = index + 1
    while limit > 0:
        start = text.find("{", search_from)
        if start < 0:
            return
        slice_text = _first_object_slice(text[start:])
        if slice_text is None:
            return
        yield slice_text
        search_from = start + len(slice_text)
        limit -= 1


def _decode_for_sniff(head: bytes) -> str | None:
    """Lenient decode for signature sniffing only (a truncated tail is fine)."""
    if head.startswith(codecs.BOM_UTF16_LE) or head.startswith(codecs.BOM_UTF16_BE):
        return head.decode("utf-16", errors="ignore")
    if head.startswith(codecs.BOM_UTF8):
        return head.decode("utf-8-sig", errors="ignore")
    for encoding in ("utf-8", "gb18030"):
        try:
            return head.decode(encoding)
        except UnicodeError:
            try:
                return head.decode(encoding, errors="ignore")
            except (UnicodeError, LookupError):  # pragma: no cover - defensive
                continue
    return None  # pragma: no cover - gb18030 decodes every byte sequence


def _first_object_slice(text: str) -> str | None:
    """Return the first top-level ``{...}`` slice of a JSON array (string aware)."""
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


# --------------------------------------------------------------------------- helpers
def decode_json_text(raw: bytes) -> str:
    """Decode explicit encodings only; never guesses by executing anything.

    UTF-16 is only attempted when a real BOM is present: decoding arbitrary
    even-length UTF-8 bytes as ``utf-16`` silently "succeeds" with mojibake, which
    would make a valid companion file look corrupt.
    """
    if raw.startswith(codecs.BOM_UTF8):
        raw = raw[len(codecs.BOM_UTF8):]
    elif raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        try:
            return raw.decode("utf-16")
        except UnicodeError:
            raise HistoryImportError("伴生 JSON 编码无法识别（UTF-16 BOM 无效）。") from None
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise HistoryImportError("伴生 JSON 编码无法识别（支持 UTF-8 / UTF-16 / GB18030）。")


def is_exportwechat_array(data: Any) -> bool:
    """True only for a non-empty array of identity-bearing message objects."""
    if not isinstance(data, list) or not data:
        return False
    first = data[0]
    if not isinstance(first, dict):
        return False
    return all(key in first for key in IDENTITY_KEYS)


def clean_text(value: Any) -> str:
    """HTML-unescape then collapse whitespace (ExportWeChat stores ``&#x20;`` etc.)."""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    if not value:
        return ""
    unescaped = html_module.unescape(value)
    if unescaped == value and "&" in value:
        # ``html.unescape`` already handles every HTML5 entity; a second explicit
        # pass keeps legacy double-escaped exports ("&amp;#x20;") readable.
        unescaped = html_module.unescape(unescaped)
    return " ".join(unescaped.split())


def _as_label(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return text[:MAX_LABEL_CHARS]


def _as_timestamp(value: Any) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return text[:MAX_SENT_AT_CHARS]


class ExportWeChatJSONParser(Parser):
    """Reads ``<name>.html.json`` and emits one record per meaningful message."""

    kind = "exportwechat_html"
    parser_name = PARSER_NAME
    support_level = SUPPORT_LEVEL

    def __init__(self, *, clock: Callable[[], float] | None = None,
                 parse_timeout: float | None = None) -> None:
        super().__init__(clock=clock)
        self.parse_timeout = (HTML_PARSE_TIMEOUT_SECONDS if parse_timeout is None
                              else float(parse_timeout))
        self.kept = 0
        self.skipped_non_text = 0
        self.skipped_empty = 0
        self.skipped_types: dict[str, int] = {}
        self.truncated = 0
        self.total_entries = 0

    @classmethod
    def probe(cls, path: Path) -> FormatMatch | None:
        return probe(path)

    # ----------------------------------------------------------------- limits
    def _check_limits(self, cancel: CancellationToken, started: float) -> None:
        if cancel.is_cancelled():
            raise CancelledError("导入已取消")
        if self.clock() - started > self.parse_timeout:
            raise HistoryImportError(
                f"伴生 JSON 解析超时：超过 {self.parse_timeout:g} 秒上限，已停止；"
                "请拆分文件后重试。")

    def _skip(self, type_name: str) -> None:
        if type_name in SKIPPED_TEXT_TYPES:
            self.skipped_non_text += 1
        else:
            self.skipped_empty += 1
        self.skipped_types[type_name] = self.skipped_types.get(type_name, 0) + 1

    # ------------------------------------------------------------------- text
    @staticmethod
    def _compose_text(item: dict) -> str:
        """Extract the meaningful body for one entry (empty string when none)."""
        type_name = _type_name(item.get("type"))
        if type_name == TYPE_QUOTE:
            main = clean_text(item.get("text"))
            quoted = clean_text(item.get("quote_text"))
            if quoted and main:
                return f"{QUOTE_PREFIX}{quoted}｜{main}"
            return main or (f"{QUOTE_PREFIX}{quoted}" if quoted else "")
        if type_name == TYPE_LOCATION:
            place = clean_text(item.get("poiname"))
            label = clean_text(item.get("label"))
            if place and label and label != place:
                return f"{place} {label}"
            return place or label
        if type_name in TYPE_LINK_TYPES:
            title = clean_text(item.get("title"))
            description = clean_text(item.get("description"))
            if title and description:
                return f"{title} {description}"
            return title or description
        if type_name in (TYPE_TEXT, TYPE_RECALL, TYPE_RED_PACKET):
            return clean_text(item.get("text"))
        if type_name == TYPE_VOICE:
            return clean_text(item.get("display_content"))
        if type_name in SKIPPED_TEXT_TYPES:
            return ""
        for key in _UNKNOWN_KEYS:
            value = clean_text(item.get(key))
            if value:
                return value
        return ""

    # ------------------------------------------------------------------ parse
    def parse(self, path: Path, cancel: CancellationToken) -> Iterator[ImportedRecord]:
        cancel = cancel if isinstance(cancel, CancellationToken) else CancellationToken()
        path = checked_file(path)
        companion = companion_path(path)
        try:
            companion = checked_file(companion)
        except HistoryImportError:
            raise HistoryImportError(
                "找不到伴生 JSON 文件（<文件名>.html.json），无法精确解析该导出。") from None
        if cancel.is_cancelled():
            raise CancelledError("导入已取消")

        self._reset_counters()
        started = self.clock()
        data = self._load(companion, cancel, started)
        self._check_limits(cancel, started)
        if len(data) > HTML_MAX_RECORDS:
            raise HistoryImportError(
                f"伴生 JSON 消息超过 {HTML_MAX_RECORDS} 条，请拆分文件后导入。")

        self.total_entries = len(data)
        self.page_count = 1
        self.pages_done = 0
        last_commit = started
        fallback_sender = self._fallback_sender(data)

        for index, item in enumerate(data):
            if index % COMMIT_EVERY_RECORDS == 0:
                self._check_limits(cancel, started)
                now = self.clock()
                if now - last_commit > COMMIT_EVERY_SECONDS:
                    self.pages_done = min(self.page_count, self.pages_done + 1)
                    last_commit = now
            if not isinstance(item, dict):
                self._skip("invalid")
                continue
            type_name = _type_name(item.get("type"))
            text = self._compose_text(item)
            if not text:
                self._skip(type_name)
                continue
            if len(text) > MAX_TEXT_CHARS:
                text = text[:MAX_TEXT_CHARS]
                self.truncated += 1
            self.kept += 1
            yield ImportedRecord(
                text=text,
                ordinal=index,
                record_type="message",
                sender=self._sender(item, fallback_sender),
                sent_at=_as_timestamp(item.get("StrTime")),
                locator={"kind": "exportwechat_json", "ordinal": index,
                         "type": type_name},
                quality=self._quality(item, type_name))

        self._check_limits(cancel, started)
        self.pages_done = self.page_count
        self._record_notes()

    def _reset_counters(self) -> None:
        self.kept = 0
        self.skipped_non_text = 0
        self.skipped_empty = 0
        self.skipped_types = {}
        self.truncated = 0
        self.total_entries = 0
        self.page_count = 1
        self.pages_done = 0

    # ------------------------------------------------------------------- load
    def _load(self, companion: Path, cancel: CancellationToken,
              started: float) -> list:
        self._check_limits(cancel, started)
        raw = read_limited_bytes(companion)
        self._check_limits(cancel, started)
        if len(raw) > MAX_IMPORT_FILE_BYTES:  # pragma: no cover - checked earlier
            raise HistoryImportError("伴生 JSON 超过 100 MB，请分批导入。")
        try:
            data = json.loads(decode_json_text(raw))
        except HistoryImportError:
            raise
        except (json.JSONDecodeError, UnicodeError, ValueError) as error:
            raise HistoryImportError(
                f"伴生 JSON 无法解析（{type(error).__name__}），文件可能已损坏。") from None
        if not isinstance(data, list):
            raise HistoryImportError("伴生 JSON 顶层不是数组，无法按 ExportWeChat 导出解析。")
        if not data:
            raise HistoryImportError("伴生 JSON 是空数组，没有可导入的聊天记录。")
        if not is_exportwechat_array(data):
            raise HistoryImportError(
                "伴生 JSON 不是可识别的 ExportWeChat 消息数组（缺少 StrTime/type/is_send）。")
        return data

    # ----------------------------------------------------------------- fields
    @staticmethod
    def _fallback_sender(data: list) -> str | None:
        """Most common ``display_name``: the peer name shown for incoming rows."""
        counts: dict[str, int] = {}
        for item in data:
            if not isinstance(item, dict) or item.get("is_send"):
                continue
            name = _as_label(item.get("display_name"))
            if name:
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            return None
        return max(counts.items(), key=lambda pair: (pair[1], pair[0]))[0]

    @staticmethod
    def _sender(item: dict, fallback_sender: str | None) -> str | None:
        if item.get("is_send"):
            return SENDER_SELF
        name = _as_label(item.get("display_name"))
        if name:
            return name
        wxid = _as_label(item.get("sender"))
        if wxid:
            return wxid
        return fallback_sender

    @staticmethod
    def _quality(item: dict, type_name: str) -> dict:
        quality: dict = {"source": PARSER_NAME, "original_type": type_name}
        if type_name == TYPE_QUOTE:
            quote_type = _type_name(item.get("quote_type"))
            if quote_type:
                quality["quote_type"] = quote_type
        elif type_name in TYPE_LINK_TYPES:
            app_name = _as_label(item.get("app_name"))
            if app_name:
                quality["app_name"] = app_name
        elif type_name == TYPE_VOICE:
            duration = clean_text(item.get("duration"))
            if duration:
                quality["duration"] = duration
        return quality

    # ------------------------------------------------------------------ notes
    def _record_notes(self) -> None:
        if self.skipped_non_text:
            self.notes.append(
                f"跳过 {self.skipped_non_text} 条非文本消息（图片/表情，不含正文）。")
        if self.skipped_empty:
            detail = self._type_breakdown()
            self.notes.append(
                f"跳过 {self.skipped_empty} 条无可用文本的消息"
                + (f"（类型：{detail}）" if detail else "") + "。")
        if self.truncated:
            self.notes.append(
                f"{self.truncated} 条消息超过 {MAX_TEXT_CHARS} 个字符，已截断保存。")
        self.notes.append(
            f"伴生 JSON 共 {self.total_entries} 条，导入 {self.kept} 条文本消息。")

    def _type_breakdown(self, limit: int = 6) -> str:
        ordered = sorted(self.skipped_types.items(), key=lambda pair: (-pair[1], pair[0]))
        return "、".join(f"{name}×{count}" for name, count in ordered[:limit])


def _type_name(value: Any) -> str:
    """Normalise the observed ``type`` value to its stable string form."""
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, (int, str)):
        return str(value).strip() or "unknown"
    return f"<{type(value).__name__}>"
