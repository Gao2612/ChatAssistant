"""S-AUTO-SEND tests: conversation selection + message sending (task-19).

All tests use a fake Win32 backend / fake clock and never touch real WeChat:
no real window, mouse, keyboard, clipboard or OCR call is made. Covered:

* title verification: mismatch / unreadable title / missing verifier all fail
  closed before sending; an explicit verifier or OCR title rect is required;
* ``dry_run=True`` fills the input but never sends;
* send success via button or Enter, clipboard paste, Ctrl+A optional;
* DPI conversion of logical client coordinates (96..192 DPI);
* timeouts and backend failures return structured :class:`ActionResult`
  instead of raising;
* stale/reused window identity is rejected before any input;
* the message text never appears in the logged result (no chat-content leak).

The real-window acceptance (one explicitly marked test message on the
user-authorized conversation) is executed by the Lead; see
``_reports/S_AUTO_SEND_report.md``.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from liteapp import discovery  # noqa: E402
from liteapp import wechat_actions as actions_module  # noqa: E402
from liteapp.discovery import WindowTarget  # noqa: E402
from liteapp.wechat_actions import (  # noqa: E402
    ActionResult,
    REASONS,
    WeChatActions,
    dpi_scale,
    estimate_layout_rects,
    logical_point_to_physical,
    logical_rect_to_physical,
    physical_point_to_logical,
    rect_center,
)

TARGET = "测试联系人"
MARKER_TEXT = "【自动化验收测试消息】请忽略（合成文本，仅用于本次测试）"

# 120 DPI (125%) -> scale 1.25; centers chosen so physical coordinates are exact
SEARCH_RECT = (96, 96, 208, 144)      # center (152, 120) -> (190, 150)
RESULT_RECT = (96, 160, 224, 208)     # center (160, 184) -> (200, 230)
INPUT_RECT = (96, 304, 400, 400)      # center (248, 352) -> (310, 440)
SEND_RECT = (400, 416, 480, 480)      # center (440, 448) -> (550, 560)
TITLE_RECT = (200, 20, 600, 60)


class FakeBackend:
    """Records every Win32-ish call; no real input/clipboard/window access."""

    def __init__(self, dpi=120):
        self.calls = []
        self.now = 0.0
        self.windows = {1001}
        self.dpi_value = int(dpi)
        self.activate_result = True
        self.activate_advance = 0.0
        self.click_result = True
        self.key_result = True
        self.clipboard_result = True
        self.fail_click_at = None
        self.raise_on = None
        self._clicks = 0

    def is_window(self, hwnd):
        return int(hwnd) in self.windows

    def dpi(self, hwnd):
        return self.dpi_value

    def activate(self, hwnd):
        self.calls.append(("activate", int(hwnd)))
        self.now += self.activate_advance
        if self.raise_on == "activate":
            raise OSError("synthetic backend error")
        return self.activate_result

    def click(self, hwnd, x, y):
        self._clicks += 1
        self.calls.append(("click", int(hwnd), int(x), int(y)))
        if self.raise_on == "click":
            raise OSError("synthetic backend error")
        if self.fail_click_at == self._clicks:
            return False
        return self.click_result

    def key_combo(self, keys):
        self.calls.append(("key", tuple(int(key) for key in keys)))
        if self.raise_on == "key":
            raise OSError("synthetic backend error")
        return self.key_result

    def press_enter(self):
        self.calls.append(("enter",))
        return self.key_result

    def set_clipboard_text(self, text):
        self.calls.append(("clipboard", str(text)))
        if self.raise_on == "clipboard":
            raise OSError("synthetic backend error")
        return self.clipboard_result

    def sleep(self, seconds):
        seconds = float(seconds)
        self.calls.append(("sleep", round(seconds, 3)))
        self.now += seconds

    # -- helpers -------------------------------------------------------------
    def clicks(self):
        return [call for call in self.calls if call[0] == "click"]

    def keys(self):
        return [call[1] for call in self.calls if call[0] == "key"]

    def clipboard_texts(self):
        return [call[1] for call in self.calls if call[0] == "clipboard"]


def make_actions(backend):
    return WeChatActions(backend, clock=lambda: backend.now,
                         sleep=backend.sleep)


class CoordinateConversionTests(unittest.TestCase):
    def test_dpi_scale_and_invalid_dpi(self):
        self.assertAlmostEqual(dpi_scale(96), 1.0)
        self.assertAlmostEqual(dpi_scale(120), 1.25)
        self.assertAlmostEqual(dpi_scale(144), 1.5)
        for bad in (0, -96):
            with self.subTest(dpi=bad):
                with self.assertRaises(ValueError):
                    dpi_scale(bad)
        with self.assertRaises(TypeError):
            dpi_scale("120")

    def test_logical_to_physical_and_back_within_one_pixel(self):
        for dpi in (96, 120, 144, 168, 192, 240):
            for point in ((0, 0), (100, 50), (248, 352), (999, 701)):
                with self.subTest(dpi=dpi, point=point):
                    physical = logical_point_to_physical(point, dpi)
                    back = physical_point_to_logical(physical, dpi)
                    self.assertLessEqual(abs(back[0] - point[0]), 1)
                    self.assertLessEqual(abs(back[1] - point[1]), 1)

    def test_rect_conversion_and_center(self):
        self.assertEqual(logical_rect_to_physical((96, 96, 208, 144), 120),
                         (120, 120, 260, 180))
        self.assertEqual(rect_center((0, 0, 10, 20)), (5, 10))
        with self.assertRaises(TypeError):
            logical_rect_to_physical((1, 2, 3), 120)

    def test_estimate_layout_rects_are_sane(self):
        rects = estimate_layout_rects(1000, 700)
        self.assertEqual(set(rects), {"search_rect", "result_rect", "title_rect",
                                      "input_rect", "send_rect"})
        for name, rect in rects.items():
            with self.subTest(name=name):
                self.assertEqual(len(rect), 4)
                self.assertGreater(rect[2], rect[0])
                self.assertGreater(rect[3], rect[1])
                self.assertGreaterEqual(rect[0], 0)
                self.assertGreaterEqual(rect[1], 0)
                self.assertLessEqual(rect[2], 1000)
                self.assertLessEqual(rect[3], 700)
        self.assertLess(rects["search_rect"][0], rects["input_rect"][0])
        self.assertLess(rects["search_rect"][1], rects["result_rect"][1])
        with self.assertRaises(ValueError):
            estimate_layout_rects(0, 700)


class SelectConversationTests(unittest.TestCase):
    _UNSET = object()

    def setUp(self):
        self.backend = FakeBackend()
        self.actions = make_actions(self.backend)
        self.verify_calls = []

    def verify(self, current=TARGET):
        def verify():
            self.verify_calls.append(current)
            return current

        return verify

    def select(self, verify=_UNSET, **overrides):
        if verify is self._UNSET:
            verify = self.verify()
        values = dict(search_rect=SEARCH_RECT, result_rect=RESULT_RECT,
                      input_rect=INPUT_RECT, send_rect=SEND_RECT, verify=verify)
        values.update(overrides)
        return self.actions.select_conversation(1001, TARGET, **values)

    def test_success_selects_and_verifies_title(self):
        result = self.select()
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "ok")
        self.assertTrue(result.selected)
        self.assertTrue(result.verified)
        self.assertEqual(len(self.verify_calls), 1)
        clicks = self.backend.clicks()
        self.assertEqual([(click[2], click[3]) for click in clicks],
                         [(190, 150), (200, 230)])
        self.assertEqual(self.backend.clipboard_texts(), [TARGET])
        keys = self.backend.keys()
        self.assertIn((actions_module.VK_CONTROL, actions_module.VK_A), keys)
        self.assertIn((actions_module.VK_CONTROL, actions_module.VK_V), keys)
        for step in ("activate", "click_search", "paste_title",
                     "click_result", "verify"):
            self.assertIn(step, result.steps)
        self.assertEqual(result.steps[-1], "verify")

    def test_title_mismatch_refuses_and_stops(self):
        result = self.select(verify=self.verify("另一位联系人"))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "title_mismatch")
        self.assertFalse(result.selected)
        self.assertFalse(result.verified)
        self.assertEqual(len(self.verify_calls), 1)
        self.assertEqual(result.steps[-1], "verify")
        self.assertNotIn("send", result.steps)

    def test_unreadable_title_refused(self):
        result = self.select(verify=self.verify(None))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "title_unverified")
        self.assertFalse(result.selected)

    def test_whitespace_only_verify_result_refused(self):
        result = self.select(verify=self.verify("   "))
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "title_unverified")

    def test_missing_verifier_fails_before_touching_window(self):
        result = self.select(verify=None, title_rect=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "verification_required")
        self.assertEqual(self.backend.calls, [])

    def test_invalid_rects_fail_closed(self):
        result = self.select(result_rect=None)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "invalid_rect")
        self.assertEqual(self.backend.calls, [])

    def test_invalid_window_fails_closed(self):
        for window in (0, 999999, None):
            with self.subTest(window=window):
                self.backend.calls.clear()
                result = self.actions.select_conversation(
                    window, TARGET, search_rect=SEARCH_RECT,
                    result_rect=RESULT_RECT, verify=self.verify())
                self.assertFalse(result.ok)
                self.assertEqual(result.reason, "invalid_window")
                self.assertEqual(self.backend.calls, [])

    def test_stale_window_identity_refused(self):
        stale = WindowTarget(1001, 777, r"C:\Program Files\Tencent\Weixin\Weixin.exe",
                             987654321)
        with patch.object(actions_module.discovery, "valid", return_value=False):
            result = self.actions.select_conversation(
                stale, TARGET, search_rect=SEARCH_RECT,
                result_rect=RESULT_RECT, verify=self.verify())
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "stale_window")
        self.assertEqual(self.backend.calls, [])

    def test_activation_failure_stops_before_search(self):
        self.backend.activate_result = False
        result = self.select()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "activate_failed")
        self.assertEqual(self.backend.clicks(), [])

    def test_clipboard_failure_stops_before_result_click(self):
        self.backend.clipboard_result = False
        result = self.select()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "clipboard_failed")
        self.assertEqual(len(self.backend.clicks()), 1)  # search click only

    def test_timeout_returns_structured_failure(self):
        self.backend.activate_advance = 10.0
        result = self.select(timeout=5.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "timeout")
        self.assertEqual(len(self.backend.clicks()), 0)

    def test_backend_exception_is_wrapped(self):
        self.backend.raise_on = "click"
        result = self.select()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "backend_error")
        self.assertIsInstance(result, ActionResult)

    def test_ocr_title_rect_verifier_is_used(self):
        with patch.object(actions_module, "_read_title_via_ocr",
                          return_value=TARGET) as ocr:
            result = self.actions.select_conversation(
                1001, TARGET, search_rect=SEARCH_RECT, result_rect=RESULT_RECT,
                verify=None, title_rect=TITLE_RECT)
        self.assertTrue(result.ok)
        self.assertTrue(result.selected)
        ocr.assert_called_once()
        self.assertEqual(ocr.call_args[0][0], 1001)

    def test_ocr_title_mismatch_refuses(self):
        with patch.object(actions_module, "_read_title_via_ocr",
                          return_value="另一位联系人"):
            result = self.actions.select_conversation(
                1001, TARGET, search_rect=SEARCH_RECT, result_rect=RESULT_RECT,
                verify=None, title_rect=TITLE_RECT)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "title_mismatch")

    def test_dry_run_flag_is_reported(self):
        result = self.select(dry_run=True)
        self.assertTrue(result.ok)
        self.assertTrue(result.dry_run)


class SendMessageTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        self.actions = make_actions(self.backend)

    def send(self, text=MARKER_TEXT, **overrides):
        values = dict(input_rect=INPUT_RECT, send_rect=SEND_RECT)
        values.update(overrides)
        return self.actions.send_message(1001, text, **values)

    def test_send_success_via_send_button(self):
        result = self.send(use_enter=False)
        self.assertTrue(result.ok)
        self.assertEqual(result.reason, "ok")
        self.assertTrue(result.sent)
        self.assertFalse(result.dry_run)
        clicks = self.backend.clicks()
        self.assertEqual([(click[2], click[3]) for click in clicks],
                         [(310, 440), (550, 560)])
        self.assertEqual(self.backend.clipboard_texts(), [MARKER_TEXT])
        keys = self.backend.keys()
        self.assertIn((actions_module.VK_CONTROL, actions_module.VK_A), keys)
        self.assertIn((actions_module.VK_CONTROL, actions_module.VK_V), keys)
        for step in ("activate", "click_input", "select_all", "paste_text",
                     "click_send"):
            self.assertIn(step, result.steps)

    def test_send_via_enter_when_no_send_rect(self):
        result = self.send(send_rect=None)
        self.assertTrue(result.ok)
        self.assertTrue(result.sent)
        self.assertIn(("enter",), self.backend.calls)
        self.assertEqual(len(self.backend.clicks()), 1)  # only the input click
        self.assertEqual(result.steps[-1], "press_enter")

    def test_dry_run_fills_text_but_never_sends(self):
        result = self.send(dry_run=True)
        self.assertTrue(result.ok)
        self.assertFalse(result.sent)
        self.assertTrue(result.dry_run)
        self.assertFalse(result.verified)
        self.assertEqual(self.backend.clipboard_texts(), [MARKER_TEXT])
        self.assertNotIn(("enter",), self.backend.calls)
        self.assertEqual(len(self.backend.clicks()), 1)  # input only, no send click
        self.assertNotIn("click_send", result.steps)
        self.assertNotIn("press_enter", result.steps)

    def test_select_all_can_be_disabled(self):
        result = self.send(select_all=False)
        self.assertTrue(result.ok)
        self.assertNotIn((actions_module.VK_CONTROL, actions_module.VK_A),
                         self.backend.keys())

    def test_send_button_failure_reports_send_failed(self):
        self.backend.fail_click_at = 2  # first input click ok, send click fails
        result = self.send(use_enter=False)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "send_failed")
        self.assertFalse(result.sent)
        self.assertEqual(len(self.backend.clicks()), 2)

    def test_clipboard_failure_stops_before_sending(self):
        self.backend.clipboard_result = False
        result = self.send()
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "clipboard_failed")
        self.assertFalse(result.sent)
        self.assertNotIn(("enter",), self.backend.calls)

    def test_verify_sent_false_is_reported_as_sent_but_unverified(self):
        result = self.send(verify_sent=lambda text: False)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "send_verify_failed")
        self.assertTrue(result.sent)
        self.assertFalse(result.verified)

    def test_verify_sent_true_marks_verified(self):
        result = self.send(verify_sent=lambda text: text == MARKER_TEXT)
        self.assertTrue(result.ok)
        self.assertTrue(result.sent)
        self.assertTrue(result.verified)

    def test_empty_or_too_long_text_rejected(self):
        empty = self.send(text="   ")
        self.assertEqual(empty.reason, "empty_text")
        long_text = self.send(text="x" * (actions_module.MAX_TEXT_CHARS + 1))
        self.assertEqual(long_text.reason, "text_too_long")
        self.assertEqual(self.backend.calls, [])

    def test_invalid_input_rect_rejected(self):
        result = self.send(input_rect=None)
        self.assertEqual(result.reason, "invalid_rect")
        self.assertEqual(self.backend.calls, [])

    def test_invalid_window_rejected(self):
        result = self.actions.send_message(0, MARKER_TEXT, input_rect=INPUT_RECT)
        self.assertEqual(result.reason, "invalid_window")
        self.assertEqual(self.backend.calls, [])

    def test_timeout_on_activation(self):
        self.backend.activate_advance = 10.0
        result = self.send(timeout=5.0)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "timeout")
        self.assertEqual(self.backend.clicks(), [])

    def test_message_text_not_leaked_in_result(self):
        result = self.send()
        self.assertTrue(result.ok)
        rendered = repr(result) + result.detail + " ".join(result.steps)
        self.assertNotIn("自动化验收", rendered)
        self.assertNotIn(MARKER_TEXT, rendered)
        self.assertEqual(self.backend.clipboard_texts(), [MARKER_TEXT])


class ActionResultTests(unittest.TestCase):
    def test_reason_catalogue_is_complete_and_stable(self):
        for name in ("ok", "invalid_window", "stale_window", "title_mismatch",
                     "title_unverified", "verification_required", "timeout",
                     "backend_error", "empty_text", "send_failed",
                     "send_verify_failed", "clipboard_failed", "activate_failed"):
            self.assertIn(name, REASONS)
            self.assertEqual(REASONS[name], name)

    def test_action_result_is_frozen_and_normalizes_steps(self):
        result = ActionResult(ok=True, reason="ok", steps=["activate", "verify"])
        self.assertEqual(result.steps, ("activate", "verify"))
        with self.assertRaises(Exception):
            result.ok = False  # type: ignore[misc]
        with self.assertRaises(ValueError):
            ActionResult(ok=True, reason="")


if __name__ == "__main__":
    unittest.main()
