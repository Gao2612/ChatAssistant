"""History page: current contact + imports, original text / profile / trend / statistics / tasks.

T5：HistoryStore 的公开 API 已收紧为 ScopeKey（不再接受 person_id 快捷入口）。
T6：导入走 liteapp.import_jobs.ImportJobs（后台线程 + 进度 + 取消）与 liteapp.importers。
T8：主流程改为“当前联系人 + 导入聊天记录”（账号选择移到设置，本页只保留隐藏的
账号绑定字段）；原文/档案/趋势/统计/任务五个标签；`open_evidence(ref)` 从档案/报告
引用跳到正确页与记录；首次导入确认档案关联时创建独立 UUID；次要入口“选择其他联系
人”保留独立离线查看/导入；长任务使用独立任务列表，不阻塞浏览。

T7 依赖：档案内容来自 T7 HistoryReport，经 `ProfilePanel.set_report`（duck-typed
适配，见 profile_ui）显示；任务进度通过 `set_job_progress` 接收 T7 JobProgress。
本文件不 import T7 模块，T7 落地后可原样接入。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .contracts import EvidenceRef, ScopeKey
from .history import HistoryImportError, HistoryStore
from .history_report import build_local_report
from .history_statistics import HistoryStatistics, StatisticsReport
from .import_jobs import ImportJobError, ImportJobs
from .profile_ui import (
    HistoryTaskList,
    ProfilePanel,
    StatisticsPanel,
    TrendPanel,
)

DEFAULT_ACCOUNT_KEY = "本机默认账号"


class HistoryPage(QWidget):
    PAGE_SIZE = 100

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        self._store = store
        self.offset = 0
        self._jobs = None
        self._active_import = None
        self._profile_report = None
        self._statistics_cache: dict[tuple, StatisticsReport] = {}
        self._statistics = HistoryStatistics(store) if store is not None else None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(8)
        title = QLabel("历史记录与本地记忆库")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("账号归属在设置中选择；这里显示当前联系人档案与本地原文。"))

        # 账号选择已移到设置：隐藏字段仅用于 T9/设置绑定，主流程不再要求填写。
        self.account = QLineEdit(DEFAULT_ACCOUNT_KEY)
        self.account.setVisible(False)
        self.account_hint = QLabel(
            "微信账号归属在设置中选择；首次导入只需确认联系人档案。")
        self.account_hint.setWordWrap(True)
        layout.addWidget(self.account_hint)

        contact_row = QHBoxLayout()
        self.contact_label = QLabel("当前联系人：未选择")
        self.contact_label.setWordWrap(True)
        contact_row.addWidget(self.contact_label, 1)
        self.import_button = QPushButton("导入聊天记录")
        self.import_button.clicked.connect(self.choose_file)
        contact_row.addWidget(self.import_button)
        self.cancel_import_button = QPushButton("取消导入")
        self.cancel_import_button.setEnabled(False)
        self.cancel_import_button.clicked.connect(self.cancel_active_import)
        contact_row.addWidget(self.cancel_import_button)
        self.other_person_button = QPushButton("选择其他联系人")
        self.other_person_button.clicked.connect(self.choose_other_person)
        contact_row.addWidget(self.other_person_button)
        refresh_button = QPushButton("刷新")
        refresh_button.clicked.connect(self.refresh_people)
        contact_row.addWidget(refresh_button)
        layout.addLayout(contact_row)

        # 隐藏的联系人选择器：T9/设置绑定按 ScopeKey 选择；主界面用当前联系人标签。
        self.persons = QComboBox(self)
        self.persons.setVisible(False)
        self.persons.currentIndexChanged.connect(self.load_page)

        ocr_row = QHBoxLayout()
        self.page_ocr = QCheckBox("扫描页本地 OCR（较慢，仅对无文字页）")
        ocr_row.addWidget(self.page_ocr)
        ocr_row.addStretch(1)
        layout.addLayout(ocr_row)
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_original_tab(), "原文")
        self.tabs.addTab(self._build_profile_tab(), "档案")
        self.tabs.addTab(self._build_trend_tab(), "趋势")
        self.tabs.addTab(self._build_statistics_tab(), "统计")
        self.tabs.addTab(self._build_tasks_tab(), "任务")
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        # Keep one bound-method object so closeEvent can really disconnect it; this
        # breaks the widget<->page reference cycle that otherwise crashes PySide6
        # at interpreter shutdown in offscreen tests.
        self._evidence_slot = self.open_evidence
        self.profile_panel.evidence_activated.connect(self._evidence_slot)
        self._import_timer = QTimer(self)
        self._import_timer.setInterval(200)
        self._import_timer.timeout.connect(self._poll_import)
        if self._store is not None:
            self.refresh_people()

    # ------------------------------------------------------------------ widgets
    def _build_original_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel("选择联系人后可查看导入范围和记录。")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        search_row = QHBoxLayout()
        self.query = QLineEdit()
        self.query.setPlaceholderText("搜索这个联系人的全部已导入文字")
        self.query.returnPressed.connect(self.search_records)
        search_row.addWidget(self.query, 1)
        search = QPushButton("搜索")
        search.clicked.connect(self.search_records)
        search_row.addWidget(search)
        layout.addLayout(search_row)
        self.records = QPlainTextEdit()
        self.records.setReadOnly(True)
        self.records.setPlaceholderText("导入 HTML/PDF 后，在这里浏览完整原文。")
        layout.addWidget(self.records, 1)
        pager = QHBoxLayout()
        self.previous_button = QPushButton("上一页")
        self.next_button = QPushButton("下一页")
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        self.page_label = QLabel("第 1 页")
        pager.addWidget(self.previous_button)
        pager.addWidget(self.next_button)
        pager.addWidget(self.page_label)
        pager.addStretch(1)
        layout.addLayout(pager)
        return page

    def _build_profile_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.profile_panel = ProfilePanel()
        layout.addWidget(self.profile_panel, 1)
        report_row = QHBoxLayout()
        self.report_button = QPushButton("生成/刷新本地整理报告")
        self.report_button.clicked.connect(self.generate_report)
        report_row.addWidget(self.report_button)
        report_row.addStretch(1)
        layout.addLayout(report_row)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setMaximumHeight(220)
        self.report.setPlaceholderText(
            "本地整理报告（确定性统计，不调用模型）；模型档案显示在上方。")
        layout.addWidget(self.report)
        return page

    def _build_trend_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.trend_refresh_button = QPushButton("刷新趋势")
        self.trend_refresh_button.clicked.connect(lambda: self.refresh_statistics(force=True))
        row.addWidget(self.trend_refresh_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.trend_panel = TrendPanel()
        layout.addWidget(self.trend_panel, 1)
        return page

    def _build_statistics_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.statistics_refresh_button = QPushButton("刷新统计")
        self.statistics_refresh_button.clicked.connect(
            lambda: self.refresh_statistics(force=True))
        row.addWidget(self.statistics_refresh_button)
        row.addStretch(1)
        layout.addLayout(row)
        self.statistics_panel = StatisticsPanel()
        layout.addWidget(self.statistics_panel, 1)
        return page

    def _build_tasks_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.task_list = HistoryTaskList()
        layout.addWidget(self.task_list, 1)
        return page

    # ------------------------------------------------------------------ plumbing
    @property
    def store(self):
        if self._store is None:
            self._store = HistoryStore()
        return self._store

    @property
    def statistics(self):
        if self._statistics is None:
            self._statistics = HistoryStatistics(self.store)
        return self._statistics

    @property
    def jobs(self):
        if self._jobs is None:
            self._jobs = ImportJobs(self.store)
        return self._jobs

    def current_scope(self):
        data = self.persons.currentData()
        return data if isinstance(data, ScopeKey) else None

    def _select_scope(self, scope):
        for index in range(self.persons.count()):
            if self.persons.itemData(index) == scope:
                self.persons.setCurrentIndex(index)
                return index
        return -1

    def _update_contact_label(self, display_name=None):
        scope = self.current_scope()
        name = display_name or (self.persons.currentText() if scope else "")
        if not name and scope is not None:
            name = scope.person_id[:8]
        self.contact_label.setText(f"当前联系人：{name or '未选择（请导入并确认档案）'}")

    def refresh_people(self):
        selected = self.persons.currentData()
        self.persons.blockSignals(True)
        self.persons.clear()
        for person in self.store.list_people(self.account.text().strip() or DEFAULT_ACCOUNT_KEY):
            self.persons.addItem(person.display_name, person.scope)
        self._select_scope(selected) if selected else None
        self.persons.blockSignals(False)
        self.offset = 0
        self._update_contact_label()
        self.load_page()

    def set_current_scope(self, scope, display_name=None):
        """T9/设置绑定入口：按 ScopeKey 选中当前联系人并刷新原文。"""
        if scope is None:
            self.persons.blockSignals(True)
            self.persons.setCurrentIndex(-1)
            self.persons.blockSignals(False)
            self._update_contact_label()
            self.load_page()
            return
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey 或 None。")
        self.persons.blockSignals(True)
        index = self._select_scope(scope)
        if index < 0:
            self.persons.addItem(display_name or scope.person_id[:8], scope)
            self.persons.setCurrentIndex(self.persons.count() - 1)
        self.persons.blockSignals(False)
        self._invalidate_derived_views(clear_profile=True)
        self.offset = 0
        self._update_contact_label(display_name)
        self.load_page()

    # ------------------------------------------------------------- import flow
    def ensure_scope_for_import(self):
        """First import: confirm the contact profile and create an independent UUID."""
        scope = self.current_scope()
        if scope is not None:
            return scope
        name, ok = self._ask_text(
            "确认档案关联",
            "首次导入：请确认这份记录所属联系人的档案名称。\n"
            "将创建独立 UUID 档案，同名联系人不会自动合并：",
            default="当前联系人")
        if not ok or not str(name).strip():
            self.status.setText("已取消导入：需要先确认档案关联。")
            return None
        try:
            return self.create_person(str(name).strip())
        except ValueError as error:
            self.status.setText(str(error))
            return None

    def _ask_text(self, title, prompt, default=""):
        """Small seam so tests can fake the confirmation dialog without patching Qt."""
        return QInputDialog.getText(self, title, prompt, text=default)

    def _ask_choice(self, title, prompt, items):
        return QInputDialog.getItem(self, title, prompt, items, 0, False)

    def choose_other_person(self):
        """Secondary entry: look at inherited/offline profiles without a live chat."""
        people = self.store.list_people(self.account.text().strip() or DEFAULT_ACCOUNT_KEY)
        if not people:
            self.status.setText("尚无其他联系人档案；请先导入记录并确认档案。")
            return None
        names = [person.display_name for person in people]
        name, ok = self._ask_choice(
            "选择其他联系人",
            "从既有档案中选择（次要入口：独立离线查看/导入，不与当前会话自动绑定）：",
            names)
        if not ok or not name:
            return None
        person = people[names.index(name)]
        self.set_current_scope(person.scope, person.display_name)
        self.status.setText(f"已切换到档案：{person.display_name}（独立离线查看/导入）。")
        return person.scope

    def create_person(self, display_name, account_key=None):
        if account_key is not None:
            self.account.setText(account_key)
        scope = self.store.create_person(
            display_name, self.account.text().strip() or DEFAULT_ACCOUNT_KEY)
        self.refresh_people()
        self._select_scope(scope)
        self._update_contact_label()
        self.offset = 0
        self.load_page()
        self._invalidate_derived_views(clear_profile=True)
        return scope

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入聊天记录", "",
                                              "聊天记录 (*.html *.htm *.pdf)")
        if path:
            self.start_import_path(path)

    # ------------------------------------------------------------- import jobs
    def active_import_job(self):
        return self._active_import

    def start_import_path(self, path):
        """Start a background import for the current contact; returns job id."""
        scope = self.current_scope()
        if scope is None:
            scope = self.ensure_scope_for_import()
        if scope is None:
            return None
        if self._active_import is not None:
            self.status.setText("已有导入任务在进行，请等待完成或取消。")
            return None
        try:
            job_id = self.jobs.start(scope, Path(path), self.page_ocr.isChecked())
        except (TypeError, ValueError) as error:
            self.status.setText(str(error))
            return None
        self._active_import = job_id
        self.import_button.setEnabled(False)
        self.cancel_import_button.setEnabled(True)
        self.progress_label.setText("正在识别文件格式…")
        self._import_timer.start()
        return job_id

    def cancel_active_import(self):
        if self._active_import is None:
            return
        try:
            self.jobs.cancel(self._active_import)
        except ImportJobError:
            pass
        self.cancel_import_button.setEnabled(False)
        self.progress_label.setText("正在取消导入（不会保存半条记录）…")

    def _progress_text(self, progress):
        parts = [f"导入中（{progress.phase}）"]
        if progress.format_kind:
            parts.append({"html": "HTML", "pdf": "PDF",
                          "exportwechat_html": "ExportWeChat 特征/通用回退"}.get(
                              progress.format_kind, progress.format_kind))
        if progress.page_count:
            parts.append(f"{progress.pages_done}/{progress.page_count} 页")
        parts.append(f"{progress.record_count} 个文字片段")
        if progress.failure_count:
            parts.append(f"{progress.failure_count} 页解析失败")
        return "；".join(parts)

    def _poll_import(self):
        job_id = self._active_import
        if job_id is None:
            self._import_timer.stop()
            return
        try:
            progress = self.jobs.progress(job_id)
        except ImportJobError:
            self._active_import = None
            self._import_timer.stop()
            self.import_button.setEnabled(True)
            self.cancel_import_button.setEnabled(False)
            self.status.setText("导入任务已丢失，请重新导入。")
            return
        if progress.finished:
            self._finish_import(progress)
        else:
            self.progress_label.setText(self._progress_text(progress))

    def _finish_import(self, progress):
        self._import_timer.stop()
        self._active_import = None
        self.import_button.setEnabled(True)
        self.cancel_import_button.setEnabled(False)
        if progress.status == "succeeded":
            self.query.clear()
            self.report.clear()
            self.offset = 0
            self.refresh_people()
            self._invalidate_derived_views(clear_profile=True)
            if progress.duplicate:
                self.progress_label.setText("该文件此前已导入，未新增记录。")
                self.status.setText("已存在相同文件，未重复导入。")
            else:
                self.progress_label.setText(
                    f"完成：{progress.record_count} 个文字片段，约 {progress.char_count} 字"
                    + (f"；{progress.failure_count} 页解析失败。" if progress.failure_count
                       else "。"))
                self.status.setText(
                    f"已导入 {progress.record_count} 个文字片段；内容仅保存在本机。")
        elif progress.status == "cancelled":
            self.progress_label.setText("已取消，未保存任何记录。")
            self.status.setText("导入已取消；历史库未写入半条记录。")
        else:
            self.progress_label.setText("")
            self.status.setText(f"导入失败：{progress.error or '未知错误'}")

    def import_path(self, path):
        """Synchronous import (tests / explicit small files); async path is choose_file."""
        scope = self.current_scope()
        if scope is None:
            scope = self.ensure_scope_for_import()
        if scope is None:
            return None
        try:
            progress = self.jobs.run_sync(scope, Path(path), self.page_ocr.isChecked())
        except (HistoryImportError, OSError, ValueError) as error:
            self.status.setText(str(error))
            return None
        self._finish_import(progress)
        return progress

    def closeEvent(self, event):
        if self._active_import is not None:
            try:
                self.jobs.cancel(self._active_import)
            except ImportJobError:
                pass
        self._import_timer.stop()
        # 断开信号里保存的 bound method，避免 widget 引用环在解释器退出时随机崩溃
        # （PySide6 offscreen 测试环境；正常关闭也更干净）。closeEvent 可能被调用
        # 多次（显式 close + 清理），第二次跳过即可。
        if self._evidence_slot is not None:
            try:
                self.profile_panel.evidence_activated.disconnect(self._evidence_slot)
            except (RuntimeError, TypeError):
                pass
            self._evidence_slot = None
        super().closeEvent(event)

    # ------------------------------------------------------------ original text
    def _set_records(self, rows):
        self.records.setPlainText("\n\n".join(
            f"[{row['source_name']} · {row['label']}]\n{row['text']}" for row in rows))

    def load_page(self, *_):
        scope = self.current_scope()
        if not scope:
            self.records.clear()
            self.summary.setText("当前联系人未选择。请导入聊天记录并确认档案关联，"
                                 "或从「选择其他联系人」进入既有档案。")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self._update_contact_label()
            self.profile_panel.set_scope(None)
            return
        totals = self.store.stats(scope)
        rows = self.store.list_messages(scope, offset=self.offset, limit=self.PAGE_SIZE)
        self._set_records(rows)
        self.summary.setText(
            f"当前联系人档案：已导入 {totals.source_count} 个文件，"
            f"{totals.record_count} 个文字片段，约 {totals.char_count} 字。"
            "来源与范围以导入文件为准；账号归属在设置中选择。")
        self.page_label.setText(f"第 {self.offset // self.PAGE_SIZE + 1} 页")
        self.previous_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(self.offset + self.PAGE_SIZE < totals.record_count)
        self._update_contact_label()
        self.profile_panel.set_scope(scope, self.persons.currentText())
        self._drop_profile_report_if_foreign(scope)

    def search_records(self):
        scope = self.current_scope()
        if not scope:
            return
        query = self.query.text().strip()
        if not query:
            self.offset = 0
            self.load_page()
            return
        rows = self.store.search(scope, query, limit=100)
        self._set_records(rows)
        self.status.setText(f"找到 {len(rows)} 条匹配（最多显示 100 条）。")
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)

    def previous_page(self):
        self.query.clear()
        self.offset = max(0, self.offset - self.PAGE_SIZE)
        self.load_page()

    def next_page(self):
        self.query.clear()
        self.offset += self.PAGE_SIZE
        self.load_page()

    # ------------------------------------------------------------------ evidence
    def open_evidence(self, ref):
        """Jump from a report/reference to the exact page/record and highlight it.

        Deleted evidence (source removed / scope changed / stale version) is
        refused; the page never re-opens a cached copy of deleted content.
        """
        if not isinstance(ref, EvidenceRef):
            raise TypeError("ref 必须是 EvidenceRef。")
        scope = self.current_scope()
        if scope is None:
            self.status.setText("尚未选择当前联系人，不能打开引用。")
            return False
        if ref.scope != scope:
            self.status.setText("引用属于其他联系人范围，已拒绝打开（同名联系人不自动合并）。")
            return False
        try:
            evidence = self.store.read_evidence(ref)
        except ValueError:
            self.status.setText("引用已失效：证据已被删除或已过期，不能打开旧缓存。")
            return False
        try:
            snapshot = self.store.snapshot(scope)
        except ValueError:
            self.status.setText("当前来源版本不可用，引用已失效。")
            return False
        record_ids = [item.record_id for item in snapshot.ranges]
        try:
            index = record_ids.index(ref.record_id)
        except ValueError:
            self.status.setText("引用记录不在当前来源版本中，已失效。")
            return False
        self.tabs.setCurrentIndex(0)
        self.offset = (index // self.PAGE_SIZE) * self.PAGE_SIZE
        self.load_page()
        rows = self.store.list_messages(scope, offset=self.offset, limit=self.PAGE_SIZE)
        row = next((item for item in rows if item.get("record_id") == ref.record_id), None)
        if row is None:
            self.status.setText("引用记录不在当前页数据中，已失效。")
            return False
        self._highlight_evidence(row["text"], evidence)
        self.status.setText(
            f"已定位到第 {index + 1} 条记录（第 {self.offset // self.PAGE_SIZE + 1} 页）。")
        return True

    def _highlight_evidence(self, row_text, evidence):
        page_text = self.records.toPlainText()
        position = page_text.find(row_text)
        if position < 0 and row_text:
            position = page_text.find(row_text[:40])
        if position < 0:
            return
        start = position + max(0, min(evidence.ref.start, len(row_text)))
        if evidence.ref.end > evidence.ref.start:
            end = position + max(0, min(evidence.ref.end, len(row_text)))
        else:
            end = position + len(row_text)
        cursor = self.records.textCursor()
        cursor.setPosition(start)
        cursor.setPosition(max(start, end), QTextCursor.KeepAnchor)
        self.records.setTextCursor(cursor)
        self.records.centerCursor()
        self.records.setFocus()

    # ----------------------------------------------------------- profile/report
    def set_profile_report(self, report):
        """Receive a T7 HistoryReport (duck-typed; None clears the profile tab)."""
        self._profile_report = report
        self.profile_panel.set_scope(self.current_scope(), self.persons.currentText())
        self.profile_panel.set_report(report)
        if report is not None:
            self.tabs.setCurrentIndex(1)

    def set_job_progress(self, progress):
        """Update the independent long-task list from a T7 JobProgress (duck-typed)."""
        self.task_list.upsert(progress)

    def _report_scope(self):
        report = self._profile_report
        if report is None:
            return None
        if isinstance(report, dict):
            return report.get("scope")
        return getattr(report, "scope", None)

    def _drop_profile_report_if_foreign(self, scope):
        report_scope = self._report_scope()
        if report_scope is not None and report_scope != scope:
            self._profile_report = None
            self.profile_panel.set_report(None)

    def _invalidate_derived_views(self, *, clear_profile=False):
        self._statistics_cache.clear()
        self.statistics_panel.set_statistics(None)
        self.trend_panel.set_statistics(None)
        if clear_profile:
            self._profile_report = None
            self.profile_panel.set_report(None)

    # -------------------------------------------------------------- statistics
    def _statistics_key(self, scope):
        totals = self.store.stats(scope)
        return (scope.wechat_account_id, scope.person_id,
                totals.source_version, totals.memory_version)

    def refresh_statistics(self, force=False):
        scope = self.current_scope()
        if scope is None:
            self.statistics_panel.set_statistics(None)
            self.trend_panel.set_statistics(None)
            return None
        key = self._statistics_key(scope)
        report = None if force else self._statistics_cache.get(key)
        if report is None:
            try:
                report = self.statistics.compute(scope, self.store.snapshot(scope))
            except ValueError as error:
                self.status.setText(f"本地统计失败：{error}")
                self.statistics_panel.set_statistics(None)
                self.trend_panel.set_statistics(None)
                return None
            self._statistics_cache[key] = report
            while len(self._statistics_cache) > 8:
                self._statistics_cache.pop(next(iter(self._statistics_cache)))
        self.statistics_panel.set_statistics(report)
        self.trend_panel.set_statistics(report)
        return report

    def generate_report(self):
        scope = self.current_scope()
        if not scope:
            self.status.setText("请先选择联系人并导入聊天记录。")
            return ""
        statistics = self.refresh_statistics(force=True)
        text = build_local_report(self.store, scope, statistics=statistics)
        self.report.setPlainText(text)
        self.tabs.setCurrentIndex(1)
        self.status.setText(
            "已整理全部已导入文字：本地统计为确定性事实；模型档案/趋势使用独立标签。")
        return text

    def _on_tab_changed(self, index):
        if index in (2, 3):
            self.refresh_statistics()
