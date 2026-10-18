"""C5 synthetic acceptance: two synthetic window/frame sources driving T10.

SYNTHETIC ONLY. Two in-memory ``WindowTarget`` instances (fake hwnd/pid/creation
time, synthetic exe path) and in-memory frame generators drive the **real**
``SourceRegistry``/``CaptureScheduler``/``BackgroundSourceMonitor``. No real
WeChat window is opened, moved, captured or read, and no chat content is stored.

Scenarios:
* two synthetic sources produce stable, distinct ``SourceKey`` values;
* active source priority over an enabled background source;
* background monitoring off by default;
* minimized source pauses only itself;
* an unchanged (repeated) frame never triggers a second OCR;
* closing one source does not stop the other;
* background monitor packets (frame/snapshot) feed the same scheduler.
"""
import threading
import unittest
from collections import Counter
from unittest.mock import patch

from liteapp.capture_scheduler import CaptureScheduler
from liteapp.contracts import SourceKey
from liteapp.discovery import WindowTarget
from liteapp.multi_source_monitor import BackgroundSourceMonitor
from liteapp.source_registry import SourceRegistry

WECHAT = r"C:\Program Files\Tencent\Weixin\Weixin.exe"


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


def synthetic_window_target(hwnd, pid, created, title):
    """Synthetic WindowTarget + discovery-shaped item; no real HWND is touched."""
    target = WindowTarget(hwnd, pid, WECHAT, created)
    return target, {"hwnd": int(target), "pid": target.pid, "created": target.created,
                    "path": target.path, "title": title, "status": "visible",
                    "visible": True, "minimized": False}


class SyntheticCaptureSource:
    """In-memory frame source: only a content change increments frame_seq."""

    def __init__(self, target, registry, scheduler):
        self.target = target
        self.source = SourceKey.from_window_target(target)
        self.registry = registry
        self.scheduler = scheduler
        self.frame_seq = 0
        self.changes = 0
        self.repeats = 0

    def content_changed(self):
        self.changes += 1
        self.frame_seq += 1
        self._publish(self.frame_seq)
        return self.frame_seq

    def unchanged_frame(self):
        self.repeats += 1
        self._publish(self.frame_seq)
        return self.frame_seq

    def _publish(self, frame_seq):
        self.registry.observe_frame(self.source, frame_seq)
        self.scheduler.observe_frame(self.source, frame_seq)


class SyntheticOcrWorker:
    """Counts OCR executions chosen by the real scheduler; never reads a window."""

    def __init__(self, scheduler):
        self.scheduler = scheduler
        self.ocr_counts = Counter()
        self.jobs = []

    def tick(self):
        job = self.scheduler.take_next()
        if job is None:
            return None
        self.ocr_counts[job.source] += 1
        self.jobs.append(job)
        self.scheduler.complete(job.source, frame_seq=job.frame_seq)
        return job

    def ticks(self, count):
        return [self.tick() for _ in range(count)]


class _SyntheticSourceTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.registry = SourceRegistry(clock=self.clock)
        self.target1, self.item1 = synthetic_window_target(101, 2001, 1_000_000, "合成账号 A")
        self.target2, self.item2 = synthetic_window_target(202, 2002, 2_000_000, "合成账号 B")
        with patch("liteapp.source_registry.discovery.discover",
                   return_value=[self.item1, self.item2]):
            self.registry.discover(WECHAT, include_hidden=True)
        self.s1 = SourceKey.from_window_target(self.target1)
        self.s2 = SourceKey.from_window_target(self.target2)
        self.scheduler = CaptureScheduler(self.registry, clock=self.clock)
        self.ocr = SyntheticOcrWorker(self.scheduler)
        self.source_a = SyntheticCaptureSource(self.target1, self.registry, self.scheduler)
        self.source_b = SyntheticCaptureSource(self.target2, self.registry, self.scheduler)


class SourceIdentityTests(_SyntheticSourceTestCase):
    def test_two_synthetic_targets_have_distinct_normalized_source_keys(self):
        self.assertEqual(self.s1.pid, 2001)
        self.assertEqual(self.s1.hwnd, 101)
        self.assertEqual(self.s1.process_started, float(1_000_000))
        self.assertEqual(self.s2.pid, 2002)
        self.assertNotEqual(self.s1, self.s2)
        managed = self.registry.managed_sources()
        self.assertEqual([info.source for info in managed], [self.s1, self.s2])
        self.assertEqual(self.source_a.source, self.s1)
        self.assertEqual(self.source_b.source, self.s2)

    def test_reused_hwnd_pid_with_new_process_start_is_a_different_source(self):
        old = SourceKey.from_window_target(self.target1)
        new_target, _item = synthetic_window_target(101, 2001, 3_000_000, "合成账号 A")
        self.assertNotEqual(old, SourceKey.from_window_target(new_target))
        self.assertEqual(old.exe_path, SourceKey.from_window_target(new_target).exe_path)


class ActivePriorityTests(_SyntheticSourceTestCase):
    def test_active_source_wins_over_enabled_background_source(self):
        self.scheduler.activate(self.s1)
        self.assertTrue(self.scheduler.enable(self.s2, True))
        self.source_a.content_changed()
        self.source_b.content_changed()
        self.clock.advance(3.0)
        first = self.ocr.tick()
        self.assertIsNotNone(first)
        self.assertEqual(first.source, self.s1)
        self.assertEqual(first.priority, "active")
        second = self.ocr.tick()
        self.assertEqual(second.source, self.s2)
        self.assertEqual(second.priority, "background")
        self.assertEqual(self.ocr.ocr_counts[self.s1], 1)
        self.assertEqual(self.ocr.ocr_counts[self.s2], 1)

    def test_background_monitoring_is_off_by_default(self):
        self.scheduler.activate(self.s1)
        self.source_a.content_changed()
        self.source_b.content_changed()
        self.clock.advance(5.0)
        self.ocr.ticks(3)
        self.assertEqual(self.ocr.ocr_counts[self.s1], 1)
        self.assertEqual(self.ocr.ocr_counts[self.s2], 0)
        self.assertTrue(self.scheduler.enable(self.s2, True))
        self.clock.advance(5.0)
        self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s2], 1)

    def test_minimized_source_pauses_only_itself(self):
        self.scheduler.activate(self.s1)
        self.assertTrue(self.scheduler.enable(self.s2, True))
        self.source_a.content_changed()
        self.source_b.content_changed()
        self.scheduler.set_minimized(self.s2, True)
        self.clock.advance(5.0)
        self.ocr.ticks(3)
        self.assertEqual(self.ocr.ocr_counts[self.s1], 1)
        self.assertEqual(self.ocr.ocr_counts[self.s2], 0)
        self.scheduler.set_minimized(self.s2, False)
        self.clock.advance(5.0)
        self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s2], 1)


class UnchangedFrameTests(_SyntheticSourceTestCase):
    def test_unchanged_frames_never_repeat_active_ocr(self):
        self.scheduler.activate(self.s1)
        self.source_a.content_changed()
        self.clock.advance(2.0)
        self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s1], 1)
        for _ in range(5):
            self.source_a.unchanged_frame()
            self.clock.advance(2.0)
            self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s1], 1)
        self.assertEqual(self.ocr.ocr_counts[self.s2], 0)
        self.source_a.content_changed()
        self.clock.advance(2.0)
        self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s1], 2)
        self.assertEqual(self.source_a.repeats, 5)

    def test_unchanged_frames_never_repeat_background_ocr(self):
        self.scheduler.activate(self.s1)
        self.assertTrue(self.scheduler.enable(self.s2, True))
        self.source_b.content_changed()
        self.clock.advance(5.0)
        self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s2], 1)
        for _ in range(4):
            self.source_b.unchanged_frame()
            self.clock.advance(5.0)
            self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s2], 1)
        self.source_b.content_changed()
        self.clock.advance(5.0)
        self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s2], 2)

    def test_closing_one_source_does_not_stop_the_other(self):
        self.scheduler.activate(self.s1)
        self.assertTrue(self.scheduler.enable(self.s2, True))
        self.source_a.content_changed()
        self.source_b.content_changed()
        self.registry.close(self.s1)
        self.clock.advance(5.0)
        self.ocr.ticks(2)
        self.assertEqual(self.ocr.ocr_counts[self.s1], 0)
        self.assertEqual(self.ocr.ocr_counts[self.s2], 1)
        self.assertIsNone(self.registry.active_source())
        self.assertTrue(self.registry.get(self.s2).present)


class _FakeQueue:
    def __init__(self):
        self.packets = []

    def get_nowait(self):
        if not self.packets:
            raise RuntimeError("empty")
        return self.packets.pop(0)


class _FakeProcess:
    def __init__(self):
        self.terminated = False

    def join(self, timeout=None):
        return None

    def is_alive(self):
        return not self.terminated

    def terminate(self):
        self.terminated = True


class BackgroundMonitorSyntheticTests(unittest.TestCase):
    """Synthetic capture-worker packets feed the real registry/scheduler."""

    def setUp(self):
        self.clock = FakeClock()
        self.registry = SourceRegistry(clock=self.clock)
        self.target1, item1 = synthetic_window_target(301, 3001, 5_000_000, "合成 A")
        self.target2, item2 = synthetic_window_target(302, 3002, 6_000_000, "合成 B")
        with patch("liteapp.source_registry.discovery.discover",
                   return_value=[item1, item2]):
            self.registry.discover(WECHAT, include_hidden=True)
        self.s1 = SourceKey.from_window_target(self.target1)
        self.s2 = SourceKey.from_window_target(self.target2)
        self.registry.set_active(self.s1)
        self.scheduler = CaptureScheduler(self.registry, clock=self.clock)
        self.ocr = SyntheticOcrWorker(self.scheduler)
        self.workers = {}

        def factory(source):
            queue_obj = _FakeQueue()
            self.workers[source] = queue_obj
            return queue_obj, _FakeProcess()

        self.monitor = BackgroundSourceMonitor(
            self.registry, self.scheduler, worker_factory=factory, max_workers=2)

    def push(self, source, *packets):
        self.workers[source].packets.extend(packets)

    def test_background_frames_and_snapshots_drive_scheduler_without_repeat(self):
        self.assertFalse(self.monitor.enable(self.s1, True),
                         "active source must not be background-monitored")
        self.assertTrue(self.monitor.enable(self.s2, True))
        # Capture-child packets: "frame" means a new frame is available. The
        # synthetic OCR worker below is what performs OCR via the scheduler.
        self.push(self.s2, {"kind": "frame", "frame_seq": 1})
        events = self.monitor.poll()
        self.assertEqual([event.kind for event in events], ["frame"])
        self.assertEqual(self.registry.get(self.s2).frame_seq, 1)
        self.clock.advance(5.0)
        self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s2], 1)
        for _ in range(3):
            self.push(self.s2, {"kind": "frame", "frame_seq": 1})
            self.monitor.poll()
            self.clock.advance(5.0)
            self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s2], 1)
        self.push(self.s2, {"kind": "frame", "frame_seq": 2})
        self.monitor.poll()
        self.clock.advance(5.0)
        self.ocr.tick()
        self.assertEqual(self.ocr.ocr_counts[self.s2], 2)
        self.assertEqual(self.ocr.ocr_counts[self.s1], 0)

    def test_background_monitor_stop_keeps_other_source_untouched(self):
        self.assertTrue(self.monitor.enable(self.s2, True))
        self.assertTrue(self.monitor.enable(self.s2, False))
        self.assertEqual(self.monitor.running_sources(), [])
        self.assertTrue(self.registry.get(self.s2).present)
        self.assertEqual(self.ocr.ocr_counts[self.s2], 0)


if __name__ == "__main__":
    unittest.main()
