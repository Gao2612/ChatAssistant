"""联系人基础档案的本地自动生成（导入 HTML/PDF 后立即可用）。

用户新需求（Lead 2026-10-18）：HTML/PDF 全量历史导入成功后，先不等待模型分析，
立即生成一份**本地、可解释、无真实原文泄漏**的基础档案，让档案页/悬浮球有内容；
模型逐句分析完成后再补充并标记 ``model_analyzed=True``。

本模块只做本地确定性推断：

* 关系定位默认 ``"普通联系人/待确认"``（本地没有足够证据，绝不猜测关系）；
* 常聊话题来自固定中文关键词表与导入来源格式计数（HTML/PDF/ExportWeChat），
  只输出话题名与计数，不包含聊天正文；
* 重要日期用固定正则识别“生日/纪念日/合同交付/入职/节日”等标签附近的日期，
  只输出标签 + 规范化日期；
* ``reply_style``/``style_prompt``/``display_report`` 可来自调用方已有的
  ``StyleProfile``（duck-typed）；没有则保持 None；
* 保存到与 HistoryStore 同目录的 :class:`ContactProfileStore`，并标记
  ``model_analyzed=False``；``needs_model_analysis`` 供 UI 决定是否触发模型
  逐句档案。

不联网、不读微信数据库、不修改 ``ui.py``/``history_ui.py`` 之外的界面；所有
输入都来自本地已导入记录。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .contact_profile import ContactProfile, ContactProfileStore
from .contracts import ScopeKey

__all__ = [
    "DEFAULT_RELATION",
    "build_local_profile",
    "needs_model_analysis",
]

DEFAULT_RELATION = "普通联系人/待确认"
MAX_TOPIC_ITEMS = 6
MAX_IMPORTANT_DATES = 6
MAX_SCAN_RECORDS = 50_000
MAX_SCAN_CHARS = 4_000_000

# 只保留“可解释的话题名 + 命中次数”，不引用任何原文片段。
_TOPIC_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("预算与价格", re.compile(r"预算|报价|价格|折扣|优惠|成本|价钱|费用")),
    ("库存与交期", re.compile(r"库存|现货|发货|交期|送达|到货|交付|物流|快递")),
    ("产品需求", re.compile(r"需求|规格|型号|设备|功能|尺寸|参数|方案")),
    ("售后与风险", re.compile(r"售后|保修|退货|退款|故障|投诉|维修")),
    ("工作与学习", re.compile(r"工作|项目|会议|面试|简历|学习|考试|课程|培训|作业")),
    ("生活与家庭", re.compile(r"家里|家人|父母|孩子|宝宝|周末|天气|吃饭|旅行|"
                              r"旅游|电影|生日|纪念日")),
    ("健康与作息", re.compile(r"医院|健康|生病|感冒|发烧|睡眠|熬夜|吃药|体检|休息")),
    ("情感与关系", re.compile(r"想你|喜欢|爱你|感情|吵架|和好|朋友|约会|礼物|"
                              r"关心|陪伴")),
)

# 日期：支持 2026-05-20 / 2026.5.20 / 2026年5月20日 / 5月20日 / 5/20。
_DATE_RE = re.compile(
    r"(?:(?P<year>(?:19|20)\d{2})\s*[-/.年]\s*)?"
    r"(?P<month>0?[1-9]|1[0-2])\s*(?:月|[-/.])\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])(?!\d)\s*日?"
)

# 先匹配更具体的标签；同一段文本里出现多个标签时取第一个。
_DATE_LABELS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("生日", ("生日", "出生", "阳历", "阴历", "农历")),
    ("纪念日", ("纪念日", "周年", "结婚", "订婚")),
    ("合同/交付", ("合同", "签约", "交付", "到期", "截止", "验收")),
    ("入职", ("入职", "面试", "报到")),
    ("节日", ("节日", "放假", "开学", "考试", "期末")),
)
_GENERIC_DATE_LABEL = "日期"

_SOURCE_KIND_LABELS = {
    "html": "HTML",
    "pdf": "PDF",
    "exportwechat_html": "ExportWeChat HTML",
}


def build_local_profile(store: Any, scope: ScopeKey, style_profile: Any = None,
                        *, profile_store: ContactProfileStore | None = None
                        ) -> ContactProfile:
    """生成并保存一位联系人的本地基础档案；不调用模型、不泄漏原文。

    ``store`` 是 :class:`liteapp.history.HistoryStore`；``style_profile`` 可为
    ``StyleProfile`` 或任何带 ``prompt_style``/``display_report`` 的对象。
    ``profile_store`` 仅供测试/特殊数据目录注入；默认与历史库同目录。
    """
    if not isinstance(scope, ScopeKey):
        raise TypeError("scope 必须是 ScopeKey。")
    target_store = profile_store or _default_profile_store(store)
    title = _display_name(store, scope)
    existing = target_store.get(title, scope.person_id)

    stats = store.stats(scope)
    snapshot = store.snapshot(scope)
    source_kinds = [getattr(source, "kind", "") for source in snapshot.sources]
    texts = _iter_texts(store, scope, stats.source_version)
    topics = _topics_value(texts, source_kinds)
    important_dates = _important_dates(texts)
    prompt_style, display_report = _style_values(style_profile)
    reply_style = None
    if style_profile is not None:
        reply_style = getattr(style_profile, "reply_style", None) or prompt_style

    profile = ContactProfile(
        title=title,
        scope_person_id=scope.person_id,
        style_prompt=prompt_style if prompt_style is not None
        else (existing.style_prompt if existing is not None else None),
        display_report=display_report if display_report is not None
        else (existing.display_report if existing is not None else None),
        persona=existing.persona if existing is not None else None,
        affection_score=existing.affection_score if existing is not None else None,
        updated_at="",
        favorite=existing.favorite if existing is not None else False,
        relation=(existing.relation if existing is not None and existing.relation
                  else DEFAULT_RELATION),
        topics=topics,
        preferences=existing.preferences if existing is not None else None,
        taboos=existing.taboos if existing is not None else None,
        reply_style=reply_style or (existing.reply_style if existing is not None
                                    else None),
        important_dates=important_dates,
        model_analyzed=False,
    )
    return target_store.save(profile)


def needs_model_analysis(profile: ContactProfile | None) -> bool:
    """基础档案（``model_analyzed=False``）或没有档案时需要模型逐句分析。"""
    if profile is None:
        return True
    return not bool(getattr(profile, "model_analyzed", False))


# --------------------------------------------------------------------- helpers
def _default_profile_store(store: Any) -> ContactProfileStore:
    path = getattr(store, "path", None)
    if path is not None:
        try:
            return ContactProfileStore(Path(path).parent)
        except (TypeError, ValueError, OSError):
            pass
    from . import settings
    return ContactProfileStore(settings.data_dir())


def _display_name(store: Any, scope: ScopeKey) -> str:
    try:
        people = store.list_people_in_account(scope.wechat_account_id)
    except (AttributeError, TypeError, ValueError):
        people = []
    for person in people:
        if getattr(person, "person_id", None) == scope.person_id:
            name = getattr(person, "display_name", None)
            if isinstance(name, str) and name.strip():
                return name.strip()
    return scope.person_id


def _iter_texts(store: Any, scope: ScopeKey, source_version: int) -> list[str]:
    texts: list[str] = []
    scanned_chars = 0
    try:
        records = store.iter_records(scope, source_version)
    except (AttributeError, TypeError, ValueError):
        return texts
    for index, record in enumerate(records):
        if index >= MAX_SCAN_RECORDS or scanned_chars >= MAX_SCAN_CHARS:
            break
        text = getattr(record, "text", "")
        if not isinstance(text, str) or not text:
            continue
        remaining = MAX_SCAN_CHARS - scanned_chars
        if len(text) > remaining:
            text = text[:remaining]
        texts.append(text)
        scanned_chars += len(text)
    return texts


def _topics_value(texts: list[str], source_kinds: list[str]) -> list[str] | None:
    """话题标签列表（与模型档案的 topics 列表同构），只含关键词/来源标签。"""
    counts: list[tuple[str, int]] = []
    for name, pattern in _TOPIC_PATTERNS:
        count = sum(1 for text in texts if pattern.search(text))
        if count:
            counts.append((name, count))
    counts.sort(key=lambda item: (-item[1], item[0]))
    topics = [name for name, _count in counts[:MAX_TOPIC_ITEMS]]
    for kind in source_kinds:
        label = _SOURCE_KIND_LABELS.get(str(kind), str(kind) or "其它")
        if label not in topics:
            topics.append(label)
    return topics or None


def _important_dates(texts: list[str]) -> list[str] | None:
    labelled: list[str] = []
    generic: list[str] = []
    seen_labelled: set[tuple[str, str]] = set()
    seen_generic: set[str] = set()
    for text in texts:
        matches = list(_DATE_RE.finditer(text))
        if not matches:
            continue
        for match in matches:
            formatted = _format_date(match)
            if not formatted:
                continue
            label = _date_label_near(text, match.start())
            if label:
                key = (label, formatted)
                if key not in seen_labelled:
                    seen_labelled.add(key)
                    labelled.append(f"{label}：{formatted}")
            elif formatted not in seen_generic:
                seen_generic.add(formatted)
                generic.append(f"{_GENERIC_DATE_LABEL}：{formatted}")
    selected = labelled[:MAX_IMPORTANT_DATES]
    if not selected:
        selected = generic[:min(3, MAX_IMPORTANT_DATES)]
    return selected or None


def _date_label_near(text: str, date_start: int) -> str | None:
    """把日期匹配到同一段文本里最近的“生日/纪念日/…”标签。

    一条消息可能同时写“生日 5月20日，纪念日 6月1日”，因此不能只用整段文本
    的第一个标签；这里只在日期前 40 字内找最近标签，找不到就不猜测。
    """
    best_label: str | None = None
    best_distance: int | None = None
    window_start = max(0, date_start - 40)
    prefix = text[window_start:date_start]
    for label, keywords in _DATE_LABELS:
        for keyword in keywords:
            position = prefix.rfind(keyword)
            if position < 0:
                continue
            distance = len(prefix) - (position + len(keyword))
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_label = label
    return best_label


def _format_date(match: re.Match[str]) -> str:
    year = match.group("year")
    month = int(match.group("month"))
    day = int(match.group("day"))
    if year:
        return f"{int(year):04d}-{month:02d}-{day:02d}"
    return f"{month}月{day}日"


def _style_values(style_profile: Any) -> tuple[str | None, str | None]:
    if style_profile is None:
        return None, None
    if isinstance(style_profile, dict):
        prompt = style_profile.get("prompt_style")
        report = style_profile.get("display_report")
    else:
        prompt = getattr(style_profile, "prompt_style", None)
        report = getattr(style_profile, "display_report", None)
    return _nonempty_text(prompt), _nonempty_text(report)


def _nonempty_text(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value
    return None
