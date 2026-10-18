"""C5 synthetic acceptance: real InferenceGateway/InferenceQueue + fake OpenAI server.

SYNTHETIC ONLY. A loopback OpenAI-compatible server returns fixed JSON/SSE content;
the *real* transport, schemas, queue and (for one case) ``subscription.analyze`` run
against it. This proves wiring/error/cancel/retry behaviour deterministically and
does **not** prove real model quality, real ChatGPT plan quota behaviour or live
network conditions. No real token, credential, chat content or user profile is used.

Scenarios:
* T9 history reference: real MemoryRetriever over a synthetic encrypted history store
  builds a prompt with `[历史参考]` evidence; the fake model reports how many
  references it received; the returned realtime suggestions arrive through the queue.
* transient 503 then retry succeeds; authorization error stops same-profile tasks;
  nested ChatGPT quota error pauses the queue until resume; cancel closes a stalled
  response stream and a retry succeeds; max one real HTTP request in flight.
* ``subscription.analyze`` (real ChatGPT Responses protocol) consumes the same
  synthetic history text end to end.
"""
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from liteapp import inference_schemas, subscription
from liteapp.contracts import CancelledError, ScopeKey
from liteapp.history import HistoryStore
from liteapp.history_models import ImportSource, ImportedRecord
from liteapp.inference import GatewayProfile, InferenceGateway
from liteapp.inference_queue import InferenceQueue
from liteapp.memory_retrieval import MemoryRetriever

REALTIME_RESULT = {
    "intent": "询问价格", "need": "报价", "action": "确认规格",
    "caution": "合成模型；信息有限",
    "replies": ["请问需要什么规格？", "可以先说说用途吗？", "稍后整理报价给您。"],
}
SYNTHETIC_HISTORY = (
    "合成历史1：客户上次说预算大约五万元，希望周五前拿到报价。",
    "合成历史2：客户对交期敏感，要求本月内发货。",
    "合成历史3：客户询问售后保修，还没有最终决定。",
)


def xor_protect(raw):
    return bytes(value ^ 0xC5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xC5 for value in raw)


def _sse_bytes(events):
    return b"".join(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n"
                    for event in events)


def _delta_event(text):
    return {"type": "response.output_text.delta", "delta": text}


def _completed_event(usage=None):
    response = {"output": []}
    if usage is not None:
        response["usage"] = usage
    return {"type": "response.completed", "response": response}


class FakeOpenAIServer:
    """Loopback fake for /v1/chat/completions and /v1/responses (synthetic content)."""

    def __init__(self):
        self.requests = []
        self.modes = []
        self.mode = "success"  # str or list consumed one entry per request
        self.block_seconds = 0.0
        self.stall_seconds = 1.2
        self.max_concurrent = 0
        self.closed_early = 0
        self._active = 0
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    body = json.loads(raw.decode("utf-8")) if raw else {}
                except (ValueError, UnicodeDecodeError):
                    body = {}
                with outer._lock:
                    outer._active += 1
                    outer.max_concurrent = max(outer.max_concurrent, outer._active)
                    mode = outer._next_mode_locked()
                    outer.requests.append({"path": self.path,
                                           "headers": dict(self.headers),
                                           "json": body, "mode": mode})
                try:
                    if outer.block_seconds:
                        time.sleep(outer.block_seconds)
                    if self.path.endswith("/responses"):
                        outer._respond_responses(self, mode)
                    else:
                        outer._respond_chat(self, mode, body)
                except OSError:
                    pass
                finally:
                    with outer._lock:
                        outer._active -= 1

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    # -- control -----------------------------------------------------------
    def _next_mode_locked(self):
        if isinstance(self.mode, list):
            value = self.mode.pop(0) if self.mode else "success"
        else:
            value = self.mode
        self.modes.append(value)
        return value

    def set_modes(self, *modes):
        self.mode = list(modes)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    # -- responses ---------------------------------------------------------
    @staticmethod
    def _send_json(handler, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    def _chat_content(self, body):
        messages = body.get("messages") or []
        user = " ".join(str(message.get("content") or "") for message in messages
                        if isinstance(message, dict))
        history_count = user.count("[历史参考]")
        result = dict(REALTIME_RESULT)
        result["caution"] = f"合成模型；收到历史参考{history_count}条"
        return json.dumps(result, ensure_ascii=False)

    def _respond_chat(self, handler, mode, body):
        if mode == "auth":
            return self._send_json(handler, 401, {"error": {"message": "invalid token"}})
        if mode == "transient":
            return self._send_json(handler, 503, {"error": {"message": "unavailable"}})
        if mode == "rate":
            return self._send_json(handler, 429, {"error": {"message": "rate limited"}})
        payload = {
            "id": "chatcmpl-c5-synthetic",
            "choices": [{"message": {"content": self._chat_content(body)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        return self._send_json(handler, 200, payload)

    def _respond_responses(self, handler, mode):
        if mode == "auth":
            return self._send_json(handler, 401, {"error": {"message": "invalid token"}})
        content = json.dumps(REALTIME_RESULT, ensure_ascii=False)
        if mode == "quota":
            events = [
                _delta_event(content),
                {"type": "response.failed",
                 "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}},
            ]
            return self._send_sse(handler, _sse_bytes(events))
        if mode == "stall":
            full = _sse_bytes([_delta_event(content), _completed_event(
                {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13})])
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Content-Length", str(len(full)))
            handler.end_headers()
            cut = max(1, len(full) // 3)
            handler.wfile.write(full[:cut])
            handler.wfile.flush()
            time.sleep(self.stall_seconds)
            if self._wait_client_closed(handler, timeout=1.5):
                with self._lock:
                    self.closed_early += 1
                return
            try:
                handler.wfile.write(full[cut:])
                handler.wfile.flush()
            except OSError:
                with self._lock:
                    self.closed_early += 1
            return
        events = [_delta_event(content), _completed_event(
            {"input_tokens": 9, "output_tokens": 4, "total_tokens": 13})]
        return self._send_sse(handler, _sse_bytes(events))

    def _send_sse(self, handler, raw):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
        handler.wfile.flush()

    @staticmethod
    def _client_closed(handler):
        """Detect that the client closed the connection (cancel closed the stream)."""
        try:
            handler.connection.settimeout(0.2)
            return handler.connection.recv(1, socket.MSG_PEEK) == b""
        except (socket.timeout, TimeoutError, BlockingIOError):
            return False
        except OSError:
            return True

    @classmethod
    def _wait_client_closed(cls, handler, timeout=1.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if cls._client_closed(handler):
                return True
            time.sleep(0.05)
        return False


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _C5InferenceTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.server = FakeOpenAIServer()
        self.addCleanup(self.server.close)
        self.gateway = InferenceGateway()
        self.gateway.register_profile("compat", GatewayProfile(
            backend="compatible", url=self.server.url + "/v1/chat/completions",
            model="synthetic-c5-model", api_key="synthetic-c5-key"))
        self.gateway.register_profile("compat2", GatewayProfile(
            backend="compatible", url=self.server.url + "/v1/chat/completions",
            model="synthetic-c5-model", api_key="synthetic-c5-key"))
        self.gateway.register_profile("plan", GatewayProfile(
            backend="chatgpt", url=self.server.url + "/v1/responses",
            model="synthetic-c5-plan", api_key="synthetic-c5-token"))
        self._queues = []

    def queue(self, **kwargs):
        kwargs.setdefault("auto_start", True)
        queue = InferenceQueue(self.gateway, **kwargs)
        self._queues.append(queue)
        self.addCleanup(queue.stop)
        return queue

    @staticmethod
    def request(task_id="task-1", *, profile="compat", kind="realtime_reply",
                **input_data):
        return inference_schemas.InferenceRequest(
            task_id=task_id, kind=kind, profile_id=profile,
            input_data=input_data, schema_version="v1", version_key="version-" + task_id)

    def messages(self, user_text):
        return [{"role": "system", "content": "你是合成验收助手。"},
                {"role": "user", "content": user_text}]


class T9HistoryReferenceTests(_C5InferenceTestCase):
    def synthetic_history_store(self):
        store = HistoryStore(self.folder / "memory.sqlite3",
                             protect=xor_protect, unprotect=xor_unprotect)
        scope = store.create_person("合成客户", "synthetic-account")
        store.import_records(scope, ImportSource(
            digest="a" * 64, kind="html", name="合成历史.html"),
            [ImportedRecord(text=text, ordinal=index)
             for index, text in enumerate(SYNTHETIC_HISTORY)])
        return store, scope

    def test_history_reference_reaches_real_gateway_through_queue(self):
        store, scope = self.synthetic_history_store()
        snapshot = store.snapshot(scope)
        retriever = MemoryRetriever(store)
        context = retriever.retrieve(scope, "预算 报价 交期",
                                     snapshot.memory_version,
                                     max_items=4, max_bytes=2048)
        self.assertGreaterEqual(len(context.items), 1)
        for ref in context.refs:
            self.assertGreater(len(store.read_evidence(ref).text), 0)
        history_lines = ["[历史参考] " + item.text for item in context.items]
        user_text = ("当前对话：\n对方：请问这个报价多少？\n\n"
                     + "\n".join(history_lines))
        queue = self.queue()
        handle = queue.submit(self.request(
            "history-ref", profile="compat", messages=self.messages(user_text)),
            scope_id="source-a")
        self.assertTrue(handle.wait(10), "queue task did not finish")
        self.assertIsNone(handle.error)
        self.assertEqual(handle.state, "completed")
        self.assertEqual(handle.result.validated_data["replies"],
                         REALTIME_RESULT["replies"])
        self.assertIn(f"历史参考{len(context.items)}条",
                      handle.result.validated_data["caution"])
        request = self.server.requests[-1]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"],
                         "Bearer synthetic-c5-key")
        sent_user = " ".join(str(message["content"])
                             for message in request["json"]["messages"])
        self.assertIn("[历史参考]", sent_user)
        self.assertIn("预算大约五万元", sent_user)
        self.assertEqual(queue.in_flight_count, 0)

    def test_t9_realtime_analyze_with_history_context_via_subscription(self):
        config = {
            "backend": "chatgpt", "model": "synthetic-c5-plan",
            "chatgpt_model": "synthetic-c5-plan",
            "subscription": {
                "access_token": "synthetic-c5-plan-token",
                "refresh_token": "synthetic-c5-refresh",
                "access_expires_at": time.time() + 3600,
                "scopes": ["chatgpt.tokens.use.direct"],
                "client_id": "synthetic-client",
            },
            "subscription_models": [["Synthetic Plan", "synthetic-c5-plan"]],
            "relationship": "合成客户", "style": "自然",
        }
        prompt_text = ("对方：请问报价多少？\n\n[历史参考] 客户上次说预算五万元。\n"
                       "[历史参考] 客户要求本月内发货。")
        with patch.object(subscription, "API_BASE", self.server.url + "/v1"):
            result = subscription.analyze(config, prompt_text)
        self.assertEqual(result["replies"], REALTIME_RESULT["replies"])
        request = self.server.requests[-1]
        self.assertEqual(request["path"], "/v1/responses")
        self.assertEqual(request["headers"]["Authorization"],
                         "Bearer synthetic-c5-plan-token")
        self.assertIs(request["json"]["store"], False)
        self.assertIs(request["json"]["stream"], True)
        sent = json.dumps(request["json"], ensure_ascii=False)
        self.assertIn("[历史参考]", sent)
        self.assertIn("预算五万元", sent)
        self.assertNotIn("temperature", request["json"])


class ErrorRetryCancelTests(_C5InferenceTestCase):
    def test_transient_error_then_retry_succeeds(self):
        self.server.set_modes("transient", "success")
        queue = self.queue(auto_start=False)
        first = queue.submit(self.request("transient-1",
                                          messages=self.messages("合成重试消息")),
                             scope_id="source-a")
        queue.run_pending()
        self.assertIsInstance(first.error, inference_schemas.InferenceTransientError)
        self.assertFalse(queue.paused)
        retry = queue.submit(self.request("transient-2",
                                          messages=self.messages("合成重试消息")),
                             scope_id="source-a")
        queue.run_pending()
        self.assertIsNone(retry.error)
        self.assertEqual(retry.state, "completed")
        self.assertEqual([item["mode"] for item in self.server.requests],
                         ["transient", "success"])

    def test_authorization_error_stops_only_that_profile(self):
        self.server.set_modes("auth", "success")
        queue = self.queue(auto_start=False)
        denied = queue.submit(self.request("denied-1", profile="compat",
                                           messages=self.messages("合成消息")),
                              scope_id="source-a")
        same_profile = queue.submit(self.request("denied-2", profile="compat",
                                                 messages=self.messages("合成消息")),
                                    scope_id="source-b")
        other_profile = queue.submit(self.request("other-1", profile="compat2",
                                                  messages=self.messages("合成消息")),
                                     scope_id="source-c")
        queue.run_pending()
        self.assertIsInstance(denied.error, inference_schemas.InferenceAuthError)
        self.assertTrue(same_profile.cancelled)
        self.assertEqual(other_profile.state, "completed")
        self.assertFalse(queue.paused)

    def test_nested_quota_error_pauses_queue_until_resume(self):
        self.server.set_modes("quota", "success")
        queue = self.queue()
        limited = queue.submit(self.request("quota-1", profile="plan",
                                            instructions="系统", input="用户文字"),
                               scope_id="source-a")
        self.assertTrue(limited.wait(10))
        self.assertIsInstance(limited.error, inference_schemas.InferenceQuotaError)
        self.assertTrue(queue.paused)
        self.assertTrue(queue.pause_reason)
        blocked = queue.submit(self.request("quota-2", profile="plan",
                                            instructions="系统", input="用户文字"),
                               scope_id="source-b")
        self.assertFalse(blocked.wait(0.2))
        self.assertEqual(queue.pending_count, 1)
        queue.resume()
        self.assertTrue(blocked.wait(10))
        self.assertIsNone(blocked.error)
        self.assertEqual(len(self.server.requests), 2)

    def test_cancel_closes_stalled_stream_then_retry_succeeds(self):
        self.server.set_modes("stall", "success")
        queue = self.queue()
        handle = queue.submit(self.request("cancel-1", profile="plan",
                                           instructions="系统", input="用户文字"),
                              scope_id="source-a")
        self.assertTrue(wait_until(lambda: len(self.server.requests) >= 1, timeout=5))
        started = time.monotonic()
        handle.cancel()
        self.assertTrue(handle.wait(5))
        self.assertLess(time.monotonic() - started, 5)
        self.assertIsInstance(handle.error, CancelledError)
        # Client-side proof: the stalled stream cannot deliver a result and the
        # retry opens a fresh request. Server-side TCP close observation is
        # best-effort on Windows and is recorded by tools/c5_synthetic_evidence.py.
        retry = queue.submit(self.request("cancel-2", profile="plan",
                                          instructions="系统", input="用户文字"),
                             scope_id="source-a")
        self.assertTrue(retry.wait(10))
        self.assertIsNone(retry.error)
        self.assertEqual(retry.state, "completed")
        self.assertEqual(len(self.server.requests), 2)

    def test_one_real_http_request_in_flight(self):
        self.server.block_seconds = 0.05
        queue = self.queue()
        handles = [queue.submit(self.request(f"flight-{index}",
                                             messages=self.messages("合成消息")),
                                scope_id=f"source-{index}")
                   for index in range(4)]
        self.assertTrue(all(handle.wait(10) for handle in handles))
        self.assertTrue(all(handle.error is None for handle in handles),
                        [str(handle.error) for handle in handles if handle.error])
        self.assertEqual(self.server.max_concurrent, 1)
        self.assertEqual(sorted(queue.served_order),
                         [f"flight-{index}" for index in range(4)])


if __name__ == "__main__":
    unittest.main()
