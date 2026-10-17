"""Profile / trend / statistics / task widgets for the history page (T8).

Design §8: the history page keeps the original text, the model-derived profile,
trends, deterministic statistics and long-running analysis tasks in separate tabs;
a long task must never block browsing the chat or the history page.

T7 dependency: ``ProfilePanel.set_report(report)`` consumes the frozen T7
``HistoryReport`` shape through the duck-typed adapter in
:mod:`liteapp.history_report` (``facts``/``inferences``/``open_questions``,
``ReportItem.text/category/evidence``, ``timeline``, ``coverage`` …). This module
does not import any T7 module, so T8 can ship before/parallel to T7 and the real
report is accepted unchanged once available. ``HistoryTaskList.upsert`` likewise
accepts the frozen T7 ``JobProgress`` fields (records_done/total_records,
pending_batches/failed_batches, coverage, parse_missing_pages, message).
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .contracts import EvidenceRef, ScopeKey
from .history_report import (
    LABEL_FACT,
    LABEL_INFERENCE,
    PROFILE_SECTIONS,
    ReportFinding,
    findings_by_section,
    report_meta,
)
from .history_statistics import LABELS, StatisticsReport

__all__ = [
    "HistoryTaskList",
    "ProfilePanel",
    "StatisticsPanel",
    "TrendPanel",
    "format_task_progress",
]

TASK_STATUS_LABELS = {
    "pending": "排队中",
    "queued": "排队中",
    "running": "运行中",
    "paused": "已暂停",
    "completed": "已完成",
    "succeeded": "已完成",
    "failed": "已失败",
    "cancelled": "已取消",
}

EMPTY_PROFILE_TEXT = "尚无模型分析结果；完成历史分析任务后显示。"


class ProfilePanel(QWidget):
    """Model-derived profile: 需求/偏好/承诺/待办/冲突/时间线 with evidence labels."""

    evidence_activated = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scope: ScopeKey | None = None
        self._report: Any = None
        self._findings: tuple[ReportFinding, ...] = ()
        self._item_for_finding: dict[int, QTreeWidgetItem] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.header = QLabel("当前档案：未选择")
        self.header.setWordWrap(True)
        layout.addWidget(self.header)
        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        self.empty = QLabel(EMPTY_PROFILE_TEXT)
        self.empty.setWordWrap(True)
        layout.addWidget(self.empty)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        self.tree.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.tree.itemActivated.connect(self._on_tree_item_activated)
        self.tree.itemClicked.connect(self._on_tree_item_clicked)
        layout.addWidget(self.tree, 1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.set_report(None)

    # ------------------------------------------------------------------ public
    def set_scope(self, scope: ScopeKey | None, display_name: str = "") -> None:
        if scope is not None and not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey 或 None。")
        self._scope = scope
        name = display_name.strip() if isinstance(display_name, str) else ""
        if not name and scope is not None:
            name = scope.person_id[:8]
        self.header.setText(f"当前档案：{name or '未选择'}")

    def set_report(self, report: Any) -> None:
        self._report = report
        grouped = findings_by_section(report)
        flat: list[ReportFinding] = []
        for findings in grouped.values():
            flat.extend(findings)
        self._findings = tuple(flat)
        self._rebuild_tree(grouped)
        self._update_summary()
        self.empty.setText("" if flat else EMPTY_PROFILE_TEXT)
        self.empty.setVisible(not bool(flat))

    def clear(self) -> None:
        self._report = None
        self._findings = ()
        self.tree.clear()
        self._item_for_finding.clear()
        self.empty.setText(EMPTY_PROFILE_TEXT)
        self.empty.setVisible(True)
        self.summary.setText("")
        self.status.setText("")

    def findings(self) -> tuple[ReportFinding, ...]:
        return self._findings

    def tree_text(self) -> str:
        lines: list[str] = []

        def collect(item: QTreeWidgetItem) -> None:
            lines.append(item.text(0))
            for index in range(item.childCount()):
                collect(item.child(index))

        for index in range(self.tree.topLevelItemCount()):
            collect(self.tree.topLevelItem(index))
        return "\n".join(lines)

    def activate_finding(self, index: int) -> bool:
        """Select one normalized finding; emit its first EvidenceRef if any."""
        if isinstance(index, bool) or not isinstance(index, int):
            raise TypeError("index 必须是整数。")
        if index < 0 or index >= len(self._findings):
            return False
        finding = self._findings[index]
        item = self._item_for_finding.get(index)
        if item is not None:
            self.tree.setCurrentItem(item)
            self.tree.scrollToItem(item)
        if not finding.evidence:
            self.status.setText("该条目没有证据引用，不能跳转原文。")
            return False
        self.status.setText(f"已选择证据 {len(finding.evidence)} 条。")
        self.evidence_activated.emit(finding.evidence[0])
        return True

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        # Long Chinese findings wrap inside the tree; keep the panel itself from
        # forcing a horizontally scrolling page at 125%/150% scaling.
        return QSize(min(hint.width(), 760), hint.height())

    # ----------------------------------------------------------------- internal
    def _rebuild_tree(self, grouped: dict[str, tuple[ReportFinding, ...]]) -> None:
        self.tree.clear()
        self._item_for_finding.clear()
        next_index = 0
        ordered_sections = list(PROFILE_SECTIONS)
        ordered_sections.extend(section for section in grouped
                                if section not in ordered_sections)
        for section in ordered_sections:
            findings = grouped.get(section, ())
            top = QTreeWidgetItem([f"{section}（{len(findings)}）"])
            self.tree.addTopLevelItem(top)
            for finding in findings:
                child = QTreeWidgetItem([finding.as_line()])
                if finding.evidence:
                    child.setData(0, Qt.UserRole, finding.evidence[0])
                    child.setToolTip(0, "点击/回车跳转到原文证据")
                child.setData(0, Qt.UserRole + 1, next_index)
                top.addChild(child)
                self._item_for_finding[next_index] = child
                next_index += 1
            top.setExpanded(True)

    def _update_summary(self) -> None:
        if self._report is None:
            self.summary.setText("")
            return
        meta = report_meta(self._report)
        parts = []
        if meta.get("profile_id"):
            parts.append(f"档案 {meta['profile_id']}")
        if meta.get("source_version") is not None:
            parts.append(f"source_version={meta['source_version']}")
        if meta.get("memory_version") is not None:
            parts.append(f"memory_version={meta['memory_version']}")
        if meta.get("coverage") is not None:
            parts.append(f"覆盖率 {meta['coverage']:.0%}")
        if meta.get("parse_missing_pages"):
            parts.append(f"解析缺页 {meta['parse_missing_pages']} 页")
        summary = meta.get("summary")
        if summary:
            parts.append(str(summary))
        self.summary.setText("；".join(parts) if parts else "模型报告未提供版本/覆盖率信息。")

    def _on_tree_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        self._emit_item(item)

    def _on_tree_item_activated(self, item: QTreeWidgetItem, _column: int) -> None:
        self._emit_item(item)

    def _emit_item(self, item: QTreeWidgetItem) -> None:
        evidence = item.data(0, Qt.UserRole)
        if isinstance(evidence, EvidenceRef):
            self.evidence_activated.emit(evidence)


class StatisticsPanel(QWidget):
    """Deterministic local statistics with [事实]/[推断]/[不可计算] labels."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: dict[str, tuple[str, str, str, str]] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("尚未生成统计。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["指标", "值", "标签", "备注"])
        self.tree.setWordWrap(True)
        self.tree.setUniformRowHeights(False)
        layout.addWidget(self.tree, 1)
        self.set_statistics(None)

    def set_statistics(self, report: StatisticsReport | None) -> None:
        if report is not None and not isinstance(report, StatisticsReport):
            raise TypeError("report 必须是 StatisticsReport 或 None。")
        self._rows = {}
        self.tree.clear()
        if report is None:
            self.hint.setText("尚未导入或尚未生成统计；导入记录后可生成确定性本地统计。")
            return
        for metric in report.metrics:
            item = QTreeWidgetItem([metric.title, metric.value, metric.label, metric.note])
            self.tree.addTopLevelItem(item)
            self._rows[metric.key] = (metric.title, metric.value, metric.label, metric.note)
        self.hint.setText("本地确定性统计（不调用模型）；[事实] 可直接核对，"
                          "[推断] 仅为文本线索，[不可计算] 表示缺少可靠字段。")

    def metric_row(self, key: str) -> tuple[str, str, str, str] | None:
        return self._rows.get(key)

    def tree_text(self) -> str:
        lines = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            lines.append(" | ".join(item.text(column) for column in range(4)))
        return "\n".join(lines)


class TrendPanel(QWidget):
    """Trends: local month facts vs model timeline/inferences, clearly labelled."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._statistics: StatisticsReport | None = None
        self._report: Any = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("暂无趋势数据。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["项目", "数量/说明", "标签"])
        self.tree.setWordWrap(True)
        layout.addWidget(self.tree, 1)

    def set_statistics(self, report: StatisticsReport | None) -> None:
        if report is not None and not isinstance(report, StatisticsReport):
            raise TypeError("report 必须是 StatisticsReport 或 None。")
        self._statistics = report
        self._rebuild()

    def set_report(self, report: Any) -> None:
        self._report = report
        self._rebuild()

    def clear(self) -> None:
        self._statistics = None
        self._report = None
        self._rebuild()

    def tree_text(self) -> str:
        lines = []
        for index in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(index)
            lines.append(" | ".join(item.text(column) for column in range(3)))
        return "\n".join(lines)

    def _add_row(self, title: str, detail: str, label: str) -> None:
        self.tree.addTopLevelItem(QTreeWidgetItem([f"[{label}] {title}", detail, label]))

    def _rebuild(self) -> None:
        self.tree.clear()
        if self._statistics is not None:
            for month, count in self._statistics.months:
                self._add_row(f"{month} 带可靠时间的记录", f"{count} 条", LABELS["fact"])
            if self._statistics.date_hint_count:
                self._add_row("文本中的日期线索", f"{self._statistics.date_hint_count} 条",
                              LABELS["inference"])
            if self._statistics.reply_latency_seconds is not None:
                self._add_row("回复时延（中位）",
                              f"{self._statistics.reply_latency_seconds:.1f} 秒",
                              LABELS["fact"])
            else:
                self._add_row("回复时延", "不可计算", LABELS["unavailable"])
        grouped = findings_by_section(self._report) if self._report is not None else {}
        for finding in grouped.get("时间线", ()):
            self._add_row("模型时间线", finding.text, finding.label)
        if self.tree.topLevelItemCount() == 0:
            self.hint.setText("暂无趋势数据；导入记录或完成历史分析后显示。")
        else:
            self.hint.setText("本地统计为 [事实] 标签；模型倾向解释为 [推断]/[待确认] 标签。")


def _progress_value(progress: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        value = getattr(progress, name, None)
        if value is not None:
            return value
    return default


def format_task_progress(progress: Any) -> str:
    """Format a T7 ``JobProgress`` (duck-typed) for the independent task list."""
    status = str(_progress_value(progress, "status", default="running") or "running")
    label = TASK_STATUS_LABELS.get(status, status)
    job_id = str(_progress_value(progress, "job_id", default="任务"))
    records_done = _progress_value(progress, "records_done", default=0)
    total_records = _progress_value(progress, "total_records", "total", default="?")
    parts = [f"[{label}] {job_id}：记录 {records_done}/{total_records}"]
    coverage = _progress_value(progress, "coverage", default=None)
    try:
        if coverage is not None:
            parts.append(f"覆盖率 {float(coverage):.0%}")
    except (TypeError, ValueError):
        pass
    pending = _progress_value(progress, "pending_batches", default=None)
    if pending is not None:
        parts.append(f"待办 {pending}")
    failed = _progress_value(progress, "failed_batches", default=None)
    if failed is not None:
        parts.append(f"失败批次 {failed}")
    missing = _progress_value(progress, "parse_missing_pages", default=None)
    if missing is not None:
        parts.append(f"缺页 {missing}")
    message = _progress_value(progress, "message", "error", default=None)
    text = "，".join(parts)
    if message:
        text += f"；{message}"
    return text


class HistoryTaskList(QWidget):
    """Independent long-task list; updates never block chat/history browsing."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: dict[str, int] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.hint = QLabel("暂无分析任务；长任务在后台运行，不阻塞聊天与浏览。")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.list = QListWidget()
        self.list.setWordWrap(False)
        layout.addWidget(self.list, 1)

    def upsert(self, progress: Any) -> None:
        job_id = str(_progress_value(progress, "job_id", default="任务"))
        text = format_task_progress(progress)
        row = self._rows.get(job_id)
        if row is None:
            self._rows[job_id] = self.list.count()
            self.list.addItem(QListWidgetItem(text))
        else:
            self.list.item(row).setText(text)
        self.hint.setText("任务进度仅显示计数/阶段/失败批次；长任务不阻塞聊天。")

    def clear(self) -> None:
        self._rows = {}
        self.list.clear()
        self.hint.setText("暂无分析任务；长任务在后台运行，不阻塞聊天与浏览。")

    def job_texts(self) -> tuple[str, ...]:
        return tuple(self.list.item(index).text() for index in range(self.list.count()))
