"""Locate the already-installed official WeChat executable.

The application must work without asking the user to browse for WeChat: the
official client can live in different directories (``Weixin.exe`` on 4.x,
``WeChat.exe`` on older builds). This module is read-only: it only inspects
running processes, the uninstall registry entries and the usual install
folders. It never launches or modifies WeChat.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

WECHAT_EXE_NAMES = ("weixin.exe", "wechat.exe")
_UNINSTALL_ROOTS = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
)


def _looks_like_wechat_exe(path: str) -> bool:
    try:
        candidate = Path(str(path).strip().strip('"'))
    except (OSError, ValueError, TypeError):
        return False
    return candidate.is_file() and candidate.name.casefold() in WECHAT_EXE_NAMES


def _running_candidates() -> list[str]:
    """Paths of WeChat processes that are running right now."""
    try:
        from .discovery import running_wechat_paths
        return [path for path in running_wechat_paths() if path]
    except (ImportError, OSError, ValueError, AttributeError):
        return []


def _registry_candidates() -> list[str]:
    """InstallLocation / DisplayIcon from Tencent uninstall entries."""
    if sys.platform != "win32":
        return []
    try:
        import winreg
    except ImportError:
        return []
    found: list[str] = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for root_name in _UNINSTALL_ROOTS:
            try:
                root = winreg.OpenKey(hive, root_name)
            except OSError:
                continue
            try:
                index = 0
                while True:
                    try:
                        sub_name = winreg.EnumKey(root, index)
                    except OSError:
                        break
                    index += 1
                    try:
                        sub = winreg.OpenKey(root, sub_name)
                    except OSError:
                        continue
                    try:
                        def value(name: str) -> str:
                            try:
                                return str(winreg.QueryValueEx(sub, name)[0] or "")
                            except OSError:
                                return ""
                        display_name = value("DisplayName")
                        folded = display_name.casefold()
                        if not display_name or not (
                                "微信" in display_name or "wechat" in folded
                                or "weixin" in folded):
                            continue
                        icon = value("DisplayIcon")
                        location = value("InstallLocation")
                        if icon:
                            found.append(icon)
                        if location:
                            location = location.strip().strip('"')
                            found.append(location)
                            for name in WECHAT_EXE_NAMES:
                                found.append(str(Path(location) / name))
                    finally:
                        try:
                            winreg.CloseKey(sub)
                        except OSError:
                            pass
            finally:
                try:
                    winreg.CloseKey(root)
                except OSError:
                    pass
    return found


def _common_candidates() -> list[str]:
    roots = []
    for env_name in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        value = os.environ.get(env_name)
        if value:
            roots.append(Path(value))
    found: list[str] = []
    for root in roots:
        for relative in ("Tencent/Weixin", "Tencent/WeChat"):
            base = root / relative
            for name in WECHAT_EXE_NAMES:
                found.append(str(base / name))
    return found


def _normalized_candidate(path: str) -> str | None:
    text = (path or "").strip().strip('"')
    if not text:
        return None
    candidate = Path(text)
    if candidate.is_dir():
        for name in WECHAT_EXE_NAMES:
            child = candidate / name
            if _looks_like_wechat_exe(str(child)):
                return str(child)
        return None
    if _looks_like_wechat_exe(text):
        return os.path.normpath(text)
    return None


def detect_wechat_path() -> str | None:
    """Return the existing official WeChat executable, or ``None``.

    Order matters: a running client is the strongest signal (it is the install
    the user is actually using), then the uninstall registry entries, then the
    conventional install folders.
    """
    seen: set[str] = set()
    for group in (_running_candidates(), _registry_candidates(), _common_candidates()):
        for item in group:
            key = str(item).strip().strip('"').casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            resolved = _normalized_candidate(item)
            if resolved:
                return resolved
    return None


def ensure_wechat_path(config: dict, *, save: bool = False) -> str:
    """Fill ``config['wechat_path']`` when it is missing or invalid.

    Returns the effective path (possibly the already valid configured one).
    Saving is opt-in so callers that only need a runtime value (tests/CLI)
    never rewrite the user's settings.
    """
    current = str(config.get("wechat_path") or "").strip().strip('"')
    if _looks_like_wechat_exe(current):
        return current
    detected = detect_wechat_path()
    if not detected:
        return current
    config["wechat_path"] = detected
    if save:
        try:
            from . import settings
            settings.save(config)
        except (OSError, ValueError, ImportError):
            pass
    return detected
