"""Floating assistant ball.

The ball is the primary compact interface: a draggable always-on-top circular
button that expands into a panel with live status, the AI suggestions and the
analysis action. The main window is only used for settings/history once the ball
mode is active.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

BALL_STYLE = """
QWidget#floatRoot { background: transparent; }
QFrame#ballFrame {
    background: #176B5B; border: 2px solid #0F483D; border-radius: 29px;
}
QLabel#ballText { color: #FFFFFF; font-size: 15px; font-weight: 700; }
QFrame#floatPanel {
    background: #F7F5EE; border: 1px solid #D5D0C4; border-radius: 12px;
}
QLabel#floatTitle { color: #1F2421; font-size: 15px; font-weight: 600; }
QLabel#floatName { color: #176B5B; font-size: 14px; font-weight: 600; }
QLabel#floatMuted { color: #6B746E; font-size: 12px; }
QLabel#floatReply { color: #1F2421; font-size: 12px; }
QPushButton {
    background: #FFFFFF; border: 1px solid #CFCBC0; border-radius: 6px;
    padding: 4px 8px; color: #1F2421; font-size: 12px;
}
QPushButton:hover { background: #EEF3EF; }
QPushButton#floatPrimary {
    background: #176B5B; border-color: #176B5B; color: #FFFFFF; font-weight: 600;
}
QPushButton#floatPrimary:disabled { background: #A9C4BA; border-color: #A9C4BA; }
QPushButton#floatGhost { border: 0; background: transparent; padding: 2px 6px; }
"""

COLLAPSED = 62
EXPANDED_W, EXPANDED_H = 390, 500


class FloatingAssistant(QWidget):
    """Draggable ball that expands to the AI-suggestion panel."""

    def __init__(self, host, parent=None):
        super().__init__(parent, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.host = host
        self._drag_offset = None
        self._dragged = False
        self._expanded = False
        self.setWindowTitle("ChatAssistant · 悬浮球")
        self.setObjectName("floatRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(BALL_STYLE)
        self.setFixedSize(COLLAPSED, COLLAPSED)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # -- collapsed ball -------------------------------------------------
        self.ball = QFrame()
        self.ball.setObjectName("ballFrame")
        ball_layout = QVBoxLayout(self.ball)
        ball_layout.setContentsMargins(4, 4, 4, 4)
        self.ball_label = QLabel("AI")
        self.ball_label.setObjectName("ballText")
        self.ball_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ball_layout.addWidget(self.ball_label)
        root.addWidget(self.ball)

        # -- expanded panel -------------------------------------------------
        self.panel = QFrame()
        self.panel.setObjectName("floatPanel")
        self.panel.setVisible(False)
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(12, 8, 12, 10)
        panel_layout.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("ChatAssistant")
        title.setObjectName("floatTitle")
        header.addWidget(title, 1)
        self.collapse_button = QPushButton("−")
        self.collapse_button.setObjectName("floatGhost")
        self.collapse_button.setFixedWidth(24)
        self.collapse_button.setToolTip("收起为悬浮球")
        self.collapse_button.clicked.connect(self.collapse)
        header.addWidget(self.collapse_button)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("floatGhost")
        self.settings_button.setToolTip("打开主界面设置/历史")
        self.settings_button.clicked.connect(self.show_main_settings)
        header.addWidget(self.settings_button)
        self.quit_button = QPushButton("退出")
        self.quit_button.setObjectName("floatGhost")
        self.quit_button.setToolTip("退出 ChatAssistant")
        self.quit_button.clicked.connect(self._quit)
        header.addWidget(self.quit_button)
        panel_layout.addLayout(header)

        self.name_label = QLabel("尚未连接会话")
        self.name_label.setObjectName("floatName")
        self.name_label.setWordWrap(False)
        panel_layout.addWidget(self.name_label)

        self.status_label = QLabel("等待微信窗口")
        self.status_label.setObjectName("floatMuted")
        self.status_label.setWordWrap(True)
        panel_layout.addWidget(self.status_label)

        self.analysis_label = QLabel("等待分析")
        self.analysis_label.setObjectName("floatMuted")
        self.analysis_label.setWordWrap(True)
        panel_layout.addWidget(self.analysis_label)

        self.analyze_button = QPushButton("分析当前对话")
        self.analyze_button.setObjectName("floatPrimary")
        self.analyze_button.setToolTip("对当前原生微信窗口的可见对话进行分析")
        self.analyze_button.clicked.connect(self._analyze)
        panel_layout.addWidget(self.analyze_button)

        self.reply_labels, self.copy_buttons, self.send_buttons = [], [], []
        for index in range(3):
            frame = QFrame()
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(0, 2, 0, 2)
            frame_layout.setSpacing(3)
            text = QLabel("等待分析")
            text.setObjectName("floatReply")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            frame_layout.addWidget(text)
            row = QHBoxLayout()
            copy = QPushButton("复制")
            copy.setEnabled(False)
            copy.clicked.connect(lambda _=False, i=index: self._copy(i))
            send = QPushButton("发送到微信")
            send.setEnabled(False)
            send.clicked.connect(lambda _=False, i=index: self._send(i))
            row.addWidget(copy)
            row.addWidget(send)
            row.addStretch(1)
            frame_layout.addLayout(row)
            panel_layout.addWidget(frame)
            self.reply_labels.append(text)
            self.copy_buttons.append(copy)
            self.send_buttons.append(send)
        panel_layout.addStretch(1)
        self.hint_label = QLabel("只监控原生微信窗口 · 设置/历史在主界面")
        self.hint_label.setObjectName("floatMuted")
        self.hint_label.setWordWrap(True)
        panel_layout.addWidget(self.hint_label)
        root.addWidget(self.panel, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(600)
        self.refresh()

    # -- state --------------------------------------------------------------
    def refresh(self):
        host = self.host
        if host is None:
            return
        try:
            self.name_label.setText(host.chat_title.text() or "尚未连接会话")
            self.status_label.setText(host.connection_status.text() or "等待微信窗口")
            self.analysis_label.setText(host.analysis_status.text() or "等待分析")
            ready = bool(getattr(getattr(host, "state", None), "ready", False))
            busy = bool(getattr(host, "busy", False))
            self.analyze_button.setEnabled(ready and not busy)
            self.analyze_button.setToolTip(
                "分析当前可见对话" if ready else "等待识别到可直接分析的聊天文字")
            for index, label in enumerate(self.reply_labels):
                text = host.reply_boxes[index].text() if index < len(host.reply_boxes) else ""
                label.setText(text or "等待分析")
                self.copy_buttons[index].setEnabled(bool(text) and text != "等待分析")
                sent_ok = (index < len(host.send_buttons)
                           and host.send_buttons[index].isEnabled() and text
                           and text != "等待分析")
                self.send_buttons[index].setEnabled(bool(sent_ok))
            server = getattr(host, "settings_page", None)
            if server is not None:
                self.send_buttons = self.send_buttons  # keep layout stable
            if self._expanded:
                self._fit_expanded()
        except RuntimeError:
            self._timer.stop()

    def _fit_expanded(self):
        self.setFixedSize(EXPANDED_W, EXPANDED_H)
        self._place_near_current_position()

    def _place_near_current_position(self):
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = self.x()
        y = self.y()
        x = max(area.left() + 8, min(x, area.right() - self.width() - 8))
        y = max(area.top() + 8, min(y, area.bottom() - self.height() - 8))
        self.move(x, y)

    # -- expand/collapse ----------------------------------------------------
    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self.ball.setVisible(False)
        self.panel.setVisible(True)
        self._fit_expanded()
        self.refresh()

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self.panel.setVisible(False)
        self.ball.setVisible(True)
        self.setFixedSize(COLLAPSED, COLLAPSED)

    def toggle(self):
        if self._expanded:
            self.collapse()
        else:
            self.expand()

    # -- actions ------------------------------------------------------------
    def _analyze(self):
        try:
            self.host.start_analysis()
        except RuntimeError:
            return

    def _copy(self, index):
        try:
            self.host.copy_reply(index)
        except (RuntimeError, AttributeError):
            return

    def _send(self, index):
        try:
            self.host.send_reply_to_wechat(index)
        except (RuntimeError, AttributeError):
            return

    def show_main(self):
        self.show_main_settings()

    def show_main_settings(self):
        # Keep the ball visible (collapsed) while the settings/history main window
        # is open; only the chat page is gone in ball mode.
        self.collapse()
        try:
            self.host.show()
            self.host.show_settings()
            self.host.raise_()
            self.host.activateWindow()
        except RuntimeError:
            pass

    def _quit(self):
        self._timer.stop()
        try:
            self.host.config["floating_ball"] = False
            from . import settings
            settings.save(self.host.config)
        except Exception:  # noqa: BLE001 - best-effort while exiting
            pass
        self.hide()
        try:
            self.host.close()
        except RuntimeError:
            pass

    # -- dragging / ball click ---------------------------------------------
    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self._dragged = True
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            dragged, self._dragged = self._dragged, False
            self._drag_offset = None
            if not dragged:
                self.toggle()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self._timer.stop()
        self.host = None
        event.accept()
