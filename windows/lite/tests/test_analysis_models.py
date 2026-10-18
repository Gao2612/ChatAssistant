import json
import os
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp import analysis, backends, settings
from liteapp.ui import MainWindow, SettingsPage

MODELS_JSON = {"data": [
    {"id": "vendor-chat", "display_name": "Vendor Chat"},
    {"id": "vendor-reasoner", "name": "reasoner"},
    {"id": "vendor-chat", "display_name": "Duplicate"},
    {"bad": True},
]}


class FakeModelsServer:
    def __init__(self, payload=None, status=200):
        self.requests = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                outer.requests.append({"path": self.path,
                                       "headers": dict(self.headers)})
                body = json.dumps(payload if payload is not None else MODELS_JSON,
                                  ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ModelListTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeModelsServer()
        self.addCleanup(self.fake.close)

    def test_models_endpoint_normalizes_base_and_full_chat_url(self):
        self.assertEqual(analysis.models_endpoint("https://api.example.com/v1/"),
                         "https://api.example.com/v1/models")
        self.assertEqual(analysis.models_endpoint("https://api.example.com/v1/chat/completions"),
                         "https://api.example.com/v1/models")
        for value in ("http://example.com/v1", "file:///secret", "https://u:p@example.com/v1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                analysis.models_endpoint(value)

    def test_list_models_reads_models_and_sends_bearer(self):
        models = analysis.list_models({"backend": "api", "base_url": self.fake.base_url,
                                       "api_key": "synthetic-key"})
        self.assertEqual(models, [("Vendor Chat", "vendor-chat"), ("reasoner", "vendor-reasoner")])
        request = self.fake.requests[0]
        self.assertEqual(request["path"], "/v1/models")
        self.assertEqual(request["headers"]["Authorization"], "Bearer synthetic-key")

    def test_list_models_requires_key_for_remote_api(self):
        with self.assertRaises(ValueError):
            analysis.list_models({"backend": "api", "base_url": self.fake.base_url,
                                  "api_key": ""})

    def test_list_models_local_allows_empty_key(self):
        models = analysis.list_models({"backend": "local", "base_url": self.fake.base_url,
                                       "api_key": ""})
        self.assertEqual(models[0][1], "vendor-chat")
        self.assertNotIn("Authorization", self.fake.requests[0]["headers"])

    def test_list_models_reports_bad_payload_and_http_errors(self):
        broken = FakeModelsServer(payload={"error": "no"})
        self.addCleanup(broken.close)
        with self.assertRaises(ValueError):
            analysis.list_models({"backend": "local", "base_url": broken.base_url, "api_key": ""})
        denied = FakeModelsServer(payload={"error": "denied"}, status=401)
        self.addCleanup(denied.close)
        with self.assertRaises(ValueError) as raised:
            analysis.list_models({"backend": "local", "base_url": denied.base_url, "api_key": ""})
        self.assertIn("401", str(raised.exception))


GPT_MODELS = [["GPT-6.1-Sol", "gpt-6.1-sol"], ["GPT-6-Astra", "gpt-6-astra"]]


def subscription_config(**overrides):
    config = {
        "backend": "chatgpt", "subscription": {"access_token": "synthetic",
                                               "refresh_token": "synthetic-refresh",
                                               "email": "person@example.test",
                                               "planType": "plus"},
        "subscription_models": GPT_MODELS,
        "model": "gpt-6.1-sol", "chatgpt_model": "gpt-6.1-sol",
        "api_model": "vendor-chat", "local_model": "local-chat", "codex_model": "gpt-6-sol",
        "base_url": "https://api.deepseek.com/v1", "api_key": "synthetic",
        "wechat_path": "", "auto_connect": False, "auto_analysis": True,
        "reading_backend": "ocr", "reading_base_url": "", "reading_model": "",
        "reading_api_key": "", "codex_path": "", "style": "自然", "host_id": "urn:uuid:x",
    }
    config.update(overrides)
    return config


class BackendMemoryTests(unittest.TestCase):
    def test_codex_reading_requires_explicit_model_selection(self):
        config = subscription_config(reading_backend="codex", reading_model="",
                                     codex_path="codex")
        with patch("liteapp.backends.codex_backend.find_codex", return_value="codex"):
            with self.assertRaisesRegex(ValueError, "读取模型"):
                backends.validate_reading(config)
        with patch("liteapp.reading.codex_backend.available", return_value=True):
            self.assertFalse(backends.reading_ready(config))

    def test_each_backend_remembers_its_own_model(self):
        config = subscription_config(backend="api")
        self.assertEqual(backends.remembered_model(config, "api"), "vendor-chat")
        self.assertEqual(backends.remembered_model(config, "chatgpt"), "gpt-6.1-sol")
        self.assertEqual(backends.remembered_model(config, "local"), "local-chat")
        self.assertEqual(backends.remembered_model(config, "codex"), "gpt-6-sol")

    def test_effective_model_recovers_saved_chatgpt_choice(self):
        # The generic field can still contain a different valid ChatGPT model after
        # a dropdown choice was remembered without pressing Save.
        config = subscription_config(model="gpt-6.1-sol", chatgpt_model="gpt-6-astra")
        self.assertEqual(backends.effective_model(config), "gpt-6-astra")
        self.assertTrue(backends.analysis_ready(config))
        self.assertIn("ChatGPT / gpt-6-astra", backends.describe_analysis(config))

    def test_effective_model_falls_back_to_first_catalog_entry(self):
        config = subscription_config(model="", chatgpt_model="")
        self.assertEqual(backends.effective_model(config), "gpt-6.1-sol")
        self.assertTrue(backends.analysis_ready(config))

    def test_new_settings_fields_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
            cfg.update(backend="local", model="local-chat", local_model="local-chat",
                       api_provider="ollama", chatgpt_model="gpt-6.1-sol",
                       codex_model="gpt-6-sol")
            settings.save(cfg, folder)
            restored = settings.load(folder)
        self.assertEqual(restored["local_model"], "local-chat")
        self.assertEqual(restored["chatgpt_model"], "gpt-6.1-sol")
        self.assertEqual(restored["codex_model"], "gpt-6-sol")
        self.assertEqual(restored["api_provider"], "ollama")


class SettingsPageBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_provider_preset_fills_base_url(self):
        page = SettingsPage(subscription_config(backend="api"), None)
        page.backend.setCurrentIndex(page.backend.findData("api"))
        page.provider.setCurrentIndex(page.provider.findData("openrouter"))
        self.assertEqual(page.base.text(), "https://openrouter.ai/api/v1")
        page.close()

    def test_switching_back_to_chatgpt_restores_remembered_model(self):
        page = SettingsPage(subscription_config(backend="api", model="vendor-chat"), None)
        page.backend.setCurrentIndex(page.backend.findData("chatgpt"))
        self.app.processEvents()
        self.assertEqual(page.model.currentData(), "gpt-6.1-sol")
        self.assertTrue(page.chatgpt_state.text().startswith("已连接 ChatGPT"))
        page.close()

    def test_chatgpt_model_selection_updates_effective_model_before_save(self):
        page = SettingsPage(subscription_config(backend="chatgpt"), None)
        page.set_models(GPT_MODELS)
        page.model.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(page.config["chatgpt_model"], "gpt-6-astra")
        self.assertEqual(page.config["model"], "gpt-6-astra")
        self.assertEqual(backends.effective_model(page.config), "gpt-6-astra")
        self.assertIn("gpt-6-astra", page.effective_label.text())
        page.close()

    def test_stale_chatgpt_model_is_not_silently_replaced(self):
        page = SettingsPage(subscription_config(
            backend="chatgpt", chatgpt_model="removed-model"), None)
        page.set_models(GPT_MODELS)
        self.assertEqual(page.model.currentIndex(), -1)
        self.assertEqual(backends.effective_model(page.config), "")
        page.close()

    def test_commit_saves_provider_and_per_backend_model(self):
        page = SettingsPage(subscription_config(backend="api"), None)
        page.provider.setCurrentIndex(page.provider.findData("openrouter"))
        page.model.setEditText("vendor-new")
        with patch("liteapp.ui.settings.save") as save:
            page.commit()
        page.close()
        self.assertEqual(page.error.text(), "")
        saved = save.call_args[0][0]
        self.assertEqual(saved["backend"], "api")
        self.assertEqual(saved["api_model"], "vendor-new")
        self.assertEqual(saved["model"], "vendor-new")
        self.assertEqual(saved["api_provider"], "openrouter")

    def test_models_fetched_populate_dropdown(self):
        page = SettingsPage(subscription_config(backend="api"), None)
        page.backend.setCurrentIndex(page.backend.findData("api"))
        with patch("liteapp.ui.analysis.list_models",
                   return_value=[("Vendor Chat", "vendor-chat"), ("Reasoner", "vendor-reasoner")]):
            page.fetch_models()
            deadline = time.monotonic() + 2
            while page.model.count() < 2 and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(0.01)
        self.assertEqual(page.model.count(), 2)
        self.assertIn("已读取 2 个模型", page.model_status.text())
        page.close()


class MainWindowBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(config=subscription_config(), scanning=False)
        self.window.poll.stop()
        self.window.dock_timer.stop()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_header_shows_effective_backend_and_model_after_restart(self):
        self.assertIn("ChatGPT / gpt-6.1-sol", self.window.effective_label.text())
        self.assertTrue(self.window.backend_ready())

    def test_immediate_chatgpt_choice_updates_effective_request_model(self):
        with patch("liteapp.ui.settings.remember_model") as remember:
            self.window.remember_chatgpt_model("gpt-6-astra")
        self.assertEqual(self.window.config["model"], "gpt-6-astra")
        self.assertEqual(backends.effective_model(self.window.config), "gpt-6-astra")
        remember.assert_called_once_with("chatgpt_model", "gpt-6-astra")

    def test_restart_with_api_model_field_still_uses_remembered_gpt_model(self):
        config = subscription_config(backend="chatgpt", model="vendor-chat",
                                     chatgpt_model="gpt-6-astra")
        window = MainWindow(config=config, scanning=False)
        window.poll.stop()
        window.dock_timer.stop()
        try:
            self.assertEqual(backends.effective_model(window.config), "gpt-6-astra")
            self.assertTrue(window.backend_ready())
            self.assertIn("gpt-6-astra", window.effective_label.text())
        finally:
            window.close()
            self.app.processEvents()

    def test_analysis_uses_effective_chatgpt_model(self):
        from liteapp.inference_schemas import InferenceResult

        class Handle:
            def __init__(self, request):
                self.request = request
                self.callback = None
                self.cancelled = False
                self.error = None
                self.result = None

            def set_callback(self, callback):
                self.callback = callback

            def cancel(self):
                self.cancelled = True

        class Queue:
            def __init__(self):
                self.requests = []
                self.last = None

            def submit(self, request, scope_id=None):
                self.requests.append(request)
                self.last = Handle(request)
                return self.last

            def stop(self):
                pass

            def resume(self):
                pass

        config = subscription_config(backend="chatgpt", model="vendor-chat",
                                     chatgpt_model="gpt-6-astra")
        self.window.config = config
        self.window.state.connect("source")
        self.window.state.update("客户", [("her", "", "你好")])
        self.window.set_editor(self.window.state.text)
        self.window.remote_reading = False
        self.window.reading_ok_token = None
        self.window.changed_at = 0
        queue = Queue()
        self.window.inference_queue.stop()
        self.window.inference_queue = queue
        self.window.start_analysis()
        self.assertEqual(len(queue.requests), 1)
        request = queue.requests[0]
        self.assertEqual(request.version_key, "chatgpt:gpt-6-astra")
        self.assertIn("instructions", request.input_data)
        result = {"intent": "x", "need": "y", "action": "z", "caution": "",
                  "replies": ["a", "b", "c"]}
        handle = queue.last
        handle.result = InferenceResult(validated_data=dict(result),
                                        request_id="req-1", elapsed=0.1)
        handle.callback(handle)
        self.assertFalse(self.window.busy)
        self.assertIn("x", self.window.summary.text())

    def test_disconnect_chatgpt_clears_binding_but_keeps_other_backends(self):
        with patch("liteapp.ui.settings.save"):
            self.window.disconnect_chatgpt()
        self.assertIsNone(self.window.config["subscription"])
        self.assertEqual(self.window.config["subscription_models"], [])
        self.assertFalse(self.window.backend_ready())
        self.assertIn("未授权", self.window.effective_label.text())
        self.assertIn("已断开", self.window.notice.text())


if __name__ == "__main__":
    unittest.main()
