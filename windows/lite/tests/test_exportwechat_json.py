"""T6 扩展验收：ExportWeChat 伴生 JSON（``<name>.html.json``）解析器。

真实样本（用户导出，1.29 MB HTML + 670 KB 伴生 JSON / 1854 条）只用于校准实现，
**测试一律使用合成数据**：本文件里的每条文本都是虚构的，绝不读取用户真实文件，
也不把真实聊天写进夹具。

覆盖：
- probe 命中/未命中（伴生 JSON 缺失、顶层非数组、首元素缺签名、非 HTML 后缀）；
- 文本/引用/撤回/链接/位置/通话/红包的正文拼装；
- is_send 方向（true=“我”，false=display_name，display_name 空时用 sender wxid 兜底）；
- 图片/表情只计数不生成空记录；未知 type 有文本则取文本，否则跳过并计数；
- HTML 反转义（``&#x20;``/``&#x0A;``/``&amp;``）与空白归一；
- 空数组/坏 JSON/顶层非数组报错，坏条目容错跳过；
- 取消、条数上限、文件大小上限、超时边界；
- 注册表优先级（伴生 JSON 优先于 html_generic）与 HTML-only 仍走通用回退。
"""

import html
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))

from liteapp.contracts import CancellationToken, CancelledError
from liteapp.history_models import MAX_TEXT_CHARS, HistoryImportError
from liteapp.importers import create_parser, probe, select_parser
from liteapp.importers import base, exportwechat_html, exportwechat_json
from liteapp.importers.base import FormatMatch, Parser
from liteapp.importers.exportwechat_json import (
    PARSER_NAME,
    ExportWeChatJSONParser,
    companion_path,
)
from liteapp.importers.html_generic import HtmlGenericParser

FIXTURES = Path(__file__).parent / "fixtures" / "import_samples"
PAIR_HTML = FIXTURES / "exportwechat_pair.html"
PAIR_JSON = FIXTURES / "exportwechat_pair.html.json"
SIGNATURE_HTML = FIXTURES / "exportwechat_synthetic.html"
GENERIC_HTML = FIXTURES / "generic_chat.html"

HTML_ONLY_SHELL = (
    "<!DOCTYPE html><html><body>"
    '<div data-exportwechat="synthetic" class="exportwechat-message">'
    "仅有的合成占位正文</div></body></html>"
)


def message(**overrides):
    """One synthetic ExportWeChat message object (defaults: incoming text)."""
    item = {
        "StrTime": "2026-01-02 10:00:00",
        "display_name": "合成联系人",
        "is_send": False,
        "sender": "wxid_peer_synthetic",
        "text": "合成正文",
        "timestamp": 1767386400,
        "type": "1",
        "xml_dict": {},
        "avatar_src": "synthetic://avatar/peer.png",
    }
    item.update(overrides)
    return item


def bad_clock(calls_before_timeout=2):
    """Clock that jumps past any sane timeout after a few reads."""
    state = {"calls": 0}

    def clock():
        state["calls"] += 1
        return 1000.0 + state["calls"] * 1000.0

    return clock


class ExportWeChatJSONTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    # -------------------------------------------------------------- fixtures
    def write_pair(self, messages, *, html_text=HTML_ONLY_SHELL, name="sample.html"):
        """Write ``<name>`` + ``<name>.json`` and return the HTML path."""
        html_path = self.folder / name
        html_path.write_text(html_text, encoding="utf-8")
        (self.folder / f"{name}.json").write_text(
            json.dumps(messages, ensure_ascii=False), encoding="utf-8")
        return html_path

    def write_raw_companion(self, raw, *, name="sample.html"):
        html_path = self.folder / name
        html_path.write_text(HTML_ONLY_SHELL, encoding="utf-8")
        (self.folder / f"{name}.json").write_bytes(raw)
        return html_path

    @staticmethod
    def parse(path, **kwargs):
        parser = ExportWeChatJSONParser(**kwargs)
        records = list(parser.parse(path, CancellationToken()))
        return parser, records

    # ------------------------------------------------------------------ probe
    def test_probe_matches_companion_json_with_exportwechat_signature(self):
        path = self.write_pair([message()])
        match = probe(path)
        self.assertIsInstance(match, FormatMatch)
        self.assertEqual(match.kind, "exportwechat_html", "kind 沿用 FORMAT_KINDS")
        self.assertEqual(match.parser_name, PARSER_NAME)
        self.assertEqual(match.parser_name, "exportwechat_json")
        self.assertIs(match.parser, ExportWeChatJSONParser)
        self.assertEqual(match.support_level, "exact")
        self.assertEqual(match.confidence, "signature")
        self.assertEqual(match.signature, ("html.json",))
        self.assertTrue(match.exact_support)
        self.assertIn("伴生 JSON", " ".join(match.notes))
        # 注册表必须选中它，而不是通用 HTML 回退。
        self.assertIs(select_parser(path).parser, ExportWeChatJSONParser)

    def test_probe_returns_none_without_companion_or_signature(self):
        shell = self.folder / "html_only.html"
        shell.write_text(HTML_ONLY_SHELL, encoding="utf-8")
        self.assertIsNone(exportwechat_json.probe(shell), "无伴生 JSON 不命中")

        not_array = self.write_pair([message()])
        (self.folder / "sample.html.json").write_text(
            json.dumps({"messages": []}, ensure_ascii=False), encoding="utf-8")
        self.assertIsNone(exportwechat_json.probe(not_array), "顶层非数组不命中")

        missing_key = self.write_pair([{"StrTime": "2026-01-02 10:00:00",
                                        "text": "缺少 type/is_send 的合成对象"}])
        self.assertIsNone(exportwechat_json.probe(missing_key), "缺签名键不命中")

        empty = self.write_pair([])
        self.assertIsNone(exportwechat_json.probe(empty), "空数组不命中")

        not_html = self.write_pair([message()], name="sample.txt")
        self.assertIsNone(exportwechat_json.probe(not_html), "非 HTML 后缀不命中")

        missing_html = self.folder / "missing.html"
        with self.assertRaises(HistoryImportError):
            exportwechat_json.probe(missing_html)

    def test_html_only_export_stays_on_generic_fallback(self):
        shell = self.folder / "html_only.html"
        shell.write_bytes(SIGNATURE_HTML.read_bytes())
        match = probe(shell)
        self.assertIsNotNone(match)
        self.assertEqual(match.kind, "exportwechat_html")
        self.assertEqual(match.parser_name, "html_generic", "HTML-only 仍是通用回退")
        self.assertEqual(match.support_level, "signature_fallback")
        self.assertFalse(match.exact_support)
        self.assertIs(match.parser, HtmlGenericParser)
        # 同一份 HTML 一旦有了伴生 JSON，就切到精确解析。
        (self.folder / "html_only.html.json").write_text(
            json.dumps([message()], ensure_ascii=False), encoding="utf-8")
        upgraded = probe(shell)
        self.assertIs(upgraded.parser, ExportWeChatJSONParser)
        self.assertIsInstance(upgraded.support_level, str)
        self.assertTrue(upgraded.exact_support)

    # ------------------------------------------------------------------ parse
    def test_text_direction_time_and_locator(self):
        path = self.write_pair([
            message(StrTime="2026-01-02 10:00:00", is_send=False,
                    display_name="合成联系人", text="对方发来的合成消息"),
            message(StrTime="2026-01-02 10:01:00", is_send=True,
                    display_name="合成联系人", sender="wxid_self_synthetic",
                    text="我方回复的合成消息"),
        ])
        parser, records = self.parse(path)
        self.assertEqual(len(records), 2)
        self.assertEqual([record.text for record in records],
                         ["对方发来的合成消息", "我方回复的合成消息"])
        self.assertEqual([record.sender for record in records], ["合成联系人", "我"])
        self.assertEqual([record.sent_at for record in records],
                         ["2026-01-02 10:00:00", "2026-01-02 10:01:00"])
        self.assertEqual([record.ordinal for record in records], [0, 1])
        self.assertEqual([record.record_type for record in records],
                         ["message", "message"])
        self.assertEqual(records[0].locator,
                         {"kind": "exportwechat_json", "ordinal": 0, "type": "1"})
        self.assertEqual(records[0].quality["source"], PARSER_NAME)
        self.assertEqual(records[0].quality["original_type"], "1")
        self.assertIsNone(records[0].record_id, "record_id 交给存储层生成，避免重复")
        self.assertEqual(parser.page_count, 1)
        self.assertEqual(parser.pages_done, 1)

    def test_sender_falls_back_to_sender_wxid_when_display_name_missing(self):
        path = self.write_pair([
            message(display_name="", is_send=False, sender="wxid_peer_synthetic",
                    text="显示名为空的合成消息"),
            message(display_name=None, is_send=False, sender="",
                    text="显示名与 sender 都为空的合成消息"),
        ])
        _parser, records = self.parse(path)
        self.assertEqual(records[0].sender, "wxid_peer_synthetic")
        # 两条来消息都没有可用名字时，用出现最多的对方 display_name 兜底；
        # 这里没有可用名字，最后手段是 None（不伪造身份）。
        self.assertIsNone(records[1].sender)

    def test_quote_recall_red_packet_link_location_and_voice_text(self):
        path = self.write_pair([
            message(type="244813135921", text="这条是引用回复的合成正文",
                    quote_text="被引用的合成原文片段", quote_type="1"),
            message(type="10000", text="你撤回了一条合成消息"),
            message(type="8594229559345", text="[红包] 合成恭喜发财"),
            message(type="21474836529", title="合成小程序标题",
                    description="合成小程序描述", app_name="合成小程序"),
            message(type="141733920817", title="合成视频号标题",
                    description="合成视频号描述", app_name="合成视频号"),
            message(type="154618822705", title="合成文章标题",
                    description="合成文章摘要", app_name="合成公众号"),
            message(type="48", poiname="合成地点名称", label="合成地点标签"),
            message(type="50", display_content="[语音] 合成 5 秒", duration="5000"),
        ])
        parser, records = self.parse(path)
        self.assertEqual(len(records), 8, "8 条都应生成正文记录")
        texts = [record.text for record in records]
        self.assertEqual(texts[0], "〔引用〕被引用的合成原文片段｜这条是引用回复的合成正文")
        self.assertEqual(texts[1], "你撤回了一条合成消息")
        self.assertEqual(texts[2], "[红包] 合成恭喜发财")
        self.assertEqual(texts[3], "合成小程序标题 合成小程序描述")
        self.assertEqual(texts[4], "合成视频号标题 合成视频号描述")
        self.assertEqual(texts[5], "合成文章标题 合成文章摘要")
        self.assertEqual(texts[6], "合成地点名称 合成地点标签")
        self.assertEqual(texts[7], "[语音] 合成 5 秒")
        self.assertEqual(records[0].quality["quote_type"], "1")
        self.assertEqual(records[3].quality["app_name"], "合成小程序")
        self.assertEqual(records[7].quality["duration"], "5000")
        types = [record.locator["type"] for record in records]
        self.assertEqual(types, ["244813135921", "10000", "8594229559345",
                                 "21474836529", "141733920817", "154618822705",
                                 "48", "50"])
        self.assertEqual(parser.skipped_non_text, 0)
        self.assertEqual(parser.skipped_empty, 0)

    def test_images_and_emoji_are_counted_but_never_emit_empty_records(self):
        path = self.write_pair([
            message(type="3", text=None, path="synthetic://image/a.jpg",
                    thumb_path="synthetic://thumb/a.jpg", file_name="合成图片.jpg"),
            message(type="47", text=None, path="synthetic://emoji/a.gif",
                    desc="合成表情描述"),
            message(text="图片之间的合成文本"),
        ])
        parser, records = self.parse(path)
        self.assertEqual([record.text for record in records], ["图片之间的合成文本"])
        self.assertEqual(parser.skipped_non_text, 2)
        self.assertEqual(parser.skipped_empty, 0)
        self.assertEqual(parser.kept, 1)
        self.assertEqual(parser.total_entries, 3)
        self.assertTrue(any("跳过 2 条非文本消息" in note for note in parser.notes))
        self.assertEqual(records[0].ordinal, 2, "跳过项不占 ordinal，但保留原始下标")
        # 图片/表情的路径与文件名绝不出现在 notes（不含正文/路径）。
        joined = " ".join(parser.notes)
        self.assertNotIn("synthetic://", joined)
        self.assertNotIn("合成图片", joined)

    def test_unknown_type_uses_available_text_or_counts_as_skipped(self):
        path = self.write_pair([
            message(type="999999999", text="未知类型但带合成文本"),
            message(type="888888888", text=""),
            {"StrTime": "2026-01-02 10:03:00", "is_send": False,
             "display_name": "合成联系人", "sender": "wxid_peer_synthetic",
             "type": "777777777", "desc": "未知类型只有 desc 合成文本"},
            "损坏的合成占位条目（非对象）",
        ])
        parser, records = self.parse(path)
        self.assertEqual([record.text for record in records],
                         ["未知类型但带合成文本", "未知类型只有 desc 合成文本"])
        self.assertEqual(parser.skipped_empty, 2)
        self.assertEqual(parser.skipped_types.get("888888888"), 1)
        self.assertEqual(parser.skipped_types.get("invalid"), 1)
        self.assertTrue(any("无可用文本" in note for note in parser.notes))

    def test_entities_are_unescaped_and_whitespace_normalized(self):
        path = self.write_pair([
            message(text="&#x20;前后有空格的&#x0A;换行合成文本&#x20;"),
            message(text="A&amp;B 合成 &lt;标签&gt; &quot;引号&quot;"),
            message(text="   \t\n  "),
        ])
        _parser, records = self.parse(path)
        self.assertEqual([record.text for record in records],
                         ["前后有空格的 换行合成文本", 'A&B 合成 <标签> "引号"'])
        self.assertEqual(records[0].text, html.unescape(records[0].text),
                         "返回值必须已是反转义后的文本")
        self.assertIn("\n", html.unescape("&#x0A;"))
        self.assertNotIn("&amp;", records[1].text)

    def test_invalid_json_empty_array_and_non_array_are_reported(self):
        broken = self.write_raw_companion(b'[{"StrTime": "2026-01-02", ')
        with self.assertRaises(HistoryImportError) as raised:
            self.parse(broken)
        self.assertIn("伴生 JSON 无法解析", str(raised.exception))

        empty = self.write_raw_companion(b"[]", name="empty.html")
        with self.assertRaises(HistoryImportError) as raised:
            self.parse(empty)
        self.assertIn("空数组", str(raised.exception))

        obj = self.write_raw_companion(b'{"messages": []}', name="object.html")
        with self.assertRaises(HistoryImportError) as raised:
            self.parse(obj)
        self.assertIn("不是数组", str(raised.exception))

        unknown = self.write_raw_companion(
            b'[{"hello": "synthetic"}]', name="unknown.html")
        with self.assertRaises(HistoryImportError) as raised:
            self.parse(unknown)
        self.assertIn("不是可识别的 ExportWeChat", str(raised.exception))

        html_without_json = self.folder / "no_companion.html"
        html_without_json.write_text(HTML_ONLY_SHELL, encoding="utf-8")
        with self.assertRaises(HistoryImportError) as raised:
            self.parse(html_without_json)
        self.assertIn("伴生 JSON", str(raised.exception))

    def test_cancel_and_timeout_interrupt_parsing(self):
        path = self.write_pair([message(text=f"合成第 {index} 条") for index in range(50)])
        token = CancellationToken()
        token.cancel()
        with self.assertRaises(CancelledError):
            list(ExportWeChatJSONParser().parse(path, token))

        timed_out = ExportWeChatJSONParser(clock=bad_clock(), parse_timeout=30.0)
        with self.assertRaises(HistoryImportError) as raised:
            list(timed_out.parse(path, CancellationToken()))
        self.assertIn("超时", str(raised.exception))

    def test_record_limit_and_oversized_companion_are_rejected(self):
        limit = 3
        original = exportwechat_json.HTML_MAX_RECORDS
        self.addCleanup(setattr, exportwechat_json, "HTML_MAX_RECORDS", original)
        exportwechat_json.HTML_MAX_RECORDS = limit
        path = self.write_pair([message(text=f"合成第 {index} 条")
                                for index in range(limit + 1)])
        with self.assertRaises(HistoryImportError) as raised:
            self.parse(path)
        self.assertIn(str(limit), str(raised.exception))

        self.assertEqual(exportwechat_json.HTML_MAX_RECORDS, limit)
        sized = self.folder / "oversized.html"
        sized.write_text(HTML_ONLY_SHELL, encoding="utf-8")
        with (self.folder / "oversized.html.json").open("wb") as handle:
            handle.seek(base.MAX_IMPORT_FILE_BYTES + 1024)
            handle.write(b"x")
        with self.assertRaises(HistoryImportError):
            self.parse(sized)

    def test_long_text_is_truncated_and_counted(self):
        path = self.write_pair([message(text="合" * (MAX_TEXT_CHARS + 10))])
        parser, records = self.parse(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(len(records[0].text), MAX_TEXT_CHARS)
        self.assertEqual(parser.truncated, 1)
        self.assertTrue(any("截断" in note for note in parser.notes))

    # ------------------------------------------------------------- registry
    def test_companion_encoding_utf8_gb18030_and_utf16_bom(self):
        payload = [message(text="合成 GB18030 正文"),
                   message(text="合成 UTF-16 正文", is_send=True)]
        html_path = self.folder / "enc.html"
        html_path.write_text(HTML_ONLY_SHELL, encoding="utf-8")
        expectations = (
            ("utf-8", lambda text: text.encode("utf-8")),
            ("gb18030", lambda text: text.encode("gb18030")),
            ("utf-16", lambda text: text.encode("utf-16")),
        )
        for label, encode in expectations:
            with self.subTest(encoding=label):
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                (self.folder / "enc.html.json").write_bytes(encode(raw.decode("utf-8")))
                match = probe(html_path)
                self.assertIsNotNone(match, f"{label} 伴生 JSON 必须被识别")
                self.assertIs(match.parser, ExportWeChatJSONParser)
                _parser, records = self.parse(html_path)
                self.assertEqual([record.text for record in records],
                                 ["合成 GB18030 正文", "合成 UTF-16 正文"])

    def test_probe_survives_leading_placeholder_and_huge_first_message(self):
        # 真实导出首条通常是消息；就算前面多一个空对象也应能识别。
        html_path = self.write_pair([{"placeholder": True}, message(text="合成正文")])
        self.assertIs(probe(html_path).parser, ExportWeChatJSONParser)

        # 首条消息本身很大（超过初始探针窗口）也要命中。
        big = self.write_pair([message(text="合" * 300_000)])
        match = probe(big)
        self.assertIsNotNone(match)
        self.assertIs(match.parser, ExportWeChatJSONParser)

    def test_create_parser_builds_calibrated_parser_with_clock(self):
        match = FormatMatch(kind="exportwechat_html", parser_name=PARSER_NAME,
                            parser=ExportWeChatJSONParser, support_level="exact",
                            confidence="signature", signature=("html.json",))
        parser = create_parser(match, clock=lambda: 1.0, html_timeout=5.0)
        self.assertIsInstance(parser, ExportWeChatJSONParser)
        self.assertIsInstance(parser, Parser)
        self.assertEqual(parser.clock(), 1.0)
        self.assertEqual(parser.parse_timeout, 5.0)
        self.assertEqual(parser.kind, "exportwechat_html")
        self.assertEqual(parser.parser_name, PARSER_NAME)
        self.assertEqual(parser.support_level, "exact")
        self.assertTrue(issubclass(ExportWeChatJSONParser, Parser))

    def test_companion_path_is_name_based_and_sample_pair_fixture_parses(self):
        self.assertEqual(companion_path(Path("a") / "b.html"),
                         Path("a") / "b.html.json")
        self.assertIsNone(exportwechat_json.probe(GENERIC_HTML), "普通 HTML 不命中")
        self.assertTrue(PAIR_HTML.is_file() and PAIR_JSON.is_file(),
                        "缺少合成伴生夹具")
        match = probe(PAIR_HTML)
        self.assertIs(match.parser, ExportWeChatJSONParser)
        parser, records = self.parse(PAIR_HTML)
        # 19 条合成条目里：图片/表情/纯空白/无文本未知类型/非对象各 1 条被跳过。
        self.assertEqual(len(records), 14)
        self.assertEqual(parser.total_entries, 19)
        self.assertEqual(parser.skipped_non_text, 2)
        self.assertEqual(parser.skipped_empty, 3)
        texts = [record.text for record in records]
        self.assertIn("你好，我是合成联系人的第一条消息", texts)
        self.assertTrue(any("〔引用〕" in text for text in texts))
        self.assertTrue(any(text.startswith("合成小程序标题") for text in texts))
        self.assertFalse(any("synthetic://" in text for text in texts),
                         "图片/表情路径不得进入正文")
        self.assertEqual(len({record.ordinal for record in records}), len(records))

    def test_notes_and_diagnostics_never_contain_message_bodies(self):
        path = self.write_pair([
            message(text="非常独特的合成正文标记 ALPHA-ONLY"),
            message(type="3", path="synthetic://image/secret.jpg"),
        ])
        parser, records = self.parse(path)
        payload = "\n".join(parser.notes) + json.dumps(
            [{"locator": record.locator, "quality": record.quality,
              "sender": record.sender} for record in records], ensure_ascii=False)
        self.assertNotIn("ALPHA-ONLY", payload, "诊断信息不得包含聊天正文")
        self.assertNotIn("synthetic://image/secret.jpg", payload)


class ExportWeChatCalendarSafetyTests(unittest.TestCase):
    """Boundary checks that belong to the module contract, not one fixture."""

    def test_parser_never_executes_scripts_and_uses_json_module_only(self):
        source = Path(exportwechat_json.__file__).read_text(encoding="utf-8")
        for forbidden in ("eval(", "exec(", "subprocess", "urllib", "socket",
                          "importlib", "os.system"):
            self.assertNotIn(forbidden, source, f"不得使用 {forbidden}")
        self.assertIn("json.loads", source)

    def test_html_adapter_marks_sample_verified_for_json_channel_only(self):
        self.assertTrue(exportwechat_html.SAMPLE_VERIFIED)
        doc = exportwechat_html.__doc__ or ""
        self.assertIn("companion", doc.lower())
        self.assertIn("NOT calibrated", doc)
        # HTML-only 的签名回退仍然存在且语义不变。
        self.assertIn("伴生", exportwechat_html.UNVERIFIED_NOTE)
        self.assertIn("未标记“精确支持”", exportwechat_html.UNVERIFIED_NOTE)
        match = exportwechat_html.probe(SIGNATURE_HTML)
        self.assertEqual(match.support_level, "signature_fallback")
        self.assertEqual(match.parser_name, "html_generic")
        with self.assertRaises(HistoryImportError):
            list(exportwechat_html.ExportWeChatHTMLParser().parse(
                SIGNATURE_HTML, CancellationToken()))


if __name__ == "__main__":
    unittest.main()
