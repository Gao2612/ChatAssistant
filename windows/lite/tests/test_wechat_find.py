import unittest
from unittest import mock

from liteapp import wechat_find as wf
from liteapp.wechat_find import find_conversation

WECHAT_HWND = 4242
VK_RETURN = 0x0D


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

    def clicks(self):
        return [call for call in self.calls if call[0] == "click"]

    def key_combos(self):
        return [call[1] for call in self.calls if call[0] == "keys"]


class FakeReader:
    """Return scripted OCR rows per call."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def ocr(self, image, use_cls=False):
        self.calls += 1
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


def run(reader, backend=None, title="田子夜"):
    backend = backend or FakeBackend()
    result = find_conversation(
        {"hwnd": WECHAT_HWND}, title, backend=backend, reader=reader,
        capture=lambda hwnd: (frame(), 1200, 800), settle=0.0)
    return result, backend


class FindConversationTests(unittest.TestCase):
    def setUp(self):
        # Never load the real OCR engine in unit tests (read_title is patched),
        # and treat the fake HWND as a visible foreground window: the product
        # guard (Lead final review) is covered separately in
        # tests.test_wechat_find_foreground.
        for name, value in (("_read_title_crop", lambda crop: ""),
                            ("_foreground_ok", lambda hwnd: True)):
            patcher = mock.patch.object(wf, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_non_foreground_window_fails_without_touching_wechat(self):
        backend = FakeBackend()
        reader = FakeReader(script([("Q搜索", 98, 58)]))
        with mock.patch.object(wf, "_foreground_ok", lambda hwnd: False):
            result = find_conversation(
                {"hwnd": WECHAT_HWND}, "田子夜", backend=backend, reader=reader,
                capture=lambda hwnd: (frame(), 1200, 800), settle=0.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "not_foreground")
        self.assertEqual(backend.clicks(), [])
        self.assertNotIn(("keys", (0x1B,)), backend.calls)

    def test_successful_ocr_find(self):
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("田子夜", 145, 115), ("郜亚禹", 145, 196)],
            [("田子夜", 390, 56)],
        ))
        result, backend = run(reader)
        self.assertTrue(result.ok, result.reason)
        self.assertIn("verify", result.steps)
        self.assertEqual(backend.clipboard, "田子夜")
        self.assertEqual(len(backend.clicks()), 2)

    def test_missing_search_field_fails_closed(self):
        result, backend = run(FakeReader(script([("聊天", 10, 10)])))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "search_not_found")
        self.assertEqual(backend.clicks(), [])

    def test_title_mismatch_closes_search_without_sending(self):
        backend = FakeBackend()
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("田子夜", 145, 115)],
            [("鄢子夜", 390, 56)],
        ))
        result, _ = run(reader, backend=backend)
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

    # -- C4: exact result-row evidence, no fuzzy/substring click -------------
    def test_message_preview_row_is_not_the_click_target(self):
        """A row that only *contains* the name must never be clicked."""
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("包含：田子夜", 145, 115), ("田子夜", 145, 200)],
            [("田子夜", 390, 56)],
        ))
        result, backend = run(reader)
        self.assertTrue(result.ok, result.reason)
        clicks = backend.clicks()
        self.assertEqual(len(clicks), 2)
        # second click = exact name row (y 200..220 -> center 210), not preview (125)
        self.assertEqual(clicks[1][2], 210)

    def test_preview_rows_only_fail_closed_without_result_click(self):
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("包含：田子夜", 145, 115)],
            [],
            [],
            [],
            [("Q搜索", 98, 58)],
            [("包含：田子夜", 145, 115)],
            [],
            [],
            [],
        ))
        result, backend = run(reader)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "result_not_found")
        self.assertEqual(len(backend.clicks()), 2)  # only the two search-box clicks
        self.assertIn(("keys", (0x1B,)), backend.calls)

    def test_search_input_row_is_never_clicked(self):
        """The search input shows the typed name at y=60; it is not a result row."""
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("田子夜", 122, 60)],
            [],
            [],
            [],
            [("Q搜索", 98, 58)],
            [("田子夜", 122, 60)],
            [],
            [],
            [],
        ))
        result, backend = run(reader)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "result_not_found")
        self.assertTrue(all(call[2] < 100 for call in backend.clicks()))

    def test_duplicate_exact_rows_fail_closed(self):
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("田子夜", 145, 115), ("田子夜", 145, 200)],
        ))
        result, backend = run(reader)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "result_ambiguous")
        self.assertEqual(len(backend.clicks()), 1)  # no result-row click
        self.assertIn(("keys", (0x1B,)), backend.calls)

    # -- C4: exact header acceptance only -----------------------------------
    def test_secondary_header_crop_accepts_exact_name(self):
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("郜亚禹", 145, 115)],
            [("口", 10, 5)],
            [("郜亚禹", 20, 10)],
        ))
        result, _ = run(reader, title="郜亚禹")
        self.assertTrue(result.ok, result.reason)
        self.assertIn("verify", result.steps)

    def test_single_character_difference_is_never_accepted(self):
        """郜亚禹 vs 部亚禹: a controlled-tolerance accept would risk a wrong chat."""
        header_calls = [[("部亚禹", 390, 56)] for _ in range(4)]
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("郜亚禹", 145, 115)],
            *header_calls,
        ))
        with mock.patch.object(wf, "_read_title_crop", lambda crop: "部亚禹"):
            result, backend = run(reader, title="郜亚禹")
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "title_mismatch")
        self.assertIn(("keys", (0x1B,)), backend.calls)

    def test_read_title_exact_match_is_accepted(self):
        header_calls = [[("口", 10, 5)] for _ in range(4)]
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("郜亚禹", 145, 115)],
            *header_calls,
        ))
        with mock.patch.object(wf, "_read_title_crop", lambda crop: "郜亚禹"):
            result, _ = run(reader, title="郜亚禹")
        self.assertTrue(result.ok, result.reason)

    # -- C4: the removed unconditional Enter must stay removed --------------
    def test_no_enter_is_pressed_after_the_result_click(self):
        reader = FakeReader(script(
            [("Q搜索", 98, 58)],
            [("田子夜", 145, 115)],
            [("田子夜", 390, 56)],
        ))
        result, backend = run(reader)
        self.assertTrue(result.ok, result.reason)
        combos = backend.key_combos()
        self.assertNotIn((VK_RETURN,), combos)
        for combo in combos:
            self.assertNotIn(VK_RETURN, combo)
        self.assertNotIn("press_enter", result.steps)


if __name__ == "__main__":
    unittest.main()
