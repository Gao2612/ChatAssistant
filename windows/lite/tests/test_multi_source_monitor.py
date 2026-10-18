import unittest
from unittest.mock import patch

from liteapp.capture_scheduler import CaptureScheduler
from liteapp.contracts import SourceKey
from liteapp.multi_source_monitor import BackgroundSourceMonitor
from liteapp.source_registry import SourceRegistry

WECHAT = r"C:\Program Files\Tencent\Weixin\Weixin.exe"


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def item(hwnd, pid=100, created=1000):
    return {"hwnd": hwnd, "pid": pid, "created": created, "path": WECHAT,
            "title": "微信", "status": "visible", "visible": True,
            "minimized": False}


def source(hwnd, pid=100, created=1000):
    return SourceKey(WECHAT, pid, created, hwnd)


class FakeProcess:
    def __init__(self):
        self.terminated = False
        self.joined = False

    def join(self, timeout=None):
        self.joined = True

    def is_alive(self):
        return not self.terminated

    def terminate(self):
        self.terminated = True


class FakeQueue:
    def __init__(self):
        self.packets = []

    def get_nowait(self):
        if not self.packets:
            raise RuntimeError("empty")
        return self.packets.pop(0)


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.registry = SourceRegistry(clock=self.clock)
        with patch("liteapp.source_registry.discovery.discover",
                   return_value=[item(1), item(2, pid=101), item(3, pid=102)]):
            self.registry.discover(WECHAT, include_hidden=True)
        self.sources = [source(1), source(2, pid=101), source(3, pid=102)]
        self.registry.set_active(self.sources[0])
        self.scheduler = CaptureScheduler(self.registry, clock=self.clock)
        self.workers = {}
        self.processes = {}

        def factory(src):
            queue_obj = FakeQueue()
            process = FakeProcess()
            self.workers[src] = queue_obj
            self.processes[src] = process
            return queue_obj, process

        self.monitor = BackgroundSourceMonitor(
            self.registry, self.scheduler, worker_factory=factory, max_workers=2)

    def test_active_source_is_never_background_monitored(self):
        self.assertFalse(self.monitor.enable(self.sources[0], True))
        self.assertEqual(self.monitor.running_sources(), [])

    def test_enable_starts_and_disable_stops_worker(self):
        self.assertTrue(self.monitor.enable(self.sources[1], True))
        self.assertTrue(self.monitor.is_running(self.sources[1]))
        self.assertTrue(self.registry.get(self.sources[1]).enabled)
        self.assertTrue(self.monitor.enable(self.sources[1], False))
        self.assertFalse(self.monitor.is_running(self.sources[1]))

    def test_worker_limit_and_one_source_failure_isolation(self):
        self.assertTrue(self.monitor.enable(self.sources[1], True))
        self.assertTrue(self.monitor.enable(self.sources[2], True))
        self.assertEqual(len(self.monitor.running_sources()), 2)
        # max_workers=2, both other sources already monitored
        self.assertFalse(self.monitor.start(source(99)))
        self.monitor.stop(self.sources[1])
        self.assertTrue(self.monitor.is_running(self.sources[2]))

    def test_poll_updates_registry_and_emits_events(self):
        self.assertTrue(self.monitor.enable(self.sources[1], True))
        self.workers[self.sources[1]].packets.extend([
            {"kind": "frame", "frame_seq": 3},
            {"kind": "snapshot", "frame_seq": 3, "title": "微信",
             "messages": [("her", "", "hi")]},
        ])
        events = self.monitor.poll()
        self.assertEqual([event.kind for event in events], ["frame", "snapshot"])
        self.assertEqual(self.registry.get(self.sources[1]).frame_seq, 3)
        self.assertIsNotNone(self.registry.get(self.sources[1]).last_ocr_at)

    def test_stop_all_stops_every_worker(self):
        self.monitor.enable(self.sources[1], True)
        self.monitor.enable(self.sources[2], True)
        self.monitor.stop_all()
        self.assertEqual(self.monitor.running_sources(), [])
        self.assertTrue(all(process.terminated or process.joined
                            for process in self.processes.values()))


if __name__ == "__main__":
    unittest.main()
