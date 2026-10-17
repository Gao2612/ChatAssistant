import multiprocessing as mp
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
import uuid
import webbrowser

from PySide6.QtCore import Qt, QTimer, QObject, Signal, QRectF, QPoint, QRect
from PySide6.QtGui import QPainter, QColor, QPen, QBrush, QPainterPath, QPixmap, QRegion
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QPlainTextEdit, QLineEdit, QComboBox, QCheckBox, QFormLayout,
    QFileDialog, QGroupBox, QScrollArea, QMessageBox,
    QFrame, QStackedWidget, QToolButton,
)
from . import settings
from .analysis import analyze
from .contracts import CancellationToken, SourceKey
from .state import ConversationState, FrameFreshness, choose_window
from .discovery import WindowTarget, discover, dpi
from .worker import run as capture_run
from . import account_store, analysis, backends, codex_backend, reading, subscription
from .history_ui import HistoryPage
from .inference import GatewayProfile, InferenceGateway
from .inference_queue import InferenceQueue
from .inference_schemas import (
    KIND_REALTIME_REPLY, InferenceAuthError, InferenceQuotaError, InferenceRequest,
)
from .window_manager import WindowManager, rect_to_physical

STYLE = """
QWidget { font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", sans-serif; font-size: 13px; color: #203044; }
QMainWindow, QDialog { background: #f3f6fa; }
QFrame#navigationRail { background: #202d3b; border: 0; }
QFrame#chatContent, QFrame#aiPanel { background: white; border: 1px solid #dfe6ef; border-radius: 10px; }
QToolButton { border: 0; border-radius: 9px; padding: 9px; color: #d7e2ef; }
QToolButton:hover, QToolButton:checked { background: #34485b; color: white; }
QPushButton#aiToggle { background: white; border: 1px solid #dfe6ef; color: #2469c9; font-weight: 600; }
QGroupBox { background: white; border: 1px solid #dfe6ef; border-radius: 9px;
 padding: 14px; margin-top: 15px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; }
QPushButton { background: #ffffff; border: 1px solid #cbd6e4; border-radius: 6px; padding: 8px 13px; }
QPushButton:hover { background: #eaf2fe; border-color: #769ac7; }
QPushButton:disabled { color: #9aa5b4; background: #f2f4f7; }
QPushButton#primary { color: white; background: #2469c9; border-color: #2469c9; font-weight: 600; }
QPushButton#primary:disabled { background: #a4bad9; border-color: #a4bad9; }
QLineEdit, QComboBox, QPlainTextEdit { background: white; border: 1px solid #d5deea; border-radius: 6px; padding: 8px; selection-background-color: #c7dfff; }
QLabel#muted { color: #65778c; }
QLabel#title { font-size: 22px; font-weight: 700; }
QCheckBox { spacing: 7px; }
"""


def label(text="", muted=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    if muted:
        widget.setObjectName("muted")
    return widget


class NoWheelComboBox(QComboBox):
    """Dropdown that never changes by scrolling the mouse wheel.

    On Windows it is easy to scroll the settings page and accidentally change the
    model/backend. Wheel events are ignored here (the page itself can still
    scroll); every choice is selected by click. The popup shows up to 20 rows so
    short lists never need inner scrolling.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMaxVisibleItems(20)

    def wheelEvent(self, event):  # noqa: N802 - Qt API name
        event.ignore()


class SettingsPage(QWidget):
    saved = Signal(object)
    cancel_requested = Signal()
    codex_done = Signal(object, str)
    models_done = Signal(object, str)
    disconnect_requested = Signal()
    account_action = Signal(str, str)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = dict(config)
        self.setObjectName("settingsPage")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 22, 24, 18)
        outer.setSpacing(12)
        title = label("设置")
        title.setObjectName("title")
        outer.addWidget(title)
        outer.addWidget(label("管理微信连接、模型和自动分析。", True))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        content = QWidget()
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 4, 8, 4)
        body.setSpacing(12)

        wechat = QGroupBox("微信连接")
        wechat_layout = QVBoxLayout(wechat)
        path_form = QFormLayout()
        self.path = QLineEdit(config.get("wechat_path", ""))
        browse = QPushButton("选择文件")
        browse.clicked.connect(self.browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(browse)
        path_form.addRow("微信程序路径", path_row)
        self.windows = NoWheelComboBox()
        path_form.addRow("微信窗口", self.windows)
        wechat_layout.addLayout(path_form)
        actions = QHBoxLayout()
        self.refresh_button = QPushButton("刷新窗口")
        self.connect_button = QPushButton("连接所选窗口")
        self.launch_button = QPushButton("启动微信")
        self.disconnect_button = QPushButton("断开连接")
        for button in (self.refresh_button, self.launch_button, self.connect_button, self.disconnect_button):
            actions.addWidget(button)
        wechat_layout.addLayout(actions)
        self.wechat_status = label("先启动并登录官方微信，再选择窗口。", True)
        wechat_layout.addWidget(self.wechat_status)
        body.addWidget(wechat)

        reading_group = QGroupBox("文字读取（屏幕 → 聊天文字）")
        reading_layout = QVBoxLayout(reading_group)
        reading_form = QFormLayout()
        self.reading_backend = NoWheelComboBox()
        for text, key in backends.READING_CHOICES:
            self.reading_backend.addItem(text, key)
        self.reading_backend.setCurrentIndex(max(0, self.reading_backend.findData(
            config.get("reading_backend", "ocr"))))
        self.reading_backend.currentIndexChanged.connect(self.reading_changed)
        self.reading_base = QLineEdit(config.get("reading_base_url", ""))
        self.reading_base.setPlaceholderText("多模态读取接口地址，例如 https://api.example.com/v1")
        self.reading_model = QLineEdit(config.get("reading_model", ""))
        self.reading_model.setPlaceholderText("多模态模型名称；Codex 读取留空用默认模型")
        self.reading_key = QLineEdit(config.get("reading_api_key", ""))
        self.reading_key.setEchoMode(QLineEdit.EchoMode.Password)
        reading_form.addRow("读取后端", self.reading_backend)
        reading_form.addRow("视觉接口地址", self.reading_base)
        reading_form.addRow("读取模型", self.reading_model)
        reading_form.addRow("读取 API Key", self.reading_key)
        reading_layout.addLayout(reading_form)
        reading_layout.addWidget(label(
            "本地 OCR 完全离线、不上传画面；选择多模态模型或 Codex 时，只有当前聊天画面会发送给"
            "所选服务用于转写文字。接入的聊天内容只作为数据处理，不会触发工具或发送消息。", True))
        body.addWidget(reading_group)

        model_group = QGroupBox("对话分析（聊天文字 → 判断与建议）")
        model_layout = QVBoxLayout(model_group)
        model_form = QFormLayout()
        self.backend = NoWheelComboBox()
        for text, key in backends.ANALYSIS_CHOICES:
            self.backend.addItem(text, key)
        self.backend.setCurrentIndex(max(0, self.backend.findData(config.get("backend", "chatgpt"))))
        self.provider = NoWheelComboBox()
        for text, key, _base in backends.API_PRESETS:
            self.provider.addItem(text, key)
        self.provider.setCurrentIndex(max(0, self.provider.findData(
            config.get("api_provider") or backends.match_preset(config.get("base_url", "")))))
        self.base = QLineEdit(config.get("base_url", ""))
        self.base.setPlaceholderText("兼容 API 地址；本地模型可填 http://127.0.0.1:11434/v1")
        self.key = QLineEdit(config.get("api_key", ""))
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.model = NoWheelComboBox()
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.fetch_models_button = QPushButton("读取模型列表")
        self.fetch_models_button.clicked.connect(self.fetch_models)
        self.model_row = QHBoxLayout()
        self.model_row.addWidget(self.model, 1)
        self.model_row.addWidget(self.fetch_models_button)
        self.model_status = label("", True)
        self.style = QLineEdit(config.get("style", ""))
        model_form.addRow("分析后端", self.backend)
        model_form.addRow("API 厂商", self.provider)
        model_form.addRow("兼容/本地服务地址", self.base)
        model_form.addRow("API Key", self.key)
        model_form.addRow("模型名称", self.model_row)
        model_form.addRow("回复风格", self.style)
        model_layout.addLayout(model_form)
        model_layout.addWidget(self.model_status)
        account_row = QHBoxLayout()
        account_row.addWidget(label("ChatGPT 账号", True))
        self.account_combo = NoWheelComboBox()
        self.account_combo.setMinimumWidth(240)
        self.account_switch_button = QPushButton("切换账号")
        self.account_reauth_button = QPushButton("重新授权")
        self.account_refresh_models_button = QPushButton("刷新模型")
        self.account_switch_button.clicked.connect(
            lambda: self._account_action("switch"))
        self.account_reauth_button.clicked.connect(
            lambda: self._account_action("reauth"))
        self.account_refresh_models_button.clicked.connect(
            lambda: self._account_action("refresh"))
        account_row.addWidget(self.account_combo, 1)
        account_row.addWidget(self.account_switch_button)
        account_row.addWidget(self.account_reauth_button)
        account_row.addWidget(self.account_refresh_models_button)
        model_layout.addLayout(account_row)
        self.backend.currentIndexChanged.connect(self.backend_changed)
        self.provider.currentIndexChanged.connect(self.provider_changed)
        self.models_done.connect(self.models_checked)
        self.base.editingFinished.connect(self.auto_fetch_models)
        self.key.editingFinished.connect(self.auto_fetch_models)
        auth_row = QHBoxLayout()
        self.chatgpt_button = QPushButton("连接 ChatGPT 套餐")
        self.usage_button = QPushButton("查看 ChatGPT Usage")
        self.usage_button.clicked.connect(lambda: webbrowser.open("https://chatgpt.com/settings/usage"))
        self.disconnect_chatgpt_button = QPushButton("断开 ChatGPT 授权")
        self.disconnect_chatgpt_button.clicked.connect(self.request_disconnect_chatgpt)
        self.disconnect_chatgpt_button.setVisible(False)
        auth_row.addWidget(self.chatgpt_button)
        auth_row.addWidget(self.usage_button)
        auth_row.addWidget(self.disconnect_chatgpt_button)
        model_layout.addLayout(auth_row)
        self.chatgpt_state = label("", True)
        model_layout.addWidget(self.chatgpt_state)
        self.auto_connect = QCheckBox("仅在发现唯一匹配窗口时自动连接")
        self.auto_connect.setChecked(bool(config.get("auto_connect", False)))
        self.auto_analysis = QCheckBox("自动分析新识别的对话")
        self.auto_analysis.setChecked(bool(config.get("auto_analysis", True)))
        model_layout.addWidget(self.auto_connect)
        model_layout.addWidget(self.auto_analysis)
        model_layout.addWidget(label("开启自动分析后，当前可见对话文本会发送给所选分析后端；"
                                     "助手只生成建议，不会代你发送微信消息。", True))
        model_layout.addWidget(label("ChatGPT 套餐需要在本应用单独授权，授权和模型会被记住；"
                                     "API Key 由 Windows DPAPI 加密保存。本地模型服务只连接本机地址。", True))
        effective_row = QHBoxLayout()
        self.effective_label = label("", True)
        self.card_save_button = QPushButton("保存设置")
        self.card_save_button.setObjectName("primary")
        self.card_save_button.clicked.connect(self.commit)
        effective_row.addWidget(self.effective_label, 1)
        effective_row.addWidget(self.card_save_button)
        model_layout.addLayout(effective_row)
        body.addWidget(model_group)

        codex_group = QGroupBox("Codex 账户（读取与分析的共用选项）")
        codex_layout = QVBoxLayout(codex_group)
        codex_form = QFormLayout()
        self.codex_path = QLineEdit(config.get("codex_path", ""))
        self.codex_path.setPlaceholderText("留空自动查找官方 Codex；也可填写 codex.exe 路径")
        codex_form.addRow("Codex 程序", self.codex_path)
        codex_layout.addLayout(codex_form)
        codex_row = QHBoxLayout()
        self.codex_button = QPushButton("检测 Codex 账号与模型")
        self.codex_button.clicked.connect(self.detect_codex)
        codex_row.addWidget(self.codex_button)
        codex_row.addStretch(1)
        codex_layout.addLayout(codex_row)
        self.codex_status = label("未检测。Codex 后端使用本机已登录的官方 Codex，"
                                  "在只读沙箱中只请求 JSON 结果，不读取桌面凭据。", True)
        codex_layout.addWidget(self.codex_status)
        self.codex_done.connect(self.codex_checked)
        body.addWidget(codex_group)

        self.reading_changed()
        self.backend_changed()
        self.update_chatgpt_state(config)
        self.refresh_effective(config)
        body.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        self.status = label()
        outer.addWidget(self.status)
        footer = QHBoxLayout()
        footer.addStretch(1)
        self.cancel_button = QPushButton("返回聊天")
        self.save_button = QPushButton("保存设置")
        self.save_button.setObjectName("primary")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        self.save_button.clicked.connect(self.commit)
        footer.addWidget(self.cancel_button)
        footer.addWidget(self.save_button)
        outer.addLayout(footer)
        self.error = label(muted=True)
        outer.addWidget(self.error)

    def browse(self):
        file, _ = QFileDialog.getOpenFileName(self, "选择官方微信", self.path.text(), "程序 (*.exe)")
        if file:
            self.path.setText(file)

    def reading_changed(self):
        backend = self.reading_backend.currentData()
        self.reading_base.setEnabled(backend == "api")
        self.reading_model.setEnabled(backend in ("api", "codex"))
        self.reading_key.setEnabled(backend == "api")

    def update_chatgpt_state(self, config):
        connected = backends.chatgpt_connected(config)
        account = config.get("subscription") or {}
        if connected:
            email = backends.masked_email(config)
            plan = str(account.get("planType") or "").strip()
            text = "已连接 ChatGPT" + (f"：{email}" if email else "") + (f"（{plan}）" if plan else "")
            self.chatgpt_button.setText("重新连接 ChatGPT")
        else:
            text = "未连接 ChatGPT。授权一次后会长期记住，只有授权失效或手动断开才需要重连。"
            self.chatgpt_button.setText("连接 ChatGPT 套餐")
        self.chatgpt_state.setText(text)
        self.disconnect_chatgpt_button.setVisible(connected)

    def set_accounts(self, registrations):
        """Render the ChatGPT registrations with their lifecycle status."""
        current = self.account_combo.currentData()
        self.account_combo.blockSignals(True)
        self.account_combo.clear()
        selected_index = 0
        status_text = {"connected": "已连接", "login_only": "仅登录",
                       "reauth_required": "需重新授权"}
        for registration in registrations or []:
            registration_id = str(registration.get("registration_id") or "")
            if not registration_id:
                continue
            label = str(registration.get("label") or "ChatGPT 账号")
            status = status_text.get(str(registration.get("status") or ""),
                                     str(registration.get("status") or ""))
            self.account_combo.addItem(f"{label} · {status}", registration_id)
            if registration.get("selected") or registration_id == current:
                selected_index = self.account_combo.count() - 1
        if self.account_combo.count():
            self.account_combo.setCurrentIndex(selected_index)
        self.account_combo.blockSignals(False)
        has_accounts = self.account_combo.count() > 0
        for button in (self.account_switch_button, self.account_reauth_button,
                       self.account_refresh_models_button):
            button.setEnabled(has_accounts)

    def _account_action(self, action):
        registration_id = self.account_combo.currentData()
        if action in ("switch", "reauth") and not registration_id:
            self.chatgpt_state.setText("请先连接或选择一个 ChatGPT 账号。")
            return
        self.account_action.emit(action, str(registration_id or ""))

    def refresh_effective(self, config=None):
        current = dict(config if config is not None else self.config)
        backend = self.backend.currentData()
        if backend:
            current["backend"] = backend
            if backend in ("api", "local", "codex"):
                current["model"] = self.model.currentText().strip()
        self.effective_label.setText("当前生效：" + backends.describe_analysis(current))

    def backend_changed(self):
        backend = self.backend.currentData()
        editable = backend in ("api", "local", "codex")
        self.model.setEditable(editable)
        remembered = backends.remembered_model(self.config, backend)
        if backend == "chatgpt":
            catalog_models = list(self.config.get("subscription_models", []))
            catalog_values = [str(slug) for _name, slug in catalog_models]
            current_values = [str(self.model.itemData(i)) for i in range(self.model.count())]
            if current_values != catalog_values:
                self.model.clear()
                for display, slug in catalog_models:
                    self.model.addItem(display, slug)
            catalog = catalog_values
            preferred = remembered if remembered in catalog else (catalog[0] if catalog else "")
            index = self.model.findData(preferred)
            if index >= 0:
                self.model.setCurrentIndex(index)
        elif backend == "api":
            self.model.setEditText(remembered or "deepseek-chat")
        else:
            self.model.setEditText(remembered)
        self.base.setEnabled(backend in ("api", "local"))
        self.key.setEnabled(backend in ("api", "local"))
        self.provider.setEnabled(backend == "api")
        self.fetch_models_button.setEnabled(backend in ("api", "local"))
        self.refresh_effective()

    def provider_changed(self):
        base = backends.preset_base(self.provider.currentData())
        if base:
            self.base.setText(base)
            self.refresh_effective()

    def fetch_models(self):
        backend = self.backend.currentData()
        if backend not in ("api", "local"):
            self.model_status.setText("只有“兼容模型 API / 本地模型服务”可以读取模型列表。")
            return
        snapshot = dict(self.config, backend=backend, base_url=self.base.text().strip(),
                        api_key=self.key.text().strip())
        self.fetch_models_button.setEnabled(False)
        self.model_status.setText("正在读取接口模型列表…")

        def work():
            try:
                models = analysis.list_models(snapshot)
                error = ""
            except Exception as exc:
                models, error = None, (str(exc) if isinstance(exc, ValueError)
                                       else "读取模型列表失败，请检查地址与 Key。")
            self.models_done.emit(models, error)

        threading.Thread(target=work, daemon=True).start()

    def auto_fetch_models(self):
        backend = self.backend.currentData()
        if backend not in ("api", "local"):
            return
        if not self.base.text().strip():
            return
        if backend == "api" and not self.key.text().strip():
            return
        self.fetch_models()

    def models_checked(self, models, error):
        self.fetch_models_button.setEnabled(self.backend.currentData() in ("api", "local"))
        if error or not models:
            self.model_status.setText(error or "接口没有返回可用模型。")
            return
        current = self.model.currentText().strip()
        self.model.clear()
        for display, model_id in models:
            self.model.addItem(display, model_id)
        index = self.model.findData(current)
        if index < 0:
            index = self.model.findText(current)
        self.model.setCurrentIndex(index if index >= 0 else 0)
        self.model_status.setText(f"已读取 {len(models)} 个模型，请选择后保存设置。")
        self.refresh_effective()

    def request_disconnect_chatgpt(self):
        answer = QMessageBox.question(
            self, "断开 ChatGPT 授权",
            "只会清除本应用的 ChatGPT 授权和模型目录，其它后端配置保留。确定断开吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.disconnect_requested.emit()

    def set_models(self, models):
        remembered = backends.remembered_model(self.config, "chatgpt")
        selected = remembered or self.config.get("model")
        self.model.clear()
        for display, slug in models:
            self.model.addItem(display, slug)
        index = self.model.findData(selected)
        if index >= 0:
            self.model.setCurrentIndex(index)
        elif models:
            self.model.setCurrentIndex(0)

    def commit(self):
        try:
            backend = self.backend.currentData()
            if backend == "chatgpt":
                model = self.model.currentData()
                candidate = dict(self.config, backend=backend, model=model or "")
                if not model:
                    model = backends.effective_model(candidate)
                if not model:
                    raise ValueError("请先连接 ChatGPT 并读取账号模型目录。")
            else:
                model = self.model.currentText().strip()
                if not model:
                    raise ValueError("请先读取或输入模型名称。")
            cfg = dict(self.config, wechat_path=self.path.text().strip().strip('"'),
                       backend=backend, base_url=self.base.text().strip(), model=model,
                       api_key=self.key.text().strip(), style=self.style.text(),
                       auto_connect=self.auto_connect.isChecked(),
                       auto_analysis=self.auto_analysis.isChecked(),
                       reading_backend=self.reading_backend.currentData(),
                       reading_base_url=self.reading_base.text().strip(),
                       reading_model=self.reading_model.text().strip(),
                       reading_api_key=self.reading_key.text().strip(),
                       codex_path=self.codex_path.text().strip(),
                       api_provider=self.provider.currentData())
            cfg[backends.MODEL_FIELDS[backend]] = model
            backends.validate_reading(cfg)
            backends.validate_analysis(cfg)
            settings.save(cfg)
            self.config = cfg
            self.error.clear()
            self.update_chatgpt_state(cfg)
            self.refresh_effective(cfg)
            self.model_status.setText("设置已保存。")
            self.saved.emit(cfg)
        except (ValueError, OSError) as exc:
            self.error.setText(str(exc))

    def detect_codex(self):
        self.codex_button.setEnabled(False)
        self.codex_status.setText("正在启动 Codex app-server 读取账号与模型（不会请求模型生成）…")
        config = dict(self.config, codex_path=self.codex_path.text().strip())

        def work():
            try:
                info = codex_backend.status(config)
                error = ""
            except Exception as exc:
                info = None
                error = str(exc) if isinstance(exc, ValueError) else "Codex 检测失败，请检查安装与登录状态。"
            self.codex_done.emit(info, error)

        threading.Thread(target=work, daemon=True).start()

    def codex_checked(self, info, error):
        self.codex_button.setEnabled(True)
        if error or not info:
            self.codex_status.setText(error or "Codex 检测失败。")
            return
        models = info.get("models") or []
        plan = info.get("plan") or "套餐未知"
        names = "、".join(item["display_name"] for item in models[:8])
        if models:
            self.codex_status.setText(f"已连接 Codex（{plan}），模型 {len(models)} 个：{names}")
        else:
            self.codex_status.setText(f"已连接 Codex（{plan}），但账号没有返回可选模型。")
        if models and self.backend.currentData() == "codex":
            self.model.clear()
            for item in models:
                self.model.addItem(item["display_name"], item["id"])
            self.model.setCurrentIndex(0)
        if models and not self.reading_model.text().strip():
            self.reading_model.setText(models[0]["id"])


# Kept as an import alias for existing downstream scripts using the settings editor.
SettingsDialog = SettingsPage


class ResultBus(QObject):
    done = Signal(object, object, str)
    subscription_done = Signal(object, object, str)
    read_done = Signal(object, object, str)
    account_done = Signal(str, object, str)
    queue_event = Signal(object)


class _UiInferenceGateway:
    """Build the live profile when the queue worker runs, not when the UI submits.

    ChatGPT access tokens are refreshed lazily by the account store; resolving the
    profile inside the worker thread keeps the refresh (and its network timeout) off
    the Qt main thread while every request still goes through InferenceGateway's
    transport, cancellation and schema validation.
    """

    def __init__(self, window):
        self.window = window

    def run(self, request, cancel=None):
        config = dict(self.window.config)
        gateway = InferenceGateway()
        backend = backends.analysis_backend(config)
        if backend == "chatgpt":
            gateway.register_profile("chatgpt", GatewayProfile(
                backend="chatgpt", url=account_store.API_BASE + "/responses",
                model=str(config.get("model") or "").strip(),
                api_key=subscription.access_token(config)))
        else:
            base = str(config.get("base_url") or "").strip()
            if backend == "local" and not base:
                base = analysis.DEFAULT_LOCAL_BASE
            gateway.register_profile("analysis", GatewayProfile(
                backend="compatible", url=analysis.endpoint(base),
                model=str(config.get("model") or "").strip(),
                api_key=str(config.get("api_key") or "").strip()))
        return gateway.run(request, cancel)


class BrandMark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(40, 40)
        self.setAccessibleName("ChatAssistant")
        self.setToolTip("ChatAssistant")

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#07c160"))
        painter.drawRoundedRect(QRectF(0, 0, 40, 40), 12, 12)
        painter.setBrush(QBrush(Qt.GlobalColor.transparent))
        painter.setPen(QPen(QColor("white"), 1.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap,
                            Qt.PenJoinStyle.RoundJoin))
        bubble = QPainterPath()
        bubble.moveTo(11, 10)
        bubble.quadTo(9, 10, 9, 12)
        bubble.lineTo(9, 23)
        bubble.quadTo(9, 25, 11, 25)
        bubble.lineTo(14, 25)
        bubble.lineTo(14, 29)
        bubble.lineTo(19, 25)
        bubble.lineTo(27, 25)
        bubble.quadTo(29, 25, 29, 23)
        bubble.lineTo(29, 12)
        bubble.quadTo(29, 10, 27, 10)
        bubble.closeSubpath()
        painter.drawPath(bubble)
        painter.setPen(QPen(QColor("white"), 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(14, 15, 24, 15)
        painter.drawLine(14, 19, 21, 19)


class MainWindow(QMainWindow):
    def __init__(self, config=None, scanning=True, window_manager=None, account_store=None,
                 inference_queue=None):
        super().__init__()
        self.setWindowTitle("微信实时助手 · 便捷版 0.4.0")
        self.resize(1180, 790)
        self.setMinimumSize(850, 600)
        self.setStyleSheet(STYLE)
        self.config = config if config is not None else settings.load()
        self._account_store = account_store
        self.account_busy = False
        self.state = ConversationState()
        self.child = self.stop_event = self.capture_q = None
        self.window_item = None
        self.capture_session = None
        self.user_paused = False
        self.busy = False
        self.last_analysis = 0.0
        self.last_auto_token = None
        self.changed_at = 0.0
        self.last_packet_at = 0.0
        self.latest_frame_seq = 0
        self.frame_freshness = FrameFreshness()
        self.frame_clock = None
        self.displayed_token = None
        self._cached_analysis = None
        self.last_keys = None
        self.remote_reading = backends.reading_backend(self.config) != "ocr"
        self.reading_ok_token = None
        self.reading_busy = False
        self.pending_read = None
        self.last_ocr_snapshot = None
        # T4-UI: all native docking goes through WindowManager. A recovery store
        # under the application data directory lets the crash guard restore the
        # managed window after an abnormal exit (records are signed; no chat
        # content is stored). Tests may inject a fake manager.
        self.window_manager = (window_manager if window_manager is not None
                               else self._create_window_manager())
        self._dock_message = None
        self._aperture_open = False
        self.bus = ResultBus()
        self.bus.done.connect(self.analysis_done)
        self.bus.subscription_done.connect(self.subscription_done)
        self.bus.read_done.connect(self.reading_done)
        self.bus.account_done.connect(self.account_action_done)
        self._queue_action_slot = self._run_queue_action
        self.bus.queue_event.connect(self._queue_action_slot)
        # T3-UI: one global in-flight model request, latest-frame-only per scope.
        # The real worker thread starts lazily on the first analysis (or earlier if
        # the caller injects a queue), so mere window construction never leaks a
        # model worker into tests or short-lived windows.
        self._inference_queue = inference_queue
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        rail = QFrame()
        rail.setObjectName("navigationRail")
        rail.setFixedWidth(62)
        rail_layout = QVBoxLayout(rail)
        rail_layout.setContentsMargins(10, 14, 10, 12)
        rail_layout.setSpacing(12)
        rail_layout.addWidget(BrandMark(), 0, Qt.AlignmentFlag.AlignHCenter)
        self.nav_chat = QToolButton()
        self.nav_chat.setText("▤")
        self.nav_chat.setToolTip("聊天")
        self.nav_chat.setCheckable(True)
        self.nav_chat.setChecked(True)
        self.nav_history = QToolButton()
        self.nav_history.setText("▦")
        self.nav_history.setToolTip("历史记录")
        self.nav_history.setCheckable(True)
        self.nav_settings = QToolButton()
        self.nav_settings.setText("⚙")
        self.nav_settings.setToolTip("设置")
        self.nav_settings.setCheckable(True)
        self.nav_chat.clicked.connect(self.show_chat)
        self.nav_history.clicked.connect(self.show_history)
        self.nav_settings.clicked.connect(self.show_settings)
        rail_layout.addWidget(self.nav_chat)
        rail_layout.addWidget(self.nav_history)
        rail_layout.addWidget(self.nav_settings)
        rail_layout.addStretch(1)
        rail_layout.addWidget(label("实时助手", True), 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(rail)

        self.page_stack = QStackedWidget()
        self.chat_page = QWidget()
        chat_layout = QVBoxLayout(self.chat_page)
        chat_layout.setContentsMargins(20, 18, 16, 14)
        chat_layout.setSpacing(12)
        chat_header = QHBoxLayout()
        self.chat_title = label("尚未连接会话")
        self.chat_title.setObjectName("title")
        chat_header.addWidget(self.chat_title, 1)
        self.connection_status = label("未连接微信", True)
        chat_header.addWidget(self.connection_status)
        self.effective_label = label("分析：未配置", True)
        chat_header.addWidget(self.effective_label)
        self.analysis_status = label("等待分析", True)
        chat_header.addWidget(self.analysis_status)
        chat_layout.addLayout(chat_header)

        chat_body = QHBoxLayout()
        chat_body.setContentsMargins(0, 0, 0, 0)
        chat_body.setSpacing(12)
        chat_content = QFrame()
        chat_content.setObjectName("chatContent")
        chat_content_layout = QVBoxLayout(chat_content)
        chat_content_layout.setContentsMargins(12, 10, 12, 10)
        chat_content_layout.setSpacing(8)
        chat_content_layout.addWidget(label("微信窗口画面", True))
        self.preview = QLabel("连接微信后在这里显示实时画面")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(340)
        self.preview.setStyleSheet("background: #edf1f5; border-radius: 7px; color: #65778c;")
        self.preview_image = None
        chat_content_layout.addWidget(self.preview, 1)
        chat_content_layout.addWidget(label("当前识别文字", True))
        self.editor = QPlainTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setPlaceholderText("等待识别到聊天内容")
        self.editor.setObjectName("conversationText")
        self.editor.setMaximumHeight(124)
        chat_content_layout.addWidget(self.editor)
        self.notice = label(self.config.get("_warning", ""), True)
        chat_content_layout.addWidget(self.notice)
        chat_body.addWidget(chat_content, 1)

        self.ai_panel = QFrame()
        self.ai_panel.setObjectName("aiPanel")
        self.ai_panel.setFixedWidth(320)
        self.ai_panel.setVisible(False)
        result_layout = QVBoxLayout(self.ai_panel)
        result_layout.setContentsMargins(12, 10, 12, 10)
        result_layout.setSpacing(8)
        result_header = QHBoxLayout()
        result_header.addWidget(label("✦ AI 建议"), 1)
        self.ai_close = QToolButton()
        self.ai_close.setText("×")
        self.ai_close.setToolTip("收起建议")
        self.ai_close.clicked.connect(self.toggle_ai_panel)
        result_header.addWidget(self.ai_close)
        result_layout.addLayout(result_header)
        summary_group = QGroupBox("对话判断")
        sum_layout = QVBoxLayout(summary_group)
        self.summary = label("等待新对话快照")
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sum_layout.addWidget(self.summary)
        result_layout.addWidget(summary_group)
        self.reply_boxes, self.copy_buttons = [], []
        for i in range(3):
            group = QGroupBox(f"回复建议 {i + 1}")
            box_layout = QVBoxLayout(group)
            text = label("等待分析")
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            copy = QPushButton("复制")
            copy.setEnabled(False)
            copy.clicked.connect(lambda checked=False, n=i: self.copy_reply(n))
            box_layout.addWidget(text, 1)
            box_layout.addWidget(copy, 0, Qt.AlignmentFlag.AlignRight)
            result_layout.addWidget(group)
            self.reply_boxes.append(text)
            self.copy_buttons.append(copy)
        result_layout.addStretch(1)
        chat_layout.addLayout(chat_body, 1)
        self.page_stack.addWidget(self.chat_page)

        self.settings_page = SettingsPage(self.config)
        self.settings_page.saved.connect(self.settings_saved)
        self.settings_page.cancel_requested.connect(self.show_chat)
        self.settings_page.disconnect_requested.connect(self.disconnect_chatgpt)
        self._account_action_slot = self._on_account_action
        self.settings_page.account_action.connect(self._account_action_slot)
        self.settings_page.set_accounts(self.config.get("registrations") or [])
        self.refresh_effective_model()
        self.page_stack.addWidget(self.settings_page)
        self.history_page = HistoryPage()
        self.page_stack.addWidget(self.history_page)
        self.windows = self.settings_page.windows
        self.connect_button = self.settings_page.connect_button
        self.chatgpt_button = self.settings_page.chatgpt_button
        self.auto_analysis = self.settings_page.auto_analysis
        self.windows.setMinimumWidth(220)
        self.connect_button.clicked.connect(self.connect_selected)
        self.settings_page.refresh_button.clicked.connect(self.refresh_windows)
        self.settings_page.launch_button.clicked.connect(self.launch_wechat)
        self.settings_page.disconnect_button.clicked.connect(self.disconnect_capture)
        self.chatgpt_button.clicked.connect(self.connect_account_new)
        self.auto_analysis.toggled.connect(self.auto_toggled)
        layout.addWidget(self.page_stack, 1)

        self.ai_toggle = QPushButton("✦\nA\nI\n建\n议")
        self.ai_toggle.setObjectName("aiToggle")
        self.ai_toggle.setCheckable(True)
        self.ai_toggle.setFixedWidth(38)
        self.ai_toggle.clicked.connect(self.toggle_ai_panel)
        layout.addWidget(self.ai_toggle)
        layout.addWidget(self.ai_panel)
        self.show_chat()
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.tick)
        self.poll.start(200)
        self.dock_timer = QTimer(self)
        self.dock_timer.timeout.connect(self.sync_dock)
        self.dock_timer.start(450)
        self.scanner = QTimer(self)
        self.scanner.timeout.connect(self.scan)
        if scanning:
            self.scanner.start(2500)
            QTimer.singleShot(100, self.scan)

    def clear_results(self, forget=True):
        if forget:
            self._cached_analysis = None
        self.displayed_token = None
        self.summary.setText("等待新对话快照")
        for text, button in zip(self.reply_boxes, self.copy_buttons):
            text.setText("等待分析")
            button.setEnabled(False)

    def set_editor(self, text):
        self.editor.blockSignals(True)
        self.editor.setPlainText(text)
        self.editor.blockSignals(False)

    def show_preview(self, image):
        pixmap = QPixmap()
        if not pixmap.loadFromData(image):
            return
        self.preview_image = pixmap
        self.preview.setPixmap(pixmap.scaled(self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.SmoothTransformation))

    def clear_preview(self):
        self.preview_image = None
        self.preview.clear()
        self.preview.setText("连接微信后在这里显示实时画面")

    # -- native docking: WindowManager + capture health (T4-UI) -------------
    @staticmethod
    def _create_window_manager():
        """WindowManager with signed crash-recovery records under the app data dir."""
        recovery = None
        try:
            from .recovery_guard import RecoveryStore
            recovery = RecoveryStore(settings.data_dir())
        except (OSError, ImportError):
            recovery = None
        return WindowManager(recovery_store=recovery)

    def _window_source(self):
        """SourceKey of the connected window; None when the handle is unverified."""
        item = self.window_item
        if not item:
            return None
        target = item.get("hwnd")
        if not isinstance(target, WindowTarget):
            return None
        try:
            return SourceKey.from_window_target(target)
        except (TypeError, ValueError):
            return None

    def _host_hwnd(self):
        try:
            return int(self.winId())
        except (RuntimeError, TypeError, ValueError):
            return 0

    def _aperture_hwnd(self):
        try:
            return int(self.preview.winId())
        except (RuntimeError, TypeError, ValueError):
            return 0

    def _aperture_rect(self):
        """Physical-pixel rectangle of the central preview (the docking aperture).

        Prefer the native Win32 rectangle (exact under Per-Monitor V2, as in 0.2);
        fall back to the Qt logical geometry converted with the host window DPI.
        """
        manager = self.window_manager
        api = getattr(manager, "api", None) if manager is not None else None
        if api is not None:
            try:
                return tuple(api.rect(int(self.preview.winId())))
            except (AttributeError, OSError, TypeError, ValueError):
                pass
        origin = self.preview.mapToGlobal(QPoint(0, 0))
        size = self.preview.size()
        dpi_value = dpi(self._host_hwnd()) or 96
        return rect_to_physical((origin.x(), origin.y(),
                                 origin.x() + size.width(),
                                 origin.y() + size.height()), dpi_value)

    @property
    def _overlay_safe(self):
        manager = getattr(self, "window_manager", None)
        return bool(manager is not None and manager.overlay_safe)

    def _open_aperture(self):
        if (self.page_stack.currentWidget() is not self.chat_page
                or not self.isVisible() or self.isMinimized()):
            self._close_aperture()
            return
        origin = self.preview.mapTo(self, QPoint(0, 0))
        hole = QRect(origin, self.preview.size())
        self.setMask(QRegion(self.rect()).subtracted(QRegion(hole)))
        self._aperture_open = True

    def _close_aperture(self):
        self._aperture_open = False
        self.clearMask()

    def _set_dock_message(self, text):
        """Avoid rewriting the connection label every docking timer tick."""
        if getattr(self, "_dock_message", None) == text:
            return
        self._dock_message = text
        if text:
            self.set_connection_status(text)

    def _apply_dock_status(self, status):
        """Open the transparent aperture only when the group order is verified."""
        if status is None:
            self._close_aperture()
            return
        if status.ok and status.state == "attached" and status.overlay_safe:
            self._open_aperture()
            self._set_dock_message("微信已停靠 · 中央微信原生可交互")
            return
        self._close_aperture()
        if status.state == "popup":
            self._set_dock_message("微信弹窗覆盖聊天区域：中央切换为只读画面，请先关闭弹窗。")
        elif status.reason == "identity":
            self._set_dock_message("微信窗口身份已变化：中央为只读画面，请重新连接。")
        elif status.reason in ("host_invalid", "aperture_invalid"):
            self._set_dock_message("窗口句柄尚未就绪：中央为只读画面，暂不打开透明开口。")
        else:
            self._set_dock_message("窗口层级未确认：中央切换为只读画面，避免露出其他桌面内容。")

    def _apply_capture_health(self, health):
        """Capture heartbeat / popup / minimized / stale gate the read-only fallback."""
        if health is None:
            self._close_aperture()
            return
        if health.overlay_safe and health.state == "live":
            self._open_aperture()
            self._set_dock_message("微信已停靠 · 中央微信原生可交互")
            return
        self._close_aperture()
        if health.state == "popup":
            self._set_dock_message("微信弹窗覆盖聊天区域：中央切换为只读画面，请先关闭弹窗。")
        elif health.state == "minimized":
            self._set_dock_message("微信已最小化：中央为只读画面，返回聊天页后自动还原。")
        elif health.state == "stale":
            self._set_dock_message("采集画面已过期：中央切换为只读画面，等待新画面。")
        elif health.state in ("error", "disconnected"):
            self._set_dock_message("微信窗口状态异常：中央为只读画面，请重新连接。")
        elif health.state == "login":
            self._set_dock_message("微信处于登录窗口：中央为只读画面。")
        else:
            self._set_dock_message("窗口层级未确认：中央切换为只读画面，避免露出其他桌面内容。")

    def undock(self):
        """Detach the managed window (restores geometry/minimized state) + close hole."""
        manager = getattr(self, "window_manager", None)
        if manager is not None:
            manager.detach()
        self._close_aperture()

    def sync_dock(self):
        manager = getattr(self, "window_manager", None)
        if manager is None:
            self._close_aperture()
            return
        source = self._window_source()
        if source is None:
            if manager.attached:
                manager.detach()
            self._close_aperture()
            return
        chat_visible = (self.isVisible() and not self.isMinimized()
                        and self.page_stack.currentWidget() is self.chat_page)
        if not chat_visible:
            page = self.page_stack.currentWidget()
            if self.isMinimized() or not self.isVisible():
                mode = "minimized"
            elif page is self.history_page:
                mode = "history"
            elif page is self.settings_page:
                mode = "settings"
            else:
                mode = "minimized"
            manager.set_view(mode)
            self._close_aperture()
            return
        manager.set_view("chat")
        if not manager.attached:
            host_hwnd, aperture_hwnd = self._host_hwnd(), self._aperture_hwnd()
            if not host_hwnd or not aperture_hwnd:
                self._close_aperture()
                self._set_dock_message("窗口句柄尚未就绪：中央为只读画面，暂不打开透明开口。")
                return
            status = manager.attach(source, self._aperture_rect(),
                                    host_hwnd=host_hwnd,
                                    aperture_hwnd=aperture_hwnd)
        else:
            status = manager.sync()
        self._apply_dock_status(status)
        self._apply_capture_health(manager.capture_health(source))

    def on_activated(self):
        """Second-instance activation: repair the docked group's Z order (no focus steal)."""
        manager = getattr(self, "window_manager", None)
        if manager is None:
            return False
        repaired = bool(manager.on_activated())
        QTimer.singleShot(0, self.sync_dock)
        return repaired

    def show_chat(self):
        if hasattr(self, "page_stack"):
            self.page_stack.setCurrentWidget(self.chat_page)
            self.nav_chat.setChecked(True)
            self.nav_history.setChecked(False)
            self.nav_settings.setChecked(False)
            manager = getattr(self, "window_manager", None)
            if manager is not None:
                manager.set_view("chat")
            self.ai_toggle.show()
            QTimer.singleShot(0, self.sync_dock)

    def show_history(self):
        manager = getattr(self, "window_manager", None)
        if manager is not None:
            manager.set_view("history")
        self._close_aperture()
        if not self.ai_panel.isHidden():
            self.toggle_ai_panel()
        self.page_stack.setCurrentWidget(self.history_page)
        self.nav_chat.setChecked(False)
        self.nav_history.setChecked(True)
        self.nav_settings.setChecked(False)
        self.ai_toggle.hide()
        try:
            self.history_page.refresh_people()
        except OSError:
            self.history_page.status.setText("无法打开本地记忆库，请检查用户目录权限。")

    def show_settings(self):
        manager = getattr(self, "window_manager", None)
        if manager is not None:
            manager.set_view("settings")
        self._close_aperture()
        if not self.ai_panel.isHidden():
            self.toggle_ai_panel()
        self.page_stack.setCurrentWidget(self.settings_page)
        self.nav_chat.setChecked(False)
        self.nav_history.setChecked(False)
        self.nav_settings.setChecked(True)
        self.ai_toggle.setChecked(False)
        self.ai_toggle.hide()

    def toggle_ai_panel(self, _checked=False):
        if self.page_stack.currentWidget() is not self.chat_page:
            self.show_chat()
        visible = self.ai_panel.isHidden()
        extension = self.ai_panel.width()
        if visible:
            self.resize(self.width() + extension, self.height())
            screen = self.screen()
            if screen is not None:
                area = screen.availableGeometry()
                if self.geometry().right() > area.right():
                    self.move(max(area.left(), area.right() - self.width() + 1), self.y())
        self.ai_panel.setVisible(visible)
        if not visible:
            self.resize(max(self.minimumWidth(), self.width() - extension), self.height())
        self.ai_toggle.setChecked(visible)

    def set_connection_status(self, text):
        self.connection_status.setText(text)
        self.settings_page.wechat_status.setText(text)

    def set_notice(self, text):
        self.notice.setText(text)
        self.settings_page.status.setText(text)

    def settings_saved(self, config):
        path_changed = self.config.get("wechat_path") != config.get("wechat_path")
        auto_changed = self.config.get("auto_analysis", True) != config.get("auto_analysis", True)
        reading_changed = (backends.reading_backend(self.config)
                           != backends.reading_backend(config))
        codex_changed = ((self.config.get("codex_path") or "").strip()
                         != (config.get("codex_path") or "").strip())
        self.config = dict(config)
        self.settings_page.config = dict(config)
        self.settings_page.update_chatgpt_state(self.config)
        self.refresh_effective_model()
        self.remote_reading = backends.reading_backend(config) != "ocr"
        self.reading_ok_token = None
        self.state.invalidate()
        self.clear_results()
        if codex_changed:
            codex_backend.shutdown()
        if auto_changed:
            self.auto_toggled(bool(config.get("auto_analysis", True)))
        if path_changed:
            self.stop_capture()
            self.window_item = None
            self.state.connect(None)
            self.set_editor("")
            self.user_paused = False
        elif reading_changed and self.window_item is not None:
            # The capture process needs the new reading mode; reconnect the same window.
            target = self.window_item
            self.stop_capture()
            self.connect_item(target)
        self.scan()
        warnings = []
        if (backends.analysis_backend(self.config) == "codex"
                or backends.reading_backend(self.config) == "codex") \
                and not codex_backend.available(self.config):
            warnings.append("未检测到 Codex 应用，Codex 后端将在安装并登录官方 Codex 后可用。")
        self.set_notice("设置已保存。" + (" " + " ".join(warnings) if warnings else ""))

    def scan(self):
        try:
            path = self.settings_page.path.text().strip().strip('"')
            items = discover(path or self.config["wechat_path"])
        except OSError:
            self.set_connection_status("无法枚举窗口，请检查微信路径或稍后重试。")
            return
        selected_id = self.windows.currentData()["id"] if self.windows.currentData() else None
        keys = [(x["id"], x["minimized"]) for x in items]
        if keys != self.last_keys:
            self.windows.blockSignals(True)
            self.windows.clear()
            for n, item in enumerate(items, 1):
                self.windows.addItem(f"微信窗口 {n} · 进程 {item['pid']}" + ("（最小化）" if item["minimized"] else ""), item)
            idx = next((i for i, x in enumerate(items) if x["id"] == selected_id), 0)
            if items:
                self.windows.setCurrentIndex(idx)
            self.windows.blockSignals(False)
            self.last_keys = keys
        if self.window_item and not any(x["id"] == self.window_item["id"] for x in items):
            self.stop_capture()
            self.window_item = None
            self.state.connect(None)
            self.set_editor("")
            self.chat_title.setText("微信窗口已关闭")
            self.clear_results()
        if not self.window_item and not self.user_paused:
            candidate = choose_window(items, None)
            if candidate and self.config["auto_connect"]:
                self.connect_item(candidate)
            elif not items:
                self.set_connection_status("等待微信窗口")
            elif len(items) > 1:
                self.set_connection_status("发现多个微信窗口")

    def refresh_windows(self):
        self.last_keys = None
        self.scan()

    def connect_selected(self):
        item = self.windows.currentData()
        if item:
            self.connect_item(item)
        else:
            self.scan()

    def disconnect_capture(self):
        self.stop_capture()
        self.window_item = None
        self.user_paused = True
        self.state.connect(None)
        self.set_editor("")
        self.chat_title.setText("尚未连接会话")
        self.clear_results()
        self.set_connection_status("已断开微信连接。可在设置中重新连接窗口。")

    def connect_item(self, item):
        self.stop_capture()
        self.clear_preview()
        self.window_item = item
        self.user_paused = False
        self.capture_session = uuid.uuid4().hex
        self.state.connect(self.capture_session)
        self.set_editor("")
        self.clear_results()
        self.reading_ok_token = None
        self.reading_busy = False
        self.pending_read = None
        self.chat_title.setText("等待识别会话名称")
        self.editor.setReadOnly(True)
        self.last_ocr_snapshot = None
        self.capture_q = mp.Queue(maxsize=6)
        self.stop_event = mp.Event()
        self.frame_clock = mp.Array("q", [0, 0])
        self.frame_freshness = FrameFreshness()
        self.latest_frame_seq = 0
        self.child = mp.Process(target=capture_run, args=(
            self.capture_q, self.stop_event, item["hwnd"], self.capture_session,
            self.frame_clock, backends.reading_backend(self.config)), daemon=True)
        self.child.start()
        self.set_connection_status("已连接微信，正在启动本地识别…")
        self.last_packet_at = time.monotonic()
        self.show_chat()
        QTimer.singleShot(250, self.sync_dock)

    def stop_capture(self):
        # Detach restores the pre-takeover geometry/minimized state and clears
        # this assistant's signed recovery record; never touches unmanaged windows.
        self.undock()
        if self.stop_event:
            self.stop_event.set()
        if self.child:
            self.child.join(timeout=0.25)
            if self.child.is_alive():
                self.child.terminate()
                self.child.join(timeout=0.5)
        if self.capture_q:
            self.capture_q.cancel_join_thread()
            self.capture_q.close()
        self.child = self.stop_event = self.capture_q = None
        self.frame_clock = None
        self.capture_session = None
        self.reading_ok_token = None
        self.reading_busy = False
        self.pending_read = None
        self.last_ocr_snapshot = None
        self.state.source_status = "disconnected"
        self.state.invalidate()
        self.clear_preview()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, "preview_image", None) is not None:
            self.preview.setPixmap(self.preview_image.scaled(self.preview.size(),
                Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        manager = getattr(self, "window_manager", None)
        if manager is not None and manager.attached:
            QTimer.singleShot(0, self.sync_dock)

    def moveEvent(self, event):
        super().moveEvent(event)
        manager = getattr(self, "window_manager", None)
        if manager is not None and manager.attached:
            QTimer.singleShot(0, self.sync_dock)

    def tick(self):
        if self.frame_clock is not None:
            with self.frame_clock.get_lock():
                frame_seq = int(self.frame_clock[0])
            if self.frame_freshness.observe(frame_seq):
                self.latest_frame_seq = frame_seq
                if self.window_item:
                    self.state.mark_pending()
                    self.clear_results(forget=False)
                    self.set_connection_status("画面有更新，正在重新识别；旧建议已停用。")
        if self.capture_q:
            for _ in range(10):
                try:
                    packet = self.capture_q.get_nowait()
                except (queue.Empty, ValueError, OSError):
                    break
                if packet["session"] != self.capture_session:
                    continue
                self.last_packet_at = time.monotonic()
                kind = packet["kind"]
                # Feed capture freshness into WindowManager: every packet proves the
                # capture process is alive, so a static picture is not "stale".
                manager = getattr(self, "window_manager", None)
                source = self._window_source()
                if manager is not None and source is not None:
                    manager.record_heartbeat(source)
                if kind == "frame":
                    self.show_preview(packet["image"])
                    if manager is not None and source is not None:
                        manager.record_frame(source)
                elif kind == "snapshot":
                    if not self.frame_freshness.accepts(packet.get("frame_seq", 0)):
                        continue
                    if manager is not None and source is not None:
                        manager.record_frame(source)
                        manager.record_ocr(source)
                    if self.remote_reading:
                        # Local OCR stays the change detector and failure fallback;
                        # once a remote read confirmed the current text, OCR must not
                        # overwrite it with a slightly different transcription.
                        self.last_ocr_snapshot = (packet["title"], packet["messages"],
                                                  packet.get("frame_seq", 0))
                        if self.reading_snapshot_ready():
                            continue
                    changed = self.state.update(packet["title"], packet["messages"])
                    if changed and manager is not None and source is not None:
                        manager.record_frame(source, changed=True)
                    self.chat_title.setText(packet["title"])
                    self.set_connection_status("正在本地识别 · 微信已停靠"
                                               if self._overlay_safe else
                                               "正在本地识别 · 中央实时画面")
                    if changed:
                        self.set_editor(self.state.text)
                        self.clear_results()
                        self.changed_at = time.monotonic()
                    elif not self.remote_reading and self._cached_analysis:
                        self.restore_analysis()
                elif kind == "read_frame":
                    if not self.frame_freshness.accepts(packet.get("frame_seq", 0)):
                        continue
                    if self.remote_reading:
                        self.queue_remote_read(packet)
                elif kind in ("unavailable", "error", "closed"):
                    self.state.update("", [])
                    self.last_ocr_snapshot = None
                    self.set_editor("")
                    self.clear_results()
                    self.chat_title.setText("当前会话未确认")
                    self.set_connection_status(packet["message"])
                    if kind in ("error", "closed"):
                        self.stop_capture()
                        self.user_paused = True
                        self.set_connection_status("微信窗口识别已停止，请在设置中重新连接。")
                        break
                else:
                    self.set_connection_status(packet.get("message", "正在识别…"))
        if self.child and not self.child.is_alive():
            self.stop_capture()
            self.state.update("", [])
            self.set_editor("")
            self.clear_results()
            self.user_paused = True
            self.set_connection_status("采集进程已停止，请在设置中重新连接。")
        if self.auto_analysis.isChecked() and self.state.ready:
            if self.remote_reading and not self.reading_snapshot_ready() and self.reading_busy:
                self.analysis_status.setText("正在用所选读取后端识别画面…")
            elif self.remote_reading and not reading.reading_ready(self.config):
                self.analysis_status.setText("等待读取后端")
                self.set_notice("已开启远程读取，但读取后端尚未配置完成；请在设置中检查读取后端。")
            elif not self.backend_ready():
                self.analysis_status.setText("等待模型连接")
                self.set_notice("已读取聊天文字；模型尚未就绪，请在设置中连接并选择分析模型。")
        if (self.auto_analysis.isChecked() and self.backend_ready() and self.reading_snapshot_ready()
                and self.child and self.state.ready and not self.busy
                and self.last_auto_token != self.state.token and time.monotonic() - self.changed_at > 2
                and time.monotonic() - self.last_analysis > 10):
            self.start_analysis()

    def auto_toggled(self, enabled):
        self.config["auto_analysis"] = bool(enabled)
        try:
            settings.save(self.config)
        except OSError:
            self.set_notice("无法保存自动分析设置，请检查本机用户目录权限。")
        if not enabled:
            self.state.invalidate()
            self.clear_results()
        elif not self.backend_ready():
            self.set_notice("自动分析已开启。配置并连接模型后，新识别内容会自动分析。")
        else:
            if self._inference_queue is not None:
                try:
                    self._inference_queue.resume()
                except Exception:  # noqa: BLE001 - fake queue in tests
                    pass

    def start_analysis(self):
        if self.busy or not self.state.ready:
            return
        if not self.reading_snapshot_ready():
            return
        if not self.backend_ready():
            self.set_notice(backends.hint_for_analysis(self.config))
            if backends.analysis_backend(self.config) != "chatgpt":
                self.open_settings()
            return
        token = self.state.token
        text = self.editor.toPlainText()
        cfg = dict(self.config, relationship="普通联系人")
        cfg["model"] = backends.effective_model(cfg)
        self.busy = True
        self.analysis_status.setText("正在分析…")
        self.last_analysis = time.monotonic()
        self.last_auto_token = token
        self.set_notice("正在分析当前可见对话；切换会话会使本次结果失效。")
        if backends.analysis_backend(cfg) == "codex":
            def work():
                try:
                    result = codex_backend.analyze(cfg, text)
                    error = ""
                except Exception as e:  # noqa: BLE001 - UI thread reports the message
                    result, error = None, (
                        str(e) if isinstance(e, ValueError) else "分析失败，请检查设置后重试。")
                self.bus.done.emit(token, result, error)
            threading.Thread(target=work, daemon=True).start()
            return
        try:
            request = self._analysis_request(cfg, text)
            handle = self.inference_queue.submit(
                request, scope_id=self._queue_scope_id())
        except (TypeError, ValueError) as exc:
            self.busy = False
            self.analysis_status.setText("分析未完成")
            self.set_notice(str(exc))
            return
        handle.set_callback(lambda finished: self._on_inference_handle(token, finished))

    def _queue_scope_id(self):
        """Same visible chat = same realtime slot; a newer frame supersedes it."""
        return "visible-chat"

    @property
    def inference_queue(self):
        if self._inference_queue is None:
            self._inference_queue = InferenceQueue(
                _UiInferenceGateway(self), auto_start=True,
                dispatch=self._dispatch_queue_action)
        return self._inference_queue

    @inference_queue.setter
    def inference_queue(self, queue):
        self._inference_queue = queue

    def _dispatch_queue_action(self, action):
        self.bus.queue_event.emit(action)

    def _run_queue_action(self, action):
        if callable(action):
            action()

    def _analysis_request(self, cfg, text):
        backend = backends.analysis_backend(cfg)
        model = str(backends.effective_model(cfg) or "").strip()
        relationship = cfg.get("relationship", "普通联系人")
        style = cfg.get("style", "自然、简短")
        if backend == "chatgpt":
            shared = subscription.build_payload(text, relationship, style, model)
            return InferenceRequest(
                task_id="ui-" + uuid.uuid4().hex, kind=KIND_REALTIME_REPLY,
                profile_id="chatgpt",
                input_data={"instructions": shared["messages"][0]["content"],
                            "input": shared["messages"][1]["content"]},
                schema_version="v1", version_key=f"chatgpt:{model}")
        payload = analysis.build_payload(text, relationship, style, model)
        return InferenceRequest(
            task_id="ui-" + uuid.uuid4().hex, kind=KIND_REALTIME_REPLY,
            profile_id="analysis",
            input_data={"messages": payload["messages"],
                        "temperature": payload["temperature"],
                        "max_tokens": payload["max_tokens"]},
            schema_version="v1", version_key=f"{backend}:{model}")

    def _on_inference_handle(self, token, handle):
        self.busy = False
        if handle.cancelled:
            return
        if handle.error is not None:
            self.analysis_status.setText("分析未完成")
            if isinstance(handle.error, InferenceQuotaError):
                self.analysis_status.setText("额度受限，自动分析已暂停")
                self.set_notice("模型额度受限，自动分析已暂停；恢复额度后重新开启自动分析或保存设置即可继续。")
            elif isinstance(handle.error, InferenceAuthError):
                self.analysis_status.setText("授权失效")
                self.set_notice("模型授权失效：" + str(handle.error) + " 请到设置中重新连接或刷新授权。")
            else:
                self.set_notice(str(handle.error) if isinstance(handle.error, ValueError)
                                else "分析失败，请检查设置后重试。")
            return
        result = getattr(handle.result, "validated_data", None)
        if not isinstance(result, dict):
            self.analysis_status.setText("分析未完成")
            self.set_notice("分析返回格式异常，请重试。")
            return
        self.analysis_done(token, result, "")

    def _selected_analysis(self, cfg, text):
        backend = backends.analysis_backend(cfg)
        if backend == "chatgpt":
            return subscription.analyze(cfg, text)
        if backend == "codex":
            return codex_backend.analyze(cfg, text)
        return analyze(cfg, text)

    def backend_ready(self):
        return backends.analysis_ready(self.config)

    def refresh_effective_model(self):
        self.effective_label.setText("分析：" + backends.describe_analysis(self.config))

    def _store(self):
        if self._account_store is None:
            self._account_store = account_store.AccountStore(settings.data_dir())
        return self._account_store

    def _on_account_action(self, action, registration_id):
        if action == "switch":
            self.switch_account(registration_id)
        elif action == "reauth":
            self.reauth_account(registration_id)
        elif action == "refresh":
            self.refresh_account_models()

    def _apply_fresh_account_state(self, fresh=None):
        fresh = settings.load() if fresh is None else fresh
        for key in ("subscription", "subscription_models", "registration_id",
                    "active_registration_id", "registrations", "_account_revision",
                    "host_id"):
            if key in fresh:
                self.config[key] = fresh[key]
        if backends.analysis_backend(self.config) == "chatgpt":
            effective = backends.effective_model(self.config)
            if effective:
                self.config["model"] = effective
                self.config["chatgpt_model"] = effective
        self.settings_page.config = dict(self.config)
        self.settings_page.set_accounts(self.config.get("registrations") or [])
        self.settings_page.update_chatgpt_state(self.config)
        if backends.analysis_backend(self.config) == "chatgpt":
            self.settings_page.backend.setCurrentIndex(
                max(0, self.settings_page.backend.findData("chatgpt")))
            self.settings_page.set_models(self.config.get("subscription_models") or [])
        self.refresh_effective_model()

    def switch_account(self, registration_id):
        if not registration_id:
            return
        try:
            self._store().select(registration_id)
            self._apply_fresh_account_state()
        except (OSError, ValueError) as exc:
            self.set_notice(f"切换账号失败：{exc}")
            return
        self.set_notice("已切换 ChatGPT 账号；分析将使用该账号的模型。")

    def reauth_account(self, registration_id):
        if not registration_id:
            return
        try:
            self._store().select(registration_id)
        except (OSError, ValueError) as exc:
            self.set_notice(f"选择账号失败：{exc}")
            return
        self.connect_account_new(registration_id)

    def connect_account_new(self, registration_id=None):
        if self.subscription_busy:
            return
        self.subscription_busy = True
        self.chatgpt_button.setEnabled(False)
        self.set_notice("正在启动 OpenAI 官方授权流程。请在浏览器确认后返回此应用。")

        def work():
            try:
                store = self._store()
                attempt = account_store.AuthAttempt(store)
                attempt.start(registration_id)
                registration = attempt.wait()
                fresh = settings.load()
                models = []
                model_error = ""
                try:
                    models = subscription.list_models(fresh)
                except ValueError as exc:
                    model_error = str(exc)
                if models:
                    try:
                        store.update_model_catalog(registration.registration_id, models)
                    except (OSError, ValueError):
                        pass
                self.bus.account_done.emit(
                    "connect", {"registration": registration, "models": models}, model_error)
            except Exception as exc:  # noqa: BLE001 - UI thread reports the message
                self.bus.account_done.emit(
                    "connect", None,
                    str(exc) if isinstance(exc, ValueError)
                    else "ChatGPT 授权失败，请检查网络后重试。")

        threading.Thread(target=work, daemon=True).start()

    def refresh_account_models(self):
        if self.account_busy:
            return
        self.account_busy = True
        self.set_notice("正在刷新账号模型目录…")

        def work():
            try:
                fresh = settings.load()
                models = subscription.list_models(fresh)
                registration_id = (fresh.get("registration_id")
                                   or fresh.get("active_registration_id"))
                if registration_id:
                    try:
                        self._store().update_model_catalog(registration_id, models)
                    except (OSError, ValueError):
                        pass
                self.bus.account_done.emit("models", models, "")
            except Exception as exc:  # noqa: BLE001 - UI thread reports the message
                self.bus.account_done.emit(
                    "models", None,
                    str(exc) if isinstance(exc, ValueError)
                    else "刷新模型目录失败，请检查网络后重试。")

        threading.Thread(target=work, daemon=True).start()

    def account_action_done(self, action, payload, error):
        self.subscription_busy = False
        self.account_busy = False
        self.chatgpt_button.setEnabled(True)
        if error:
            self.set_notice(error)
            return
        if action == "connect":
            data = payload or {}
            models = data.get("models") or []
            try:
                self._apply_fresh_account_state()
            except (OSError, ValueError):
                pass
            self.config["backend"] = "chatgpt"
            if models:
                self.config["subscription_models"] = [list(item) for item in models]
                if not backends.effective_model(self.config):
                    self.config["chatgpt_model"] = models[0][1]
                    self.config["model"] = models[0][1]
                self.settings_page.backend.setCurrentIndex(
                    max(0, self.settings_page.backend.findData("chatgpt")))
                self.settings_page.set_models(models)
                try:
                    settings.save(self.config)
                except OSError:
                    pass
                self.refresh_effective_model()
                self.set_notice(f"已连接 ChatGPT，读取到 {len(models)} 个可用模型。")
            else:
                self.set_notice("授权已完成，但模型目录暂不可用；请点“刷新模型”。")
        elif action == "models":
            models = payload or []
            try:
                self._apply_fresh_account_state()
            except (OSError, ValueError):
                pass
            self.settings_page.set_models(models)
            if models:
                self.settings_page.model_status.setText(f"已刷新 {len(models)} 个模型。")
            self.set_notice("账号模型目录已刷新。")

    def disconnect_chatgpt(self):
        registration_id = (self.config.get("registration_id")
                           or self.config.get("active_registration_id"))
        if registration_id:
            try:
                result = self._store().sign_out(registration_id, CancellationToken())
            except (OSError, ValueError) as exc:
                self.set_notice(f"退出账号失败：{exc}")
                return
            try:
                self._apply_fresh_account_state()
            except (OSError, ValueError):
                pass
            self.state.invalidate()
            self.clear_results()
            self.set_notice(result.message or (
                "已撤销 ChatGPT 授权。" if result.revoked
                else "已在本机退出 ChatGPT（远程撤销未确认）。"))
            return
        # Legacy single-account snapshot from before T2: clear the compatibility view.
        self.config["subscription"] = None
        self.config["subscription_models"] = []
        self.config["chatgpt_model"] = ""
        try:
            settings.save(self.config)
        except OSError:
            self.set_notice("已断开 ChatGPT 授权，但本机未能保存设置；请检查用户目录权限。")
            return
        self.settings_page.config = dict(self.config)
        self.settings_page.update_chatgpt_state(self.config)
        self.settings_page.set_models([])
        self.refresh_effective_model()
        self.state.invalidate()
        self.clear_results()
        self.set_notice("已断开本应用的 ChatGPT 授权；其它后端配置保留。需要时可重新连接。")

    def reading_snapshot_ready(self):
        if not self.remote_reading:
            return True
        return (self.reading_ok_token is not None
                and self.reading_ok_token == self.state.token)

    def queue_remote_read(self, packet):
        if not self.remote_reading:
            return
        if self.reading_busy:
            self.pending_read = packet
            return
        self.reading_busy = True
        self.analysis_status.setText("正在用所选读取后端识别画面…")
        cfg = dict(self.config, relationship="普通联系人")
        frame_seq = packet.get("frame_seq", 0)
        image = packet.get("image", b"")
        hint = packet.get("title", "")

        def work():
            try:
                result = reading.read_chat(cfg, image, hint)
                error = ""
            except Exception as exc:
                result = None
                error = str(exc) if isinstance(exc, ValueError) else "远程读取失败，请检查读取后端设置。"
            self.bus.read_done.emit(frame_seq, result, error)

        threading.Thread(target=work, daemon=True).start()

    def reading_done(self, frame_seq, result, error):
        self.reading_busy = False
        if self.capture_session is None:
            self.pending_read = None
            return
        accepted = self.frame_freshness.accepts(frame_seq)
        if accepted and error:
            fallback = self.last_ocr_snapshot
            if fallback and self.frame_freshness.accepts(fallback[2]):
                self.state.update(fallback[0], fallback[1])
                self.set_editor(self.state.text)
                if fallback[0]:
                    self.chat_title.setText(fallback[0])
                self.changed_at = time.monotonic()
            if self.state.ready:
                self.reading_ok_token = self.state.token
            self.analysis_status.setText("远程读取失败，已回退本地 OCR")
            self.set_notice(error + " 已回退本地 OCR 识别。")
        elif accepted and result:
            title, messages = result
            changed = self.state.update(title, messages)
            if title:
                self.chat_title.setText(title)
            self.set_editor(self.state.text)
            if self.state.ready:
                self.reading_ok_token = self.state.token
                self.changed_at = time.monotonic()
                self.clear_results()
                self.set_connection_status("远程读取完成 · 微信已停靠"
                                           if self._overlay_safe
                                           else "远程读取完成 · 中央实时画面")
                if changed:
                    self.analysis_status.setText("已读取新对话，等待分析")
        pending, self.pending_read = self.pending_read, None
        if pending is not None and self.frame_freshness.accepts(pending.get("frame_seq", 0)):
            self.queue_remote_read(pending)

    def connect_chatgpt(self):
        if getattr(self, "subscription_busy", False):
            return
        self.subscription_busy = True
        self.chatgpt_button.setEnabled(False)
        self.set_notice("正在启动 OpenAI 官方授权流程。请在浏览器确认后返回此应用。")
        if not self.config.get("host_id"):
            self.config["host_id"] = "urn:uuid:" + str(uuid.uuid4())
        try:
            settings.save(self.config)  # Persist the stable host identifier before first sign-in.
        except OSError:
            self.subscription_busy = False
            self.chatgpt_button.setEnabled(True)
            self.notice.setText("无法安全保存本机授权配置，请检查用户目录权限。")
            return
        config = dict(self.config)

        def work():
            registration = [None]
            def remember_client(client_id):
                provisional = dict(config.get("subscription") or {}, client_id=client_id)
                settings.save_subscription(provisional)
                registration[0] = provisional
            try:
                credential = subscription.sign_in(config["host_id"], config.get("subscription"),
                                                  on_registration=remember_client)
                candidate = dict(config, subscription=credential)
                try:
                    models = subscription.list_models(candidate)
                    message = "" if models else "账号授权成功，但当前账号没有返回可选模型。请稍后刷新。"
                except ValueError:
                    models = []
                    message = "套餐授权已完成，但模型目录暂时不可用。请检查网络后重试刷新。"
                self.bus.subscription_done.emit((credential, models), None, message)
            except Exception as exc:
                error = str(exc) if isinstance(exc, ValueError) else "ChatGPT 授权失败，请检查网络后重试。"
                self.bus.subscription_done.emit((registration[0], []) if registration[0] else None,
                                                None, error)

        threading.Thread(target=work, daemon=True).start()

    def subscription_done(self, account_models, _unused, error):
        self.subscription_busy = False
        self.chatgpt_button.setEnabled(True)
        if account_models and error and not account_models[0].get("access_token"):
            self.config["subscription"] = account_models[0]
            self.settings_page.config = dict(self.config)
            self.notice.setText(error + " 已保留注册信息，可点击连接 ChatGPT 重试。")
            return
        if error and not account_models:
            self.notice.setText(error)
            return
        credential, models = account_models
        self.config["subscription"] = credential
        self.config["subscription_models"] = models
        available = {slug for _name, slug in models}
        if self.config.get("model") not in available and models:
            self.config["model"] = models[0][1]
        self.config["chatgpt_model"] = self.config.get("model") or ""
        self.config["backend"] = "chatgpt"
        try:
            settings.save(self.config)
        except OSError:
            self.notice.setText("ChatGPT 已连接，但本机未能安全保存授权。请检查用户目录权限并重试。")
            self.config["subscription"] = None
            return
        self.settings_page.config = dict(self.config)
        self.settings_page.backend.setCurrentIndex(
            max(0, self.settings_page.backend.findData("chatgpt")))
        self.settings_page.set_models(models)
        self.settings_page.update_chatgpt_state(self.config)
        self.refresh_effective_model()
        account = credential.get("email") or "ChatGPT 账号"
        if models:
            self.notice.setText(f"已连接 {account}，读取到 {len(models)} 个可用模型。已记住账号与模型，"
                                "下次重开无需重新授权。")
        else:
            self.notice.setText(error or "授权已保存，暂时没有可选模型；请检查网络后重新连接。")

    def analysis_done(self, token, result, error):
        self.busy = False
        if self.state.token != token:
            self.set_notice("会话或文字已变化，旧分析结果已丢弃。")
            return
        if error:
            self.analysis_status.setText("分析未完成")
            self.set_notice(error)
            return
        self._cached_analysis = (token, result)
        if not self.state.accepts(token):
            self.analysis_status.setText("正在确认聊天变化")
            return
        self.restore_analysis()

    def restore_analysis(self):
        if not self._cached_analysis:
            return
        token, result = self._cached_analysis
        if not self.state.accepts(token):
            return
        self.summary.setText("可能意图：" + result["intent"] + "\n\n可能需求：" + result["need"] +
                             "\n\n建议动作：" + result["action"] + "\n\n需留意：" + result["caution"])
        for text, copy, reply in zip(self.reply_boxes, self.copy_buttons, result["replies"]):
            text.setText(reply)
            copy.setEnabled(True)
        self.displayed_token = token
        self.analysis_status.setText("已生成 3 条建议")
        self.ai_toggle.setToolTip("有新的 AI 建议，点击展开")
        self.set_notice("分析完成。请核对建议后复制，再由你手动发送。")

    def copy_reply(self, index):
        if (self.copy_buttons[index].isEnabled() and self.displayed_token is not None
                and self.state.accepts(self.displayed_token)):
            QApplication.clipboard().setText(self.reply_boxes[index].text())
            self.set_notice("已复制回复。请到微信核对并手动发送。")
        else:
            self.clear_results()
            self.set_notice("对话已变化，旧回复不能复制。等待自动分析新内容。")

    def launch_wechat(self):
        path = self.settings_page.path.text().strip().strip('"') or self.config["wechat_path"].strip().strip('"')
        if not Path(path).is_file() or Path(path).suffix.lower() != ".exe":
            self.notice.setText("微信路径无效，请在设置中选择官方微信程序。")
            return
        try:
            subprocess.Popen([path], close_fds=True)
            self.notice.setText("已启动官方微信，请在微信中自行登录。")
        except OSError:
            self.notice.setText("微信启动失败，请手动启动或检查路径。")

    def open_settings(self):
        self.show_settings()

    def closeEvent(self, event):
        # Break widget -> window bound-method connections before Qt destroys the
        # widgets; otherwise the Python/Qt ownership cycle can crash on shutdown.
        for signal, slot_name in ((self.settings_page.account_action, "_account_action_slot"),
                                  (self.bus.queue_event, "_queue_action_slot")):
            slot = getattr(self, slot_name, None)
            if slot is not None:
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):
                    pass
                setattr(self, slot_name, None)
        self.scanner.stop()
        self.poll.stop()
        self.dock_timer.stop()
        if self._inference_queue is not None:
            try:
                self._inference_queue.stop()
            except Exception:  # noqa: BLE001 - fake queue / already stopped
                pass
        self.undock()
        self.stop_capture()
        try:
            codex_backend.shutdown()
        except OSError:
            pass
        event.accept()
