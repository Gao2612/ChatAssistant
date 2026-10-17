"""T3 acceptance tests: generic inference gateway, SSE transport and schemas.

All services are loopback fakes. Tests cover the protocol contract (allowed fields
for the ChatGPT Responses route vs. the compatible Chat Completions route),
connection/idle/total timeouts, cancellation, redirect safety, quota/auth/transient
error classification, stream breakage after deltas, oversized responses and the
three output schemas. No real model, token or chat content is used.
"""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from liteapp import inference, inference_schemas
from liteapp.contracts import CancellationToken, CancelledError

REALTIME_JSON = json.dumps({
    "intent": "询问价格", "need": "报价", "action": "先确认规格",
    "caution": "信息有限", "replies": ["请问需要什么规格？", "可以先说说用途吗？", "稍后给您报价。"],
}, ensure_ascii=False)
HISTORY_EXTRACT_JSON = json.dumps({
    "items": [
        {"category": "fact", "text": "客户要求周五前报价",
         "evidence": [{"record_id": "r1", "start": 0, "end": 4}]},
        {"category": "inference", "text": "可能更关注价格", "evidence": [{"record_id": "r2", "start": 1, "end": 3}]},
        {"category": "open_question", "text": "预算未知", "evidence": [{"record_id": "r1", "start": 5, "end": 8}]},
    ]
}, ensure_ascii=False)
HISTORY_REDUCE_JSON = json.dumps({
    "summary": "客户关注报价与交期。",
    "items": [
        {"category": "fact", "text": "客户询问报价", "evidence": [{"record_id": "r1", "start": 0, "end": 4}]},
    ],
}, ensure_ascii=False)


def sse(*events: dict) -> bytes:
    return b"".join(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n"
                    for event in events)


def delta_event(text: str) -> dict:
    return {"type": "response.output_text.delta", "delta": text}


def completed_event(*, usage=None) -> dict:
    response = {"output": []}
    if usage is not None:
        response["usage"] = usage
    return {"type": "response.completed", "response": response}


class FakeClock:
    """Deterministic monotonic clock that advances on every reading."""

    def __init__(self, step=0.0):
        self.value = 0.0
        self.step = step
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            value = self.value
            self.value += self.step
            return value


class FakeHttpServer:
    """Loopback HTTP server with a scripted responder.

    The responder receives the parsed request record and returns a plan:
    ``{"status", "headers", "content_type", "body"}`` for a normal response or
    ``{"stream": [{"data": bytes, "delay": float, "stall": float}, ...]}`` for a
    chunked response (Content-Length is the sum of all chunks).
    """

    def __init__(self, responder):
        self.requests = []
        self.responder = responder
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                record = {"path": self.path, "headers": dict(self.headers), "raw": raw}
                try:
                    record["json"] = json.loads(raw.decode("utf-8")) if raw else {}
                except (ValueError, UnicodeDecodeError):
                    record["json"] = None
                outer.requests.append(record)
                try:
                    plan = outer.responder(record)
                except Exception as exc:  # noqa: BLE001 - surfaced as 500 to the client
                    plan = {"status": 500, "body": json.dumps({"error": str(exc)}).encode()}
                status = plan.get("status", 200)
                stream = plan.get("stream")
                headers = plan.get("headers", {}) or {}
                if stream is None:
                    body = plan.get("body", b"")
                    if isinstance(body, str):
                        body = body.encode("utf-8")
                    self.send_response(status)
                    self.send_header("Content-Type", plan.get("content_type", "application/json"))
                    for key, value in headers.items():
                        self.send_header(key, value)
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                        self.wfile.flush()
                    except OSError:
                        pass
                    return
                total = b"".join(item.get("data", b"") for item in stream)
                self.send_response(status)
                self.send_header("Content-Type", plan.get("content_type", "text/event-stream"))
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(total)))
                self.end_headers()
                for item in stream:
                    delay = item.get("delay", 0)
                    if delay:
                        time.sleep(delay)
                    data = item.get("data", b"")
                    if data:
                        try:
                            self.wfile.write(data)
                            self.wfile.flush()
                        except OSError:
                            return
                    stall = item.get("stall")
                    if stall:
                        time.sleep(stall)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def chatgpt_events(content=REALTIME_JSON, *, usage=None, completed=True) -> list:
    events = [delta_event(content)]
    if completed:
        events.append(completed_event(usage=usage))
    return events


def chatgpt_server(events, *, status=200, request_id="req-test-1", extra_headers=None):
    body = sse(*events)

    def responder(_record):
        return {"status": status,
                "headers": {"x-request-id": request_id, **(extra_headers or {})},
                "content_type": "text/event-stream",
                "stream": [{"data": body}]}
    return FakeHttpServer(responder)


def request(kind=inference_schemas.KIND_REALTIME_REPLY, *, profile="profile-1",
            task_id="task-1", schema_version="v1", version_key="version-1", **input_data):
    return inference_schemas.InferenceRequest(
        task_id=task_id, kind=kind, profile_id=profile, input_data=input_data,
        schema_version=schema_version, version_key=version_key)


def chatgpt_profile(url, *, model="gpt-test", api_key=""):
    return inference.GatewayProfile(backend="chatgpt", url=url, model=model, api_key=api_key)


def compatible_profile(url, *, model="compat-model", api_key="synthetic-key"):
    return inference.GatewayProfile(backend="compatible", url=url, model=model, api_key=api_key)


class InferenceTestCase(unittest.TestCase):
    def gateway(self, profile, **kwargs):
        gateway = inference.InferenceGateway(**kwargs)
        gateway.register_profile("profile-1", profile)
        return gateway

    def run_chatgpt(self, server, *, kind=inference_schemas.KIND_REALTIME_REPLY,
                    gateway=None, **input_data):
        gateway = gateway or self.gateway(chatgpt_profile(server.url + "/v1/responses"))
        input_data.setdefault("instructions", "系统提示")
        input_data.setdefault("input", "用户文字")
        return gateway.run(request(kind, **input_data))


class TransportTimeoutTests(InferenceTestCase):
    def test_default_timeouts_match_the_design(self):
        gateway = inference.InferenceGateway()
        self.assertEqual(gateway.connect_timeout, 10.0)
        self.assertEqual(gateway.idle_timeout, 30.0)
        self.assertEqual(gateway.kind_timeouts[inference_schemas.KIND_REALTIME_REPLY], 120.0)
        self.assertEqual(gateway.kind_timeouts[inference_schemas.KIND_HISTORY_EXTRACT], 180.0)
        self.assertEqual(gateway.kind_timeouts[inference_schemas.KIND_HISTORY_REDUCE], 180.0)

    def test_connection_failure_is_reported_as_transient(self):
        gateway = inference.InferenceGateway(connect_timeout=0.5)
        gateway.register_profile("profile-1", chatgpt_profile("http://127.0.0.1:1/v1/responses"))
        with self.assertRaises(inference.InferenceTransientError):
            gateway.run(request(instructions="sys", input="用户文字"))

    def test_idle_timeout_aborts_a_stalled_stream(self):
        def responder(_record):
            return {"content_type": "text/event-stream", "stream": [
                {"data": sse(delta_event(REALTIME_JSON))},
                {"stall": 3.0},
                {"data": sse(completed_event())},
            ]}
        server = FakeHttpServer(responder)
        self.addCleanup(server.close)
        gateway = inference.InferenceGateway(connect_timeout=1.0, idle_timeout=0.3,
                                             kind_timeouts={inference_schemas.KIND_REALTIME_REPLY: 10.0})
        gateway.register_profile("profile-1", chatgpt_profile(server.url + "/v1/responses"))
        started = time.monotonic()
        with self.assertRaises(inference.InferenceTimeoutError):
            gateway.run(request(instructions="sys", input="用户文字"))
        self.assertLess(time.monotonic() - started, 3)

    def test_total_timeout_uses_the_injected_clock(self):
        server = chatgpt_server([delta_event(REALTIME_JSON), completed_event()])
        self.addCleanup(server.close)
        clock = FakeClock(step=60.0)
        gateway = self.gateway(chatgpt_profile(server.url + "/v1/responses"), clock=clock)
        with self.assertRaises(inference.InferenceTimeoutError):
            gateway.run(request(instructions="sys", input="用户文字"))

    def test_cancel_closes_the_stream_and_raises(self):
        def responder(_record):
            return {"content_type": "text/event-stream", "stream": [
                {"data": sse(delta_event(REALTIME_JSON))},
                {"stall": 2.0},
                {"data": sse(completed_event())},
            ]}
        server = FakeHttpServer(responder)
        self.addCleanup(server.close)
        gateway = self.gateway(chatgpt_profile(server.url + "/v1/responses"),
                               connect_timeout=1.0, idle_timeout=10.0,
                               kind_timeouts={inference_schemas.KIND_REALTIME_REPLY: 10.0})
        cancel = CancellationToken()
        timer = threading.Timer(0.2, cancel.cancel)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        with self.assertRaises(CancelledError):
            gateway.run(request(instructions="sys", input="用户文字"), cancel)
        self.assertLess(time.monotonic() - started, 5)

    def test_oversized_stream_is_rejected(self):
        server = chatgpt_server([delta_event("x" * 8192), completed_event()])
        self.addCleanup(server.close)
        gateway = self.gateway(chatgpt_profile(server.url + "/v1/responses"),
                               max_response_bytes=512)
        with self.assertRaises(inference.InferenceProtocolError) as raised:
            gateway.run(request(instructions="sys", input="用户文字"))
        self.assertIn("过大", str(raised.exception))


class TransportErrorMappingTests(InferenceTestCase):
    def error_server(self, status, payload=None):
        def responder(_record):
            return {"status": status,
                    "body": json.dumps(payload or {"error": "synthetic"}).encode()}
        server = FakeHttpServer(responder)
        self.addCleanup(server.close)
        return server

    def test_401_and_403_are_authorization_errors(self):
        for status in (401, 403):
            with self.subTest(status=status):
                server = self.error_server(status)
                gateway = self.gateway(chatgpt_profile(server.url + "/v1/responses"))
                with self.assertRaises(inference.InferenceAuthError):
                    gateway.run(request(instructions="sys", input="用户文字"))

    def test_429_and_5xx_are_transient_errors(self):
        for status in (429, 500, 503):
            with self.subTest(status=status):
                server = self.error_server(status)
                gateway = self.gateway(chatgpt_profile(server.url + "/v1/responses"))
                with self.assertRaises(inference.InferenceTransientError):
                    gateway.run(request(instructions="sys", input="用户文字"))

    def test_redirect_is_refused_without_forwarding_credentials_or_payload(self):
        target = FakeHttpServer(lambda _record: {"status": 200, "body": b"{}"})
        self.addCleanup(target.close)

        def responder(_record):
            return {"status": 302, "headers": {"Location": target.url + "/stolen"},
                    "body": b""}
        redirector = FakeHttpServer(responder)
        self.addCleanup(redirector.close)
        gateway = self.gateway(chatgpt_profile(redirector.url + "/v1/responses"))
        with self.assertRaises(inference.InferenceProtocolError):
            gateway.run(request(instructions="sys", input="用户文字"))
        self.assertEqual(target.requests, [])


class StreamProtocolTests(InferenceTestCase):
    def test_failed_event_after_delta_is_not_success(self):
        events = [delta_event(REALTIME_JSON),
                  {"type": "response.failed", "response": {"error": {"code": "server_error"}}}]
        server = chatgpt_server(events)
        self.addCleanup(server.close)
        with self.assertRaises(inference.InferenceError):
            self.run_chatgpt(server)

    def test_incomplete_event_after_delta_is_not_success(self):
        events = [delta_event(REALTIME_JSON),
                  {"type": "response.incomplete", "response": {"incomplete_details": {}}}]
        server = chatgpt_server(events)
        self.addCleanup(server.close)
        with self.assertRaises(inference.InferenceError):
            self.run_chatgpt(server)

    def test_nested_response_error_quota_pauses_tasks(self):
        events = [delta_event(REALTIME_JSON),
                  {"type": "response.failed",
                   "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}]
        server = chatgpt_server(events)
        self.addCleanup(server.close)
        with self.assertRaises(inference.InferenceQuotaError) as raised:
            self.run_chatgpt(server)
        self.assertEqual(raised.exception.code, "subscription_sharing_usage_limit_exceeded")

    def test_stream_closed_without_completed_event_is_not_success(self):
        server = chatgpt_server([delta_event(REALTIME_JSON)])
        self.addCleanup(server.close)
        with self.assertRaises(inference.InferenceError):
            self.run_chatgpt(server)

    def test_invalid_schema_in_completed_stream_is_a_protocol_error(self):
        server = chatgpt_server([delta_event('{"replies": ["只有一条"]}'), completed_event()])
        self.addCleanup(server.close)
        with self.assertRaises(inference.InferenceProtocolError):
            self.run_chatgpt(server)

    def test_successful_realtime_result_carries_metadata_only(self):
        usage = {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
        server = chatgpt_server(chatgpt_events(usage=usage), request_id="req-abc")
        self.addCleanup(server.close)
        result = self.run_chatgpt(server, instructions="系统提示", input="用户文字")
        self.assertEqual(result.validated_data["replies"][0], "请问需要什么规格？")
        self.assertEqual(result.request_id, "req-abc")
        self.assertEqual(result.usage, usage)
        self.assertGreaterEqual(result.elapsed, 0)
        fields = {field.name for field in __import__("dataclasses").fields(inference.InferenceResult)}
        self.assertEqual(fields, {"validated_data", "request_id", "elapsed", "usage"})
        for name in ("raw", "body", "headers", "payload", "response_text", "stream"):
            self.assertFalse(hasattr(result, name))
        request_record = server.requests[0]
        self.assertEqual(request_record["headers"]["Authorization"] if "Authorization" in request_record["headers"] else "", "")
        self.assertEqual(request_record["headers"]["Accept"], "text/event-stream")

    def test_chatgpt_payload_sends_only_allowed_fields(self):
        server = chatgpt_server(chatgpt_events())
        self.addCleanup(server.close)
        gateway = self.gateway(chatgpt_profile(server.url + "/v1/responses", api_key="synthetic-token"))
        gateway.run(request(instructions="系统提示", input="用户文字", temperature=0.9,
                            tools=[{"type": "web_search"}], evil="ignored"))
        payload = server.requests[0]["json"]
        self.assertEqual(set(payload), {"model", "instructions", "input", "store", "stream"})
        self.assertFalse(payload["store"])
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["instructions"], "系统提示")
        self.assertEqual(payload["input"][0]["content"][0]["text"], "用户文字")
        self.assertEqual(server.requests[0]["headers"]["Authorization"], "Bearer synthetic-token")


class CompatibleProtocolTests(InferenceTestCase):
    def test_compatible_backend_uses_chat_completions_protocol(self):
        def responder(_record):
            return {"body": json.dumps({
                "id": "chatcmpl-1",
                "choices": [{"message": {"content": REALTIME_JSON}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }, ensure_ascii=False).encode()}
        server = FakeHttpServer(responder)
        self.addCleanup(server.close)
        gateway = self.gateway(compatible_profile(server.url + "/v1/chat/completions"))
        result = gateway.run(request(messages=[{"role": "system", "content": "sys"},
                                               {"role": "user", "content": "用户文字"}],
                                     temperature=0.6, max_tokens=1200))
        self.assertEqual(result.validated_data["replies"][0], "请问需要什么规格？")
        request_record = server.requests[0]
        self.assertEqual(request_record["path"], "/v1/chat/completions")
        self.assertEqual(request_record["headers"]["Authorization"], "Bearer synthetic-key")
        self.assertEqual(request_record["json"]["model"], "compat-model")
        self.assertEqual(request_record["json"]["messages"][1]["content"], "用户文字")
        self.assertEqual(request_record["json"]["temperature"], 0.6)

    def test_compatible_http_errors_are_classified(self):
        cases = {401: inference.InferenceAuthError, 403: inference.InferenceAuthError,
                 429: inference.InferenceTransientError, 503: inference.InferenceTransientError}
        for status, expected in cases.items():
            with self.subTest(status=status):
                server = FakeHttpServer(lambda _record, status=status: {
                    "status": status, "body": b'{"error": "synthetic"}'})
                self.addCleanup(server.close)
                gateway = self.gateway(compatible_profile(server.url + "/v1/chat/completions"))
                with self.assertRaises(expected):
                    gateway.run(request(messages=[{"role": "user", "content": "x"}]))


class SchemaTests(InferenceTestCase):
    def test_history_extract_schema_accepts_valid_items(self):
        server = chatgpt_server([delta_event(HISTORY_EXTRACT_JSON), completed_event()])
        self.addCleanup(server.close)
        result = self.run_chatgpt(
            server, kind=inference_schemas.KIND_HISTORY_EXTRACT,
            instructions="提取", input="合成历史",
            records=[{"record_id": "r1", "text": "客户要求周五前报价"},
                     {"record_id": "r2", "text": "客户又问价格"}])
        self.assertEqual(result.validated_data["items"][0]["category"], "fact")

    def test_history_extract_rejects_out_of_scope_evidence(self):
        payload = json.dumps({"items": [
            {"category": "fact", "text": "不存在的证据",
             "evidence": [{"record_id": "record-outside", "start": 0, "end": 2}]}]})
        server = chatgpt_server([delta_event(payload), completed_event()])
        self.addCleanup(server.close)
        with self.assertRaises(inference.InferenceProtocolError):
            self.run_chatgpt(server, kind=inference_schemas.KIND_HISTORY_EXTRACT,
                             instructions="提取", input="合成历史",
                             records=[{"record_id": "r1", "text": "客户要求周五前报价"}])

    def test_history_extract_rejects_missing_or_inverted_evidence(self):
        for item in (
            {"category": "fact", "text": "没有证据", "evidence": []},
            {"category": "fact", "text": "倒置范围", "evidence": [{"record_id": "r1", "start": 5, "end": 2}]},
            {"category": "unknown", "text": "类别错误", "evidence": [{"record_id": "r1", "start": 0, "end": 2}]},
        ):
            with self.subTest(item=item):
                server = chatgpt_server([delta_event(json.dumps({"items": [item]})), completed_event()])
                self.addCleanup(server.close)
                with self.assertRaises(inference.InferenceProtocolError):
                    self.run_chatgpt(server, kind=inference_schemas.KIND_HISTORY_EXTRACT,
                                     instructions="提取", input="合成历史")

    def test_history_reduce_schema_validates_summary_and_evidence(self):
        server = chatgpt_server([delta_event(HISTORY_REDUCE_JSON), completed_event()])
        self.addCleanup(server.close)
        result = self.run_chatgpt(server, kind=inference_schemas.KIND_HISTORY_REDUCE,
                                  instructions="合并", input="合成批次摘要")
        self.assertEqual(result.validated_data["summary"], "客户关注报价与交期。")
        broken = chatgpt_server([delta_event(json.dumps({"summary": ""})), completed_event()])
        self.addCleanup(broken.close)
        with self.assertRaises(inference.InferenceProtocolError):
            self.run_chatgpt(broken, kind=inference_schemas.KIND_HISTORY_REDUCE,
                             instructions="合并", input="合成批次摘要")

    def test_unknown_profile_and_backend_are_rejected(self):
        gateway = inference.InferenceGateway()
        with self.assertRaises(ValueError):
            gateway.run(request(instructions="sys", input="x"))
        gateway.register_profile("profile-1", inference.GatewayProfile(
            backend="weird", url="http://127.0.0.1:1/v1", model="m"))
        with self.assertRaises(ValueError):
            gateway.run(request(instructions="sys", input="x"))

    def test_request_validation_fails_closed(self):
        with self.assertRaises(ValueError):
            inference_schemas.InferenceRequest(task_id="", kind="realtime_reply",
                                               profile_id="p", input_data={},
                                               schema_version="v1", version_key="v")
        with self.assertRaises(ValueError):
            inference_schemas.InferenceRequest(task_id="t", kind="unknown",
                                               profile_id="p", input_data={},
                                               schema_version="v1", version_key="v")
        with self.assertRaises(ValueError):
            inference.InferenceGateway().run(
                inference_schemas.InferenceRequest(task_id="t",
                                                   kind=inference_schemas.KIND_REALTIME_REPLY,
                                                   profile_id="p", input_data={},
                                                   schema_version="v9", version_key="v"))


if __name__ == "__main__":
    unittest.main()
