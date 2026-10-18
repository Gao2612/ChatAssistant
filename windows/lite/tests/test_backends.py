import json
import threading
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from liteapp import analysis, backends, codex_backend, reading, settings
from liteapp.state import ConversationState

ANALYSIS_JSON = json.dumps({
    "intent": "询问价格", "need": "报价", "action": "先确认规格", "caution": "信息有限",
    "replies": ["请问需要什么规格？", "我先查一下报价。", "可以，稍后发您。"]}, ensure_ascii=False)
SNAPSHOT_JSON = json.dumps({
    "title": "测试客户", "messages": [
        {"side": "me", "name": "", "text": "你好"},
        {"side": "other", "name": "", "text": "请问价格是多少？"}]}, ensure_ascii=False)


class FakeServer:
    def __init__(self, content):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append({
                    "path": self.path,
                    "headers": dict(self.headers),
                    "body": json.loads(raw.decode("utf-8")),
                })
                payload = json.dumps({"choices": [{"message": {"content": content}}]},
                                     ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class BackendSettingsTests(unittest.TestCase):
    def test_new_install_defaults_are_local_ocr_and_chatgpt_analysis(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
        self.assertEqual(cfg["reading_backend"], "ocr")
        self.assertEqual(cfg["backend"], "chatgpt")
        self.assertEqual(cfg["reading_api_key"], "")

    def test_reading_key_is_encrypted_and_roundtrips_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
            cfg.update(reading_backend="api", reading_model="vision-model",
                       reading_base_url="https://vision.example.test/v1",
                       reading_api_key="synthetic-vision-secret",
                       backend="local", model="local-chat",
                       base_url="http://127.0.0.1:11434/v1", api_key="")
            settings.save(cfg, folder)
            raw = (Path(folder) / "settings.json").read_text(encoding="utf-8")
            restored = settings.load(folder)
        self.assertNotIn("synthetic-vision-secret", raw)
        self.assertEqual(restored["reading_api_key"], "synthetic-vision-secret")
        self.assertEqual(restored["reading_model"], "vision-model")
        self.assertEqual(restored["backend"], "local")
        self.assertEqual(restored["model"], "local-chat")
        self.assertEqual(restored["api_key"], "")


class ReadinessTests(unittest.TestCase):
    def test_reading_ready_matrix(self):
        self.assertTrue(backends.reading_ready({"reading_backend": "ocr"}))
        vision = {"reading_backend": "api", "reading_base_url": "https://vision.test/v1",
                  "reading_model": "vision", "reading_api_key": "k"}
        self.assertTrue(backends.reading_ready(vision))
        self.assertFalse(backends.reading_ready(dict(vision, reading_api_key="")))
        self.assertFalse(backends.reading_ready(dict(vision, reading_model="")))
        with patch.object(codex_backend, "available", return_value=True):
            self.assertFalse(backends.reading_ready({"reading_backend": "codex"}))
            self.assertTrue(backends.reading_ready({"reading_backend": "codex",
                                                    "reading_model": "fake-vision"}))
        with patch.object(codex_backend, "available", return_value=False):
            self.assertFalse(backends.reading_ready({"reading_backend": "codex",
                                                     "reading_model": "fake-vision"}))

    def test_analysis_ready_matrix(self):
        self.assertFalse(backends.analysis_ready({"backend": "chatgpt"}))
        self.assertTrue(backends.analysis_ready({
            "backend": "chatgpt", "subscription": {"access_token": "x"},
            "model": "account-model", "subscription_models": [["Account", "account-model"]]}))
        self.assertFalse(backends.analysis_ready({
            "backend": "api", "model": "m", "api_key": "", "base_url": "https://x.test/v1"}))
        self.assertTrue(backends.analysis_ready({
            "backend": "api", "model": "m", "api_key": "k", "base_url": "https://x.test/v1"}))
        self.assertTrue(backends.analysis_ready({"backend": "local", "model": "local-chat"}))
        with patch.object(codex_backend, "available", return_value=True):
            self.assertTrue(backends.analysis_ready({"backend": "codex"}))


class RoutingTests(unittest.TestCase):
    def test_analysis_dispatch_selects_one_backend(self):
        text = "合成对话"
        cases = [
            ("chatgpt", "subscription.analyze"),
            ("api", "analysis.analyze"),
            ("local", "analysis.analyze"),
            ("codex", "codex_backend.analyze"),
        ]
        for backend, target in cases:
            with self.subTest(backend=backend), \
                    patch.object(backends.subscription, "analyze", return_value="chatgpt"), \
                    patch.object(backends.analysis, "analyze", return_value="compatible"), \
                    patch.object(backends.codex_backend, "analyze", return_value="codex"):
                result = backends.analyze({"backend": backend}, text)
            self.assertIn(result, ("chatgpt", "compatible", "codex"))

    def test_reading_dispatch_and_validation(self):
        with patch.object(backends.reading, "read_chat", return_value=("t", [("me", "", "x")])):
            self.assertEqual(backends.read_chat({"reading_backend": "api"}, b"img")[0], "t")
        with self.assertRaises(ValueError):
            backends.read_chat({"reading_backend": "ocr"}, b"img")
        with self.assertRaises(ValueError):
            backends.validate_reading({"reading_backend": "api", "reading_base_url": "",
                                       "reading_model": "", "reading_api_key": ""})
        with self.assertRaises(ValueError):
            backends.validate_reading({"reading_backend": "api",
                                       "reading_base_url": "https://vision.test/v1",
                                       "reading_model": "vision", "reading_api_key": ""})
        backends.validate_reading({"reading_backend": "ocr"})
        with self.assertRaises(ValueError):
            backends.validate_analysis({"backend": "api", "model": "m", "api_key": "",
                                        "base_url": "https://x.test/v1"})
        backends.validate_analysis({"backend": "local", "model": "local-chat", "base_url": ""})
        with self.assertRaises(ValueError):
            backends.validate_analysis({"backend": "chatgpt", "model": "",
                                        "subscription": None, "subscription_models": []})


class AnalysisBackendTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeServer(ANALYSIS_JSON)
        self.addCleanup(self.fake.close)

    def test_local_backend_uses_compatible_endpoint_without_key(self):
        result = analysis.analyze({
            "backend": "local", "base_url": self.fake.base_url,
            "model": "local-chat", "api_key": "", "style": "自然", "relationship": "客户"},
            "对方：请问价格？")
        self.assertEqual(result["replies"][0], "请问需要什么规格？")
        request = self.fake.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertNotIn("Authorization", request["headers"])
        self.assertEqual(request["body"]["model"], "local-chat")

    def test_api_backend_requires_key(self):
        with self.assertRaises(ValueError):
            analysis.analyze({"backend": "api", "base_url": self.fake.base_url,
                              "model": "m", "api_key": ""}, "合成对话")

    def test_remote_plain_http_is_rejected(self):
        with self.assertRaises(ValueError):
            analysis.endpoint("http://example.com/v1")


class ReadingBackendTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeServer(SNAPSHOT_JSON)
        self.addCleanup(self.fake.close)

    def test_vision_reading_sends_image_and_parses_snapshot(self):
        title, messages = reading.vision_read({
            "reading_backend": "api", "reading_base_url": self.fake.base_url,
            "reading_model": "vision-model", "reading_api_key": "synthetic-vision-secret",
        }, b"\xff\xd8synthetic", "测试客户")
        self.assertEqual(title, "测试客户")
        self.assertEqual(messages[1], ("other", "", "请问价格是多少？"))
        request = self.fake.requests[0]
        self.assertEqual(request["path"], "/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer synthetic-vision-secret")
        user_content = request["body"]["messages"][1]["content"]
        image = next(item for item in user_content if item["type"] == "image_url")
        self.assertTrue(image["image_url"]["url"].startswith("data:image/jpeg;base64,"))

    def test_vision_reading_reports_invalid_model_output(self):
        broken = FakeServer("这不是 JSON")
        self.addCleanup(broken.close)
        with self.assertRaises(ValueError):
            reading.vision_read({"reading_backend": "api", "reading_base_url": broken.base_url,
                                 "reading_model": "vision", "reading_api_key": "k"}, b"img")

    def test_parse_snapshot_validates_and_normalizes(self):
        title, messages = reading.parse_snapshot("```json\n" + SNAPSHOT_JSON + "\n```")
        self.assertEqual((title, len(messages)), ("测试客户", 2))
        for value in ("", "not json", '{"title":"x","messages":[]}',
                      '{"title":"x","messages":[{"text":""}]}', "[]"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                reading.parse_snapshot(value)


class StateAndEncodeTests(unittest.TestCase):
    def test_remote_snapshot_messages_feed_conversation_state(self):
        state = ConversationState()
        state.connect("remote")
        state.update("测试客户", [("me", "", "你好"), ("other", "", "请问价格是多少？")])
        self.assertEqual(state.text, "我：你好\n对方：请问价格是多少？")
        self.assertTrue(state.ready)

    def test_reading_crop_is_jpeg_and_width_limited(self):
        import numpy as np
        from io import BytesIO
        from PIL import Image
        from liteapp.worker import encode_reading
        image = np.zeros((200, 3000, 3), dtype=np.uint8)
        blob = encode_reading(image)
        self.assertEqual(blob[:2], b"\xff\xd8")
        with Image.open(BytesIO(blob)) as decoded:
            self.assertLessEqual(decoded.width, 1400)
            self.assertLessEqual(decoded.height, 200)


if __name__ == "__main__":
    unittest.main()
