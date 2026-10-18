"""QSS 资源定位、``@token`` 替换与缓存。

开发态优先读 ``windows/lite/liteapp/resources``；PyInstaller onedir 下
先找 ``sys._MEIPASS/liteapp/resources``，再找 ``sys._MEIPASS/resources``
以及 exe 同级 ``resources/``。读取失败只打印一行警告并返回空串，
UI 不能因为缺少样式文件而起不来。
"""
from __future__ import annotations

import functools
import re
import sys
from pathlib import Path

from . import theme

__all__ = ["resource_path", "load_qss", "load_stylesheet", "clear_cache"]

_TOKEN_RE = re.compile(r"@[A-Za-z0-9_-]+")


@functools.lru_cache(maxsize=1)
def _candidate_roots() -> tuple[Path, ...]:
    """按优先级列出所有可能的 ``resources`` 目录。"""
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        bundle = Path(meipass)
        roots.append(bundle / "liteapp" / "resources")
        roots.append(bundle / "resources")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        roots.append(exe_dir / "liteapp" / "resources")
        roots.append(exe_dir / "resources")
        roots.append(exe_dir / "_internal" / "liteapp" / "resources")
    package_dir = Path(__file__).resolve().parent  # .../liteapp/design
    liteapp_dir = package_dir.parent               # .../liteapp
    roots.append(liteapp_dir / "resources")
    roots.append(liteapp_dir.parent / "resources")

    seen: set[Path] = set()
    ordered: list[Path] = []
    for root in roots:
        if root not in seen:
            seen.add(root)
            ordered.append(root)
    return tuple(ordered)


def resource_path(rel: str) -> Path:
    """返回 ``resources/`` 下资源的第一个存在路径；缺失时返回首选路径。

    不抛异常，也不缓存：调用方可以据此提示文件该放在哪里。
    """
    relative = Path(rel)
    if relative.is_absolute():
        return relative
    roots = _candidate_roots()
    for root in roots:
        candidate = root / relative
        if candidate.is_file():
            return candidate
    return roots[0] / relative


def _style_path(name: str) -> Path:
    """先找 ``resources/styles/<name>``，再接受 ``resources/<name>``。

    ``load_qss("global.qss")`` 因此不需要调用方写死 ``styles/`` 前缀。
    """
    relative = Path(name)
    if relative.is_absolute() or relative.parent != Path("."):
        return resource_path(name)
    nested = resource_path(str(Path("styles") / relative))
    if nested.is_file():
        return nested
    flat = resource_path(name)
    if flat.is_file():
        return flat
    return nested


def _substitute_tokens(text: str) -> str:
    """替换 ``@token``；未登记的 token 原样保留，方便发现拼写错误。"""
    values = theme.qss_vars()
    return _TOKEN_RE.sub(
        lambda match: values.get(match.group(0), match.group(0)),
        text,
    )


@functools.lru_cache(maxsize=16)
def load_qss(name: str = "global.qss") -> str:
    """读取单个 QSS、替换 token 后返回；文件缺失/不可读时返回空串。"""
    path = _style_path(name)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        print(f"[style_loader] 无法读取 QSS，已跳过：{path} ({exc})")
        return ""
    return _substitute_tokens(raw)


@functools.lru_cache(maxsize=16)
def load_stylesheet(*names: str) -> str:
    """按顺序拼接多个 QSS；空块自动跳过，未传文件名时返回空串。"""
    if not names:
        return ""
    return "\n\n".join(
        chunk for chunk in (load_qss(name) for name in names) if chunk
    )


def clear_cache() -> None:
    """清空 QSS 与路径缓存（开发态热更新、测试用）。"""
    load_stylesheet.cache_clear()
    load_qss.cache_clear()
    _candidate_roots.cache_clear()
