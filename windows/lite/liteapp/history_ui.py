"""History import and browsing page. Imported text stays local until an explicit model task."""
from pathlib import Path

from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QComboBox, QPlainTextEdit, QFileDialog,
                               QInputDialog, QTabWidget)

from .history import HistoryStore, HistoryImportError
from .history_report import build_local_report


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
        layout.addLayout(person_row)

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
        if self._store is not None:
            self.refresh_people()

    @property
    def store(self):
        if self._store is None:
            self._store = HistoryStore()
        return self._store

    def refresh_people(self):
        self.report.clear()
        selected = self.persons.currentData()
        self.persons.blockSignals(True)
        self.persons.clear()
        for person in self.store.list_people(self.account.text().strip() or "default"):
            self.persons.addItem(person["display_name"], person["id"])
        index = self.persons.findData(selected)
        if index >= 0:
            self.persons.setCurrentIndex(index)
        self.persons.blockSignals(False)
        self.offset = 0
        self.load_page()

    def create_person(self, display_name, account_key=None):
        if account_key is not None:
            self.account.setText(account_key)
        person_id = self.store.create_person(display_name, self.account.text().strip() or "default")
        self.refresh_people()
        self.persons.setCurrentIndex(self.persons.findData(person_id))
        return person_id

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
            self.import_path(path)

    def import_path(self, path):
        person_id = self.persons.currentData()
        if not person_id:
            self.status.setText("请先为这份记录选择或新建联系人。")
            return
        try:
            result = self.store.import_file(Path(path), person_id)
        except (HistoryImportError, OSError, ValueError) as error:
            self.status.setText(str(error))
            return
        self.query.clear()
        self.report.clear()
        self.offset = 0
        self.load_page()
        self.status.setText(("已存在相同文件，未重复导入。" if result["duplicate"] else
                             f"已导入 {result['section_count']} 个文字片段；内容仅保存在本机。"))

    def _set_records(self, rows):
        self.records.setPlainText("\n\n".join(
            f"[{row['source_name']} · {row['label']}]\n{row['text']}" for row in rows))

    def load_page(self, *_):
        person_id = self.persons.currentData()
        if not person_id:
            self.records.clear()
            self.summary.setText("先创建联系人，再导入 HTML 或 PDF。")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        totals = self.store.stats(person_id)
        rows = self.store.list_messages(person_id, offset=self.offset, limit=self.PAGE_SIZE)
        self._set_records(rows)
        self.summary.setText(f"已导入 {totals['source_count']} 个文件，"
                             f"{totals['section_count']} 个文字片段，约 {totals['char_count']} 字。"
                             "来源与范围以导入文件为准。")
        self.page_label.setText(f"第 {self.offset // self.PAGE_SIZE + 1} 页")
        self.previous_button.setEnabled(self.offset > 0)
        self.next_button.setEnabled(self.offset + self.PAGE_SIZE < totals["section_count"])

    def search_records(self):
        person_id = self.persons.currentData()
        if not person_id:
            return
        query = self.query.text().strip()
        if not query:
            self.offset = 0
            self.load_page()
            return
        rows = self.store.search(person_id, query, limit=100)
        self._set_records(rows)
        self.status.setText(f"找到 {len(rows)} 条匹配（最多显示 100 条）。")
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)

    def generate_report(self):
        person_id = self.persons.currentData()
        if not person_id:
            self.status.setText("请先选择联系人并导入聊天记录。")
            return
        self.report.setPlainText(build_local_report(self.store, person_id))
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
