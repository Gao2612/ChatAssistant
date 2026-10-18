"""T4 acceptance tests: native docking, focus, minimize and crash recovery.

These tests are written before the implementation (PLAN T4 task-first loop). See
``docs/windows/WINDOWS_LITE_COMPLETION_PLAN_2026-10-17.md`` T4 for the checklist:

* reused HWND / process identity must never be operated on;
* the assistant restores only windows it manages;
* an existing native popup is never covered by the position loop;
* DIP <-> physical pixel round trips stay within 2 physical pixels;
* an external synthetic chat window (own process, Win32 only) drives geometry,
  popup, resize, minimize, close, heartbeats and static-vs-changed frames;
* occlusion regression: geometry unchanged, a third window inserted between
  host and WeChat; after activating the assistant the selected HWND must be
  directly below the host and its capture frame must not contain the third
  window's marker;
* recovery guard restores geometry only from records signed by this assistant,
  after re-verifying process identity; a closed/reused WeChat HWND is untouched.

No test touches the real WeChat process, reads chat content or presses send.
"""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from ctypes import wintypes as W
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from liteapp import discovery  # noqa: E402
from liteapp import recovery_guard  # noqa: E402
from liteapp import window_dock  # noqa: E402
from liteapp import window_manager  # noqa: E402
from liteapp.contracts import SourceKey  # noqa: E402
from fixtures import native_chat_fixture as native  # noqa: E402


def _make_dpi_aware():
    """Mirror the real application: geometry read here is physical pixels."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


_make_dpi_aware()

WEIXIN_EXE = r"C:\Program Files\Tencent\Weixin\Weixin.exe"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def source_for(fixture) -> SourceKey:
    identity = discovery.process_identity(fixture.pid)
    if not identity:
        raise AssertionError("could not read synthetic window process identity")
    return SourceKey(identity["path"], identity["pid"], identity["created"], int(fixture.hwnd))


def fixture_rect(fixture):
    return tuple(fixture.state()["rect"])


def assert_rect_close(testcase, actual, expected, tolerance=2):
    testcase.assertEqual(len(actual), len(expected))
    for a, b in zip(actual, expected):
        testcase.assertLessEqual(abs(int(a) - int(b)), tolerance,
                                 f"rect {actual} differs from {expected} by more than {tolerance}px")


def set_zorder(hwnd, insert_after):
    """Put ``hwnd`` directly below ``insert_after`` in the global Z order."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowPos.argtypes = [W.HWND, W.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, W.UINT]
    user32.SetWindowPos.restype = W.BOOL
    return bool(user32.SetWindowPos(int(hwnd), int(insert_after), 0, 0, 0, 0,
                                    0x0001 | 0x0002 | 0x0010))  # NOSIZE|NOMOVE|NOACTIVATE


def capture_one_frame(hwnd, timeout=15.0):
    """Capture one frame of exactly this HWND through Windows Graphics Capture."""
    try:
        import numpy as np
        from windows_capture import WindowsCapture
    except ImportError as exc:  # pragma: no cover - environment guard
        raise unittest.SkipTest(f"windows_capture unavailable: {exc}") from None
    frames, closed = [], []

    capture = WindowsCapture(cursor_capture=None, draw_border=None, window_hwnd=int(hwnd))

    @capture.event
    def on_frame_arrived(frame, control):
        frames.append(np.array(frame.frame_buffer[:, :, :3], copy=True))
        control.stop()

    @capture.event
    def on_closed():
        closed.append(True)

    control = capture.start_free_threaded()
    deadline = time.monotonic() + timeout
    while not frames and not closed and time.monotonic() < deadline:
        time.sleep(0.02)
    try:
        control.stop()
    except Exception:  # noqa: BLE001 - capture teardown must not mask result
        pass
    if not frames:
        raise AssertionError("no capture frame received from synthetic window")
    return frames[0]


class NativeFixtureTestCase(unittest.TestCase):
    """Spawns real synthetic windows; skips only when no interactive desktop."""

    def setUp(self):
        self._fixtures = []
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        for fixture in reversed(self._fixtures):
            try:
                fixture.close()
            except Exception:  # noqa: BLE001 - cleanup must not mask failures
                pass
        self._tmp.cleanup()

    @property
    def data_dir(self):
        return Path(self._tmp.name)

    def spawn(self, **kwargs):
        fixture = native.NativeChatFixture(**kwargs)
        try:
            fixture.start()
        except (TimeoutError, RuntimeError, OSError) as exc:
            raise unittest.SkipTest(f"interactive Win32 window unavailable: {exc}") from None
        self._fixtures.append(fixture)
        return fixture

    def close_fixture(self, fixture):
        if fixture in self._fixtures:
            self._fixtures.remove(fixture)
        fixture.close()


# ---------------------------------------------------------------------------
# Fake low-level window API used by deterministic manager/dock/recovery tests
# ---------------------------------------------------------------------------

class FakeWindows:
    """In-memory stand-in for ``window_dock.NativeWindows``."""

    def __init__(self):
        self.rectangles = {
            10: (10, 20, 810, 620),
            20: (0, 0, 1200, 900),
            30: (100, 120, 700, 520),
        }
        self.minimized = set()
        self.calls = []
        self.zorder_ok = False
        self.keep_zorder_broken = False
        self.set_pos_result = True
        self.offset = 0
        self.on_set_pos = None

    def is_window(self, hwnd):
        return int(hwnd) in self.rectangles

    def is_minimized(self, hwnd):
        return int(hwnd) in self.minimized

    def set_minimized(self, hwnd, minimized):
        self.calls.append(("minimize", int(hwnd), bool(minimized)))
        if minimized:
            self.minimized.add(int(hwnd))
        else:
            self.minimized.discard(int(hwnd))
        return True

    def rect(self, hwnd):
        return self.rectangles[int(hwnd)]

    def restored_rect(self, hwnd):
        return self.rectangles[int(hwnd)]

    def set_pos(self, hwnd, after, rect):
        self.calls.append(("set_pos", int(hwnd), None if after is None else int(after),
                           tuple(rect)))
        if self.on_set_pos is not None:
            self.on_set_pos()
        if not self.set_pos_result:
            return False
        adjusted = tuple(value + self.offset for value in rect)
        self.rectangles[int(hwnd)] = adjusted
        if after is not None and not self.keep_zorder_broken:
            self.zorder_ok = True
        return True

    # convenience for assertions
    def set_pos_calls(self):
        return [call for call in self.calls if call[0] == "set_pos"]

    def minimize_calls(self):
        return [call for call in self.calls if call[0] == "minimize"]


def make_manager(api, **overrides):
    """WindowManager wired to the fake api and fail-closed probe defaults."""
    values = dict(
        api=api,
        validate=overrides.pop("validate", lambda source: True),
        popup_probe=overrides.pop("popup_probe", lambda hwnd, rect: None),
        zorder_probe=overrides.pop("zorder_probe", None),
        classify_probe=overrides.pop("classify_probe", lambda source: "chat"),
        foreground_probe=overrides.pop("foreground_probe", lambda: 0),
        clock=overrides.pop("clock", None),
    )
    values.update(overrides)
    if values["zorder_probe"] is None:
        values["zorder_probe"] = lambda hwnd, host: api.zorder_ok
    return window_manager.WindowManager(**values)


def fake_source(hwnd=10, pid=456, created=987654321, path=WEIXIN_EXE):
    return SourceKey(path, pid, created, hwnd)


def recovery_record(hwnd=10, rect=(10, 20, 810, 620), minimized=False):
    return recovery_guard.RecoveryRecord(
        source=fake_source(hwnd=hwnd),
        original_rect=tuple(rect),
        original_minimized=minimized,
        recorded_at=1.0,
    )


class RestoreFakeWindows(FakeWindows):
    """Fake api for the recovery restore path (single recorded window)."""

    def __init__(self, hwnd=10, rect=(400, 300, 1100, 800)):
        super().__init__()
        self.rectangles = {hwnd: tuple(rect)}
        self.fail_move = False

    def set_pos(self, hwnd, after, rect):
        self.calls.append(("set_pos", int(hwnd), None if after is None else int(after),
                           tuple(rect)))
        if self.fail_move:
            return False
        self.rectangles[int(hwnd)] = tuple(rect)
        return True


# ---------------------------------------------------------------------------
# Cross-DPI coordinate conversion
# ---------------------------------------------------------------------------

class CoordinateConversionTests(unittest.TestCase):
    def test_dip_to_physical_and_back_within_two_physical_pixels(self):
        rectangles = [
            (0, 0, 800, 600),
            (100, 120, 700, 520),
            (1920, 0, 2560, 1080),
            (7, 13, 451, 301),
            (-200, -100, 400, 300),
        ]
        for dpi_value in (96, 120, 144, 168, 192, 216, 240, 264, 288, 336, 384):
            for rect in rectangles:
                with self.subTest(dpi=dpi_value, rect=rect):
                    physical = window_manager.rect_to_physical(
                        window_manager.rect_to_dip(rect, dpi_value), dpi_value)
                    self.assertLessEqual(
                        window_manager.rect_round_trip_error(rect, dpi_value), 2)
                    for actual, expected in zip(physical, rect):
                        self.assertLessEqual(abs(actual - expected), 2,
                                             f"round trip drifted at {dpi_value} DPI")

    def test_known_scale_values(self):
        self.assertEqual(window_manager.to_physical(100, 96), 100)
        self.assertEqual(window_manager.to_physical(100, 120), 125)
        self.assertEqual(window_manager.to_physical(96, 120), 120)
        self.assertEqual(window_manager.to_physical(100, 144), 150)
        self.assertEqual(round(window_manager.to_dip(125, 120), 6), 100.0)

    def test_invalid_dpi_rejected(self):
        for bad in (0, -96):
            with self.subTest(dpi=bad):
                with self.assertRaises(ValueError):
                    window_manager.to_physical(10, bad)

    def test_tolerance_constant_is_two_physical_pixels(self):
        self.assertEqual(window_manager.GEOMETRY_TOLERANCE_PX, 2)
        self.assertEqual(window_dock.GEOMETRY_TOLERANCE_PX, 2)


# ---------------------------------------------------------------------------
# WindowManager: deterministic fake-api tests
# ---------------------------------------------------------------------------

class WindowManagerFakeTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeWindows()
        self.manager = make_manager(self.api)
        self.source = fake_source()

    def attach(self, **kwargs):
        return self.manager.attach(self.source, (100, 120, 700, 520), **kwargs)

    def test_attach_refuses_invalid_identity_and_never_moves(self):
        manager = make_manager(self.api, validate=lambda source: False)
        status = manager.attach(self.source, (100, 120, 700, 520))
        self.assertFalse(status.ok)
        self.assertEqual(status.state, "refused")
        self.assertEqual(status.reason, "identity")
        self.assertEqual(self.api.calls, [])
        self.assertFalse(manager.attached)

    def test_attach_refuses_too_small_aperture(self):
        status = self.manager.attach(self.source, (100, 120, 200, 150))
        self.assertFalse(status.ok)
        self.assertEqual(status.state, "refused")
        self.assertEqual(status.reason, "aperture_too_small")
        self.assertEqual(self.api.calls, [])

    def test_attach_records_original_geometry_and_restores_on_detach(self):
        original = self.api.rect(10)
        status = self.attach()
        self.assertTrue(status.ok)
        self.assertEqual(status.state, "attached")
        self.assertEqual(status.original_rect, original)
        self.assertEqual(self.api.rect(10), (100, 120, 700, 520))
        self.assertTrue(self.manager.attached)
        self.assertEqual(self.manager.active_source, self.source)

        self.manager.detach()
        self.assertEqual(self.api.rect(10), original)
        self.assertFalse(self.manager.attached)
        self.assertEqual(self.manager.status.state, "detached")

    def test_attach_with_host_verifies_and_repairs_zorder(self):
        self.api.zorder_ok = False
        status = self.manager.attach(self.source, (100, 120, 700, 520), host_hwnd=20)
        self.assertTrue(status.ok)
        self.assertTrue(status.zorder_ok)
        self.assertTrue(status.overlay_safe)
        self.assertFalse(status.read_only)
        # first placement already requested "directly below host"
        self.assertEqual(self.api.set_pos_calls()[0][2], 20)
        self.assertTrue(self.api.zorder_ok)

    def test_attach_readonly_fallback_when_zorder_cannot_be_verified(self):
        self.api.keep_zorder_broken = True
        status = self.manager.attach(self.source, (100, 120, 700, 520), host_hwnd=20)
        self.assertTrue(status.ok)
        self.assertFalse(status.zorder_ok)
        self.assertFalse(status.overlay_safe)
        self.assertTrue(status.read_only)
        # exactly one repair attempt, then fail closed instead of looping
        self.assertEqual(len(self.api.set_pos_calls()), 2)

    def test_attach_writes_signed_recovery_record_before_moving(self):
        data_dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(data_dir, ignore_errors=True))
        store = recovery_guard.RecoveryStore(data_dir)
        original = self.api.rect(10)
        seen = []
        self.api.on_set_pos = lambda: seen.append(len(store.load()))
        manager = make_manager(self.api, recovery_store=store)
        status = manager.attach(self.source, (100, 120, 700, 520))
        self.assertTrue(status.ok)
        self.assertEqual(seen[0], 1, "recovery record must exist before the first move")
        records = store.load()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].original_rect, original)
        self.assertEqual(records[0].source, self.source)
        manager.detach()
        self.assertEqual(store.load(), [])

    def test_attach_fails_closed_when_recovery_record_cannot_be_written(self):
        class FailingStore:
            def remember(self, *_args, **_kwargs):
                raise OSError("disk full")

            def forget(self, *_args, **_kwargs):
                pass

            def clear(self):
                pass

        manager = make_manager(self.api, recovery_store=FailingStore())
        status = manager.attach(self.source, (100, 120, 700, 520))
        self.assertFalse(status.ok)
        self.assertEqual(status.state, "refused")
        self.assertEqual(status.reason, "recovery_unavailable")
        self.assertEqual(self.api.set_pos_calls(), [])
        self.assertEqual(self.api.rect(10), (10, 20, 810, 620))

    def test_attach_refuses_while_native_popup_is_open(self):
        manager = make_manager(self.api, popup_probe=lambda hwnd, rect: 12)
        status = manager.attach(self.source, (100, 120, 700, 520))
        self.assertFalse(status.ok)
        self.assertEqual(status.state, "refused")
        self.assertEqual(status.reason, "popup_open")
        self.assertEqual(status.popup_hwnd, 12)
        self.assertEqual(self.api.set_pos_calls(), [])
        self.assertFalse(manager.attached)

    def test_reattach_same_source_is_idempotent(self):
        self.attach()
        original = self.manager.status.original_rect
        status = self.manager.attach(self.source, (120, 140, 720, 540))
        self.assertTrue(status.ok)
        self.assertEqual(self.manager.status.original_rect, original)
        self.assertEqual(self.api.rect(10), (120, 140, 720, 540))

    def test_login_to_chat_takeover_allowed_for_same_process_instance(self):
        login = fake_source(hwnd=10)
        chat = fake_source(hwnd=30)  # same pid/path/created, new main window
        self.attach()
        status = self.manager.attach(chat, (100, 120, 700, 520))
        self.assertTrue(status.ok)
        self.assertEqual(status.state, "attached")
        self.assertEqual(self.manager.active_source, chat)
        # the old window is restored before the new one is docked
        self.assertEqual(self.api.rect(10), (10, 20, 810, 620))

    def test_takeover_from_live_window_of_another_process_is_refused(self):
        self.attach()
        other = SourceKey(WEIXIN_EXE, 999, 123123123, 30)
        status = self.manager.attach(other, (100, 120, 700, 520))
        self.assertFalse(status.ok)
        self.assertEqual(status.state, "refused")
        self.assertEqual(status.reason, "untrusted_takeover")
        self.assertEqual(self.manager.active_source, self.source)
        self.assertEqual(self.api.rect(30), (100, 120, 700, 520))

    def test_detach_never_touches_unmanaged_windows(self):
        self.attach()
        self.api.calls.clear()
        self.manager.detach()
        for call in self.api.calls:
            self.assertEqual(call[1], 10, f"unmanaged window touched: {call}")

    def test_detach_restore_false_leaves_geometry_and_clears_state(self):
        self.attach()
        before = self.api.rect(10)
        self.api.calls.clear()
        self.manager.detach(restore=False)
        self.assertEqual(self.api.rect(10), before)
        self.assertFalse(self.manager.attached)
        self.assertEqual(self.api.set_pos_calls(), [])

    def test_detach_after_identity_change_does_not_move_reused_handle(self):
        self.attach()
        self.api.calls.clear()
        self.manager._validate = lambda source: False
        self.manager.detach()
        self.assertFalse(self.manager.attached)
        self.assertEqual(self.api.set_pos_calls(), [])
        self.assertEqual(self.api.minimize_calls(), [])

    def test_attach_unminimizes_and_detach_restores_original_minimized_state(self):
        self.api.minimized.add(10)
        status = self.attach()
        self.assertTrue(status.ok)
        self.assertTrue(status.original_minimized)
        self.assertNotIn(10, self.api.minimized)
        self.assertEqual(self.api.minimize_calls()[0], ("minimize", 10, False))
        self.manager.detach()
        self.assertIn(10, self.api.minimized)
        self.assertEqual(self.api.minimize_calls()[-1], ("minimize", 10, True))
        self.assertEqual(self.api.rect(10), (10, 20, 810, 620))

    def test_set_view_hides_and_restores_only_managed_window(self):
        self.attach()
        self.api.calls.clear()
        self.manager.set_view("settings")
        self.assertEqual(self.manager.view, "settings")
        self.assertEqual(self.manager.status.state, "hidden")
        self.assertEqual(self.api.minimize_calls(), [("minimize", 10, True)])
        self.manager.set_view("chat")
        self.assertEqual(self.manager.status.state, "attached")
        self.assertEqual(self.api.minimize_calls()[-1], ("minimize", 10, False))
        for call in self.api.calls:
            self.assertEqual(call[1], 10)

    def test_set_view_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            self.manager.set_view("fullscreen")

    def test_set_view_minimized_hides_like_other_views(self):
        self.attach()
        self.manager.set_view("minimized")
        self.assertEqual(self.manager.status.state, "hidden")
        self.assertEqual(self.api.minimize_calls(), [("minimize", 10, True)])
        self.assertEqual(self.manager.capture_health(self.source).state, "minimized")
        self.manager.set_view("chat")
        self.assertEqual(self.manager.status.state, "attached")
        self.assertNotIn(10, self.api.minimized)

    def test_set_view_when_detached_has_no_effect(self):
        self.manager.set_view("history")
        self.assertEqual(self.api.calls, [])
        self.assertEqual(self.manager.status.state, "detached")

    def test_sync_corrects_drifted_geometry(self):
        self.attach()
        self.api.rectangles[10] = (300, 300, 900, 700)  # user/native resize
        self.api.calls.clear()
        status = self.manager.sync()
        self.assertTrue(status.ok)
        self.assertEqual(self.api.set_pos_calls()[-1][3], (100, 120, 700, 520))
        self.assertEqual(self.api.rect(10), (100, 120, 700, 520))

    def test_sync_pauses_while_popup_open_and_resumes_after_close(self):
        popup = [None]
        manager = make_manager(self.api, popup_probe=lambda hwnd, rect: popup[0])
        manager.attach(self.source, (100, 120, 700, 520))
        self.api.rectangles[10] = (280, 260, 880, 660)  # drifted under popup
        self.api.calls.clear()
        popup[0] = 12
        for _ in range(5):
            status = manager.sync()
            self.assertEqual(status.state, "popup")
            self.assertEqual(status.popup_hwnd, 12)
            self.assertFalse(status.overlay_safe)
            self.assertTrue(status.read_only)
        self.assertEqual(self.api.set_pos_calls(), [])
        self.assertEqual(self.api.rect(10), (280, 260, 880, 660))
        health = manager.capture_health(self.source)
        self.assertEqual(health.state, "popup")
        popup[0] = None
        status = manager.sync()
        self.assertEqual(status.state, "attached")
        self.assertEqual(self.api.rect(10), (100, 120, 700, 520))

    def test_sync_skips_correction_while_user_is_dragging(self):
        manager = make_manager(self.api, foreground_probe=lambda: 10)
        manager.attach(self.source, (100, 120, 700, 520))
        self.api.rectangles[10] = (260, 240, 860, 640)
        self.api.calls.clear()
        status = manager.sync()
        self.assertEqual(status.state, "attached")
        self.assertEqual(self.api.set_pos_calls(), [])
        self.assertEqual(self.api.rect(10), (260, 240, 860, 640))

    def test_sync_repairs_zorder_only_when_group_is_foreground(self):
        foreground = [10]
        manager = make_manager(self.api, foreground_probe=lambda: foreground[0])
        manager.attach(self.source, (100, 120, 700, 520), host_hwnd=20)
        self.api.zorder_ok = False  # third window inserted between host and target
        self.api.calls.clear()
        status = manager.sync()
        self.assertTrue(status.zorder_ok)
        self.assertTrue(status.overlay_safe)
        self.assertEqual(len(self.api.set_pos_calls()), 1)
        self.assertEqual(self.api.set_pos_calls()[0][2], 20)

    def test_sync_marks_readonly_when_foreign_app_is_foreground(self):
        foreground = [999]
        manager = make_manager(self.api, foreground_probe=lambda: foreground[0])
        manager.attach(self.source, (100, 120, 700, 520), host_hwnd=20)
        self.api.zorder_ok = False
        self.api.calls.clear()
        status = manager.sync()
        self.assertFalse(status.zorder_ok)
        self.assertFalse(status.overlay_safe)
        self.assertTrue(status.read_only)
        self.assertEqual(status.reason, "foreign_foreground")
        self.assertEqual(manager.capture_health(self.source).reason,
                         "foreign_foreground")
        self.assertEqual(self.api.set_pos_calls(), [], "must not reorder under a foreign app")
        foreground[0] = 20  # assistant group activated
        status = manager.sync()
        self.assertTrue(status.overlay_safe)
        self.assertEqual(status.reason, "")
        self.assertEqual(len(self.api.set_pos_calls()), 1)

    def test_sync_lost_identity_detaches_without_touching_handle(self):
        self.attach()
        self.api.calls.clear()
        self.manager._validate = lambda source: False
        status = self.manager.sync()
        self.assertFalse(status.ok)
        self.assertEqual(status.state, "lost")
        self.assertEqual(status.reason, "identity")
        self.assertFalse(self.manager.attached)
        self.assertEqual(self.api.set_pos_calls(), [])
        self.assertEqual(self.api.minimize_calls(), [])
        health = self.manager.capture_health(self.source)
        self.assertEqual(health.state, "error")
        self.assertFalse(health.identity_ok)

    def test_on_activated_repairs_zorder_without_stealing_focus(self):
        foreground = [20]
        manager = make_manager(self.api, foreground_probe=lambda: foreground[0])
        manager.attach(self.source, (100, 120, 700, 520), host_hwnd=20)
        self.api.zorder_ok = False
        self.api.calls.clear()
        self.assertTrue(manager.on_activated())
        self.assertTrue(self.api.zorder_ok)
        self.assertEqual(self.api.set_pos_calls()[-1][2], 20)
        self.assertTrue(manager.status.overlay_safe)

    def test_on_activated_keeps_settings_view_hidden(self):
        manager = make_manager(self.api, foreground_probe=lambda: 20)
        manager.attach(self.source, (100, 120, 700, 520), host_hwnd=20)
        manager.set_view("settings")
        self.api.calls.clear()
        self.assertFalse(manager.on_activated())
        self.assertEqual(self.api.minimize_calls(), [])
        self.assertEqual(self.api.set_pos_calls(), [])
        self.assertEqual(manager.view, "settings")

    def test_on_activated_returns_false_without_attachment(self):
        self.assertFalse(self.manager.on_activated())


class WindowManagerHealthTests(unittest.TestCase):
    def setUp(self):
        self.api = FakeWindows()
        self.now = [1000.0]
        self.manager = make_manager(self.api, clock=lambda: self.now[0])
        self.source = fake_source()

    def health(self):
        return self.manager.capture_health(self.source)

    def test_health_requires_source_key(self):
        with self.assertRaises(TypeError):
            self.manager.capture_health(10)

    def test_health_starts_connecting_then_live_then_stale(self):
        self.manager.attach(self.source, (100, 120, 700, 520))
        self.assertEqual(self.health().state, "connecting")
        self.manager.record_frame(self.source)
        health = self.health()
        self.assertEqual(health.state, "live")
        self.assertTrue(health.capture_ok)
        self.assertTrue(health.fresh_frame)
        self.assertFalse(health.changed)
        self.now[0] += 10.0
        health = self.health()
        self.assertEqual(health.state, "stale")
        self.assertFalse(health.capture_ok)
        self.assertFalse(health.fresh_frame)

    def test_static_picture_with_heartbeat_is_live_not_stale(self):
        self.manager.attach(self.source, (100, 120, 700, 520))
        self.manager.record_frame(self.source)
        for _ in range(5):
            self.now[0] += 1.0
            self.manager.record_heartbeat(self.source)
        health = self.health()
        self.assertEqual(health.state, "live", "static screen is not a capture failure")
        self.assertTrue(health.capture_ok)
        self.assertFalse(health.fresh_frame)
        self.assertFalse(health.changed)

    def test_health_marks_content_change_window(self):
        self.manager.attach(self.source, (100, 120, 700, 520))
        self.manager.record_frame(self.source, changed=True)
        self.assertTrue(self.health().changed)
        self.now[0] += 5.0
        self.assertFalse(self.health().changed)

    def test_health_reports_minimized_popup_login_and_error(self):
        self.manager.attach(self.source, (100, 120, 700, 520))
        self.manager.record_frame(self.source)
        self.api.minimized.add(10)
        self.assertEqual(self.health().state, "minimized")
        self.api.minimized.discard(10)

        popup = [None]
        manager = make_manager(self.api, popup_probe=lambda hwnd, rect: popup[0],
                               clock=lambda: self.now[0])
        manager.attach(self.source, (100, 120, 700, 520))
        manager.record_frame(self.source)
        popup[0] = 12
        manager.sync()
        health = manager.capture_health(self.source)
        self.assertEqual(health.state, "popup")
        self.assertTrue(health.read_only)

        login_manager = make_manager(self.api, classify_probe=lambda source: "login",
                                     clock=lambda: self.now[0])
        login_manager.attach(self.source, (100, 120, 700, 520))
        self.assertEqual(login_manager.capture_health(self.source).state, "login")

        lost = make_manager(self.api, validate=lambda source: False)
        health = lost.capture_health(self.source)
        self.assertEqual(health.state, "error")
        self.assertFalse(health.identity_ok)

    def test_health_uses_ocr_time_separately_from_new_frames(self):
        self.manager.attach(self.source, (100, 120, 700, 520))
        self.manager.record_frame(self.source)
        self.now[0] += 1.0
        self.manager.record_ocr(self.source)
        health = self.health()
        self.assertIsNotNone(health.ocr_age)
        self.assertLess(health.ocr_age, 2.0)
        self.assertGreaterEqual(health.heartbeat_age, 1.0)


# ---------------------------------------------------------------------------
# Recovery store: only assistant-signed records are accepted
# ---------------------------------------------------------------------------

class RecoveryStoreTests(unittest.TestCase):
    def setUp(self):
        import shutil

        self.data_dir = Path(tempfile.mkdtemp(prefix="t4-recovery-"))
        self.addCleanup(lambda: shutil.rmtree(self.data_dir, ignore_errors=True))
        self.store = recovery_guard.RecoveryStore(self.data_dir)
        self.source = fake_source(hwnd=10)

    def test_record_roundtrip_and_signature(self):
        record = self.store.remember(self.source, (10, 20, 810, 620), False, at=123.0)
        self.assertEqual(record.source, self.source)
        loaded = self.store.load()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0], record)
        raw = json.loads(self.store.records_path.read_text(encoding="utf-8"))
        self.assertEqual(raw["app_id"], recovery_guard.APP_ID)
        self.assertIn("signature", raw["records"][0])
        self.assertTrue(raw["records"][0]["signature"])

    def test_tampered_record_is_rejected(self):
        self.store.remember(self.source, (10, 20, 810, 620), False, at=123.0)
        raw = json.loads(self.store.records_path.read_text(encoding="utf-8"))
        raw["records"][0]["payload"]["original_rect"][0] += 500
        self.store.records_path.write_text(json.dumps(raw), encoding="utf-8")
        records, rejected = self.store.load_with_rejections()
        self.assertEqual(records, [])
        self.assertEqual(rejected, 1)

    def test_unsigned_foreign_record_is_rejected(self):
        payload = {
            "app_id": recovery_guard.APP_ID,
            "schema_version": recovery_guard.SCHEMA_VERSION,
            "source": {"exe_path": "c:\\other.exe", "pid": 1, "process_started": 2.0,
                       "hwnd": 3},
            "original_rect": [0, 0, 100, 100],
            "original_minimized": False,
            "recorded_at": 1.0,
        }
        foreign = {"app_id": recovery_guard.APP_ID,
                   "schema_version": recovery_guard.SCHEMA_VERSION,
                   "records": [{"payload": payload}]}
        self.store.records_path.write_text(json.dumps(foreign), encoding="utf-8")
        records, rejected = self.store.load_with_rejections()
        self.assertEqual(records, [])
        self.assertEqual(rejected, 1)

    def test_wrong_key_record_is_rejected(self):
        self.store.remember(self.source, (10, 20, 810, 620), False, at=123.0)
        self.store.key_path.write_bytes(os.urandom(32))
        records, rejected = recovery_guard.RecoveryStore(self.data_dir).load_with_rejections()
        self.assertEqual(records, [])
        self.assertEqual(rejected, 1)

    def test_forget_and_clear_remove_records(self):
        self.store.remember(self.source, (10, 20, 810, 620), False, at=123.0)
        self.assertTrue(self.store.forget(self.source))
        self.assertEqual(self.store.load(), [])
        self.store.remember(self.source, (10, 20, 810, 620), False, at=123.0)
        self.store.clear()
        self.assertEqual(self.store.load(), [])

    def test_record_file_never_contains_chat_content_or_screenshots(self):
        self.store.remember(self.source, (10, 20, 810, 620), False, at=123.0)
        text = self.store.records_path.read_text(encoding="utf-8").lower()
        for forbidden in ("message", "screenshot", "title", "nickname", "content",
                          "conversation"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, text)


class RecoveryRestoreTests(unittest.TestCase):
    def setUp(self):
        self.api = RestoreFakeWindows()
        self.record = recovery_record()

    def test_restores_verified_identity_geometry(self):
        report = recovery_guard.restore_records([self.record], api=self.api,
                                                validate=lambda source: True)
        self.assertEqual(report.restored_count, 1)
        self.assertEqual(self.api.rect(10), (10, 20, 810, 620))
        self.assertEqual(self.api.set_pos_calls()[0][2], None)

    def test_closed_window_is_not_touched(self):
        self.api.rectangles = {}
        report = recovery_guard.restore_records([self.record], api=self.api,
                                                validate=lambda source: False)
        self.assertEqual(report.restored_count, 0)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(report.outcomes[0].status, "window_gone")

    def test_identity_mismatch_is_not_touched(self):
        report = recovery_guard.restore_records([self.record], api=self.api,
                                                validate=lambda source: False)
        self.assertEqual(report.restored_count, 0)
        self.assertEqual(self.api.calls, [])
        self.assertEqual(report.outcomes[0].status, "identity_mismatch")

    def test_restores_original_minimized_state(self):
        # Original state was "minimized": the guard restores the rectangle and
        # minimizes the window again (no need to touch visibility first).
        record = recovery_record(minimized=True)
        report = recovery_guard.restore_records([record], api=self.api,
                                                validate=lambda source: True)
        self.assertEqual(report.restored_count, 1)
        self.assertEqual(self.api.rect(10), (10, 20, 810, 620))
        self.assertIn(10, self.api.minimized)

    def test_restores_window_that_was_left_minimized_by_the_assistant(self):
        # Original state was "normal"; the assistant had hidden it while
        # docking, and crashed before restoring. It must become visible again.
        self.api.minimized.add(10)
        report = recovery_guard.restore_records([recovery_record()], api=self.api,
                                                validate=lambda source: True)
        self.assertEqual(report.restored_count, 1)
        self.assertNotIn(10, self.api.minimized)
        self.assertEqual(self.api.minimize_calls(), [("minimize", 10, False)])

    def test_move_failure_is_reported_not_raised(self):
        self.api.fail_move = True
        report = recovery_guard.restore_records([self.record], api=self.api,
                                                validate=lambda source: True)
        self.assertEqual(report.restored_count, 0)
        self.assertEqual(report.outcomes[0].status, "error")


# ---------------------------------------------------------------------------
# WindowManager on real synthetic windows
# ---------------------------------------------------------------------------

class WindowManagerNativeTests(NativeFixtureTestCase):
    def make_native_manager(self, **overrides):
        return window_manager.WindowManager(
            api=window_dock.NativeWindows(),
            validate=overrides.pop("validate", discovery.valid_source),
            popup_probe=overrides.pop("popup_probe", discovery.popup_covering),
            zorder_probe=overrides.pop("zorder_probe", discovery.zorder_ok),
            classify_probe=overrides.pop("classify_probe", discovery.source_kind),
            foreground_probe=overrides.pop("foreground_probe", discovery.foreground_window),
            **overrides)

    def test_attach_detach_real_window_geometry(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        original = fixture_rect(chat)
        source = source_for(chat)
        manager = self.make_native_manager()
        target = (360, 260, 1100, 820)
        status = manager.attach(source, target)
        self.assertTrue(status.ok, status.reason)
        self.assertEqual(status.state, "attached")
        assert_rect_close(self, fixture_rect(chat), target)
        self.assertEqual(status.original_rect, original)
        manager.detach()
        assert_rect_close(self, fixture_rect(chat), original)

    def test_attach_rejects_reused_hwnd_identity(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        before = fixture_rect(chat)
        identity = discovery.process_identity(chat.pid)
        # FILETIME ticks are stored as float in SourceKey; use a delta larger than
        # float resolution at this magnitude so this tests identity reuse, not
        # the documented precision boundary (covered by test_contracts).
        stale = SourceKey(identity["path"], identity["pid"],
                          identity["created"] + 1_000_000, int(chat.hwnd))
        manager = self.make_native_manager()
        status = manager.attach(stale, (360, 260, 1100, 820))
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, "identity")
        assert_rect_close(self, fixture_rect(chat), before)

    def test_attach_unminimizes_then_detach_restores_minimized_state(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        original = fixture_rect(chat)
        source = source_for(chat)
        chat.command("minimize")
        self.assertTrue(chat.state()["minimized"])
        manager = self.make_native_manager()
        status = manager.attach(source, (360, 260, 1100, 820))
        self.assertTrue(status.ok, status.reason)
        self.assertTrue(status.original_minimized)
        self.assertFalse(chat.state()["minimized"])
        manager.set_view("history")
        self.assertTrue(chat.state()["minimized"])
        manager.set_view("chat")
        self.assertFalse(chat.state()["minimized"])
        manager.detach()
        self.assertTrue(chat.state()["minimized"], "original minimized state must be restored")
        chat.command("restore")
        assert_rect_close(self, fixture_rect(chat), original)

    def test_attach_refuses_popup_and_resumes_after_close(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        source = source_for(chat)
        manager = self.make_native_manager()
        chat.command("popup")
        popup_hwnd = int(chat.state()["popup"])
        self.assertNotEqual(popup_hwnd, 0)
        status = manager.attach(source, (360, 260, 1100, 820))
        self.assertFalse(status.ok)
        self.assertEqual(status.state, "refused")
        self.assertEqual(status.reason, "popup_open")
        self.assertEqual(status.popup_hwnd, popup_hwnd)
        chat.command("close_popup")
        status = manager.attach(source, (360, 260, 1100, 820))
        self.assertTrue(status.ok, status.reason)
        assert_rect_close(self, fixture_rect(chat), (360, 260, 1100, 820))

    def test_dpi_round_trip_on_real_window_stays_within_two_pixels(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        dpi_value = discovery.dpi(chat.hwnd)
        self.assertGreaterEqual(dpi_value, 96)
        rect = fixture_rect(chat)
        converted = window_manager.rect_to_physical(
            window_manager.rect_to_dip(rect, dpi_value), dpi_value)
        for actual, expected in zip(converted, rect):
            self.assertLessEqual(abs(actual - expected), 2)
        source = source_for(chat)
        desired_dip = (560.0, 300.0, 1240.0, 800.0)
        desired_physical = window_manager.rect_to_physical(desired_dip, dpi_value)
        manager = self.make_native_manager()
        status = manager.attach(source, desired_physical)
        self.assertTrue(status.ok, status.reason)
        assert_rect_close(self, fixture_rect(chat), desired_physical)

    def test_detach_never_moves_other_windows(self):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        other = self.spawn(mode="marker", color="#00AA00", rect=(900, 500, 300, 300))
        other_rect = fixture_rect(other)
        source = source_for(chat)
        manager = self.make_native_manager()
        self.assertTrue(manager.attach(source, (360, 260, 1100, 820)).ok)
        manager.detach()
        assert_rect_close(self, fixture_rect(other), other_rect)

    def test_login_to_chat_trusted_handover_real_same_process(self):
        login = self.spawn(mode="login", rect=(220, 220, 420, 560))
        old_source = source_for(login)
        manager = self.make_native_manager()
        self.assertTrue(manager.attach(old_source, (360, 260, 900, 820)).ok)
        self.assertEqual(manager.capture_health(old_source).state, "login")
        ack = login.command("replace_main")
        self.assertEqual(ack["class"], "CASyntheticChat")
        new_hwnd = int(ack["hwnd"])
        identity = discovery.process_identity(login.pid)
        new_source = SourceKey(identity["path"], identity["pid"],
                               identity["created"], new_hwnd)
        # same PID + process creation time = the trusted process instance
        self.assertEqual(new_source.pid, old_source.pid)
        self.assertEqual(new_source.process_started, old_source.process_started)
        target = (360, 260, 1100, 820)
        status = manager.attach(new_source, target)
        self.assertTrue(status.ok, status.reason)
        self.assertEqual(int(manager.active_source.hwnd), new_hwnd)
        assert_rect_close(self, fixture_rect(login), target)
        self.assertEqual(discovery.source_kind(new_source), "chat")
        manager.detach()


class OcclusionRegressionTests(NativeFixtureTestCase):
    def make_native_manager(self, **overrides):
        return window_manager.WindowManager(
            api=window_dock.NativeWindows(),
            validate=discovery.valid_source,
            popup_probe=discovery.popup_covering,
            zorder_probe=discovery.zorder_ok,
            classify_probe=discovery.source_kind,
            foreground_probe=overrides.pop("foreground_probe", discovery.foreground_window),
            **overrides)

    def test_zorder_repair_and_capture_frame_excludes_third_window(self):
        chat = self.spawn(mode="chat", rect=(200, 200, 620, 500))
        host = self.spawn(mode="marker", color="#336699", rect=(120, 120, 1100, 800))
        third = self.spawn(mode="marker", color="#FF00FF", rect=(320, 320, 480, 380))
        chat.command("text 遮挡回归测试")
        target_rect = (260, 200, 1060, 720)
        foreground = [int(host.hwnd)]
        manager = self.make_native_manager(foreground_probe=lambda: foreground[0])
        status = manager.attach(source_for(chat), target_rect, host_hwnd=host.hwnd)
        self.assertTrue(status.ok, status.reason)
        self.assertTrue(status.zorder_ok)
        self.assertTrue(status.overlay_safe)
        self.assertEqual(discovery.foreground_window() in (int(host.hwnd), int(chat.hwnd)), False)

        # geometry stays identical, but a third window is inserted between host and target
        self.assertTrue(set_zorder(third.hwnd, host.hwnd))
        self.assertFalse(discovery.zorder_ok(int(chat.hwnd), int(host.hwnd)))
        frame = capture_one_frame(chat.hwnd)
        magenta = ((frame[:, :, 0] > 200) & (frame[:, :, 1] < 60) & (frame[:, :, 2] > 200))
        self.assertEqual(int(magenta.sum()), 0,
                         "selected HWND capture must not contain the third window marker")
        blue = ((((frame[:, :, 0] > 150) & (frame[:, :, 1] > 70) & (frame[:, :, 1] < 180)
                  & (frame[:, :, 2] < 100))
                 | ((frame[:, :, 2] > 150) & (frame[:, :, 1] > 70) & (frame[:, :, 1] < 180)
                    & (frame[:, :, 0] < 100))))
        self.assertGreater(int(blue.sum()), 500, "capture frame should show the chat window itself")

        # activating the assistant repairs the group order without touching geometry
        geometry = fixture_rect(chat)
        status = manager.sync()
        self.assertTrue(status.zorder_ok)
        self.assertTrue(status.overlay_safe)
        assert_rect_close(self, fixture_rect(chat), geometry)
        self.assertTrue(discovery.zorder_ok(int(chat.hwnd), int(host.hwnd)))
        self.assertNotIn(discovery.foreground_window(), (int(host.hwnd), int(chat.hwnd)))

    def test_foreign_foreground_is_not_covered_or_forced(self):
        chat = self.spawn(mode="chat", rect=(200, 200, 620, 500))
        host = self.spawn(mode="marker", color="#336699", rect=(120, 120, 1100, 800))
        third = self.spawn(mode="marker", color="#FF00FF", rect=(320, 320, 480, 380))
        foreign = int(third.hwnd)
        manager = self.make_native_manager(foreground_probe=lambda: foreign)
        status = manager.attach(source_for(chat), (260, 200, 1060, 720), host_hwnd=host.hwnd)
        self.assertTrue(status.ok, status.reason)
        set_zorder(third.hwnd, host.hwnd)
        self.assertFalse(discovery.zorder_ok(int(chat.hwnd), int(host.hwnd)))
        status = manager.sync()
        self.assertFalse(status.overlay_safe)
        self.assertTrue(status.read_only)
        self.assertFalse(discovery.zorder_ok(int(chat.hwnd), int(host.hwnd)),
                         "must not reorder/raise the group while another app is foreground")
        self.assertNotIn(discovery.foreground_window(), (int(host.hwnd), int(chat.hwnd)))


# ---------------------------------------------------------------------------
# Recovery supervisor: real separate processes, abnormal exit / clean exit
# ---------------------------------------------------------------------------

_CREATE_NO_WINDOW = 0x08000000


class _FakeMainProcess:
    """Stands in for the crashed assistant main process."""

    def __init__(self):
        self.process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(120)"],
            creationflags=_CREATE_NO_WINDOW if sys.platform == "win32" else 0)

    @property
    def pid(self):
        return self.process.pid

    @property
    def created(self):
        return recovery_guard.process_created_for(self.process.pid)

    def kill(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)


class RecoveryGuardNativeTests(NativeFixtureTestCase):
    def setUp(self):
        super().setUp()
        self.fake_main = _FakeMainProcess()
        self.addCleanup(self._cleanup_fake_main)

    def _cleanup_fake_main(self):
        try:
            self.fake_main.kill()
        except Exception:  # noqa: BLE001
            pass

    def start_supervisor(self, store):
        created = self.fake_main.created
        self.assertIsNotNone(created)
        supervisor = recovery_guard.RecoverySupervisor(
            store.data_dir, parent_pid=self.fake_main.pid, parent_created=created,
            startup_timeout=15.0)
        self.assertTrue(supervisor.start(), "recovery supervisor failed to start")
        self.addCleanup(lambda: supervisor.stop_clean(timeout=5.0))
        state = supervisor.wait_for_state(("watching", "failed"), timeout=10.0)
        self.assertEqual(state["stage"], "watching")
        return supervisor

    def prepare_docked_window(self, store):
        chat = self.spawn(mode="chat", rect=(220, 220, 640, 520))
        original = fixture_rect(chat)
        source = source_for(chat)
        store.remember(source, original, False, at=time.time())
        chat.command("set_rect 420 360 700 560")  # simulated docked geometry
        docked = fixture_rect(chat)
        self.assertNotEqual(docked, original)
        return chat, source, original, docked

    def test_guard_restores_after_abnormal_main_exit(self):
        store = recovery_guard.RecoveryStore(self.data_dir)
        chat, source, original, docked = self.prepare_docked_window(store)
        supervisor = self.start_supervisor(store)
        self.assertNotEqual(supervisor.process.poll(), 0)
        self.fake_main.kill()  # abnormal exit without clean event
        code = supervisor.wait(timeout=25.0)
        self.assertIsNotNone(code)
        assert_rect_close(self, fixture_rect(chat), original)
        self.assertEqual(store.load(), [])
        state = supervisor.state()
        self.assertEqual(state["stage"], "restored")
        self.assertEqual(state["records"], 1)
        self.assertEqual(state["restored"], 1)

    def test_guard_ignores_foreign_unsigned_record(self):
        store = recovery_guard.RecoveryStore(self.data_dir)
        chat, source, original, docked = self.prepare_docked_window(store)
        # replace the signed file with a foreign unsigned record for the same window
        payload = {
            "app_id": recovery_guard.APP_ID,
            "schema_version": recovery_guard.SCHEMA_VERSION,
            "source": {"exe_path": source.exe_path, "pid": source.pid,
                       "process_started": source.process_started, "hwnd": source.hwnd},
            "original_rect": list(original),
            "original_minimized": False,
            "recorded_at": time.time(),
        }
        store.records_path.write_text(
            json.dumps({"app_id": recovery_guard.APP_ID,
                        "schema_version": recovery_guard.SCHEMA_VERSION,
                        "records": [{"payload": payload}]}), encoding="utf-8")
        supervisor = self.start_supervisor(store)
        self.fake_main.kill()
        self.assertIsNotNone(supervisor.wait(timeout=25.0))
        assert_rect_close(self, fixture_rect(chat), docked, tolerance=3)
        state = supervisor.state()
        self.assertEqual(state["restored"], 0)
        self.assertEqual(state["skipped"], 1)

    def test_guard_does_not_touch_closed_or_reused_hwnd(self):
        store = recovery_guard.RecoveryStore(self.data_dir)
        chat, source, original, docked = self.prepare_docked_window(store)
        recorded_source = source
        self.close_fixture(chat)  # the recorded WeChat window is gone
        replacement = self.spawn(mode="chat", rect=(300, 300, 700, 560))
        replacement_rect = fixture_rect(replacement)
        supervisor = self.start_supervisor(store)
        self.fake_main.kill()
        self.assertIsNotNone(supervisor.wait(timeout=25.0))
        assert_rect_close(self, fixture_rect(replacement), replacement_rect, tolerance=3)
        report = recovery_guard.restore_records(store.load(), api=None, validate=None)
        self.assertEqual(report.restored_count, 0)
        state = supervisor.state()
        self.assertEqual(state["restored"], 0)
        self.assertGreaterEqual(state["skipped"], 1)

    def test_guard_clean_shutdown_clears_records_without_restore(self):
        store = recovery_guard.RecoveryStore(self.data_dir)
        chat, source, original, docked = self.prepare_docked_window(store)
        supervisor = self.start_supervisor(store)
        self.assertTrue(supervisor.stop_clean(timeout=15.0))
        self.assertIsNotNone(supervisor.wait(timeout=10.0))
        assert_rect_close(self, fixture_rect(chat), docked, tolerance=3)
        self.assertEqual(store.load(), [])
        state = supervisor.state()
        self.assertEqual(state["stage"], "clean")
        self.assertEqual(state["restored"], 0)


class MainIntegrationTests(unittest.TestCase):
    def test_main_dispatches_recovery_guard_before_qt_and_instance_claim(self):
        import main as main_module

        with patch.object(recovery_guard, "supervisor_main", return_value=7) as dispatched, \
                patch.object(sys, "argv", ["main.py", "--recovery-guard",
                                           "--data-dir", "x"]), \
                patch.object(main_module, "claim_instance") as claim:
            self.assertEqual(main_module.main(), 7)
        dispatched.assert_called_once()
        claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
