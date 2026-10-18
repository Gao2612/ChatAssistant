"""Regression coverage for live WeChat context entering the history page."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtWidgets import QApplication
from liteapp.contracts import SourceKey
from liteapp.history import HistoryStore
from liteapp.history_ui import DEFAULT_ACCOUNT_KEY
from liteapp.import_jobs import ImportProgress
from liteapp.ui import MainWindow


class HistoryContextIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_show_history_uses_verified_current_binding_and_clears_unbound_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            window = MainWindow(config={"dock_wechat": False}, scanning=False)
            window.dock_timer.stop()
            window.poll.stop()
            window.scanner.stop()
            window.window_manager = None
            self.app.processEvents()
            window.history_page._store = store
            window.history_page._statistics = None
            first_scope = store.create_person("已绑定联系人", DEFAULT_ACCOUNT_KEY)
            unrelated_scope = store.create_person("上次打开联系人", DEFAULT_ACCOUNT_KEY)
            window.history_page.set_current_scope(unrelated_scope, "上次打开联系人")

            window.window_item = {"path": r"C:\\Program Files\\Tencent\\Weixin\\Weixin.exe",
                                  "pid": 1234, "created": 10.0, "hwnd": 5678}
            source = SourceKey.from_window_target(type("Target", (), {
                "path": window.window_item["path"], "pid": 1234,
                "created": 10.0, "__int__": lambda self: 5678})())
            window._epoch_title = "当前会话标题"
            window.state.connect("synthetic-session")
            window.state.set_manual("当前会话标题", "我：合成测试消息")
            window.bindings.bind(source, window.conversation_epoch, first_scope)

            window.show_history()
            self.assertEqual(window.history_page.current_scope(), first_scope)
            self.assertIn("已关联联系人档案", window.history_page.contact_label.text())

            window.bindings.revoke(source, window.conversation_epoch)
            window.show_history()
            self.assertIsNone(window.history_page.current_scope())
            self.assertIn("尚未关联", window.history_page.contact_label.text())
            self.app.processEvents()
            window.close()

    def test_first_confirmed_import_binds_only_the_captured_live_chat(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            window = MainWindow(config={"dock_wechat": False}, scanning=False)
            self.addCleanup(lambda: (window.close(), self.app.processEvents()))
            window.dock_timer.stop()
            window.poll.stop()
            window.scanner.stop()
            window.window_manager = None
            window.history_page._store = store
            window.window_item = {"path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
                                  "pid": 1234, "created": 10.0, "hwnd": 5678}
            window._epoch_title = "当前确认会话"
            window.state.connect("synthetic-session")
            window.state.update("当前确认会话", [("me", "", "合成测试消息")])
            scope = store.create_person("刚导入档案", DEFAULT_ACCOUNT_KEY)

            context = window._capture_history_import_context()
            self.assertIsNotNone(context)
            self.assertTrue(window._bind_imported_live_history(
                scope, context, created_for_live=True))
            self.assertEqual(window._bound_scope(), scope)
            self.assertIn("已把导入档案关联到当前会话", window.history_page.status.text())

            # A profile chosen from the offline list must never be auto-bound.
            other_scope = store.create_person("离线档案", DEFAULT_ACCOUNT_KEY)
            self.assertFalse(window._bind_imported_live_history(
                other_scope, context, created_for_live=False))
            self.assertEqual(window._bound_scope(), scope)

    def test_import_completion_after_chat_epoch_change_does_not_bind_new_chat(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            window = MainWindow(config={"dock_wechat": False}, scanning=False)
            self.addCleanup(window.close)
            window.dock_timer.stop()
            window.poll.stop()
            window.scanner.stop()
            window.window_manager = None
            window.history_page._store = store
            window.window_item = {"path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
                                  "pid": 1234, "created": 10.0, "hwnd": 5678}
            window._epoch_title = "导入时的会话"
            window.state.connect("synthetic-session")
            window.state.update("导入时的会话", [("me", "", "合成测试消息")])
            context = window._capture_history_import_context()
            scope = store.create_person("导入档案", DEFAULT_ACCOUNT_KEY)

            window.conversation_epoch += 1
            window._epoch_title = "后来切换的会话"
            self.assertFalse(window._bind_imported_live_history(
                scope, context, created_for_live=True))
            self.assertIsNone(window._bound_scope())
            self.assertIn("会话已变化", window.history_page.status.text())

    def test_import_does_not_bind_after_selected_history_profile_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            window = MainWindow(config={"dock_wechat": False}, scanning=False)
            self.addCleanup(window.close)
            window.dock_timer.stop()
            window.poll.stop()
            window.scanner.stop()
            window.window_manager = None
            window.history_page._store = store
            window.window_item = {"path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
                                  "pid": 1234, "created": 10.0, "hwnd": 5678}
            window._epoch_title = "导入时会话"
            window.state.connect("synthetic-session")
            window.state.update("导入时会话", [("me", "", "合成消息")])
            chosen = store.create_account("工作资料")
            other = store.create_account("私人资料")
            window.history_page.refresh_accounts(chosen.account_id)
            context = window._capture_history_import_context()
            scope = store.create_person_in_account(chosen.account_id, "导入档案")

            window.history_page.refresh_accounts(other.account_id)
            self.assertFalse(window._bind_imported_live_history(
                scope, context, created_for_live=True))
            self.assertIsNone(window._bound_scope())
            self.assertIn("资料已变化", window.history_page.status.text())

    def test_first_import_completion_does_not_bind_if_profile_changed_mid_job(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            window = MainWindow(config={"dock_wechat": False}, scanning=False)
            self.addCleanup(lambda: (window.close(), self.app.processEvents()))
            window.dock_timer.stop()
            window.poll.stop()
            window.scanner.stop()
            window.window_manager = None
            page = window.history_page
            page._store = store
            window.window_item = {"path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
                                  "pid": 1234, "created": 10.0, "hwnd": 5678}
            window._epoch_title = "首导入会话"
            window.state.connect("synthetic-session")
            window.state.update("首导入会话", [("me", "", "合成消息")])
            page.refresh_accounts()
            context = window._capture_history_import_context()
            account_id = page.active_account_id()
            page._ask_text = lambda *_args, **_kwargs: ("首导入联系人", True)

            class FakeJobs:
                def start(self, _scope, _path, _ocr):
                    return "first-import"

                def cancel(self, _job_id):
                    return None

            page._jobs = FakeJobs()
            html = Path(folder) / "synthetic.html"
            html.write_text("<p>synthetic</p>", encoding="utf-8")
            page.start_import_path(html, captured_context=context,
                                   captured_scope=None,
                                   captured_account_id=account_id)
            scope = page._import_job_contexts["first-import"][0]
            other = store.create_account("另一个资料")
            page.account_profiles.setCurrentIndex(
                page.account_profiles.findData(other.account_id))

            page._finish_import(ImportProgress(
                job_id="first-import", scope=scope, path_name=html.name,
                status="succeeded", record_count=1, char_count=9))

            self.assertIsNone(window._bound_scope())
            self.assertIn("资料发生变化", page.status.text())

    def test_import_binding_requires_live_confirmed_not_pending_or_manual_state(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            window = MainWindow(config={"dock_wechat": False}, scanning=False)
            self.addCleanup(window.close)
            window.dock_timer.stop()
            window.poll.stop()
            window.scanner.stop()
            window.window_manager = None
            window.history_page._store = store
            window.window_item = {"path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
                                  "pid": 1234, "created": 10.0, "hwnd": 5678}
            window._epoch_title = "已识别会话"
            window.state.connect("synthetic-session")
            window.state.update("已识别会话", [("me", "", "合成测试消息")])
            context = window._capture_history_import_context()
            scope = store.create_person("导入档案", DEFAULT_ACCOUNT_KEY)

            # A reconnect can reuse the same HWND/title but starts a new capture
            # session; a callback from the old session must not link to it.
            window.state.connect("new-synthetic-session")
            window.state.update("已识别会话", [("me", "", "合成测试消息")])
            self.assertFalse(window._bind_imported_live_history(
                scope, context, created_for_live=True))
            self.assertIsNone(window._bound_scope())

            # A newly observed frame invalidates readiness until OCR confirms it.
            window.state.mark_pending()
            self.assertFalse(window._bind_imported_live_history(
                scope, context, created_for_live=True))
            self.assertIsNone(window._bound_scope())

            # Manual text can be useful for analysis but must not identify a live chat.
            window.state.set_manual("已识别会话", "我：手工文本")
            self.assertIsNone(window._capture_history_import_context())

    def test_main_window_starts_history_job_with_registered_backend_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            window = MainWindow(config={"dock_wechat": False, "backend": "local",
                                        "local_model": "synthetic-model",
                                        "base_url": "http://127.0.0.1:11434/v1",
                                        "api_key": "synthetic-secret"}, scanning=False)
            self.addCleanup(window.close)
            window.dock_timer.stop()
            window.poll.stop()
            window.scanner.stop()
            window.window_manager = None
            self.app.processEvents()
            window.history_page._store = store
            scope = store.create_person("合成历史联系人", DEFAULT_ACCOUNT_KEY)
            window.history_page.set_current_scope(scope, "合成历史联系人")

            class Queue:
                def submit(self, *_args, **_kwargs):
                    raise AssertionError("fake HistoryJobs should not submit")

            window.inference_queue = Queue()
            captured = {}

            class Jobs:
                def __init__(self, _history, _analysis, _gateway, **kwargs):
                    captured["model_version"] = kwargs["model_version"]

                def start(self, selected_scope, profile_id):
                    captured.update(scope=selected_scope, profile_id=profile_id)
                    return "synthetic-job"

            with patch("liteapp.ui.AnalysisStore", return_value=object()), \
                    patch("liteapp.ui.HistoryJobs", Jobs), \
                    patch("liteapp.ui.backends.analysis_ready", return_value=True), \
                    patch("liteapp.ui.backends.effective_model", return_value="synthetic-model"), \
                    patch("liteapp.ui.backends.analysis_backend", return_value="local"):
                job_id = window.history_page.start_history_analysis()

            self.assertEqual(job_id, "synthetic-job")
            self.assertEqual(captured["scope"], scope)
            self.assertEqual(captured["model_version"], "local:synthetic-model")
            self.assertIn(captured["profile_id"], window._ui_gateway._history_profiles)
            self.assertNotIn("synthetic-secret", captured["profile_id"])

    def test_history_backend_snapshots_are_immutable_across_credential_changes(self):
        window = MainWindow(config={"dock_wechat": False, "backend": "local",
                                    "local_model": "synthetic-model",
                                    "base_url": "http://127.0.0.1:11434/v1",
                                    "api_key": "first-secret"}, scanning=False)
        self.addCleanup(window.close)
        window.dock_timer.stop()
        window.poll.stop()
        window.scanner.stop()
        window.window_manager = None
        gateway = window._ui_gateway

        first_id = gateway.register_history_profile(window.config)
        gateway.retain_history_profile(first_id, "first-job")
        gateway.retain_history_profile(first_id, "second-job")
        window.config["api_key"] = "second-secret"
        second_id = gateway.register_history_profile(window.config)

        self.assertNotEqual(first_id, second_id)
        self.assertEqual(gateway._history_profiles[first_id]["api_key"], "first-secret")
        self.assertEqual(gateway._history_profiles[second_id]["api_key"], "second-secret")
        self.assertNotIn("first-secret", first_id)
        self.assertNotIn("second-secret", second_id)
        self.assertEqual(gateway.register_history_profile(window.config), second_id)
        gateway.release_history_profile("first-job")
        self.assertIn(first_id, gateway._history_profiles)
        gateway.release_history_profile("second-job")
        self.assertNotIn(first_id, gateway._history_profiles)
        with self.assertRaisesRegex(ValueError, "原模型配置未恢复"):
            gateway.run(SimpleNamespace(profile_id="history:unknown"))

    def test_failed_history_job_start_discards_unreferenced_model_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "history.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            window = MainWindow(config={"dock_wechat": False, "backend": "local",
                                        "local_model": "synthetic-model",
                                        "api_key": "synthetic-secret"}, scanning=False)
            self.addCleanup(window.close)
            window.dock_timer.stop()
            window.poll.stop()
            window.scanner.stop()
            window.window_manager = None
            window.history_page._store = store
            scope = store.create_person("启动失败联系人", DEFAULT_ACCOUNT_KEY)

            class Queue:
                def submit(self, *_args, **_kwargs):
                    raise AssertionError("queue must not run")

            class BrokenJobs:
                def __init__(self, *_args, **_kwargs):
                    pass

                def start(self, *_args, **_kwargs):
                    raise ValueError("synthetic start failure")

            window.inference_queue = Queue()
            with patch("liteapp.ui.AnalysisStore", return_value=object()), \
                    patch("liteapp.ui.HistoryJobs", BrokenJobs), \
                    patch("liteapp.ui.backends.analysis_ready", return_value=True), \
                    patch("liteapp.ui.backends.effective_model", return_value="synthetic-model"), \
                    patch("liteapp.ui.backends.analysis_backend", return_value="local"):
                with self.assertRaisesRegex(ValueError, "synthetic start failure"):
                    window._start_history_analysis(scope)

            self.assertFalse(window._ui_gateway._history_profiles)

    def test_cancel_returns_persisted_terminal_state_for_stale_job(self):
        window = MainWindow(config={"dock_wechat": False}, scanning=False)
        self.addCleanup(window.close)
        window.dock_timer.stop()
        window.poll.stop()
        window.scanner.stop()
        window.window_manager = None
        cancelled = SimpleNamespace(status="cancelled")
        calls = []

        class Store:
            def get_job(self, job_id):
                calls.append(("get_job", job_id))
                return cancelled

        class Jobs:
            analysis_store = Store()

            def cancel(self, job_id):
                calls.append(("cancel", job_id))

            def progress(self, _job_id):
                raise AssertionError("progress applies stale-source validation")

        window._history_jobs = Jobs()
        self.assertIs(window._cancel_history_analysis("stale-job"), cancelled)
        self.assertEqual(calls, [("cancel", "stale-job"), ("get_job", "stale-job")])


if __name__ == "__main__":
    unittest.main()
