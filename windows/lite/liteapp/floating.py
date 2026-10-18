"""Floating assistant ball with per-contact affection.

The ball is the primary compact interface: a draggable always-on-top circular
button showing the current contact's affection score. Expanding it reveals live
status, a per-contact profile summary, the AI suggestions and actions. The main
window keeps settings/history only.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QColor, QLinearGradient, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

BALL_STYLE = """
QFrame#floatPanel {
    background: #F7F5EE; border: 1px solid #D5D0C4; border-radius: 14px;
}
QLabel#floatTitle { color: #1F2421; font-size: 15px; font-weight: 600; }
QLabel#floatName { color: #176B5B; font-size: 15px; font-weight: 700; }
QLabel#floatScore { color: #176B5B; font-size: 22px; font-weight: 800; }
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
QPushButton#floatFavorite { border-color: #B6D6C9; color: #176B5B; }
QPushButton#floatGhost { border: 0; background: transparent; padding: 2px 6px; }
"""

COLLAPSED = 68
EXPANDED_W, EXPANDED_H = 400, 560


class BallFace(QWidget):
    """Circular gradient face with an affection arc and score."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(COLLAPSED, COLLAPSED)
        self._score = None
        self._favorite = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_state(self, score=None, favorite=False):
        self._score = score
        self._favorite = favorite
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(4, 4, self.width() - 8, self.height() - 8)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor("#1B806B"))
        gradient.setColorAt(1.0, QColor("#0F483D"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(rect)

        track_pen = QPen(QColor(255, 255, 255, 55), 4)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        arc = QRectF(rect.left() + 5, rect.top() + 5, rect.width() - 10, rect.height() - 10)
        painter.drawArc(arc, 90 * 16, -360 * 16)
        if self._score is not None:
            progress_pen = QPen(QColor("#9BE7C9"), 4)
            progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(progress_pen)
            span = max(0, min(100, int(self._score))) * 360 / 100
            painter.drawArc(arc, 90 * 16, -int(span * 16))

        painter.setPen(QColor("#FFFFFF"))
        font = painter.font()
        font.setPointSizeF(15 if self._score is None else 13)
        font.setBold(True)
        painter.setFont(font)
        text = "AI" if self._score is None else str(int(self._score))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        if self._favorite:
            heart_font = painter.font()
            heart_font.setPointSizeF(8)
            painter.setFont(heart_font)
            painter.setPen(QColor("#9BE7C9"))
            painter.drawText(QRectF(rect.left(), rect.bottom() - 20, rect.width(), 16),
                             Qt.AlignmentFlag.AlignCenter, "♥ 已固定")


class FloatingAssistant(QWidget):
    """Draggable ball expanding to status + affection + AI suggestions."""

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
        self.ball = BallFace()
        root.addWidget(self.ball)

        self.panel = QFrame()
        self.panel.setObjectName("floatPanel")
        self.panel.setVisible(False)
        panel = QVBoxLayout(self.panel)
        panel.setContentsMargins(12, 8, 12, 10)
        panel.setSpacing(6)

        header = QHBoxLayout()
        title = QLabel("ChatAssistant")
        title.setObjectName("floatTitle")
        header.addWidget(title, 1)
        self.favorite_button = QPushButton("固定")
        self.favorite_button.setObjectName("floatFavorite")
        self.favorite_button.setToolTip("只自动分析固定联系人；其他联系人需点分析")
        self.favorite_button.clicked.connect(self._toggle_favorite)
        header.addWidget(self.favorite_button)
        self.collapse_button = QPushButton("−")
        self.collapse_button.setObjectName("floatGhost")
        self.collapse_button.setFixedWidth(24)
        self.collapse_button.clicked.connect(self.collapse)
        header.addWidget(self.collapse_button)
        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("floatGhost")
        self.settings_button.clicked.connect(self.show_main_settings)
        header.addWidget(self.settings_button)
        self.profile_button = QPushButton("档案")
        self.profile_button.setObjectName("floatGhost")
        self.profile_button.setToolTip("在历史页查看/生成该联系人的专属风格与画像")
        self.profile_button.clicked.connect(self.show_main_history)
        header.addWidget(self.profile_button)
        self.quit_button = QPushButton("退出")
        self.quit_button.setObjectName("floatGhost")
        self.quit_button.clicked.connect(self._quit)
        header.addWidget(self.quit_button)
        panel.addLayout(header)

        name_row = QHBoxLayout()
        self.name_label = QLabel("尚未连接会话")
        self.name_label.setObjectName("floatName")
        name_row.addWidget(self.name_label, 1)
        self.affection_label = QLabel("好感度 —")
        self.affection_label.setObjectName("floatScore")
        name_row.addWidget(self.affection_label)
        panel.addLayout(name_row)

        self.affection_detail = QLabel("等待识别到聊天后计算本地好感度")
        self.affection_detail.setObjectName("floatMuted")
        self.affection_detail.setWordWrap(True)
        panel.addWidget(self.affection_detail)
        self.profile_label = QLabel("专属档案：未生成")
        self.profile_label.setObjectName("floatMuted")
        self.profile_label.setWordWrap(True)
        panel.addWidget(self.profile_label)
        self.model_label = QLabel("模型档案：待分析")
        self.model_label.setObjectName("floatMuted")
        self.model_label.setWordWrap(True)
        panel.addWidget(self.model_label)

        self.status_label = QLabel("等待微信窗口")
        self.status_label.setObjectName("floatMuted")
        self.status_label.setWordWrap(True)
        panel.addWidget(self.status_label)

        self.analyze_button = QPushButton("分析当前对话")
        self.analyze_button.setObjectName("floatPrimary")
        self.analyze_button.clicked.connect(self._analyze)
        panel.addWidget(self.analyze_button)
        self.model_button = QPushButton("模型分析档案")
        self.model_button.setToolTip("对已导入的完整历史分块调用模型，逐句判断情绪/意图并生成档案")
        self.model_button.clicked.connect(self._model_analyze)
        panel.addWidget(self.model_button)

        self.reply_labels, self.copy_buttons, self.send_buttons = [], [], []
        for index in range(3):
            frame = QFrame()
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(0, 1, 0, 1)
            frame_layout.setSpacing(2)
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
            panel.addWidget(frame)
            self.reply_labels.append(text)
            self.copy_buttons.append(copy)
            self.send_buttons.append(send)
        panel.addStretch(1)
        hint = QLabel("只监控原生微信窗口 · 设置/历史在主界面；好感度是本地估算，仅供参考")
        hint.setObjectName("floatMuted")
        hint.setWordWrap(True)
        panel.addWidget(hint)
        root.addWidget(self.panel, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(700)
        self.refresh()

    # -- state --------------------------------------------------------------
    def refresh(self):
        host = self.host
        if host is None:
            return
        try:
            title = host.current_contact_title() if hasattr(host, "current_contact_title") else ""
            self.name_label.setText(title or "尚未连接会话")
            self.status_label.setText(host.connection_status.text() or "等待微信窗口")
            ready = bool(getattr(getattr(host, "state", None), "ready", False))
            busy = bool(getattr(host, "busy", False))
            self.analyze_button.setEnabled(ready and not busy)
            favorite = host.is_favorite_contact(title) if hasattr(host, "is_favorite_contact") else False
            self.favorite_button.setText("已固定" if favorite else "固定")
            score = None
            detail = "等待识别到聊天后计算本地好感度"
            if hasattr(host, "affection_for_current"):
                profile = host.affection_for_current()
                if profile is not None:
                    score = int(profile.score)
                    dims = " · ".join(
                        f"{d.label}{int(round(d.level + 1))}/5"
                        for d in profile.dimensions[:3])
                    if profile.boundary_applied:
                        detail = f"{profile.level_label} · 对方边界明确，分数封顶 25"
                    elif profile.status == "insufficient":
                        detail = f"{profile.level_label} · 样本不足，仅供参考"
                    else:
                        detail = f"{profile.level_label} · {dims} · 本地估算"
            self.affection_label.setText(f"好感度 {score}" if score is not None else "好感度 —")
            self.affection_detail.setText(detail)
            profile = None
            if hasattr(host, "contact_profile_for_current"):
                profile = host.contact_profile_for_current()
            profile_parts = []
            if profile is not None:
                if profile.relation:
                    profile_parts.append(f"关系：{profile.relation}")
                if profile.topics:
                    profile_parts.append(f"话题：{profile.topics}")
                if profile.preferences:
                    profile_parts.append(f"偏好：{profile.preferences}")
                if profile.taboos:
                    profile_parts.append(f"禁忌：{profile.taboos}")
                if profile.important_dates:
                    profile_parts.append(f"日期：{profile.important_dates}")
            if profile_parts:
                prefix = "模型档案" if getattr(profile, "model_analyzed", False) else "基础档案"
                self.profile_label.setText(prefix + " · " + "；".join(profile_parts))
            elif profile is not None:
                self.profile_label.setText("专属档案：已建立，等待生成画像")
            else:
                self.profile_label.setText("专属档案：未生成（导入后自动建立）")
            model_text = ""
            if getattr(host, "_model_busy", False):
                model_text = getattr(host, "_model_progress_text", "") or "模型逐句分析进行中…"
            elif getattr(host, "_model_progress_text", ""):
                model_text = host._model_progress_text
            elif profile is not None and getattr(profile, "model_analyzed", False):
                model_text = "模型档案：已逐句分析"
            else:
                model_text = "模型档案：待分析（导入后自动分块分析）"
            self.model_label.setText(model_text)
            self.model_button.setEnabled(not getattr(host, "_model_busy", False))
            self.ball.set_state(score, favorite)
            for index, label in enumerate(self.reply_labels):
                text = host.reply_boxes[index].text() if index < len(host.reply_boxes) else ""
                label.setText(text or "等待分析")
                self.copy_buttons[index].setEnabled(bool(text) and text != "等待分析")
                self.send_buttons[index].setEnabled(
                    index < len(host.send_buttons)
                    and host.send_buttons[index].isEnabled()
                    and bool(text) and text != "等待分析")
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
        x = max(area.left() + 8, min(self.x(), area.right() - self.width() - 8))
        y = max(area.top() + 8, min(self.y(), area.bottom() - self.height() - 8))
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
        self.collapse() if self._expanded else self.expand()

    # -- actions ------------------------------------------------------------
    def _analyze(self):
        try:
            self.host.start_analysis()
        except RuntimeError:
            return

    def _model_analyze(self):
        try:
            self.host.start_model_profile_analysis()
        except (RuntimeError, AttributeError):
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

    def _toggle_favorite(self):
        try:
            self.host.toggle_favorite_contact()
        except (RuntimeError, AttributeError):
            return
        self.refresh()

    def show_main(self):
        self.show_main_settings()

    def show_main_settings(self):
        self.collapse()
        try:
            self.host.show()
            self.host.show_settings()
            self.host.raise_()
            self.host.activateWindow()
        except RuntimeError:
            pass

    def show_main_history(self):
        self.collapse()
        try:
            self.host.show()
            self.host.show_history()
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
        except Exception:  # noqa: BLE001 - best effort while exiting
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
