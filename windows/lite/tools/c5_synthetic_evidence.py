"""C5 synthetic evidence runner: fake OpenAI server + two synthetic capture sources.

SYNTHETIC ONLY — this script proves deterministic wiring/error/cancel/retry and
multi-source scheduling behaviour. It does **not** prove real model quality, real
ChatGPT plan quota limits, real WeChat windows or real dual-account isolation.
No real credentials, tokens, chat content or user profile are used.

Usage (from ``windows/lite``):
    G:\\codex\\wechat-lite-venv\\Scripts\\python.exe tools\\c5_synthetic_evidence.py

Writes ``test-output/c5/c5_synthetic_evidence.json`` and prints a short summary.
Exit code 0 only when every expectation below holds.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from liteapp import inference_schemas, subscription  # noqa: E402
from liteapp.capture_scheduler import CaptureScheduler  # noqa: E402
from liteapp.contracts import CancelledError, SourceKey  # noqa: E402
from liteapp.discovery import WindowTarget  # noqa: E402
from liteapp.history import HistoryStore  # noqa: E402
from liteapp.history_models import ImportSource, ImportedRecord  # noqa: E402
from liteapp.inference import GatewayProfile, InferenceGateway  # noqa: E402
from liteapp.inference_queue import InferenceQueue  # noqa: E402
from liteapp.memory_retrieval import MemoryRetriever  # noqa: E402
from liteapp.source_registry import SourceRegistry  # noqa: E402

WECHAT = r"C:\Program Files\Tencent\Weixin\Weixin.exe"
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


# --------------------------------------------------------------------------- #
# fake OpenAI-compatible server (synthetic fixed responses)
# --------------------------------------------------------------------------- #
def _sse_bytes(events):
    return b"".join(b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n"
                    for event in events)


def _delta(text):
    return {"type": "response.output_text.delta", "delta": text}


def _completed():
    return {"type": "response.completed",
            "response": {"output": [], "usage": {"input_tokens": 9,
                                                 "output_tokens": 4,
                                                 "total_tokens": 13}}}


class FakeOpenAIServer:
    def __init__(self):
        self.requests = []
        self.mode = "success"
        self.block_seconds = 0.0
        self.stall_seconds = 1.0
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
                    outer.requests.append({"path": self.path, "mode": mode,
                                           "json": body,
                                           "headers": dict(self.headers)})
                try:
                    if outer.block_seconds:
                        time.sleep(outer.block_seconds)
                    if self.path.endswith("/responses"):
                        outer._responses(self, mode)
                    else:
                        outer._chat(self, mode, body)
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

    def _next_mode_locked(self):
        if isinstance(self.mode, list):
            return self.mode.pop(0) if self.mode else "success"
        return self.mode

    def set_modes(self, *modes):
        self.mode = list(modes)

    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    @staticmethod
    def _json(handler, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)

    @staticmethod
    def _sse(handler, raw):
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(raw)))
        handler.end_headers()
        handler.wfile.write(raw)
        handler.wfile.flush()

    def _chat(self, handler, mode, body):
        if mode == "auth":
            return self._json(handler, 401, {"error": {"message": "invalid token"}})
        if mode == "transient":
            return self._json(handler, 503, {"error": {"message": "unavailable"}})
        messages = body.get("messages") or []
        user = " ".join(str(message.get("content") or "") for message in messages
                        if isinstance(message, dict))
        history_count = user.count("[历史参考]")
        result = dict(REALTIME_RESULT)
        result["caution"] = f"合成模型；收到历史参考{history_count}条"
        return self._json(handler, 200, {
            "id": "chatcmpl-c5-synthetic",
            "choices": [{"message": {"content": json.dumps(result, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}})

    def _responses(self, handler, mode):
        if mode == "auth":
            return self._json(handler, 401, {"error": {"message": "invalid token"}})
        content = json.dumps(REALTIME_RESULT, ensure_ascii=False)
        if mode == "quota":
            return self._sse(handler, _sse_bytes([
                _delta(content),
                {"type": "response.failed",
                 "response": {"error": {"code": "subscription_sharing_usage_limit_exceeded"}}}]))
        if mode == "stall":
            full = _sse_bytes([_delta(content), _completed()])
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
        return self._sse(handler, _sse_bytes([_delta(content), _completed()]))

    @staticmethod
    def _client_closed(handler):
        """Detect that the client closed the response stream (e.g. cancellation)."""
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


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def xor_protect(raw):
    return bytes(value ^ 0xC5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xC5 for value in raw)


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def make_request(task_id, *, profile, kind="realtime_reply", **input_data):
    return inference_schemas.InferenceRequest(
        task_id=task_id, kind=kind, profile_id=profile, input_data=input_data,
        schema_version="v1", version_key="version-" + task_id)


def make_gateway(server):
    gateway = InferenceGateway()
    gateway.register_profile("compat", GatewayProfile(
        backend="compatible", url=server.url + "/v1/chat/completions",
        model="synthetic-c5-model", api_key="synthetic-c5-key"))
    gateway.register_profile("compat2", GatewayProfile(
        backend="compatible", url=server.url + "/v1/chat/completions",
        model="synthetic-c5-model", api_key="synthetic-c5-key"))
    gateway.register_profile("plan", GatewayProfile(
        backend="chatgpt", url=server.url + "/v1/responses",
        model="synthetic-c5-plan", api_key="synthetic-c5-token"))
    return gateway


# --------------------------------------------------------------------------- #
# inference evidence
# --------------------------------------------------------------------------- #
def run_inference_evidence(folder, server):
    store = HistoryStore(folder / "memory.sqlite3",
                         protect=xor_protect, unprotect=xor_unprotect)
    scope = store.create_person("合成客户", "synthetic-account")
    store.import_records(scope, ImportSource(
        digest="b" * 64, kind="html", name="合成历史.html"),
        [ImportedRecord(text=text, ordinal=index)
         for index, text in enumerate(SYNTHETIC_HISTORY)])
    snapshot = store.snapshot(scope)
    context = MemoryRetriever(store).retrieve(
        scope, "预算 报价 交期", snapshot.memory_version, max_items=4, max_bytes=2048)
    readback = sum(1 for ref in context.refs if store.read_evidence(ref).text)
    history_lines = ["[历史参考] " + item.text for item in context.items]
    user_text = "当前对话：\n对方：请问这个报价多少？\n\n" + "\n".join(history_lines)
    gateway = make_gateway(server)
    evidence = {"history": {"items": len(context.items), "evidence_readback": readback}}

    # 1) T9 history reference through the real gateway/queue
    queue = InferenceQueue(gateway, auto_start=True)
    try:
        handle = queue.submit(make_request(
            "history-ref", profile="compat",
            messages=[{"role": "system", "content": "你是合成验收助手。"},
                      {"role": "user", "content": user_text}]), scope_id="source-a")
        assert handle.wait(15), "history task did not finish"
        assert handle.error is None, f"history task error: {handle.error}"
        sent_user = " ".join(str(message["content"])
                             for message in server.requests[-1]["json"]["messages"])
        evidence["history"].update({
            "queue_state": handle.state,
            "replies": len(handle.result.validated_data.get("replies", [])),
            "history_marker_echo": f"历史参考{len(context.items)}条",
            "caution": handle.result.validated_data.get("caution", ""),
            "prompt_contains_reference": "[历史参考]" in sent_user,
            "authorization_header": server.requests[-1]["headers"].get("Authorization", ""),
            "in_flight_after": queue.in_flight_count,
        })
        assert evidence["history"]["history_marker_echo"] in evidence["history"]["caution"]
        assert evidence["history"]["replies"] == 3
        assert evidence["history"]["in_flight_after"] == 0

        # 2) transient 503 then retry succeeds
        server.set_modes("transient", "success")
        first = queue.submit(make_request("transient-1", profile="compat",
                                          messages=[{"role": "user", "content": "合成重试"}]),
                             scope_id="source-b")
        assert first.wait(15)
        retry = queue.submit(make_request("transient-2", profile="compat",
                                          messages=[{"role": "user", "content": "合成重试"}]),
                             scope_id="source-b")
        assert retry.wait(15)
        evidence["transient_retry"] = {
            "first_error": type(first.error).__name__,
            "retry_state": retry.state,
            "server_modes_last_two": [item["mode"] for item in server.requests[-2:]],
        }
        assert evidence["transient_retry"]["first_error"] == "InferenceTransientError"
        assert evidence["transient_retry"]["server_modes_last_two"] == ["transient", "success"]
        assert retry.state == "completed"

        # 3) nested ChatGPT quota error pauses the queue, resume succeeds
        server.set_modes("quota", "success")
        limited = queue.submit(make_request("quota-1", profile="plan",
                                            instructions="系统", input="用户文字"),
                               scope_id="source-c")
        assert limited.wait(15)
        paused = queue.paused
        pause_reason = queue.pause_reason
        blocked = queue.submit(make_request("quota-2", profile="plan",
                                            instructions="系统", input="用户文字"),
                               scope_id="source-d")
        blocked_while_paused = not blocked.wait(0.2)
        queue.resume()
        assert blocked.wait(15)
        evidence["quota_pause"] = {
            "error": type(limited.error).__name__,
            "queue_paused": paused,
            "second_blocked_while_paused": blocked_while_paused,
            "resumed_state": blocked.state,
            "pause_reason": pause_reason,
        }
        assert evidence["quota_pause"]["error"] == "InferenceQuotaError"
        assert paused and blocked_while_paused and blocked.state == "completed"

        # 4) cancel a stalled stream, then retry succeeds
        server.set_modes("stall", "success")
        before_cancel = len(server.requests)
        cancel_handle = queue.submit(make_request("cancel-1", profile="plan",
                                                  instructions="系统", input="用户文字"),
                                     scope_id="source-e")
        assert wait_until(lambda: len(server.requests) > before_cancel, 10)
        started = time.monotonic()
        cancel_handle.cancel()
        assert cancel_handle.wait(10)
        closed = wait_until(lambda: server.closed_early >= 1, 5)
        retry = queue.submit(make_request("cancel-2", profile="plan",
                                          instructions="系统", input="用户文字"),
                             scope_id="source-e")
        assert retry.wait(15)
        evidence["cancel_retry"] = {
            "error": type(cancel_handle.error).__name__,
            "cancelled_in_seconds": round(time.monotonic() - started, 2),
            "server_saw_closed_connection_best_effort": closed,
            "retry_state": retry.state,
        }
        assert evidence["cancel_retry"]["error"] == "CancelledError"
        assert retry.state == "completed"

        # 5) at most one real HTTP request in flight through the real queue
        server.block_seconds = 0.03
        handles = [queue.submit(make_request(f"flight-{index}", profile="compat",
                                             messages=[{"role": "user", "content": "合成"}]
                                             ), scope_id=f"flight-{index}")
                   for index in range(4)]
        assert all(handle.wait(15) for handle in handles)
        assert all(handle.error is None for handle in handles)
        evidence["single_flight"] = {
            "server_max_concurrent": server.max_concurrent,
            "tasks": len(handles),
            "served": sorted(queue.served_order),
        }
        assert server.max_concurrent == 1
    finally:
        queue.stop()

    # 6) T9 realtime through the real ChatGPT Responses protocol
    config = {
        "backend": "chatgpt", "model": "synthetic-c5-plan",
        "chatgpt_model": "synthetic-c5-plan",
        "subscription": {"access_token": "synthetic-c5-plan-token",
                         "refresh_token": "synthetic-c5-refresh",
                         "access_expires_at": time.time() + 3600,
                         "scopes": ["chatgpt.tokens.use.direct"],
                         "client_id": "synthetic-client"},
        "subscription_models": [["Synthetic Plan", "synthetic-c5-plan"]],
        "relationship": "合成客户", "style": "自然",
    }
    with patch.object(subscription, "API_BASE", server.url + "/v1"):
        result = subscription.analyze(
            config, "对方：请问报价多少？\n\n[历史参考] 客户上次说预算五万元。")
    request = server.requests[-1]
    evidence["chatgpt_responses_protocol"] = {
        "path": request["path"],
        "store": request["json"].get("store"),
        "stream": request["json"].get("stream"),
        "has_temperature": "temperature" in request["json"],
        "replies": len(result["replies"]),
    }
    assert request["path"] == "/v1/responses"
    assert request["json"].get("store") is False
    assert request["json"].get("stream") is True
    assert "temperature" not in request["json"]
    return evidence


# --------------------------------------------------------------------------- #
# two-source scheduling evidence
# --------------------------------------------------------------------------- #
class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class SyntheticSource:
    def __init__(self, target, registry, scheduler):
        self.source = SourceKey.from_window_target(target)
        self.registry = registry
        self.scheduler = scheduler
        self.frame_seq = 0

    def content_changed(self):
        self.frame_seq += 1
        self._publish()

    def unchanged_frame(self):
        self._publish()

    def _publish(self):
        self.registry.observe_frame(self.source, self.frame_seq)
        self.scheduler.observe_frame(self.source, self.frame_seq)


class SyntheticOcr:
    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.counts = Counter()
        self.jobs = []

    def tick(self):
        job = self.scheduler.take_next()
        if job is None:
            return None
        self.counts[job.source] += 1
        self.jobs.append({"source": "A" if job.source.hwnd == 101 else "B",
                          "priority": job.priority, "frame_seq": job.frame_seq})
        self.scheduler.complete(job.source, frame_seq=job.frame_seq)
        return job


def synthetic_window(hwnd, pid, created, title):
    target = WindowTarget(hwnd, pid, WECHAT, created)
    return target, {"hwnd": int(target), "pid": target.pid, "created": target.created,
                    "path": target.path, "title": title, "status": "visible",
                    "visible": True, "minimized": False}


def run_source_evidence():
    clock = FakeClock()
    registry = SourceRegistry(clock=clock)
    target_a, item_a = synthetic_window(101, 2001, 1_000_000, "合成账号 A")
    target_b, item_b = synthetic_window(202, 2002, 2_000_000, "合成账号 B")
    with patch("liteapp.source_registry.discovery.discover",
               return_value=[item_a, item_b]):
        registry.discover(WECHAT, include_hidden=True)
    initial_managed = len(registry.managed_sources())
    source_a = SourceKey.from_window_target(target_a)
    source_b = SourceKey.from_window_target(target_b)
    scheduler = CaptureScheduler(registry, clock=clock)
    ocr = SyntheticOcr(scheduler)
    a = SyntheticSource(target_a, registry, scheduler)
    b = SyntheticSource(target_b, registry, scheduler)
    evidence = {}

    # active priority + background enabled
    scheduler.activate(source_a)
    assert scheduler.enable(source_b, True)
    a.content_changed()
    b.content_changed()
    clock.advance(3.0)
    first, second = ocr.tick(), ocr.tick()
    evidence["active_priority"] = {
        "first_source": "A" if first and first.source == source_a else "B",
        "first_priority": first.priority if first else None,
        "second_source": "B" if second and second.source == source_b else "A",
        "second_priority": second.priority if second else None,
    }
    assert evidence["active_priority"]["first_source"] == "A"
    assert evidence["active_priority"]["first_priority"] == "active"
    assert evidence["active_priority"]["second_priority"] == "background"

    # background monitoring off by default (fresh pair of frames)
    a.content_changed()
    b.content_changed()
    clock.advance(5.0)
    before_a, before_b = ocr.counts[source_a], ocr.counts[source_b]
    scheduler.enable(source_b, False)
    ocr.tick()
    ocr.tick()
    evidence["background_off"] = {
        "new_a_ocr": ocr.counts[source_a] - before_a,
        "new_b_ocr": ocr.counts[source_b] - before_b,
    }
    assert evidence["background_off"]["new_a_ocr"] == 1
    assert evidence["background_off"]["new_b_ocr"] == 0

    # minimized background pauses only itself
    assert scheduler.enable(source_b, True)
    a.content_changed()
    b.content_changed()
    scheduler.set_minimized(source_b, True)
    clock.advance(5.0)
    before_a, before_b = ocr.counts[source_a], ocr.counts[source_b]
    ocr.tick()
    ocr.tick()
    paused = {"new_a_ocr": ocr.counts[source_a] - before_a,
              "new_b_ocr": ocr.counts[source_b] - before_b}
    scheduler.set_minimized(source_b, False)
    clock.advance(5.0)
    ocr.tick()
    paused["after_restore_b_ocr"] = ocr.counts[source_b] - before_b
    evidence["minimized_pause"] = paused
    assert paused["new_a_ocr"] == 1
    assert paused["new_b_ocr"] == 0
    assert paused["after_restore_b_ocr"] == 1

    # unchanged frames never repeat OCR
    scheduler.activate(source_a)
    a.content_changed()
    clock.advance(5.0)
    before = ocr.counts[source_a]
    ocr.tick()
    for _ in range(5):
        a.unchanged_frame()
        clock.advance(5.0)
        ocr.tick()
    unchanged_extra = ocr.counts[source_a] - before - 1
    evidence["unchanged_frame"] = {"extra_ocr_for_5_repeats": unchanged_extra}
    assert unchanged_extra == 0
    before = ocr.counts[source_a]
    a.content_changed()
    clock.advance(5.0)
    ocr.tick()
    evidence["unchanged_frame"]["new_frame_ocr"] = ocr.counts[source_a] - before
    assert evidence["unchanged_frame"]["new_frame_ocr"] == 1

    # closing one source keeps the other
    assert scheduler.enable(source_b, True)
    a.content_changed()
    b.content_changed()
    registry.close(source_a)
    clock.advance(5.0)
    before_b = ocr.counts[source_b]
    ocr.tick()
    evidence["close_isolation"] = {"other_source_ocr": ocr.counts[source_b] - before_b}
    assert evidence["close_isolation"]["other_source_ocr"] == 1

    evidence["two_sources"] = {
        "source_a": {"hwnd": source_a.hwnd, "pid": source_a.pid},
        "source_b": {"hwnd": source_b.hwnd, "pid": source_b.pid},
        "distinct_source_keys": source_a != source_b,
        "initial_managed": initial_managed,
        "managed_after_closing_a": len(registry.managed_sources()),
    }
    assert evidence["two_sources"]["distinct_source_keys"]
    assert evidence["two_sources"]["initial_managed"] == 2
    return evidence


def main():
    folder = Path(tempfile.mkdtemp(prefix="c5-synthetic-"))
    server = FakeOpenAIServer()
    try:
        inference_evidence = run_inference_evidence(folder, server)
        source_evidence = run_source_evidence()
    finally:
        server.close()
    evidence = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "synthetic_statement": (
            "Synthetic only: fixed loopback fake model, in-memory synthetic window "
            "targets and frames. NOT evidence of real model quality, real ChatGPT "
            "quota limits, real WeChat windows or real dual-account isolation."),
        "assertions": "all hard assertions passed",
        "inference_gateway_queue": inference_evidence,
        "two_source_scheduler": source_evidence,
    }
    output = ROOT / "test-output" / "c5" / "c5_synthetic_evidence.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    print("C5 synthetic evidence: ALL EXPECTATIONS PASSED")
    print(json.dumps({key: evidence[key] for key in
                      ("inference_gateway_queue", "two_source_scheduler")},
                     ensure_ascii=False, indent=2))
    print(f"evidence_json: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
