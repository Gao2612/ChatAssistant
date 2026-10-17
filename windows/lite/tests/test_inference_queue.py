"""T3 acceptance tests: single-flight inference queue and scheduler.

The gateway is scripted locally (no network): tests cover global in-flight limit 1,
same-source latest-only semantics, per-source rotation, history starvation
avoidance (one batch after five yields), cancellation scopes, quota pause, auth
error scoping, completion callbacks dispatched through the caller's dispatcher and
deterministic task timing with an injected clock.
"""
import threading
import time
import unittest

from liteapp import inference_schemas
from liteapp.contracts import CancellationToken, CancelledError
from liteapp.inference_queue import InferenceQueue, TaskHandle


def request(task_id, kind=inference_schemas.KIND_REALTIME_REPLY, *, profile="profile-1"):
    return inference_schemas.InferenceRequest(
        task_id=task_id, kind=kind, profile_id=profile,
        input_data={}, schema_version="v1", version_key="version-" + task_id)


def result_for(request_):
    return inference_schemas.InferenceResult(
        validated_data={"task_id": request_.task_id}, request_id="req-" + request_.task_id,
        elapsed=0.0, usage=None)


class ScriptedGateway:
    """Gateway double with deterministic behaviors and concurrency accounting."""

    def __init__(self):
        self.calls = []
        self.started = []
        self.behaviors = {}
        self.concurrent = 0
        self.max_concurrent = 0
        self.lock = threading.Lock()

    def behavior(self, task_id, action):
        self.behaviors[task_id] = action

    def run(self, request_, cancel):
        with self.lock:
            self.calls.append(request_.task_id)
            self.started.append(request_.task_id)
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            action = self.behaviors.get(request_.task_id)
            if action is None:
                return result_for(request_)
            if isinstance(action, BaseException):
                raise action
            return action(request_, cancel)
        finally:
            with self.lock:
                self.concurrent -= 1


class FakeClock:
    def __init__(self, step=1.0):
        self.value = 0.0
        self.step = step
        self.lock = threading.Lock()

    def __call__(self):
        with self.lock:
            value = self.value
            self.value += self.step
            return value


def wait_until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class QueueTestCase(unittest.TestCase):
    def setUp(self):
        self.gateway = ScriptedGateway()

    def queue(self, **kwargs):
        kwargs.setdefault("auto_start", False)
        return InferenceQueue(self.gateway, **kwargs)

    def submit(self, queue, task_id, *, kind=inference_schemas.KIND_REALTIME_REPLY,
               scope_id="scope-1", priority=None, profile="profile-1"):
        return queue.submit(request(task_id, kind, profile=profile),
                            priority=priority, scope_id=scope_id)


class ExecutionAndCallbackTests(QueueTestCase):
    def test_submit_runs_task_and_dispatches_completion_callback(self):
        dispatched = []
        callback_calls = []

        def dispatch(action):
            dispatched.append(action)
            action()

        queue = self.queue(dispatch=dispatch)
        handle = self.submit(queue, "task-1")
        self.assertIsInstance(handle, TaskHandle)
        handle.set_callback(lambda completed: callback_calls.append(completed))
        completed = queue.run_pending()

        self.assertEqual([item.task_id for item in completed], ["task-1"])
        self.assertEqual(handle.state, "completed")
        self.assertTrue(handle.done)
        self.assertFalse(handle.cancelled)
        self.assertEqual(handle.result.validated_data, {"task_id": "task-1"})
        self.assertEqual(len(dispatched), 1)  # callbacks go through the caller's dispatcher
        self.assertEqual(callback_calls, [handle])

    def test_task_handle_cancel_before_dispatch(self):
        queue = self.queue()
        handle = self.submit(queue, "task-1")
        handle.cancel()
        completed = queue.run_pending()
        self.assertEqual(completed, [])
        self.assertEqual(self.gateway.calls, [])
        self.assertEqual(handle.state, "cancelled")
        self.assertTrue(handle.cancelled)
        self.assertIsInstance(handle.error, CancelledError)

    def test_pending_cancellation_error_is_not_delivered_as_result(self):
        queue = self.queue()
        handle = self.submit(queue, "task-1")
        queue.cancel_scope("scope-1")
        queue.run_pending()
        self.assertIsNone(handle.result)
        self.assertEqual(handle.state, "cancelled")

    def test_injected_clock_records_task_times(self):
        clock = FakeClock(step=1.0)
        queue = self.queue(clock=clock)
        handle = self.submit(queue, "task-1")
        queue.run_pending()
        self.assertEqual(handle.created_at, 0.0)
        self.assertEqual(handle.started_at, 1.0)
        self.assertEqual(handle.finished_at, 2.0)


class SchedulingTests(QueueTestCase):
    def test_same_scope_keeps_only_the_latest_pending_task(self):
        queue = self.queue()
        superseded = self.submit(queue, "task-1", scope_id="source-a")
        latest = self.submit(queue, "task-2", scope_id="source-a")
        other = self.submit(queue, "task-3", scope_id="source-b")

        self.assertTrue(superseded.cancelled)
        self.assertIsInstance(superseded.error, CancelledError)
        self.assertEqual(queue.pending_count, 2)
        completed = queue.run_pending()
        self.assertEqual([item.request.task_id for item in completed], ["task-2", "task-3"])
        self.assertEqual(self.gateway.calls, ["task-2", "task-3"])
        self.assertEqual((latest.state, other.state), ("completed", "completed"))

    def test_multiple_sources_are_served_round_robin(self):
        queue = self.queue()
        for index, scope in enumerate(("source-a", "source-b", "source-c"), start=1):
            self.submit(queue, f"task-{index}", scope_id=scope)
        queue.run_pending()
        self.assertEqual(self.gateway.calls, ["task-1", "task-2", "task-3"])
        for index, scope in ((4, "source-b"), (5, "source-a")):
            self.submit(queue, f"task-{index}", scope_id=scope)
        queue.run_pending()
        self.assertEqual(self.gateway.calls[3:], ["task-4", "task-5"])

    def test_history_batch_runs_after_five_realtime_yields(self):
        history = inference_schemas.KIND_HISTORY_EXTRACT
        queue = self.queue()
        self.submit(queue, "history-1", kind=history, scope_id="job-1", priority="history")
        self.submit(queue, "history-2", kind=history, scope_id="job-2", priority="history")
        for index in range(7):
            self.submit(queue, f"realtime-{index}", scope_id=f"source-{index}")

        queue.run_pending()

        self.assertEqual(self.gateway.calls[:5], [f"realtime-{i}" for i in range(5)])
        self.assertEqual(self.gateway.calls[5], "history-1")
        self.assertEqual(self.gateway.calls[6:8], ["realtime-5", "realtime-6"])
        self.assertEqual(self.gateway.calls[8], "history-2")

    def test_history_only_queue_still_runs(self):
        queue = self.queue()
        for index in range(2):
            self.submit(queue, f"history-{index}",
                        kind=inference_schemas.KIND_HISTORY_REDUCE,
                        scope_id=f"job-{index}", priority="history")
        queue.run_pending()
        self.assertEqual(self.gateway.calls, ["history-0", "history-1"])

    def test_priority_must_match_task_kind(self):
        queue = self.queue()
        with self.assertRaises(ValueError):
            self.submit(queue, "history-as-realtime",
                        kind=inference_schemas.KIND_HISTORY_EXTRACT,
                        scope_id="job", priority="realtime")
        with self.assertRaises(ValueError):
            self.submit(queue, "realtime-as-history",
                        kind=inference_schemas.KIND_REALTIME_REPLY,
                        scope_id="source", priority="history")


class CancellationTests(QueueTestCase):
    def test_cancel_scope_cancels_pending_and_running_tasks(self):
        started = threading.Event()

        def blocking(request_, cancel):
            started.set()
            if cancel.wait(5):
                cancel.raise_if_cancelled()
            return result_for(request_)

        self.gateway.behavior("running", blocking)
        queue = self.queue(auto_start=True)
        self.addCleanup(queue.stop)
        running = self.submit(queue, "running", scope_id="source-a")
        self.assertTrue(wait_until(started.is_set))
        pending = self.submit(queue, "pending", scope_id="source-a")
        self.assertEqual(queue.in_flight_count, 1)

        queue.cancel_scope("source-a")

        self.assertTrue(pending.cancelled)
        self.assertTrue(wait_until(lambda: running.done))
        self.assertEqual(running.state, "cancelled")
        self.assertIsInstance(running.error, CancelledError)
        self.assertTrue(wait_until(lambda: queue.in_flight_count == 0))

    def test_stop_cancels_running_and_pending_tasks(self):
        started = threading.Event()

        def blocking(request_, cancel):
            started.set()
            cancel.wait(5)
            cancel.raise_if_cancelled()
            return result_for(request_)

        self.gateway.behavior("running", blocking)
        queue = self.queue(auto_start=True)
        running = self.submit(queue, "running", scope_id="source-a")
        self.assertTrue(wait_until(started.is_set))
        pending = self.submit(queue, "pending", scope_id="source-b")

        queue.stop()

        self.assertTrue(pending.cancelled)
        self.assertTrue(running.cancelled)
        self.assertFalse(queue.running)
        self.assertEqual(queue.in_flight_count, 0)

    def test_new_realtime_task_cancels_the_running_task_for_the_same_source(self):
        started = threading.Event()

        def blocking(request_, cancel):
            started.set()
            cancel.wait(5)
            cancel.raise_if_cancelled()
            return result_for(request_)

        self.gateway.behavior("task-1", blocking)
        queue = self.queue(auto_start=True)
        self.addCleanup(queue.stop)
        first = self.submit(queue, "task-1", scope_id="source-a")
        self.assertTrue(wait_until(started.is_set))

        second = self.submit(queue, "task-2", scope_id="source-a")
        self.assertTrue(wait_until(lambda: first.done))
        self.assertEqual(first.state, "cancelled")
        self.assertIsNone(first.result)
        self.assertTrue(wait_until(lambda: second.done))
        self.assertEqual(second.state, "completed")
        self.assertNotIn("task-1", self.gateway.calls[1:])


class FailureHandlingTests(QueueTestCase):
    def test_quota_error_pauses_the_queue_until_resume(self):
        self.gateway.behavior("limited",
                              inference_schemas.InferenceQuotaError("额度受限", code="quota"))
        queue = self.queue()
        limited = self.submit(queue, "limited", scope_id="source-a")
        waiting = self.submit(queue, "waiting", scope_id="source-b")
        queue.run_pending()

        self.assertIsInstance(limited.error, Exception)
        self.assertTrue(queue.paused)
        self.assertEqual(self.gateway.calls, ["limited"])
        self.assertEqual(queue.run_pending(), [])
        self.assertTrue(waiting.done is False)

        queue.resume()
        queue.run_pending()
        self.assertEqual(self.gateway.calls, ["limited", "waiting"])
        self.assertEqual(waiting.state, "completed")

    def test_quota_error_from_gateway_is_detected_by_type(self):
        self.gateway.behavior(
            "limited",
            inference_schemas.InferenceQuotaError("额度受限", code="usage_unavailable"))
        queue = self.queue()
        handle = self.submit(queue, "limited", scope_id="source-a")
        self.submit(queue, "waiting", scope_id="source-b")
        queue.run_pending()
        self.assertIsInstance(handle.error, inference_schemas.InferenceQuotaError)
        self.assertTrue(queue.paused)

    def test_authorization_error_stops_tasks_for_the_same_profile(self):
        self.gateway.behavior("denied", inference_schemas.InferenceAuthError("授权失效"))
        queue = self.queue()
        denied = self.submit(queue, "denied", scope_id="source-a", profile="profile-1")
        same_profile = self.submit(queue, "same-profile", scope_id="source-b", profile="profile-1")
        other_profile = self.submit(queue, "other-profile", scope_id="source-c", profile="profile-2")
        queue.run_pending()

        self.assertIsInstance(denied.error, inference_schemas.InferenceAuthError)
        self.assertTrue(same_profile.cancelled)
        self.assertEqual(other_profile.state, "completed")
        self.assertIn("other-profile", self.gateway.calls)

    def test_unexpected_gateway_failure_does_not_stop_the_queue(self):
        self.gateway.behavior("broken", RuntimeError("unexpected"))
        queue = self.queue()
        broken = self.submit(queue, "broken", scope_id="source-a")
        normal = self.submit(queue, "normal", scope_id="source-b")
        queue.run_pending()
        self.assertIsInstance(broken.error, RuntimeError)
        self.assertFalse(queue.paused)
        self.assertEqual(normal.state, "completed")


class InFlightTests(QueueTestCase):
    def test_global_in_flight_never_exceeds_one(self):
        release = threading.Event()

        def blocking(request_, cancel):
            release.wait(5)
            return result_for(request_)

        self.gateway.behavior("task-1", blocking)
        self.gateway.behavior("task-2", blocking)
        queue = self.queue(auto_start=True)
        self.addCleanup(queue.stop)
        handles = [self.submit(queue, f"task-{index}", scope_id=f"source-{index}")
                   for index in range(1, 5)]

        self.assertTrue(wait_until(lambda: len(self.gateway.started) >= 1))
        self.assertEqual(queue.in_flight_count, 1)
        self.assertLessEqual(self.gateway.max_concurrent, 1)
        release.set()
        self.assertTrue(wait_until(lambda: all(handle.done for handle in handles), timeout=10))
        self.assertEqual(self.gateway.max_concurrent, 1)
        self.assertEqual(sorted(self.gateway.calls), [f"task-{i}" for i in range(1, 5)])
        self.assertTrue(all(handle.state == "completed" for handle in handles))


if __name__ == "__main__":
    unittest.main()
