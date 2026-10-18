"""Import parser registry (T6).

``probe`` is cheap and read-only; unknown formats return ``None`` and are never
parsed by guesswork. ExportWeChat candidates with a calibrated companion JSON
(``<name>.html.json``) get the exact parser (see
:mod:`liteapp.importers.exportwechat_json`); HTML-only ExportWeChat exports have
no calibrated DOM and stay on the generic safe parser (see
:mod:`liteapp.importers.exportwechat_html`).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from ..history_models import HistoryImportError
from . import exportwechat_html, exportwechat_json, html_generic, pdf
from .base import (
    HTML_PARSE_TIMEOUT_SECONDS,
    MAX_IMPORT_FILE_BYTES,
    PDF_MAX_PAGE_PIXELS,
    PDF_PAGE_TIMEOUT_SECONDS,
    PDF_RENDER_DPI,
    FormatMatch,
    Parser,
    checked_file,
    file_digest,
)

__all__ = [
    "HTML_PARSE_TIMEOUT_SECONDS",
    "MAX_IMPORT_FILE_BYTES",
    "PDF_MAX_PAGE_PIXELS",
    "PDF_PAGE_TIMEOUT_SECONDS",
    "PDF_RENDER_DPI",
    "FormatMatch",
    "Parser",
    "create_parser",
    "file_digest",
    "probe",
    "select_parser",
    "supported_extensions",
]

# Order matters: signature-based adapters first, then container formats, then the
# generic HTML fallback. ``exportwechat_html.probe`` delegates to the calibrated
# companion-JSON probe, so ``<name>.html`` + ``<name>.html.json`` resolves to the
# exact ExportWeChat parser before ``html_generic`` can claim the same HTML.
# Each probe validates size itself (fail closed).
_PROBES = (exportwechat_html.probe, exportwechat_json.probe, pdf.probe,
           html_generic.probe)


def supported_extensions() -> tuple[str, ...]:
    return (".html", ".htm", ".pdf", ".HTM", ".HTML", ".PDF")


def probe(path: Path | str) -> FormatMatch | None:
    """Return the format match for ``path`` or ``None`` when unsupported."""
    path = checked_file(path)
    for probe_function in _PROBES:
        match = probe_function(path)
        if match is not None:
            return match
    return None


def select_parser(path: Path | str) -> FormatMatch:
    match = probe(path)
    if match is None:
        raise HistoryImportError("不支持的文件格式；目前仅支持 HTML 与 PDF 文件。")
    return match


def create_parser(match: FormatMatch, *, clock: Callable[[], float] | None = None,
                  page_timeout: float | None = None,
                  html_timeout: float | None = None,
                  python_executable: str | None = None,
                  worker_command: list[str] | None = None) -> Parser:
    """Instantiate the parser described by ``match`` (tests may inject options)."""
    parser_class = match.parser
    if issubclass(parser_class, pdf.PDFParser):
        parser = parser_class(clock=clock, page_timeout=page_timeout,
                              worker_command=worker_command,
                              python_executable=python_executable)
    elif issubclass(parser_class, html_generic.HtmlGenericParser):
        parser = parser_class(clock=clock, parse_timeout=html_timeout)
    elif issubclass(parser_class, exportwechat_json.ExportWeChatJSONParser):
        parser = parser_class(clock=clock, parse_timeout=html_timeout)
    else:  # test doubles only need the clock
        try:
            parser = parser_class(clock=clock)
        except TypeError:
            parser = parser_class()
    return parser
