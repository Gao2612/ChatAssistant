"""C3 evidence-governance helpers: status sanitising and geometry comparisons.

These unit tests are headless: they import only the pure helper functions of
``tools/c3_real_window_verify.py`` (no Qt, no UIA, no real window access).  They
pin the privacy rule "known assistant status messages only, never chat/analysis
content" (review P2-8) and the 2-physical-pixel geometry tolerance used by C3.
"""

import importlib.util
import unittest
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "tools" / "c3_real_window_verify.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("c3_real_window_verify", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StatusSanitiserTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()

    def test_keeps_assistant_protocol_status_messages(self):
        texts = self.tool.status_texts([
            "正在本地识别 · 中央实时画面",
            "微信已停靠 · 中央微信原生可交互",
            "微信弹窗覆盖聊天区域，已暂停分析，请先关闭弹窗。",
            "微信已隐藏到托盘，仍在后台读取；点击“启动微信”可恢复窗口。",
            "微信已最小化：中央为只读画面，返回聊天页后自动还原。",
            "设置已保存。",
        ])
        self.assertEqual(len(texts), 6)

    def test_drops_chat_analysis_and_contact_names(self):
        long_text = "这是一段真实的聊天内容" * 20
        texts = self.tool.status_texts([
            "田子夜",                                  # contact name
            "我们明天几点见面？",                       # chat message
            "建议一：可以先确认时间。",                  # generated suggestion
            "我昨天在微信上看到你说的识别结果了",        # chat text with trigger words
            "微信聊天记录：今天聊到很晚，明天再说吧",     # chat text with "微信"
            long_text,                                 # over the length cap
            "",
            "   ",
        ])
        self.assertEqual(texts, [])

    def test_drops_unknown_text_even_if_short(self):
        texts = self.tool.status_texts([
            "微信", "识别", "窗口", "画面", "连接",
        ])
        self.assertEqual(texts, [])

    def test_deduplicates_and_collapses_whitespace(self):
        texts = self.tool.status_texts(["微信已停靠   ·  中央",
                                        "微信已停靠 · 中央"])
        self.assertEqual(texts, ["微信已停靠 · 中央"])


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()

    def test_rect_close_uses_physical_pixel_tolerance(self):
        close = self.tool.rect_close
        self.assertTrue(close((10, 10, 110, 110), (12, 8, 112, 108)))
        self.assertFalse(close((10, 10, 110, 110), (13, 10, 113, 110)))
        self.assertFalse(close(None, (0, 0, 1, 1)))
        self.assertFalse(close((0, 0, 1), (0, 0, 1, 1)))

    def test_rect_inside_requires_strict_containment(self):
        inside = self.tool.rect_inside
        self.assertTrue(inside((10, 10, 100, 100), (0, 0, 200, 200)))
        self.assertFalse(inside((0, 0, 200, 200), (10, 10, 100, 100)))
        self.assertFalse(inside((0, 0, 200, 200), (0, 0, 200, 200)))
        self.assertFalse(inside(None, (0, 0, 10, 10)))

    def test_rect_size(self):
        self.assertEqual(self.tool.rect_size((10, 20, 110, 220)), [100, 200])
        self.assertIsNone(self.tool.rect_size(None))


class SourceTreePinTests(unittest.TestCase):
    """The probe must declare the 0.5.8 source-tree acceptance target it was run on."""

    def test_acceptance_target_is_pinned(self):
        tool = load_tool()
        self.assertEqual(tool.SOURCE_TREE_VERSION, "0.5.8")
        self.assertTrue(tool.SOURCE_TREE_COMMIT.startswith("a3b2994"))


if __name__ == "__main__":
    unittest.main()
