"""ExportWeChat HTML adapter *framework* (deliberately not claimed as supported).

PLAN T6 / design §7.2: a dedicated adapter may only be enabled after a real
ExportWeChat export sample has been provided and the DOM/format signature is
calibrated. No real sample exists in this project yet, so:

* :func:`probe` recognizes only explicit format markers (never guesses the DOM);
* a recognized candidate is reported as ``signature_fallback`` and the safe
  generic parser continues to do the actual extraction;
* :class:`ExportWeChatHTMLParser` exists as the future calibrated entry point but
  raises until ``SAMPLE_VERIFIED`` becomes true, so nothing is silently claimed.
"""

from __future__ import annotations

from pathlib import Path

from ..contracts import CancellationToken
from ..history_models import HistoryImportError
from .base import FormatMatch, checked_file, sniff_head
from .html_generic import HtmlGenericParser

__all__ = [
    "SAMPLE_VERIFIED",
    "SIGNATURE_MARKERS",
    "UNVERIFIED_NOTE",
    "ExportWeChatHTMLParser",
    "probe",
]

SAMPLE_VERIFIED = False
"""Must stay False until a real (sanitized) ExportWeChat export sample is calibrated."""

SIGNATURE_MARKERS = (b"data-exportwechat", b"exportwechat-message", b"ExportWeChat")
"""Explicit markers observed in synthetic fixtures only; not yet a real signature.

A real sample must confirm/rename these before the dedicated parser is enabled.
"""

UNVERIFIED_NOTE = ("检测到 ExportWeChat 特征标记，但当前没有真实导出样本校准，"
                   "继续使用通用安全纯文本回退，未标记“精确支持”。")

HTML_SUFFIXES = (".html", ".htm")
SIGNATURE_HEAD_BYTES = 512 * 1024


def probe(path: Path) -> FormatMatch | None:
    path = checked_file(path)
    if path.suffix.lower() not in HTML_SUFFIXES:
        return None
    head = sniff_head(path, SIGNATURE_HEAD_BYTES).lower()
    matched = tuple(marker.decode("utf-8", "replace")
                    for marker in SIGNATURE_MARKERS
                    if marker.lower() in head)
    if not matched:
        return None
    if not SAMPLE_VERIFIED:
        return FormatMatch(
            kind="exportwechat_html", parser_name="html_generic",
            parser=HtmlGenericParser, support_level="signature_fallback",
            confidence="signature", signature=matched,
            notes=(UNVERIFIED_NOTE, _pending_feature_note(matched)))
    return FormatMatch(  # pragma: no cover - 需要真实样本校准后才会执行
        kind="exportwechat_html", parser_name="exportwechat_html",
        parser=ExportWeChatHTMLParser, support_level="exact",
        confidence="signature", signature=matched)


def _pending_feature_note(matched: tuple[str, ...]) -> str:
    return "识别到特征：" + ", ".join(matched) + "；专用发送人/时间/附件解析尚未实现。"


class ExportWeChatHTMLParser(HtmlGenericParser):
    """Future calibrated adapter. Raises until a real sample has been verified."""

    kind = "exportwechat_html"
    parser_name = "exportwechat_html"
    support_level = "unverified"

    @classmethod
    def probe(cls, path: Path) -> FormatMatch | None:
        return probe(path)

    def parse(self, path: Path, cancel: CancellationToken):
        if not SAMPLE_VERIFIED:
            raise HistoryImportError(
                "尚未用真实 ExportWeChat 导出样本校准专用解析，"
                "请使用通用安全解析（不会执行脚本或请求远程资源）。")
        for record in super().parse(path, cancel):  # pragma: no cover
            yield record
