"""Safe generic HTML -> text fragments.

Only the standard library HTML parser is used. Scripts, styles, iframes and
other non-content elements are skipped; attributes (including ``src``/``href``)
are never resolved, so no remote resource is requested and nothing is executed.
UTF-8 / UTF-16 (BOM) / GB18030 are preserved; byte order and consecutive
identical lines are kept so evidence can be traced back.
"""

from __future__ import annotations

import codecs
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterator

from ..contracts import CancellationToken, CancelledError
from ..history_models import HistoryImportError, ImportedRecord
from .base import (
    HTML_MAX_RECORDS,
    HTML_PARSE_TIMEOUT_SECONDS,
    FormatMatch,
    Parser,
    checked_file,
    read_limited_bytes,
)

__all__ = ["HtmlGenericParser", "decode_html", "probe"]

HTML_SUFFIXES = (".html", ".htm")
BLOCK_TAGS = {"address", "article", "blockquote", "br", "dd", "div", "dt", "h1",
              "h2", "h3", "h4", "h5", "h6", "li", "p", "section", "td", "th",
              "tr", "pre", "table", "ul", "ol"}
SKIP_TAGS = {"script", "style", "noscript", "svg", "iframe", "template", "head",
             "object", "embed", "link", "meta", "canvas", "video", "audio", "math"}
VOID_TAGS = {"meta", "link", "br", "hr", "img", "input", "area", "base", "col",
             "embed", "param", "source", "track", "wbr"}


def decode_html(raw: bytes) -> str:
    """Decode explicit encodings only; never guesses by executing anything."""
    if raw.startswith(codecs.BOM_UTF8):
        return raw[len(codecs.BOM_UTF8):].decode("utf-8-sig")
    if raw.startswith(codecs.BOM_UTF16_LE) or raw.startswith(codecs.BOM_UTF16_BE):
        return raw.decode("utf-16")
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeError:
            continue
    raise HistoryImportError("HTML 编码无法识别（支持 UTF-8 / UTF-16 / GB18030）。")


class _TextCollector(HTMLParser):
    """Accumulate visible text with block boundaries; enforce timeout/cancel."""

    def __init__(self, *, clock: Callable[[], float], started: float,
                 timeout: float, cancel: CancellationToken) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping: list[str] = []
        self._clock = clock
        self._started = started
        self._timeout = timeout
        self._cancel = cancel

    def _check_limits(self) -> None:
        if self._cancel.is_cancelled():
            raise CancelledError("导入已取消")
        if self._clock() - self._started > self._timeout:
            raise HistoryImportError(
                f"HTML 解析超时：超过 {self._timeout:g} 秒上限，已停止；请拆分文件后重试。")

    def handle_starttag(self, tag, attrs):
        self._check_limits()
        if tag in SKIP_TAGS:
            # Void elements (meta/link/embed...) never produce an end tag; pushing
            # them would leave us skipping the rest of the document.
            if tag not in VOID_TAGS:
                self.skipping.append(tag)
        elif not self.skipping and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        self._check_limits()
        if not self.skipping and tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if self.skipping:
            if tag == self.skipping[-1]:
                self.skipping.pop()
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.skipping:
            return
        self._check_limits()
        self.parts.append(data)


def probe(path: Path) -> FormatMatch | None:
    path = checked_file(path)
    if path.suffix.lower() not in HTML_SUFFIXES:
        return None
    return FormatMatch(
        kind="html", parser_name="html_generic", parser=HtmlGenericParser,
        support_level="generic", confidence="suffix",
        notes=("通用 HTML 纯文本解析：不执行脚本、不请求远程资源；"
               "发送人/时间不保证还原。",))


class HtmlGenericParser(Parser):
    kind = "html"
    parser_name = "html_generic"
    support_level = "generic"

    def __init__(self, *, clock: Callable[[], float] | None = None,
                 parse_timeout: float | None = None) -> None:
        super().__init__(clock=clock)
        self.parse_timeout = HTML_PARSE_TIMEOUT_SECONDS if parse_timeout is None else float(parse_timeout)

    @classmethod
    def probe(cls, path: Path) -> FormatMatch | None:
        return probe(path)

    def parse(self, path: Path, cancel: CancellationToken) -> Iterator[ImportedRecord]:
        cancel = cancel if isinstance(cancel, CancellationToken) else CancellationToken()
        path = checked_file(path)
        if cancel.is_cancelled():
            raise CancelledError("导入已取消")
        raw = read_limited_bytes(path)
        text = decode_html(raw)
        started = self.clock()
        collector = _TextCollector(clock=self.clock, started=started,
                                   timeout=self.parse_timeout, cancel=cancel)
        collector.feed(text)
        collector.close()
        lines = [" ".join(line.split()) for line in "".join(collector.parts).splitlines()
                 if line.strip()]
        if len(lines) > HTML_MAX_RECORDS:
            raise HistoryImportError(
                f"HTML 可提取文字行超过 {HTML_MAX_RECORDS} 条，请拆分文件后导入。")
        if not lines:
            raise HistoryImportError("HTML 中没有可导入的聊天文字。")
        self.page_count = 1
        self.pages_done = 0
        for ordinal, line in enumerate(lines):
            if cancel.is_cancelled():
                raise CancelledError("导入已取消")
            yield ImportedRecord(
                text=line, ordinal=ordinal, record_type="fragment",
                locator={"kind": "html", "section": ordinal + 1},
                quality={"source": "html"})
        self.pages_done = 1
