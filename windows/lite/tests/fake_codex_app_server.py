"""Deterministic fake ``codex app-server`` used by the unit tests.

It speaks the same newline-delimited JSON-RPC subset as the real app-server:
initialize / initialized / account/read / model/list / thread/start / turn/start
and the thread, item and turn notifications. Behaviour is controlled by
environment variables so no test needs a Codex login or model quota:

* ``FAKE_CODEX_LOG``: append every received request/response JSON line here.
* ``FAKE_CODEX_REPLY``: final agent text for text turns.
* ``FAKE_CODEX_IMAGE_REPLY``: final agent text when a turn contains an image.
* ``FAKE_CODEX_MODE``: ``fail``, ``hang`` or ``server_request``.
"""
import json
import os
import sys
import time

# The client always speaks UTF-8 on the wire; Windows pipes default to the ANSI
# code page when stdin/stdout are redirected, so force UTF-8 on both ends.
try:
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

LOG = os.environ.get("FAKE_CODEX_LOG", "")
MODE = os.environ.get("FAKE_CODEX_MODE", "")


def log(entry):
    if not LOG:
        return
    try:
        with open(LOG, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def send(payload):
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def wait_response(expected_id):
    """Block until the client answers a server->client request; log it."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        log({"received": message})
        if message.get("id") == expected_id:
            return message
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        log({"received": message})
        method = message.get("method")
        request_id = message.get("id")
        if method == "initialize":
            send({"id": request_id, "result": {
                "userAgent": "fake-codex/0.0.1", "codexHome": "C:\\fake",
                "platformFamily": "windows", "platformOs": "windows"}})
        elif method == "account/read":
            send({"id": request_id, "result": {
                "account": {"type": "chatgpt", "email": "fake@example.test", "planType": "plus"},
                "requiresOpenaiAuth": True}})
        elif method == "model/list":
            send({"id": request_id, "result": {"data": [
                {"id": "fake-text", "model": "fake-text", "displayName": "Fake Text",
                 "hidden": False, "isDefault": True, "defaultReasoningEffort": "low",
                 "description": "", "supportedReasoningEfforts": [],
                 "inputModalities": ["text"]},
                {"id": "fake-vision", "model": "fake-vision", "displayName": "Fake Vision",
                 "hidden": False, "isDefault": False, "defaultReasoningEffort": "low",
                 "description": "", "supportedReasoningEfforts": [],
                 "inputModalities": ["text", "image"]},
            ]}})
        elif method == "thread/start":
            send({"id": request_id, "result": {
                "thread": {"id": "fake-thread"}, "approvalPolicy": "never",
                "approvalsReviewer": "user", "cwd": "C:\\fake", "model": "fake-text",
                "modelProvider": "openai", "sandbox": {"type": "readOnly"}}})
        elif method == "turn/start":
            send({"id": request_id, "result": {
                "turn": {"id": "fake-turn", "items": [], "status": "inProgress"}}})
            log({"turn_start": message.get("params", {})})
            if MODE == "hang":
                continue
            if MODE == "fail":
                send({"method": "turn/completed", "params": {
                    "threadId": "fake-thread", "turn": {
                        "id": "fake-turn", "items": [], "status": "failed",
                        "error": {"message": "synthetic quota failure"}}}})
                continue
            if MODE == "server_request":
                send({"id": 900, "method": "item/fileChange/requestApproval",
                      "params": {"threadId": "fake-thread", "turnId": "fake-turn"}})
                if wait_response(900) is None:
                    return
            reply = os.environ.get("FAKE_CODEX_REPLY", "")
            has_image = any(item.get("type") == "image"
                            for item in (message.get("params", {}).get("input") or [])
                            if isinstance(item, dict))
            if has_image:
                reply = os.environ.get("FAKE_CODEX_IMAGE_REPLY", reply)
            send({"method": "item/agentMessage/delta", "params": {
                "threadId": "fake-thread", "turnId": "fake-turn",
                "itemId": "fake-message", "delta": reply[: max(1, len(reply) // 2)]}})
            send({"method": "item/agentMessage/delta", "params": {
                "threadId": "fake-thread", "turnId": "fake-turn",
                "itemId": "fake-message", "delta": reply[max(1, len(reply) // 2):]}})
            send({"method": "turn/completed", "params": {
                "threadId": "fake-thread", "turn": {
                    "id": "fake-turn", "status": "completed",
                    "items": [{"type": "agentMessage", "id": "fake-message", "text": reply}]}}})
        elif method in ("turn/interrupt", "thread/unsubscribe"):
            send({"id": request_id, "result": {}})
        elif request_id is not None:
            # Unknown client request: keep the protocol alive.
            send({"id": request_id, "result": {}})


if __name__ == "__main__":
    main()
