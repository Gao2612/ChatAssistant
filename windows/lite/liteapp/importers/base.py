"""Import parser contracts, centralized limits and small safe file helpers.

PLAN T6: every limit lives here so UI, jobs and tests share one definition; the
parsers are pure local code (no Qt, no network, no scripts). Bodies are read as
bytes and decoded with explicit encodings; nothing here logs chat content.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from ..contracts import CancellationToken
from ..history_models import HistoryImportError, ImportedRecord, PageIssue

__all__ = [
    "HTML_MAX_RECORDS",
    "HTML_PARSE_TIMEOUT_SECONDS",
    "MAX_IMPORT_FILE_BYTES",
    "PDF_MAX_PAGE_PIXELS",
    "PDF_PAGE_TIMEOUT_SECONDS",
    "PDF_RENDER_DPI",
    "READ_CHUNK_BYTES",
    "WORKER_POLL_SECONDS",
    "FormatMatch",
    "Parser",
    "checked_file",
    "file_digest",
    "read_limited_bytes",
    "sniff_head",
]

MAX_IMPORT_FILE_BYTES = 100 * 1024 * 1024
"""Single import file limit (100 MB), identical to the 0.2 limit."""

HTML_PARSE_TIMEOUT_SECONDS = 30.0
"""Wall-clock limit for one HTML parse (checked while feeding the parser)."""

PDF_RENDER_DPI = 150
"""Default rasterization DPI for OCR pages."""

PDF_MAX_PAGE_PIXELS = 12_000_000
"""Hard per-page rasterization cap (width * height after scaling)."""

PDF_PAGE_TIMEOUT_SECONDS = 30.0
"""Per-page extract/render/OCR limit; timed-out pages are retried individually."""

HTML_MAX_RECORDS = 200_000
"""Upper bound on extracted HTML fragments to keep one import bounded."""

READ_CHUNK_BYTES = 1024 * 1024
WORKER_POLL_SECONDS = 0.1

SUPPORT_LEVELS = ("exact", "generic", "signature_fallback", "unverified")
FORMAT_KINDS = ("html", "pdf", "exportwechat_html")


@dataclass(frozen=True)
class FormatMatch:
    """Result of ``probe``: which parser to use and how much is actually verified.

    ``support_level`` is deliberately conservative: without a real ExportWeChat
    sample the format is reported as ``signature_fallback`` (generic parser), and
    only calibrated parsers may use ``exact``.
    """

    kind: str
    parser_name: str
    parser: type
    support_level: str
    confidence: str
    signature: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in FORMAT_KINDS:
            raise ValueError(f"未知格式 kind：{self.kind!r}")
        if self.support_level not in SUPPORT_LEVELS:
            raise ValueError(f"未知 support_level：{self.support_level!r}")
        if not isinstance(self.parser, type):
            raise TypeError("parser 必须是 Parser 子类。")
        object.__setattr__(self, "signature", tuple(self.signature or ()))
        object.__setattr__(self, "notes", tuple(self.notes or ()))

    @property
    def exact_support(self) -> bool:
        """True only for a calibrated parser; never guessed from the file name."""
        return self.support_level == "exact"


class Parser:
    """Base parser: ``probe`` is cheap/read-only, ``parse`` yields records.

    ``page_count``/``pages_done``/``page_issues`` are filled while ``parse`` runs
    and read by :class:`liteapp.import_jobs.ImportJobs` afterwards (one instance
    per job). ``enable_page_ocr`` is set by the job for PDF scanned pages.
    """

    kind = "unknown"
    parser_name = "base"
    support_level = "generic"

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self.clock = clock or time.monotonic
        self.page_count = 0
        self.pages_done = 0
        self.page_issues: list[PageIssue] = []
        self.notes: list[str] = []
        self.enable_page_ocr = False

    @classmethod
    def probe(cls, path: Path) -> FormatMatch | None:
        raise NotImplementedError

    def parse(self, path: Path, cancel: CancellationToken) -> Iterator[ImportedRecord]:
        raise NotImplementedError

    def add_issue(self, page: int, code: str, detail: str = "") -> None:
        self.page_issues.append(PageIssue(page=page, code=code, detail=detail))


def checked_file(path: Path | str) -> Path:
    """Validate existence/type/size before any parser touches the content."""
    path = Path(path)
    try:
        if not path.exists():
            raise HistoryImportError("文件不存在或已被移动。")
        if not path.is_file():
            raise HistoryImportError("只支持导入单个文件。")
        size = path.stat().st_size
    except HistoryImportError:
        raise
    except OSError:
        raise HistoryImportError("文件无法读取。") from None
    if size <= 0:
        raise HistoryImportError("文件为空。")
    if size > MAX_IMPORT_FILE_BYTES:
        raise HistoryImportError("文件超过 100 MB，请分批导入。")
    return path


def read_limited_bytes(path: Path | str) -> bytes:
    path = checked_file(path)
    try:
        return path.read_bytes()
    except OSError:
        raise HistoryImportError("文件无法读取。") from None


def file_digest(path: Path | str) -> str:
    """Streaming SHA-256 of the file bytes (also used for duplicate detection)."""
    path = checked_file(path)
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(READ_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        raise HistoryImportError("文件无法读取。") from None
    return digest.hexdigest()


def sniff_head(path: Path | str, limit: int = 4096) -> bytes:
    path = checked_file(path)
    try:
        with path.open("rb") as handle:
            return handle.read(limit)
    except OSError:
        raise HistoryImportError("文件无法读取。") from None
