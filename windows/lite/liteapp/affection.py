"""本地、可解释的好感度估算（悬浮球角色好感度的本地近似）。

**这是本地估算，不是心理概率。** 本模块只用关键词、句式与消息长度等
可解释的启发式规则，根据“对方”发来的消息粗略估计聊天中的投入程度；
它不调用任何模型、不联网、不做人格或真实情感判断，也不代表对方真实想法。
相同输入始终得到相同结果（确定性），所有维度都带证据计数，便于人工核对。

参考 ``crush-monitor-ref/shared/affinity.ts`` 的 6 个维度与权重：
主动延续 15、回应投入 20、关心体贴 20、自我开放 15、亲密表达 20、实际行动 10；
每维 0–4 级，最终按权重折算为 0–100。检测到“明确拒绝/保持距离”等边界信号
（参考实现的 ``boundary >= 0.8``）时总分封顶 25。confidence/status 反映的是
“证据是否足够”，不是“情感判断有多准”：``insufficient``（样本不足）、
``ambiguous``（信号混杂/偏少）、``clear``（有较充分的可解释信号）。

输入方向约定：
* ``HistoryRecord``：``sender == "me"`` 表示我；``sender == "other"`` 表示对方；
  ``sender is None``（方向未知）跳过；其它非空标签按“对方”处理（导入记录常带昵称）。
* UI 消息三元组 ``(who, name, body)``：仅 ``who == "me"`` 表示我；
  ``who`` 为 ``other``/``her``/``them`` 等已知对方标记表示对方；其它方向未知跳过。
* 也接受 ``{"sender"/"who": ..., "text"/"body": ...}`` 映射，便于悬浮球直接传 JSON。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

__all__ = [
    "AFFECTION_DIMENSIONS",
    "BOUNDARY_CAP",
    "AffectionDimension",
    "AffectionProfile",
    "DimensionScore",
    "boundary_signal_score",
    "estimate_affection",
    "level_label_for",
]

BOUNDARY_CAP = 25
LEVEL_MIN, LEVEL_MAX = 0, 4
MAX_SAMPLES = 3
MAX_SAMPLE_CHARS = 80
STATUS_INSUFFICIENT = "insufficient"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_CLEAR = "clear"

_LEVEL_LABELS = (
    (20, "低"),
    (35, "一般"),
    (55, "友好"),
    (70, "较好"),
    (85, "高"),
    (101, "很高"),
)

_ME_DIRECTIONS = frozenset({"me", "self", "我", "自己"})
_OTHER_DIRECTIONS = frozenset({"other", "her", "him", "them", "ta", "她", "他",
                               "对方", "friend", "partner"})


def _signal(pattern: str, strength: float) -> tuple[re.Pattern, float]:
    return re.compile(pattern), float(strength)


# 每条规则的 (正则, 强度)：正数提升、负数降低该维度的等级；最终等级 =
# clamp(round(2 + 2 * 平均强度), 0, 4)。规则刻意保持“可人工读出”的数量级。
_DIMENSION_SPECS = (
    {
        "key": "initiative", "label": "主动延续", "weight": 15,
        "positive": (
            _signal(r"在吗|最近怎么样|有空吗|要不要一起|下次一起|再约|常联系|随时找我|以后多聊", 1.2),
            _signal(r"[？?]|对了|然后呢|后来呢|还有吗|你呢|听说|我想问|跟你说", 0.6),
        ),
        "negative": (
            _signal(r"别联系|不想聊|先这样吧|不说了|不要再发|没空聊", -1.5),
            _signal(r"忙|再说吧|改天吧|算了|懒得", -0.6),
        ),
    },
    {
        "key": "engagement", "label": "回应投入", "weight": 20,
        "positive": (
            _signal(r"我觉得|其实|因为|确实|是啊|对的|我理解|你说得对|之前你|你提到", 1.2),
            _signal(r"然后|后来|而且|不过|所以", 0.5),
        ),
        "negative": (
            _signal(r"无视|敷衍|随便|无所谓|呵呵", -1.5),
            _signal(r"^(?:嗯+|哦+|好+|行+|知道了|好吧|行吧|嗷|喔)$", -0.8),
        ),
    },
    {
        "key": "care", "label": "关心体贴", "weight": 20,
        "positive": (
            _signal(r"注意休息|早点睡|注意身体|还好吗|没事吧|辛苦|担心|照顾|保重|"
                    r"别太累|加油|支持你|需要我|我陪你|帮你|心疼|感冒|生病|医院|"
                    r"别难过|开心点|我懂你", 1.2),
            _signal(r"晚安|早安|多喝水|注意安全|吃了吗|到家告诉我", 0.6),
        ),
        "negative": (
            _signal(r"关我什么事|跟我没关系|活该", -1.5),
            _signal(r"随便你", -0.6),
        ),
    },
    {
        "key": "openness", "label": "自我开放", "weight": 15,
        "positive": (
            _signal(r"我其实|我最近|我以前|我小时候|我家里|我喜欢|我害怕|我担心|"
                    r"我梦想|告诉你|跟你说实话|心里话|秘密|我有点|我特别|我不太|"
                    r"我的想法|我的感受|我经历过", 1.2),
            _signal(r"我觉得|我感觉|对我来说", 0.5),
        ),
        "negative": (
            _signal(r"不想说|别问|隐私|不该问", -1.5),
            _signal(r"没什么好说的", -0.6),
        ),
    },
    {
        "key": "intimacy", "label": "亲密表达", "weight": 20,
        "positive": (
            _signal(r"喜欢你|爱你|想你|想见你|想你了|舍不得|心动|在一起吧|抱抱|亲亲|"
                    r"宝贝|亲爱的|么么哒|❤|💗|💕|做我女朋友|做我男朋友", 1.5),
            _signal(r"小可爱|你真好|好可爱|依赖你|专属", 0.7),
        ),
        "negative": (
            _signal(r"只是朋友|普通朋友|朋友而已|别误会|不要误会|没那种意思|"
                    r"不适合|不合适|没可能|不可能|有男朋友|有女朋友|有喜欢的人|"
                    r"不谈恋爱|拒绝你", -1.6),
        ),
    },
    {
        "key": "action", "label": "实际行动", "weight": 10,
        "positive": (
            _signal(r"见面|一起吃饭|一起看电影|一起去|出来坐坐|出来玩|约一下|约个|"
                    r"周末见|来接你|给你带|送你|帮你拿|订好了|订了|安排好了|"
                    r"地址发我|不见不散|到时候见|准时到|到了告诉我|我到了", 1.4),
            _signal(r"有空见|改天约|下次一起|找个时间|有机会", 0.6),
        ),
        "negative": (
            _signal(r"去不了|不想见|没空见|不见", -1.5),
            _signal(r"没空|再说吧|改天吧", -0.6),
        ),
    },
)

AFFECTION_DIMENSIONS = tuple(
    {"key": spec["key"], "label": spec["label"], "weight": spec["weight"]}
    for spec in _DIMENSION_SPECS
)

# 边界信号：明确拒绝继续接近 / 要求保持距离 / 明确限定普通关系。
# 分数累加封顶 1.0；达到 0.8（与参考实现一致）即触发总分封顶 25。
_BOUNDARY_RULES = (
    _signal(r"别再?联系|不要再?联系|别再来找我|不要再发|别烦我|别来找我", 0.95),
    _signal(r"保持距离|离我远点|别靠近我", 0.9),
    _signal(r"我们只是朋友|只是普通朋友|只是朋友|普通朋友|朋友而已|只是同事", 0.85),
    _signal(r"不合适|不适合|没可能|不可能(?:喜欢|在一起)|不喜欢你|对你没(?:有)?(?:那种)?意思", 0.85),
    _signal(r"有男朋友|有女朋友|有喜欢的人|不(?:想)?谈恋爱|不想谈感情|拒绝你", 0.8),
    _signal(r"别误会|不要误会|别多想|不要多想", 0.7),
)


@dataclass(frozen=True)
class AffectionDimension:
    """One explainable dimension of the local estimate."""

    key: str
    label: str
    weight: int


@dataclass(frozen=True)
class DimensionScore:
    key: str
    label: str
    weight: int
    level: int
    evidence_count: int


@dataclass(frozen=True)
class AffectionProfile:
    """Local estimate for the floating-ball affection display (0–100)."""

    score: int
    level_label: str
    dimensions: tuple[DimensionScore, ...]
    boundary_applied: bool
    confidence: float
    status: str
    sample_messages: tuple[str, ...]
    other_messages: int
    my_messages: int


def level_label_for(score: int) -> str:
    """Map 0–100 to the six user-facing labels (deterministic thresholds)."""
    value = max(0, min(100, int(score)))
    for threshold, label in _LEVEL_LABELS:
        if value < threshold:
            return label
    return "很高"


def boundary_signal_score(texts: Iterable[str]) -> float:
    """Boundary strength 0..1 from explicit rejection/distance phrases."""
    total = 0.0
    for text in texts:
        matched = 0.0
        for pattern, weight in _BOUNDARY_RULES:
            if pattern.search(text):
                matched = max(matched, weight)
        total += matched
    return min(1.0, round(total, 3))


def _direction_and_text(item: Any) -> tuple[str | None, str]:
    """Return ("me"|"other"|None, text); None means unknown direction (skip)."""
    if isinstance(item, str):
        return None, item
    sender = who = None
    text = None
    if hasattr(item, "sender") and hasattr(item, "text"):
        sender = getattr(item, "sender")
        text = getattr(item, "text")
    elif isinstance(item, Mapping):
        if "who" in item:
            who = item.get("who")
        else:
            sender = item.get("sender")
        text = item.get("text", item.get("body"))
    elif isinstance(item, (tuple, list)) and len(item) == 3:
        who, _name, text = item
    else:
        return None, ""
    if not isinstance(text, str) or not text.strip():
        return None, ""
    raw = who if who is not None else sender
    if raw is None:
        return None, ""
    value = str(raw).strip().lower()
    if not value:
        return None, ""
    if value in _ME_DIRECTIONS:
        return "me", text.strip()
    if value in _OTHER_DIRECTIONS:
        return "other", text.strip()
    if who is not None:
        # UI tuple with an unrecognized direction: direction unknown -> skip.
        return None, ""
    # A non-empty imported speaker label (nickname) means "not me" -> other.
    return "other", text.strip()


@dataclass
class _ParsedMessages:
    mine: list[str]
    other: list[str]


def _parse(messages: Iterable[Any]) -> _ParsedMessages:
    mine: list[str] = []
    other: list[str] = []
    for item in messages:
        direction, text = _direction_and_text(item)
        if direction == "me":
            mine.append(text)
        elif direction == "other":
            other.append(text)
    return _ParsedMessages(mine=mine, other=other)


def _dimension_score(spec: dict, other_texts: list[str]) -> DimensionScore:
    positives, negatives = spec["positive"], spec["negative"]
    evidence_messages = 0
    total_delta = 0.0
    for text in other_texts:
        deltas: list[float] = []
        for pattern, strength in positives:
            if pattern.search(text):
                deltas.append(strength)
        for pattern, strength in negatives:
            if pattern.search(text):
                deltas.append(strength)
        if spec["key"] == "engagement":
            if len(text) >= 15:
                deltas.append(0.5)
            elif len(text) <= 2:
                deltas.append(-0.5)
        if not deltas:
            continue
        evidence_messages += 1
        message_delta = max(-2.0, min(2.0, sum(deltas)))
        total_delta += message_delta
    if evidence_messages == 0:
        level = 2  # no evidence -> neutral, reported with evidence_count == 0
    else:
        level = round(2 + 2 * (total_delta / evidence_messages))
    level = max(LEVEL_MIN, min(LEVEL_MAX, int(level)))
    return DimensionScore(key=spec["key"], label=spec["label"],
                          weight=spec["weight"], level=level,
                          evidence_count=evidence_messages)


def _signal_messages(other_texts: list[str]) -> list[tuple[int, float, str]]:
    """(index, absolute signal strength, text) for sample selection."""
    scored: list[tuple[int, float, str]] = []
    for index, text in enumerate(other_texts):
        strength = 0.0
        for spec in _DIMENSION_SPECS:
            for pattern, value in spec["positive"]:
                if pattern.search(text):
                    strength += value
            for pattern, value in spec["negative"]:
                if pattern.search(text):
                    strength += abs(value)
        if strength:
            scored.append((index, strength, text))
    return scored


def estimate_affection(messages: Iterable[Any], *,
                       max_samples: int = MAX_SAMPLES) -> AffectionProfile:
    """Estimate affection from a conversation iterable using local heuristics only.

    ``messages`` may mix ``HistoryRecord`` instances, ``(who, name, body)`` tuples
    and ``{"sender"/"who", "text"/"body"}`` mappings. Direction-unknown items are
    skipped; my own messages never contribute to the affection dimensions.
    """
    if messages is None:
        raise TypeError("messages 必须是可迭代对象。")
    parsed = _parse(messages)
    dimensions = tuple(_dimension_score(spec, parsed.other) for spec in _DIMENSION_SPECS)
    boundary = boundary_signal_score(parsed.other)
    boundary_applied = boundary >= 0.8
    other_count = len(parsed.other)

    if other_count == 0:
        dimensions = tuple(DimensionScore(key=dim.key, label=dim.label,
                                          weight=dim.weight, level=2,
                                          evidence_count=0) for dim in dimensions)
        return AffectionProfile(
            score=0, level_label=level_label_for(0), dimensions=dimensions,
            boundary_applied=False, confidence=0.0,
            status=STATUS_INSUFFICIENT, sample_messages=(),
            other_messages=0, my_messages=len(parsed.mine))

    evidence_total = sum(dim.evidence_count for dim in dimensions)
    raw_score = round(sum(dim.weight * dim.level for dim in dimensions) / 4)
    raw_score = max(0, min(100, raw_score))
    score = min(BOUNDARY_CAP, raw_score) if boundary_applied else raw_score

    if other_count < 2 or evidence_total < 2:
        status = STATUS_INSUFFICIENT
    elif other_count >= 4 and evidence_total >= 6:
        status = STATUS_CLEAR
    else:
        status = STATUS_AMBIGUOUS
    confidence = (0.2 + 0.5 * min(1.0, evidence_total / 10.0)
                  + 0.3 * min(1.0, other_count / 10.0))
    if status == STATUS_INSUFFICIENT:
        confidence = min(confidence, 0.35)
    if boundary_applied:
        # 明确拒绝/保持距离是清晰信号：它可以给出“明确”的低好感结论。
        status = STATUS_CLEAR
        confidence = max(confidence, 0.7)
    confidence = round(max(0.0, min(0.95, confidence)), 3)

    signals = _signal_messages(parsed.other)
    signals.sort(key=lambda row: (-row[1], row[0]))
    chosen = signals[:max(1, int(max_samples))]
    chosen.sort(key=lambda row: row[0])
    samples = tuple(row[2][:MAX_SAMPLE_CHARS] for row in chosen)

    return AffectionProfile(
        score=int(score), level_label=level_label_for(score),
        dimensions=dimensions, boundary_applied=boundary_applied,
        confidence=confidence, status=status, sample_messages=samples,
        other_messages=other_count, my_messages=len(parsed.mine))
