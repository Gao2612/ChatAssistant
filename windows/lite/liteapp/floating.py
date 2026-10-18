"""Compact always-on-top floating assistant.

The floating window is an optional view over the same main window: it does not
own capture or analysis, it only shows the live status and the "分析当前对话"
action. Opening the main window keeps every other feature (settings, history,
AI suggestions) unchanged.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

FLOATING_STYLE = """
QWidget#floatingPanel {
    background: #F7F5EE;
    border: 1px solid #D5D0C4;
    border-radius: 12px;
}
QLabel#floatTitle { color: #1F2421; font-size: 15px; font-weight: 600; }
QLabel#floatName { color: #176B5B; font-size: 14px; font-weight: 600; }
QLabel#floatMuted { color: #6B746E; font-size: 12px; }
QPushButton {
    background: #FFFFFF; border: 1px solid #CFCBC0; border-radius: 6px;
    padding: 5px 9px; color: #1F2421;
}
QPushButton:hover { background: #EEF3EF; }
QPushButton#floatPrimary {
    background: #176B5B; border-color: #176B5B; color: #FFFFFF; font-weight: 600;
}
QPushButton#floatPrimary:disabled { background: #A9C4BA; border-color: #A9C4BA; }
QPushButton#floatGhost { border: 0; background: transparent; padding: 2px 6px; }
"""


class FloatingAssistant(QWidget):
    """Small frameless, draggable, always-on-top status/action panel."""

    def __init__(self, host, parent=None):
        super().__init__(parent, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.host = host
        self._drag_offset = None
        self.setWindowTitle("ChatAssistant · 悬浮")
        self.setObjectName("floatingPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(FLOATING_STYLE)
        self.setFixedSize(380, 236)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("ChatAssistant · 悬浮")
        title.setObjectName("floatTitle")
        header.addWidget(title, 1)
        self.main_button = QPushButton("主界面")
        self.main_button.setObjectName("floatGhost")
        self.main_button.setToolTip("打开完整主界面（设置/历史/AI 建议）")
        self.main_button.clicked.connect(self.show_main)
        header.addWidget(self.main_button)
        close_button = QPushButton("×")
        close_button.setObjectName("floatGhost")
        close_button.setFixedWidth(24)
        close_button.setToolTip("关闭悬浮窗并打开主界面")
        close_button.clicked.connect(self.show_main)
        header.addWidget(close_button)
        layout.addLayout(header)

        self.name_label = QLabel("尚未连接会话")
        self.name_label.setObjectName("floatName")
        self.name_label.setWordWrap(False)
        layout.addWidget(self.name_label)

        self.status_label = QLabel("等待微信窗口")
        self.status_label.setObjectName("floatMuted")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.analysis_label = QLabel("等待分析")
        self.analysis_label.setObjectName("floatMuted")
        self.analysis_label.setWordWrap(True)
        layout.addWidget(self.analysis_label)

        self.analyze_button = QPushButton("分析当前对话")
        self.analyze_button.setObjectName("floatPrimary")
        self.analyze_button.setToolTip("对当前原生微信窗口的可见对话进行分析")
        self.analyze_button.clicked.connect(self._analyze)
        layout.addWidget(self.analyze_button)
        layout.addWidget(QLabel("只监控原生微信窗口；完整设置与建议在主界面查看",)
                         if False else self._hint())
        layout.addStretch(1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(600)
        self.refresh()

    @staticmethod
    def _hint():
        hint = QLabel("只监控原生微信窗口 · 完整设置与建议在主界面查看")
        hint.setObjectName("floatMuted")
        hint.setWordWrap(True)
        return hint

    def refresh(self):
        host = self.host
        try:
            self.name_label.setText(host.chat_title.text() or "尚未连接会话")
            self.status_label.setText(host.connection_status.text() or "等待微信窗口")
            self.analysis_label.setText(host.analysis_status.text() or "等待分析")
            ready = bool(getattr(getattr(host, "state", None), "ready", False))
            busy = bool(getattr(host, "busy", False))
            self.analyze_button.setEnabled(ready and not busy)
            self.analyze_button.setToolTip(
                "分析当前可见对话" if ready else "等待识别到可直接分析的聊天文字")
        except RuntimeError:
            self._timer.stop()

    def _analyze(self):
        try:
            self.host.start_analysis()
        except RuntimeError:
            return

    def show_main(self):
        self.hide()
        try:
            self.host.show()
            self.host.raise_()
            self.host.activateWindow()
        except RuntimeError:
            pass

    # -- dragging -----------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self._timer.stop()
        self.host = None
        event.accept()
