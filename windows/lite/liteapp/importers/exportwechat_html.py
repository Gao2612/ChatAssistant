"""ExportWeChat adapter: companion-JSON parser (calibrated) + HTML fallback.

PLAN T6 / design §7.2. A dedicated adapter may only claim ``exact`` support after
a real ExportWeChat export sample has been calibrated. That sample has now been
provided and one part of the format is calibrated:

* the tested ExportWeChat version writes the **complete** conversation into a
  companion file next to the rendered page (``田子夜.html`` + ``田子夜.html.json``);
  the HTML itself is only a rendering shell whose ``#chat-container`` holds a few
  placeholder rows and whose body is filled by JavaScript from that JSON;
* therefore :func:`liteapp.importers.exportwechat_json.probe` (delegated to from
  here, so it wins over the generic HTML fallback) claims ``exact`` support when
  that companion file exists and carries the ExportWeChat message signature;
* **HTML-only exports (no companion JSON) are still NOT calibrated**: the DOM
  layout has never been verified against a real sample, so those keep using the
  safe generic plain-text parser and stay at ``signature_fallback``.
"""

from __future__ import annotations

from pathlib import Path

from ..contracts import CancellationToken
from ..history_models import HistoryImportError
from . import exportwechat_json
from .base import FormatMatch, checked_file, sniff_head
from .html_generic import HtmlGenericParser

__all__ = [
    "SAMPLE_VERIFIED",
    "SIGNATURE_MARKERS",
    "UNVERIFIED_NOTE",
    "ExportWeChatHTMLParser",
    "probe",
]

SAMPLE_VERIFIED = True
"""Calibrated **for the companion-JSON channel only** (verified on a real sample).

Set to True because :mod:`liteapp.importers.exportwechat_json` was calibrated
against a real ExportWeChat export (1854 messages) on 2026-10-18. This flag does
NOT mean the HTML DOM of the export is understood: an HTML-only export without a
companion ``*.html.json`` still has no calibrated adapter and must keep using the
generic plain-text parser (``signature_fallback``), never ``exact``.
"""

SIGNATURE_MARKERS = (b"data-exportwechat", b"exportwechat-message", b"ExportWeChat")
"""Explicit markers for the HTML shell; still not a calibrated DOM signature."""

HTML_ONLY_VERIFIED_NOTE = ("ExportWeChat 的 HTML 模板仍未校准（真实样本的完整聊天在伴生 "
                           "*.html.json 中）；当前文件没有伴生 JSON，继续使用通用纯文本回退，"
                           "未标记“精确支持”。")
UNVERIFIED_NOTE = HTML_ONLY_VERIFIED_NOTE
"""Backwards-compatible alias: the "sample not verified" note is the HTML-only note."""

HTML_SUFFIXES = (".html", ".htm")
SIGNATURE_HEAD_BYTES = 512 * 1024


def probe(path: Path) -> FormatMatch | None:
    """Companion JSON first (exact), then the signature-gated generic fallback."""
    path = checked_file(path)
    if path.suffix.lower() not in HTML_SUFFIXES:
        return None
    # The complete conversation may live in <name>.html.json; that channel is
    # calibrated, so it must win over both this module and html_generic.
    json_match = exportwechat_json.probe(path)
    if json_match is not None:
        return json_match
    head = sniff_head(path, SIGNATURE_HEAD_BYTES).lower()
    matched = tuple(marker.decode("utf-8", "replace")
                    for marker in SIGNATURE_MARKERS
                    if marker.lower() in head)
    if not matched:
        return None
    return FormatMatch(
        kind="exportwechat_html", parser_name="html_generic",
        parser=HtmlGenericParser, support_level="signature_fallback",
        confidence="signature", signature=matched,
        notes=(HTML_ONLY_VERIFIED_NOTE, _pending_feature_note(matched)))


def _pending_feature_note(matched: tuple[str, ...]) -> str:
    return "识别到特征：" + ", ".join(matched) + "；专用发送人/时间/附件解析尚未实现。"


class ExportWeChatHTMLParser(HtmlGenericParser):
    """HTML-shell adapter. Still unverified: only the companion JSON is exact."""

    kind = "exportwechat_html"
    parser_name = "exportwechat_html"
    support_level = "unverified"

    @classmethod
    def probe(cls, path: Path) -> FormatMatch | None:
        return probe(path)

    def parse(self, path: Path, cancel: CancellationToken):
        if not SAMPLE_VERIFIED:  # pragma: no cover - kept for the safety contract
            raise HistoryImportError(
                "尚未用真实 ExportWeChat 导出样本校准专用解析，"
                "请使用通用安全解析（不会执行脚本或请求远程资源）。")
        # Even with SAMPLE_VERIFIED=True the HTML DOM is not calibrated; a plain
        # HTML export must go through the generic text parser, and any real
        # companion JSON is handled by ExportWeChatJSONParser instead.
        raise HistoryImportError(
            "ExportWeChat 的 HTML 模板尚未校准；有伴生 *.html.json 时请用伴生 JSON 精确解析，"
            "否则继续使用通用安全纯文本解析。")
