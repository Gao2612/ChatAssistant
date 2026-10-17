import unittest

from liteapp.wechat_find import find_conversation

WECHAT_HWND = 4242


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.clipboard = None
        self._foreground = True

    def is_window(self, hwnd):
        return hwnd == WECHAT_HWND

    def activate(self, hwnd):
        self.calls.append(("activate", hwnd))
        return self._foreground

    def click(self, hwnd, x, y):
        self.calls.append(("click", x, y))
        return True

    def key_combo(self, keys):
        self.calls.append(("keys", tuple(keys)))
        return True

    def set_clipboard_text(self, text):
        self.calls.append(("clipboard", text))
        self.clipboard = text
        return True


class FakeReader:
    """Return scripted OCR rows per call."""

    def __init__(self, script):
        self.script = list(script)

    def ocr(self, image, use_cls=False):
        rows = self.script.pop(0) if self.script else []
        result = []
        for text, x, y in rows:
            box = [(x, y), (x + 60, y), (x + 60, y + 20), (x, y + 20)]
            result.append((box, text, 0.9))
        return result, None


def frame():
    import numpy as np
    return np.zeros((800, 1200, 3), dtype=np.uint8)


def script(*calls):
    frames = []
    for rows in calls:
        frames.append([(text, x, y) for text, x, y in rows])
    return frames


class FindConversationTests(unittest.TestCase):
    def test_successful_ocr_find(self):
        backend = FakeBackend()
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("田子夜", 145, 115), ("郜亚禹", 145, 196)],
            [("田子夜", 390, 56)],
        ))
        result = find_conversation(
            {"hwnd": WECHAT_HWND}, "田子夜", backend=backend, reader=reader,
            capture=lambda hwnd: (frame(), 1200, 800), settle=0.0)
        self.assertTrue(result.ok, result.reason)
        self.assertIn("verify", result.steps)
        self.assertEqual(backend.clipboard, "田子夜")
        self.assertEqual([call[0] for call in backend.calls].count("click"), 2)

    def test_missing_search_field_fails_closed(self):
        result = find_conversation(
            {"hwnd": WECHAT_HWND}, "田子夜", backend=FakeBackend(),
            reader=FakeReader(script([("聊天", 10, 10)])),
            capture=lambda hwnd: (frame(), 1200, 800), settle=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "search_not_found")

    def test_title_mismatch_closes_search_without_sending(self):
        backend = FakeBackend()
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("田子夜", 145, 115)],
            [("鄢子夜", 390, 56)],
        ))
        result = find_conversation(
            {"hwnd": WECHAT_HWND}, "田子夜", backend=backend, reader=reader,
            capture=lambda hwnd: (frame(), 1200, 800), settle=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "title_mismatch")
        self.assertIn(("keys", (0x1B,)), backend.calls)

    def test_invalid_title_fails_before_touching_wechat(self):
        backend = FakeBackend()
        result = find_conversation({"hwnd": WECHAT_HWND}, " ",
                                   backend=backend, capture=lambda hwnd: None)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_title")
        self.assertEqual(backend.calls, [])


if __name__ == "__main__":
    unittest.main()
