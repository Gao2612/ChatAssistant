import unittest
from unittest.mock import patch

from liteapp import wechat_find
from liteapp.wechat_find import capture_client_bgr, find_conversation

HWND = 4242


class FakeBackend:
    def __init__(self):
        self.clicks = []
        self.keys = []

    def is_window(self, hwnd):
        return hwnd == HWND

    def activate(self, hwnd):
        return True

    def click(self, hwnd, x, y):
        self.clicks.append((x, y))
        return True

    def key_combo(self, keys):
        self.keys.append(tuple(keys))
        return True

    def set_clipboard_text(self, text):
        return True


class FakeReader:
    def __init__(self, script):
        self.script = list(script)

    def ocr(self, image, use_cls=False):
        rows = self.script.pop(0) if self.script else []
        result = []
        for text, x, y in rows:
            box = [(x, y), (x + 40, y), (x + 40, y + 18), (x, y + 18)]
            result.append((box, text, 0.9))
        return result, None


def frame():
    import numpy as np
    return np.zeros((600, 1000, 3), dtype=np.uint8)


class ForegroundGuardTests(unittest.TestCase):
    def test_capture_refuses_when_window_not_foreground(self):
        with patch.object(wechat_find, "_foreground_ok", return_value=False):
            self.assertIsNone(capture_client_bgr(HWND))

    def test_find_refuses_before_first_click(self):
        backend = FakeBackend()
        reader = FakeReader([ [("Q搜索", 60, 50)] ])
        with patch.object(wechat_find, "_foreground_ok", return_value=False):
            result = find_conversation({"hwnd": HWND}, "田子夜", backend=backend,
                                       reader=reader,
                                       capture=lambda hwnd: (frame(), 1000, 600),
                                       settle=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "not_foreground")
        self.assertEqual(backend.clicks, [])
        self.assertNotIn((0x1B,), backend.keys)

    def test_find_refuses_race_before_result_click(self):
        backend = FakeBackend()
        reader = FakeReader([
            [("Q搜索", 60, 50)],
            [("田子夜", 80, 120)],
        ])
        # capture guard is bypassed by the injected capture; the two pre-click
        # checks are True, then the result-click check becomes False.
        with patch.object(wechat_find, "_foreground_ok",
                          side_effect=[True, True, False]):
            result = find_conversation({"hwnd": HWND}, "田子夜", backend=backend,
                                       reader=reader,
                                       capture=lambda hwnd: (frame(), 1000, 600),
                                       settle=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "not_foreground")
        # Exactly one click: the search box. The result row must not be clicked.
        self.assertEqual(len(backend.clicks), 1)


if __name__ == "__main__":
    unittest.main()
