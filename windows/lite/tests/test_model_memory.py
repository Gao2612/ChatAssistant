import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from liteapp import settings


class RememberModelTests(unittest.TestCase):
    def test_model_choice_is_persisted_without_touching_keys(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
            cfg["api_key"] = "sk-test-placeholder-key"
            settings.save(cfg, folder)
            settings.remember_model("chatgpt_model", "gpt-6.1-sol", folder)
            restored = settings.load(folder)
        self.assertEqual(restored.get("chatgpt_model"), "gpt-6.1-sol")
        self.assertEqual(restored.get("api_key"), "sk-test-placeholder-key")

    def test_chatgpt_choice_updates_active_model_for_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
            cfg.update(backend="chatgpt", model="gpt-6.1-sol",
                       chatgpt_model="gpt-6.1-sol")
            settings.save(cfg, folder)
            settings.remember_model("chatgpt_model", "gpt-6-astra", folder)
            restored = settings.load(folder)
        self.assertEqual(restored["chatgpt_model"], "gpt-6-astra")
        self.assertEqual(restored["model"], "gpt-6-astra")

    def test_unknown_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                settings.remember_model("protected_key", "x", folder)



class SettingsModelSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_chatgpt_catalog_selection_updates_config_and_emits(self):
        from liteapp.ui import SettingsPage
        config = {
            "wechat_path": "", "backend": "chatgpt", "base_url": "",
            "api_key": "", "model": "", "chatgpt_model": "",
            "subscription": {"email": "synthetic@example.test"},
            "subscription_models": [["GPT-6.1-Sol", "gpt-6.1-sol"],
                                    ["GPT-6-Astra", "gpt-6-astra"]],
            "style": "自然、简短", "auto_connect": False, "auto_analysis": False,
            "dock_wechat": False, "background_monitor": False,
            "auto_send_allowed": False, "reading_backend": "ocr",
            "reading_base_url": "", "reading_model": "", "reading_api_key": "",
            "codex_path": "", "api_provider": "deepseek",
            "subscription_models": [["GPT-6.1-Sol", "gpt-6.1-sol"],
                                    ["GPT-6-Astra", "gpt-6-astra"]],
        }
        page = SettingsPage(config)
        page.show()
        emitted = []
        page.model_remembered.connect(emitted.append)
        index = page.backend.findData("chatgpt")
        self.assertGreaterEqual(index, 0)
        page.backend.setCurrentIndex(index)
        page.set_models(config["subscription_models"])
        page.model.setCurrentIndex(1)
        self.app.processEvents()
        self.assertEqual(page.config.get("chatgpt_model"), "gpt-6-astra")
        self.assertIn("gpt-6-astra", emitted)
        page.close()


if __name__ == "__main__":
    unittest.main()
