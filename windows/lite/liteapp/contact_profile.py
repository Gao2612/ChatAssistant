"""联系人专属档案：按微信会话标题保存风格/画像/亲密度，DPAPI 加密落盘。

用户新需求（Lead 2026-10-18）：为每个联系人保存专属档案（显示名、风格提示词、
用户看到的风格报告、画像摘要、亲密度、收藏/固定状态），导入 HTML/PDF 全量记录后
可按联系人复用；本模块只负责**本地、加密、确定性的存储与 StyleProfile 衔接**，
不修改 ``ui.py`` / ``history_ui.py``（界面由 Lead 接线）。

设计边界：

* 标题只是**显示名/查找键**，不是身份：同一个标题在不同 ``scope_person_id``
  下是两条独立档案，绝不互相覆盖；跨账号/联系人读取只能显式给 scope。
* 键规范化与既有 ``MainWindow._contact_key`` 一致：NFKC + 去首尾空白 +
  casefold + 去掉所有空白；用于查找、去重和 favorite 匹配，原始标题仍保留。
* 所有正文类字段（style_prompt/display_report/persona）和标题一起加密写入
  ``contact_profiles.dat``（默认 ``settings.data_dir()``）；文件头部没有明文
  JSON/正文；默认使用 ``settings.crypt`` 的 Windows DPAPI，测试可注入
  ``protect``/``unprotect`` 变换。
* 损坏/无法解密的文件 fail-soft：``all()``/``get()``/``delete()`` 当作空仓库，
  不抛异常、不阻塞 App；下一次 ``save()`` 前会把损坏文件备份为
  ``contact_profiles.dat.corrupt`` 再重建，避免静默覆盖唯一副本。
* 单进程内用 ``RLock`` 串行化读改写，并以临时文件 + ``os.replace`` 原子落盘。

本模块不联网、不读取微信数据库；``profile_from_style`` 只做 duck-typed 读取，
不改变 StyleProfile 本身，也不把真实聊天原文带进档案。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping

from .contracts import ScopeKey

__all__ = [
    "ContactProfile",
    "ContactProfileStore",
    "FILENAME",
    "KIND",
    "SCHEMA_VERSION",
    "normalize_title",
    "profile_from_style",
]

KIND = "contact_profile"
SCHEMA_VERSION = 1
FILENAME = "contact_profiles.dat"

Protect = Callable[[bytes], bytes]


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def normalize_title(title: Any) -> str:
    """返回查找用规范化键；空标题返回空串（由调用方决定拒绝或视为未找到）。"""
    text = unicodedata.normalize("NFKC", str(title or ""))
    text = text.strip().casefold()
    return "".join(text.split())


def _clean_scope(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{label} 必须是字符串或 None。")
    return value if value else None


def _optional_profile_value(value: Any, label: str) -> Any:
    """档案内容字段兼容字符串或字符串列表（旧/新 UI/模型输出都可读）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value if value else None
    if isinstance(value, (list, tuple)):
        items: list[str] = []
        for item in value:
            if item is None:
                continue
            text = str(item).strip()
            if text:
                items.append(text)
        return items or None
    raise TypeError(f"{label} 必须是字符串、字符串列表或 None。")


def _scope_person_id(scope: Any) -> str | None:
    """从 ScopeKey/显式 person_id/None 提取稳定的 scope_person_id。"""
    if scope is None:
        return None
    if isinstance(scope, ScopeKey):
        return _clean_scope(scope.person_id)
    if isinstance(scope, str):
        return _clean_scope(scope)
    person_id = getattr(scope, "person_id", None)
    if person_id is not None:
        return _clean_scope(person_id)
    raise TypeError("scope 必须是 ScopeKey、person_id 字符串或 None。")


@dataclass
class ContactProfile:
    """一位联系人的专属档案；标题是显示/查找键，scope 才是身份。

    字段分两类：身份/持久元信息（title/scope/affection/updated_at/favorite/
    model_analyzed）与可读档案内容（style/report/persona/relation/topics/
    preferences/taboos/reply_style/important_dates）。新需求（2026-10-18）在
    旧字段后追加的档案内容字段全部可选，旧文件缺失时读成 None，模型分析完成
    后由调用方写回并把 ``model_analyzed`` 置 True。
    """

    title: str
    scope_person_id: str | None = None
    style_prompt: str | None = None
    display_report: str | None = None
    persona: str | None = None
    affection_score: float | None = None
    updated_at: str = ""
    favorite: bool = False
    relation: str | None = None
    topics: str | list[str] | None = None
    preferences: str | list[str] | None = None
    taboos: str | list[str] | None = None
    reply_style: str | None = None
    important_dates: str | list[str] | None = None
    model_analyzed: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.title, str):
            raise TypeError("title 必须是字符串。")
        self.title = self.title.strip()
        if not normalize_title(self.title):
            raise ValueError("联系人标题不能为空。")
        self.scope_person_id = _clean_scope(self.scope_person_id)
        self.style_prompt = _optional_text(self.style_prompt, "style_prompt")
        self.display_report = _optional_text(self.display_report, "display_report")
        self.persona = _optional_text(self.persona, "persona")
        if self.affection_score is not None:
            if isinstance(self.affection_score, bool) or not isinstance(
                    self.affection_score, (int, float)):
                raise TypeError("affection_score 必须是数字或 None。")
            self.affection_score = float(self.affection_score)
        if not isinstance(self.updated_at, str):
            raise TypeError("updated_at 必须是字符串。")
        self.updated_at = self.updated_at.strip()
        self.favorite = bool(self.favorite)
        self.relation = _optional_text(self.relation, "relation")
        self.topics = _optional_profile_value(self.topics, "topics")
        self.preferences = _optional_profile_value(self.preferences, "preferences")
        self.taboos = _optional_profile_value(self.taboos, "taboos")
        self.reply_style = _optional_text(self.reply_style, "reply_style")
        self.important_dates = _optional_profile_value(self.important_dates,
                                                       "important_dates")
        self.model_analyzed = bool(self.model_analyzed)

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload（只应由 ContactProfileStore 加密落盘）。"""
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "title": self.title,
            "scope_person_id": self.scope_person_id,
            "style_prompt": self.style_prompt,
            "display_report": self.display_report,
            "persona": self.persona,
            "affection_score": self.affection_score,
            "updated_at": self.updated_at,
            "favorite": self.favorite,
            "relation": self.relation,
            "topics": self.topics,
            "preferences": self.preferences,
            "taboos": self.taboos,
            "reply_style": self.reply_style,
            "important_dates": self.important_dates,
            "model_analyzed": self.model_analyzed,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ContactProfile":
        if not isinstance(payload, Mapping):
            raise TypeError("payload 必须是字典。")
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("不支持的 contact_profile schema。")
        if payload.get("kind") != KIND:
            raise ValueError("不是 contact_profile 记录。")
        title = payload.get("title")
        if not isinstance(title, str):
            raise ValueError("contact_profile 记录缺少标题。")
        return cls(
            title=title,
            scope_person_id=payload.get("scope_person_id"),
            style_prompt=payload.get("style_prompt"),
            display_report=payload.get("display_report"),
            persona=payload.get("persona"),
            affection_score=payload.get("affection_score"),
            updated_at=str(payload.get("updated_at") or ""),
            favorite=bool(payload.get("favorite", False)),
            # 旧档案没有这些键：如实读成 None/False，不伪造分析结果。
            relation=payload.get("relation"),
            topics=payload.get("topics"),
            preferences=payload.get("preferences"),
            taboos=payload.get("taboos"),
            reply_style=payload.get("reply_style"),
            important_dates=payload.get("important_dates"),
            model_analyzed=bool(payload.get("model_analyzed", False)),
        )


class ContactProfileStore:
    """一个数据目录下的加密联系人档案仓库（默认 ``contact_profiles.dat``）。

    ``folder`` 缺省用 ``settings.data_dir()``；测试应传临时目录并注入
    ``protect``/``unprotect`` 变换，避免依赖 Windows DPAPI。
    """

    def __init__(self, folder: Path | str | None = None, protect: Protect | None = None,
                 unprotect: Protect | None = None):
        from . import settings  # 延迟导入，保持模块可离线导入/测试
        self.folder = Path(folder) if folder is not None else settings.data_dir()
        self.path = self.folder / FILENAME
        self.protect: Protect = protect or settings.crypt
        self.unprotect: Protect = unprotect or (lambda raw: settings.crypt(raw, True))
        self._lock = threading.RLock()
        self._records: dict[tuple[str, str | None], ContactProfile] = {}
        self._loaded = False
        self._corrupt = False

    # ------------------------------------------------------------------ public
    def save(self, profile: ContactProfile) -> ContactProfile:
        """新增/覆盖一条档案；同一规范化标题 + 同一 scope 覆盖，不同 scope 并存。"""
        if not isinstance(profile, ContactProfile):
            raise TypeError("profile 必须是 ContactProfile。")
        if not normalize_title(profile.title):
            raise ValueError("联系人标题不能为空。")
        with self._lock:
            self._ensure_loaded_locked()
            key = self._identity(profile)
            stored = self._copy(profile)
            if not stored.updated_at:
                stored.updated_at = _now()
            self._records[key] = stored
            self._write_locked()
            return self._copy(stored)

    def get(self, title: str, scope_person_id: str | None = None) -> ContactProfile | None:
        """按标题（+ 可选 scope）读回；同名多 scope 且未指定 scope 时返回 None。"""
        with self._lock:
            self._ensure_loaded_locked()
            profile = self._resolve_locked(title, scope_person_id)
            return None if profile is None else self._copy(profile)

    def all(self) -> list[ContactProfile]:
        """全部档案（按规范化标题、scope 稳定排序）；损坏文件返回空列表。"""
        with self._lock:
            self._ensure_loaded_locked()
            profiles = sorted(self._records.values(),
                              key=lambda item: (normalize_title(item.title),
                                                item.scope_person_id or ""))
            return [self._copy(item) for item in profiles]

    def delete(self, title: str, scope_person_id: str | None = None) -> bool:
        """删除一条档案；同名多 scope 且未指定 scope 时拒绝猜测，返回 False。"""
        with self._lock:
            self._ensure_loaded_locked()
            profile = self._resolve_locked(title, scope_person_id)
            if profile is None:
                return False
            self._records.pop(self._identity(profile), None)
            self._write_locked()
            return True

    def set_favorite(self, title: str, favorite: bool,
                     scope_person_id: str | None = None) -> ContactProfile | None:
        """切换收藏/固定状态；档案不存在时创建最小档案（同 scope）。"""
        normalized = normalize_title(title)
        if not normalized:
            raise ValueError("联系人标题不能为空。")
        wanted = bool(favorite)
        with self._lock:
            self._ensure_loaded_locked()
            profile = self._resolve_locked(title, scope_person_id)
            if profile is None:
                if scope_person_id is None:
                    matches = self._matches_locked(normalized)
                    if len(matches) > 1:
                        raise ValueError(
                            "同一标题存在多个联系人档案，请指定 scope_person_id。")
                profile = ContactProfile(title=str(title).strip(),
                                         scope_person_id=scope_person_id,
                                         favorite=wanted, updated_at=_now())
                self._records[self._identity(profile)] = profile
            else:
                profile = self._copy(profile)
                profile.favorite = wanted
                profile.updated_at = _now()
                self._records[self._identity(profile)] = profile
            self._write_locked()
            return self._copy(profile)

    # ----------------------------------------------------------------- internal
    @staticmethod
    def _identity(profile: ContactProfile) -> tuple[str, str | None]:
        return (normalize_title(profile.title), _clean_scope(profile.scope_person_id))

    @staticmethod
    def _copy(profile: ContactProfile) -> ContactProfile:
        return ContactProfile.from_dict(profile.as_dict())

    def _matches_locked(self, normalized_title: str) -> list[ContactProfile]:
        return [item for (title_key, _scope), item in self._records.items()
                if title_key == normalized_title]

    def _resolve_locked(self, title: str, scope_person_id: str | None) -> ContactProfile | None:
        normalized = normalize_title(title)
        if not normalized:
            return None
        exact = self._records.get((normalized, _clean_scope(scope_person_id)))
        if exact is not None:
            return exact
        if _clean_scope(scope_person_id) is None:
            matches = self._matches_locked(normalized)
            if len(matches) == 1:
                return matches[0]
        return None

    def _ensure_loaded_locked(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._records = {}
        self._corrupt = False
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return
        except OSError:
            self._corrupt = True
            return
        try:
            payload = json.loads(self.unprotect(raw).decode("utf-8"))
            if not isinstance(payload, Mapping) or payload.get("schema_version") != SCHEMA_VERSION \
                    or payload.get("kind") != KIND:
                raise ValueError("不支持的 contact_profiles 文件格式")
            records: dict[tuple[str, str | None], ContactProfile] = {}
            for item in payload.get("profiles") or ():
                profile = ContactProfile.from_dict(item)
                records[self._identity(profile)] = profile
            self._records = records
        except (OSError, TypeError, ValueError, UnicodeDecodeError):
            self._records = {}
            self._corrupt = True

    def _write_locked(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        if self._corrupt and self.path.exists():
            # 保留一份损坏文件，避免下一行原子写入把唯一副本静默覆盖。
            try:
                os.replace(self.path, self.path.with_name(self.path.name + ".corrupt"))
            except OSError:
                pass
        payload = {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "profiles": [self._copy(item).as_dict() for item in
                         sorted(self._records.values(),
                                key=lambda item: (normalize_title(item.title),
                                                  item.scope_person_id or ""))],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        blob = self.protect(raw)
        fd, temp_path = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp",
                                         dir=str(self.folder))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
            os.replace(temp_path, self.path)
        finally:
            try:
                if os.path.exists(temp_path):
                    os.unlink(temp_path)
            except OSError:
                pass
        self._corrupt = False


def profile_from_style(display_name: str, style_profile: Any, scope: Any = None,
                       persona: str = "", affection: Any = None) -> ContactProfile:
    """把 ``StyleProfile``（duck-typed）转成联系人档案，不修改原对象。

    ``style_profile`` 只需要有 ``prompt_style`` / ``display_report`` 属性（缺省
    或 None 视为空）；也接受 dict，便于界面/测试用轻量对象。``scope`` 可以是
    ``ScopeKey``、person_id 字符串或 None；``persona``/``affection`` 由调用方提供，
    本函数不推断画像或关系分数。
    """
    title = str(display_name or "").strip()
    if not normalize_title(title):
        raise ValueError("联系人标题不能为空。")
    prompt = _style_value(style_profile, "prompt_style")
    report = _style_value(style_profile, "display_report")
    return ContactProfile(
        title=title,
        scope_person_id=_scope_person_id(scope),
        style_prompt=_optional_text(prompt, "prompt_style"),
        display_report=_optional_text(report, "display_report"),
        persona=_optional_text(persona, "persona"),
        affection_score=affection,
        updated_at=_now(),
        favorite=False,
    )


def _style_value(style_profile: Any, name: str) -> Any:
    if style_profile is None:
        return None
    if isinstance(style_profile, Mapping):
        return style_profile.get(name)
    return getattr(style_profile, name, None)
