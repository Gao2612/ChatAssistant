"""Floating assistant ball with per-contact affection.

The collapsed ball is the primary compact surface; expanding it opens a small
popover-style workspace: identity + affection, actions, structured profile and
the AI suggestion cards. The main window keeps settings/history only.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QAction, QColor, QLinearGradient, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMenu, QPushButton,
    QScrollArea, QStackedWidget, QVBoxLayout, QWidget,
)

FLOAT_STYLE = """
QWidget#floatRoot { background: transparent; }
QFrame#floatPanel {
    background: #F7F5EE;
    border: 1px solid #D5D0C4;
    border-radius: 16px;
}
QFrame#floatCard {
    background: #FFFFFF;
    border: 1px solid #E4E0D6;
    border-radius: 10px;
}
QLabel#brandLabel { color: #5E665F; font-size: 11px; letter-spacing: 1px; }
QLabel#floatName { color: #1A2621; font-size: 16px; font-weight: 700; }
QLabel#floatStatus { color: #6B746E; font-size: 11px; }
QLabel#floatScore { color: #176B5B; font-size: 17px; font-weight: 800; }
QLabel#floatLevel { color: #176B5B; font-size: 12px; font-weight: 600; }
QLabel#floatMuted { color: #636B65; font-size: 12px; }
QLabel#floatTiny { color: #6C746E; font-size: 11px; }
QLabel#floatReply { color: #1F2421; font-size: 12px; }
QLabel#floatBadge {
    color: #176B5B; background: #E6F2EC; border-radius: 8px;
    font-size: 10px; font-weight: 700; padding: 1px 7px;
}
QLabel#floatInitial {
    color: #FFFFFF; background: #176B5B; border-radius: 16px;
    font-size: 14px; font-weight: 700;
}
QPushButton {
    background: #FFFFFF; border: 1px solid #D2CEC3; border-radius: 8px;
    padding: 5px 10px; color: #1F2421; font-size: 12px;
}
QPushButton:hover { background: #EEF3EF; border-color: #B9D3C7; }
QPushButton:focus { border: 2px solid #176B5B; }
QPushButton#floatPrimary:focus { border: 2px solid #0B3A32; }
QPushButton#floatSecondary:focus, QPushButton#floatFavorite:focus,
QPushButton#floatGhost:focus, QPushButton#floatSmall:focus {
    border: 1px solid #176B5B; background: #EEF3EF;
}
QPushButton:pressed { background: #E2ECE6; }
QPushButton#floatPrimary:disabled {
    background: #DCE8E2; border-color: #DCE8E2; color: #426055;
}
QPushButton#floatSecondary:disabled, QPushButton#floatSmall:disabled,
QPushButton#floatFavorite:disabled, QPushButton#floatGhost:disabled {
    background: #F0EEE8; border-color: #E2DED3; color: #8A948D;
}
QPushButton#floatPrimary {
    background: #176B5B; border: 1px solid #176B5B; color: #FFFFFF;
    font-weight: 700; min-height: 34px;
}
QPushButton#floatPrimary:hover { background: #1C7C69; }
QPushButton#floatPrimary:disabled { background: #A9C4BA; border-color: #A9C4BA; }
QPushButton#floatSecondary { color: #176B5B; border-color: #B6D6C9; min-height: 34px; }
QPushButton#floatFavorite { color: #176B5B; border-color: #B6D6C9; padding: 3px 10px; }
QPushButton#floatGhost { border: 0; background: transparent; padding: 2px 6px; color: #5E665F; }
QPushButton#floatGhost:hover { background: #ECE9E0; }
QPushButton#floatSmall { padding: 2px 8px; font-size: 11px; }
QScrollArea#floatScroll { background: transparent; border: 0; }
QWidget#floatScrollBody { background: transparent; }
"""

COLLAPSED = 68
EXPANDED_W, EXPANDED_H = 412, 624


class BallFace(QWidget):
    """Circular gradient face with an affection ring and score."""

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
        rect = QRectF(3, 3, self.width() - 6, self.height() - 6)
        gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
        gradient.setColorAt(0.0, QColor("#1B806B"))
        gradient.setColorAt(1.0, QColor("#0B3A32"))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(rect)
        arc = QRectF(rect.left() + 5, rect.top() + 5,
                     rect.width() - 10, rect.height() - 10)
        track_pen = QPen(QColor(255, 255, 255, 48), 4)
        track_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(track_pen)
        painter.drawArc(arc, 90 * 16, -360 * 16)
        if self._score is not None:
            progress_pen = QPen(QColor("#9BE7C9"), 4)
            progress_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(progress_pen)
            span = max(0, min(100, int(self._score))) * 360 / 100
            painter.drawArc(arc, 90 * 16, -int(span * 16))
        core = rect.adjusted(10, 10, -10, -10)
        painter.setPen(QPen(QColor(255, 255, 255, 120), 1.2))
        painter.setBrush(QColor("#F7F5EE"))
        painter.drawEllipse(core)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(23, 107, 91, 18))
        painter.drawEllipse(QRectF(core.left() + 6, core.top() + 7,
                                   core.width() - 12, core.height() * 0.42))
        painter.setPen(QColor("#176B5B"))
        font = painter.font()
        font.setPointSizeF(16 if self._score is None else 17)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(core, Qt.AlignmentFlag.AlignCenter,
                         "AI" if self._score is None else str(int(self._score)))
        if self._favorite:
            badge = QRectF(rect.right() - 24, rect.bottom() - 24, 20, 20)
            painter.setPen(QPen(QColor("#F7F5EE"), 2))
            painter.setBrush(QColor("#9BE7C9"))
            painter.drawEllipse(badge)
            heart_font = painter.font()
            heart_font.setPointSizeF(8)
            painter.setFont(heart_font)
            painter.setPen(QColor("#0F483D"))
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, "♥")


class AffectionMeter(QWidget):
    """Compact 60px affection arc used in the expanded header."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(60, 60)
        self._score = None
        self.setAccessibleName("好感度")

    def set_score(self, score):
        self._score = score
        self.setAccessibleDescription(
            "暂无好感度" if score is None else f"好感度 {int(score)} 分（满分 100）")
        self.update()

    def paintEvent(self, event):  # noqa: N802 - Qt API
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(5, 5, 50, 50)
        painter.setPen(QPen(QColor("#E1E8E3"), 5))
        painter.drawArc(rect, 90 * 16, -360 * 16)
        if self._score is not None:
            painter.setPen(QPen(QColor("#176B5B"), 5, Qt.PenStyle.SolidLine,
                                Qt.PenCapStyle.RoundCap))
            span = max(0, min(100, int(self._score))) * 360 / 100
            painter.drawArc(rect, 90 * 16, -int(span * 16))
        painter.setPen(QColor("#176B5B"))
        font = painter.font()
        font.setPointSizeF(12 if self._score is not None else 9)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                         "—" if self._score is None else str(int(self._score)))


class FloatingAssistant(QWidget):
    """Draggable ball expanding to identity/affection/actions/professional cards."""

    def __init__(self, host, parent=None):
        super().__init__(parent, Qt.WindowType.Tool
                         | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.host = host
        self._drag_offset = None
        self._dragged = False
        self._expanded = False
        self._menu_open = False
        self._press_global = None
        self._press_origin = None
        self.setWindowTitle("ChatAssistant · 悬浮球")
        self.setObjectName("floatRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAccessibleName("ChatAssistant 悬浮球")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(FLOAT_STYLE)
        self.setFixedSize(COLLAPSED, COLLAPSED)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.ball = BallFace()
        self.ball.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        root.addWidget(self.ball)

        self.panel = QFrame()
        self.panel.setObjectName("floatPanel")
        self.panel.setVisible(False)
        panel = QVBoxLayout(self.panel)
        panel.setContentsMargins(14, 10, 14, 10)
        panel.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.avatar = QLabel("聊")
        self.avatar.setObjectName("floatInitial")
        self.avatar.setFixedSize(32, 32)
        self.avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self.avatar)
        identity = QVBoxLayout()
        identity.setSpacing(0)
        brand = QLabel("微信 · 本地分析")
        brand.setObjectName("brandLabel")
        identity.addWidget(brand)
        self.name_label = QLabel("尚未连接会话")
        self.name_label.setObjectName("floatName")
        identity.addWidget(self.name_label)
        header.addLayout(identity, 1)
        self.favorite_button = QPushButton("固定")
        self.favorite_button.setObjectName("floatFavorite")
        self.favorite_button.setToolTip("只自动分析固定联系人；其他联系人需点分析")
        self.favorite_button.setAccessibleName("固定当前联系人")
        self.favorite_button.clicked.connect(self._toggle_favorite)
        header.addWidget(self.favorite_button)
        self.more_button = QPushButton("设置")
        self.more_button.setObjectName("floatGhost")
        self.more_button.setToolTip("打开设置与模型")
        self.more_button.setAccessibleName("设置")
        self.more_button.clicked.connect(self.show_main_settings)
        header.addWidget(self.more_button)
        self.collapse_button = QPushButton("−")
        self.collapse_button.setObjectName("floatGhost")
        self.collapse_button.setFixedWidth(24)
        self.collapse_button.setToolTip("收起为悬浮球")
        self.collapse_button.setAccessibleName("收起悬浮球")
        self.collapse_button.clicked.connect(self.collapse)
        header.addWidget(self.collapse_button)
        self.quit_button = QPushButton("×")
        self.quit_button.setObjectName("floatGhost")
        self.quit_button.setFixedWidth(24)
        self.quit_button.setToolTip("关闭悬浮球并退出")
        self.quit_button.setAccessibleName("关闭并退出")
        self.quit_button.clicked.connect(self._quit)
        header.addWidget(self.quit_button)
        panel.addLayout(header)

        affection = QFrame()
        affection.setObjectName("floatCard")
        affection_layout = QHBoxLayout(affection)
        affection_layout.setContentsMargins(10, 8, 10, 8)
        affection_layout.setSpacing(10)
        self.affection_meter = AffectionMeter()
        affection_layout.addWidget(self.affection_meter)
        affection_text = QVBoxLayout()
        affection_text.setSpacing(2)
        score_row = QHBoxLayout()
        score_row.setSpacing(6)
        self.affection_label = QLabel("好感度 —")
        self.affection_label.setObjectName("floatScore")
        score_row.addWidget(self.affection_label)
        self.level_badge = QLabel("待识别")
        self.level_badge.setObjectName("floatBadge")
        score_row.addWidget(self.level_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        score_row.addStretch(1)
        affection_text.addLayout(score_row)
        self.affection_detail = QLabel("等待识别到聊天后计算本地好感度")
        self.affection_detail.setObjectName("floatMuted")
        self.affection_detail.setWordWrap(True)
        affection_text.addWidget(self.affection_detail)
        self.dimension_labels = []
        dimension_grid = QGridLayout()
        dimension_grid.setHorizontalSpacing(8)
        dimension_grid.setVerticalSpacing(1)
        for index in range(6):
            label = QLabel("")
            label.setObjectName("floatTiny")
            label.setToolTip("")
            dimension_grid.addWidget(label, index // 3, index % 3)
            self.dimension_labels.append(label)
        affection_text.addLayout(dimension_grid)
        affection_layout.addLayout(affection_text, 1)
        panel.addWidget(affection)

        self.status_label = QLabel("等待微信窗口")
        self.status_label.setObjectName("floatMuted")
        self.status_label.setWordWrap(True)
        panel.addWidget(self.status_label)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.analyze_button = QPushButton("分析当前对话")
        self.analyze_button.setObjectName("floatPrimary")
        self.analyze_button.clicked.connect(self._analyze)
        actions.addWidget(self.analyze_button, 1)
        self.model_button = QPushButton("模型分析档案")
        self.model_button.setObjectName("floatSecondary")
        self.model_button.setToolTip(
            "对已导入的完整历史分块调用模型，逐句判断情绪/意图并生成档案")
        self.model_button.clicked.connect(self._model_analyze)
        actions.addWidget(self.model_button, 1)
        panel.addLayout(actions)

        self.profile_card = QFrame()
        self.profile_card.setObjectName("floatCard")
        profile_layout = QVBoxLayout(self.profile_card)
        profile_layout.setContentsMargins(10, 8, 10, 8)
        profile_layout.setSpacing(4)
        profile_header = QHBoxLayout()
        profile_title = QLabel("专属档案")
        profile_title.setObjectName("floatLevel")
        profile_header.addWidget(profile_title)
        self.profile_badge = QLabel("未建立")
        self.profile_badge.setObjectName("floatBadge")
        profile_header.addWidget(self.profile_badge)
        profile_header.addStretch(1)
        self.open_profile_button = QPushButton("档案 ↗")
        self.open_profile_button.setObjectName("floatSmall")
        self.open_profile_button.setToolTip("打开历史与档案页，导入 HTML/PDF 或查看完整档案")
        self.open_profile_button.clicked.connect(self.show_main_history)
        profile_header.addWidget(self.open_profile_button)
        profile_layout.addLayout(profile_header)
        self.profile_label = QLabel("导入聊天记录后自动建立关系、话题与重要日期")
        self.profile_label.setObjectName("floatMuted")
        self.profile_label.setWordWrap(True)
        profile_layout.addWidget(self.profile_label)
        self.profile_extra = QLabel("")
        self.profile_extra.setObjectName("floatTiny")
        self.profile_extra.setWordWrap(True)
        profile_layout.addWidget(self.profile_extra)
        self.model_label = QLabel("模型档案：待分析")
        self.model_label.setObjectName("floatTiny")
        self.model_label.setWordWrap(True)
        profile_layout.addWidget(self.model_label)
        panel.addWidget(self.profile_card)

        suggestions_header = QHBoxLayout()
        suggestions_title = QLabel("AI 建议")
        suggestions_title.setObjectName("floatLevel")
        self.suggestions_title = suggestions_title
        suggestions_header.addWidget(suggestions_title)
        self.suggestion_count = QLabel("暂无")
        self.suggestion_count.setObjectName("floatTiny")
        suggestions_header.addWidget(self.suggestion_count)
        suggestions_header.addStretch(1)
        panel.addLayout(suggestions_header)

        self.empty_card = QFrame()
        self.empty_card.setObjectName("floatCard")
        empty_layout = QVBoxLayout(self.empty_card)
        empty_layout.setContentsMargins(12, 12, 12, 12)
        empty_layout.setSpacing(3)
        empty_title = QLabel("还没有新的建议")
        empty_title.setObjectName("floatLevel")
        empty_layout.addWidget(empty_title)
        empty_hint = QLabel("点上方“分析当前对话”，会生成 3 条可直接复制的回复。")
        empty_hint.setObjectName("floatMuted")
        empty_hint.setWordWrap(True)
        self.empty_hint = empty_hint
        empty_layout.addWidget(empty_hint)

        self.suggestions_scroll = QScrollArea()
        self.suggestions_scroll.setObjectName("floatScroll")
        self.suggestions_scroll.setWidgetResizable(True)
        self.suggestions_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.suggestions_body = QWidget()
        self.suggestions_body.setObjectName("floatScrollBody")
        self.suggestions_layout = QVBoxLayout(self.suggestions_body)
        self.suggestions_layout.setContentsMargins(0, 0, 0, 0)
        self.suggestions_layout.setSpacing(6)
        self.suggestions_scroll.setWidget(self.suggestions_body)

        # Empty state and populated suggestion list swap in one stable slot; this
        # avoids reserving a large blank stripe when no analysis is available.
        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.empty_card)
        self.content_stack.addWidget(self.suggestions_scroll)
        panel.addWidget(self.content_stack, 1)

        self.reply_labels, self.copy_buttons, self.send_buttons = [], [], []
        self.reply_cards = []
        for index in range(3):
            card = QFrame()
            card.setObjectName("floatCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(9, 7, 9, 7)
            card_layout.setSpacing(4)
            text = QLabel("等待分析")
            text.setObjectName("floatReply")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            card_layout.addWidget(text)
            buttons = QHBoxLayout()
            buttons.setSpacing(6)
            copy = QPushButton("复制")
            copy.setObjectName("floatSmall")
            copy.setEnabled(False)
            copy.clicked.connect(lambda _=False, i=index: self._copy(i))
            send = QPushButton("发送到微信")
            send.setObjectName("floatSmall")
            send.setEnabled(False)
            send.clicked.connect(lambda _=False, i=index: self._send(i))
            buttons.addWidget(copy)
            buttons.addWidget(send)
            buttons.addStretch(1)
            card_layout.addLayout(buttons)
            self.suggestions_layout.addWidget(card)
            self.reply_cards.append(card)
            self.reply_labels.append(text)
            self.copy_buttons.append(copy)
            self.send_buttons.append(send)
        self.suggestions_layout.addStretch(1)

        footer = QLabel("只监控原生微信窗口 · 好感度为本地估算，仅供参考")
        footer.setObjectName("floatTiny")
        footer.setWordWrap(True)
        panel.addWidget(footer)
        root.addWidget(self.panel, 1)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(700)
        self.refresh()

    def _show_menu(self):
        menu = QMenu(self)
        menu.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        settings = QAction("设置与模型", menu)
        settings.triggered.connect(self.show_main_settings)
        menu.addAction(settings)
        profile = QAction("历史与档案", menu)
        profile.triggered.connect(self.show_main_history)
        menu.addAction(profile)
        menu.addSeparator()
        quit_action = QAction("退出 ChatAssistant", menu)
        quit_action.triggered.connect(self._quit)
        menu.addAction(quit_action)
        self._menu_open = True
        try:
            menu.exec(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))
        finally:
            self._menu_open = False
            menu.deleteLater()

    @staticmethod
    def _clean_profile_value(value):
        """Render stored list/dict profile values as readable text, not repr."""
        if value is None:
            return ""
        if isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if str(item).strip()]
            return "、".join(items)
        text = str(value).strip()
        if text.startswith("[") and text.endswith("]"):
            inner = text[1:-1].replace("'", "").replace('"', "")
            return "、".join(item.strip() for item in inner.split(",") if item.strip())
        return text

    def refresh(self):
        host = self.host
        if host is None:
            return
        try:
            title = host.current_contact_title() if hasattr(host, "current_contact_title") else ""
            clean_title = str(title or "").strip()
            display_title = clean_title if len(clean_title) <= 16 else clean_title[:15] + "…"
            self.name_label.setText(display_title or "尚未连接会话")
            self.name_label.setToolTip(clean_title)
            self.name_label.setAccessibleName(clean_title or "未连接会话")
            self.avatar.setText((clean_title[:1] or "聊").upper())
            self.status_label.setText(host.connection_status.text() or "等待微信窗口")
            ready = bool(getattr(getattr(host, "state", None), "ready", False))
            busy = bool(getattr(host, "busy", False))
            self.analyze_button.setEnabled(ready and not busy)
            self.analyze_button.setText("分析中…" if busy else "分析当前对话")
            self.analyze_button.setToolTip(
                "正在分析当前对话" if busy else
                ("分析当前可见对话" if ready else "等待识别到可直接分析的聊天文字"))
            favorite = host.is_favorite_contact(title) if hasattr(host, "is_favorite_contact") else False
            self.favorite_button.setText("已固定" if favorite else "固定")
            self.favorite_button.setAccessibleName(
                "取消固定当前联系人" if favorite else "固定当前联系人（只自动分析固定联系人）")
            score = None
            detail = "等待识别到聊天后计算本地好感度"
            level = "待识别"
            dimensions = []
            if hasattr(host, "affection_for_current"):
                profile = host.affection_for_current()
                if profile is not None:
                    score = int(profile.score)
                    level = profile.level_label
                    dimensions = list(profile.dimensions)
                    if profile.boundary_applied:
                        detail = "对方边界明确，分数封顶 25"
                    elif profile.status == "insufficient":
                        detail = "样本不足，仅供参考"
                    else:
                        detail = "本地估算"
            self.affection_label.setText("好感度" if score is not None else "好感度 —")
            self.affection_detail.setText(detail)
            short_labels = ("主动", "投入", "关心", "开放", "亲密", "行动")
            for index, label in enumerate(self.dimension_labels):
                if index < len(dimensions):
                    dim = dimensions[index]
                    filled = max(0, min(5, int(round(dim.level + 1))))
                    label.setText(f"{short_labels[index]} {'●' * filled}{'○' * (5 - filled)}")
                    label.setToolTip(f"{dim.label}：{filled}/5")
                    label.setVisible(True)
                else:
                    label.setVisible(False)
            self.level_badge.setText(level[:6] if level else "待识别")
            self.affection_meter.set_score(score)
            self.ball.set_state(score, favorite)

            prof = None
            if hasattr(host, "contact_profile_for_current"):
                prof = host.contact_profile_for_current()
            parts, extras = [], []
            if prof is not None:
                if prof.relation:
                    parts.append(f"关系：{prof.relation}")
                for label, value in (("话题", getattr(prof, "topics", None)),
                                     ("偏好", getattr(prof, "preferences", None)),
                                     ("禁忌", getattr(prof, "taboos", None)),
                                     ("日期", getattr(prof, "important_dates", None))):
                    text = self._clean_profile_value(value)
                    if text:
                        extras.append(f"{label}：{text}")
                self.profile_badge.setText(
                    "模型档案" if getattr(prof, "model_analyzed", False) else "基础档案")
                self.profile_badge.setStyleSheet("")
                self.profile_label.setText(" · ".join(parts) or "档案已建立，等待生成画像")
                self.profile_extra.setText(" · ".join(extras))
                self.profile_extra.setVisible(bool(extras))
            else:
                self.profile_badge.setText("未建立")
                self.profile_badge.setStyleSheet(
                    "color:#5F6862;background:#ECE9E0;border-radius:8px;"
                    "font-size:10px;font-weight:700;padding:1px 7px;")
                self.profile_label.setText("导入后自动整理关系、话题与重要日期")
                self.profile_extra.setText("")
                self.profile_extra.setVisible(False)

            if getattr(host, "_model_busy", False):
                model_text = getattr(host, "_model_progress_text", "") or "模型逐句分析进行中…"
            elif getattr(host, "_model_progress_text", ""):
                model_text = host._model_progress_text
            elif prof is not None and getattr(prof, "model_analyzed", False):
                model_text = "模型档案：已逐句分析"
            else:
                model_text = "模型档案：待分析（导入后自动分块分析）"
            self.model_label.setText(model_text)
            self.model_button.setEnabled(not getattr(host, "_model_busy", False))

            present = []
            for index, label in enumerate(self.reply_labels):
                text = host.reply_boxes[index].text() if index < len(host.reply_boxes) else ""
                if text and text != "等待分析":
                    present.append(index)
                label.setText(text or "等待分析")
                self.copy_buttons[index].setEnabled(bool(text) and text != "等待分析")
                self.send_buttons[index].setEnabled(
                    index < len(host.send_buttons)
                    and host.send_buttons[index].isEnabled()
                    and bool(text) and text != "等待分析")
            self.empty_card.setVisible(not present)
            self.suggestions_scroll.setVisible(bool(present))
            self.content_stack.setCurrentWidget(
                self.suggestions_scroll if present else self.empty_card)
            for index, card in enumerate(self.reply_cards):
                card.setVisible(index in present)
            self.suggestion_count.setText(f"{len(present)} 条" if present else "暂无")
            self.suggestions_title.setText(
                f"给{display_title}的建议" if display_title and display_title != "尚未连接会话"
                else "AI 建议")
            self.empty_hint.setText(
                "正在识别新消息，建议会自动更新。"
                if (busy or not ready)
                else "点上方“分析当前对话”，会生成 3 条可直接复制的回复。")
            if self._expanded and not self._menu_open:
                self._fit_expanded()
        except RuntimeError:
            self._timer.stop()

    def _fit_expanded(self):
        # Drive height from the active content instead of a large magic constant;
        # clamp to the screen so high-DPI/small displays keep the footer visible.
        self.layout().activate()
        self.panel.layout().activate()
        hint = self.panel.sizeHint().height()
        target_h = max(470, min(EXPANDED_H, int(hint)))
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry().height() - 16
            target_h = min(target_h, max(420, available))
        target = (EXPANDED_W, target_h)
        if self.size().toTuple() != target:
            self.setFixedSize(*target)
            self._place_near_current_position()

    def _place_near_current_position(self):
        screen = self.screen()
        if screen is None:
            return
        area = screen.availableGeometry()
        x = max(area.left() + 8, min(self.x(), area.right() - self.width() - 8))
        y = max(area.top() + 8, min(self.y(), area.bottom() - self.height() - 8))
        self.move(x, y)

    def expand(self):
        if self._expanded:
            return
        self._expanded = True
        self.ball.setVisible(False)
        self.panel.setVisible(True)
        self._fit_expanded()
        self.refresh()
        self.analyze_button.setFocus()

    def collapse(self):
        if not self._expanded:
            return
        self._expanded = False
        self.panel.setVisible(False)
        self.ball.setVisible(True)
        self.setFixedSize(COLLAPSED, COLLAPSED)

    def toggle(self):
        self.collapse() if self._expanded else self.expand()

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

    def mousePressEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_global = event.globalPosition().toPoint()
            self._press_origin = self.frameGeometry().topLeft()
            self._drag_offset = self._press_global - self._press_origin
            self._dragged = False
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        if self._press_global is not None and event.buttons() & Qt.MouseButton.LeftButton:
            current = event.globalPosition().toPoint()
            if not self._dragged:
                if (current - self._press_global).manhattanLength() < QApplication.startDragDistance():
                    event.accept()
                    return
                self._dragged = True
            self.move(current - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            dragged, self._dragged = self._dragged, False
            self._drag_offset = None
            self._press_global = None
            self._press_origin = None
            # Clicking the collapsed ball expands it. In the expanded panel, blank
            # label/card clicks must not collapse the window accidentally.
            if not dragged and not self._expanded:
                self.expand()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):  # noqa: N802 - Qt API
        key = event.key()
        if key == Qt.Key.Key_Escape and self._expanded:
            self.collapse()
            event.accept()
            return
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            if not self._expanded:
                self.expand()
            else:
                self._analyze()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event):  # noqa: N802 - Qt API
        self._timer.stop()
        self.host = None
        event.accept()
