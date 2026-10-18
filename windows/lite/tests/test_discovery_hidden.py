"""S-WECHAT hidden/tray discovery and restore tests (task-17).

User report: after WeChat is hidden from the taskbar / moved to the tray the app
can no longer find it, so pressing "启动微信" starts a second instance.

The APIs under test (all read-only except the explicit ``restore_window`` call):

* ``discovery.enumerate_processes()`` / ``running_wechat_paths()`` /
  ``process_running(path)`` -- decide whether the configured WeChat executable is
  already running, so the UI never launches a second copy;
* ``discovery.find_wechat_processes(path)`` / ``find_wechat_windows(path)`` --
  process + window candidates **including hidden/minimized windows**
  (``IsWindowVisible == false``), filtered to WeChat chat/login windows only;
* ``discovery.discover(path, include_hidden=True)`` -- existing discovery with an
  opt-in for hidden windows; default behaviour stays visible-only (0.2 compatible);
* ``discovery.wechat_presence(path)`` -- one aggregate answer for the launch
  button (running / hidden windows / restorable / tray-only);
* ``discovery.restore_window(window, activate=False)`` -- show a hidden or
  minimized window on explicit user action, without stealing focus, without
  touching any other window, no loops, no SetParent.

These tests use real **synthetic** Win32 windows (own process fixture) or a fake
process snapshot; the real WeChat client is never discovered, moved or restored.
"""

import ctypes
import os
import subprocess
import sys
import unittest
from ctypes import wintypes as W
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liteapp import discovery  # noqa: E402
from liteapp.contracts import normalize_path  # noqa: E402
from fixtures import native_chat_fixture as native  # noqa: E402

SW_HIDE = 0
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_RESTORE = 9
CREATE_NO_WINDOW = 0x08000000

WEIXIN = r"C:\Program Files\Tencent\Weixin\Weixin.exe"


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.argtypes = [W.HWND, ctypes.c_int]
    user32.ShowWindow.restype = W.BOOL
    user32.IsWindowVisible.argtypes = [W.HWND]
    user32.IsWindowVisible.restype = W.BOOL
    return user32


def show_window(hwnd, command):
    return bool(_user32().ShowWindow(int(hwnd), command))


def is_window_visible(hwnd):
    return bool(_user32().IsWindowVisible(int(hwnd)))


def hide_window(hwnd):
    assert show_window(hwnd, SW_HIDE)


class NativeFixtureTestCase(unittest.TestCase):
    def setUp(self):
        self._fixtures = []
        self._processes = []

    def tearDown(self):
        for fixture in reversed(self._fixtures):
            try:
                fixture.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask failures
                pass
        for process in self._processes:
            try:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass

    def spawn(self, **kwargs):
        fixture = native.NativeChatFixture(**kwargs)
        try:
            fixture.start()
        except (TimeoutError, RuntimeError, OSError) as exc:
            raise unittest.SkipTest(f"interactive Win32 window unavailable: {exc}") from None
        self._fixtures.append(fixture)
        return fixture

    def spawn_sleeper(self, seconds=60):
        process = subprocess.Popen(
            [sys.executable, "-c", f"import time; time.sleep({seconds})"],
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        self._processes.append(process)
        return process

    def identity(self, fixture):
        identity = discovery.process_identity(fixture.pid)
        self.assertIsNotNone(identity)
        return identity

    def wechat_fixture(self, **kwargs):
        kwargs.setdefault("title", "微信")
        kwargs.setdefault("rect", (220, 220, 640, 520))
        return self.spawn(**kwargs)


# ---------------------------------------------------------------------------
# Process detection (fake snapshot + real process enumeration)
# ---------------------------------------------------------------------------

class ProcessDetectionTests(unittest.TestCase):
    def test_enumerate_processes_includes_current_process(self):
        entries = discovery.enumerate_processes()
        current = [entry for entry in entries if entry["pid"] == os.getpid()]
        self.assertTrue(current, "the running test process must be enumerated")
        self.assertTrue(current[0]["path"])
        self.assertTrue(current[0]["exe_name"])

    def test_running_wechat_paths_recognizes_official_executable_names(self):
        weixin = normalize_path(r"C:\Program Files\Tencent\Weixin\Weixin.exe")
        wechat = normalize_path(r"D:\WeChat\WeChat.exe")
        fake_entries = [
            {"pid": 11, "exe_name": "Weixin.exe", "path": weixin},
            {"pid": 12, "exe_name": "wechat.exe", "path": wechat},
            {"pid": 13, "exe_name": "python.exe", "path": normalize_path(r"C:\Python\python.exe")},
        ]
        with patch.object(discovery, "enumerate_processes", return_value=fake_entries):
            self.assertEqual(discovery.running_wechat_paths(), sorted([weixin, wechat]))

    def test_process_running_matches_normalized_configured_path(self):
        configured = normalize_path(r"C:\Program Files\Tencent\Weixin\Weixin.exe")
        fake_entries = [{"pid": 11, "exe_name": "Weixin.exe", "path": configured}]
        with patch.object(discovery, "enumerate_processes", return_value=fake_entries):
            self.assertTrue(discovery.process_running(
                r"c:/program files/tencent/weixin/WEIXIN.EXE"))
            self.assertFalse(discovery.process_running(r"C:\Other\Weixin.exe"))
            self.assertFalse(discovery.process_running(""))
            self.assertFalse(discovery.process_running(None))

    def test_find_wechat_processes_skips_unrelated_processes(self):
        weixin = normalize_path(WEIXIN)
        fake_entries = [
            {"pid": 11, "exe_name": "Weixin.exe", "path": weixin},
            {"pid": 12, "exe_name": "python.exe", "path": normalize_path(r"C:\Python\python.exe")},
        ]
        identities = {
            11: {"pid": 11, "path": weixin, "created": 111},
            12: {"pid": 12, "path": normalize_path(r"C:\Python\python.exe"), "created": 222},
        }
        with patch.object(discovery, "enumerate_processes", return_value=fake_entries), \
                patch.object(discovery, "process_identity", side_effect=identities.get), \
                patch.object(discovery, "_top_level_windows_for_process", return_value=[]):
            processes = discovery.find_wechat_processes(WEIXIN)
        self.assertEqual([entry["pid"] for entry in processes], [11])
        self.assertEqual(processes[0]["windows"], [])

    def test_find_wechat_processes_without_matching_process_is_empty(self):
        fake_entries = [{"pid": 12, "exe_name": "python.exe",
                         "path": normalize_path(r"C:\Python\python.exe")}]
        with patch.object(discovery, "enumerate_processes", return_value=fake_entries):
            self.assertEqual(discovery.find_wechat_processes(WEIXIN), [])
            self.assertEqual(discovery.find_wechat_windows(WEIXIN), [])
            presence = discovery.wechat_presence(WEIXIN)
            self.assertFalse(presence["running"])
            self.assertFalse(presence["tray_only"])
            self.assertFalse(presence["restorable"])
            self.assertEqual(presence["windows"], [])


# ---------------------------------------------------------------------------
# Hidden / minimized window discovery (real synthetic windows)
# ---------------------------------------------------------------------------

class HiddenWindowDiscoveryTests(NativeFixtureTestCase):
    def test_hidden_window_is_found_only_with_include_hidden(self):
        fixture = self.wechat_fixture()
        identity = self.identity(fixture)
        hide_window(fixture.hwnd)
        self.assertFalse(is_window_visible(fixture.hwnd))
        try:
            default_items = discovery.discover(identity["path"])
            self.assertFalse(any(int(item["hwnd"]) == int(fixture.hwnd)
                                 for item in default_items),
                             "0.2 default discovery must stay visible-only")
            hidden_items = discovery.discover(identity["path"], include_hidden=True)
            matches = [item for item in hidden_items
                       if int(item["hwnd"]) == int(fixture.hwnd)]
            self.assertEqual(len(matches), 1, "hidden top-level WeChat window must be found")
            item = matches[0]
            self.assertFalse(item["visible"])
            self.assertTrue(item["hidden"])
            self.assertFalse(item["minimized"])
            self.assertFalse(item["cloaked"])
            self.assertEqual(item["status"], "hidden")
            self.assertEqual(item["kind"], "chat")
            self.assertEqual(item["title"], "微信")
        finally:
            show_window(fixture.hwnd, SW_SHOWNOACTIVATE)

    def test_minimized_window_is_marked_minimized(self):
        fixture = self.wechat_fixture()
        identity = self.identity(fixture)
        fixture.command("minimize")
        self.assertTrue(fixture.state()["minimized"])
        try:
            items = discovery.discover(identity["path"])
            matches = [item for item in items if int(item["hwnd"]) == int(fixture.hwnd)]
            self.assertEqual(len(matches), 1, "minimized windows stay visible for discovery")
            item = matches[0]
            self.assertTrue(item["minimized"])
            self.assertTrue(item["visible"])
            self.assertEqual(item["status"], "minimized")
            self.assertTrue(discovery.restore_window(item["hwnd"]))
            self.assertFalse(fixture.state()["minimized"])
        finally:
            show_window(fixture.hwnd, SW_SHOWNOACTIVATE)

    def test_window_info_marks_owned_toolwindow_popup(self):
        fixture = self.wechat_fixture()
        fixture.command("popup")
        try:
            popup_hwnd = int(fixture.state()["popup"])
            self.assertNotEqual(popup_hwnd, 0)
            info = discovery.window_info(popup_hwnd)
            self.assertIsNotNone(info)
            self.assertEqual(info["kind"], "popup")
            self.assertTrue(info["owner"])
            self.assertTrue(info["tool_window"])
            self.assertIn(info["status"], ("visible", "minimized", "hidden"))
            self.assertFalse(info["cloaked"])
        finally:
            fixture.command("close_popup")

    def test_find_wechat_processes_reports_hidden_window_candidates(self):
        fixture = self.wechat_fixture()
        identity = self.identity(fixture)
        hide_window(fixture.hwnd)
        try:
            processes = discovery.find_wechat_processes(identity["path"])
            entries = [entry for entry in processes if entry["pid"] == fixture.pid]
            self.assertEqual(len(entries), 1)
            entry = entries[0]
            self.assertTrue(entry["has_window"])
            self.assertGreaterEqual(entry["hidden_windows"], 1)
            windows = [window for window in entry["windows"]
                       if int(window["hwnd"]) == int(fixture.hwnd)]
            self.assertEqual(len(windows), 1)
            self.assertEqual(windows[0]["status"], "hidden")
            self.assertIsInstance(windows[0]["hwnd"], discovery.WindowTarget)
            self.assertTrue(discovery.restore_window(windows[0]["hwnd"]))
            self.assertTrue(is_window_visible(fixture.hwnd))
        finally:
            show_window(fixture.hwnd, SW_SHOWNOACTIVATE)

    def test_find_wechat_windows_excludes_unrelated_windows(self):
        chat = self.wechat_fixture()
        marker = self.spawn(mode="marker", color="#FF00FF", rect=(900, 500, 300, 300))
        chat_identity = self.identity(chat)
        processes = discovery.find_wechat_processes(chat_identity["path"])
        all_windows = [window for entry in processes for window in entry["windows"]]
        self.assertTrue(any(int(window["hwnd"]) == int(chat.hwnd) for window in all_windows))
        self.assertFalse(any(int(window["hwnd"]) == int(marker.hwnd) for window in all_windows),
                         "windows that are not WeChat chat/login windows must be ignored")
        marker_entry = [entry for entry in processes if entry["pid"] == marker.pid]
        if marker_entry:
            self.assertEqual(marker_entry[0]["windows"], [])

    def test_presence_reports_hidden_window_as_restorable(self):
        fixture = self.wechat_fixture()
        identity = self.identity(fixture)
        hide_window(fixture.hwnd)
        try:
            presence = discovery.wechat_presence(identity["path"])
            self.assertTrue(presence["running"], "a hidden WeChat process is still running")
            self.assertFalse(presence["tray_only"])
            self.assertTrue(presence["restorable"])
            self.assertGreaterEqual(presence["hidden_windows"], 1)
            hidden = [window for window in presence["windows"] if not window["visible"]]
            self.assertEqual(len(hidden), 1)
            self.assertEqual(hidden[0]["status"], "hidden")
        finally:
            show_window(fixture.hwnd, SW_SHOWNOACTIVATE)

    def test_presence_visible_window_is_not_tray_only(self):
        fixture = self.wechat_fixture()
        presence = discovery.wechat_presence(self.identity(fixture)["path"])
        self.assertTrue(presence["running"])
        self.assertFalse(presence["tray_only"])
        self.assertTrue(presence["restorable"])
        visible = [window for window in presence["windows"] if window["visible"]]
        self.assertEqual(len(visible), 1)
        self.assertEqual(visible[0]["status"], "visible")

    def test_tray_only_process_without_windows(self):
        sleeper = self.spawn_sleeper()
        identity = discovery.process_identity(sleeper.pid)
        self.assertIsNotNone(identity)
        with patch.object(discovery, "_top_level_windows_for_process", return_value=[]):
            presence = discovery.wechat_presence(identity["path"])
        self.assertTrue(presence["running"], "process exists but has no top-level window")
        self.assertTrue(presence["tray_only"])
        self.assertFalse(presence["restorable"])
        self.assertEqual(presence["windows"], [])


# ---------------------------------------------------------------------------
# Explicit restore: show hidden/minimized without stealing focus; failures safe
# ---------------------------------------------------------------------------

class RestoreWindowTests(NativeFixtureTestCase):
    def hidden_item(self, fixture):
        identity = self.identity(fixture)
        items = discovery.discover(identity["path"], include_hidden=True)
        return next(item for item in items if int(item["hwnd"]) == int(fixture.hwnd))

    def test_restore_hidden_window_without_stealing_focus(self):
        fixture = self.wechat_fixture()
        hide_window(fixture.hwnd)
        item = self.hidden_item(fixture)
        foreground_before = discovery.foreground_window()
        self.assertTrue(discovery.restore_window(item["hwnd"]))
        self.assertTrue(is_window_visible(fixture.hwnd))
        self.assertEqual(discovery.foreground_window(), foreground_before,
                         "restoring must not change the foreground window")

    def test_restore_accepts_dict_item_from_discovery(self):
        fixture = self.wechat_fixture()
        hide_window(fixture.hwnd)
        item = self.hidden_item(fixture)
        self.assertTrue(discovery.restore_window(item))
        self.assertTrue(is_window_visible(fixture.hwnd))

    def test_restore_minimized_window_without_forced_foreground_call(self):
        fixture = self.wechat_fixture()
        fixture.command("minimize")
        identity = self.identity(fixture)
        item = next(item for item in discovery.discover(identity["path"])
                    if int(item["hwnd"]) == int(fixture.hwnd))
        self.assertTrue(discovery.restore_window(item["hwnd"]))
        self.assertFalse(fixture.state()["minimized"])
        # Windows itself may activate a window when un-minimizing it even for
        # SW_SHOWNOACTIVATE (observed); the API still never loops and never
        # calls a force-foreground API. Hidden-window restore is asserted to
        # keep the foreground window unchanged above.

    def test_restore_activate_true_restores_window_on_explicit_action(self):
        fixture = self.wechat_fixture()
        hide_window(fixture.hwnd)
        item = self.hidden_item(fixture)
        self.assertTrue(discovery.restore_window(item["hwnd"], activate=True))
        self.assertTrue(is_window_visible(fixture.hwnd))

    def test_restore_rejects_invalid_or_reused_handles(self):
        fixture = self.wechat_fixture()
        hide_window(fixture.hwnd)
        identity = self.identity(fixture)
        stale_created = identity["created"] + 1_000_000
        stale = discovery.WindowTarget(fixture.hwnd, fixture.pid, identity["path"],
                                       stale_created)
        try:
            with patch.object(discovery.u32, "ShowWindow") as show:
                self.assertFalse(discovery.restore_window(stale),
                                 "a reused/stale process identity must not be restored")
                self.assertFalse(discovery.restore_window(0))
                self.assertFalse(discovery.restore_window(999999))
                self.assertFalse(discovery.restore_window(None))
            show.assert_not_called()
            self.assertFalse(is_window_visible(fixture.hwnd))
        finally:
            show_window(fixture.hwnd, SW_SHOWNOACTIVATE)

    def test_restore_reports_failure_when_window_stays_hidden(self):
        fixture = self.wechat_fixture()
        hide_window(fixture.hwnd)
        item = self.hidden_item(fixture)
        try:
            with patch.object(discovery.u32, "ShowWindow", return_value=False):
                self.assertFalse(discovery.restore_window(item["hwnd"]))
            self.assertFalse(is_window_visible(fixture.hwnd))
        finally:
            show_window(fixture.hwnd, SW_SHOWNOACTIVATE)

    def test_restore_cloaked_window_fails_without_show_attempt(self):
        fixture = self.wechat_fixture()
        hide_window(fixture.hwnd)
        item = self.hidden_item(fixture)
        try:
            with patch.object(discovery, "window_cloaked", return_value=True), \
                    patch.object(discovery.u32, "ShowWindow") as show:
                self.assertFalse(discovery.restore_window(item["hwnd"]),
                                 "a DWM-cloaked window cannot be safely restored")
            show.assert_not_called()
            self.assertFalse(is_window_visible(fixture.hwnd))
        finally:
            show_window(fixture.hwnd, SW_SHOWNOACTIVATE)


# ---------------------------------------------------------------------------
# 0.2 compatibility: default discovery output keeps working
# ---------------------------------------------------------------------------

class DiscoveryCompatibilityTests(NativeFixtureTestCase):
    def test_default_discovery_keeps_visible_window_behaviour(self):
        fixture = self.wechat_fixture()
        identity = self.identity(fixture)
        items = discovery.discover(identity["path"])
        matches = [item for item in items if int(item["hwnd"]) == int(fixture.hwnd)]
        self.assertEqual(len(matches), 1)
        item = matches[0]
        for key in ("id", "hwnd", "pid", "path", "created", "title", "class", "kind",
                    "dpi", "minimized"):
            self.assertIn(key, item)
        self.assertEqual(item["kind"], "chat")
        self.assertTrue(item["visible"])
        self.assertFalse(item["hidden"])
        self.assertEqual(item["status"], "visible")

    def test_include_hidden_does_not_change_visible_item_core_fields(self):
        fixture = self.wechat_fixture()
        identity = self.identity(fixture)
        default_item = next(item for item in discovery.discover(identity["path"])
                            if int(item["hwnd"]) == int(fixture.hwnd))
        hidden_item = next(item for item in discovery.discover(
            identity["path"], include_hidden=True)
            if int(item["hwnd"]) == int(fixture.hwnd))
        for key in ("id", "pid", "path", "created", "title", "class", "kind", "dpi",
                    "minimized"):
            self.assertEqual(default_item[key], hidden_item[key], key)



class OffscreenWindowTests(NativeFixtureTestCase):
    def parked(self):
        fixture = self.wechat_fixture()
        self.assertTrue(discovery.u32.SetWindowPos(fixture.hwnd, 0, -25600, -25600,
                                                  0, 0, 0x0015))
        return fixture

    def test_parked_visible_window_is_hidden_in_status_and_discovery(self):
        fixture = self.parked()
        self.assertTrue(is_window_visible(fixture.hwnd))
        self.assertEqual(discovery.window_status(fixture.hwnd), 'hidden')
        info = discovery.window_info(fixture.hwnd)
        self.assertTrue(info['hidden'])
        self.assertFalse(info['visible'])
        path = self.identity(fixture)['path']
        self.assertFalse(any(int(item['hwnd']) == fixture.hwnd
                             for item in discovery.discover(path)))
        self.assertTrue(any(int(item['hwnd']) == fixture.hwnd
                            for item in discovery.discover(path, include_hidden=True)))

    def test_explicit_restore_moves_parked_window_back_to_screen(self):
        fixture = self.parked()
        self.assertTrue(discovery.restore_window(fixture.hwnd, activate=True))
        self.assertEqual(discovery.window_status(fixture.hwnd), 'visible')
        self.assertGreater(discovery.window_rect(fixture.hwnd)[0], -25600)

    def test_nonactivating_restore_does_not_move_parked_window(self):
        fixture = self.parked()
        before = discovery.window_rect(fixture.hwnd)
        discovery.restore_window(fixture.hwnd)
        self.assertEqual(discovery.window_rect(fixture.hwnd), before)

    def test_hidden_parked_window_restores_on_explicit_action(self):
        fixture = self.parked()
        hide_window(fixture.hwnd)
        self.assertEqual(discovery.window_status(fixture.hwnd), 'hidden')
        self.assertTrue(discovery.restore_window(fixture.hwnd, activate=True))
        self.assertEqual(discovery.window_status(fixture.hwnd), 'visible')

    def test_minimized_parked_window_keeps_minimized_status_then_restores(self):
        fixture = self.parked()
        fixture.command('minimize')
        self.assertEqual(discovery.window_status(fixture.hwnd), 'minimized')
        self.assertTrue(discovery.restore_window(fixture.hwnd, activate=True))
        self.assertEqual(discovery.window_status(fixture.hwnd), 'visible')

class OffscreenUnitTests(unittest.TestCase):
    def setUp(self):
        self.rect = (-25600, -25600, -24960, -25080)
        self.visible = True
        self.iconic = False
        def move(hwnd, after, x, y, cx, cy, flags):
            self.rect = (x, y, x + 640, y + 520)
            return 1
        def show(hwnd, command):
            self.visible = True
            self.iconic = False
            return 1
        def work_area(action, param, output, flags):
            rect = ctypes.cast(output, ctypes.POINTER(W.RECT)).contents
            rect.left, rect.top, rect.right, rect.bottom = 0, 0, 1920, 1040
            return 1
        patches = [
            patch.object(discovery.u32, 'SystemParametersInfoW', side_effect=work_area),
            patch.object(discovery, '_restore_target', return_value=123),
            patch.object(discovery, 'window_cloaked', return_value=False),
            patch.object(discovery, 'window_rect', side_effect=lambda hwnd: self.rect),
            patch.object(discovery.u32, 'IsWindowVisible', side_effect=lambda hwnd: self.visible),
            patch.object(discovery.u32, 'IsIconic', side_effect=lambda hwnd: self.iconic),
            patch.object(discovery.u32, 'MonitorFromRect', side_effect=lambda rect, flags: int(self.rect[0] > -20000)),
            patch.object(discovery.u32, 'SetWindowPos', side_effect=move),
            patch.object(discovery.u32, 'ShowWindow', side_effect=show),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_parked_status_is_hidden(self):
        self.assertEqual(discovery.window_status(123), 'hidden')

    def test_restore_active_recovers_parked_hidden_and_minimized(self):
        for visible, iconic in [(True, False), (False, False), (True, True)]:
            with self.subTest(visible=visible, iconic=iconic):
                self.rect = (-25600, -25600, -24960, -25080)
                self.visible, self.iconic = visible, iconic
                self.assertTrue(discovery.restore_window(123, activate=True))
                self.assertEqual(discovery.window_status(123), 'visible')
                self.assertGreaterEqual(self.rect[0], 0)

    def test_default_restore_preserves_position(self):
        before = self.rect
        discovery.restore_window(123)
        self.assertEqual(self.rect, before)
        discovery.u32.SetWindowPos.assert_not_called()

    def test_minimized_and_hidden_status_preserved(self):
        self.iconic = True
        self.assertEqual(discovery.window_status(123), 'minimized')
        self.iconic, self.visible = False, False
        self.assertEqual(discovery.window_status(123), 'hidden')

    def test_snapshot_and_discovery_exclude_parked_visible_window(self):
        identity = {'pid': 7, 'path': normalize_path(WEIXIN), 'created': 9}
        with patch.object(discovery.u32, 'IsWindow', return_value=True), \
                patch.object(discovery, 'window_pid', return_value=7), \
                patch.object(discovery, 'window_title', return_value='\u5fae\u4fe1'), \
                patch.object(discovery, 'window_class_name', return_value='WeChatMainWnd'), \
                patch.object(discovery, 'window_owner', return_value=0), \
                patch.object(discovery.u32, 'GetWindow', return_value=0), \
                patch.object(discovery.u32, 'GetWindowLongW', return_value=0), \
                patch.object(discovery, 'dpi', return_value=96), \
                patch.object(discovery, 'process_identity', return_value=identity), \
                patch.object(discovery, '_collect_windows', side_effect=lambda collect: collect(123)):
            info = discovery.window_info(123)
            self.assertEqual(info['status'], 'hidden')
            self.assertTrue(info['hidden'])
            self.assertFalse(info['visible'])
            self.assertEqual(discovery.discover(WEIXIN), [])
            items = discovery.discover(WEIXIN, include_hidden=True)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]['status'], 'hidden')

    def test_monitor_intersection_keeps_partial_or_secondary_window_visible(self):
        discovery.u32.MonitorFromRect.side_effect = None
        discovery.u32.MonitorFromRect.return_value = 1
        self.rect = (-640, 0, 20, 520)
        self.assertEqual(discovery.window_status(123), 'visible')
        self.assertTrue(discovery.restore_window(123, activate=True))
        discovery.u32.SetWindowPos.assert_not_called()

    def test_active_restore_moves_before_show_and_preserves_size_and_zorder(self):
        events = []
        move = discovery.u32.SetWindowPos.side_effect
        show = discovery.u32.ShowWindow.side_effect
        def ordered_move(*args):
            events.append('move')
            return move(*args)
        def ordered_show(*args):
            events.append('show')
            return show(*args)
        discovery.u32.SetWindowPos.side_effect = ordered_move
        discovery.u32.ShowWindow.side_effect = ordered_show
        self.assertTrue(discovery.restore_window(123, activate=True))
        self.assertEqual(events, ['move', 'show'])
        discovery.u32.SetWindowPos.assert_called_once_with(123, 0, 0, 0, 0, 0, 0x0015)

    def test_position_failure_reports_false(self):
        discovery.u32.SetWindowPos.side_effect = None
        discovery.u32.SetWindowPos.return_value = 0
        self.assertFalse(discovery.restore_window(123, activate=True))


if __name__ == "__main__":
    unittest.main()
