"""Historical reports: deterministic local report + labelled model-report adapter.

T8 (PLAN) adds:

* explicit labels so model output and local facts are never mixed:
  ``[事实]`` / ``[推断]`` / ``[待确认]``; unavailable values are rendered as
  “不可计算” by :mod:`liteapp.history_statistics`;
* a duck-typed adapter for the T7 ``HistoryReport`` (frozen interface:
  ``scope/source_version/memory_version/summary/facts/inferences/open_questions/
  coverage/parse_missing_pages/timeline``; each ``ReportItem`` has
  ``category`` (``fact``/``inference``/``open_question``), ``text`` and an
  ``EvidenceRef`` tuple). This module does **not** import T7 modules, so T8 can be
  tested with fake reports and T7 can land independently;
* the legacy deterministic :func:`build_local_report` stays available for the
  history page (same observable strings as before, now labelled).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .contracts import EvidenceRef

__all__ = [
    "LABEL_FACT",
    "LABEL_INFERENCE",
    "LABEL_LOCAL_FACT",
    "LABEL_OPEN_QUESTION",
    "PROFILE_SECTIONS",
    "ReportFinding",
    "build_local_report",
    "findings_by_section",
    "render_report_lines",
    "report_findings",
    "report_meta",
]

LABEL_FACT = "事实"
LABEL_INFERENCE = "推断"
LABEL_OPEN_QUESTION = "待确认"
LABEL_LOCAL_FACT = "本地统计（事实）"

PROFILE_SECTIONS = ("需求", "偏好", "承诺", "待办", "冲突", "时间线")

_KIND_LABEL = {"fact": LABEL_FACT, "inference": LABEL_INFERENCE,
               "open_question": LABEL_OPEN_QUESTION}
_GROUP_KIND = (("facts", "fact"), ("inferences", "inference"),
               ("open_questions", "open_question"))

_SECTION_KEYWORDS = (
    ("冲突", ("冲突", "矛盾", "不一致", "变更", "改口", "争议", "前后")),
    ("承诺", ("承诺", "答应", "保证", "一定", "已确认", "说好", "交付")),
    ("待办", ("待办", "待确认", "待定", "下一步", "尚未", "跟进", "还需要")),
    ("需求", ("需求", "需要", "预算", "想要", "要求", "期望", "采购", "规格", "功能")),
    ("偏好", ("偏好", "倾向", "喜欢", "更愿意", "习惯", "风格", "方便")),
)
_DATE_RE = re.compile(r"(?<!\d)(20\d{2})[-/.年](0?[1-9]|1[0-2])(?:[-/.月](\d{1,2}))?")
_TEXT_KEYS = ("text", "summary", "content", "statement", "title", "value")
_EVIDENCE_KEYS = ("evidence", "evidence_refs", "refs", "references")

DATE = re.compile(r"(?<!\d)(20\d{2})[-/.年](0?[1-9]|1[0-2])(?:[-/.月]\d{1,2})?")
TOPICS = {
    "预算与价格": re.compile(r"预算|报价|价格|折扣|优惠|成本"),
    "库存与交期": re.compile(r"库存|现货|发货|交期|送达|到货|交付"),
    "产品需求": re.compile(r"需求|规格|型号|设备|功能|尺寸"),
    "售后与风险": re.compile(r"售后|保修|退货|退款|故障|投诉"),
}


@dataclass(frozen=True)
class ReportFinding:
    """One normalized model-report item with its evidence label."""

    label: str
    kind: str
    text: str
    category: str
    evidence: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence or ()))

    @property
    def has_evidence(self) -> bool:
        return bool(self.evidence)

    def as_line(self) -> str:
        suffix = f"（证据 {len(self.evidence)} 条）" if self.evidence else "（无证据引用）"
        return f"[{self.label}] {self.category}：{self.text}{suffix}"


# ------------------------------------------------------------------ duck-typed adapter
def _get(obj: Any, names: Iterable[str], default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        for name in names:
            if name in obj:
                return obj[name]
        return default
    for name in names:
        if hasattr(obj, name):
            return getattr(obj, name)
    return default


def _iter_items(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        return tuple(value.values())
    if isinstance(value, (str, bytes)):
        return ()
    try:
        return tuple(value)
    except TypeError:
        return (value,)


def _item_text(item: Any) -> str:
    value = _get(item, _TEXT_KEYS, "")
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _item_category_hint(item: Any) -> str:
    category = _get(item, ("category", "section", "group", "type"), "")
    return str(category).strip() if isinstance(category, str) else ""


def _item_evidence(item: Any) -> tuple[EvidenceRef, ...]:
    raw = _get(item, _EVIDENCE_KEYS, ())
    if raw is None:
        return ()
    if isinstance(raw, EvidenceRef):
        return (raw,)
    result = []
    for entry in _iter_items(raw):
        if isinstance(entry, EvidenceRef):
            result.append(entry)
        elif isinstance(entry, Mapping):
            try:
                result.append(EvidenceRef(
                    scope=entry["scope"], source_id=entry["source_id"],
                    record_id=entry["record_id"], start=int(entry["start"]),
                    end=int(entry["end"])))
            except (KeyError, TypeError, ValueError):
                continue
    return tuple(result)


def _derive_category(item: Any, hint: str, text: str) -> str:
    for section in PROFILE_SECTIONS:
        if hint == section or section in hint:
            return section
    for section, keywords in _SECTION_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return section
    if _DATE_RE.search(text):
        return "时间线"
    return "其他"


def _finding(item: Any, kind: str, *, forced_category: str | None = None) -> ReportFinding | None:
    text = _item_text(item)
    if not text:
        return None
    hint = _item_category_hint(item)
    category = forced_category or _derive_category(item, hint, text)
    return ReportFinding(label=_KIND_LABEL[kind], kind=kind, text=text,
                         category=category, evidence=_item_evidence(item))


def report_findings(report: Any) -> tuple[ReportFinding, ...]:
    """Normalize a T7 HistoryReport (or a mapping/fake) into labelled findings."""
    if report is None:
        return ()
    findings: list[ReportFinding] = []
    seen: set[tuple[str, str]] = set()

    def add(finding: ReportFinding | None) -> None:
        if finding is None:
            return
        key = (finding.kind, finding.text)
        if key in seen:
            return
        seen.add(key)
        findings.append(finding)

    for group_name, kind in _GROUP_KIND:
        for item in _iter_items(_get(report, (group_name,))):
            if isinstance(item, str):
                add(_finding({"text": item}, kind))
            else:
                add(_finding(item, kind))
    # Optional explicit timeline (T7 currently returns an empty tuple).
    for item in _iter_items(_get(report, ("timeline",))):
        if isinstance(item, str):
            add(_finding({"text": item}, "fact", forced_category="时间线"))
        else:
            add(_finding(item, "fact", forced_category="时间线"))
    # Optional grouped profile mapping: {"需求": [...], "承诺": [...]}.
    profile = _get(report, ("profile", "sections", "by_category"))
    if isinstance(profile, Mapping):
        for section, items in profile.items():
            if not isinstance(section, str):
                continue
            for item in _iter_items(items):
                if isinstance(item, str):
                    add(_finding({"text": item}, "fact", forced_category=section))
                else:
                    add(_finding(item, "fact", forced_category=section))
    return tuple(findings)


def findings_by_section(report: Any) -> dict[str, tuple[ReportFinding, ...]]:
    grouped: dict[str, list[ReportFinding]] = {}
    for finding in report_findings(report):
        grouped.setdefault(finding.category, []).append(finding)
    ordered: dict[str, tuple[ReportFinding, ...]] = {}
    for section in PROFILE_SECTIONS:
        if grouped.get(section):
            ordered[section] = tuple(grouped[section])
    for section in grouped:
        if section not in ordered:
            ordered[section] = tuple(grouped[section])
    return ordered


def report_meta(report: Any) -> dict[str, Any]:
    """Best-effort metadata for display; missing fields stay ``None``."""
    if report is None:
        return {}
    meta: dict[str, Any] = {}
    for key in ("source_version", "memory_version", "records_done", "total_records",
                "covered_chars", "total_chars", "profile_id", "model_version",
                "prompt_version", "generated_at", "summary", "job_id"):
        value = _get(report, (key,))
        if value is not None:
            meta[key] = value
    coverage = _get(report, ("coverage",))
    try:
        meta["coverage"] = float(coverage) if coverage is not None else None
    except (TypeError, ValueError):
        meta["coverage"] = None
    for key in ("parse_missing_pages", "page_issue_count"):
        try:
            value = _get(report, (key,), 0)
            meta[key] = int(value or 0)
        except (TypeError, ValueError):
            meta[key] = 0
    meta.setdefault("parse_missing_pages", 0)
    meta.setdefault("page_issue_count", 0)
    return meta


def render_report_lines(report: Any) -> tuple[str, ...]:
    """Render a T7 report as labelled text (facts vs inferences vs open questions)."""
    if report is None:
        return ("尚无模型分析结果；完成历史分析任务后显示。",)
    meta = report_meta(report)
    lines = ["模型档案与趋势（模型输出；[事实]/[推断]/[待确认] 为证据标签）"]
    summary = meta.get("summary")
    if summary:
        lines.append(f"[事实] 摘要：{summary}")
    version_parts = []
    if meta.get("source_version") is not None:
        version_parts.append(f"source_version={meta['source_version']}")
    if meta.get("memory_version") is not None:
        version_parts.append(f"memory_version={meta['memory_version']}")
    if version_parts:
        lines.append("[事实] 输入版本：" + "，".join(version_parts))
    if meta.get("coverage") is not None:
        lines.append(f"[事实] 覆盖率：{meta['coverage']:.0%}")
    if meta.get("covered_chars") is not None and meta.get("total_chars") is not None:
        lines.append(f"[事实] 覆盖字符：{meta['covered_chars']}/{meta['total_chars']}")
    if meta.get("parse_missing_pages"):
        lines.append(f"[事实] 解析缺页：{meta['parse_missing_pages']} 页")
    if meta.get("page_issue_count"):
        lines.append(f"[事实] 页级问题：{meta['page_issue_count']} 条")
    grouped = findings_by_section(report)
    if not grouped:
        lines.append("没有可显示的模型条目。")
    for section, findings in grouped.items():
        for finding in findings:
            lines.append(finding.as_line())
    lines.append("[推断] 模型倾向性解释与 [待确认] 未解决问题不进入事实库；"
                 "无证据内容不作为事实展示。")
    return tuple(lines)


# ------------------------------------------------------------------ local report
def build_local_report(store: Any, scope: Any, statistics: Any = None) -> str:
    """Deterministic local report over every imported text section (no model).

    Keeps the historical observable strings (record count, month lines, evidence
    snippets) while adding explicit ``[事实]``/``[推断]`` labels and the T8
    deterministic statistics header.
    """
    totals = store.stats(scope)
    if not totals.record_count:
        return "尚未导入文字记录。"
    if statistics is None:
        from .history_statistics import HistoryStatistics
        statistics = HistoryStatistics(store).compute(scope, store.snapshot(scope))
    months = Counter()
    topics = Counter()
    speakers = Counter()
    evidence = []
    undated = 0
    processed = 0
    offset = 0
    while True:
        page = store.list_messages(scope, offset=offset, limit=500)
        if not page:
            break
        for row in page:
            text = row["text"]
            processed += 1
            dates = DATE.findall(text)
            if dates:
                months.update(f"{year}-{int(month):02d}" for year, month in dates)
            else:
                undated += 1
            if re.search(r"(^|\s)(我|自己|本人)\s*[：:]", text):
                speakers["我"] += 1
            elif re.search(r"(^|\s)(客户|对方|买家)\s*[：:]", text):
                speakers["对方"] += 1
            else:
                speakers["未识别"] += 1
            found = [name for name, pattern in TOPICS.items() if pattern.search(text)]
            topics.update(found)
            if found and len(evidence) < 30:
                evidence.append(f"- {row['source_name']} / {row['label']} "
                                f"[{', '.join(found)}]：{text[:180]}")
        offset += len(page)
    result = ["本地历史整理报告（确定性本地统计；未调用模型）", "",
              f"[{LABEL_LOCAL_FACT}] 范围：{statistics.source_count} 个导入文件，"
              f"{statistics.record_count} 个文字片段，约 {statistics.char_count} 字。",
              f"[{LABEL_LOCAL_FACT}] 时间范围："
              + (f"{statistics.time_range[0]} ~ {statistics.time_range[1]}"
                 if statistics.time_range[0] else "不可计算（无可靠 sent_at）")
              + f"；{statistics.dated_record_count} 条有可靠时间，"
                f"{statistics.undated_record_count} 条无可靠时间。",
              f"[{LABEL_FACT}] 片段统计：片段 {statistics.fragment_count} 条、"
              f"标准消息 {statistics.message_count} 条；片段不等于微信原始消息数。",
              f"[{LABEL_FACT}] 回复时延："
              + ("不可计算" if statistics.reply_latency_seconds is None
                 else f"中位 {statistics.reply_latency_seconds:.1f} 秒"
                      f"（{statistics.reply_latency_samples} 个换边样本）")
              + f"；双方主动比例："
              + ("不可计算" if statistics.initiative_ratio is None
                 else f"我 {statistics.initiative_ratio:.0%} / "
                      f"对方 {1 - statistics.initiative_ratio:.0%}") + "。",
              f"[{LABEL_FACT}] 重叠来源：{statistics.overlap_note}",
              f"[{LABEL_FACT}] 覆盖与解析质量：{statistics.coverage_note}"
              + (" " + statistics.quality_note if statistics.page_failure_count else ""),
              "",
              "客户画像线索（仅列明确文字证据，不推断性格或真实意图）"]
    result.extend(f"[{LABEL_FACT}] {line}" for line in evidence)
    if not evidence:
        result.append("未发现预算、交期、产品或售后等明确线索。")
    result.extend(["", "关系与互动趋势（仅按可识别的时间和文本数量）"])
    result.extend(f"[{LABEL_FACT}] - {month}：{count} 个带日期片段"
                  for month, count in sorted(months.items()))
    result.append(f"[{LABEL_INFERENCE}] - {undated} 个片段未识别到日期；"
                  "文本中出现的日期仅作线索，不据此推断关系好坏。")
    result.extend(["", "聊天统计（文本片段，不等于微信原始消息数）",
                   f"[{LABEL_FACT}] - 发送人线索：我 {speakers['我']}，"
                   f"对方 {speakers['对方']}，未识别 {speakers['未识别']}。"])
    result.extend(f"[{LABEL_FACT}] - {topic}：{topics[topic]} 个相关片段"
                  for topic in TOPICS)
    return "\n".join(result)
