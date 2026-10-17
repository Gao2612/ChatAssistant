"""WindowDock regression tests for T4 (identity reuse, popup, z-order, DPI).

The legacy 0.2 tests are kept: a docked window is only *moved* (top-level,
never reparented) and its original geometry is restored on detach.  T4 adds the
acceptance cases from ``WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md``:

* geometry verification tolerance is 2 physical pixels (cross-DPI round trips);
* a reused HWND / changed process identity is never operated on;
* an existing native popup is never covered by the position loop;
* Z order is verified/repaired separately from geometry (the 2026-10-17 issue
  where ``align`` only fixed Z order when the rectangle changed);
* a window is not yanked back while the user is dragging it;
* no SetParent / SetForegroundWindow / AttachThreadInput / hooking is used.
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from liteapp import window_dock  # noqa: E402
from liteapp.window_dock import WindowDock  # noqa: E402


class FakeWindows:
    """In-memory Win32 stand-in; mirrors the optional NativeWindows surface."""

    def __init__(self):
        self.rectangles = {10: (10, 20, 810, 620), 20: (0, 0, 1000, 800),
                           30: (100, 120, 700, 520)}
        self.minimized = set()
        self.calls = []
        self.zorder_ok = False
        self.keep_zorder_broken = False
        self.offset_once = 0
        self.foreground = 0
        self.popup = None

    def rect(self, hwnd):
        return self.rectangles[hwnd]

    def restored_rect(self, hwnd):
        return self.rectangles[hwnd]

    def is_window(self, hwnd):
        return hwnd in self.rectangles

    def is_minimized(self, hwnd):
        return hwnd in self.minimized

    def set_pos(self, hwnd, after, rect):
        self.calls.append((hwnd, after, tuple(rect)))
        offset, self.offset_once = self.offset_once, 0
        adjusted = tuple(value + offset for value in rect)
        self.rectangles[hwnd] = adjusted
        if after is not None and not self.keep_zorder_broken:
            self.zorder_ok = True
        return True

    def set_minimized(self, hwnd, value):
        self.calls.append(("minimize", hwnd, bool(value)))
        if value:
            self.minimized.add(hwnd)
        else:
            self.minimized.discard(hwnd)
        return True

    def popup_covering(self, hwnd, area):
        return self.popup

    def zorder_ok_for(self, hwnd, host):
        return self.zorder_ok

    def foreground_window(self):
        return self.foreground

    def set_pos_calls(self):
        return [call for call in self.calls if isinstance(call[0], int)]

    def minimize_calls(self):
        return [call for call in self.calls if call[0] == "minimize"]


def make_dock(windows, validate=lambda target: True):
    return WindowDock(api=windows, validate=validate)


class DockTests(unittest.TestCase):
    def test_dock_preserves_top_level_identity_and_restores_original_geometry(self):
        windows = FakeWindows()
        dock = make_dock(windows)
        self.assertTrue(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        self.assertEqual(windows.rect(10), windows.rect(30))
        self.assertEqual(windows.calls[0][1], 20)
        self.assertTrue(dock.active)
        dock.detach()
        self.assertEqual(windows.rect(10), (10, 20, 810, 620))
        self.assertIsNone(windows.calls[-1][1])

    def test_invalid_or_too_small_window_never_moves(self):
        windows = FakeWindows()
        windows.rectangles[30] = (1, 1, 100, 90)
        dock = make_dock(windows, validate=lambda target: False)
        self.assertFalse(dock.attach(10, 20, 30))
        self.assertEqual(windows.calls, [])

    def test_app_can_hide_and_restore_its_connected_wechat_window(self):
        windows = FakeWindows()
        dock = make_dock(windows, validate=lambda target: target == 10)
        self.assertTrue(dock.set_minimized(10, True))
        self.assertIn(10, windows.minimized)
        self.assertTrue(dock.set_minimized(10, False))
        self.assertNotIn(10, windows.minimized)
        self.assertFalse(dock.set_minimized(999, True))

    def test_geometry_verification_tolerance_is_two_physical_pixels(self):
        windows = FakeWindows()
        windows.offset_once = 1  # DPI rounding within tolerance is accepted
        dock = make_dock(windows)
        self.assertTrue(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        dock.detach()
        windows.offset_once = 3  # beyond tolerance must fail and roll back
        dock = make_dock(windows)
        self.assertFalse(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        self.assertEqual(windows.rect(10), (10, 20, 810, 620))
        self.assertFalse(dock.active)

    def test_identity_change_after_attach_deactivates_without_moving(self):
        windows = FakeWindows()
        identity_ok = [True]
        dock = make_dock(windows, validate=lambda target: identity_ok[0])
        self.assertTrue(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        windows.calls.clear()
        identity_ok[0] = False
        self.assertFalse(dock.align())
        self.assertFalse(dock.active)
        self.assertEqual(windows.calls, [])

    def test_popup_blocks_attach_and_position_loop(self):
        windows = FakeWindows()
        windows.popup = 77
        dock = make_dock(windows)
        self.assertFalse(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        self.assertEqual(windows.calls, [])
        windows.popup = None
        self.assertTrue(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        # native popup opens later: position loop must pause, not cover it
        windows.popup = 77
        windows.rectangles[10] = (250, 260, 850, 660)
        windows.calls.clear()
        for _ in range(5):
            self.assertFalse(dock.align())
        self.assertEqual(windows.set_pos_calls(), [])
        windows.popup = None
        self.assertTrue(dock.align())
        self.assertEqual(windows.rect(10), windows.rect(30))

    def test_align_repairs_zorder_even_when_geometry_is_unchanged(self):
        windows = FakeWindows()
        dock = make_dock(windows)
        self.assertTrue(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        windows.zorder_ok = False  # a third window was inserted between host and target
        windows.calls.clear()
        self.assertTrue(dock.align())
        set_pos_calls = windows.set_pos_calls()
        self.assertEqual(len(set_pos_calls), 1, "Z order must be repaired without a geometry change")
        self.assertEqual(set_pos_calls[0][1], 20)
        self.assertTrue(windows.zorder_ok)

    def test_align_does_not_fight_the_user_dragging_the_window(self):
        windows = FakeWindows()
        dock = make_dock(windows)
        self.assertTrue(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        windows.rectangles[10] = (260, 240, 860, 640)  # user drags WeChat
        windows.foreground = 10
        windows.calls.clear()
        self.assertTrue(dock.align())
        self.assertEqual(windows.set_pos_calls(), [])

    def test_detach_restores_original_geometry_and_minimized_state(self):
        windows = FakeWindows()
        windows.rectangles[30] = (100, 120, 700, 520)  # valid aperture (600x400)
        windows.minimized.add(10)
        dock = make_dock(windows)
        self.assertTrue(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        self.assertNotIn(10, windows.minimized)
        dock.detach()
        self.assertEqual(windows.rect(10), (10, 20, 810, 620))
        self.assertIn(10, windows.minimized)
        self.assertEqual(windows.minimize_calls(), [("minimize", 10, False),
                                                    ("minimize", 10, True)])

    def test_unverifiable_zorder_fails_closed_without_position_loop(self):
        windows = FakeWindows()
        windows.keep_zorder_broken = True
        dock = make_dock(windows)
        self.assertFalse(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        self.assertFalse(dock.active)
        # place + one repair attempt + rollback, never an endless loop
        self.assertEqual(len(windows.set_pos_calls()), 3)
        self.assertEqual(windows.rect(10), (10, 20, 810, 620))
        windows.calls.clear()
        self.assertFalse(dock.attach(10, host_hwnd=20, aperture_hwnd=30))
        self.assertLessEqual(len(windows.set_pos_calls()), 3)


class WindowApiSafetyTests(unittest.TestCase):
    FORBIDDEN_CALLS = ("SetParent(", "SetForegroundWindow(", "AttachThreadInput(",
                       "SetWindowsHookEx(", "SetWindowLongPtr(")
    WINDOW_MODULES = ("window_dock.py", "window_manager.py", "discovery.py",
                      "recovery_guard.py", "worker.py")

    def test_window_code_never_reparents_activates_or_hooks_a_target(self):
        for name in self.WINDOW_MODULES:
            path = ROOT / "liteapp" / name
            text = path.read_text(encoding="utf-8")
            for forbidden in self.FORBIDDEN_CALLS:
                with self.subTest(module=name, api=forbidden):
                    self.assertNotIn(forbidden, text,
                                     f"{name} must not call {forbidden}")

    def test_placement_flags_never_activate_or_show_the_target(self):
        flags = window_dock.NativeWindows.placement_flags(20)
        self.assertTrue(flags & window_dock.NativeWindows.SWP_NOACTIVATE)
        self.assertTrue(flags & window_dock.NativeWindows.SWP_NOOWNERZORDER)
        self.assertFalse(flags & window_dock.NativeWindows.SWP_SHOWWINDOW)
        flags_without_zorder = window_dock.NativeWindows.placement_flags(None)
        self.assertTrue(flags_without_zorder & window_dock.NativeWindows.SWP_NOZORDER)


if __name__ == "__main__":
    unittest.main()
