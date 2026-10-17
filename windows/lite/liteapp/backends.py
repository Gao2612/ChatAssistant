"""Backend choices and routing.

Reading (screen -> chat text) and analysis (chat text -> suggestions) are two
independent settings:

* reading: ``ocr`` (local, offline, default), ``api`` (multimodal OpenAI-compatible
  vision endpoint), ``codex`` (official Codex app-server with image input);
* analysis: ``chatgpt`` (official Sign in with ChatGPT plan), ``api`` (compatible
  remote endpoint), ``local`` (compatible endpoint on this machine), ``codex``
  (official Codex app-server).

Nothing here sends chat text or images by itself; callers run it in a worker
thread after the user enabled automatic analysis or pressed a UI action.
"""
from urllib.parse import urlsplit

from . import analysis, codex_backend, reading, subscription

READING_CHOICES = (
    ("本地 OCR（离线，不上传画面）", "ocr"),
    ("多模态模型（兼容视觉 API）", "api"),
    ("Codex 应用（官方 app-server）", "codex"),
)

ANALYSIS_CHOICES = (
    ("ChatGPT Plus / Pro 套餐", "chatgpt"),
    ("兼容模型 API", "api"),
    ("本地模型服务（本机兼容 API）", "local"),
    ("Codex 应用（官方 app-server）", "codex"),
)


def label_for(choices, value):
    return next((text for text, key in choices if key == value), str(value))


def reading_backend(config):
    return (config.get("reading_backend") or "ocr").strip()


def analysis_backend(config):
    return (config.get("backend") or "chatgpt").strip()


def reading_ready(config):
    return reading.reading_ready(config)


def analysis_ready(config):
    backend = analysis_backend(config)
    if backend == "chatgpt":
        return bool(config.get("subscription") and config.get("model")
                    and config.get("model") in {slug for _name, slug in
                                                config.get("subscription_models", [])})
    if backend in ("api", "local"):
        if not (config.get("model") or "").strip():
            return False
        return backend == "local" or bool((config.get("api_key") or "").strip())
    if backend == "codex":
        return codex_backend.available(config)
    return False


def analyze(config, text):
    """Run the selected analysis backend; returns the parsed suggestion dict."""
    backend = analysis_backend(config)
    if backend == "chatgpt":
        return subscription.analyze(config, text)
    if backend in ("api", "local"):
        return analysis.analyze(config, text)
    if backend == "codex":
        return codex_backend.analyze(config, text)
    raise ValueError("未知的分析后端，请在设置中重新选择。")


def read_chat(config, image_bytes, title_hint=""):
    """Run the selected reading backend; returns (title, messages)."""
    return reading.read_chat(config, image_bytes, title_hint)


def _host_is_local(url):
    return urlsplit(url).hostname in ("localhost", "127.0.0.1", "::1")


def _validate_analysis_endpoint(config, backend):
    base = (config.get("base_url") or "").strip()
    if backend == "local" and not base:
        base = analysis.DEFAULT_LOCAL_BASE
    url = analysis.endpoint(base)
    if backend == "api" and not (config.get("api_key") or "").strip():
        raise ValueError("请填写兼容 API 的 Key。")
    return url


def validate_reading(config):
    """Validate the reading configuration; raises ValueError with a user message."""
    backend = reading_backend(config)
    if backend == "ocr":
        return
    if backend == "api":
        url = analysis.endpoint(config.get("reading_base_url") or "")
        if not (config.get("reading_model") or "").strip():
            raise ValueError("请填写读取模型名称。")
        if not _host_is_local(url) and not (config.get("reading_api_key") or "").strip():
            raise ValueError("远程视觉接口需要读取 API Key。")
        return
    if backend == "codex":
        explicit = (config.get("codex_path") or "").strip()
        if explicit and not codex_backend.find_codex(config):
            raise ValueError("填写的 Codex 路径无效，请检查 codex.exe 位置。")
        return
    raise ValueError("未知的读取后端，请重新选择。")


def validate_analysis(config):
    """Validate the analysis configuration; raises ValueError with a user message."""
    backend = analysis_backend(config)
    if backend == "chatgpt":
        if not config.get("subscription"):
            raise ValueError("请先连接 ChatGPT 套餐，再选择账号模型。")
        if not config.get("model"):
            raise ValueError("请先连接 ChatGPT 并读取账号模型目录。")
        if config.get("model") not in {slug for _name, slug in config.get("subscription_models", [])}:
            raise ValueError("请选择当前 ChatGPT 账号模型目录中的模型。")
        return
    if backend in ("api", "local"):
        _validate_analysis_endpoint(config, backend)
        if not (config.get("model") or "").strip():
            raise ValueError("请输入模型名称。")
        return
    if backend == "codex":
        explicit = (config.get("codex_path") or "").strip()
        if explicit and not codex_backend.find_codex(config):
            raise ValueError("填写的 Codex 路径无效，请检查 codex.exe 位置。")
        return
    raise ValueError("未知的分析后端，请重新选择。")


def hint_for_analysis(config):
    backend = analysis_backend(config)
    if backend == "chatgpt":
        return "请先点击“连接 ChatGPT 套餐”完成授权，并选择账号模型。"
    if backend == "api":
        return "请填写兼容 API 地址、模型和 Key。"
    if backend == "local":
        return "请填写本机模型服务地址与模型名（默认 http://127.0.0.1:11434/v1）。"
    if backend == "codex":
        return "需要本机已安装并登录官方 Codex；可在设置中检测账号与模型。"
    return "请选择分析后端。"
