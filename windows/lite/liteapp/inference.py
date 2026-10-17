"""Prompt-independent model transport, protocol adapters and the inference gateway.

T3 of ``docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md``. This module owns
the wire details that used to be duplicated inside ``subscription.analyze`` and
``analysis.analyze``:

* a cancellable SSE + JSON HTTP transport with connect timeout 10 s, no-data/idle
  timeout 30 s and a per-kind total timeout (realtime 120 s, history batch 180 s);
* redirect refusal so a bearer token or chat body can never be forwarded elsewhere;
* two protocol adapters: the official ChatGPT Responses stream (only allowed
  fields ``model``/``instructions``/``input``/``store``/``stream``) and the
  compatible Chat Completions JSON route;
* classification of HTTP/stream failures into quota (pause the queue),
  authorization (stop that registration's tasks), transient (retry later) and
  protocol errors;
* :class:`InferenceGateway.run`, which validates model output with the task schema
  from :mod:`liteapp.inference_schemas` and returns an :class:`InferenceResult`
  containing no raw HTTP diagnostics.

Prompt construction stays in the callers (``analysis.build_payload`` /
``subscription``); this module only carries the protocol-neutral inputs from
:class:`~liteapp.inference_schemas.InferenceRequest.input_data`.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from .contracts import CancellationToken, CancelledError
from .inference_schemas import (KIND_HISTORY_EXTRACT, KIND_HISTORY_REDUCE,
                                KIND_REALTIME_REPLY, InferenceAuthError,
                                InferenceError, InferenceProtocolError,
                                InferenceQuotaError, InferenceRequest,
                                InferenceResult, InferenceTimeoutError,
                                InferenceTransientError, SchemaError,
                                validate_content, validate_request)

__all__ = [
    "DEFAULT_CONNECT_TIMEOUT", "DEFAULT_IDLE_TIMEOUT", "DEFAULT_KIND_TIMEOUTS",
    "GatewayProfile", "InferenceAuthError", "InferenceError", "InferenceGateway",
    "InferenceProtocolError", "InferenceQuotaError", "InferenceRequest",
    "InferenceResult", "InferenceTimeoutError", "InferenceTransientError",
    "NoRedirectHandler",
]

DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_IDLE_TIMEOUT = 30.0
DEFAULT_KIND_TIMEOUTS = {
    KIND_REALTIME_REPLY: 120.0,
    KIND_HISTORY_EXTRACT: 180.0,
    KIND_HISTORY_REDUCE: 180.0,
}
DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_RESPONSES_ALLOWED_INPUT_KEYS = ("instructions", "input")
_COMPATIBLE_ALLOWED_INPUT_KEYS = ("messages", "temperature", "max_tokens", "top_p")
_USAGE_KEYS = ("input_tokens", "output_tokens", "total_tokens",
               "prompt_tokens", "completion_tokens")
_QUOTA_CODES = frozenset({
    "subscription_sharing_usage_limit_exceeded", "usage_unavailable",
    "insufficient_quota", "quota_exceeded", "billing_hard_limit_reached",
    "rate_limit_exceeded",
})


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward the bearer token or chat body to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


@dataclass(frozen=True)
class GatewayProfile:
    """One configured model endpoint selected by ``InferenceRequest.profile_id``."""

    backend: str
    url: str
    model: str
    api_key: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.backend, str) or not self.backend:
            raise ValueError("backend 不能为空。")
        if not isinstance(self.url, str) or not self.url.strip():
            raise ValueError("url 不能为空。")
        if not self.url.split(":", 1)[0] in ("http", "https"):
            raise ValueError("url 必须以 http:// 或 https:// 开头。")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model 不能为空。")
        if not isinstance(self.api_key, str):
            raise TypeError("api_key 必须是字符串。")


def _positive_seconds(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{name} 必须为正数。")
    return float(value)


def _apply_read_timeout(response: Any, seconds: float) -> None:
    """Best-effort switch from the connect timeout to the no-data read timeout."""
    try:
        response.fp.raw._sock.settimeout(seconds)
        return
    except (AttributeError, OSError):
        pass
    try:
        response.fp.raw.settimeout(seconds)
    except (AttributeError, OSError):
        pass


def _start_cancel_watcher(cancel: CancellationToken, response: Any,
                          done: threading.Event) -> threading.Thread:
    """Close the socket promptly when the caller cancels a blocked stream read."""

    def watch() -> None:
        while not done.is_set():
            if cancel.wait(0.05):
                try:
                    response.close()
                except OSError:
                    pass
                return

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def _classify_http_error(exc: urllib.error.HTTPError) -> InferenceError:
    status = getattr(exc, "code", 0)
    if status in (301, 302, 303, 307, 308):
        return InferenceProtocolError("模型服务返回了不支持的重定向。")
    if status in (401, 403):
        return InferenceAuthError("模型授权已失效或未获准使用，请重新连接账号。")
    if status == 429:
        return InferenceTransientError("模型服务当前限流，请稍后重试。")
    if 400 <= status < 500:
        return InferenceProtocolError(f"模型请求被拒绝（HTTP {status}）。")
    return InferenceTransientError(f"模型服务暂时不可用（HTTP {status}）。")


def _open_stream(url: str, payload: dict, headers: dict, *,
                 connect_timeout: float) -> Any:
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    try:
        return urllib.request.build_opener(NoRedirectHandler()).open(
            request, timeout=connect_timeout)
    except urllib.error.HTTPError as exc:
        raise _classify_http_error(exc) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise InferenceTransientError("模型服务连接失败或超时。") from None


def _iter_sse(response: Any, *, cancel: CancellationToken, deadline: float,
              clock: Callable[[], float], max_bytes: int, meta: dict) -> Iterator[dict]:
    """Yield parsed SSE events; enforce size, deadline, idle timeout and cancel."""
    idle_timeout = meta.get("idle_timeout")
    if idle_timeout:
        _apply_read_timeout(response, float(idle_timeout))
    headers = getattr(response, "headers", None)
    if headers is not None:
        meta["request_id"] = str(headers.get("x-request-id") or "")
    done = threading.Event()
    _start_cancel_watcher(cancel, response, done)
    seen = 0
    event_type = ""
    data_lines: list[str] = []
    try:
        while True:
            if cancel.is_cancelled():
                raise CancelledError("inference cancelled")
            if clock() >= deadline:
                raise InferenceTimeoutError("模型请求超时，请稍后重试。")
            try:
                raw_line = response.readline()
            except TimeoutError:
                if cancel.is_cancelled():
                    raise CancelledError("inference cancelled") from None
                raise InferenceTimeoutError("模型流长时间没有数据，请稍后重试。") from None
            except OSError:
                if cancel.is_cancelled():
                    raise CancelledError("inference cancelled") from None
                raise InferenceTransientError("模型流连接中断，请稍后重试。") from None
            if not raw_line:
                break
            seen += len(raw_line)
            if seen > max_bytes:
                raise InferenceProtocolError("模型响应过大。")
            line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
            if not line:
                if not data_lines:
                    event_type = ""
                    continue
                raw = "\n".join(data_lines)
                data_lines = []
                if raw.strip() == "[DONE]":
                    return
                try:
                    event = json.loads(raw)
                except ValueError:
                    raise InferenceProtocolError("模型流返回了无效事件。") from None
                if not isinstance(event, dict):
                    raise InferenceProtocolError("模型流返回了无效事件。")
                yield event
                event_type = ""
                continue
            if line.startswith("event:"):
                event_type = line[6:].strip()
            elif line.startswith("data:"):
                data = line[5:].lstrip()
                if data:
                    data_lines.append(data)
    finally:
        done.set()
        try:
            response.close()
        except OSError:
            pass


def _post_json(url: str, payload: dict, headers: dict, *, cancel: CancellationToken,
               connect_timeout: float, idle_timeout: float, deadline: float,
               clock: Callable[[], float], max_bytes: int) -> tuple[dict, dict]:
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    cancel.raise_if_cancelled()
    try:
        response = urllib.request.build_opener(NoRedirectHandler()).open(
            request, timeout=connect_timeout)
    except urllib.error.HTTPError as exc:
        raise _classify_http_error(exc) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        if cancel.is_cancelled():
            raise CancelledError("inference cancelled") from None
        raise InferenceTransientError("模型服务连接失败或超时。") from None
    _apply_read_timeout(response, idle_timeout)
    done = threading.Event()
    _start_cancel_watcher(cancel, response, done)
    try:
        try:
            raw = response.read(max_bytes + 1)
        except TimeoutError:
            if cancel.is_cancelled():
                raise CancelledError("inference cancelled") from None
            raise InferenceTimeoutError("模型请求超时，请稍后重试。") from None
        except OSError:
            if cancel.is_cancelled():
                raise CancelledError("inference cancelled") from None
            raise InferenceTransientError("模型连接中断，请稍后重试。") from None
        response_headers = dict(getattr(response, "headers", {}) or {})
    finally:
        done.set()
        try:
            response.close()
        except OSError:
            pass
    if clock() >= deadline:
        raise InferenceTimeoutError("模型请求超时，请稍后重试。")
    if len(raw) > max_bytes:
        raise InferenceProtocolError("模型响应过大。")
    try:
        value = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        raise InferenceProtocolError("模型服务返回了无效数据。") from None
    if not isinstance(value, dict):
        raise InferenceProtocolError("模型服务返回了无效数据。")
    return value, response_headers


def _filter_usage(usage: Any) -> dict | None:
    if not isinstance(usage, dict):
        return None
    filtered = {key: usage[key] for key in _USAGE_KEYS
                if isinstance(usage.get(key), (int, float))
                and not isinstance(usage.get(key), bool)}
    return filtered or None


def _output_text(response: dict | None) -> str:
    if not isinstance(response, dict):
        return ""
    output = response.get("output")
    if not isinstance(output, list):
        return ""
    parts = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _raise_stream_error(event: dict, kind: str) -> None:
    """Classify a failed/incomplete/error stream event; never echo its raw body."""
    response = event.get("response") if isinstance(event.get("response"), dict) else {}
    error = response.get("error") if isinstance(response.get("error"), dict) else {}
    if not error and isinstance(event.get("error"), dict):
        error = event["error"]
    code = str(error.get("code") or error.get("type") or event.get("code") or "")
    if code in _QUOTA_CODES:
        raise InferenceQuotaError(
            "ChatGPT 套餐当前无法继续处理，请在 ChatGPT Usage 页面查看。", code=code)
    if kind == "response.incomplete":
        raise InferenceProtocolError("模型流未正常完成，请重试。")
    raise InferenceProtocolError("模型流返回失败事件，请稍后重试。")


class InferenceGateway:
    """Run one :class:`InferenceRequest` against a registered model profile."""

    def __init__(self, profiles: dict[str, GatewayProfile] | None = None, *,
                 connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
                 idle_timeout: float = DEFAULT_IDLE_TIMEOUT,
                 kind_timeouts: dict[str, float] | None = None,
                 clock: Callable[[], float] | None = None,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES) -> None:
        self.connect_timeout = _positive_seconds(connect_timeout, "connect_timeout")
        self.idle_timeout = _positive_seconds(idle_timeout, "idle_timeout")
        self.kind_timeouts = dict(DEFAULT_KIND_TIMEOUTS)
        for kind, value in (kind_timeouts or {}).items():
            if kind not in DEFAULT_KIND_TIMEOUTS:
                raise ValueError(f"未知的模型任务类型：{kind!r}")
            self.kind_timeouts[kind] = _positive_seconds(value, "kind_timeout")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int):
            raise TypeError("max_response_bytes 必须是整数。")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes 必须为正数。")
        self.max_response_bytes = max_response_bytes
        self.clock = clock or time.monotonic
        self._profiles: dict[str, GatewayProfile] = {}
        self._lock = threading.RLock()
        for profile_id, profile in (profiles or {}).items():
            self.register_profile(profile_id, profile)

    # -- profile registry ----------------------------------------------------
    def register_profile(self, profile_id: str, profile: GatewayProfile) -> None:
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError("profile_id 不能为空。")
        if not isinstance(profile, GatewayProfile):
            raise TypeError("profile 必须是 GatewayProfile")
        with self._lock:
            self._profiles[profile_id] = profile

    def remove_profile(self, profile_id: str) -> None:
        with self._lock:
            self._profiles.pop(profile_id, None)

    def profile_ids(self) -> list[str]:
        with self._lock:
            return list(self._profiles)

    def _profile(self, profile_id: str) -> GatewayProfile:
        with self._lock:
            profile = self._profiles.get(profile_id)
        if profile is None:
            raise ValueError("模型配置不存在，请重新选择分析后端并保存。")
        return profile

    # -- public API ----------------------------------------------------------
    def run(self, request: InferenceRequest,
            cancel: CancellationToken | None = None) -> InferenceResult:
        """Execute one model task; returns validated data without raw diagnostics."""
        validate_request(request)
        profile = self._profile(request.profile_id)
        cancel = cancel or CancellationToken()
        if not isinstance(cancel, CancellationToken):
            raise TypeError("cancel 必须是 CancellationToken")
        cancel.raise_if_cancelled()
        started = self.clock()
        deadline = started + self.kind_timeouts[request.kind]
        if profile.backend == "chatgpt":
            content, request_id, usage = self._run_responses(
                profile, request, cancel, deadline)
        elif profile.backend == "compatible":
            content, request_id, usage = self._run_compatible(
                profile, request, cancel, deadline)
        else:
            raise ValueError(f"未知的模型后端：{profile.backend!r}")
        cancel.raise_if_cancelled()
        try:
            validated = validate_content(request.kind, content, request)
        except SchemaError as exc:
            raise InferenceProtocolError(str(exc)) from None
        return InferenceResult(
            validated_data=validated, request_id=request_id,
            elapsed=max(0.0, self.clock() - started), usage=usage)

    # -- protocol adapters ---------------------------------------------------
    def _run_responses(self, profile: GatewayProfile, request: InferenceRequest,
                       cancel: CancellationToken, deadline: float):
        input_data = request.input_data
        instructions = input_data.get("instructions")
        text = input_data.get("input")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("模型请求缺少系统指令。")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("模型请求缺少用户输入。")
        # Only the allow-listed fields of the official Responses route are sent.
        payload = {
            "model": profile.model,
            "instructions": instructions,
            "input": [{"role": "user",
                       "content": [{"type": "input_text", "text": text}]}],
            "store": False,
            "stream": True,
        }
        headers = {"Content-Type": "application/json", "Accept": "text/event-stream"}
        if profile.api_key:
            headers["Authorization"] = "Bearer " + profile.api_key
        meta: dict[str, Any] = {"idle_timeout": self.idle_timeout}
        deltas: list[str] = []
        completed: dict | None = None
        response = _open_stream(profile.url, payload, headers,
                                connect_timeout=self.connect_timeout)
        for event in _iter_sse(response, cancel=cancel, deadline=deadline,
                               clock=self.clock, max_bytes=self.max_response_bytes,
                               meta=meta):
            cancel.raise_if_cancelled()
            kind = str(event.get("type") or "")
            if kind == "response.output_text.delta":
                delta = event.get("delta")
                if isinstance(delta, str):
                    deltas.append(delta)
            elif kind == "response.completed":
                completed = event
                break
            elif kind in ("response.failed", "response.incomplete", "error"):
                _raise_stream_error(event, kind)
        if completed is None:
            raise InferenceProtocolError("模型流未正常完成，请重试。")
        completed_response = (completed.get("response")
                              if isinstance(completed.get("response"), dict) else {})
        if completed_response.get("error"):
            _raise_stream_error({"response": completed_response}, "response.failed")
        content = "".join(deltas) if deltas else _output_text(completed_response)
        if not content.strip():
            raise InferenceProtocolError("模型没有返回有效内容。")
        usage = _filter_usage(completed_response.get("usage"))
        request_id = str(meta.get("request_id")
                         or completed_response.get("id") or "")
        return content, request_id, usage

    def _run_compatible(self, profile: GatewayProfile, request: InferenceRequest,
                        cancel: CancellationToken, deadline: float):
        input_data = request.input_data
        messages = input_data.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ValueError("模型请求缺少 messages。")
        # The compatible route keeps its own Chat Completions protocol.
        payload: dict[str, Any] = {"model": profile.model, "messages": messages}
        for key in _COMPATIBLE_ALLOWED_INPUT_KEYS:
            if key in input_data and key not in ("model", "messages"):
                payload[key] = input_data[key]
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if profile.api_key:
            headers["Authorization"] = "Bearer " + profile.api_key
        data, response_headers = _post_json(
            profile.url, payload, headers, cancel=cancel,
            connect_timeout=self.connect_timeout, idle_timeout=self.idle_timeout,
            deadline=deadline, clock=self.clock,
            max_bytes=self.max_response_bytes)
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise InferenceProtocolError("模型响应不符合 Chat Completions 格式。") from None
        if not isinstance(content, str) or not content.strip():
            raise InferenceProtocolError("模型没有返回有效内容。")
        usage = _filter_usage(data.get("usage"))
        request_id = str(data.get("id")
                         or response_headers.get("x-request-id") or "")
        return content, request_id, usage
