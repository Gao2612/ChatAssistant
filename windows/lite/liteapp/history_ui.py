"""History import and browsing page. Imported text stays local until an explicit model task.

T5：HistoryStore 的公开 API 已收紧为 ScopeKey（不再接受 person_id 快捷入口），
联系人 combobox 的 userData 是 ScopeKey。
T6：导入改走 liteapp.import_jobs.ImportJobs（后台线程 + 进度 + 取消），解析器是
liteapp.importers（HTML 安全纯文本 / PDF pypdfium2 受控子进程），导入完成后才一次性
提交加密历史库；`import_path` 仍保留同步语义供测试与显式小文件路径使用。
"""
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QComboBox, QPlainTextEdit, QFileDialog,
                               QInputDialog, QTabWidget, QCheckBox)

from .history import HistoryStore, HistoryImportError
from .history_report import build_local_report
from .import_jobs import ImportJobError, ImportJobs


class HistoryPage(QWidget):
    PAGE_SIZE = 100

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        self._store = store
        self.offset = 0
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        title = QLabel("历史记录与本地记忆库")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addWidget(QLabel("按微信账号与联系人分开管理；导入内容保存在本机。"))

        account_row = QHBoxLayout()
        account_row.addWidget(QLabel("微信账号"))
        self.account = QLineEdit("默认微信账号")
        self.account.setPlaceholderText("输入账号标识，避免不同微信账号的同名联系人合并")
        account_row.addWidget(self.account, 1)
        self.account_refresh = QPushButton("切换账号")
        self.account_refresh.clicked.connect(self.refresh_people)
        account_row.addWidget(self.account_refresh)
        layout.addLayout(account_row)

        person_row = QHBoxLayout()
        person_row.addWidget(QLabel("联系人"))
        self.persons = QComboBox()
        self.persons.currentIndexChanged.connect(self.load_page)
        person_row.addWidget(self.persons, 1)
        add_person = QPushButton("新建联系人")
        add_person.clicked.connect(self.prompt_person)
        person_row.addWidget(add_person)
        self.import_button = QPushButton("导入 HTML / PDF")
        self.import_button.clicked.connect(self.choose_file)
        person_row.addWidget(self.import_button)
        self.cancel_import_button = QPushButton("取消导入")
        self.cancel_import_button.setEnabled(False)
        self.cancel_import_button.clicked.connect(self.cancel_active_import)
        person_row.addWidget(self.cancel_import_button)
        layout.addLayout(person_row)

        ocr_row = QHBoxLayout()
        self.page_ocr = QCheckBox("扫描页本地 OCR（较慢，仅对无文字页）")
        ocr_row.addWidget(self.page_ocr)
        ocr_row.addStretch(1)
        layout.addLayout(ocr_row)

        self.summary = QLabel("选择联系人后可查看导入范围和记录。")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.progress_label = QLabel("")
        self.progress_label.setWordWrap(True)
        layout.addWidget(self.progress_label)
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
        self.records.setPlaceholderText("导入 ExportWeChat HTML 或有文字层的 PDF 后，在这里浏览完整记录。")
        self.tabs = QTabWidget()
        self.tabs.addTab(self.records, "完整记录")
        report_page = QWidget()
        report_layout = QVBoxLayout(report_page)
        report_button = QPushButton("整理全部已导入记录")
        report_button.clicked.connect(self.generate_report)
        report_layout.addWidget(report_button)
        self.report = QPlainTextEdit()
        self.report.setReadOnly(True)
        self.report.setPlaceholderText("生成本地客户画像线索、时间趋势和聊天统计。")
        report_layout.addWidget(self.report, 1)
        self.tabs.addTab(report_page, "本地分析")
        layout.addWidget(self.tabs, 1)
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
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self._jobs = None
        self._active_import = None
        self._import_timer = QTimer(self)
        self._import_timer.setInterval(200)
        self._import_timer.timeout.connect(self._poll_import)
        if self._store is not None:
            self.refresh_people()

    @property
    def store(self):
        if self._store is None:
            self._store = HistoryStore()
        return self._store

    @property
    def jobs(self):
        if self._jobs is None:
            self._jobs = ImportJobs(self.store)
        return self._jobs

    # ------------------------------------------------------------- import jobs
    def active_import_job(self):
        return self._active_import

    def start_import_path(self, path):
        """Start a background import for the selected contact; returns job id."""
        scope = self.persons.currentData()
        if not scope:
            self.status.setText("请先为这份记录选择或新建联系人。")
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

    def closeEvent(self, event):
        if self._active_import is not None:
            try:
                self.jobs.cancel(self._active_import)
            except ImportJobError:
                pass
        self._import_timer.stop()
        super().closeEvent(event)

    def _select_scope(self, scope):
        """Select a ScopeKey in the combobox by value (PySide findData is identity-based)."""
        for index in range(self.persons.count()):
            if self.persons.itemData(index) == scope:
                self.persons.setCurrentIndex(index)
                return index
        return -1

    def refresh_people(self):
        self.report.clear()
        selected = self.persons.currentData()
        self.persons.blockSignals(True)
        self.persons.clear()
        for person in self.store.list_people(self.account.text().strip() or "default"):
            self.persons.addItem(person.display_name, person.scope)
        self._select_scope(selected)
        self.persons.blockSignals(False)
        self.offset = 0
        self.load_page()

    def create_person(self, display_name, account_key=None):
        if account_key is not None:
            self.account.setText(account_key)
        scope = self.store.create_person(display_name, self.account.text().strip() or "default")
        self.refresh_people()
        self._select_scope(scope)
        return scope

    def prompt_person(self):
        name, ok = QInputDialog.getText(self, "新建联系人", "联系人名称")
        if ok and name.strip():
            try:
                self.create_person(name)
            except ValueError as error:
                self.status.setText(str(error))

    def choose_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入聊天记录", "",
                                              "聊天记录 (*.html *.htm *.pdf)")
        if path:
            self.start_import_path(path)

    def import_path(self, path):
        """Synchronous import (tests / explicit small files); async path is choose_file."""
        scope = self.persons.currentData()
        if not scope:
            self.status.setText("请先为这份记录选择或新建联系人。")
            return None
        try:
            progress = self.jobs.run_sync(scope, Path(path), self.page_ocr.isChecked())
        except (HistoryImportError, OSError, ValueError) as error:
            self.status.setText(str(error))
            return None
        self._finish_import(progress)
        return progress

    def _set_records(self, rows):
        self.records.setPlainText("\n\n".join(
            f"[{row['source_name']} · {row['label']}]\n{row['text']}" for row in rows))

    def load_page(self, *_):
        scope = self.persons.currentData()
        if not scope:
            self.records.clear()
            self.summary.setText("先创建联系人，再导入 HTML 或 PDF。")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        totals = self.store.stats(scope)
        rows = self.store.list_messages(scope, offset=self.offset, limit=self.PAGE_SIZE)
        self._set_records(rows)
        self.summary.setText(f"已导入 {totals.source_count} 个文件，"
                             f"{totals.record_count} 个文字片段，约 {totals.char_count} 字。"
                             "来源与范围以导入文件为准。")
        self.page_label.setText(f"第 {self.offset // self.PAGE_SIZE + 1} 页")
        self.previous_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(self.offset + self.PAGE_SIZE < totals.record_count)

    def search_records(self):
        scope = self.persons.currentData()
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

    def generate_report(self):
        scope = self.persons.currentData()
        if not scope:
            self.status.setText("请先选择联系人并导入聊天记录。")
            return
        self.report.setPlainText(build_local_report(self.store, scope))
        self.tabs.setCurrentIndex(1)
        self.status.setText("已整理全部已导入的文字；报告仅依据文件中可识别的信息。")

    def previous_page(self):
        self.query.clear()
        self.offset = max(0, self.offset - self.PAGE_SIZE)
        self.load_page()

    def next_page(self):
        self.query.clear()
        self.offset += self.PAGE_SIZE
        self.load_page()
