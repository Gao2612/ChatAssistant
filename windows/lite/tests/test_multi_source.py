import unittest
from unittest.mock import patch

from liteapp.capture_scheduler import CaptureScheduler
from liteapp.contracts import SourceKey
from liteapp.source_registry import SourceInfo, SourceRegistry, source_from_window

WECHAT = r"C:\Program Files\Tencent\Weixin\Weixin.exe"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def item(hwnd, pid=100, created=1000, title="微信", status="visible"):
    return {"hwnd": hwnd, "pid": pid, "created": created, "path": WECHAT,
            "title": title, "status": status, "visible": status == "visible",
            "minimized": status == "minimized"}


def source(hwnd, pid=100, created=1000):
    return SourceKey(WECHAT, pid, created, hwnd)


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.registry = SourceRegistry(clock=self.clock)

    def discover(self, *items):
        with patch("liteapp.source_registry.discovery.discover", return_value=list(items)):
            return self.registry.discover(WECHAT, include_hidden=True)

    def test_manages_at_most_three_present_sources(self):
        found = self.discover(item(1), item(2, pid=101), item(3, pid=102), item(4, pid=103))
        self.assertEqual(len(found), 4)
        self.assertEqual(len(self.registry.managed_sources()), 3)
        unmonitored = self.registry.unmonitored_sources()
        self.assertEqual(len(unmonitored), 1)
        self.assertFalse(self.registry.enable(unmonitored[0].source, True))
        self.assertTrue(self.registry.enable(source(1), True))
        self.assertTrue(self.registry.get(source(1)).enabled)

    def test_account_label_and_state_are_per_source(self):
        self.discover(item(1), item(2, pid=101))
        self.registry.assign_account(source(1), "acct-a")
        self.registry.assign_account(source(2, pid=101), "acct-b")
        self.registry.observe_frame(source(1), 5)
        self.registry.observe_frame(source(2, pid=101), 9)
        self.assertEqual(self.registry.get(source(1)).account_id, "acct-a")
        self.assertEqual(self.registry.get(source(2, pid=101)).frame_seq, 9)

    def test_active_source_always_managed_and_evicts_when_needed(self):
        self.discover(item(1), item(2, pid=101), item(3, pid=102))
        self.registry.set_active(source(3, pid=102))
        self.assertEqual(self.registry.active_source(), source(3, pid=102))
        self.assertEqual(len(self.registry.managed_sources()), 3)

    def test_closing_one_source_keeps_others_present(self):
        self.discover(item(1), item(2, pid=101))
        self.registry.set_active(source(1))
        self.registry.close(source(1))
        self.assertIsNone(self.registry.active_source())
        self.assertFalse(self.registry.get(source(1)).present)
        self.assertTrue(self.registry.get(source(2, pid=101)).present)

    def test_source_key_from_window_item(self):
        self.assertEqual(source_from_window(item(7, pid=9, created=11)), source(7, 9, 11))
        self.assertIsNone(source_from_window({"hwnd": 1}))


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.registry = SourceRegistry(clock=self.clock)
        with patch("liteapp.source_registry.discovery.discover",
                   return_value=[item(1), item(2, pid=101)]):
            self.registry.discover(WECHAT, include_hidden=True)
        self.s1, self.s2 = source(1), source(2, pid=101)
        self.scheduler = CaptureScheduler(self.registry, clock=self.clock)

    def frame(self, src, seq):
        self.clock.now += 0.1
        self.scheduler.observe_frame(src, seq)

    def test_active_source_has_priority(self):
        self.scheduler.activate(self.s1)
        self.frame(self.s1, 1)
        self.frame(self.s2, 1)
        self.clock.now += 1.0
        job = self.scheduler.next_job()
        self.assertEqual(job.source, self.s1)
        self.assertEqual(job.priority, "active")
        self.scheduler.complete(self.s1, frame_seq=1)
        self.clock.now += 2.6
        job = self.scheduler.next_job()
        self.assertIsNone(job, "background monitoring is off by default")

    def test_background_opt_in_and_independent_state(self):
        self.scheduler.activate(self.s1)
        self.assertTrue(self.scheduler.enable(self.s2, True))
        self.frame(self.s1, 1)
        self.frame(self.s2, 1)
        self.clock.now += 3.0
        jobs = self.scheduler.pending()
        self.assertEqual([job.source for job in jobs], [self.s1, self.s2])
        self.scheduler.complete(self.s1, frame_seq=1)
        self.clock.now += 3.0
        jobs = self.scheduler.pending()
        self.assertEqual([job.source for job in jobs], [self.s2])
        self.scheduler.complete(self.s2, frame_seq=1)
        self.assertEqual(self.scheduler.pending(), [])

    def test_minimized_source_pauses_only_itself(self):
        self.scheduler.activate(self.s1)
        self.scheduler.enable(self.s2, True)
        self.scheduler.set_minimized(self.s2, True)
        self.frame(self.s1, 1)
        self.frame(self.s2, 1)
        self.clock.now += 3.0
        self.assertEqual([job.source for job in self.scheduler.pending()], [self.s1])

    def test_no_new_frame_no_repeat_ocr(self):
        self.scheduler.activate(self.s1)
        self.frame(self.s1, 3)
        self.clock.now += 2.0
        self.scheduler.complete(self.s1, frame_seq=3)
        self.clock.now += 5.0
        self.assertIsNone(self.scheduler.next_job())
        self.frame(self.s1, 4)
        self.clock.now += 2.0
        self.assertEqual(self.scheduler.next_job().frame_seq, 4)

    def test_one_source_close_does_not_stop_other(self):
        self.scheduler.activate(self.s1)
        self.scheduler.enable(self.s2, True)
        self.frame(self.s1, 1)
        self.frame(self.s2, 1)
        self.registry.close(self.s1)
        self.clock.now += 3.0
        self.assertEqual([job.source for job in self.scheduler.pending()], [self.s2])


if __name__ == "__main__":
    unittest.main()
