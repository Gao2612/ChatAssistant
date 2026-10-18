import os
import time
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from liteapp import account_store
from liteapp.ui import MainWindow

GPT_MODELS = [["GPT-6.1-Sol", "gpt-6.1-sol"], ["GPT-6-Astra", "gpt-6-astra"]]
CONFIG = {
    "wechat_path": "", "backend": "chatgpt", "base_url": "https://api.deepseek.com/v1",
    "model": "gpt-6.1-sol", "api_model": "deepseek-chat", "chatgpt_model": "gpt-6.1-sol",
    "local_model": "", "codex_model": "", "api_key": "", "api_provider": "custom",
    "subscription": {"access_token": "synthetic", "email": "person@example.test"},
    "subscription_models": GPT_MODELS, "registration_id": "reg-1",
    "active_registration_id": "reg-1",
    "registrations": [
        {"registration_id": "reg-1", "label": "主账号", "status": "connected",
         "model_catalog_version": 1, "revision": 1, "selected": True},
        {"registration_id": "reg-2", "label": "备用账号", "status": "login_only",
         "model_catalog_version": 0, "revision": 1, "selected": False},
    ],
    "selection": "reg-1", "_account_revision": 1, "style": "自然",
    "auto_connect": False, "auto_analysis": True,
    "reading_backend": "ocr", "reading_base_url": "", "reading_model": "",
    "reading_api_key": "", "codex_path": "", "host_id": "urn:uuid:test",
}


class FakeStore:
    def __init__(self):
        self.selected = None
        self.signed = []
        self.catalog = []

    def select(self, registration_id):
        self.selected = registration_id

    def sign_out(self, registration_id, cancel):
        self.signed.append(registration_id)
        return account_store.SignOutResult(registration_id=registration_id, revoked=True,
                                           local_only=False, message="已撤销该账号授权。")

    def update_model_catalog(self, registration_id, models):
        self.catalog.append((registration_id, [list(item) for item in models]))


class AccountsUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.store = FakeStore()
        self.window = MainWindow(config=dict(CONFIG), scanning=False,
                                 account_store=self.store)
        self.window.poll.stop()
        self.window.dock_timer.stop()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_registrations_are_listed_with_selected_account(self):
        combo = self.window.settings_page.account_combo
        self.assertEqual(combo.count(), 2)
        self.assertEqual(combo.currentData(), "reg-1")
        self.assertIn("主账号", combo.currentText())
        self.assertIn("备用账号", combo.itemText(1))

    def test_switch_account_selects_registration_and_refreshes(self):
        switched = dict(CONFIG, registration_id="reg-2", active_registration_id="reg-2",
                        subscription_models=[["Astra", "gpt-6-astra"]],
                        model="gpt-6-astra", chatgpt_model="gpt-6-astra",
                        registrations=[dict(CONFIG["registrations"][0], selected=False),
                                       dict(CONFIG["registrations"][1], selected=True)])
        with patch("liteapp.ui.settings.load", return_value=switched):
            self.window.switch_account("reg-2")
        self.assertEqual(self.store.selected, "reg-2")
        self.assertEqual(self.window.settings_page.account_combo.currentData(), "reg-2")
        self.assertIn("已切换", self.window.notice.text())
        self.assertEqual(self.window.config["active_registration_id"], "reg-2")

    def test_sign_out_uses_account_store(self):
        with patch("liteapp.ui.settings.load",
                   return_value=dict(CONFIG, subscription=None, subscription_models=[],
                                     registration_id=None, active_registration_id=None,
                                     registrations=[])):
            self.window.disconnect_chatgpt()
        self.assertEqual(self.store.signed, ["reg-1"])
        self.assertIsNone(self.window.config["subscription"])
        self.assertIn("撤销", self.window.notice.text())

    def test_refresh_models_updates_catalog_and_combo(self):
        models = [["GPT New", "gpt-new"], ["GPT Other", "gpt-other"]]
        with patch("liteapp.ui.settings.load", return_value=dict(CONFIG)), \
                patch("liteapp.ui.subscription.list_models", return_value=models):
            self.window.refresh_account_models()
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if (self.store.catalog
                        and self.window.settings_page.model.findData("gpt-new") >= 0):
                    break
                self.app.processEvents()
                time.sleep(0.01)
        self.assertEqual(self.store.catalog, [("reg-1", models)])
        self.assertGreaterEqual(self.window.settings_page.model.findData("gpt-new"), 0)


if __name__ == "__main__":
    unittest.main()
