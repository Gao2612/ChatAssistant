"""Deterministic local statistics over one frozen history snapshot (T8).

PLAN T8 / design §7.3: statistics are computed **locally and deterministically**
— no model, no network. Fragments stay fragments (they are never presented as
original WeChat message counts), unreliable sender/time fields make reply latency
and per-side initiative explicitly "不可计算" instead of being guessed, and
duplicate text across files is only a *candidate* overlap (never confirmed
duplication).

Every number carries a label:

* ``FACT``        — directly counted from stored fields / snapshot metadata;
* ``INFERENCE``   — a text-only clue (e.g. dates mentioned inside fragment text);
* ``UNAVAILABLE`` — cannot be computed from reliable fields (shown as 不可计算).

Model-extracted fact/inference/open-question content (T7) is rendered separately
by :mod:`liteapp.profile_ui`; this module never fabricates model output.
"""

from __future__ import annotations

import re
import statistics as _statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

from .contracts import ScopeKey
from .history_models import (
    EvidenceRef,
    HistoryRecord,
    ScopeMismatchError,
    SourceSnapshot,
    StaleSourceVersionError,
)

__all__ = [
    "FACT",
    "INFERENCE",
    "LABELS",
    "UNAVAILABLE",
    "HistoryStatistics",
    "Metric",
    "SourceOverlap",
    "StatisticsReport",
]

FACT = "fact"
INFERENCE = "inference"
UNAVAILABLE = "unavailable"
LABELS = {FACT: "事实", INFERENCE: "推断", UNAVAILABLE: "不可计算"}

PROFILE_SECTIONS = ("需求", "偏好", "承诺", "待办", "冲突", "时间线")

_MONTH_RE = re.compile(r"(?<!\d)(20\d{2})[-/.年](0?[1-9]|1[0-2])(?:[-/.月](\d{1,2}))?")
_DATETIME_RE = re.compile(
    r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})日?"
    r"(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?")
_ME_SENDERS = {"我", "自己", "本人", "me", "self"}
_OTHER_SENDERS = {"对方", "客户", "买家", "她", "他", "other", "them"}


@dataclass(frozen=True)
class Metric:
    """One labeled statistic row (title/value/label/note) for the statistics tab."""

    key: str
    title: str
    value: str
    kind: str = FACT
    note: str = ""
    numeric: float | None = None

    def __post_init__(self) -> None:
        if self.kind not in LABELS:
            raise ValueError(f"未知统计标签：{self.kind!r}")

    @property
    def label(self) -> str:
        return LABELS[self.kind]

    def as_line(self) -> str:
        suffix = f"（{self.note}）" if self.note else ""
        return f"[{self.label}] {self.title}：{self.value}{suffix}"


@dataclass(frozen=True)
class SourceOverlap:
    """Candidate overlap: identical normalized text present in several sources."""

    source_ids: tuple[str, ...]
    record_count: int
    sample_record_ids: tuple[str, ...] = ()
    kind: str = "same_text_candidate"


@dataclass(frozen=True)
class StatisticsReport:
    """Deterministic local statistics for one scope + frozen source snapshot."""

    scope: ScopeKey
    source_version: int
    memory_version: int
    source_ids: tuple[str, ...]
    source_count: int
    record_count: int
    fragment_count: int
    message_count: int
    char_count: int
    page_count: int
    page_failure_count: int
    dated_record_count: int
    undated_record_count: int
    date_hint_count: int
    months: tuple[tuple[str, int], ...]
    time_range: tuple[str | None, str | None]
    sender_known_count: int
    sender_unknown_count: int
    speaker_shares: tuple[tuple[str, float], ...] | None
    reply_latency_seconds: float | None
    reply_latency_samples: int
    reply_latency_note: str
    initiative_ratio: float | None
    initiative_note: str
    overlaps: tuple[SourceOverlap, ...]
    overlap_note: str
    coverage_note: str
    quality_note: str
    metrics: tuple[Metric, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        for name in ("source_ids", "months", "overlaps", "metrics"):
            object.__setattr__(self, name, tuple(getattr(self, name) or ()))
        if self.speaker_shares is not None:
            object.__setattr__(self, "speaker_shares", tuple(self.speaker_shares))
        if self.time_range is None:
            object.__setattr__(self, "time_range", (None, None))

    @property
    def has_records(self) -> bool:
        return self.record_count > 0

    @property
    def fragment_only(self) -> bool:
        return self.record_count > 0 and self.message_count == 0

    def metric(self, key: str) -> Metric | None:
        for metric in self.metrics:
            if metric.key == key:
                return metric
        return None

    def as_lines(self) -> tuple[str, ...]:
        lines = ["本地确定性统计（不调用模型；[事实] 可直接核对，[推断] 仅为文本线索）"]
        if not self.has_records:
            lines.append("[本地事实] 尚未导入文字记录。")
            return tuple(lines)
        lines.append(f"[本地事实] 已导入 {self.source_count} 个文件，"
                     f"{self.record_count} 条已提取记录，约 {self.char_count} 字。")
        lines.extend(metric.as_line() for metric in self.metrics)
        lines.append(f"[本地事实] 覆盖说明：{self.coverage_note}")
        lines.append(f"[本地事实] 解析质量：{self.quality_note}")
        return tuple(lines)


def _parse_sent_at(value: Any) -> tuple[str, datetime] | None:
    """Parse a reliable ``sent_at`` string; return (month, datetime) or None."""
    if not isinstance(value, str) or not value.strip():
        return None
    match = _DATETIME_RE.search(value)
    if match is None:
        return None
    year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    hour = int(match.group(4) or 0)
    minute = int(match.group(5) or 0)
    second = int(match.group(6) or 0)
    try:
        moment = datetime(year, month, day, hour, minute, second)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}", moment


def _side(sender: Any) -> str | None:
    if not isinstance(sender, str) or not sender.strip():
        return None
    normalized = sender.strip().casefold()
    if normalized in _ME_SENDERS:
        return "me"
    if normalized in _OTHER_SENDERS:
        return "other"
    return None


def _text_date_hint(text: str) -> bool:
    return isinstance(text, str) and _MONTH_RE.search(text) is not None


def _months_in_text(text: str) -> set[str]:
    if not isinstance(text, str):
        return set()
    return {f"{int(year):04d}-{int(month):02d}"
            for year, month in ((match.group(1), match.group(2))
                                for match in _MONTH_RE.finditer(text))}


class HistoryStatistics:
    """Computes :class:`StatisticsReport` from the V2 store for one frozen snapshot."""

    def __init__(self, store: Any) -> None:
        self.store = store

    def compute(self, scope: ScopeKey, snapshot: SourceSnapshot) -> StatisticsReport:
        if not isinstance(scope, ScopeKey):
            raise TypeError("scope 必须是 ScopeKey。")
        if not isinstance(snapshot, SourceSnapshot):
            raise TypeError("snapshot 必须是 SourceSnapshot。")
        if snapshot.scope != scope:
            raise ScopeMismatchError("快照属于其它联系人范围，拒绝统计。")
        current = self.store.stats(scope)
        if snapshot.source_version != current.source_version:
            raise StaleSourceVersionError(
                f"快照已过期：快照来源版本 {snapshot.source_version}，"
                f"当前 {current.source_version}；请重新生成快照后统计。")
        records = list(self.store.iter_records(scope, snapshot.source_version))
        return self._build(scope, snapshot, records)

    # ------------------------------------------------------------------ helpers
    def _build(self, scope: ScopeKey, snapshot: SourceSnapshot,
               records: list[HistoryRecord]) -> StatisticsReport:
        fragment_count = sum(1 for record in records if record.record_type == "fragment")
        message_count = sum(1 for record in records if record.record_type == "message")
        char_count = sum(len(record.text) for record in records)
        page_count = sum(source.page_count for source in snapshot.sources)
        page_failure_count = sum(source.failure_count for source in snapshot.sources)
        quality_note = self._quality_note(snapshot, page_failure_count)

        months: dict[str, int] = {}
        timed: list[tuple[str, datetime]] = []
        undated = 0
        date_hints = 0
        hint_months: set[str] = set()
        sender_known = 0
        sides: list[str | None] = []
        side_scalar: list[tuple[str, datetime] | None] = []
        for record in records:
            parsed = _parse_sent_at(record.sent_at)
            if parsed is not None:
                month, moment = parsed
                months[month] = months.get(month, 0) + 1
                timed.append((month, moment))
            else:
                undated += 1
                if _text_date_hint(record.text):
                    date_hints += 1
                    hint_months |= _months_in_text(record.text)
            sender = record.sender
            if isinstance(sender, str) and sender.strip():
                sender_known += 1
            sides.append(_side(sender))
            side_scalar.append(parsed)
        sorted_months = tuple(sorted(months.items()))
        time_range = (sorted_months[0][0], sorted_months[-1][0]) if sorted_months else (None, None)

        speaker_shares, initiative_ratio, initiative_note = self._initiative(
            sides, sender_known, len(records))
        reply_latency, latency_samples, latency_note = self._reply_latency(
            sides, side_scalar, len(records))
        overlaps, overlap_count, overlap_note = self._overlaps(records)
        coverage_note = self._coverage_note(page_failure_count)

        metrics = self._metrics(
            source_count=len(snapshot.sources), source_ids=snapshot.source_ids,
            record_count=len(records), fragment_count=fragment_count,
            message_count=message_count, char_count=char_count,
            page_count=page_count, page_failure_count=page_failure_count,
            time_range=time_range, dated_record_count=len(timed), undated=undated,
            date_hints=date_hints, hint_months=hint_months,
            sender_known=sender_known, sender_unknown=len(records) - sender_known,
            speaker_shares=speaker_shares,
            reply_latency=reply_latency, latency_samples=latency_samples,
            reply_latency_note=latency_note, initiative_ratio=initiative_ratio,
            initiative_note=initiative_note, overlap_count=overlap_count,
            overlap_note=overlap_note, quality_note=quality_note)
        return StatisticsReport(
            scope=scope, source_version=snapshot.source_version,
            memory_version=snapshot.memory_version,
            source_ids=snapshot.source_ids, source_count=len(snapshot.sources),
            record_count=len(records), fragment_count=fragment_count,
            message_count=message_count, char_count=char_count,
            page_count=page_count, page_failure_count=page_failure_count,
            dated_record_count=len(timed), undated_record_count=undated,
            date_hint_count=date_hints, months=sorted_months,
            time_range=time_range, sender_known_count=sender_known,
            sender_unknown_count=len(records) - sender_known,
            speaker_shares=speaker_shares,
            reply_latency_seconds=reply_latency,
            reply_latency_samples=latency_samples,
            reply_latency_note=latency_note, initiative_ratio=initiative_ratio,
            initiative_note=initiative_note, overlaps=overlaps,
            overlap_note=overlap_note, coverage_note=coverage_note,
            quality_note=quality_note, metrics=metrics)

    @staticmethod
    def _quality_note(snapshot: SourceSnapshot, page_failure_count: int) -> str:
        codes: dict[str, int] = {}
        for source in snapshot.sources:
            for issue in source.page_issues:
                codes[issue.code] = codes.get(issue.code, 0) + 1
        if not codes:
            return "没有记录到页级解析失败；统计只覆盖已提取文字。"
        detail = "、".join(f"{code} {count}" for code, count in sorted(codes.items()))
        return f"解析失败/缺页 {page_failure_count} 页（{detail}）。"

    @staticmethod
    def _coverage_note(page_failure_count: int) -> str:
        note = ("统计覆盖已导入的全部已提取记录，不代表微信完整历史，"
                "也不等于模型理解覆盖率。")
        if page_failure_count:
            note += f" 有 {page_failure_count} 页解析失败/缺页，统计不包含这些页。"
        return note

    @staticmethod
    def _initiative(sides: list[str | None], sender_known: int,
                    record_count: int) -> tuple[tuple[tuple[str, float], ...] | None,
                                                float | None, str]:
        mapped = [side for side in sides if side is not None]
        me = mapped.count("me")
        other = mapped.count("other")
        if record_count == 0:
            return None, None, "不可计算：没有已提取记录。"
        if me and other and len(mapped) == record_count:
            shares = (("我", me / record_count), ("对方", other / record_count))
            return shares, me / record_count, "按可靠发送人字段统计双方条数占比，不推断态度或意图。"
        if me and other:
            missing = record_count - len(mapped)
            return ((("我", me / len(mapped)), ("对方", other / len(mapped))),
                    me / len(mapped),
                    f"按已知发送人字段统计（{missing} 条未映射到双方）。")
        reason = (f"缺少可靠的发送人字段（{record_count - sender_known} 条未知）"
                  if sender_known < record_count else
                  "发送人字段无法映射为‘我/对方’双方")
        return None, None, f"不可计算：{reason}。"

    @staticmethod
    def _reply_latency(sides: list[str | None],
                       times: list[tuple[str, datetime] | None],
                       record_count: int) -> tuple[float | None, int, str]:
        samples: list[float] = []
        previous: tuple[str, datetime] | None = None
        previous_side: str | None = None
        for side, parsed in zip(sides, times):
            if parsed is None or side is None:
                continue
            if previous is not None and previous_side is not None and side != previous_side:
                delta = (parsed[1] - previous[1]).total_seconds()
                if delta >= 0:
                    samples.append(delta)
            previous = parsed
            previous_side = side
        if record_count == 0:
            return None, 0, "不可计算：没有已提取记录。"
        if len(samples) >= 2:
            median = float(_statistics.median(samples))
            return median, len(samples), "按相邻换边记录的可靠时间差取中位数；不猜测缺失时间。"
        if len(samples) == 1:
            return float(samples[0]), 1, "只有一个可靠换边样本，仅作参考。"
        return None, 0, ("不可计算：需要连续两条可靠发送人且时间可解析的不同侧记录"
                         "（缺失时间或发送人不猜测）。")

    @staticmethod
    def _overlaps(records: list[HistoryRecord]) -> tuple[tuple[SourceOverlap, ...], int, str]:
        groups: dict[str, dict[str, Any]] = {}
        for record in records:
            normalized = " ".join(record.text.split()).casefold()
            if len(normalized) < 8:
                continue
            group = groups.setdefault(normalized, {"sources": set(), "records": []})
            group["sources"].add(record.source_id)
            group["records"].append(record.record_id)
        overlaps = []
        for group in groups.values():
            if len(group["sources"]) < 2:
                continue
            overlaps.append(SourceOverlap(
                source_ids=tuple(sorted(group["sources"])),
                record_count=len(group["records"]),
                sample_record_ids=tuple(group["records"][:3])))
        overlaps.sort(key=lambda item: (-item.record_count, item.source_ids))
        total = len(overlaps)
        shown = tuple(overlaps[:20])
        if total:
            note = (f"发现 {total} 组正文在多个来源重复（只标为重叠来源候选，"
                    "不是确认重复；需导出消息 ID/序列/时间/发送人证据才能确认）。")
        else:
            note = "未发现跨来源重复正文候选；统计不据此删除任何记录。"
        return shown, total, note

    @staticmethod
    def _metrics(**values: Any) -> tuple[Metric, ...]:
        time_range = values["time_range"]
        if values["dated_record_count"]:
            time_value = f"{time_range[0]} ~ {time_range[1]}"
            time_kind = FACT
            time_note = (f"{values['dated_record_count']} 条有可靠时间，"
                         f"{values['undated']} 条无可靠时间。")
        else:
            time_value = "不可计算"
            time_kind = UNAVAILABLE
            time_note = f"没有可靠 sent_at 时间字段（{values['undated']} 条未提供）。"
        hints = values["date_hints"]
        hint_value = f"{hints} 条" + (
            "（" + "、".join(sorted(values["hint_months"])) + "）"
            if values["hint_months"] else "")
        latency = values["reply_latency"]
        latency_metric = Metric(
            key="reply_latency", title="回复时延（中位）",
            value="不可计算" if latency is None else f"{latency:.1f} 秒",
            kind=UNAVAILABLE if latency is None else FACT,
            note=values["reply_latency_note"],
            numeric=latency)
        initiative = values["initiative_ratio"]
        initiative_metric = Metric(
            key="initiative", title="双方主动比例",
            value=("不可计算" if initiative is None else
                   f"我 {values['speaker_shares'][0][1]:.0%} / "
                   f"对方 {values['speaker_shares'][1][1]:.0%}"),
            kind=UNAVAILABLE if initiative is None else FACT,
            note=values["initiative_note"], numeric=initiative)
        return (
            Metric(key="sources", title="导入文件数", value=str(values["source_count"]),
                   kind=FACT, note="同一范围按文件摘要去重。"),
            Metric(key="records", title="文字片段（片段统计）",
                   value=str(values["record_count"]), kind=FACT,
                   note=(f"片段不等于微信原始消息数；其中片段类型 "
                         f"{values['fragment_count']} 条、标准消息 "
                         f"{values['message_count']} 条。")),
            Metric(key="fragments", title="片段类型记录",
                   value=str(values["fragment_count"]), kind=FACT,
                   note="旧版迁移/通用 HTML 回退产生的记录，保留为 fragment。"),
            Metric(key="messages", title="标准消息记录",
                   value=str(values["message_count"]), kind=FACT,
                   note="只有具备可靠消息类型时才会被计为消息。"),
            Metric(key="chars", title="字符数", value=str(values["char_count"]),
                   kind=FACT, note="按已提取正文的字符数统计。"),
            Metric(key="time_range", title="可靠时间范围", value=time_value,
                   kind=time_kind, note=time_note),
            Metric(key="date_hints", title="文本中识别到的日期线索",
                   value=hint_value, kind=INFERENCE,
                   note="仅在正文里提到日期，只是文本线索，不能当作发送时间或时间轴事实。"),
            Metric(key="speakers", title="已知发送人",
                   value=f"{values['sender_known']}/{values['record_count']}",
                   kind=FACT,
                   note=(f"{values['sender_unknown']} 条未识别发送人；"
                         "缺失不猜测。")),
            latency_metric,
            initiative_metric,
            Metric(key="overlaps", title="重叠来源候选",
                   value=f"{values['overlap_count']} 组", kind=FACT,
                   note=values["overlap_note"]),
            Metric(key="page_failures", title="解析失败/缺页",
                   value=f"{values['page_failure_count']} 页", kind=FACT,
                   note=values["quality_note"]),
        )
