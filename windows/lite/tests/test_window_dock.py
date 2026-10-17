import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from liteapp.window_dock import WindowDock


class FakeWindows:
    def __init__(self):
        self.rectangles = {10: (10, 20, 810, 620), 20: (0, 0, 1000, 800),
                           30: (100, 120, 700, 520)}
        self.calls = []
        self.minimized = set()

    def rect(self, hwnd):
        return self.rectangles[hwnd]

    def is_window(self, hwnd):
        return hwnd in self.rectangles

    def set_pos(self, hwnd, after, rect):
        self.calls.append((hwnd, after, rect))
        self.rectangles[hwnd] = rect
        return True

    def set_minimized(self, hwnd, value):
        if value:
            self.minimized.add(hwnd)
        else:
            self.minimized.discard(hwnd)
        return True


class DockTests(unittest.TestCase):
    def test_dock_preserves_top_level_identity_and_restores_original_geometry(self):
        windows = FakeWindows()
        dock = WindowDock(api=windows, validate=lambda target: True)
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
        dock = WindowDock(api=windows, validate=lambda target: False)
        self.assertFalse(dock.attach(10, 20, 30))
        self.assertEqual(windows.calls, [])

    def test_app_can_hide_and_restore_its_connected_wechat_window(self):
        windows = FakeWindows()
        dock = WindowDock(api=windows, validate=lambda target: target == 10)
        self.assertTrue(dock.set_minimized(10, True))
        self.assertIn(10, windows.minimized)
        self.assertTrue(dock.set_minimized(10, False))
        self.assertNotIn(10, windows.minimized)
        self.assertFalse(dock.set_minimized(999, True))


if __name__ == "__main__":
    unittest.main()
