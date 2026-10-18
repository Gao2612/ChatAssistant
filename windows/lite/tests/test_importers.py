"""T6 验收用例：HTML/PDF 解析、源位置与导入任务。

覆盖 PLAN T6：
- HTML：脚本/远程资源不执行不请求；UTF-8/UTF-16/GB18030 保留文本；相同连续正文保留；
  同文件重导不增加记录；30 秒解析上限（可注入时钟测边界）；100 MB 文件上限。
- PDF：pypdfium2 受控子进程渲染 + 复用现有 OCR；页码/顺序准确；混合扫描页只对无文字页
  OCR；取消不提交半条记录；损坏/密码页页级问题；150 DPI/每页 1200 万像素/单页 30 秒
  上限集中定义并测边界；原图不进入记录/日志。
- ExportWeChat：只接受显式签名特征；无真实样本时标记为通用回退，不得声称“精确支持”。
- ImportJobs：start/progress/cancel、失败不落半条、10,000 条合成记录首/中/尾引用可跳回原文。

全部使用合成数据与临时目录；不接触真实微信/聊天，不保存真实样本。
"""

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parents[1]))

from liteapp.contracts import CancellationToken, CancelledError, ScopeKey
from liteapp.history import HistoryImportError, HistoryStore
from liteapp.history_models import ImportedRecord, ImportSource, PageIssue
from liteapp.import_jobs import ImportJobError, ImportJobs, ImportProgress
from liteapp.importers import create_parser, probe, select_parser, supported_extensions
from liteapp.importers import base
from liteapp.importers.base import (
    HTML_PARSE_TIMEOUT_SECONDS,
    MAX_IMPORT_FILE_BYTES,
    PDF_MAX_PAGE_PIXELS,
    PDF_PAGE_TIMEOUT_SECONDS,
    PDF_RENDER_DPI,
    FormatMatch,
    Parser,
)
from liteapp.importers.exportwechat_html import (
    ExportWeChatHTMLParser,
    SAMPLE_VERIFIED,
    SIGNATURE_MARKERS,
)
from liteapp.importers import exportwechat_html
from liteapp.importers.exportwechat_json import ExportWeChatJSONParser
from liteapp.importers.html_generic import HtmlGenericParser
from liteapp.importers import pdf as pdf_module
from liteapp.importers.pdf import (
    PDFParser,
    process_document,
    records_from_lines,
    render_scale,
)

FIXTURES = Path(__file__).parent / "fixtures" / "import_samples"
GENERIC_HTML = FIXTURES / "generic_chat.html"
UTF16_HTML = FIXTURES / "utf16_chat.html"
GB18030_HTML = FIXTURES / "gb18030_chat.html"
EXPORTWECHAT_HTML = FIXTURES / "exportwechat_synthetic.html"
LARGE_HTML = FIXTURES / "large_10000.html"
FAKE_PDF_WORKER = FIXTURES / "fake_pdf_worker.py"


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def make_text_pdf(path, page_texts):
    """Write a minimal valid PDF where each page has one Latin-1 text line."""
    path = Path(path)
    objects = []

    def add(body):
        objects.append(body)
        return len(objects)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    contents_ids = []
    for text in page_texts:
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
        contents_ids.append(add(b"<< /Length %d >>\nstream\n" % len(stream)
                                + stream + b"\nendstream"))
    pages_id = add(b"PLACEHOLDER")
    page_ids = []
    for content_id in contents_ids:
        page_ids.append(add((
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("latin-1")))
    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{pid} 0 R' for pid in page_ids)}] "
        f"/Count {len(page_ids)} >>"
    ).encode("latin-1")
    catalog_id = add(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode("latin-1"))
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = [0]
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objects) + 1)
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += b"%010d 00000 n \n" % offset
    out += (b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
            % (len(objects) + 1, catalog_id, xref))
    path.write_bytes(bytes(out))
    return path


class FakeClock:
    """Deterministic monotonic clock for timeout boundary tests."""

    def __init__(self, values, default_step=0.0):
        self._values = list(values)
        self._last = self._values[-1] if self._values else 0.0
        self._default_step = default_step

    def __call__(self):
        if self._values:
            self._last = self._values.pop(0)
            return self._last
        self._last += self._default_step
        return self._last


class FakePage:
    def __init__(self, text=None, size=(612.0, 792.0)):
        self.text = text
        self.size = size
        self.render_scales = []
        self.closed = False

    def get_size(self):
        return self.size

    def extract_text(self):
        return self.text or ""

    def render(self, scale):
        self.render_scales.append(scale)
        return f"fake-image-{id(self)}"

    def close(self):
        self.closed = True


class FakeDocument:
    def __init__(self, pages):
        self.pages = pages

    def get_page(self, page_number):
        return self.pages[page_number - 1]


class ImportTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = HistoryStore(self.folder / "memory.sqlite3",
                                  protect=xor_protect, unprotect=xor_unprotect)
        self.scope = self.store.create_person("示例客户", "account-t6")

    def jobs(self, **kwargs):
        return ImportJobs(self.store, **kwargs)

    def fixture(self, name):
        path = FIXTURES / name
        self.assertTrue(path.is_file(), f"缺少合成夹具 {name}")
        return path


class LimitsAndRegistryTests(ImportTestCase):
    def test_limits_are_defined_in_one_place(self):
        self.assertEqual(MAX_IMPORT_FILE_BYTES, 100 * 1024 * 1024)
        self.assertEqual(HTML_PARSE_TIMEOUT_SECONDS, 30.0)
        self.assertEqual(PDF_RENDER_DPI, 150)
        self.assertEqual(PDF_MAX_PAGE_PIXELS, 12_000_000)
        self.assertEqual(PDF_PAGE_TIMEOUT_SECONDS, 30.0)

    def test_probe_detects_html_and_pdf_and_rejects_unknown(self):
        html_match = probe(self.fixture("generic_chat.html"))
        self.assertIsInstance(html_match, FormatMatch)
        self.assertEqual(html_match.kind, "html")
        self.assertEqual(html_match.support_level, "generic")
        self.assertFalse(html_match.exact_support)
        pdf = self.folder / "synthetic.pdf"
        make_text_pdf(pdf, ["synthetic page"])
        pdf_match = probe(pdf)
        self.assertEqual(pdf_match.kind, "pdf")
        self.assertEqual(pdf_match.parser_name, "pdf")
        unknown = self.folder / "synthetic.txt"
        unknown.write_text("hello", encoding="utf-8")
        self.assertIsNone(probe(unknown))
        with self.assertRaises(HistoryImportError):
            select_parser(unknown)
        with self.assertRaises(HistoryImportError):
            probe(self.folder / "missing.html")
        self.assertIn(".html", supported_extensions())
        self.assertIn(".pdf", supported_extensions())

    def test_oversized_files_are_rejected_before_reading(self):
        oversized = self.folder / "oversized.html"
        with oversized.open("wb") as handle:
            handle.seek(MAX_IMPORT_FILE_BYTES + 1024)
            handle.write(b"x")
        with self.assertRaises(HistoryImportError):
            probe(oversized)
        with self.assertRaises(HistoryImportError):
            list(HtmlGenericParser().parse(oversized, CancellationToken()))
        oversized_pdf = self.folder / "oversized.pdf"
        with oversized_pdf.open("wb") as handle:
            handle.seek(MAX_IMPORT_FILE_BYTES + 1024)
            handle.write(b"x")
        with self.assertRaises(HistoryImportError):
            list(PDFParser().parse(oversized_pdf, CancellationToken()))

    def test_render_scale_caps_page_pixels(self):
        scale, downscaled = render_scale(1000.0, 1000.0)
        self.assertAlmostEqual(scale, PDF_RENDER_DPI / 72.0)
        self.assertFalse(downscaled)
        # 恰好 1200 万像素：不允许缩小；多 1 像素即必须缩小。
        side = (PDF_MAX_PAGE_PIXELS / (PDF_RENDER_DPI / 72.0) ** 2) ** 0.5
        exact_scale, exact_downscaled = render_scale(side, side)
        self.assertFalse(exact_downscaled)
        over_scale, over_downscaled = render_scale(side + 1.0, side + 1.0)
        self.assertTrue(over_downscaled)
        self.assertLessEqual(over_scale * side * over_scale * side,
                             PDF_MAX_PAGE_PIXELS + 1e-6)
        self.assertLess(over_scale, exact_scale)

    def test_records_from_lines_keep_order_and_offer_page_locators(self):
        records = records_from_lines(3, ["第一行", "第二行", "第一行"],
                                     quality={"source": "text"})
        self.assertEqual([record.text for record in records], ["第一行", "第二行", "第一行"],
                         "相同连续正文必须保留")
        self.assertEqual([record.ordinal for record in records], [0, 1, 2])
        self.assertEqual([record.record_type for record in records], ["fragment"] * 3)
        self.assertEqual([record.locator["page"] for record in records], [3, 3, 3])
        self.assertEqual([record.locator["line"] for record in records], [1, 2, 3])


class HtmlGenericParserTests(ImportTestCase):
    def test_utf8_utf16_gb18030_text_is_preserved(self):
        expectations = (
            (self.fixture("generic_chat.html"), "请问设备还有现货吗"),
            (self.fixture("utf16_chat.html"), "合成 UTF-16 编码消息：预算 3000 元"),
            (self.fixture("gb18030_chat.html"), "合成 GB18030 编码消息：交期周五"),
        )
        for path, expected in expectations:
            with self.subTest(path=path.name):
                job = self.jobs().run_sync(self.scope, path, False)
                self.assertEqual(job.status, "succeeded", job.error)
                texts = "\n".join(row["text"] for row in
                                  self.store.list_messages(self.scope, limit=500))
                self.assertIn(expected, texts)

    def test_script_and_remote_resources_are_not_executed_or_requested(self):
        calls = []

        def forbidden(*args, **kwargs):
            calls.append(args)
            raise AssertionError("导入器不得发起任何网络连接")

        original_socket = socket.socket
        original_create = socket.create_connection
        socket.socket = forbidden
        socket.create_connection = forbidden
        try:
            job = self.jobs().run_sync(self.scope, GENERIC_HTML, False)
        finally:
            socket.socket = original_socket
            socket.create_connection = original_create
        self.assertEqual(job.status, "succeeded", job.error)
        self.assertEqual(calls, [])
        texts = "\n".join(row["text"] for row in self.store.list_messages(self.scope, limit=500))
        self.assertIn("请问设备还有现货吗", texts)
        self.assertNotIn("fetch(", texts)
        self.assertNotIn("evil.invalid", texts)
        self.assertNotIn("synthetic.js", texts)

    def test_consecutive_identical_text_is_preserved(self):
        self.jobs().run_sync(self.scope, GENERIC_HTML, False)
        rows = self.store.list_messages(self.scope, limit=500)
        texts = [row["text"] for row in rows]
        duplicate_index = texts.index("合成重复正文保留")
        self.assertEqual(texts[duplicate_index + 1], "合成重复正文保留")

    def test_html_parse_timeout_boundary(self):
        # 正好到上限不算超时；超过 30 秒立即失败（可注入时钟，测试不真的等 30 秒）。
        allowed = HtmlGenericParser(clock=FakeClock([100.0, 100.0 + HTML_PARSE_TIMEOUT_SECONDS]))
        list(allowed.parse(GENERIC_HTML, CancellationToken()))
        exceeded = HtmlGenericParser(
            clock=FakeClock([200.0, 200.0 + HTML_PARSE_TIMEOUT_SECONDS + 0.001]))
        with self.assertRaises(HistoryImportError) as raised:
            list(exceeded.parse(GENERIC_HTML, CancellationToken()))
        self.assertIn("超时", str(raised.exception))

    def test_cancelled_html_parse_raises_and_records_nothing(self):
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(CancelledError):
            list(HtmlGenericParser().parse(GENERIC_HTML, token))
        self.assertEqual(self.store.stats(self.scope).record_count, 0)


class ExportWeChatTests(ImportTestCase):
    def test_exportwechat_exact_only_with_companion_json_and_fallback_without(self):
        # 对照一：HTML-only（无伴生 *.html.json）——真实样本已证明 HTML 只是渲染壳，
        # DOM 尚未校准，所以必须继续通用回退、不得声称精确支持。
        match = probe(EXPORTWECHAT_HTML)
        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "exportwechat_html")
        self.assertFalse(match.exact_support, "HTML-only 没有真实样本校准，不得标记精确支持")
        self.assertNotEqual(match.support_level, "exact")
        self.assertEqual(match.parser_name, "html_generic", "应继续通用安全回退")
        self.assertIn("样本", " ".join(match.notes))
        # SAMPLE_VERIFIED 只对伴生 JSON 通道为 True（HTML-only 仍未校准）。
        self.assertTrue(SAMPLE_VERIFIED)
        self.assertEqual(exportwechat_html.SAMPLE_VERIFIED, True)

        # 对照二：同一 HTML 一旦有伴生 JSON，就切到已校准的精确解析器。
        with tempfile.TemporaryDirectory() as folder:
            html_path = Path(folder) / "pair.html"
            html_path.write_bytes(EXPORTWECHAT_HTML.read_bytes())
            (Path(folder) / "pair.html.json").write_text(json.dumps([{
                "StrTime": "2026-01-02 10:00:00", "display_name": "合成联系人",
                "is_send": False, "sender": "wxid_peer_synthetic",
                "text": "合成伴生 JSON 正文", "timestamp": 1767386400,
                "type": "1", "xml_dict": {}}], ensure_ascii=False), encoding="utf-8")
            exact = probe(html_path)
            self.assertIs(exact.parser, ExportWeChatJSONParser)
            self.assertTrue(exact.exact_support)
            self.assertEqual(exact.parser_name, "exportwechat_json")
            self.assertEqual(exact.support_level, "exact")

        # 显式签名字段可被识别（用于锁定真实格式），但 HTML-only 解析器仍是通用回退。
        self.assertTrue(any(marker.lower() in EXPORTWECHAT_HTML.read_bytes().lower()
                            for marker in SIGNATURE_MARKERS))
        self.assertTrue(issubclass(ExportWeChatHTMLParser, HtmlGenericParser))

    def test_exportwechat_synthetic_file_imports_via_generic_fallback(self):
        job = self.jobs().run_sync(self.scope, EXPORTWECHAT_HTML, False)
        self.assertEqual(job.status, "succeeded", job.error)
        self.assertEqual(job.format_kind, "exportwechat_html")
        self.assertNotEqual(job.support_level, "exact")
        texts = "\n".join(row["text"] for row in self.store.list_messages(self.scope, limit=100))
        self.assertIn("合成 ExportWeChat 签名消息一", texts)

    def test_exportwechat_specific_parser_is_not_enabled_without_sample(self):
        match = select_parser(EXPORTWECHAT_HTML)
        parser = create_parser(match)
        self.assertIsInstance(parser, HtmlGenericParser)
        self.assertNotIsInstance(parser, ExportWeChatHTMLParser)
        # HTML DOM 仍未校准：即使 SAMPLE_VERIFIED 为 True 也不能冒充精确解析。
        with self.assertRaises(HistoryImportError):
            list(ExportWeChatHTMLParser().parse(EXPORTWECHAT_HTML, CancellationToken()))

    def test_plain_html_is_not_marked_as_exportwechat(self):
        match = probe(GENERIC_HTML)
        self.assertEqual(match.kind, "html")


class PdfProcessingTests(ImportTestCase):
    def collect(self, document, **kwargs):
        events = []
        kwargs.setdefault("clock", time.monotonic)
        timed_out = process_document(
            page_count=len(document.pages),
            get_page=document.get_page,
            emit=events.append,
            **kwargs)
        return events, timed_out

    def test_page_order_text_and_locators(self):
        document = FakeDocument([FakePage("第一页正文"), FakePage("第二页正文")])
        events, timed_out = self.collect(document)
        self.assertFalse(timed_out)
        records = [record for event in events if event["type"] == "records"
                   for record in event["records"]]
        self.assertEqual([record["text"] for record in records], ["第一页正文", "第二页正文"])
        self.assertEqual([record["locator"]["page"] for record in records], [1, 2])
        self.assertEqual([record["quality"]["source"] for record in records], ["text", "text"])
        self.assertTrue(all(page.closed for page in document.pages))

    def test_mixed_pages_only_ocr_pages_without_text(self):
        text_page = FakePage("有文字层的第一页")
        scanned_page = FakePage(None)
        document = FakeDocument([text_page, scanned_page])
        ocr_calls = []

        def fake_ocr(image):
            ocr_calls.append(image)
            return "合成 OCR 识别文字"

        events, _timed_out = self.collect(document, enable_page_ocr=True,
                                          ocr_text=fake_ocr)
        self.assertEqual(len(ocr_calls), 1, "只允许对无文字页做 OCR")
        self.assertEqual(ocr_calls, [f"fake-image-{id(scanned_page)}"])
        self.assertEqual(text_page.render_scales, [])
        self.assertEqual(len(scanned_page.render_scales), 1)
        records = [record for event in events if event["type"] == "records"
                   for record in event["records"]]
        self.assertEqual([record["text"] for record in records],
                         ["有文字层的第一页", "合成 OCR 识别文字"])
        self.assertEqual([record["quality"]["source"] for record in records], ["text", "ocr"])

    def test_ocr_render_respects_pixel_cap_and_marks_quality(self):
        huge_page = FakePage(None, size=(5000.0, 5000.0))
        document = FakeDocument([huge_page])
        events, _ = self.collect(document, enable_page_ocr=True,
                                 ocr_text=lambda image: "合成 OCR 文字")
        records = [record for event in events if event["type"] == "records"
                   for record in event["records"]]
        scale = huge_page.render_scales[0]
        self.assertLessEqual((5000.0 * scale) ** 2, PDF_MAX_PAGE_PIXELS + 1e-6)
        self.assertLess(scale, PDF_RENDER_DPI / 72.0)
        self.assertTrue(records[0]["quality"]["downscaled"])
        self.assertAlmostEqual(records[0]["quality"]["render_dpi"],
                               round(scale * 72.0, 1))

    def test_page_timeout_boundary_stops_after_timed_out_page(self):
        pages = [FakePage("第一页正文"), FakePage("第二页正文"), FakePage("第三页正文")]
        document = FakeDocument(pages)
        clock = FakeClock([0.0, 5.0,
                           10.0, 10.0 + PDF_PAGE_TIMEOUT_SECONDS,
                           20.0, 20.0 + PDF_PAGE_TIMEOUT_SECONDS + 0.5])
        events, timed_out = self.collect(document, clock=clock)
        self.assertTrue(timed_out)
        record_pages = [record["locator"]["page"] for event in events
                        if event["type"] == "records" for record in event["records"]]
        self.assertEqual(record_pages, [1, 2], "第 1 页与恰好 30 秒的第 2 页应提交")
        issue_pages = [event["page"] for event in events if event["type"] == "page_timeout"]
        self.assertEqual(issue_pages, [3])
        self.assertEqual(pages[2].render_scales, [])

    def test_corrupt_page_gives_page_issue_and_other_pages_continue(self):
        pages = [FakePage("第一页正文"), object(), FakePage("第三页正文")]

        def get_page(page_number):
            page = pages[page_number - 1]
            if not isinstance(page, FakePage):
                raise ValueError("synthetic corrupt page")
            return page

        events = []
        process_document(page_count=3, get_page=get_page, emit=events.append)
        issues = [(event["page"], event["code"]) for event in events
                  if event["type"] == "page_issue"]
        self.assertEqual(issues, [(2, "corrupt_page")])
        record_pages = [record["locator"]["page"] for event in events
                        if event["type"] == "records" for record in event["records"]]
        self.assertEqual(record_pages, [1, 3])

    def test_blank_page_without_ocr_is_page_issue_not_record(self):
        document = FakeDocument([FakePage(None)])
        events, _ = self.collect(document, enable_page_ocr=False)
        issues = [(event["page"], event["code"]) for event in events
                  if event["type"] == "page_issue"]
        self.assertEqual(issues, [(1, "no_text")])
        self.assertFalse(any(event["type"] == "records" for event in events))

    def test_ocr_returning_nothing_is_page_issue(self):
        document = FakeDocument([FakePage(None)])
        events, _ = self.collect(document, enable_page_ocr=True,
                                 ocr_text=lambda image: "")
        issues = [(event["page"], event["code"]) for event in events
                  if event["type"] == "page_issue"]
        self.assertEqual(issues, [(1, "ocr_empty")])

    def test_cancel_stops_between_pages_and_raises(self):
        document = FakeDocument([FakePage("第一页"), FakePage("第二页")])
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(CancelledError):
            process_document(page_count=2, get_page=document.get_page,
                             emit=lambda event: None, cancel=token)

    def test_diagnostics_never_contain_image_bytes(self):
        document = FakeDocument([FakePage(None, size=(5000.0, 5000.0))])
        events, _ = self.collect(document, enable_page_ocr=True,
                                 ocr_text=lambda image: "合成 OCR 文字")
        payload = json.dumps(events, ensure_ascii=False)
        self.assertNotIn("fake-image", payload)
        self.assertNotIn("base64", payload)
        self.assertNotIn("PNG", payload)
        self.assertNotIn("image/png", payload)


class PdfSubprocessTests(ImportTestCase):
    def test_real_text_pdf_page_order_through_controlled_subprocess(self):
        path = self.folder / "三页合成.pdf"
        make_text_pdf(path, ["Synthetic page one 2025-01-02",
                             "Synthetic page two", "Synthetic page three"])
        parser = PDFParser()
        records = list(parser.parse(path, CancellationToken()))
        self.assertEqual(len(parser.page_issues), 0, parser.page_issues)
        self.assertEqual(parser.page_count, 3)
        self.assertEqual(parser.pages_done, 3)
        self.assertEqual([record.locator["page"] for record in records], [1, 2, 3])
        self.assertEqual([record.text for record in records],
                         ["Synthetic page one 2025-01-02", "Synthetic page two",
                          "Synthetic page three"])

    def test_corrupt_pdf_is_reported(self):
        corrupt = self.folder / "corrupt.pdf"
        corrupt.write_bytes(b"%PDF-1.4\nthis is not a real pdf")
        with self.assertRaises(HistoryImportError):
            list(PDFParser().parse(corrupt, CancellationToken()))

    def test_password_protected_pdf_is_reported(self):
        try:
            from pypdf import PdfWriter
        except ImportError:
            self.skipTest("pypdf missing")
        encrypted = self.folder / "encrypted.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=200, height=200)
        writer.encrypt("password")
        with encrypted.open("wb") as handle:
            writer.write(handle)
        with self.assertRaises(HistoryImportError) as raised:
            list(PDFParser().parse(encrypted, CancellationToken()))
        self.assertRegex(str(raised.exception), "密码|加密|password")

    def test_page_timeout_restarts_worker_and_records_page_issue(self):
        fake_pdf = self.folder / "fake.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4\n% synthetic fake worker test file\n")
        parser = PDFParser(worker_command=[sys.executable, str(FAKE_PDF_WORKER)])
        records = list(parser.parse(fake_pdf, CancellationToken()))
        self.assertEqual([record.locator["page"] for record in records], [1, 3])
        self.assertEqual([(issue.page, issue.code) for issue in parser.page_issues],
                         [(2, "timeout")])
        self.assertEqual(parser.page_count, 3)
        self.assertEqual(parser.pages_done, 3)

    def test_cancel_before_pdf_parse_raises_and_starts_no_worker(self):
        path = self.folder / "cancel.pdf"
        make_text_pdf(path, ["synthetic"])
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(CancelledError):
            list(PDFParser().parse(path, token))


class ImportJobsTests(ImportTestCase):
    def test_start_progress_success_and_duplicate(self):
        jobs = self.jobs()
        job_id = jobs.start(self.scope, GENERIC_HTML, False)
        self.assertIsInstance(job_id, str)
        progress = jobs.wait(job_id, timeout=30)
        self.assertIsInstance(progress, ImportProgress)
        self.assertEqual(progress.status, "succeeded", progress.error)
        self.assertEqual(progress.format_kind, "html")
        self.assertEqual(progress.support_level, "generic")
        self.assertFalse(progress.duplicate)
        self.assertGreater(progress.record_count, 0)
        self.assertGreater(progress.char_count, 0)
        self.assertEqual(progress.source_version, 1)
        self.assertEqual(progress.memory_version, 1)
        self.assertEqual(progress.phase, "done")
        self.assertIsNotNone(progress.source_id)
        self.assertEqual(progress.path_name, GENERIC_HTML.name)
        self.assertEqual(progress, jobs.progress(job_id))
        stats = self.store.stats(self.scope)
        self.assertEqual(stats.record_count, progress.record_count)
        # 同文件重导：不增加记录、不提高版本。
        duplicate_id = jobs.start(self.scope, GENERIC_HTML, False)
        duplicate = jobs.wait(duplicate_id, timeout=30)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(self.store.stats(self.scope), stats)
        self.assertEqual(duplicate.source_version, progress.source_version)

    def test_progress_requires_known_job(self):
        jobs = self.jobs()
        with self.assertRaises(ImportJobError):
            jobs.progress("missing-job")
        with self.assertRaises(ImportJobError):
            jobs.cancel("missing-job")

    def test_start_requires_scope_key(self):
        with self.assertRaises(TypeError):
            self.jobs().start(self.scope.person_id, GENERIC_HTML, False)

    def test_failed_job_reports_error_and_commits_nothing(self):
        jobs = self.jobs()
        bad = self.folder / "synthetic.txt"
        bad.write_text("不是聊天记录", encoding="utf-8")
        job = jobs.wait(jobs.start(self.scope, bad, False), timeout=30)
        self.assertEqual(job.status, "failed")
        self.assertIsNotNone(job.error)
        self.assertEqual(self.store.stats(self.scope).record_count, 0)
        missing = jobs.wait(jobs.start(self.scope, self.folder / "missing.html", False),
                            timeout=30)
        self.assertEqual(missing.status, "failed")

    def test_cancel_never_commits_partial_records(self):
        class SlowParser(Parser):
            kind = "html"
            parser_name = "html_generic"

            def parse(self, path, cancel):
                self.page_count = 1
                for index in range(60):
                    if cancel.is_cancelled():
                        raise CancelledError("cancelled")
                    time.sleep(0.02)
                    yield ImportedRecord(text=f"慢速合成记录 {index}", ordinal=index)
                self.pages_done = 1

        match = FormatMatch(kind="html", parser_name="html_generic",
                            parser=SlowParser, support_level="generic",
                            confidence="suffix")
        jobs = ImportJobs(self.store, probe=lambda path: match)
        job_id = jobs.start(self.scope, GENERIC_HTML, False)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if jobs.progress(job_id).record_count >= 1:
                break
            time.sleep(0.01)
        jobs.cancel(job_id)
        progress = jobs.wait(job_id, timeout=30)
        self.assertEqual(progress.status, "cancelled")
        self.assertEqual(self.store.stats(self.scope).record_count, 0,
                         "取消不得提交半条记录")
        self.assertEqual(self.store.stats(self.scope).source_version, 0)

    def test_ten_thousand_records_first_mid_tail_evidence_and_duration(self):
        jobs = self.jobs()
        started = time.perf_counter()
        progress = jobs.run_sync(self.scope, self.fixture("large_10000.html"), False)
        elapsed = time.perf_counter() - started
        print(f"\n[T6] 10,000 条合成记录导入耗时：{elapsed:.3f}s，"
              f"记录数={progress.record_count}，字符数={progress.char_count}")
        self.assertEqual(progress.status, "succeeded", progress.error)
        self.assertEqual(progress.record_count, 10_000)
        stats = self.store.stats(self.scope)
        self.assertEqual(stats.record_count, 10_000)
        snapshot = self.store.snapshot(self.scope)
        self.assertEqual(len(snapshot.ranges), 10_000)
        for index, expected in ((0, "合成消息 00000"),
                                (5000, "合成消息 05000"),
                                (9999, "合成消息 09999")):
            ref = snapshot.ranges[index]
            evidence = self.store.read_evidence(ref)
            self.assertIn(expected, evidence.text)
            self.assertEqual(evidence.ref, ref)
            self.assertEqual(ref.start, 0)
            self.assertEqual(ref.end, len(evidence.text))

    def test_async_job_progress_phase_is_observable(self):
        jobs = self.jobs()
        job_id = jobs.start(self.scope, self.fixture("large_10000.html"), False)
        phases = set()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            progress = jobs.progress(job_id)
            phases.add(progress.phase)
            if progress.status in ("succeeded", "failed", "cancelled"):
                break
            time.sleep(0.005)
        self.assertIn("parse", phases)
        self.assertEqual(jobs.progress(job_id).status, "succeeded")


class PageRetryTests(ImportTestCase):
    """R-04: 同一来源的超时/失败页可以重试；已成功页不重复、不丢记录。"""

    @staticmethod
    def source(page_issues=(), digest="c" * 64, source_id=None):
        return ImportSource(digest=digest, kind="pdf", name="合成扫描件.pdf",
                            page_count=3, page_issues=tuple(page_issues),
                            source_id=source_id)

    @staticmethod
    def page_record(page, text, ordinal=0):
        return ImportedRecord(text=text, ordinal=ordinal, record_type="fragment",
                              locator={"kind": "pdf", "page": page, "line": 1})

    def test_retry_merges_a_recovered_page_without_duplicating_successful_pages(self):
        scope = self.store.create_person("重试客户", "account-retry")
        first = self.store.import_records(
            scope, self.source((PageIssue(page=2, code="timeout"),)),
            [self.page_record(1, "第一页正文", 0), self.page_record(3, "第三页正文", 1)])
        retried = self.store.import_records(
            scope, self.source(source_id=first.source_id),
            [self.page_record(1, "第一页正文", 0), self.page_record(2, "第二页正文", 1),
             self.page_record(3, "第三页正文", 2)], retry=True)
        self.assertTrue(retried.retried)
        self.assertFalse(retried.duplicate)
        self.assertEqual((retried.source_version, retried.memory_version), (2, 2))
        rows = list(self.store.iter_records(scope, retried.source_version))
        self.assertEqual([row.text for row in rows],
                         ["第一页正文", "第二页正文", "第三页正文"])
        source = self.store.list_sources(scope)[0]
        self.assertEqual(source.import_version, 2)
        self.assertEqual(source.record_count, 3)
        self.assertEqual(source.page_issues, ())
        self.assertGreaterEqual(retried.source_version, first.source_version + 1)

    def test_retry_keeps_old_successful_pages_when_a_page_still_fails(self):
        scope = self.store.create_person("重试客户", "account-retry")
        first = self.store.import_records(
            scope, self.source((PageIssue(page=2, code="timeout"),)),
            [self.page_record(1, "第一页正文", 0), self.page_record(3, "第三页正文", 1)])
        retried = self.store.import_records(
            scope, self.source((PageIssue(page=2, code="timeout"),),
                               source_id=first.source_id),
            [self.page_record(1, "第一页正文", 0)], retry=True)
        self.assertTrue(retried.retried)
        rows = list(self.store.iter_records(scope, retried.source_version))
        self.assertEqual([row.text for row in rows], ["第一页正文", "第三页正文"])
        source = self.store.list_sources(scope)[0]
        self.assertEqual(source.import_version, 2)
        self.assertEqual([(issue.page, issue.code) for issue in source.page_issues],
                         [(2, "timeout")])

    def test_retry_rejects_source_without_retryable_page_issues(self):
        scope = self.store.create_person("重试客户", "account-retry")
        self.store.import_records(
            scope, self.source(), [self.page_record(1, "第一页正文")])
        with self.assertRaises(HistoryImportError):
            self.store.import_records(
                scope, self.source(), [self.page_record(1, "第一页正文")], retry=True)

    def test_retry_requires_existing_source_id(self):
        scope = self.store.create_person("重试客户", "account-retry")
        with self.assertRaises(HistoryImportError):
            self.store.import_records(
                scope, self.source(), [self.page_record(1, "第一页正文")], retry=True)

    def test_import_jobs_retry_path_reports_retried_source(self):
        scope = self.store.create_person("重试客户", "account-retry")
        fake_pdf = self.folder / "retry_fake.pdf"
        fake_pdf.write_bytes(b"%PDF-1.4\n% synthetic fake worker retry file\n")
        jobs = self.jobs(worker_command=[sys.executable, str(FAKE_PDF_WORKER)])
        first = jobs.wait(jobs.start(scope, fake_pdf, False), timeout=60)
        self.assertEqual(first.status, "succeeded", first.error)
        self.assertIsNotNone(first.source_id)
        self.assertEqual([(issue.page, issue.code) for issue in first.page_issues],
                         [(2, "timeout")])
        retry_job = jobs.start_retry(scope, first.source_id, fake_pdf, False)
        retried = jobs.wait(retry_job, timeout=60)
        self.assertEqual(retried.status, "succeeded", retried.error)
        self.assertTrue(retried.retried)
        rows = list(self.store.iter_records(scope, retried.source_version))
        self.assertEqual([row.locator["page"] for row in rows], [1, 3])
        self.assertEqual(self.store.list_sources(scope)[0].import_version, 2)

    def test_import_jobs_retry_rejects_unknown_source_id(self):
        scope = self.store.create_person("重试客户", "account-retry")
        jobs = self.jobs()
        with self.assertRaises(ImportJobError):
            jobs.start_retry(scope, "missing-source-id", GENERIC_HTML, False)


class HistoryUiImportIntegrationTests(ImportTestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_async_import_updates_progress_and_page(self):
        from liteapp.history_ui import HistoryPage

        page = HistoryPage(store=self.store)
        self.addCleanup(page.close)
        page.create_person("示例客户", "account-t6")
        page.start_import_path(GENERIC_HTML)
        deadline = time.monotonic() + 30
        progress = None
        while time.monotonic() < deadline:
            self.app.processEvents()
            job_id = page.active_import_job()
            if job_id is None:
                break
            progress = page.jobs.progress(job_id)
            time.sleep(0.01)
        self.assertIsNotNone(progress)
        self.assertIn("请问设备还有现货吗", page.records.toPlainText())
        self.assertIn(progress.record_count and str(progress.record_count), page.summary.text())
        self.assertTrue(page.import_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
