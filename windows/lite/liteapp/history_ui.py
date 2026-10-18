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
from types import SimpleNamespace

from PySide6.QtCore import Signal, QTimer
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
from .auto_profile import build_local_profile
from .contact_profile import normalize_title
from .history import HistoryImportError, HistoryStore
from .history_report import build_local_report
from .history_statistics import HistoryStatistics, StatisticsReport
from .import_jobs import ImportJobError, ImportJobs
from .profile_ui import (
    HistoryTaskList,
    ProfilePanel,
    StatisticsPanel,
    TrendPanel,
    format_task_progress,
)

DEFAULT_ACCOUNT_KEY = "本机默认账号"
_UNSET_IMPORT_CONTEXT = object()
_UNSET_IMPORT_SELECTION = object()


class HistoryPage(QWidget):
    PAGE_SIZE = 100
    bind_current_requested = Signal()
    # Emitted once per successful import with its ScopeKey; MainWindow uses it to
    # start the model-backed per-contact profile. The signal never carries chat
    # text, only the immutable scope.
    import_completed = Signal(object)

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        self._store = store
        self.offset = 0
        self._jobs = None
        self._active_import = None
        self._profile_selection_revision = 0
        self._import_context_callbacks = (None, None)
        self._import_job_contexts = {}
        self._history_analysis_start = None
        self._history_analysis_resume = None
        self._history_analysis_cancel = None
        self._history_analysis_terminal = None
        self._history_analysis_progress = None
        self._history_analysis_report = None
        self._active_history_job = None
        self._paused_history_job = None
        self._history_job_scope = None
        self._history_job_scopes = {}
        self._paused_history_jobs = {}
        self._profile_report = None
        self._live_conversation_title = ""
        self._statistics_cache: dict[tuple, StatisticsReport] = {}
        self._statistics = HistoryStatistics(store) if store is not None else None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(8)
        title = QLabel("历史记录与本地记忆库")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel(
            "选择本地微信资料和联系人后，可导入该联系人完整聊天记录；资料名称由你维护，不会自动识别微信登录账号。"))

        # Keep the hidden label field for compatibility with older callers.
        self.account = QLineEdit(DEFAULT_ACCOUNT_KEY)
        self.account.setVisible(False)
        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("微信资料"))
        self.account_profiles = QComboBox(self)
        self.account_profiles.setObjectName("historyAccountProfiles")
        self.account_profiles.currentIndexChanged.connect(self._account_profile_changed)
        account_row.addWidget(self.account_profiles, 1)
        self.add_account_button = QPushButton("新建资料")
        self.add_account_button.clicked.connect(self.add_account_profile)
        account_row.addWidget(self.add_account_button)
        layout.addLayout(account_row)
        self.account_hint = QLabel(
            "旧/未区分资料保留已有记录；新建资料后，导入内容只会进入当前选中的资料。")
        self.account_hint.setWordWrap(True)
        layout.addWidget(self.account_hint)

        contact_row = QHBoxLayout()
        self.contact_label = QLabel("当前联系人：未选择")
        self.contact_label.setWordWrap(True)
        contact_row.addWidget(self.contact_label, 1)
        self.import_button = QPushButton("导入聊天记录")
        self.import_button.clicked.connect(self.choose_file)
        contact_row.addWidget(self.import_button)
        self.bind_current_button = QPushButton("关联当前微信会话")
        self.bind_current_button.setToolTip(
            "把当前微信窗口里的会话绑定到这个联系人；切换会话或窗口重启后需要重新关联。")
        self.bind_current_button.clicked.connect(self.bind_current_requested.emit)
        contact_row.addWidget(self.bind_current_button)
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
        self._history_analysis_timer = QTimer(self)
        self._history_analysis_timer.setInterval(500)
        self._history_analysis_timer.timeout.connect(self._poll_history_analysis)
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
        self.analyze_history_button = QPushButton("开始全量历史分析")
        self.analyze_history_button.setToolTip(
            "将当前联系人已导入的完整历史交给配置的分析后端；不会发送微信消息。")
        self.analyze_history_button.clicked.connect(self.start_history_analysis)
        report_row.addWidget(self.analyze_history_button)
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
        self.history_analysis_status = QLabel(
            "全量历史任务进度与失败状态显示在这里；开始分析请前往“档案”标签。")
        self.history_analysis_status.setWordWrap(True)
        layout.addWidget(self.history_analysis_status)
        self.resume_history_button = QPushButton("继续/重试已暂停分析")
        self.resume_history_button.setEnabled(False)
        self.resume_history_button.clicked.connect(self.resume_history_analysis)
        layout.addWidget(self.resume_history_button)
        self.cancel_history_button = QPushButton("取消当前分析")
        self.cancel_history_button.setEnabled(False)
        self.cancel_history_button.clicked.connect(self.cancel_history_analysis)
        layout.addWidget(self.cancel_history_button)
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

    def active_account_id(self):
        value = self.account_profiles.currentData()
        return value if isinstance(value, str) and value else None

    def refresh_accounts(self, selected_account_id=None):
        """Populate stable local account IDs while preserving the legacy bucket."""
        current = (selected_account_id or self.account_profiles.currentData()
                   or self.store.ensure_legacy_account(DEFAULT_ACCOUNT_KEY).account_id)
        self.store.ensure_legacy_account(DEFAULT_ACCOUNT_KEY)
        accounts = self.store.list_accounts()
        self.account_profiles.blockSignals(True)
        self.account_profiles.clear()
        for account in accounts:
            label = account.label
            if account.legacy:
                label += "（旧/未区分）"
            self.account_profiles.addItem(label, account.account_id)
        index = self.account_profiles.findData(current)
        if index < 0:
            legacy = next((i for i, item in enumerate(accounts) if item.legacy), 0)
            index = legacy
        self.account_profiles.setCurrentIndex(index)
        self.account_profiles.blockSignals(False)
        selected = self.account_profiles.currentData()
        account = next((item for item in accounts if item.account_id == selected), None)
        if account is not None:
            self.account.setText(account.label)
        return accounts

    def create_account_profile(self, label):
        """Create and select one explicitly named local history partition."""
        profile = self.store.create_account(label)
        self.refresh_accounts(profile.account_id)
        self._account_profile_changed()
        return profile

    def add_account_profile(self):
        label, ok = self._ask_text("新建微信资料", "输入一个便于区分的本地资料名称：")
        if not ok or not str(label).strip():
            return None
        try:
            profile = self.create_account_profile(str(label).strip())
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            return None
        self.status.setText(
            f"已新建资料“{profile.label}”。请确认当前会话确实属于该资料后再导入。")
        return profile

    def _account_profile_changed(self, *_args):
        if self.active_account_id() is None:
            return
        self._profile_selection_revision += 1
        selected = self.account_profiles.currentText()
        self.account.setText(selected.removesuffix("（旧/未区分）"))
        if hasattr(self, "persons"):
            self.persons.blockSignals(True)
            self.persons.setCurrentIndex(-1)
            self.persons.blockSignals(False)
            self._invalidate_derived_views(clear_profile=True)
            self.offset = 0
            self.refresh_people()

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
        if hasattr(self, "analyze_history_button"):
            self.analyze_history_button.setEnabled(
                self.current_scope() is not None
                and self._history_analysis_start is not None
                and self._active_history_job is None)
            if hasattr(self, "resume_history_button"):
                can_resume = (self._paused_job_for_scope(self.current_scope()) is not None
                              and self._history_analysis_resume is not None)
                self.resume_history_button.setEnabled(can_resume)
            if hasattr(self, "cancel_history_button"):
                self.cancel_history_button.setEnabled(
                    (self._active_history_job is not None
                     or self._paused_job_for_scope(self.current_scope()) is not None)
                    and self._history_analysis_cancel is not None)

    def _paused_job_for_scope(self, scope):
        return next((job_id for job_id, job_scope in reversed(
            tuple(self._paused_history_jobs.items())) if job_scope == scope), None)

    def refresh_people(self):
        selected = self.persons.currentData()
        if self.active_account_id() is None:
            self.refresh_accounts()
        self.persons.blockSignals(True)
        self.persons.clear()
        for person in self.store.list_people_in_account(self.active_account_id()):
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
            self._invalidate_derived_views(clear_profile=True)
            self.offset = 0
            self._update_contact_label()
            self.load_page()
            return
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey 或 None。")
        if self.active_account_id() != scope.wechat_account_id:
            self.refresh_accounts(scope.wechat_account_id)
            self.refresh_people()
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

    def set_live_conversation(self, title, verified_scope=None):
        """Set live chat context without ever resolving identity from its title.

        Only a caller-provided verified ScopeKey can select an existing profile.
        An unbound conversation clears any prior hidden selection and keeps its
        title solely as the default offered in the explicit first-import dialog.
        """
        title = str(title or "").strip()
        self._live_conversation_title = title
        if verified_scope is not None:
            if not isinstance(verified_scope, ScopeKey):
                raise TypeError("verified_scope 必须是已验证的 ScopeKey 或 None。")
            self.set_current_scope(verified_scope, title or None)
            if title:
                self.contact_label.setText(f"当前微信会话：{title}（已关联联系人档案）")
            return

        # Clearing the hidden selector is essential: an unbound live chat must
        # not inherit a contact previously chosen by the offline history flow.
        self.set_current_scope(None)
        if title:
            self.contact_label.setText(
                f"当前微信会话：{title}（尚未关联联系人档案；导入时需确认）")

    def set_import_context_callbacks(self, capture_context, import_succeeded):
        """Install opaque live-context capture and successful-import hooks.

        HistoryPage never interprets the captured value or derives identity from
        the displayed conversation title. The caller owns all validation.
        """
        if capture_context is not None and not callable(capture_context):
            raise TypeError("capture_context 必须是可调用对象或 None。")
        if import_succeeded is not None and not callable(import_succeeded):
            raise TypeError("import_succeeded 必须是可调用对象或 None。")
        self._import_context_callbacks = (capture_context, import_succeeded)

    def _capture_import_context(self):
        capture_context, _ = self._import_context_callbacks
        return capture_context() if capture_context is not None else None

    def _notify_import_succeeded(self, scope, context, created_for_live):
        _, callback = self._import_context_callbacks
        if callback is None:
            return
        try:
            callback(scope, context, created_for_live)
        except Exception as error:
            self.status.setText(f"导入已成功，但关联当前会话失败：{error}")

    # ------------------------------------------------------------- import flow
    def ensure_scope_for_import(self):
        """First import: confirm the contact profile and create an independent UUID."""
        return self._resolve_scope_for_import()[0]

    def _same_name_people(self, account_id, display_name):
        """同 account 下按 NFKC/空白规范化后的同名联系人（不按标题认身份）。"""
        key = normalize_title(display_name)
        if not key:
            return []
        try:
            people = self.store.list_people_in_account(account_id)
        except (AttributeError, TypeError, ValueError):
            return []
        return [person for person in people
                if normalize_title(getattr(person, "display_name", "")) == key]

    def _same_name_items(self, matches):
        return [f"关联已有档案：{person.display_name}"
                f"（{person.scope.person_id}）" for person in matches] + [
                    "新建独立档案", "取消导入"]

    def _match_same_name_choice(self, choice, matches):
        """把 _ask_choice 的返回项映射回已有档案（兼容测试只给名字/id 的情况）。"""
        text = str(choice or "").strip()
        for person in matches:
            label = (f"关联已有档案：{person.display_name}"
                     f"（{person.scope.person_id}）")
            if text == label:
                return person
        for person in matches:
            if (person.display_name and person.display_name in text) \
                    or person.scope.person_id in text:
                return person
        if len(matches) == 1 and text.startswith("关联"):
            return matches[0]
        return None

    def _resolve_scope_for_import(self, account_id=None):
        """Return (scope, created_for_live) for one explicit import attempt.

        Before creating a new archive the same-account contacts are searched with
        NFKC/whitespace-normalized names. Same-name archives are never merged
        automatically: the user explicitly chooses associate-existing, create a
        new independent archive, or cancel. Association returns
        ``created_for_live=False`` so a live conversation is not silently bound
        to an older archive.

        When a live conversation title is known it is used as the candidate name
        first (a same-name archive can be associated without retyping); without a
        title, or when the typed name differs, the normal name prompt still runs.
        """
        scope = self.current_scope()
        if scope is not None and (account_id is None
                                  or scope.wechat_account_id == account_id):
            return scope, False
        account_id = account_id or self.active_account_id()
        account = next((item for item in self.store.list_accounts()
                        if item.account_id == account_id), None)
        account_label = (account.label + ("（旧/未区分）" if account.legacy else "")
                         if account is not None else "未选择")

        def choose_or_cancel(matches, candidate_name):
            """Return (scope, handled); handled=False means create a new archive."""
            items = self._same_name_items(matches)
            choice, chosen = self._ask_choice(
                "发现同名联系人档案",
                (f"本地资料“{account_label}”下已有 {len(matches)} 个同名联系人档案。\n"
                 "请选择：关联已有档案（沿用其历史/风格/固定状态）、"
                 "新建独立档案（同名也保留为不同 UUID），或取消导入。"),
                items)
            if not chosen or not choice or "取消" in str(choice):
                self.status.setText("已取消导入：未选择联系人档案。")
                return None, True
            if str(choice) == "新建独立档案" or str(choice).startswith("新建"):
                return None, False
            person = self._match_same_name_choice(choice, matches)
            if person is None:
                self.status.setText("已取消导入：无法识别所选档案。")
                return None, True
            self.set_current_scope(person.scope, person.display_name)
            self.status.setText(
                f"已关联已有档案：{person.display_name}（{person.scope.person_id}）。")
            return person.scope, True

        display_name = self._live_conversation_title
        if display_name:
            matches = self._same_name_people(account_id, display_name)
            if matches:
                chosen_scope, handled = choose_or_cancel(matches, display_name)
                if handled:
                    return chosen_scope, False
                # "新建独立档案" with a known live title creates it directly,
                # without asking the user to retype the same name.
                try:
                    scope = self.create_person(display_name, account_id=account_id)
                    return scope, True
                except ValueError as error:
                    self.status.setText(str(error))
                    return None, False

        if self._live_conversation_title:
            prompt = (f"将把历史导入本地资料“{account_label}”。\n"
                      f"确认这份历史属于当前微信会话“{self._live_conversation_title}”吗？\n"
                      "导入成功后会将新建档案关联到此会话；若会话在导入期间变化，"
                      "将保留档案但不自动关联。\n"
                      "档案使用独立 UUID，同名联系人不会自动合并：")
        else:
            prompt = (f"将把历史导入本地资料“{account_label}”。\n"
                      "首次导入：请确认这份记录所属联系人的档案名称。\n"
                      "将创建独立 UUID 档案，同名联系人不会自动合并：")
        name, ok = self._ask_text(
            "确认档案关联",
            prompt,
            default=self._live_conversation_title or "当前联系人")
        if not ok or not str(name).strip():
            self.status.setText("已取消导入：需要先确认档案关联。")
            return None, False
        display_name = str(name).strip()
        matches = self._same_name_people(account_id, display_name)
        if matches:
            chosen_scope, handled = choose_or_cancel(matches, display_name)
            if handled:
                return chosen_scope, False
        try:
            scope = self.create_person(display_name, account_id=account_id)
            return scope, bool(self._live_conversation_title)
        except ValueError as error:
            self.status.setText(str(error))
            return None, False

    def _ask_text(self, title, prompt, default=""):
        """Small seam so tests can fake the confirmation dialog without patching Qt."""
        return QInputDialog.getText(self, title, prompt, text=default)

    def _ask_choice(self, title, prompt, items):
        return QInputDialog.getItem(self, title, prompt, items, 0, False)

    def choose_other_person(self):
        """Secondary entry: look at inherited/offline profiles without a live chat."""
        if self.active_account_id() is None:
            self.refresh_accounts()
        people = self.store.list_people_in_account(self.active_account_id())
        if not people:
            self.status.setText("尚无其他联系人档案；请先导入记录并确认档案。")
            return None
        # Display names are not identities: make each dialog entry unique so
        # selecting a later same-name profile cannot resolve to the first one.
        names = [person.display_name for person in people]
        if len(set(names)) != len(names):
            names = [f"{person.display_name}（档案 {index + 1} · {person.scope.person_id[:8]}）"
                     for index, person in enumerate(people)]
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

    def create_person(self, display_name, account_key=None, *, account_id=None):
        if account_key is not None:
            self.account.setText(account_key)
            scope = self.store.create_person(display_name, account_key)
            self.refresh_accounts(scope.wechat_account_id)
        elif account_id is not None:
            scope = self.store.create_person_in_account(account_id, display_name)
            self.refresh_accounts(account_id)
        else:
            if self.active_account_id() is None:
                self.refresh_accounts()
            scope = self.store.create_person_in_account(
                self.active_account_id(), display_name)
        self.refresh_people()
        self._select_scope(scope)
        self._update_contact_label()
        self.offset = 0
        self.load_page()
        self._invalidate_derived_views(clear_profile=True)
        return scope

    def choose_file(self):
        # Capture before the native dialog's nested event loop. The opaque value
        # follows this import even if the foreground conversation later changes.
        captured_context = self._capture_import_context()
        captured_scope = self.current_scope()
        captured_account_id = self.active_account_id()
        path, _ = QFileDialog.getOpenFileName(self, "导入聊天记录", "",
                                              "聊天记录 (*.html *.htm *.pdf)")
        if path:
            self.start_import_path(path, captured_context=captured_context,
                                   captured_scope=captured_scope,
                                   captured_account_id=captured_account_id)

    # ------------------------------------------------------------- import jobs
    def active_import_job(self):
        return self._active_import

    def start_import_path(self, path, *, captured_context=_UNSET_IMPORT_CONTEXT,
                          captured_scope=_UNSET_IMPORT_SELECTION,
                          captured_account_id=_UNSET_IMPORT_SELECTION):
        """Start a background import for the current contact; returns job id."""
        if captured_context is _UNSET_IMPORT_CONTEXT:
            captured_context = self._capture_import_context()
        if self._active_import is not None:
            self.status.setText("已有导入任务在进行，请等待完成或取消。")
            return None
        target_account_id = (self.active_account_id()
                             if captured_account_id is _UNSET_IMPORT_SELECTION
                             else captured_account_id)
        scope = (self.current_scope() if captured_scope is _UNSET_IMPORT_SELECTION
                 else captured_scope)
        if scope is not None and scope.wechat_account_id != target_account_id:
            self.status.setText("文件选择期间资料或联系人已变化，请重新选择后导入。")
            return None
        created_for_live = False
        if scope is None:
            scope, created_for_live = self._resolve_scope_for_import(target_account_id)
        if scope is None:
            return None
        try:
            job_id = self.jobs.start(scope, Path(path), self.page_ocr.isChecked())
        except (TypeError, ValueError) as error:
            self.status.setText(str(error))
            return None
        self._active_import = job_id
        selection_matches_capture = (captured_account_id is _UNSET_IMPORT_SELECTION
                                     or target_account_id == self.active_account_id())
        self._import_job_contexts[job_id] = (
            scope, captured_context, created_for_live,
            self._profile_selection_revision, selection_matches_capture)
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
            self._import_job_contexts.pop(job_id, None)
            self._import_timer.stop()
            self.import_button.setEnabled(True)
            self.cancel_import_button.setEnabled(False)
            self.status.setText("导入任务已丢失，请重新导入。")
            return
        if progress.finished:
            self._finish_import(progress)
        else:
            self.progress_label.setText(self._progress_text(progress))

    def _finish_import(self, progress, import_context=None):
        self._import_timer.stop()
        stored_context = self._import_job_contexts.pop(progress.job_id, None)
        profile_changed_during_import = False
        if stored_context is not None and len(stored_context) >= 5:
            _scope, _captured, created_for_live, selection_revision, matches_capture = stored_context
            profile_changed_during_import = bool(
                created_for_live and (not matches_capture
                                      or selection_revision != self._profile_selection_revision
                                      or self.active_account_id() != _scope.wechat_account_id))
        if import_context is None:
            import_context = stored_context
        self._active_import = None
        self.import_button.setEnabled(True)
        self.cancel_import_button.setEnabled(False)
        if progress.status in ("succeeded", "done"):
            self.query.clear()
            self.report.clear()
            self.offset = 0
            # The import job owns the ScopeKey captured when it started. If the
            # user switched profiles while parsing, return to that exact target
            # so newly imported records remain visible and attributable.
            if isinstance(progress.scope, ScopeKey):
                self.refresh_accounts(progress.scope.wechat_account_id)
            self.refresh_people()
            if isinstance(progress.scope, ScopeKey):
                self.set_current_scope(progress.scope)
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
                    f"已导入 {progress.record_count} 个文字片段；内容仅保存在本机。"
                    "已自动生成联系人基础档案；可在档案页启动模型逐句分析。")
                self.history_analysis_status.setText(
                    "已导入新历史并生成基础档案；如需更完整的客户画像与关系趋势，"
                    "请在“档案”标签启动全量模型分析。")
            imported_scope = (progress.scope if isinstance(progress.scope, ScopeKey)
                              else None)
            if imported_scope is not None:
                self._auto_generate_local_profile(imported_scope)
                try:
                    self.import_completed.emit(imported_scope)
                except (RuntimeError, TypeError):
                    pass
            if import_context is not None:
                scope, captured_context, created_for_live = import_context[:3]
                self._notify_import_succeeded(
                    scope, captured_context,
                    created_for_live and not profile_changed_during_import)
                if profile_changed_during_import:
                    self.status.setText(
                        "历史已导入到原先选定的资料，但导入期间资料发生变化，未关联当前微信会话。")
        elif progress.status == "cancelled":
            self.progress_label.setText("已取消，未保存任何记录。")
            self.status.setText("导入已取消；历史库未写入半条记录。")
        else:
            self.progress_label.setText("")
            self.status.setText(f"导入失败：{progress.error or '未知错误'}")

    def _auto_generate_local_profile(self, scope):
        """Best-effort local base profile after a successful import.

        A profile failure must never undo/rollback a successful import: the
        error is shown in ``status`` and the import-completed signal still fires
        so MainWindow can decide whether to run the model analysis.
        """
        try:
            return build_local_profile(self.store, scope)
        except Exception as error:  # noqa: BLE001 - profile is best-effort
            self.status.setText(f"导入已成功，但自动生成基础档案失败：{error}")
            return None

    def import_path(self, path):
        """Synchronous import (tests / explicit small files); async path is choose_file."""
        captured_context = self._capture_import_context()
        scope = self.current_scope()
        created_for_live = False
        if scope is None:
            scope, created_for_live = self._resolve_scope_for_import()
        if scope is None:
            return None
        try:
            progress = self.jobs.run_sync(scope, Path(path), self.page_ocr.isChecked())
        except (HistoryImportError, OSError, ValueError) as error:
            self.status.setText(str(error))
            return None
        self._finish_import(progress, (scope, captured_context, created_for_live))
        return progress

    def closeEvent(self, event):
        if self._active_import is not None:
            try:
                self.jobs.cancel(self._active_import)
            except ImportJobError:
                pass
        self._import_timer.stop()
        self._history_analysis_timer.stop()
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
        if report is not None:
            report_scope = self._report_scope_value(report)
            if report_scope is None or report_scope != self.current_scope():
                self.history_analysis_status.setText(
                    "已拒绝显示其他联系人或未绑定范围的历史分析报告。")
                return False
        self._profile_report = report
        self.profile_panel.set_scope(self.current_scope(), self.persons.currentText())
        self.profile_panel.set_report(report)
        if report is not None:
            self.tabs.setCurrentIndex(1)
        return True

    def _report_scope_value(self, report):
        if isinstance(report, dict):
            return report.get("scope")
        return getattr(report, "scope", None)

    def set_history_analysis_callbacks(self, start_callback,
                                       progress_callback=None,
                                       report_callback=None,
                                       resume_callback=None,
                                       cancel_callback=None,
                                       terminal_callback=None):
        """Inject the T7 boundary without coupling this page to its worker/backend.

        ``start_callback(scope)`` returns a job id. Optional progress/report
        callbacks are queried by job id; progress may also be pushed through
        ``set_job_progress`` by a controller.
        """
        self._history_analysis_start = start_callback
        self._history_analysis_progress = progress_callback
        self._history_analysis_report = report_callback
        self._history_analysis_resume = resume_callback
        self._history_analysis_cancel = cancel_callback
        self._history_analysis_terminal = terminal_callback
        self._update_contact_label()

    def active_history_job(self):
        return self._active_history_job

    def start_history_analysis(self):
        scope = self.current_scope()
        if scope is None:
            self.history_analysis_status.setText("请先选择联系人档案，再开始全量历史分析。")
            return None
        if self._history_analysis_start is None:
            self.history_analysis_status.setText("历史分析任务尚未连接；请先配置分析后端。")
            self.analyze_history_button.setEnabled(False)
            return None
        if self._active_history_job is not None:
            self.history_analysis_status.setText("已有全量历史分析任务正在运行。")
            return None
        paused_job = self._paused_job_for_scope(scope)
        if (paused_job is not None
                and self._history_analysis_resume is not None):
            self._paused_history_job = paused_job
            self._history_job_scope = scope
            return self.resume_history_analysis()
        try:
            job_id = self._history_analysis_start(scope)
        except Exception as error:
            self.history_analysis_status.setText(
                f"历史分析启动失败（{type(error).__name__}）；请检查配置后重试。")
            return None
        if not isinstance(job_id, str) or not job_id.strip():
            self.history_analysis_status.setText("历史分析启动失败：任务没有返回有效编号。")
            return None
        self._active_history_job = job_id
        self._paused_history_jobs.pop(job_id, None)
        self._history_job_scopes[job_id] = scope
        self._history_job_scope = scope
        self.history_analysis_status.setText(f"全量历史分析已启动：{job_id}")
        self.task_list.upsert(SimpleNamespace(
            job_id=job_id, status="running", records_done=0, total_records="?",
            message="任务已启动"))
        self._update_contact_label()
        if self._history_analysis_progress is not None:
            self._history_analysis_timer.start()
        return job_id

    def resume_history_analysis(self):
        scope = self.current_scope()
        job_id = self._paused_job_for_scope(scope)
        if job_id is None:
            self.history_analysis_status.setText(
                "请切回原联系人档案后再继续已暂停的全量分析。")
            return None
        if self._history_analysis_resume is None:
            self.history_analysis_status.setText("当前无法恢复历史分析任务。")
            return None
        try:
            self._history_analysis_resume(job_id)
        except Exception as error:
            self.history_analysis_status.setText(
                f"恢复历史分析失败（{type(error).__name__}）；可重新开始分析。")
            self._update_contact_label()
            return None
        self._paused_history_jobs.pop(job_id, None)
        self._paused_history_job = None
        self._active_history_job = job_id
        self._history_job_scope = scope
        self.task_list.upsert(SimpleNamespace(
            job_id=job_id, status="running", records_done=0, total_records="?",
            message="任务已继续"))
        self.history_analysis_status.setText(f"已继续全量历史分析：{job_id}")
        self._update_contact_label()
        if self._history_analysis_progress is not None:
            self._history_analysis_timer.start()
        return job_id

    def cancel_history_analysis(self):
        job_id = (self._active_history_job or
                  self._paused_job_for_scope(self.current_scope()))
        if job_id is None or self._history_analysis_cancel is None:
            return False
        try:
            result = self._history_analysis_cancel(job_id)
        except Exception as error:
            self.history_analysis_status.setText(
                f"取消历史分析失败（{type(error).__name__}）。")
            return False
        if self._active_history_job is None:
            self._active_history_job = job_id
        result_status = str(getattr(result, "status", "cancelled"))
        if result_status not in {"completed", "succeeded", "cancelled", "failed", "paused"}:
            result_status = "cancelled"
        self._finish_history_analysis(result_status)
        return True

    def set_job_progress(self, progress):
        """Update the independent long-task list from a T7 JobProgress (duck-typed)."""
        self.task_list.upsert(progress)
        if (self._active_history_job is not None
                and str(getattr(progress, "job_id", "")) == self._active_history_job):
            status = str(getattr(progress, "status", "running"))
            if status in {"completed", "succeeded", "cancelled", "paused", "failed"}:
                self._finish_history_analysis(status)
            self.history_analysis_status.setText(format_task_progress(progress))
        else:
            self.history_analysis_status.setText(format_task_progress(progress))

    def _poll_history_analysis(self):
        job_id = self._active_history_job
        if job_id is None or self._history_analysis_progress is None:
            self._history_analysis_timer.stop()
            return
        try:
            progress = self._history_analysis_progress(job_id)
        except Exception as error:
            self._history_analysis_timer.stop()
            self._finish_history_analysis("failed")
            self.history_analysis_status.setText(
                f"历史分析进度读取失败（{type(error).__name__}）。")
            return
        if progress is None:
            self.history_analysis_status.setText("正在等待历史分析进度…")
            return
        self.set_job_progress(progress)

    def _finish_history_analysis(self, status):
        job_id = self._active_history_job
        job_scope = self._history_job_scopes.get(job_id, self._history_job_scope)
        self._history_analysis_timer.stop()
        self._active_history_job = None
        if status in {"paused", "failed"}:
            self._paused_history_job = job_id
            self._paused_history_jobs[job_id] = self._history_job_scopes.get(
                job_id, self._history_job_scope)
        elif status in {"completed", "succeeded", "cancelled"}:
            self._paused_history_job = None
            self._paused_history_jobs.pop(job_id, None)
            self._history_job_scopes.pop(job_id, None)
            if self._history_analysis_terminal is not None:
                self._history_analysis_terminal(job_id, status)
        self._update_contact_label()
        if status in {"completed", "succeeded"} and self._history_analysis_report is not None:
            try:
                report = self._history_analysis_report(job_id)
            except Exception as error:
                self.history_analysis_status.setText(
                    f"分析已完成，但读取报告失败（{type(error).__name__}）。")
                return
            if job_scope != self.current_scope():
                self.history_analysis_status.setText(
                    "分析已完成；当前联系人已切换，报告不会显示在其他联系人下。")
                return
            if not self.set_profile_report(report):
                return
            self.history_analysis_status.setText("全量历史分析已完成，档案报告已更新。")
        elif status not in {"completed", "succeeded"}:
            if status in {"paused", "failed"}:
                self.history_analysis_status.setText(
                    f"全量历史分析已{status}；可回到任务标签继续/重试，或取消。")
            else:
                self.history_analysis_status.setText(f"全量历史分析结束：{status}。")

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
            self.trend_panel.set_report(None)
            self.report.clear()

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
