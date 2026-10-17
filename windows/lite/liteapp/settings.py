import base64
import ctypes
import json
import os
import uuid
from pathlib import Path
from ctypes import wintypes

DEFAULTS = {
    "wechat_path": r"C:\Program Files\Tencent\Weixin\Weixin.exe",
    # Analysis backends: chatgpt (official plan), api (compatible), local (compatible
    # localhost service), codex (official app-server). Every backend remembers its own
    # model so switching back or restarting the app does not lose the binding.
    "base_url": "https://api.deepseek.com/v1", "api_provider": "",
    "model": "deepseek-chat", "api_model": "deepseek-chat",
    "chatgpt_model": "", "local_model": "", "codex_model": "",
    "backend": "chatgpt", "host_id": "",
    "style": "自然简短，不编造事实", "auto_connect": True, "auto_analysis": True,
    # Reading backends: ocr (local, offline), api (multimodal vision API),
    # codex (official app-server image input). Reading and analysis are independent.
    "reading_backend": "ocr", "reading_base_url": "", "reading_model": "",
    "codex_path": "",
}


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data, decrypt=False):
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    dll = ctypes.WinDLL("crypt32", use_last_error=True)
    func = dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    func.restype = wintypes.BOOL
    func.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    if not func(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("无法使用 Windows 保护密钥。")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree.restype = ctypes.c_void_p
        kernel.LocalFree(output.data)


def data_dir():
    path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ChatAssistantLite"
    path.mkdir(parents=True, exist_ok=True)
    return path


def load(folder=None):
    result = dict(DEFAULTS, host_id="urn:uuid:" + str(uuid.uuid4()), api_key="", reading_api_key="",
                  subscription=None, subscription_models=[], _warning="")
    file = (Path(folder) if folder else data_dir()) / "settings.json"
    if not file.exists():
        return result
    try:
        saved = json.loads(file.read_text(encoding="utf-8"))
        result.update({k: saved[k] for k in DEFAULTS if k in saved and isinstance(saved[k], type(DEFAULTS[k]))})
        if saved.get("protected_key"):
            result["api_key"] = crypt(base64.b64decode(saved["protected_key"]), True).decode("utf-8")
        if saved.get("protected_reading_key"):
            result["reading_api_key"] = crypt(
                base64.b64decode(saved["protected_reading_key"]), True).decode("utf-8")
        if not result["host_id"]:
            result["host_id"] = "urn:uuid:" + str(uuid.uuid4())
        if saved.get("protected_subscription"):
            result["subscription"] = json.loads(crypt(base64.b64decode(saved["protected_subscription"]), True).decode("utf-8"))
        if isinstance(saved.get("subscription_models"), list):
            result["subscription_models"] = saved["subscription_models"]
    except (OSError, ValueError, TypeError, KeyError):
        result["_warning"] = "设置或密钥无法读取，请重新保存设置。"
    return result


def save(config, folder=None):
    dest = Path(folder) if folder else data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    saved = {k: config.get(k, v) for k, v in DEFAULTS.items()}
    saved["protected_key"] = base64.b64encode(crypt(config["api_key"].encode())).decode() if config.get("api_key") else ""
    saved["protected_reading_key"] = (base64.b64encode(crypt(config["reading_api_key"].encode())).decode()
                                      if config.get("reading_api_key") else "")
    credential = config.get("subscription")
    saved["protected_subscription"] = base64.b64encode(crypt(json.dumps(credential).encode("utf-8"))).decode() if credential else ""
    saved["subscription_models"] = config.get("subscription_models", [])
    temp = dest / "settings.json.tmp"
    temp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, dest / "settings.json")


def save_subscription(subscription, folder=None):
    """Update only renewable credentials, preserving settings saved by the UI."""
    current = load(folder)
    current["subscription"] = subscription
    save(current, folder)
