"""PDF parsing via pypdfium2 rendering + the existing local OCR, in a subprocess.

PLAN T6 / design §7.2:

* text pages are extracted directly; pages without a text layer render locally at
  150 DPI (pixel-capped at 12M) and go through the existing RapidOCR engine;
* per-page work runs in a controlled ``python -m liteapp.importers.pdf --worker``
  subprocess so a hanging renderer/OCR cannot block the UI; a watchdog kills the
  worker after the per-page limit and the parent restarts it at the next page, so
  a timed-out page is recorded as a page-level issue and can be retried alone;
* page order/page numbers are preserved, page/bitmap/textpage handles are closed
  explicitly, and page issues (corrupt/encrypted/no text/OCR failed/timeout) are
  reported per page instead of aborting the whole file;
* images exist only in worker memory and are never written to disk or logs.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from ..contracts import CancellationToken, CancelledError
from ..history_models import HistoryImportError, ImportedRecord
from .base import (
    PDF_MAX_PAGE_PIXELS,
    PDF_PAGE_TIMEOUT_SECONDS,
    PDF_RENDER_DPI,
    WORKER_POLL_SECONDS,
    FormatMatch,
    Parser,
    checked_file,
)

__all__ = [
    "EXIT_PAGE_TIMEOUT",
    "PDFParser",
    "process_document",
    "probe",
    "records_from_lines",
    "render_scale",
    "worker_main",
]

PDF_SUFFIXES = (".pdf",)
EXIT_PAGE_TIMEOUT = 66
EXIT_WORKER_ERROR = 70
WORKER_STDERR_TAIL_LINES = 40
MAX_WORKER_RESTARTS = 2000


def probe(path: Path) -> FormatMatch | None:
    path = checked_file(path)
    if path.suffix.lower() not in PDF_SUFFIXES:
        return None
    return FormatMatch(
        kind="pdf", parser_name="pdf", parser=PDFParser,
        support_level="exact", confidence="suffix",
        notes=("文字页直接提取；无文字页需显式启用本地 OCR（结果可能不完整）。",))


def render_scale(page_width: float, page_height: float, *,
                 dpi: float = PDF_RENDER_DPI,
                 max_pixels: int = PDF_MAX_PAGE_PIXELS) -> tuple[float, bool]:
    """Return ``(scale, downscaled)`` so the rendered page never exceeds the cap."""
    try:
        width = float(page_width)
        height = float(page_height)
    except (TypeError, ValueError):
        width = height = 0.0
    scale = float(dpi) / 72.0
    if width <= 0 or height <= 0:
        return scale, False
    if width * scale * height * scale <= max_pixels + 1e-6:
        return scale, False
    scale = math.sqrt(max_pixels / (width * height))
    return scale, True


def records_from_lines(page_number: int, lines: list[str], *, quality: dict | None = None,
                       ordinal_start: int = 0) -> list[ImportedRecord]:
    """Normalize page text lines into fragment records with page/line locators."""
    quality = dict(quality or {"source": "text"})
    records: list[ImportedRecord] = []
    line_number = 0
    for line in lines:
        normalized = " ".join(str(line).split())
        if not normalized:
            continue
        line_number += 1
        records.append(ImportedRecord(
            text=normalized,
            ordinal=ordinal_start + len(records),
            record_type="fragment",
            locator={"kind": "pdf", "page": int(page_number), "line": line_number},
            quality=dict(quality)))
    return records


class _PageError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def process_page(page_number: int, page: Any, *, enable_page_ocr: bool,
                 ocr_text: Callable[[Any], str] | None, ordinal_start: int,
                 dpi: float = PDF_RENDER_DPI,
                 max_pixels: int = PDF_MAX_PAGE_PIXELS) -> list[ImportedRecord]:
    """Process exactly one page (no clock/watchdog) -> records or ``_PageError``."""
    try:
        size = tuple(page.get_size())
    except Exception:
        size = (612.0, 792.0)
    try:
        text = page.extract_text() or ""
    except Exception as error:
        raise _PageError("corrupt_page", "页面文字层无法解析") from error
    text = text.strip()
    quality: dict[str, Any] = {"source": "text"}
    if not text:
        if enable_page_ocr and ocr_text is not None:
            try:
                scale, downscaled = render_scale(size[0], size[1], dpi=dpi,
                                                 max_pixels=max_pixels)
                image = page.render(scale)
                text = (ocr_text(image) or "").strip()
            except _PageError:
                raise
            except Exception as error:
                raise _PageError("ocr_failed", "页面本地 OCR 失败") from error
            if not text:
                raise _PageError("ocr_empty", "页面本地 OCR 未识别出文字")
            quality = {"source": "ocr", "downscaled": downscaled,
                       "render_dpi": round(scale * 72.0, 1)}
        else:
            raise _PageError("no_text", "页面没有文字层；如需 OCR 请显式启用页级 OCR")
    return records_from_lines(page_number, text.splitlines(), quality=quality,
                              ordinal_start=ordinal_start)


def process_document(*, page_count: int, get_page: Callable[[int], Any],
                     emit: Callable[[dict], None], enable_page_ocr: bool = False,
                     ocr_text: Callable[[Any], str] | None = None,
                     clock: Callable[[], float] | None = None,
                     page_timeout: float | None = None,
                     cancel: CancellationToken | None = None,
                     start_page: int = 1) -> bool:
    """In-process page loop shared by tests and the worker (with real clock).

    Emits JSON-able events (page_start/records/page_issue/page_done/page_timeout)
    and returns True when a page exceeded the timeout (processing stops, the
    caller may restart at the next page). No image leaves this function.
    """
    clock = clock or time.monotonic
    timeout = PDF_PAGE_TIMEOUT_SECONDS if page_timeout is None else float(page_timeout)
    next_ordinal = 0
    for page_number in range(max(1, int(start_page)), int(page_count) + 1):
        if cancel is not None and cancel.is_cancelled():
            raise CancelledError("导入已取消")
        emit({"type": "page_start", "page": page_number})
        started = clock()
        page = None
        try:
            try:
                page = get_page(page_number)
            except Exception:
                raise _PageError("corrupt_page", "页面无法打开") from None
            records = process_page(page_number, page, enable_page_ocr=enable_page_ocr,
                                   ocr_text=ocr_text, ordinal_start=next_ordinal)
            elapsed = clock() - started
            if elapsed > timeout:
                emit({"type": "page_timeout", "page": page_number})
                return True
            next_ordinal += len(records)
            if records:
                emit({"type": "records", "page": page_number,
                      "records": [_record_to_dict(record) for record in records]})
            emit({"type": "page_done", "page": page_number})
        except _PageError as error:
            elapsed = clock() - started
            if elapsed > timeout:
                emit({"type": "page_timeout", "page": page_number})
                return True
            emit({"type": "page_issue", "page": page_number, "code": error.code,
                  "detail": error.detail})
            emit({"type": "page_done", "page": page_number})
        except Exception:
            emit({"type": "page_issue", "page": page_number, "code": "parse_error",
                  "detail": "页面解析失败"})
            emit({"type": "page_done", "page": page_number})
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
    return False


def _record_to_dict(record: ImportedRecord) -> dict[str, Any]:
    return {"text": record.text, "ordinal": record.ordinal,
            "record_type": record.record_type, "sender": record.sender,
            "sent_at": record.sent_at, "locator": dict(record.locator or {}),
            "quality": dict(record.quality or {}),
            "record_id": record.record_id}


# --------------------------------------------------------------------------- worker
def _emit_json(payload: dict[str, Any], *, lock: threading.Lock | None = None) -> None:
    line = json.dumps(payload, ensure_ascii=False)
    if lock is not None:
        with lock:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    else:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def _load_pdfium():
    try:
        import pypdfium2 as pdfium
    except ImportError:
        raise HistoryImportError("缺少 PDF 渲染组件 pypdfium2，请重新安装应用。") from None
    return pdfium


def _pdfium_error_message(error: Exception) -> str:
    text = str(error).lower()
    if "password" in text or "encrypt" in text:
        return "PDF 有密码保护，请先导出无密码版本。"
    return "PDF 损坏或无法读取。"


class _PdfiumPage:
    """Close PDFium page/textpage/bitmap handles explicitly after every use."""

    def __init__(self, page: Any) -> None:
        self._page = page

    def get_size(self):
        return self._page.get_size()

    def extract_text(self) -> str:
        textpage = self._page.get_textpage()
        try:
            return textpage.get_text_bounded()
        finally:
            textpage.close()

    def render(self, scale: float):
        bitmap = self._page.render(scale=scale)
        try:
            return bitmap.to_pil().convert("RGB")
        finally:
            bitmap.close()

    def close(self) -> None:
        self._page.close()


def _ocr_text() -> Callable[[Any], str]:
    """Reuse the existing RapidOCR engine from ``vendor.ocr`` (lazy, local only)."""
    def recognize(image: Any) -> str:
        import numpy as np
        from vendor.ocr import _engine
        engine = _engine()
        result, _ = engine(np.asarray(image), use_cls=False)
        if not result:
            return ""
        rows = []
        for box, text, _score in result:
            top = min(point[1] for point in box)
            left = min(point[0] for point in box)
            normalized = str(text).strip()
            if normalized:
                rows.append((top, left, normalized))
        rows.sort()
        return "\n".join(text for _top, _left, text in rows)
    return recognize


def _worker_page_watchdog(page_holder: dict[str, int], done: threading.Event,
                          timeout: float, lock: threading.Lock) -> None:
    if done.wait(timeout):
        return
    try:
        _emit_json({"type": "page_timeout", "page": page_holder["page"]}, lock=lock)
    except Exception:
        pass
    os._exit(EXIT_PAGE_TIMEOUT)


def _parse_worker_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--path", required=True)
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--page-timeout", type=float, default=PDF_PAGE_TIMEOUT_SECONDS)
    parser.add_argument("--dpi", type=float, default=PDF_RENDER_DPI)
    parser.add_argument("--max-pixels", type=int, default=PDF_MAX_PAGE_PIXELS)
    parser.add_argument("--ocr", type=int, default=0)
    parser.add_argument("--ordinal-start", type=int, default=0)
    return parser.parse_args(argv)


def worker_main(argv: list[str] | None = None) -> int:
    """Controlled subprocess entry point: JSON-lines protocol on stdout."""
    args = _parse_worker_args(sys.argv[1:] if argv is None else argv)
    emit_lock = threading.Lock()
    try:
        path = checked_file(args.path)
    except HistoryImportError as error:
        _emit_json({"type": "error", "message": str(error)}, lock=emit_lock)
        return EXIT_WORKER_ERROR
    try:
        pdfium = _load_pdfium()
        document = pdfium.PdfDocument(str(path))
    except HistoryImportError as error:
        _emit_json({"type": "error", "message": str(error)}, lock=emit_lock)
        return EXIT_WORKER_ERROR
    except Exception as error:
        _emit_json({"type": "error", "message": _pdfium_error_message(error)},
                   lock=emit_lock)
        return EXIT_WORKER_ERROR
    enable_ocr = bool(args.ocr)
    recognize = _ocr_text() if enable_ocr else None
    next_ordinal = max(0, int(args.ordinal_start))
    try:
        page_count = len(document)
        _emit_json({"type": "meta", "page_count": page_count}, lock=emit_lock)
        if page_count <= 0:
            _emit_json({"type": "error", "message": "PDF 没有任何页面。"}, lock=emit_lock)
            return EXIT_WORKER_ERROR
        start_page = max(1, min(int(args.start_page), page_count + 1))
        for page_number in range(start_page, page_count + 1):
            _emit_json({"type": "page_start", "page": page_number}, lock=emit_lock)
            page_holder = {"page": page_number}
            done = threading.Event()
            threading.Thread(target=_worker_page_watchdog,
                             args=(page_holder, done, float(args.page_timeout), emit_lock),
                             daemon=True).start()
            page = None
            try:
                page = _PdfiumPage(document[page_number - 1])
                records = process_page(
                    page_number, page, enable_page_ocr=enable_ocr,
                    ocr_text=recognize, ordinal_start=next_ordinal,
                    dpi=float(args.dpi), max_pixels=int(args.max_pixels))
                next_ordinal += len(records)
                if records:
                    _emit_json({"type": "records", "page": page_number,
                                "records": [_record_to_dict(record) for record in records]},
                               lock=emit_lock)
            except _PageError as error:
                _emit_json({"type": "page_issue", "page": page_number,
                            "code": error.code, "detail": error.detail},
                           lock=emit_lock)
            except Exception:
                _emit_json({"type": "page_issue", "page": page_number,
                            "code": "parse_error", "detail": "页面解析失败"},
                           lock=emit_lock)
            finally:
                done.set()
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
            _emit_json({"type": "page_done", "page": page_number}, lock=emit_lock)
    finally:
        try:
            document.close()
        except Exception:
            pass
    return 0


# --------------------------------------------------------------------------- parent
def _default_worker_command() -> list[str]:
    if getattr(sys, "frozen", False):  # pragma: no cover - packaging is T11
        # Packaged builds must dispatch this flag in main.py (documented T11 gap).
        return [sys.executable, "--lite-pdf-worker"]
    return [sys.executable, "-m", "liteapp.importers.pdf", "--worker"]


def _worker_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONIOENCODING"] = "utf-8"
    root = str(Path(__file__).resolve().parents[2])
    environment["PYTHONPATH"] = root + os.pathsep + environment.get("PYTHONPATH", "")
    return environment


def _read_stream(stream, sink: "queue.Queue[Any]") -> None:
    try:
        for line in stream:
            sink.put(line.rstrip("\r\n"))
    finally:
        sink.put(_WORKER_EOF)


_WORKER_EOF = object()


class PDFParser(Parser):
    kind = "pdf"
    parser_name = "pdf"
    support_level = "exact"

    def __init__(self, *, clock: Callable[[], float] | None = None,
                 page_timeout: float | None = None,
                 worker_command: list[str] | None = None,
                 python_executable: str | None = None) -> None:
        super().__init__(clock=clock)
        self.page_timeout = (PDF_PAGE_TIMEOUT_SECONDS if page_timeout is None
                             else float(page_timeout))
        self.worker_command = list(worker_command) if worker_command else None
        self.python_executable = python_executable

    @classmethod
    def probe(cls, path: Path) -> FormatMatch | None:
        return probe(path)

    def _command(self) -> list[str]:
        if self.worker_command is not None:
            return list(self.worker_command)
        if self.python_executable:
            return [self.python_executable, "-m", "liteapp.importers.pdf", "--worker"]
        return _default_worker_command()

    def _spawn(self, path: Path, start_page: int,
               ordinal_start: int = 0) -> tuple[subprocess.Popen, Any, list[str]]:
        command = [*self._command(),
                   "--path", str(path),
                   "--start-page", str(start_page),
                   "--ordinal-start", str(ordinal_start),
                   "--page-timeout", str(self.page_timeout),
                   "--dpi", str(PDF_RENDER_DPI),
                   "--max-pixels", str(PDF_MAX_PAGE_PIXELS),
                   "--ocr", "1" if self.enable_page_ocr else "0"]
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW
        try:
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
                env=_worker_environment(), cwd=str(Path(__file__).resolve().parents[2]),
                creationflags=creationflags)
        except OSError as error:
            raise HistoryImportError(f"无法启动 PDF 解析子进程：{type(error).__name__}") from None
        lines: "queue.Queue[Any]" = queue.Queue()
        stderr_tail: list[str] = []
        threading.Thread(target=_read_stream, args=(process.stdout, lines),
                         daemon=True).start()
        threading.Thread(target=_drain_stderr, args=(process.stderr, stderr_tail),
                         daemon=True).start()
        return process, lines, stderr_tail

    @staticmethod
    def _stop_process(process: subprocess.Popen) -> None:
        if process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=3)
            except Exception:
                try:
                    process.kill()
                    process.wait(timeout=3)
                except Exception:
                    pass
        for stream in (process.stdout, process.stderr):
            try:
                stream.close()
            except (AttributeError, OSError, ValueError):
                pass

    def parse(self, path: Path, cancel: CancellationToken) -> Iterator[ImportedRecord]:
        cancel = cancel if isinstance(cancel, CancellationToken) else CancellationToken()
        path = checked_file(path)
        try:
            with path.open("rb") as handle:
                head = handle.read(8)
        except OSError:
            raise HistoryImportError("文件无法读取。") from None
        if not head.lstrip().startswith(b"%PDF-"):
            raise HistoryImportError("PDF 损坏或无法识别（缺少 %PDF- 文件头）。")
        if cancel.is_cancelled():
            raise CancelledError("导入已取消")

        start_page = 1
        restarts = 0
        next_ordinal = 0
        while True:
            process, lines, stderr_tail = self._spawn(path, start_page, next_ordinal)
            timed_out_page: int | None = None
            current_page = start_page - 1
            worker_error: str | None = None
            try:
                while True:
                    if cancel.is_cancelled():
                        raise CancelledError("导入已取消")
                    try:
                        item = lines.get(timeout=WORKER_POLL_SECONDS)
                    except queue.Empty:
                        if process.poll() is not None and lines.empty():
                            break
                        continue
                    if item is _WORKER_EOF:
                        break
                    try:
                        event = json.loads(item)
                    except (TypeError, ValueError):
                        continue
                    event_type = event.get("type")
                    if event_type == "meta":
                        try:
                            page_count = int(event.get("page_count", 0))
                        except (TypeError, ValueError):
                            raise HistoryImportError("PDF 子进程返回了无效页数。") from None
                        if page_count <= 0:
                            raise HistoryImportError("PDF 没有任何页面。")
                        self.page_count = page_count
                    elif event_type == "page_start":
                        current_page = int(event.get("page", current_page))
                        self.pages_done = max(self.pages_done, current_page - 1)
                    elif event_type == "records":
                        batch = event.get("records") or []
                        for raw in batch:
                            yield self._record_from_worker(raw)
                        next_ordinal += len(batch)
                    elif event_type == "page_issue":
                        try:
                            code = str(event.get("code") or "page_error")
                            detail = str(event.get("detail") or "")
                            self.add_issue(int(event.get("page", current_page)), code, detail)
                        except (TypeError, ValueError):
                            continue
                    elif event_type == "page_timeout":
                        timed_out_page = int(event.get("page", current_page))
                    elif event_type == "page_done":
                        self.pages_done = max(self.pages_done, int(event.get("page", 0)))
                    elif event_type == "error":
                        worker_error = str(event.get("message") or "PDF 解析失败。")
            finally:
                self._stop_process(process)
            return_code = process.poll()
            if worker_error is not None:
                raise HistoryImportError(worker_error)
            if self.page_count <= 0:
                raise HistoryImportError("PDF 子进程没有返回页数。")
            if timed_out_page is not None:
                self.add_issue(timed_out_page, "timeout",
                               f"单页解析/OCR 超过 {self.page_timeout:g} 秒上限")
                next_start = timed_out_page + 1
            elif return_code not in (0, None):
                failed_page = current_page if current_page >= start_page else start_page
                self.add_issue(failed_page, "worker_error", "PDF 解析子进程异常退出")
                next_start = failed_page + 1
            elif self.pages_done < self.page_count and current_page < self.page_count:
                # Worker exited cleanly before all pages; retry the next page once.
                failed_page = max(current_page, start_page)
                self.add_issue(failed_page, "worker_error", "PDF 子进程提前退出")
                next_start = failed_page + 1
            else:
                break
            if next_start > self.page_count:
                break
            start_page = next_start
            restarts += 1
            if restarts > MAX_WORKER_RESTARTS:
                raise HistoryImportError("PDF 页级重试次数超过上限，已停止。")
        if self.pages_done <= 0 and self.page_count > 0:
            self.pages_done = self.page_count

    @staticmethod
    def _record_from_worker(raw: Any) -> ImportedRecord:
        if not isinstance(raw, dict):
            raise HistoryImportError("PDF 子进程返回了无效记录。")
        try:
            return ImportedRecord(
                text=str(raw["text"]), ordinal=int(raw["ordinal"]),
                record_type=str(raw.get("record_type") or "fragment"),
                sender=raw.get("sender"), sent_at=raw.get("sent_at"),
                locator=raw.get("locator"), quality=raw.get("quality"),
                record_id=raw.get("record_id"))
        except (KeyError, TypeError, ValueError) as error:
            raise HistoryImportError("PDF 子进程返回了无效记录。") from error


def _drain_stderr(stream, tail: list[str]) -> None:
    try:
        for line in stream:
            tail.append(line.rstrip("\r\n"))
            del tail[:-WORKER_STDERR_TAIL_LINES]
    except Exception:
        pass


if __name__ == "__main__":  # pragma: no cover - subprocess entry point
    raise SystemExit(worker_main())
