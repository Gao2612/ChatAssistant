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
    # Mirror preview is the default: resizing the assistant window never moves or
    # resizes the native WeChat window. Users can opt into docking explicitly.
    "dock_wechat": False,
    # Background monitoring is opt-in and bounded; other windows keep their own
    # normal window and are never moved or typed into.
    "background_monitor": False,
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


def settings_path(folder=None):
    """Path of the single settings file for a data directory."""
    return (Path(folder) if folder is not None else data_dir()) / "settings.json"


def read_raw(folder=None):
    """Read the raw JSON document; missing/corrupt files yield an empty document."""
    file = settings_path(folder)
    try:
        if not file.exists():
            return {}
        raw = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def write_raw(payload, folder=None):
    """Atomically replace the settings document (single writer expectation)."""
    dest = Path(folder) if folder is not None else data_dir()
    dest.mkdir(parents=True, exist_ok=True)
    temp = dest / "settings.json.tmp"
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, dest / "settings.json")


def load(folder=None):
    """Load settings plus the token-free account view for the active registration.

    The legacy 0.2 single ``protected_subscription`` record is migrated in memory
    (never rewritten here) so the first save/store operation persists it as a
    registration while keeping the original ``host_id``/``client_id``.
    """
    dest = Path(folder) if folder is not None else data_dir()
    result = dict(DEFAULTS, host_id="urn:uuid:" + str(uuid.uuid4()), api_key="",
                  reading_api_key="", subscription=None, subscription_models=[],
                  registration_id=None, active_registration_id=None,
                  registrations=[], _account_revision=0, _data_dir=str(dest),
                  _warning="")
    saved = read_raw(dest)
    if not saved:
        return result
    try:
        result.update({k: saved[k] for k in DEFAULTS
                       if k in saved and isinstance(saved[k], type(DEFAULTS[k]))})
        if saved.get("protected_key"):
            result["api_key"] = crypt(base64.b64decode(saved["protected_key"]),
                                      True).decode("utf-8")
        if saved.get("protected_reading_key"):
            result["reading_api_key"] = crypt(
                base64.b64decode(saved["protected_reading_key"]), True).decode("utf-8")
        if not result["host_id"]:
            result["host_id"] = "urn:uuid:" + str(uuid.uuid4())
    except (OSError, ValueError, TypeError, KeyError):
        result["_warning"] = "设置或密钥无法读取，请重新保存设置。"
    # Placeholder keys from tests/probes are never valid credentials; surface them
    # instead of silently failing every analysis request.
    if result.get("api_key") in ("test-key", "synthetic", "synthetic-key", "test"):
        result["_warning"] = (
            "当前 API Key 是测试占位值（可能由早期测试写入），请在设置中重新填写"
            "真实 Key 并保存。")
    try:
        from . import account_store
        result.update(account_store.settings_view(saved, dest))
    except (OSError, ValueError, TypeError, KeyError):
        result["_warning"] = "ChatGPT 授权记录无法读取，请重新连接账号。"
    return result


def save(config, folder=None):
    """Save non-credential settings and persist the authoritative account state.

    Credential material is owned by :mod:`liteapp.account_store`: a UI snapshot may
    only update it when it was loaded from the same record revision. A stale
    snapshot can therefore never write an old access token back.
    """
    dest = Path(folder) if folder is not None else data_dir()
    from . import account_store
    with account_store.folder_lock(dest):
        raw = read_raw(dest)
        saved = {k: config.get(k, v) for k, v in DEFAULTS.items()}
        saved["protected_key"] = (base64.b64encode(crypt(config["api_key"].encode())).decode()
                                  if config.get("api_key") else "")
        saved["protected_reading_key"] = (
            base64.b64encode(crypt(config["reading_api_key"].encode())).decode()
            if config.get("reading_api_key") else "")
        try:
            account_store.write_settings_view(saved, raw, config)
        except (OSError, ValueError, TypeError):
            # Never destroy credential material that cannot be decoded/encrypted.
            for key in ("accounts", "protected_subscription", "subscription_models"):
                if key in raw:
                    saved[key] = raw[key]
        write_raw(saved, dest)


def save_subscription(subscription, folder=None):
    """Update only identity metadata for the active registration (legacy helper).

    Token material is intentionally ignored here: renewable credentials are written
    exclusively by :class:`liteapp.account_store.AccountStore` (refresh CAS and
    authorization completion), so a caller holding an old snapshot cannot replace a
    newer token through this path.
    """
    if not isinstance(subscription, dict):
        return
    identity = {key: subscription[key] for key in
                ("client_id", "subject", "email") if isinstance(subscription.get(key), str)
                and subscription.get(key)}
    if not identity:
        return
    from . import account_store
    store = account_store.AccountStore(folder)
    active = store.active_registration_id()
    store.save_credentials(active, identity)
