"""专属回复风格：从已导入聊天里“我”的发言生成可解释的双层风格档案。

用户需求（task-18，revision 3）：分析已导入 HTML/PDF 聊天记录中“我”的发言，
形成专属回复风格，并可在设置中选用。输出分两层：

* ``prompt_style``：300–600 字，可直接作为模型提示词里的精简风格指令（太长会
  稀释指令、增加 token、容易跑偏）；
* ``display_report``：800–1500 字，面向用户的风格报告：统计指标、句式描述、
  “推断/不确定”标签，不输出长段真实聊天原文。

本模块只做**本地、确定性、可解释**的统计，不调用模型、不联网、不跨范围：

* 方向筛选：只接受已识别的“我”方向（T5 ``HistoryRecord.sender`` 的 me 方向，
  或正文里的「我：」「本人：」等前缀）；无法判断方向时**跳过**，绝不按来源名称/
  昵称/文件内容猜测（避免把客户的话当成“我”）。
* 特征：平均/中位句长、短句比例、标点/语气词/表情、礼貌开头结尾、疑问句比例、
  称呼与口头禅计数；片段与标准消息分开计数，HTML/PDF 来源都支持。
* 持久化：``store.save_derived_memory(scope, "style_profile", payload)`` 加密保存；
  ``latest(scope)`` 读回；来源删除/记忆版本变化后旧档案不会返回（memory_version
  门禁）；跨 scope 一律拒绝。

不修改 ui.py/history_ui.py（设置页由 Lead 接线，可直接使用 ``latest()`` 的
``prompt_style`` / ``display_report`` / ``features``）。
"""

from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, Sequence

from .contracts import ScopeKey
from .history_models import (
    HistoryRecord,
    ScopeMismatchError,
    SourceSnapshot,
    StaleSourceVersionError,
)

__all__ = [
    "DEFAULT_MY_LABELS",
    "DESCRIPTION_MAX_CHARS",
    "DESCRIPTION_MIN_CHARS",
    "DISPLAY_REPORT_MAX_CHARS",
    "DISPLAY_REPORT_MIN_CHARS",
    "KIND",
    "MIN_ME_MESSAGES",
    "PROMPT_STYLE_MAX_CHARS",
    "PROMPT_STYLE_MIN_CHARS",
    "SCHEMA_VERSION",
    "SHORT_MESSAGE_CHARS",
    "StyleFeatures",
    "StyleProfile",
    "StyleProfileBuilder",
    "StyleProfileError",
    "build_style_profile",
    "load_style_profile",
    "save_style_profile",
]

KIND = "style_profile"
SCHEMA_VERSION = 1

PROMPT_STYLE_MIN_CHARS = 300
PROMPT_STYLE_MAX_CHARS = 600
DISPLAY_REPORT_MIN_CHARS = 800
DISPLAY_REPORT_MAX_CHARS = 1500
# 兼容旧调用名（T8/早期报告用过 DESCRIPTION_*；语义现为 prompt_style 长度）。
DESCRIPTION_MIN_CHARS = PROMPT_STYLE_MIN_CHARS
DESCRIPTION_MAX_CHARS = PROMPT_STYLE_MAX_CHARS

MIN_ME_MESSAGES = 3
SHORT_MESSAGE_CHARS = 12
DEFAULT_MY_LABELS = ("我", "自己", "本人", "me", "self")

_DIRECTION_ME = frozenset({"me", "self"})
_OTHER_SENDERS = frozenset({"客户", "对方", "买家", "other", "them", "他", "她"})
_TONE_CHARS = "啊吧呢哈嗯嘛啦哦哟诶"
_POLITE_OPEN = ("请", "麻烦", "谢谢", "感谢", "辛苦", "您好", "拜托")
_POLITE_CLOSE = ("谢谢", "感谢", "辛苦", "拜托", "了哦", "哈", "麻烦了")
_DIRECT_START = ("好的", "收到", "可以", "行", "没问题", "OK", "ok", "嗯", "好",
                 "明白", "稍等")
_EXPRESSION_TOKENS = ("好的", "收到", "可以", "没问题", "行", "嗯", "谢谢", "麻烦",
                      "请", "您", "你", "亲", "老板", "哥", "姐", "哈哈", "马上",
                      "确认", "尽快", "稍等", "明白")
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;…]+")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]")
_EMOTICON_RE = re.compile(r"\[[^\[\]\n]{1,8}\]")
_NUMBER_RE = re.compile(r"\d|%|％|元|块|价格|报价|时间|点|天|号|周|月|日")


class StyleProfileError(ValueError):
    """样本不足/没有可用方向/保存空档案等可向用户解释的问题。"""


@dataclass(frozen=True)
class StyleFeatures:
    """Deterministic, explainable features computed only from “我”的发言."""

    me_message_count: int = 0
    me_fragment_count: int = 0
    char_count: int = 0
    mean_chars: float = 0.0
    median_chars: float = 0.0
    min_chars: int = 0
    max_chars: int = 0
    sentence_count: int = 0
    mean_sentence_chars: float = 0.0
    median_sentence_chars: float = 0.0
    short_message_ratio: float = 0.0
    question_ratio: float = 0.0
    exclamation_ratio: float = 0.0
    ending_punctuation_ratio: float = 0.0
    comma_ratio: float = 0.0
    ellipsis_ratio: float = 0.0
    emoji_ratio: float = 0.0
    tone_particle_ratio: float = 0.0
    polite_open_ratio: float = 0.0
    polite_close_ratio: float = 0.0
    direct_start_ratio: float = 0.0
    address_you_count: int = 0
    address_nin_count: int = 0
    number_ratio: float = 0.0
    expression_counts: tuple[tuple[str, int], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "expression_counts",
                           tuple(self.expression_counts or ()))

    def as_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for field in fields(self):
            value = getattr(self, field.name)
            if field.name == "expression_counts":
                value = [[token, count] for token, count in value]
            data[field.name] = value
        return data


@dataclass(frozen=True)
class StyleProfile:
    """One generated style profile: prompt instruction + user-facing report.

    Both layers are local deterministic inference over the imported records; they
    are never factual claims about the person and never leave this scope.
    """

    scope: ScopeKey
    source_version: int
    memory_version: int
    prompt_style: str
    display_report: str
    features: StyleFeatures
    sample_count: int
    fragment_count: int
    message_count: int
    char_count: int
    source_ids: tuple[str, ...]
    source_kinds: tuple[str, ...]
    sources_analyzed: int
    sources_with_me: int
    sources_without_me: int
    generated_at: str
    is_empty: bool = False
    empty_reason: str = ""
    limitations: tuple[str, ...] = ()
    saved: bool = False
    derived_memory_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        if not isinstance(self.prompt_style, str) or not isinstance(self.display_report, str):
            raise TypeError("prompt_style/display_report 必须是字符串。")
        object.__setattr__(self, "source_ids", tuple(self.source_ids or ()))
        object.__setattr__(self, "source_kinds", tuple(self.source_kinds or ()))
        object.__setattr__(self, "limitations", tuple(self.limitations or ()))

    @property
    def description(self) -> str:
        """Backward-compatible alias: the concise model prompt style."""
        return self.prompt_style

    @property
    def setting_text(self) -> str:
        """Text the settings page can store/select as the model style instruction."""
        return self.prompt_style if not self.is_empty else self.empty_reason

    @property
    def display_text(self) -> str:
        """Text the settings page shows to the user (report, not prompt instruction)."""
        return self.display_report if not self.is_empty else self.empty_reason

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe payload; persisted encrypted by the store, never by callers."""
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": KIND,
            "scope": {
                "wechat_account_id": self.scope.wechat_account_id,
                "person_id": self.scope.person_id,
            },
            "source_version": self.source_version,
            "memory_version": self.memory_version,
            "generated_at": self.generated_at,
            "prompt_style": self.prompt_style,
            "display_report": self.display_report,
            "features": self.features.as_dict(),
            "sample_count": self.sample_count,
            "fragment_count": self.fragment_count,
            "message_count": self.message_count,
            "char_count": self.char_count,
            "source_ids": list(self.source_ids),
            "source_kinds": list(self.source_kinds),
            "sources_analyzed": self.sources_analyzed,
            "sources_with_me": self.sources_with_me,
            "sources_without_me": self.sources_without_me,
            "is_empty": self.is_empty,
            "empty_reason": self.empty_reason,
            "limitations": list(self.limitations),
        }


def _now() -> str:
    return datetime.now().isoformat(timespec="microseconds")


def _prefix_body(text: str, labels: Sequence[str]) -> str:
    """Return content for ``我：…`` / ``我 2025-01-02 10:00 …`` style prefixes."""
    if not isinstance(text, str):
        return ""
    for label in labels:
        escaped = re.escape(label)
        match = re.match(rf"^\s*{escaped}\s*[：:]\s*(?P<body>.+?)\s*$", text,
                         re.IGNORECASE)
        if match:
            return match.group("body").strip()
        match = re.match(
            rf"^\s*{escaped}\s+20\d{{2}}[-/.年]\d{{1,2}}[-/.月]\d{{1,2}}日?"
            r"(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\s*[：:]?\s*(?P<body>.*)$",
            text, re.IGNORECASE)
        if match:
            return match.group("body").strip()
    return ""


def _classify_me(record: HistoryRecord, labels: Sequence[str],
                 me_names: frozenset[str]) -> tuple[bool, str]:
    """(is_me, body). Unknown direction is always skipped — never guessed."""
    sender = record.sender
    if isinstance(sender, str) and sender.strip():
        normalized = sender.strip().casefold()
        if normalized in me_names or normalized in _DIRECTION_ME:
            return True, _prefix_body(record.text, labels) or record.text.strip()
        # 已知的对方方向或其他昵称：无法证明是“我”，跳过（不按昵称猜）。
        return False, ""
    body = _prefix_body(record.text, labels)
    if body:
        return True, body
    return False, ""


def _sentence_lengths(text: str) -> list[int]:
    parts = [part.strip() for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    if not parts:
        parts = [text.strip()]
    return [len(part) for part in parts if part.strip()]


def _ratio(count: int, total: int) -> float:
    return round(count / total, 4) if total else 0.0


def _compute_features(texts: list[str], fragment_count: int,
                      message_count: int) -> StyleFeatures:
    sample = len(texts)
    lengths = [len(text) for text in texts]
    sentence_lengths = [length for text in texts for length in _sentence_lengths(text)]
    expression_counts = []
    for token in _EXPRESSION_TOKENS:
        count = sum(text.count(token) for text in texts)
        if count:
            expression_counts.append((token, count))
    expression_counts.sort(key=lambda item: (-item[1], item[0]))
    return StyleFeatures(
        me_message_count=message_count,
        me_fragment_count=fragment_count,
        char_count=sum(lengths),
        mean_chars=round(sum(lengths) / sample, 4),
        median_chars=round(float(statistics.median(lengths)), 4),
        min_chars=min(lengths),
        max_chars=max(lengths),
        sentence_count=len(sentence_lengths),
        mean_sentence_chars=round(sum(sentence_lengths) / len(sentence_lengths), 4),
        median_sentence_chars=round(float(statistics.median(sentence_lengths)), 4),
        short_message_ratio=_ratio(sum(1 for length in lengths
                                       if length <= SHORT_MESSAGE_CHARS), sample),
        question_ratio=_ratio(sum(1 for text in texts
                                  if "？" in text or "?" in text), sample),
        exclamation_ratio=_ratio(sum(1 for text in texts
                                     if "！" in text or "!" in text), sample),
        ending_punctuation_ratio=_ratio(sum(
            1 for text in texts if text and text[-1] in "。！？!?…"), sample),
        comma_ratio=_ratio(sum(1 for text in texts
                               if "，" in text or "," in text), sample),
        ellipsis_ratio=_ratio(sum(1 for text in texts
                                  if "…" in text or "..." in text), sample),
        emoji_ratio=_ratio(sum(1 for text in texts
                               if _EMOJI_RE.search(text) or _EMOTICON_RE.search(text)),
                           sample),
        tone_particle_ratio=_ratio(sum(1 for text in texts
                                       if any(char in text for char in _TONE_CHARS)),
                                   sample),
        polite_open_ratio=_ratio(sum(1 for text in texts
                                     if text.startswith(_POLITE_OPEN)), sample),
        polite_close_ratio=_ratio(sum(1 for text in texts
                                      if text.endswith(_POLITE_CLOSE)), sample),
        direct_start_ratio=_ratio(sum(1 for text in texts
                                      if text.startswith(_DIRECT_START)), sample),
        address_you_count=sum(text.count("你") for text in texts),
        address_nin_count=sum(text.count("您") for text in texts),
        number_ratio=_ratio(sum(1 for text in texts if _NUMBER_RE.search(text)), sample),
        expression_counts=tuple(expression_counts),
    )


def _length_style(features: StyleFeatures) -> str:
    if features.mean_chars <= SHORT_MESSAGE_CHARS:
        return "偏短"
    if features.mean_chars >= 40:
        return "较长"
    return "中等长度"


def _tokens_text(features: StyleFeatures, limit: int = 4) -> str:
    tokens = "、".join(f"{token} {count} 次"
                       for token, count in features.expression_counts[:limit])
    return tokens or "样本中未出现高频短词"


# ----------------------------------------------------------------- prompt style
def _prompt_style(*, features: StyleFeatures, sample_count: int,
                  fragment_count: int, message_count: int, char_count: int,
                  sources_with_me: int, html_sources: int, pdf_sources: int,
                  limitations: Sequence[str]) -> str:
    """300–600 字精简模型提示词风格指令（第一层输出）。"""
    tokens_text = _tokens_text(features)
    head = [
        "专属回复风格说明（由已导入记录做本地统计推断，不是事实断言）",
        (f"你在当前档案中可识别到 {sample_count} 条“我”的发言，来自 "
         f"{sources_with_me} 个导入文件（HTML {html_sources} 个、PDF "
         f"{pdf_sources} 个）；片段 {fragment_count} 条、标准消息 {message_count} 条，"
         f"合计 {char_count} 字。"),
        (f"从长度看，平均每条 {features.mean_chars:.1f} 字、中位 "
         f"{features.median_chars:.1f} 字，最短 {features.min_chars} 字、最长 "
         f"{features.max_chars} 字；{features.short_message_ratio:.0%} 的发言不超过 "
         f"{SHORT_MESSAGE_CHARS} 字，整体属于{_length_style(features)}表达。"),
    ]
    optional = [
        (f"从句法习惯看，共 {features.sentence_count} 句、平均句长 "
         f"{features.mean_sentence_chars:.1f} 字；{features.question_ratio:.0%} 的"
         f"发言是疑问句，{features.exclamation_ratio:.0%} 带感叹号，"
         f"{features.ending_punctuation_ratio:.0%} 以句末标点收尾，"
         f"{features.comma_ratio:.0%} 使用逗号。"),
        (f"语气与表情方面，语气词比例约 {features.tone_particle_ratio:.0%}、"
         f"表情/表情符比例约 {features.emoji_ratio:.0%}、省略号比例约 "
         f"{features.ellipsis_ratio:.0%}。"),
    ]
    tail = [
        (f"礼貌与开头习惯：{features.polite_open_ratio:.0%} 的发言以"
         f"“请/麻烦/谢谢/感谢”等开头，{features.polite_close_ratio:.0%} 以感谢/"
         f"辛苦等收尾；{features.direct_start_ratio:.0%} 的发言直接以"
         f"“好的/收到/可以/没问题”等回应；称呼上“您” "
         f"{features.address_nin_count} 次、“你” {features.address_you_count} 次。"),
        f"常用短词/口头禅（只统计出现次数，不引用正文）：{tokens_text}。",
        ("推断与使用建议：以上是本地统计得到的写作倾向，不是事实结论；可在设置中"
         "把它作为默认回复风格。建议保留自然语气，但遇到价格、库存、承诺等事实时"
         "以当前对话为准，避免把过时信息当作现状，也避免机械复述样本语句。"),
        (f"限制：{'；'.join(limitations)}；本风格仅代表已导入范围，不跨账号或"
         "联系人使用，也不读取微信数据库。"),
    ]

    def assemble(chosen: list[str]) -> str:
        return "\n".join(head + chosen + tail)

    chosen = list(optional)
    text = assemble(chosen)
    while len(text) > PROMPT_STYLE_MAX_CHARS and chosen:
        chosen.pop()
        text = assemble(chosen)
    if len(text) > PROMPT_STYLE_MAX_CHARS:
        cut = text[:PROMPT_STYLE_MAX_CHARS]
        for separator in ("。", "\n"):
            position = cut.rfind(separator)
            if position >= PROMPT_STYLE_MIN_CHARS:
                text = cut[:position + 1]
                break
        else:
            text = cut
    padding = (
        "由于样本来自本机已导入文件，风格说明只反映这些记录里的可见习惯，"
        "不推断未导入的聊天或对方的态度。",
        "若希望说明更稳定，可继续导入保留发送人方向的 HTML/PDF 记录后重新生成。",
    )
    for sentence in padding:
        candidate = text + "\n" + sentence
        if PROMPT_STYLE_MIN_CHARS <= len(candidate) <= PROMPT_STYLE_MAX_CHARS:
            text = candidate
        if len(text) >= PROMPT_STYLE_MIN_CHARS:
            break
    return text


# ----------------------------------------------------------------- display report
def _display_report(*, features: StyleFeatures, sample_count: int,
                    fragment_count: int, message_count: int, char_count: int,
                    sources_analyzed: int, sources_with_me: int,
                    sources_without_me: int, html_sources: int, pdf_sources: int,
                    source_kinds: Sequence[str], limitations: Sequence[str]) -> str:
    """800–1500 字面向用户的风格报告（第二层输出）。

    只包含统计指标、句式描述与明确的“推断/不确定”标签；不输出长段真实原文。
    """
    kinds_text = "、".join(source_kinds) if source_kinds else "无"
    tokens = "、".join(f"{token}（{count} 次）"
                       for token, count in features.expression_counts[:8])
    if not tokens:
        tokens = "样本中未出现高频短词"
    short_tendency = ("短句、直接" if features.mean_chars <= SHORT_MESSAGE_CHARS
                      else "长句、解释型" if features.mean_chars >= 40 else
                      "中等长度、以信息交代为主")
    if features.question_ratio >= 0.3:
        question_tendency = "疑问句比例较高，可能习惯先确认信息再推进"
    elif features.question_ratio >= 0.1:
        question_tendency = "偶有确认式提问，直接陈述仍占多数"
    else:
        question_tendency = "很少使用疑问句，倾向直接给出结论"
    if features.polite_open_ratio + features.polite_close_ratio >= 0.3:
        politeness = "礼貌标记较明显"
    elif features.polite_open_ratio + features.polite_close_ratio >= 0.1:
        politeness = "礼貌标记适中"
    else:
        politeness = "礼貌客套较少，表达更直接"
    head = [
        "专属回复风格报告（本地统计与推断，供设置页预览；不调用模型、不读取微信数据库）",
        (f"一、样本与来源：本档案共识别到 {sample_count} 条“我”的发言，其中片段 "
         f"{fragment_count} 条、标准消息 {message_count} 条，合计 {char_count} 字；"
         f"来自 {sources_analyzed} 个导入文件中的 {sources_with_me} 个"
         f"（HTML {html_sources} 个、PDF {pdf_sources} 个，来源类型：{kinds_text}）。"),
        (f"方向筛选说明：只统计发送人字段为“我/自己/本人/me”或正文带“我：”等明确"
         f"前缀的记录；有 {sources_without_me} 个来源没有识别到“我”的发言，"
         "未按文件名或昵称猜测方向。"),
        "二、可核对指标（事实标签，均由本地记录直接统计）：",
        (f"[事实] 句子长度：平均每条 {features.mean_chars:.1f} 字、中位 "
         f"{features.median_chars:.1f} 字，最短 {features.min_chars} 字、最长 "
         f"{features.max_chars} 字；短句（≤{SHORT_MESSAGE_CHARS} 字）占比 "
         f"{features.short_message_ratio:.0%}。共 {features.sentence_count} 句，"
         f"平均句长 {features.mean_sentence_chars:.1f} 字、中位句长 "
         f"{features.median_sentence_chars:.1f} 字。"),
        (f"[事实] 标点使用：疑问句占比 {features.question_ratio:.0%}，带感叹号 "
         f"{features.exclamation_ratio:.0%}，句末标点收尾 {features.ending_punctuation_ratio:.0%}，"
         f"使用逗号 {features.comma_ratio:.0%}，省略号 {features.ellipsis_ratio:.0%}。"),
        (f"[事实] 语气与表情：语气词出现比例约 {features.tone_particle_ratio:.0%}；"
         f"表情/表情符出现比例约 {features.emoji_ratio:.0%}。"),
        (f"[事实] 礼貌与开头结尾：礼貌开头（请/麻烦/谢谢/感谢等）占比 "
         f"{features.polite_open_ratio:.0%}，礼貌收尾占比 "
         f"{features.polite_close_ratio:.0%}，直接回应开头（好的/收到/可以/"
         f"没问题等）占比 {features.direct_start_ratio:.0%}。"),
        (f"[事实] 称呼与口头禅：称呼“您” {features.address_nin_count} 次、“你” "
         f"{features.address_you_count} 次；含数字/时间/价格等信息型表达的发言占比 "
         f"{features.number_ratio:.0%}。常用短词（仅频次，不引用正文）：{tokens}。"),
        "三、句式与倾向描述（推断标签；仅基于上述统计，不代表性格或真实意图）：",
        (f"[推断] 句式倾向：根据长度与短句比例，你的回复整体呈现{short_tendency}的表达习惯。"
         "该判断由统计量推导，样本有限时不确定。"),
        (f"[推断] 提问倾向：{question_tendency}。这是从疑问句比例得到的倾向描述，"
         "不确定是否适用于所有对话场景。"),
        (f"[推断] 礼貌倾向：{politeness}；称呼习惯显示你会在部分发言中区分“您/你”。"
         "该结论只反映已导入记录，不能据此判断关系亲疏或真实态度。"),
        (f"[不确定] 互动节奏：当前记录缺少可靠的双方发送人/时间字段时不推断回复速度、"
         "主动比例或会话发起频率；只有可靠字段才可由历史统计另行计算。"),
        "四、使用建议：可把前 300–600 字的精简风格指令用于模型提示词，"
        "本报告供你人工确认是否符合自己的表达习惯。遇到价格、库存、承诺等事实时"
        "仍以当前对话为准，避免把样本中的旧信息当作现状，也避免机械照搬句式。",
        (f"五、限制与不确定性：{'；'.join(limitations)}；本报告仅代表已导入范围，"
         "不跨账号或联系人使用，不读取微信数据库；若记录更新（新增导入或删除来源），"
         "旧报告会随记忆版本失效，需要重新生成。"),
    ]
    optional = [
        (f"补充观察（推断）：样本中平均每条不超过 {SHORT_MESSAGE_CHARS} 字的比例较高时，"
         "回复通常更短；若你的业务场景需要更完整的解释，可在保留语气的前提下"
         "适当补充结论依据。"),
        ("补充观察（推断）：礼貌开头/结尾比例较高通常意味着对客户沟通更注意缓冲；"
         "但该指标受导出记录完整度影响，不确定时可结合近期真实对话人工核对。"),
        (f"补充观察（事实）：当前样本的标准消息占比 "
         f"{(message_count / sample_count if sample_count else 0):.0%}，"
         "片段占比越高，句式判断越可能受导出格式影响。"),
    ]

    def assemble(chosen: list[str]) -> str:
        return "\n".join(head + chosen)

    text = assemble(list(optional))
    while len(text) > DISPLAY_REPORT_MAX_CHARS and optional:
        optional.pop()
        text = assemble(optional)
    padding = (
        "说明：本报告由固定统计模板生成，未调用模型；所有“推断/不确定”段落都只是"
        "基于计数的展示层解释，不是对个人性格、偏好或关系的结论。",
        "若你希望报告更贴近真实沟通方式，可继续导入保留发言人方向的 HTML/PDF "
        "记录后重新生成；系统不会为补足字数编造未观察到的内容。",
    )
    for sentence in padding:
        candidate = text + "\n" + sentence
        if len(candidate) <= DISPLAY_REPORT_MAX_CHARS:
            text = candidate
        if len(text) >= DISPLAY_REPORT_MIN_CHARS:
            break
    if len(text) > DISPLAY_REPORT_MAX_CHARS:
        cut = text[:DISPLAY_REPORT_MAX_CHARS]
        for separator in ("。", "\n"):
            position = cut.rfind(separator)
            if position >= DISPLAY_REPORT_MIN_CHARS:
                text = cut[:position + 1]
                break
        else:
            text = cut
    return text


# ------------------------------------------------------------------- collection
def _iter_me_texts(store: Any, scope: ScopeKey, snapshot: SourceSnapshot,
                   labels: Sequence[str]) -> tuple[list[str], dict[str, int],
                                                   int, int]:
    me_names = frozenset(label.casefold() for label in labels) | _DIRECTION_ME
    texts: list[str] = []
    per_source: dict[str, int] = {}
    fragment_count = 0
    message_count = 0
    for record in store.iter_records(scope, snapshot.source_version):
        is_me, body = _classify_me(record, labels, me_names)
        if not is_me:
            continue
        body = body.strip()
        if not body:
            continue
        texts.append(body)
        per_source[record.source_id] = per_source.get(record.source_id, 0) + 1
        if record.record_type == "fragment":
            fragment_count += 1
        else:
            message_count += 1
    return texts, per_source, fragment_count, message_count


class StyleProfileBuilder:
    """Builds/saves/loads the local style profile for exactly one ScopeKey."""

    def __init__(self, store: Any) -> None:
        if not hasattr(store, "snapshot") or not hasattr(store, "iter_records"):
            raise TypeError("store 必须是 HistoryStore（V2）")
        self.store = store

    # ------------------------------------------------------------------ public
    def build(self, scope: ScopeKey, *,
              my_labels: Sequence[str] = DEFAULT_MY_LABELS,
              snapshot: SourceSnapshot | None = None) -> StyleProfile:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        labels = self._labels(my_labels)
        current = self.store.stats(scope)  # validates scope ownership
        if snapshot is None:
            snapshot = self.store.snapshot(scope)
        elif not isinstance(snapshot, SourceSnapshot):
            raise TypeError("snapshot 必须是 SourceSnapshot 或 None。")
        elif snapshot.scope != scope:
            raise ScopeMismatchError("快照属于其它联系人范围，拒绝生成风格。")
        if snapshot.source_version != current.source_version:
            raise StaleSourceVersionError(
                f"快照已过期：快照来源版本 {snapshot.source_version}，"
                f"当前 {current.source_version}；请重新导入/刷新后重试。")
        return self._analyze(scope, snapshot, labels)

    def latest(self, scope: ScopeKey) -> StyleProfile | None:
        """Read the current-version saved profile; stale/foreign/corrupt -> None."""
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        stats = self.store.stats(scope)  # validates scope ownership
        candidates: list[StyleProfile] = []
        for entry in self.store.list_derived_memory(scope):
            if entry.kind != KIND:
                continue
            try:
                payload = json.loads(entry.payload)
                profile = _profile_from_payload(payload, entry.id)
            except (TypeError, ValueError, KeyError):
                continue
            if profile.scope != scope:
                continue
            if profile.memory_version != stats.memory_version:
                continue
            if profile.source_version != stats.source_version:
                continue
            candidates.append(profile)
        if not candidates:
            return None
        return max(candidates, key=lambda item: (item.generated_at,
                                                 item.derived_memory_id or ""))

    def save(self, scope: ScopeKey, *,
             my_labels: Sequence[str] = DEFAULT_MY_LABELS,
             snapshot: SourceSnapshot | None = None) -> StyleProfile:
        profile = self.build(scope, my_labels=my_labels, snapshot=snapshot)
        if profile.is_empty:
            raise StyleProfileError(profile.empty_reason)
        payload = json.dumps(profile.as_dict(), ensure_ascii=False, sort_keys=True)
        derived = self.store.save_derived_memory(scope, KIND, payload)
        return replace(profile, saved=True, derived_memory_id=derived.id)

    # ----------------------------------------------------------------- internal
    @staticmethod
    def _labels(my_labels: Sequence[str]) -> tuple[str, ...]:
        if isinstance(my_labels, str) or not isinstance(my_labels, (tuple, list)):
            raise TypeError("my_labels 必须是非空字符串序列。")
        labels = tuple(str(label).strip() for label in my_labels
                       if str(label).strip())
        if not labels:
            raise ValueError("my_labels 至少需要一个非空标签。")
        return labels

    def _analyze(self, scope: ScopeKey, snapshot: SourceSnapshot,
                 labels: Sequence[str]) -> StyleProfile:
        sources = list(snapshot.sources)
        kind_by_source = {source.source_id: source.kind for source in sources}
        texts, per_source, fragment_count, message_count = _iter_me_texts(
            self.store, scope, snapshot, labels)
        analyzed = len(sources)
        with_me = len(per_source)
        without_me = max(0, analyzed - with_me)
        contributing_kinds = sorted({kind_by_source.get(source_id, "unknown")
                                     for source_id in per_source})
        all_kinds = sorted({source.kind for source in sources})
        source_kinds = tuple(contributing_kinds or all_kinds)
        html_sources = sum(1 for source_id in per_source
                           if kind_by_source.get(source_id) == "html")
        pdf_sources = sum(1 for source_id in per_source
                          if kind_by_source.get(source_id) == "pdf")
        limitations: list[str] = []
        if analyzed == 0:
            limitations.append("还没有导入任何聊天记录")
        elif without_me:
            limitations.append(
                f"{without_me} 个来源未识别到“我”的发言（未保留发送人或方向无法"
                "判断；不按来源名称猜测）")
        if fragment_count:
            limitations.append(
                f"其中 {fragment_count} 条来自片段（无可靠发送人/时间），只用于"
                "文本风格统计")
        limitations.append("样本只代表已导入范围，可能不是完整聊天历史")

        if not texts:
            reason = ("还没有导入任何聊天记录；无法生成专属回复风格。"
                      if analyzed == 0 else
                      "没有识别到“我”的发言：现有记录没有可靠的发送人方向字段，"
                      "也没有“我：”等方向前缀；不会按来源名称或昵称猜测。"
                      "可导入保留发送人方向的 HTML/PDF 后重试。")
            return StyleProfile(
                scope=scope, source_version=snapshot.source_version,
                memory_version=snapshot.memory_version, prompt_style="",
                display_report="", features=StyleFeatures(), sample_count=0,
                fragment_count=0, message_count=0, char_count=0,
                source_ids=tuple(source.source_id for source in sources),
                source_kinds=tuple(all_kinds), sources_analyzed=analyzed,
                sources_with_me=0, sources_without_me=without_me,
                generated_at=_now(), is_empty=True, empty_reason=reason,
                limitations=tuple(limitations))
        if len(texts) < MIN_ME_MESSAGES:
            raise StyleProfileError(
                f"识别到的“我”的发言不足：{len(texts)} 条，至少需要 "
                f"{MIN_ME_MESSAGES} 条才能形成可靠风格说明；请导入更多保留发送人"
                "方向的记录。")
        features = _compute_features(texts, fragment_count, message_count)
        prompt_style = _prompt_style(
            features=features, sample_count=len(texts),
            fragment_count=fragment_count, message_count=message_count,
            char_count=features.char_count, sources_with_me=with_me,
            html_sources=html_sources, pdf_sources=pdf_sources,
            limitations=limitations)
        display_report = _display_report(
            features=features, sample_count=len(texts),
            fragment_count=fragment_count, message_count=message_count,
            char_count=features.char_count, sources_analyzed=analyzed,
            sources_with_me=with_me, sources_without_me=without_me,
            html_sources=html_sources, pdf_sources=pdf_sources,
            source_kinds=source_kinds, limitations=limitations)
        return StyleProfile(
            scope=scope, source_version=snapshot.source_version,
            memory_version=snapshot.memory_version, prompt_style=prompt_style,
            display_report=display_report, features=features,
            sample_count=len(texts), fragment_count=fragment_count,
            message_count=message_count, char_count=features.char_count,
            source_ids=tuple(sorted(per_source)), source_kinds=source_kinds,
            sources_analyzed=analyzed, sources_with_me=with_me,
            sources_without_me=without_me, generated_at=_now(),
            limitations=tuple(limitations))


def _profile_from_payload(payload: dict[str, Any], derived_id: str) -> StyleProfile:
    if not isinstance(payload, dict):
        raise TypeError("payload 必须是字典")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("不支持的风格档案 schema")
    if payload.get("kind") != KIND:
        raise ValueError("不是风格档案")
    scope_data = payload["scope"]
    scope = ScopeKey(wechat_account_id=str(scope_data["wechat_account_id"]),
                     person_id=str(scope_data["person_id"]))
    feature_data = dict(payload.get("features") or {})
    if "expression_counts" in feature_data:
        feature_data["expression_counts"] = tuple(
            (str(token), int(count))
            for token, count in feature_data["expression_counts"])
    allowed = set(StyleFeatures.__dataclass_fields__)
    features = StyleFeatures(**{key: value for key, value in feature_data.items()
                                if key in allowed})
    prompt_style = payload.get("prompt_style")
    if not isinstance(prompt_style, str):
        # 兼容最早一版（只有 description）的开发期 payload。
        prompt_style = str(payload.get("description", ""))
    return StyleProfile(
        scope=scope,
        source_version=int(payload["source_version"]),
        memory_version=int(payload["memory_version"]),
        prompt_style=prompt_style,
        display_report=str(payload.get("display_report", "")),
        features=features,
        sample_count=int(payload.get("sample_count", 0)),
        fragment_count=int(payload.get("fragment_count", 0)),
        message_count=int(payload.get("message_count", 0)),
        char_count=int(payload.get("char_count", 0)),
        source_ids=tuple(payload.get("source_ids", ())),
        source_kinds=tuple(payload.get("source_kinds", ())),
        sources_analyzed=int(payload.get("sources_analyzed", 0)),
        sources_with_me=int(payload.get("sources_with_me", 0)),
        sources_without_me=int(payload.get("sources_without_me", 0)),
        generated_at=str(payload.get("generated_at", "")),
        is_empty=bool(payload.get("is_empty", False)),
        empty_reason=str(payload.get("empty_reason", "")),
        limitations=tuple(payload.get("limitations", ())),
        saved=True, derived_memory_id=derived_id)


# ---------------------------------------------------------------- module helpers
def build_style_profile(store: Any, scope: ScopeKey, *,
                        my_labels: Sequence[str] = DEFAULT_MY_LABELS,
                        snapshot: SourceSnapshot | None = None) -> StyleProfile:
    return StyleProfileBuilder(store).build(scope, my_labels=my_labels,
                                            snapshot=snapshot)


def save_style_profile(store: Any, scope: ScopeKey, *,
                       my_labels: Sequence[str] = DEFAULT_MY_LABELS,
                       snapshot: SourceSnapshot | None = None) -> StyleProfile:
    return StyleProfileBuilder(store).save(scope, my_labels=my_labels,
                                           snapshot=snapshot)


def load_style_profile(store: Any, scope: ScopeKey) -> StyleProfile | None:
    return StyleProfileBuilder(store).latest(scope)
