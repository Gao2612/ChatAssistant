import unittest

from liteapp.contracts import ScopeKey, SourceKey
from liteapp.conversation_binding import ConversationBindings
from liteapp.realtime import ChatSnapshot, RealtimeController


def source(pid=100, created=1000, hwnd=200):
    return SourceKey(exe_path="C:/Weixin/Weixin.exe", pid=pid,
                     process_started=created, hwnd=hwnd)


def snapshot(src, epoch=1, title="客户 A", text="你好", frame=1, status="live_ready"):
    messages = (("me", "", text),) if text else ()
    return ChatSnapshot(source=src, conversation_epoch=epoch, frame_seq=frame,
                        title=title, messages=messages, status=status)


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class RealtimeControllerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.bindings = ConversationBindings()
        self.source = source()
        self.scope = ScopeKey("acct", "alice")
        self.bindings.bind(self.source, 1, self.scope)
        self.controller = RealtimeController(self.bindings, clock=self.clock,
                                             min_stable=2.0, min_interval=10.0)

    def test_stability_wait_before_request(self):
        self.controller.on_snapshot(snapshot(self.source))
        self.assertEqual(self.controller.due(), [])
        self.clock.now = 1.99
        self.assertEqual(self.controller.due(), [])
        self.clock.now = 2.01
        requests = self.controller.due()
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].scope, self.scope)
        self.assertEqual(requests[0].analysis_key.source, self.source)
        self.assertEqual(requests[0].analysis_key.binding_revision, 1)

    def test_min_interval_between_requests(self):
        self.controller.on_snapshot(snapshot(self.source, text="第一句"))
        self.clock.now = 2.5
        self.assertEqual(len(self.controller.due()), 1)
        self.controller.on_snapshot(snapshot(self.source, text="第二句", frame=2))
        self.clock.now = 3.0
        self.assertEqual(self.controller.due(), [])
        self.clock.now = 10.4
        self.assertEqual(self.controller.due(), [])
        self.clock.now = 12.6
        self.assertEqual(len(self.controller.due()), 1)

    def test_unbound_scope_never_requests_and_epoch_change_cancels(self):
        cancelled = []
        controller = RealtimeController(
            self.bindings, clock=self.clock, cancel_scope=cancelled.append)
        pending_bound = snapshot(self.source, text="已绑定会话")
        controller.on_snapshot(pending_bound)
        # The previous bound epoch is replaced by an unbound new epoch: the old
        # analysis is cancelled and the unconfirmed conversation is not sent.
        controller.on_snapshot(snapshot(self.source, epoch=2, title="同名客户", text="新会话"))
        self.assertEqual(cancelled, [self.scope])
        self.clock.now = 9.0
        self.assertEqual(controller.due(), [])
        other = source(pid=200, created=3000, hwnd=400)
        controller.on_snapshot(snapshot(other, epoch=1, text="未绑定来源"))
        self.clock.now = 20.0
        self.assertEqual(controller.due(), [])

    def test_non_ready_snapshot_clears_pending(self):
        self.controller.on_snapshot(snapshot(self.source, text="临时"))
        self.controller.on_snapshot(snapshot(self.source, title="", text="", status="unavailable"))
        self.clock.now = 9.0
        self.assertEqual(self.controller.due(), [])

    def test_semantic_change_restarts_stability_timer(self):
        self.controller.on_snapshot(snapshot(self.source, text="旧文字"))
        self.clock.now = 1.0
        self.controller.on_snapshot(snapshot(self.source, text="新文字", frame=2))
        self.clock.now = 2.5
        self.assertEqual(self.controller.due(), [])
        self.clock.now = 3.1
        self.assertEqual(len(self.controller.due()), 1)

    def test_analysis_key_uses_digest_and_frozen_versions(self):
        self.bindings.bind(self.source, 1, self.scope)
        controller = RealtimeController(
            self.bindings, clock=self.clock,
            memory_version_provider=lambda scope: 7,
            inference_revision_provider=lambda scope: 3)
        controller.on_snapshot(snapshot(self.source, text="报价"))
        self.clock.now = 2.5
        req = controller.due()[0]
        self.assertEqual(req.analysis_key.memory_version, 7)
        self.assertEqual(req.analysis_key.inference_revision, 3)
        self.assertEqual(req.analysis_key.snapshot_digest, req.snapshot.digest())

    def test_custom_key_factory_is_used(self):
        sentinel = object()
        controller = RealtimeController(self.bindings, clock=self.clock,
                                        key_factory=lambda snap, binding: sentinel)
        controller.on_snapshot(snapshot(self.source))
        self.clock.now = 2.5
        self.assertIs(controller.due()[0].analysis_key, sentinel)

    def test_pending_count_and_cancel(self):
        self.controller.on_snapshot(snapshot(self.source))
        self.assertEqual(self.controller.pending_count(), 1)
        self.controller.cancel(self.source)
        self.assertEqual(self.controller.pending_count(), 0)


if __name__ == "__main__":
    unittest.main()
