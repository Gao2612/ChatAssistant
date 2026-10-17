"""T4-UI acceptance tests: ``ui.py`` wired to the T4 ``WindowManager`` (R-03/R-07).

These tests use a **fake WindowManager / fake HWNDs only** -- no real WeChat
window is discovered or moved. They are written before the ui.py integration
(task-first) and cover:

* page switches call ``set_view(chat/settings/history/minimized)``;
* ``attach`` is called with a contracts ``SourceKey`` plus validated host and
  aperture handles and a physical aperture rectangle;
* ``capture_health``/``DockStatus`` drive the transparent aperture mask: a
  closed aperture + read-only fallback whenever ``overlay_safe`` is false
  (popup, unverified Z order, stale/error, missing host handle);
* second-instance activation is forwarded to ``WindowManager.on_activated``;
* connect/disconnect/close detach exactly once (restoring the managed window);
* ``tick()`` feeds ``record_heartbeat``/``record_frame``/``record_ocr``;
* R-07: ``after=0`` is treated as "keep Z order", never HWND_TOP; invalid
  ``host_hwnd``/``aperture_hwnd`` fail closed before any move.
"""

import os
import sys
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication  # noqa: E402

from liteapp import discovery  # noqa: E402
import main as main_module  # noqa: E402
from liteapp import window_dock  # noqa: E402
from liteapp.contracts import SourceKey  # noqa: E402
from liteapp.ui import MainWindow  # noqa: E402
from liteapp.window_manager import CaptureHealth, DockStatus, WindowManager  # noqa: E402

WEIXIN = r"C:\Program Files\Tencent\Weixin\Weixin.exe"


def make_config(**overrides):
    config = {
        "wechat_path": "", "base_url": "https://example.test/v1",
        "backend": "api", "model": "api-model", "api_model": "api-model",
        "api_key": "", "style": "自然简短", "auto_connect": False,
        "subscription": None, "subscription_models": [], "_warning": "",
        "auto_analysis": False,
    }
    config.update(overrides)
    return config


def fake_target(hwnd=4242, pid=777, created=987654321):
    return discovery.WindowTarget(hwnd, pid, WEIXIN, created)


def fake_source(hwnd=4242, pid=777, created=987654321):
    return SourceKey(WEIXIN, pid, created, hwnd)


class FakeWindowManager:
    """Records ui.py interactions; no Win32 calls, no real windows."""

    def __init__(self):
        self.attached = False
        self.active_source = None
        self.view = "chat"
        self.overlay_safe = False
        self.calls = []
        self.attach_status = DockStatus(state="attached", ok=True,
                                        zorder_ok=True, overlay_safe=True)
        self.sync_status = DockStatus(state="attached", ok=True,
                                      zorder_ok=True, overlay_safe=True)
        self.health = CaptureHealth(state="live", identity_ok=True, capture_ok=True,
                                    fresh_frame=True, zorder_ok=True,
                                    overlay_safe=True, read_only=False)
        self.activated = []

    # -- lifecycle used by ui.py -------------------------------------------
    def attach(self, source, target_rect, *, host_hwnd=None, aperture_hwnd=None,
               now=None):
        self.calls.append(("attach", source, tuple(target_rect), host_hwnd,
                           aperture_hwnd))
        if self.attach_status.ok:
            self.attached = True
            self.active_source = source
            self.view = "chat"
            self.overlay_safe = self.attach_status.overlay_safe
        return self.attach_status

    def sync(self, **kwargs):
        self.calls.append(("sync", kwargs))
        self.overlay_safe = self.sync_status.overlay_safe
        return self.sync_status

    def set_view(self, mode):
        self.calls.append(("set_view", mode))
        self.view = mode

    def detach(self, restore=True):
        self.calls.append(("detach", restore))
        self.attached = False
        self.active_source = None
        self.overlay_safe = False

    def capture_health(self, source, **kwargs):
        self.calls.append(("capture_health", source))
        return self.health

    def on_activated(self):
        self.calls.append(("on_activated",))
        self.activated.append(True)
        return True

    def record_heartbeat(self, source, **kwargs):
        self.calls.append(("record_heartbeat", source, kwargs))

    def record_frame(self, source, **kwargs):
        self.calls.append(("record_frame", source, kwargs))

    def record_ocr(self, source, **kwargs):
        self.calls.append(("record_ocr", source, kwargs))

    # -- assertions helpers -------------------------------------------------
    def named(self, name):
        return [call for call in self.calls if call[0] == name]


class FakeCaptureQueue:
    def __init__(self, packets):
        self.packets = list(packets)

    def get_nowait(self):
        if not self.packets:
            import queue
            raise queue.Empty
        return self.packets.pop(0)

    def cancel_join_thread(self):
        pass

    def close(self):
        pass


class FakeChild:
    def is_alive(self):
        return True

    def join(self, timeout=None):
        pass

    def terminate(self):
        pass


class _FakeMultiprocessingQueue:
    def cancel_join_thread(self):
        pass

    def close(self):
        pass


class _FakeMultiprocessingProcess:
    def __init__(self, target, args, daemon):
        self.target = target
        self.args = args
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True

    def join(self, timeout=None):
        pass

    def is_alive(self):
        return False

    def terminate(self):
        pass


@contextmanager
def patch_capture_process():
    """Keep connect_item from spawning a real capture child process."""
    class FakeEvent:
        def set(self):
            pass

    with patch("liteapp.ui.mp.Queue", return_value=_FakeMultiprocessingQueue()), \
            patch("liteapp.ui.mp.Event", return_value=FakeEvent()), \
            patch("liteapp.ui.mp.Array", return_value=object()), \
            patch("liteapp.ui.mp.Process", _FakeMultiprocessingProcess):
        yield


class FakeGuard:
    def __init__(self):
        self.requests = []

    def take_activation_request(self):
        return self.requests.pop(0) if self.requests else False


class UIWindowManagerTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.manager = FakeWindowManager()
        self.window = MainWindow(config=make_config(), scanning=False,
                                 window_manager=self.manager)
        # Flush the initial (windowless) docking singleShot before injecting the
        # connected window, then keep the periodic docking timer out of these
        # deterministic tests (they call sync_dock explicitly).
        self.app.processEvents()
        self.window.dock_timer.stop()
        self.window.window_item = {"id": "window-1", "hwnd": fake_target()}
        self.window.capture_session = "session-1"
        self.window.show()

    def tearDown(self):
        try:
            self.window.close()
        except Exception:  # noqa: BLE001 - teardown must not mask failures
            pass
        self.app.processEvents()

    # -- helpers ------------------------------------------------------------
    def sync_with_rect(self, rect=(120, 120, 780, 600)):
        with patch.object(self.window, "_aperture_rect", return_value=rect), \
                patch.object(self.window, "_host_hwnd", return_value=1001), \
                patch.object(self.window, "_aperture_hwnd", return_value=1002):
            self.app.processEvents()  # run show/resize/move-triggered singleShot syncs
            self.window.sync_dock()

    def assert_mask_closed(self):
        self.assertFalse(getattr(self.window, "_aperture_open", False),
                         "aperture must be closed (read-only fallback)")
        self.assertTrue(self.window.mask().isEmpty(),
                        "aperture mask must be closed (read-only fallback)")

    def assert_mask_open(self):
        self.assertTrue(getattr(self.window, "_aperture_open", False),
                        "aperture must be opened when overlay is safe")
        self.assertFalse(self.window.mask().isEmpty(),
                         "aperture mask must be opened when overlay is safe")


class PageViewTests(UIWindowManagerTestCase):
    def test_page_switches_forward_set_view(self):
        with patch.object(self.window.history_page, "refresh_people"):
            self.window.show_settings()
        self.assertEqual(self.manager.view, "settings")
        with patch.object(self.window.history_page, "refresh_people"):
            self.window.show_history()
        self.assertEqual(self.manager.view, "history")
        self.window.show_chat()
        self.assertEqual(self.manager.view, "chat")

    def test_minimized_assistant_uses_minimized_view(self):
        with patch.object(self.window, "isMinimized", return_value=True):
            self.sync_with_rect()
        self.assertEqual(self.manager.view, "minimized")
        self.assertEqual(self.manager.named("set_view")[-1], ("set_view", "minimized"))
        self.assertFalse(self.manager.named("attach"))
        self.assert_mask_closed()

    def test_non_chat_page_closes_aperture_without_detaching(self):
        manager = self.manager
        manager.attached = True
        manager.active_source = fake_source()
        with patch.object(self.window.history_page, "refresh_people"):
            self.window.show_history()
        self.assertEqual(manager.view, "history")
        self.assert_mask_closed()
        self.assertEqual(manager.named("detach"), [],
                         "switching pages must hide/reuse the dock, not restore geometry")


class AttachTests(UIWindowManagerTestCase):
    def test_chat_page_attaches_with_source_and_validated_handles(self):
        self.window.page_stack.setCurrentWidget(self.window.chat_page)
        self.sync_with_rect((120, 140, 780, 640))
        attaches = self.manager.named("attach")
        self.assertEqual(len(attaches), 1)
        _, source, rect, host_hwnd, aperture_hwnd = attaches[0]
        self.assertIsInstance(source, SourceKey)
        self.assertEqual(source, SourceKey.from_window_target(fake_target()))
        self.assertEqual(source.hwnd, 4242)
        self.assertEqual(host_hwnd, 1001)
        self.assertEqual(aperture_hwnd, 1002)
        self.assertEqual(rect, (120, 140, 780, 640))
        self.assert_mask_open()

    def test_missing_host_or_aperture_handle_fails_closed_without_attach(self):
        for field in ("_host_hwnd", "_aperture_hwnd"):
            with self.subTest(field=field):
                self.manager.calls.clear()
                with patch.object(self.window, "_aperture_rect",
                                  return_value=(120, 140, 780, 640)), \
                        patch.object(self.window, "_host_hwnd", return_value=1001), \
                        patch.object(self.window, "_aperture_hwnd", return_value=1002):
                    with patch.object(self.window, field, return_value=0):
                        self.window.sync_dock()
                self.assertEqual(self.manager.named("attach"), [],
                                 "must not attach with an invalid host/aperture HWND")
                self.assert_mask_closed()

    def test_unverified_handle_is_not_passed_to_the_manager(self):
        self.manager.attached = True  # e.g. a previous window was still docked
        self.manager.active_source = fake_source()
        self.window.window_item = {"id": "window-1", "hwnd": 1234}  # bare int, no identity
        self.sync_with_rect()
        self.assertEqual(self.manager.named("attach"), [])
        self.assertGreaterEqual(len(self.manager.named("detach")), 1,
                                "an unverified handle must tear down the old attachment")
        self.assertFalse(self.manager.attached)
        self.assert_mask_closed()


class ApertureHealthTests(UIWindowManagerTestCase):
    def test_attach_readonly_fallback_keeps_aperture_closed(self):
        self.manager.attach_status = DockStatus(
            state="attached", ok=True, zorder_ok=False, overlay_safe=False,
            read_only=True, reason="zorder_unverified")
        self.manager.health = CaptureHealth(state="live", identity_ok=True,
                                            zorder_ok=False, overlay_safe=False,
                                            read_only=True)
        self.sync_with_rect()
        self.assert_mask_closed()

    def test_popup_health_closes_aperture_and_explains_readonly(self):
        self.sync_with_rect()
        self.assert_mask_open()
        self.manager.health = CaptureHealth(
            state="popup", identity_ok=True, capture_ok=True, popup_hwnd=77,
            overlay_safe=False, read_only=True, reason="popup_open")
        self.manager.sync_status = DockStatus(
            state="popup", popup_hwnd=77, overlay_safe=False, read_only=True)
        self.manager.attached = True
        self.manager.active_source = fake_source()
        self.sync_with_rect()
        self.assert_mask_closed()
        self.assertIn("只读", self.window.connection_status.text() + self.window.notice.text())

    def test_stale_health_closes_aperture(self):
        self.sync_with_rect()
        self.assert_mask_open()
        self.manager.health = CaptureHealth(
            state="stale", identity_ok=True, capture_ok=False,
            overlay_safe=False, read_only=True)
        self.manager.sync_status = DockStatus(state="attached", ok=True,
                                              zorder_ok=True, overlay_safe=True)
        self.manager.attached = True
        self.manager.active_source = fake_source()
        self.sync_with_rect()
        self.assert_mask_closed()

    def test_health_recovers_and_reopens_aperture(self):
        self.manager.attach_status = DockStatus(
            state="attached", ok=True, overlay_safe=False, read_only=True)
        self.manager.health = CaptureHealth(state="stale", identity_ok=True,
                                            overlay_safe=False, read_only=True)
        self.sync_with_rect()
        self.assert_mask_closed()
        self.manager.sync_status = DockStatus(state="attached", ok=True,
                                              zorder_ok=True, overlay_safe=True)
        self.manager.health = CaptureHealth(state="live", identity_ok=True,
                                            overlay_safe=True, read_only=False)
        self.sync_with_rect()
        self.assert_mask_open()


class ActivationTests(UIWindowManagerTestCase):
    def test_on_activated_forwards_to_window_manager(self):
        self.assertTrue(self.window.on_activated())
        self.assertEqual(len(self.manager.activated), 1)

    def test_main_activation_poll_calls_window_on_activated(self):
        guard = FakeGuard()
        guard.requests.append(True)
        timer = main_module.install_activation_poll(guard, self.window, interval_ms=60000)
        try:
            timer.timeout.emit()  # simulate one poll tick synchronously
        finally:
            timer.stop()
        self.assertEqual(len(self.manager.activated), 1,
                         "second-instance activation must repair the docked group")

    def test_on_activated_without_manager_is_safe(self):
        self.window.window_manager = None
        self.assertFalse(self.window.on_activated())


class DetachLifecycleTests(UIWindowManagerTestCase):
    def test_disconnect_detaches_and_closes_aperture(self):
        self.sync_with_rect()
        self.assertEqual(self.manager.attached, True)
        self.window.disconnect_capture()
        self.assertGreaterEqual(len(self.manager.named("detach")), 1)
        self.assertFalse(self.manager.attached)
        self.assert_mask_closed()

    def test_close_detaches_managed_window(self):
        self.sync_with_rect()
        with patch.object(self.window.history_page, "refresh_people"):
            self.window.close()
        self.assertGreaterEqual(len(self.manager.named("detach")), 1)
        self.assertFalse(self.manager.attached)

    def test_switching_connected_window_detaches_previous(self):
        other_target = fake_target(hwnd=5252, pid=999, created=987654322)
        self.sync_with_rect()
        with patch_capture_process():
            self.window.connect_item({"id": "window-2", "hwnd": other_target})
        self.assertGreaterEqual(len(self.manager.named("detach")), 1)


class TickFreshnessTests(UIWindowManagerTestCase):
    def setUp(self):
        super().setUp()
        self.window.frame_clock = None
        self.window.child = FakeChild()
        self.window.capture_session = "session-1"

    def _packet(self, kind, **extra):
        packet = {"kind": kind, "session": "session-1"}
        packet.update(extra)
        return packet

    def test_tick_feeds_heartbeat_frame_ocr_and_change(self):
        self.sync_with_rect()
        self.manager.calls.clear()
        self.window.frame_freshness.observe(2)  # the snapshot's frame is the newest
        self.window.capture_q = FakeCaptureQueue([
            self._packet("frame", image=b"", frame_seq=1),
            self._packet("snapshot", title="Alice",
                         messages=[("her", "", "你好")], frame_seq=2, ocr_ms=12),
            self._packet("unavailable", message="请打开聊天"),
        ])
        self.window.tick()
        beats = self.manager.named("record_heartbeat")
        self.assertGreaterEqual(len(beats), 3, "every packet is a capture heartbeat")
        frames = self.manager.named("record_frame")
        self.assertGreaterEqual(len(frames), 2, "frame + snapshot both arrive as frames")
        self.assertTrue(any(call[2].get("changed") for call in frames),
                        "a semantic snapshot change must be recorded")
        self.assertEqual(len(self.manager.named("record_ocr")), 1)

    def test_tick_without_verified_source_does_not_feed_manager(self):
        self.window.window_item = {"id": "window-1", "hwnd": 1234}
        self.window.capture_q = FakeCaptureQueue([self._packet("frame", image=b"")])
        self.window.tick()
        self.assertEqual(self.manager.named("record_heartbeat"), [])


class RecoveryRecordIntegrationTests(UIWindowManagerTestCase):
    def test_window_manager_constructed_with_recovery_store_by_default(self):
        window = MainWindow(config=make_config(), scanning=False)
        try:
            manager = window.window_manager
            self.assertIsInstance(manager, WindowManager)
            # The recovery store points into the application data directory so a
            # crash can restore the managed window later (T4 backend).
            store = manager.recovery_store
            self.assertIsNotNone(store)
            self.assertEqual(store.records_path.parent, Path(store.data_dir))
        finally:
            window.close()
            self.app.processEvents()


class RecoveryRecordLifecycleTests(UIWindowManagerTestCase):
    """Real WindowManager + RecoveryStore through ui.py, fake HWND/API only."""

    def setUp(self):
        import shutil
        import tempfile

        from liteapp.recovery_guard import RecoveryStore

        super().setUp()
        self.data_dir = Path(tempfile.mkdtemp(prefix="t4ui-recovery-"))
        self.addCleanup(lambda: shutil.rmtree(self.data_dir, ignore_errors=True))
        self.store = RecoveryStore(self.data_dir)
        self.api = FakeWindowApi()
        # host (assistant window) and aperture handles passed by ui.py
        self.api.rectangles[1001] = (0, 0, 1400, 900)
        self.api.rectangles[1002] = (100, 100, 1180, 780)
        self.real_manager = WindowManager(
            api=self.api, validate=lambda source: True,
            popup_probe=lambda hwnd, rect: None,
            zorder_probe=lambda hwnd, host: self.api.zorder_ok,
            classify_probe=lambda source: "chat",
            foreground_probe=lambda: 0,
            recovery_store=self.store)
        self.window.window_manager = self.real_manager
        self.window.window_item = {"id": "window-1", "hwnd": fake_target(hwnd=10)}

    def test_attach_writes_recovery_record_and_detach_clears_it(self):
        original = self.api.rect(10)
        self.sync_with_rect((120, 120, 780, 600))
        self.assertTrue(self.real_manager.attached)
        records = self.store.load()
        self.assertEqual(len(records), 1, "docking must write one signed recovery record")
        self.assertEqual(records[0].original_rect, tuple(original))
        self.assertEqual(int(records[0].source.hwnd), 10)
        self.window.disconnect_capture()
        self.assertFalse(self.real_manager.attached)
        self.assertEqual(self.store.load(), [], "detach must clear the recovery record")
        self.assertEqual(tuple(self.api.rect(10)), tuple(original))

    def test_close_while_docked_detaches_and_clears_recovery_record(self):
        self.sync_with_rect((120, 120, 780, 600))
        self.assertTrue(self.real_manager.attached)
        self.window.close()
        self.assertFalse(self.real_manager.attached)
        self.assertEqual(self.store.load(), [])


# ---------------------------------------------------------------------------
# R-07: fake-value handling and host/aperture validation in WindowManager
# ---------------------------------------------------------------------------

class FakeWindowApi:
    def __init__(self):
        self.rectangles = {10: (10, 20, 810, 620), 20: (0, 0, 1366, 768),
                           30: (100, 120, 700, 520)}
        self.calls = []
        self.zorder_ok = False

    def is_window(self, hwnd):
        return int(hwnd) in self.rectangles

    def is_minimized(self, hwnd):
        return False

    def set_minimized(self, hwnd, minimized):
        self.calls.append(("minimize", int(hwnd), bool(minimized)))
        return True

    def rect(self, hwnd):
        return self.rectangles[int(hwnd)]

    def restored_rect(self, hwnd):
        return self.rectangles[int(hwnd)]

    def set_pos(self, hwnd, after, rect):
        self.calls.append(("set_pos", int(hwnd),
                           None if after is None else int(after), tuple(rect)))
        if after is not None and not getattr(self, "keep_zorder_broken", False):
            self.zorder_ok = True
        self.rectangles[int(hwnd)] = tuple(rect)
        return True


class R07FakeValueTests(unittest.TestCase):
    def make_manager(self, api=None):
        api = api or FakeWindowApi()
        return WindowManager(
            api=api, validate=lambda source: True,
            popup_probe=lambda hwnd, rect: None,
            zorder_probe=lambda hwnd, host: api.zorder_ok,
            classify_probe=lambda source: "chat",
            foreground_probe=lambda: 0), api

    def source(self):
        return SourceKey(WEIXIN, 777, 987654321, 10)

    def test_attach_refuses_zero_or_invalid_host_hwnd(self):
        manager, api = self.make_manager()
        for host in (0, 999):  # 0 = no handle, 999 = not a real window
            with self.subTest(host=host):
                api.calls.clear()
                status = manager.attach(self.source(), (100, 120, 700, 520),
                                        host_hwnd=host)
                self.assertFalse(status.ok)
                self.assertEqual(status.state, "refused")
                self.assertEqual(status.reason, "host_invalid")
                self.assertEqual(api.calls, [], "must fail before moving any window")

    def test_attach_refuses_zero_or_invalid_aperture_hwnd(self):
        manager, api = self.make_manager()
        for aperture in (0, 999):
            with self.subTest(aperture=aperture):
                api.calls.clear()
                status = manager.attach(self.source(), (100, 120, 700, 520),
                                        host_hwnd=20, aperture_hwnd=aperture)
                self.assertFalse(status.ok)
                self.assertEqual(status.state, "refused")
                self.assertEqual(status.reason, "aperture_invalid")
                self.assertEqual(api.calls, [])

    def test_sync_refuses_to_reorder_with_an_invalid_host(self):
        manager, api = self.make_manager()
        status = manager.attach(self.source(), (100, 120, 700, 520),
                                host_hwnd=20, aperture_hwnd=30)
        self.assertTrue(status.ok, status.reason)
        api.calls.clear()
        status = manager.sync(host_hwnd=0)
        self.assertFalse(status.ok)
        self.assertEqual(status.reason, "host_invalid")
        self.assertEqual(api.calls, [])

    def test_placement_flags_treat_after_zero_as_keep_zorder(self):
        flags_none = window_dock.NativeWindows.placement_flags(None)
        flags_zero = window_dock.NativeWindows.placement_flags(0)
        self.assertTrue(flags_none & window_dock.NativeWindows.SWP_NOZORDER)
        self.assertTrue(flags_zero & window_dock.NativeWindows.SWP_NOZORDER,
                        "after=0 must not raise the target to HWND_TOP")
        self.assertFalse(flags_zero & window_dock.NativeWindows.SWP_SHOWWINDOW)

    def test_native_set_pos_normalizes_after_zero_away_from_hwnd_top(self):
        native = window_dock.NativeWindows()

        class FakeUser32:
            def __init__(self):
                self.args = None

            def SetWindowPos(self, hwnd, after, *rest):
                self.args = (int(hwnd), after, *rest)
                return 1

        fake = FakeUser32()
        native.user32 = fake
        self.assertTrue(native.set_pos(10, 0, (100, 120, 700, 520)))
        _, insert_after, left, top, width, height, flags = fake.args
        self.assertEqual((left, top, width, height), (100, 120, 600, 400))
        self.assertFalse(insert_after, "after=0 must be normalized to no insert-after")
        self.assertTrue(flags & window_dock.NativeWindows.SWP_NOZORDER)
        self.assertTrue(flags & window_dock.NativeWindows.SWP_NOACTIVATE)


if __name__ == "__main__":
    unittest.main()
