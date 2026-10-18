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
from liteapp.history import HistoryStore
from liteapp.history_ui import DEFAULT_ACCOUNT_KEY, HistoryPage
from liteapp.import_jobs import ImportProgress


class HistoryPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_create_import_browse_and_search(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            store = HistoryStore(path / "memory.sqlite3", protect=lambda x: x[::-1], unprotect=lambda x: x[::-1])
            page = HistoryPage(store=store)
            person_id = page.create_person("示例客户", "账号 A")
            self.assertEqual(page.persons.currentData(), person_id)
            source = path / "chat.html"
            source.write_text("<html><body><p>客户：周五可以交货吗？</p><p>我：确认后回复。</p></body></html>", encoding="utf-8")
            page.import_path(source)
            self.assertIn("周五可以交货", page.records.toPlainText())
            self.assertIn("2", page.summary.text())
            page.query.setText("交货")
            page.search_records()
            self.assertIn("周五可以交货", page.records.toPlainText())
            self.assertNotIn("确认后回复", page.records.toPlainText())
            page.generate_report()
            self.assertIn("2 个文字片段", page.report.toPlainText())
            page.close()

    def test_explicit_history_profiles_isolate_contacts_and_preserve_legacy_bucket(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            legacy_scope = store.create_person("旧数据联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.refresh_accounts()
            legacy_index = page.account_profiles.findData(legacy_scope.wechat_account_id)
            self.assertGreaterEqual(legacy_index, 0)
            self.assertIn("旧/未区分", page.account_profiles.itemText(legacy_index))

            profile = page.create_account_profile("工作微信")
            page.account_profiles.setCurrentIndex(
                page.account_profiles.findData(profile.account_id))
            new_scope = page.create_person("同名客户")
            self.assertEqual(new_scope.wechat_account_id, profile.account_id)
            self.assertEqual(store.list_people_in_account(
                legacy_scope.wechat_account_id)[0].scope, legacy_scope)
            self.assertEqual(store.list_people_in_account(profile.account_id)[0].scope,
                             new_scope)

    def test_import_uses_profile_and_contact_frozen_before_file_picker(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            first = page.create_account_profile("工作微信")
            scope = store.create_person_in_account(first.account_id, "冻结联系人")
            page.refresh_people()
            page.set_current_scope(scope, "冻结联系人")
            second = page.create_account_profile("私人微信")
            captured = {}

            class FakeJobs:
                def start(self, selected_scope, path, ocr):
                    captured.update(scope=selected_scope, path=Path(path), ocr=ocr)
                    return "import-job"

                def cancel(self, _job_id):
                    return None

            page._jobs = FakeJobs()
            html = Path(folder) / "chat.html"
            html.write_text("<p>合成内容</p>", encoding="utf-8")
            page.start_import_path(
                html, captured_scope=scope,
                captured_account_id=first.account_id, captured_context=None)
            self.assertEqual(captured["scope"], scope)
            self.assertEqual(page.active_account_id(), second.account_id)
            self.assertEqual(page._import_job_contexts["import-job"][0], scope)
            page._import_timer.stop()

    def test_successful_import_returns_to_the_frozen_profile_and_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            imported_profile = page.create_account_profile("导入来源资料")
            scope = store.create_person_in_account(imported_profile.account_id, "导入联系人")
            other_profile = page.create_account_profile("当前选择资料")
            self.assertEqual(page.active_account_id(), other_profile.account_id)

            page._finish_import(ImportProgress(
                job_id="frozen-import", scope=scope, path_name="chat.html",
                status="succeeded", record_count=1, char_count=8),
                import_context=(scope, None, False))

            self.assertEqual(page.active_account_id(), imported_profile.account_id)
            self.assertEqual(page.current_scope(), scope)
            self.assertIn("导入联系人", page.contact_label.text())

    def test_same_name_profiles_can_be_selected_independently(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            first = store.create_person("合成同名联系人", DEFAULT_ACCOUNT_KEY)
            second = store.create_person("合成同名联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            offered = []

            def pick_second(title, prompt, items):
                offered.extend(items)
                return items[1], True

            page._ask_choice = pick_second
            people = store.list_people(DEFAULT_ACCOUNT_KEY)
            self.assertEqual({p.scope for p in people}, {first, second})
            selected = page.choose_other_person()
            self.assertEqual(selected, people[1].scope)
            self.assertEqual(page.current_scope(), people[1].scope)
            self.assertEqual(len(set(offered)), 2)

    def test_clearing_current_contact_clears_previous_report_and_statistics(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.create_person("合成历史联系人")
            source = Path(folder) / "synthetic.html"
            source.write_text("<p>synthetic private history</p>", encoding="utf-8")
            page.import_path(source)
            page.generate_report()
            self.assertTrue(page.report.toPlainText())
            self.assertIsNotNone(page.statistics_panel.metric_row("records"))
            page.set_current_scope(None)
            self.assertIsNone(page.current_scope())
            self.assertEqual(page.records.toPlainText(), "")
            self.assertEqual(page.report.toPlainText(), "")
            self.assertIsNone(page.statistics_panel.metric_row("records"))
            self.assertEqual(page.trend_panel.tree.topLevelItemCount(), 0)

    def test_unbound_live_chat_clears_selected_scope_and_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            old_scope = store.create_person("旧联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(old_scope, "旧联系人")

            page.set_live_conversation("当前微信标题", verified_scope=None)

            self.assertIsNone(page.current_scope())
            self.assertIn("当前微信标题", page.contact_label.text())
            self.assertIn("尚未关联", page.contact_label.text())

            confirmation = []

            def approve(title, prompt, default=""):
                confirmation.append((title, prompt, default))
                return default, True

            page._ask_text = approve
            source = Path(folder) / "current-chat.html"
            source.write_text("<p>当前会话的合成记录</p>", encoding="utf-8")
            result = page.import_path(source)

            self.assertIsNotNone(result)
            self.assertEqual(len(confirmation), 1)
            self.assertEqual(confirmation[0][2], "当前微信标题")
            new_scope = page.current_scope()
            self.assertNotEqual(new_scope, old_scope)
            self.assertEqual(store.stats(old_scope).source_count, 0)
            self.assertEqual(store.stats(new_scope).source_count, 1)

    def test_live_title_same_name_requires_manual_choice_and_can_create_new(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            existing_scope = store.create_person("同名候选", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(existing_scope, "同名候选")
            page.set_live_conversation("同名候选", verified_scope=None)

            self.assertIsNone(page.current_scope())
            calls = []
            offered = []

            def approve(title, prompt, default=""):
                calls.append((title, prompt, default))
                return default, True

            def choose_new(title, prompt, items):
                offered.extend(items)
                return "新建独立档案", True

            page._ask_text = approve
            page._ask_choice = choose_new
            created_scope = page.ensure_scope_for_import()

            # The live title is already a usable candidate: same-name users are
            # asked to associate/new/cancel directly, without retyping the name.
            self.assertEqual(calls, [])
            self.assertEqual(len(offered), 3)
            self.assertTrue(offered[0].startswith("关联已有档案：同名候选"))
            self.assertIn("新建独立档案", offered)
            self.assertIn("取消导入", offered)
            self.assertNotEqual(created_scope, existing_scope)
            self.assertEqual(len(store.list_people(DEFAULT_ACCOUNT_KEY)), 2)

    def test_same_name_import_can_associate_existing_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            existing = store.create_person("同名客户", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(None)
            page._ask_text = lambda *_args, **_kwargs: ("同名客户", True)
            offered = []

            def associate(_title, _prompt, items):
                offered.extend(items)
                return items[0], True

            page._ask_choice = associate
            scope, created_for_live = page._resolve_scope_for_import()

            self.assertEqual(scope, existing)
            self.assertFalse(created_for_live)
            self.assertEqual(page.current_scope(), existing)
            self.assertEqual(len(store.list_people(DEFAULT_ACCOUNT_KEY)), 1)
            self.assertTrue(offered[0].startswith("关联已有档案：同名客户"))

    def test_same_name_import_can_create_independent_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            existing = store.create_person("同名客户", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(None)
            page.set_live_conversation("同名客户")
            page._ask_text = lambda *_args, **_kwargs: ("同名客户", True)
            page._ask_choice = lambda *_args, **_kwargs: ("新建独立档案", True)
            scope, created_for_live = page._resolve_scope_for_import()

            self.assertNotEqual(scope, existing)
            self.assertTrue(created_for_live)
            self.assertEqual(len(store.list_people(DEFAULT_ACCOUNT_KEY)), 2)

    def test_same_name_import_can_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            existing = store.create_person("同名客户", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(None)
            page._ask_text = lambda *_args, **_kwargs: ("同名客户", True)
            page._ask_choice = lambda *_args, **_kwargs: ("取消导入", True)
            scope, created_for_live = page._resolve_scope_for_import()

            self.assertIsNone(scope)
            self.assertFalse(created_for_live)
            self.assertIn("取消", page.status.text())
            self.assertEqual(len(store.list_people(DEFAULT_ACCOUNT_KEY)), 1)
            self.assertNotEqual(page.current_scope(), existing)

    def test_import_completed_signal_and_local_base_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            scope = store.create_person("导入联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            captured = []
            page.import_completed.connect(captured.append)

            page._finish_import(ImportProgress(
                job_id="signal-import", scope=scope, path_name="chat.html",
                status="succeeded", record_count=1, char_count=8),
                import_context=(scope, None, False))

            self.assertEqual(captured, [scope])
            from liteapp.contact_profile import ContactProfileStore
            try:
                profile = ContactProfileStore(Path(folder)).get(
                    "导入联系人", scope.person_id)
            except OSError:
                self.skipTest("Windows DPAPI unavailable in this environment")
            self.assertIsNotNone(profile)
            self.assertIs(profile.model_analyzed, False)
            self.assertEqual(profile.relation, "普通联系人/待确认")

    def test_auto_profile_failure_only_shows_status_and_keeps_import(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            scope = store.create_person("失败联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            captured = []
            page.import_completed.connect(captured.append)
            with patch("liteapp.history_ui.build_local_profile",
                       side_effect=RuntimeError("synthetic profile failure")):
                page._finish_import(ImportProgress(
                    job_id="failed-profile", scope=scope, path_name="chat.html",
                    status="succeeded", record_count=1, char_count=8),
                    import_context=(scope, None, False))

            self.assertIn("自动生成基础档案失败", page.status.text())
            self.assertEqual(captured, [scope])

    def test_import_context_is_captured_before_file_dialog_and_forwarded_on_success(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_live_conversation("当前微信会话")
            context = object()
            captured = []
            succeeded = []
            page.set_import_context_callbacks(
                lambda: captured.append(context) or context,
                lambda scope, captured_context, created_for_live: succeeded.append(
                    (scope, captured_context, created_for_live)))
            page._ask_text = lambda *_args, **_kwargs: ("当前微信会话", True)
            fake_job_id = "synthetic-import"

            class Jobs:
                def start(self, scope, path, _enable_ocr):
                    self.scope = scope
                    self.path = path
                    return fake_job_id

            page._jobs = Jobs()
            source = Path(folder) / "conversation.html"
            dialog_observations = []

            def open_dialog(*_args):
                dialog_observations.append(tuple(captured))
                return str(source), "HTML"

            with patch("liteapp.history_ui.QFileDialog.getOpenFileName", open_dialog):
                page.choose_file()

            self.assertEqual(dialog_observations, [(context,)])
            scope = page.current_scope()
            self.assertEqual(page.active_import_job(), fake_job_id)
            self.assertEqual(succeeded, [])

            page._finish_import(ImportProgress(
                job_id=fake_job_id, scope=scope, path_name=source.name,
                status="succeeded", record_count=1, char_count=8))

            self.assertEqual(succeeded, [(scope, context, True)])

    def test_cancelled_first_import_does_not_report_success(self):
        page = HistoryPage(store=HistoryStore())
        self.addCleanup(page.close)
        context = object()
        captured = []
        succeeded = []
        page.set_import_context_callbacks(
            lambda: captured.append(context) or context,
            lambda *args: succeeded.append(args))
        page._ask_text = lambda *_args, **_kwargs: ("", False)

        self.assertIsNone(page.start_import_path("cancelled.html"))
        self.assertEqual(captured, [context])
        self.assertEqual(succeeded, [])
        self.assertIsNone(page.current_scope())

    def test_failed_import_does_not_report_success(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_live_conversation("当前微信会话")
            context = object()
            succeeded = []
            page.set_import_context_callbacks(
                lambda: context, lambda *args: succeeded.append(args))
            page._ask_text = lambda *_args, **_kwargs: ("当前微信会话", True)
            fake_job_id = "synthetic-failure"

            class Jobs:
                def start(self, scope, _path, _enable_ocr):
                    self.scope = scope
                    return fake_job_id

            page._jobs = Jobs()
            page.start_import_path(Path(folder) / "bad.html")
            scope = page.current_scope()

            page._finish_import(ImportProgress(
                job_id=fake_job_id, scope=scope, path_name="bad.html",
                status="failed", error="synthetic parse failure"))

            self.assertEqual(succeeded, [])

    def test_verified_live_scope_selects_bound_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            scope = store.create_person("已绑定联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)

            page.set_live_conversation("当前聊天标题", verified_scope=scope)

            self.assertEqual(page.current_scope(), scope)
            self.assertIn("已关联联系人档案", page.contact_label.text())

    def test_full_history_analysis_is_explicit_and_uses_selected_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            scope = store.create_person("合成客户", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(scope, "合成客户")
            starts = []
            progresses = []
            reports = []
            progress = SimpleNamespace(
                job_id="analysis-1", status="running", records_done=2,
                total_records=5, coverage=.4, message="处理第 2/5 条")
            report = SimpleNamespace(scope=scope, facts=(), inferences=(),
                                     open_questions=(), coverage=.8,
                                     source_version=1, memory_version=1)

            page.set_history_analysis_callbacks(
                lambda selected: starts.append(selected) or "analysis-1",
                lambda job_id: progresses.append(job_id) or progress,
                lambda job_id: reports.append(job_id) or report)

            page.analyze_history_button.click()
            self.assertEqual(starts, [scope])
            self.assertEqual(page.active_history_job(), "analysis-1")
            page._poll_history_analysis()
            self.assertEqual(progresses, ["analysis-1"])
            self.assertTrue(any("2/5" in text for text in page.task_list.job_texts()))
            self.assertFalse(reports)

            completed = SimpleNamespace(**{**vars(progress), "status": "completed"})
            progress_holder = [completed]
            page.set_history_analysis_callbacks(
                lambda selected: "analysis-1",
                lambda job_id: progress_holder[0],
                lambda job_id: reports.append(job_id) or report)
            page._poll_history_analysis()

            self.assertEqual(reports, ["analysis-1"])
            self.assertEqual(page.profile_panel._report, report)
            self.assertIn("80%", page.profile_panel.summary.text())
            self.assertIn("已完成", page.task_list.job_texts()[0])

    def test_full_history_analysis_errors_are_visible_and_foreign_reports_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            scope = store.create_person("合成客户", DEFAULT_ACCOUNT_KEY)
            foreign = store.create_person("另一个合成客户", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(scope, "合成客户")
            page.set_history_analysis_callbacks(
                lambda selected: (_ for _ in ()).throw(RuntimeError("synthetic failure")))

            page.analyze_history_button.click()
            self.assertIn("启动失败", page.history_analysis_status.text())
            self.assertIsNone(page.active_history_job())

            page.set_profile_report(SimpleNamespace(
                scope=foreign, facts=(), inferences=(), open_questions=()))
            self.assertIsNone(page.profile_panel._report)
            self.assertIn("其他联系人", page.history_analysis_status.text())

    def test_paused_history_analysis_can_resume_only_for_its_original_scope(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            scope = store.create_person("暂停恢复联系人", DEFAULT_ACCOUNT_KEY)
            other = store.create_person("另一联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(scope, "暂停恢复联系人")
            resumed = []
            page.set_history_analysis_callbacks(
                lambda selected: "paused-job",
                lambda _job: SimpleNamespace(
                    job_id="paused-job", status="paused", records_done=2,
                    total_records=5, coverage=.4, message="超额已暂停"),
                resume_callback=lambda job_id: resumed.append(job_id))

            page.start_history_analysis()
            page._poll_history_analysis()
            self.assertIsNone(page.active_history_job())
            self.assertEqual(page._paused_history_job, "paused-job")
            self.assertTrue(page.resume_history_button.isEnabled())

            page.set_current_scope(other, "另一联系人")
            self.assertFalse(page.resume_history_button.isEnabled())
            self.assertIsNone(page.resume_history_analysis())
            self.assertFalse(resumed)

            page.set_current_scope(scope, "暂停恢复联系人")
            self.assertEqual(page.resume_history_analysis(), "paused-job")
            self.assertEqual(resumed, ["paused-job"])
            self.assertEqual(page.active_history_job(), "paused-job")

    def test_paused_job_survives_starting_and_finishing_another_contact(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            scope_a = store.create_person("A联系人", DEFAULT_ACCOUNT_KEY)
            scope_b = store.create_person("B联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(scope_a, "A联系人")
            current = {"job": "job-a", "status": "paused"}
            resumed = []
            page.set_history_analysis_callbacks(
                lambda scope: "job-a" if scope == scope_a else "job-b",
                lambda job: SimpleNamespace(
                    job_id=job, status=current["status"], records_done=1,
                    total_records=2, coverage=.5, message="测试状态"),
                resume_callback=lambda job: resumed.append(job))

            page.start_history_analysis()
            page._poll_history_analysis()
            self.assertEqual(page._paused_job_for_scope(scope_a), "job-a")

            page.set_current_scope(scope_b, "B联系人")
            current.update(job="job-b", status="completed")
            page.start_history_analysis()
            page._poll_history_analysis()
            self.assertIsNone(page._paused_job_for_scope(scope_b))

            page.set_current_scope(scope_a, "A联系人")
            self.assertEqual(page._paused_job_for_scope(scope_a), "job-a")
            self.assertEqual(page.resume_history_analysis(), "job-a")
            self.assertEqual(resumed, ["job-a"])

    def test_cancel_observes_completion_race_and_keeps_completed_report(self):
        with tempfile.TemporaryDirectory() as folder:
            store = HistoryStore(Path(folder) / "memory.sqlite3",
                                 protect=lambda raw: raw, unprotect=lambda raw: raw)
            scope = store.create_person("竞态联系人", DEFAULT_ACCOUNT_KEY)
            page = HistoryPage(store=store)
            self.addCleanup(page.close)
            page.set_current_scope(scope, "竞态联系人")
            report = SimpleNamespace(scope=scope, facts=(), inferences=(),
                                     open_questions=(), coverage=1.0)
            page.set_history_analysis_callbacks(
                lambda _scope: "race-job",
                cancel_callback=lambda _job: SimpleNamespace(status="completed"),
                report_callback=lambda _job: report)
            page.start_history_analysis()

            self.assertTrue(page.cancel_history_analysis())
            self.assertEqual(page.profile_panel._report, report)
            self.assertIn("已完成", page.history_analysis_status.text())

    def test_history_analysis_requires_selected_contact_and_callback(self):
        page = HistoryPage(store=HistoryStore())
        self.addCleanup(page.close)

        self.assertIsNone(page.start_history_analysis())
        self.assertTrue(page.history_analysis_status.text())
        self.assertFalse(page.analyze_history_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
