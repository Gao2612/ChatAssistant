import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from liteapp import codex_backend
from liteapp.analysis import parse_result
from liteapp.reading import parse_snapshot

FAKE = str(Path(__file__).with_name("fake_codex_app_server.py"))
ANALYSIS_JSON = json.dumps({
    "intent": "询问价格", "need": "报价", "action": "先确认规格",
    "caution": "上下文有限", "replies": ["请问需要什么规格？", "我先查一下报价。", "可以，稍后发您。"]},
    ensure_ascii=False)
SNAPSHOT_JSON = json.dumps({
    "title": "测试客户", "messages": [
        {"side": "me", "name": "", "text": "你好"},
        {"side": "other", "name": "", "text": "请问价格是多少？"}]}, ensure_ascii=False)


def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


class FakeCodexAppServerTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.log_path = os.path.join(self.folder.name, "protocol.jsonl")
        self.env = {
            "FAKE_CODEX_LOG": self.log_path,
            "FAKE_CODEX_REPLY": ANALYSIS_JSON,
            "FAKE_CODEX_IMAGE_REPLY": SNAPSHOT_JSON,
            "FAKE_CODEX_MODE": "",
        }
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server = None

    def _server(self):
        self.server = codex_backend.CodexAppServer(command=[sys.executable, FAKE], config={})
        self.server.start()
        self.addCleanup(self.server.close)
        return self.server

    def test_handshake_account_and_models_need_no_model_turn(self):
        server = self._server()
        account = server.account()
        self.assertEqual(account["account"]["planType"], "plus")
        models = server.models()
        self.assertEqual([item["id"] for item in models], ["fake-text", "fake-vision"])
        self.assertEqual(models[1]["input_modalities"], ["text", "image"])
        methods = [entry["received"].get("method") for entry in read_log(self.log_path)
                   if "received" in entry]
        self.assertIn("initialize", methods)
        self.assertNotIn("turn/start", methods)

    def test_text_turn_returns_parseable_json_and_readonly_settings(self):
        server = self._server()
        raw = server.run_turn("请分析这段合成对话", model="fake-text")
        self.assertEqual(parse_result(raw)["replies"][0], "请问需要什么规格？")
        entries = read_log(self.log_path)
        thread = next(entry["received"]["params"] for entry in entries
                      if entry.get("received", {}).get("method") == "thread/start")
        self.assertEqual(thread["sandbox"], "read-only")
        self.assertEqual(thread["approvalPolicy"], "never")
        self.assertIs(thread["ephemeral"], True)
        self.assertTrue(thread["baseInstructions"])
        turn = next(entry["turn_start"] for entry in entries if "turn_start" in entry)
        self.assertEqual(turn["input"], [{"type": "text", "text": "请分析这段合成对话"}])
        self.assertEqual(turn["model"], "fake-text")

    def test_image_turn_sends_data_url_and_parses_snapshot(self):
        server = self._server()
        raw = server.run_turn("请转写画面", image=b"\xff\xd8synthetic-jpeg")
        title, messages = parse_snapshot(raw)
        self.assertEqual(title, "测试客户")
        self.assertEqual(messages[0][0], "me")
        self.assertEqual(messages[1][2], "请问价格是多少？")
        entries = read_log(self.log_path)
        turn = next(entry["turn_start"] for entry in entries if "turn_start" in entry)
        image = turn["input"][1]
        self.assertEqual(image["type"], "image")
        self.assertTrue(image["url"].startswith("data:image/jpeg;base64,"))

    def test_failed_turn_reports_status(self):
        os.environ["FAKE_CODEX_MODE"] = "fail"
        server = self._server()
        with self.assertRaises(ValueError) as raised:
            server.run_turn("请分析")
        self.assertIn("failed", str(raised.exception))

    def test_server_tool_request_is_refused(self):
        os.environ["FAKE_CODEX_MODE"] = "server_request"
        server = self._server()
        raw = server.run_turn("请分析")
        self.assertEqual(parse_result(raw)["intent"], "询问价格")
        replies = [entry["received"] for entry in read_log(self.log_path)
                   if "received" in entry and entry["received"].get("id") == 900]
        self.assertTrue(replies, "client did not answer the server tool request")
        self.assertIn("error", replies[0])

    def test_timeout_interrupts_the_turn(self):
        os.environ["FAKE_CODEX_MODE"] = "hang"
        server = self._server()
        with self.assertRaises(ValueError) as raised:
            server.run_turn("请分析", timeout=0.6)
        self.assertIn("超时", str(raised.exception))
        methods = [entry["received"].get("method") for entry in read_log(self.log_path)
                   if "received" in entry]
        self.assertIn("turn/interrupt", methods)

    def test_missing_executable_is_reported_without_fallback(self):
        missing = os.path.join(self.folder.name, "missing-codex.exe")
        self.assertEqual(codex_backend.find_codex({"codex_path": missing}), "")
        server = codex_backend.CodexAppServer(config={"codex_path": missing})
        with self.assertRaises(ValueError):
            server.start()
        server.close()

    def test_shared_status_uses_model_catalog_without_turn(self):
        with patch.object(codex_backend, "find_codex", return_value=sys.executable), \
                patch.object(codex_backend, "_SHARED", {"server": None, "path": None}):
            self.server = codex_backend.CodexAppServer(command=[sys.executable, FAKE], config={})
            self.server.start()
            self.addCleanup(self.server.close)
            with patch.object(codex_backend, "_shared", return_value=self.server):
                info = codex_backend.status({})
        self.assertEqual(info["plan"], "plus")
        self.assertEqual(len(info["models"]), 2)


if __name__ == "__main__":
    unittest.main()
