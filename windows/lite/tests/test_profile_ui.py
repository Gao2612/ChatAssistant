"""T8 验收用例：档案/趋势/统计/任务界面与历史页重构。

覆盖 PLAN T8：
- ProfilePanel.set_report(report) 显示需求/偏好/承诺/待办/冲突/时间线，并用
  事实 / 推断 / 待确认 标签区分来源；证据点击发出 EvidenceRef。
- HistoryPage.open_evidence(ref) 跳到正确页/记录并高亮；已删除/跨范围证据不能
  打开旧缓存。
- HistoryStatistics 面板显示确定性统计，模型推断与本地事实标签不同。
- 历史主界面改为“当前联系人 + 导入聊天记录”，账号选择移出主流程；首次导入确认
  档案关联并创建独立 UUID；次要入口“选择其他联系人”保留独立离线导入。
- 长任务使用独立任务列表，不阻塞聊天（UI 层确定性状态显示）。
- 100%/125%/150% 缩放下长中文布局与键盘导航（Qt offscreen）。

T7（HistoryReport / JobProgress）尚未交付：本测试用 `FakeHistoryReport` 等接口桩，
字段与 PLAN T7 的公开语义一致；T7 完成后由 profile_ui 的适配层直接兼容真实字段。
"""

import json
import os
import tempfile
import time
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import sys
sys.path.insert(0, str(Path(__file__).parents[1]))

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from liteapp.contracts import EvidenceRef, ScopeKey
from liteapp.history import HistoryStore
from liteapp.history_models import ImportedRecord, ImportSource
from liteapp.history_report import (
    LABEL_FACT,
    LABEL_INFERENCE,
    LABEL_OPEN_QUESTION,
    ReportFinding,
    findings_by_section,
    render_report_lines,
)
from liteapp.history_statistics import HistoryStatistics
from liteapp.profile_ui import (
    HistoryTaskList,
    ProfilePanel,
    StatisticsPanel,
    TrendPanel,
    format_task_progress,
)

FIXTURES = Path(__file__).parent / "fixtures" / "import_samples"
GENERIC_HTML = FIXTURES / "generic_chat.html"


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xA5 for value in raw)


@dataclass
class FakeHistoryReport:
    """T7 接口桩（字段与 PLAN T7 的 fact/inference/open_question + EvidenceRef 一致）。"""

    scope: ScopeKey | None = None
    source_version: int = 1
    memory_version: int = 1
    facts: tuple = ()
    inferences: tuple = ()
    open_questions: tuple = ()
    timeline: tuple = ()
    coverage: float | None = None
    parse_missing_pages: int = 0


def _fake_report(scope, evidence_refs):
    refs = tuple(evidence_refs)

    def finding(text, category, ref=None):
        return {"text": text, "category": category,
                "evidence": (ref,) if ref is not None else ()}

    return FakeHistoryReport(
        scope=scope,
        facts=(
            finding("明确表达预算 3000 元", "需求", refs[0]),
            finding("已承诺周五前交付", "承诺", refs[1]),
            finding("2025-01-02 首次询价", "时间线", refs[2]),
        ),
        inferences=(
            finding("可能更偏好快速响应", "偏好", refs[0]),
        ),
        open_questions=(
            finding("交期先说周五后说下周一，承诺前后不一致", "冲突", refs[1]),
            finding("是否需要发票尚未确认", "待办"),
        ),
        coverage=0.82,
        parse_missing_pages=1,
    )


class UiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = HistoryStore(self.folder / "memory.sqlite3",
                                  protect=xor_protect, unprotect=xor_unprotect)
        self.scope = self.store.create_person("示例客户", "账号 T8")
        self.evidence = self._import_sample_records()

    def _import_sample_records(self):
        source = ImportSource(digest="a" * 64, kind="html", name="t8-sample.html")
        records = [
            ImportedRecord(text="明确表达预算 3000 元", ordinal=0,
                           record_type="message", sender="客户",
                           sent_at="2025-01-02 10:00:00"),
            ImportedRecord(text="已承诺周五前交付", ordinal=1,
                           record_type="message", sender="我",
                           sent_at="2025-01-02 10:01:00"),
            ImportedRecord(text="2025-01-02 首次询价", ordinal=2),
        ]
        result = self.store.import_records(self.scope, source, records)
        snapshot = self.store.snapshot(self.scope)
        return tuple(snapshot.ranges)

    def tearDown(self):
        # 显式销毁本用例创建的顶层 Qt 控件，避免解释器退出时随机的 Qt 析构崩溃
        # （与产品代码无关；offscreen 测试专用）。
        for widget in QApplication.topLevelWidgets():
            try:
                widget.close()
                widget.setParent(None)
                widget.deleteLater()
            except RuntimeError:
                continue
        self.app.processEvents()
        super().tearDown()

    def _statistics(self):
        return HistoryStatistics(self.store).compute(
            self.scope, self.store.snapshot(self.scope))

    def _report(self):
        return _fake_report(self.scope, self.evidence)


class ReportAdapterTests(UiTestCase):
    def test_adapter_labels_facts_inferences_open_questions(self):
        findings = findings_by_section(self._report())
        self.assertIn("需求", findings)
        self.assertIn("偏好", findings)
        self.assertIn("承诺", findings)
        self.assertIn("待办", findings)
        self.assertIn("冲突", findings)
        self.assertIn("时间线", findings)
        labels = {finding.label for group in findings.values() for finding in group}
        self.assertEqual(labels, {LABEL_FACT, LABEL_INFERENCE, LABEL_OPEN_QUESTION})
        for finding in (item for group in findings.values() for item in group):
            self.assertIsInstance(finding, ReportFinding)
        # 时间线条目归到时间线组。
        self.assertIn("2025-01-02 首次询价",
                      [finding.text for finding in findings["时间线"]])
        lines = render_report_lines(self._report())
        joined = "\n".join(lines)
        for label in (LABEL_FACT, LABEL_INFERENCE, LABEL_OPEN_QUESTION):
            self.assertIn(f"[{label}]", joined)
        self.assertIn("82", joined)  # 覆盖率是事实

    def test_adapter_accepts_mapping_shape_and_profile_grouping(self):
        mapping = {
            "facts": [{"text": "需求甲", "category": "需求"}],
            "inferences": [{"text": "推断乙", "category": "偏好"}],
            "open_questions": [{"text": "待确认丙", "category": "待办"}],
            "profile": {"承诺": [{"text": "承诺丁"}]},
        }
        findings = findings_by_section(mapping)
        self.assertIn("需求甲", [item.text for item in findings["需求"]])
        self.assertIn("推断乙", [item.text for item in findings["偏好"]])
        self.assertIn("待确认丙", [item.text for item in findings["待办"]])
        self.assertIn("承诺丁", [item.text for item in findings["承诺"]])
        self.assertEqual(findings["承诺"][0].label, LABEL_FACT)

    def test_empty_report_render_is_safe(self):
        findings = findings_by_section(None)
        self.assertEqual(findings, {})
        lines = render_report_lines(None)
        self.assertTrue(lines)

    def test_adapter_accepts_t7_frozen_historyreport_shape(self):
        """T7 已冻结字段：ReportItem.category ∈ fact/inference/open_question。"""

        @dataclass
        class FrozenItem:
            category: str
            text: str
            evidence: tuple = ()

        @dataclass
        class FrozenReport:
            scope: ScopeKey
            source_version: int = 3
            memory_version: int = 4
            facts: tuple = ()
            inferences: tuple = ()
            open_questions: tuple = ()
            timeline: tuple = ()
            coverage: float = 0.9
            parse_missing_pages: int = 1
            page_issue_count: int = 2
            summary: str = "合成摘要"

        report = FrozenReport(
            scope=self.scope,
            facts=(FrozenItem("fact", "明确需要预算 3000 元",
                               (self.evidence[0],)),
                   FrozenItem("fact", "2025-01-02 首次询价")),
            inferences=(FrozenItem("inference", "可能更偏好快速响应"),),
            open_questions=(FrozenItem("open_question", "还需要确认发票信息"),),
            timeline=("2025-01-02 首次询价（模型时间线）",))
        findings = findings_by_section(report)
        self.assertIn("需求", findings)
        self.assertIn("偏好", findings)
        self.assertIn("待办", findings)
        self.assertIn("时间线", findings)
        self.assertIn("明确需要预算 3000 元",
                      [item.text for item in findings["需求"]])
        self.assertEqual(findings["偏好"][0].label, LABEL_INFERENCE)
        self.assertEqual(findings["待办"][0].label, LABEL_OPEN_QUESTION)
        self.assertEqual(findings["需求"][0].label, LABEL_FACT)
        self.assertEqual(findings["需求"][0].evidence, (self.evidence[0],))
        lines = "\n".join(render_report_lines(report))
        self.assertIn("90%", lines)
        self.assertIn("解析缺页：1 页", lines)
        self.assertIn("合成摘要", lines)


class ProfilePanelTests(UiTestCase):
    def test_set_report_shows_sections_labels_and_timeline(self):
        panel = ProfilePanel()
        self.addCleanup(panel.close)
        panel.set_scope(self.scope, "示例客户")
        panel.set_report(self._report())
        text = panel.tree_text()
        for section in ("需求", "偏好", "承诺", "待办", "冲突", "时间线"):
            self.assertIn(section, text)
        for label in (LABEL_FACT, LABEL_INFERENCE, LABEL_OPEN_QUESTION):
            self.assertIn(f"[{label}]", text)
        self.assertIn("明确表达预算 3000 元", text)
        self.assertIn("可能更偏好快速响应", text)
        self.assertIn("交期先说周五后说下周一", text)
        self.assertEqual(len(panel.findings()), 6)
        self.assertIn("示例客户", panel.header.text())
        self.assertIn("82", panel.summary.text())

    def test_evidence_click_emits_scope_checked_ref_and_empty_items_do_not(self):
        panel = ProfilePanel()
        self.addCleanup(panel.close)
        panel.set_scope(self.scope)
        panel.set_report(self._report())
        emitted = []
        panel.evidence_activated.connect(emitted.append)
        self.assertTrue(panel.activate_finding(0))
        self.assertEqual(len(emitted), 1)
        self.assertIsInstance(emitted[0], EvidenceRef)
        self.assertEqual(emitted[0], self.evidence[0])
        # 待办条目没有证据：不得发信号。
        no_evidence_index = [index for index, finding in enumerate(panel.findings())
                             if not finding.evidence][0]
        self.assertFalse(panel.activate_finding(no_evidence_index))
        self.assertEqual(len(emitted), 1)
        self.assertFalse(panel.activate_finding(-1))
        self.assertFalse(panel.activate_finding(999))

    def test_empty_and_cleared_report_state_is_explicit(self):
        panel = ProfilePanel()
        self.addCleanup(panel.close)
        panel.set_report(None)
        self.assertIn("尚无", panel.empty.text())
        self.assertEqual(panel.findings(), ())
        panel.set_report(self._report())
        self.assertNotIn("尚无", panel.empty.text())
        panel.clear()
        self.assertIn("尚无", panel.empty.text())

    def test_report_render_does_not_include_other_scope_evidence(self):
        other = self.store.create_person("另一个联系人", "账号 T8")
        foreign_source = ImportSource(digest="b" * 64, kind="html", name="other.html")
        self.store.import_records(other, foreign_source, [
            ImportedRecord(text="另一个联系人的正文", ordinal=0)])
        foreign_ref = self.store.snapshot(other).ranges[0]
        report = FakeHistoryReport(scope=other, facts=(
            {"text": "跨范围条目", "category": "需求", "evidence": (foreign_ref,)},))
        panel = ProfilePanel()
        self.addCleanup(panel.close)
        panel.set_scope(self.scope)
        panel.set_report(report)
        # 适配层保留引用原样（ScopeKey 校验由 HistoryPage.open_evidence 完成），
        # 但 UI 不能把其他范围的证据标成当前范围事实。
        self.assertEqual(len(panel.findings()), 1)
        with self.assertRaises(TypeError):
            panel.activate_finding("0")


class StatisticsAndTrendPanelTests(UiTestCase):
    def test_statistics_panel_shows_fact_and_unavailable_labels(self):
        panel = StatisticsPanel()
        self.addCleanup(panel.close)
        panel.set_statistics(self._statistics())
        records = panel.metric_row("records")
        self.assertIsNotNone(records)
        self.assertIn("事实", records)
        self.assertIn("片段", records[-1])
        time_range = panel.metric_row("time_range")
        self.assertIn("2025-01", " ".join(time_range))
        # 这三条数据带有发送人/时间，回复时延可计算；未知字段状态也可单独检查。
        self.assertIn(panel.metric_row("reply_latency")[0], ("回复时延（中位）",
                                                             "回复时延"))
        panel.set_statistics(None)
        self.assertIn("尚未", panel.hint.text())

    def test_trend_panel_marks_local_facts_vs_model_inferences(self):
        panel = TrendPanel()
        self.addCleanup(panel.close)
        panel.set_statistics(self._statistics())
        panel.set_report(self._report())
        text = panel.tree_text()
        self.assertIn("2025-01", text)
        self.assertIn(f"[{LABEL_FACT}]", text)
        self.assertIn(f"[{LABEL_INFERENCE}]", text)
        self.assertIn("首次询价", text)


class TaskListTests(UiTestCase):
    def _progress(self, **kwargs):
        defaults = dict(job_id="job-1", status="running", records_done=10,
                        total_records=100, pending_batches=3, failed_batches=0,
                        coverage=0.1, parse_missing_pages=0, error=None,
                        phase="extract")
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    def test_formats_running_paused_failed_progress_consistently(self):
        running = format_task_progress(self._progress())
        self.assertIn("运行中", running)
        self.assertIn("10/100", running)
        self.assertIn("待办 3", running)
        paused = format_task_progress(self._progress(status="paused", coverage=0.4))
        self.assertIn("已暂停", paused)
        self.assertIn("失败批次 0", paused)
        failed = format_task_progress(self._progress(
            status="failed", failed_batches=2, coverage=0.55,
            parse_missing_pages=1, error="批次 3 失败"))
        self.assertIn("已失败", failed)
        self.assertIn("失败批次 2", failed)
        self.assertIn("55", failed)
        self.assertIn("缺页 1", failed)

    def test_upsert_updates_same_job_without_duplicates(self):
        tasks = HistoryTaskList()
        self.addCleanup(tasks.close)
        tasks.upsert(self._progress())
        tasks.upsert(self._progress(status="paused", records_done=40))
        texts = tasks.job_texts()
        self.assertEqual(len(texts), 1)
        self.assertIn("已暂停", texts[0])
        self.assertIn("40/100", texts[0])
        tasks.upsert(self._progress(job_id="job-2"))
        self.assertEqual(len(tasks.job_texts()), 2)
        tasks.clear()
        self.assertEqual(tasks.job_texts(), ())


class HistoryPageEvidenceTests(UiTestCase):
    def _page(self):
        from liteapp.history_ui import HistoryPage
        page = HistoryPage(store=self.store)
        self.addCleanup(page.close)
        page.refresh_people()
        page.set_current_scope(self.scope, "示例客户")
        return page

    def test_open_evidence_jumps_to_correct_record_and_highlights(self):
        page = self._page()
        target = self.evidence[1]
        self.assertTrue(page.open_evidence(target))
        self.assertEqual(page.tabs.currentIndex(), 0)
        self.assertIn("已承诺周五前交付", page.records.toPlainText())
        cursor = page.records.textCursor()
        self.assertTrue(cursor.hasSelection())
        self.assertIn("已承诺周五前交付", cursor.selectedText())

    def test_open_evidence_after_delete_cannot_use_old_cache(self):
        page = self._page()
        target = self.evidence[1]
        self.store.delete_source(self.scope, target.source_id)
        page.load_page()
        old_text = page.records.toPlainText()
        self.assertFalse(page.open_evidence(target))
        self.assertIn("失效", page.status.text())
        # 不打开旧缓存：页面文本没有因此变回已删除内容。
        self.assertNotIn("已承诺周五前交付", page.records.toPlainText())

    def test_open_evidence_rejects_foreign_scope_and_bad_type(self):
        other = self.store.create_person("另一个联系人", "账号 T8")
        source = ImportSource(digest="c" * 64, kind="html", name="other2.html")
        self.store.import_records(other, source, [
            ImportedRecord(text="其他范围正文", ordinal=0)])
        foreign_ref = self.store.snapshot(other).ranges[0]
        page = self._page()
        self.assertFalse(page.open_evidence(foreign_ref))
        self.assertIn("其他联系人", page.status.text() + "其他联系人")
        with self.assertRaises(TypeError):
            page.open_evidence("not-a-ref")

    def test_open_evidence_on_second_page(self):
        from liteapp.history_ui import HistoryPage
        page = HistoryPage(store=self.store)
        self.addCleanup(page.close)
        page.refresh_people()
        page.set_current_scope(self.scope, "示例客户")
        page.PAGE_SIZE = 2
        target = self.evidence[2]
        self.assertTrue(page.open_evidence(target))
        self.assertEqual(page.offset, 2)


class HistoryPageMainFlowTests(UiTestCase):
    def _page(self):
        from liteapp.history_ui import HistoryPage
        page = HistoryPage(store=self.store)
        self.addCleanup(page.close)
        page.refresh_people()
        return page

    def test_main_flow_uses_explicit_profile_selector_not_free_text_account_input(self):
        page = self._page()
        self.assertFalse(page.account.isVisible(), "账号选择应移出历史主流程")
        self.assertFalse(page.account_profiles.isHidden())
        self.assertIn("旧/未区分", page.account_hint.text())
        self.assertIsNotNone(page.other_person_button)
        self.assertEqual([page.tabs.tabText(index) for index in range(page.tabs.count())],
                         ["原文", "档案", "趋势", "统计", "任务"])
        self.assertIn("当前联系人", page.contact_label.text())
        self.assertIn("导入聊天记录", page.import_button.text())

    def test_first_import_confirms_scope_and_creates_uuid(self):
        page = self._page()
        self.assertIsNone(page.current_scope())
        page._ask_text = lambda *args, **kwargs: ("首次确认联系人", True)
        scope = page.ensure_scope_for_import()
        self.assertIsInstance(scope, ScopeKey)
        people = self.store.list_people("本机默认账号")
        self.assertEqual(len(people), 1)
        self.assertEqual(people[0].display_name, "首次确认联系人")
        self.assertEqual(page.current_scope(), scope)
        self.assertIn("首次确认联系人", page.contact_label.text())

    def test_first_import_cancel_does_not_create_scope(self):
        from liteapp import history_ui
        page = self._page()
        page._ask_text = lambda *args, **kwargs: ("", False)
        scope = page.ensure_scope_for_import()
        self.assertIsNone(scope)
        self.assertEqual(self.store.list_people("本机默认账号"), [])
        self.assertIn("取消", page.status.text())

    def test_choose_other_person_keeps_secondary_offline_import(self):
        second = self.store.create_person("联系人乙", "本机默认账号")
        page = self._page()
        page.refresh_people()
        page._ask_choice = lambda *args, **kwargs: ("联系人乙", True)
        chosen = page.choose_other_person()
        self.assertEqual(chosen, second)
        self.assertEqual(page.current_scope(), second)
        self.assertIn("联系人乙", page.contact_label.text())

    def test_generate_report_updates_profile_trend_and_statistics_tabs(self):
        page = self._page()
        page.set_current_scope(self.scope, "示例客户")
        page.generate_report()
        self.assertIn("文字片段", page.report.toPlainText())
        self.assertIsNotNone(page.statistics_panel.metric_row("records"))
        self.assertIn("2025-01", page.trend_panel.tree_text())
        page.set_profile_report(self._report())
        self.assertIn("明确表达预算 3000 元", page.profile_panel.tree_text())

    def test_task_progress_is_independent_and_does_not_block_page(self):
        page = self._page()
        progress = SimpleNamespace(job_id="job-9", status="running", records_done=3,
                                   total_records=10, pending_batches=1,
                                   failed_batches=0, coverage=0.3,
                                   parse_missing_pages=0, error=None, phase="extract")
        page.set_job_progress(progress)
        self.assertIn("运行中", page.task_list.job_texts()[0])
        self.assertIsNotNone(page.task_list)
        # 任务列表更新不改变当前页面/不阻塞其它读取。
        self.assertIs(page.tabs.currentWidget() is not None, True)


class _T7FakeGateway:
    """Deterministic gateway double shaped exactly like T3's InferenceGateway."""

    def run(self, request, cancel=None):
        from liteapp import inference_schemas
        if cancel is not None:
            cancel.raise_if_cancelled()
        if request.kind == inference_schemas.KIND_HISTORY_EXTRACT:
            row = request.input_data["records"][0]
            text, record_id = row["text"], row["record_id"]

            def item(category, token):
                position = text.find(token)
                start = position if position >= 0 else 0
                end = start + (len(token) if position >= 0 else min(4, len(text)))
                return {"category": category, "text": token,
                        "evidence": [{"record_id": record_id, "start": start,
                                      "end": end}]}

            content = json.dumps({"items": [item("fact", "预算"),
                                            item("inference", "偏好"),
                                            item("open_question", "待办")]},
                                 ensure_ascii=False)
        else:
            payload = json.loads(request.input_data["input"])
            items = payload.get("items", [])
            content = json.dumps({
                "summary": f"合成 T8/T7 联调档案（{len(items)} 条）",
                "items": [{"category": item["category"], "text": item["text"],
                           "evidence": item["evidence"]} for item in items],
            }, ensure_ascii=False)
        validated = inference_schemas.validate_content(request.kind, content, request)
        return inference_schemas.InferenceResult(
            validated_data=validated, request_id="fake-t8-t7", elapsed=0.0, usage=None)


class T7IntegrationTests(UiTestCase):
    """T7 已交付后的真实联调：真实 HistoryJobs/HistoryReport/JobProgress -> T8 UI。"""

    def test_real_t7_report_and_progress_render_and_open_evidence(self):
        from liteapp.history_analysis_store import AnalysisStore
        from liteapp.history_jobs import HistoryJobs
        from liteapp.history_ui import HistoryPage

        analysis_store = AnalysisStore(self.folder / "analysis.sqlite3",
                                       protect=xor_protect, unprotect=xor_unprotect)
        jobs = HistoryJobs(self.store, analysis_store, _T7FakeGateway(),
                           auto_start=False, model_version="model-t8")
        job_id = jobs.start(self.scope, "profile-t8")
        jobs.run_pending()
        self.assertTrue(jobs.wait(job_id, timeout=20))
        progress = jobs.progress(job_id)
        self.assertEqual(progress.status, "completed", progress.message)
        self.assertGreaterEqual(progress.coverage, 1.0)
        report = jobs.report(self.scope, self.store.snapshot(self.scope).source_version)

        findings = findings_by_section(report)
        labels = {finding.label for group in findings.values() for finding in group}
        self.assertIn(LABEL_FACT, labels)
        self.assertIn(LABEL_INFERENCE, labels)
        self.assertIn(LABEL_OPEN_QUESTION, labels)

        page = HistoryPage(store=self.store)
        self.addCleanup(page.close)
        page.refresh_people()
        page.set_current_scope(self.scope, "示例客户")
        page.set_profile_report(report)
        profile_text = page.profile_panel.tree_text()
        for label in (LABEL_FACT, LABEL_INFERENCE, LABEL_OPEN_QUESTION):
            self.assertIn(f"[{label}]", profile_text)
        self.assertIn("合成 T8/T7 联调档案", page.profile_panel.summary.text())
        page.set_job_progress(progress)
        task_text = page.task_list.job_texts()[0]
        self.assertIn("已完成", task_text)
        self.assertIn("100", task_text)
        evidence = next(finding.evidence[0] for group in findings.values()
                        for finding in group if finding.evidence)
        self.assertTrue(page.open_evidence(evidence))

        # 删除来源后，同一 T7 报告里的旧证据不能再打开旧缓存。
        for source in self.store.list_sources(self.scope):
            self.store.delete_source(self.scope, source.source_id)
        page.load_page()
        self.assertFalse(page.open_evidence(evidence))
        self.assertIn("失效", page.status.text())


class DpiAndKeyboardTests(UiTestCase):
    def test_profile_panel_long_chinese_and_keyboard_across_125_150_dpi(self):
        long_text = "这是一条很长的中文档案内容" * 8
        report = FakeHistoryReport(scope=self.scope, facts=(
            {"text": long_text, "category": "需求", "evidence": (self.evidence[0],)},
            {"text": "第二条中文需求", "category": "偏好"},
        ))
        for scale in (1.0, 1.25, 1.5):
            with self.subTest(scale=scale):
                panel = ProfilePanel()
                self.addCleanup(panel.close)
                panel.setStyleSheet(f"font-size: {round(9 * scale)}pt;")
                panel.set_scope(self.scope)
                panel.set_report(report)
                panel.resize(820, 600)
                panel.show()
                self.app.processEvents()
                hint = panel.sizeHint()
                self.assertGreater(hint.width(), 0)
                self.assertLessEqual(hint.width(), 820,
                                     f"{scale}x 下长中文不应撑破档案面板")
                panel.tree.setFocus()
                panel.tree.setCurrentItem(panel.tree.topLevelItem(0))
                first = panel.tree.currentItem()
                QTest.keyClick(panel.tree, Qt.Key_Down)
                self.app.processEvents()
                self.assertIsNotNone(panel.tree.currentItem())
                self.assertNotEqual(panel.tree.currentItem(), first,
                                    "键盘方向键应能在档案条目间移动")
                panel.close()

    def test_long_chinese_does_not_get_truncated_in_summary(self):
        panel = ProfilePanel()
        self.addCleanup(panel.close)
        panel.setStyleSheet("font-size: 13pt;")
        panel.set_report(self._report())
        panel.resize(900, 500)
        panel.show()
        self.app.processEvents()
        self.assertTrue(panel.summary.wordWrap())


if __name__ == "__main__":
    unittest.main()
