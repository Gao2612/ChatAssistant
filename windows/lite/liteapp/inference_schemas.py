"""Prompt-independent request/result contracts and the three T3 output schemas.

T3 of ``docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md`` requires a generic
model layer with three fixed task kinds instead of the old "three chat replies"
validator reused everywhere:

* ``realtime_reply``  - the right-hand reply suggestions (intent/need/action/...)
* ``history_extract`` - facts/inferences/open questions extracted from one batch,
  every item carrying evidence ranges inside that batch's records;
* ``history_reduce``  - merged profile summary of several batch results.

Everything here is pure: no network, no Qt, no prompts. The SSE/HTTP protocol lives
in :mod:`liteapp.inference`; prompt construction stays in the callers.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

KIND_REALTIME_REPLY = "realtime_reply"
KIND_HISTORY_EXTRACT = "history_extract"
KIND_HISTORY_REDUCE = "history_reduce"
KINDS = (KIND_REALTIME_REPLY, KIND_HISTORY_EXTRACT, KIND_HISTORY_REDUCE)
HISTORY_KINDS = (KIND_HISTORY_EXTRACT, KIND_HISTORY_REDUCE)

SCHEMA_VERSION = "v1"
SUPPORTED_SCHEMA_VERSIONS = {kind: frozenset({"v1"}) for kind in KINDS}
ALLOWED_CATEGORIES = ("fact", "inference", "open_question")
MAX_ITEM_TEXT = 2000
MAX_SUMMARY_TEXT = 4000
MAX_EVIDENCE_ITEMS = 32


class SchemaError(ValueError):
    """Model output does not satisfy the requested task schema."""


class InferenceError(ValueError):
    """Base class for gateway/queue failures; UI may show ``str(exc)`` directly."""


class InferenceAuthError(InferenceError):
    """Authorization is gone: stop every task that uses this registration."""


class InferenceQuotaError(InferenceError):
    """Usage/quota limit: pause the queue instead of retrying immediately."""

    def __init__(self, message: str, *, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class InferenceTransientError(InferenceError):
    """Temporary network/service failure; the task can be retried later."""


class InferenceTimeoutError(InferenceTransientError):
    """No data / no completion within the configured deadline."""


class InferenceProtocolError(InferenceError):
    """Broken stream, refused redirect, oversized response or invalid schema."""


def _require_text(value: Any, name: str, *, limit: int = 2000) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"{name} 必须是字符串。")
    text = value.strip()
    if not text:
        raise SchemaError(f"{name} 不能为空。")
    if len(text) > limit:
        raise SchemaError(f"{name} 过长。")
    return text


def _require_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{name} 必须是整数。")
    if value < 0:
        raise SchemaError(f"{name} 不能为负数。")
    return value


def _allowed_records(request: "InferenceRequest | None") -> dict[str, int] | None:
    """Map of allowed record IDs to their text length, when the caller provided it."""
    if request is None:
        return None
    rows = request.input_data.get("records")
    if not isinstance(rows, (list, tuple)):
        return None
    allowed: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        record_id = row.get("record_id")
        text = row.get("text")
        if isinstance(record_id, str) and record_id and isinstance(text, str):
            allowed[record_id] = len(text)
    return allowed or None


def _normalize_evidence(value: Any, allowed: dict[str, int] | None) -> list[dict]:
    if not isinstance(value, (list, tuple)) or not value:
        raise SchemaError("模型结论缺少证据引用。")
    if len(value) > MAX_EVIDENCE_ITEMS:
        raise SchemaError("证据引用过多。")
    normalized = []
    for entry in value:
        if not isinstance(entry, dict):
            raise SchemaError("证据引用格式不正确。")
        record_id = _require_text(entry.get("record_id"), "证据记录 ID", limit=200)
        start = _require_int(entry.get("start"), "证据起点")
        end = _require_int(entry.get("end"), "证据终点")
        if end < start:
            raise SchemaError("证据范围倒置。")
        if allowed is not None:
            length = allowed.get(record_id)
            if length is None:
                raise SchemaError("证据记录不在本批范围内。")
            if end > length:
                raise SchemaError("证据范围超出记录正文。")
        normalized.append({"record_id": record_id, "start": start, "end": end})
    return normalized


def _normalize_item(entry: Any, allowed: dict[str, int] | None,
                    *, require_evidence: bool) -> dict:
    if not isinstance(entry, dict):
        raise SchemaError("模型条目格式不正确。")
    category = str(entry.get("category") or "").strip()
    if category not in ALLOWED_CATEGORIES:
        raise SchemaError("模型条目类别不在允许范围。")
    text = _require_text(entry.get("text"), "模型条目正文", limit=MAX_ITEM_TEXT)
    if require_evidence or "evidence" in entry:
        evidence = _normalize_evidence(entry.get("evidence"), allowed)
    else:
        evidence = []
    return {"category": category, "text": text, "evidence": evidence}


def _parse_json_object(content: Any) -> dict:
    if not isinstance(content, str):
        raise SchemaError("模型未返回文本结果。")
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    try:
        value = json.loads(text)
    except (ValueError, TypeError):
        raise SchemaError("模型未返回有效 JSON，请重试或更换兼容模型。") from None
    if not isinstance(value, dict):
        raise SchemaError("模型未返回 JSON 对象。")
    return value


def parse_realtime_reply(content: Any) -> dict:
    """Validate the realtime reply suggestions (three distinct short replies)."""
    obj = _parse_json_object(content)
    replies = obj.get("replies")
    if not isinstance(replies, list):
        raise SchemaError("模型回复缺少候选列表。")
    if len(replies) != 3:
        raise SchemaError("模型需要返回三条有效回复，请重试。")
    normalized_replies = []
    for reply in replies:
        if not isinstance(reply, str) or not reply.strip() or len(reply) > 2000:
            raise SchemaError("模型需要返回三条有效回复，请重试。")
        normalized_replies.append(reply.strip())
    result = {}
    for key in ("intent", "need", "action", "caution"):
        value = obj.get(key, "未提供")
        if not isinstance(value, str):
            raise SchemaError("模型分析字段格式不正确。")
        result[key] = value[:2000]
    result["replies"] = normalized_replies
    return result


def parse_history_extract(content: Any, request: "InferenceRequest | None" = None) -> dict:
    """Validate one history batch extraction with evidence inside this batch."""
    obj = _parse_json_object(content)
    items = obj.get("items")
    if not isinstance(items, list):
        raise SchemaError("历史提取结果缺少 items 列表。")
    allowed = _allowed_records(request)
    normalized = [_normalize_item(item, allowed, require_evidence=True) for item in items]
    return {"items": normalized}


def parse_history_reduce(content: Any, request: "InferenceRequest | None" = None) -> dict:
    """Validate a merged history summary with evidence-backed findings."""
    obj = _parse_json_object(content)
    summary = _require_text(obj.get("summary"), "历史摘要", limit=MAX_SUMMARY_TEXT)
    items = obj.get("items", [])
    if not isinstance(items, list):
        raise SchemaError("历史摘要缺少 items 列表。")
    allowed = _allowed_records(request)
    normalized = [_normalize_item(item, allowed, require_evidence=True) for item in items]
    return {"summary": summary, "items": normalized}


_PARSERS = {
    KIND_REALTIME_REPLY: parse_realtime_reply,
    KIND_HISTORY_EXTRACT: parse_history_extract,
    KIND_HISTORY_REDUCE: parse_history_reduce,
}


def validate_content(kind: str, content: Any, request: "InferenceRequest | None" = None) -> dict:
    """Parse and validate model output for one task kind; raises :class:`SchemaError`."""
    parser = _PARSERS.get(kind)
    if parser is None:
        raise SchemaError("未知的模型任务类型。")
    if kind == KIND_REALTIME_REPLY:
        return parser(content)
    return parser(content, request)


@dataclass(frozen=True)
class InferenceRequest:
    """One prompt-independent model task submitted to the gateway/queue.

    ``input_data`` carries only protocol-neutral inputs for the task kind:
    realtime reply needs ``instructions`` + ``input`` (ChatGPT route) or
    ``messages`` (compatible route); history tasks also pass ``records`` so evidence
    can be scope-checked. ``version_key`` freezes the caller's result-acceptance
    version; a stale result must never be written back by the caller.
    """

    task_id: str
    kind: str
    profile_id: str
    input_data: dict
    schema_version: str
    version_key: str

    def __post_init__(self) -> None:
        for value, name in ((self.task_id, "task_id"), (self.profile_id, "profile_id"),
                            (self.version_key, "version_key"),
                            (self.schema_version, "schema_version")):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 不能为空。")
        if self.kind not in KINDS:
            raise ValueError(f"未知的模型任务类型：{self.kind!r}")
        if not isinstance(self.input_data, dict):
            raise ValueError("input_data 必须是字典。")


def validate_request(request: Any) -> None:
    """Extra checks that need the registry: schema version must be supported."""
    if not isinstance(request, InferenceRequest):
        raise TypeError("request 必须是 InferenceRequest")
    supported = SUPPORTED_SCHEMA_VERSIONS.get(request.kind, frozenset())
    if request.schema_version not in supported:
        raise ValueError("模型任务 schema 版本不受支持，请更新应用。")


@dataclass(frozen=True)
class InferenceResult:
    """Validated model output without any raw HTTP diagnostics or stream transcript."""

    validated_data: dict
    request_id: str
    elapsed: float
    usage: dict | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.validated_data, dict):
            raise TypeError("validated_data 必须是字典。")
        if not isinstance(self.request_id, str):
            raise TypeError("request_id 必须是字符串。")
        if isinstance(self.elapsed, bool) or not isinstance(self.elapsed, (int, float)):
            raise TypeError("elapsed 必须是数字。")
        if self.elapsed < 0:
            raise ValueError("elapsed 必须 >= 0。")
        if self.usage is not None and not isinstance(self.usage, dict):
            raise TypeError("usage 必须是字典或 None。")
