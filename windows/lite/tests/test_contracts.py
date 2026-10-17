"""T1 验收用例：范围/来源/结果版本契约与取消令牌。

这些用例先于实现编写，用于确认现状缺少契约模块；实现后必须全部通过。
测试只用合成 ID 与合成聊天文本，不涉及真实微信账号或聊天内容。
"""

import dataclasses
import threading
import time
import unittest

from liteapp.contracts import (
    AnalysisKey,
    CancelledError,
    CancellationToken,
    EvidenceRef,
    ScopeKey,
    SourceKey,
)
from liteapp.state import ConversationState

EXE = r"C:\Program Files\Tencent\Weixin\Weixin.exe"
PROCESS_STARTED = 133500000000000000  # FILETIME，与 discovery.process_identity 的 created 同型
HWND = 0x000A1234


def scope(account="wx-account-1", person="person-1"):
    return ScopeKey(account, person)


def source(**overrides):
    values = dict(exe_path=EXE, pid=4242, process_started=PROCESS_STARTED, hwnd=HWND)
    values.update(overrides)
    return SourceKey(**values)


def analysis(**overrides):
    values = dict(source=source(), conversation_epoch=3, snapshot_digest="d" * 64,
                  binding_revision=2, memory_version=5, inference_revision=7)
    values.update(overrides)
    return AnalysisKey(**values)


class ScopeKeyTests(unittest.TestCase):
    def test_scope_is_frozen_value_object(self):
        key = scope()
        self.assertEqual(key, ScopeKey("wx-account-1", "person-1"))
        self.assertEqual(hash(key), hash(scope()))
        self.assertEqual({key: "证据"}[scope()], "证据")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            key.person_id = "other"

    def test_empty_or_blank_account_and_person_rejected(self):
        for account, person in (("", "p"), ("   ", "p"), ("a", ""), ("a", "\t\n")):
            with self.assertRaises(ValueError):
                ScopeKey(account, person)
        for bad in (None, 123, b"account"):
            with self.assertRaises(TypeError):
                ScopeKey(bad, "p")
            with self.assertRaises(TypeError):
                ScopeKey("account", bad)

    def test_same_display_name_never_merges_scope(self):
        # 显示名称不是身份；不同稳定 ID 必须是不同范围，同名只是 UI 层的问题。
        self.assertNotEqual(scope(account="wx-1", person="uuid-a"),
                            scope(account="wx-2", person="uuid-a"))
        self.assertNotEqual(scope(account="wx-1", person="uuid-a"),
                            scope(account="wx-1", person="uuid-b"))


class SourceKeyTests(unittest.TestCase):
    def test_same_pid_and_hwnd_with_new_process_start_is_different_source(self):
        first = source(process_started=PROCESS_STARTED)
        reused = source(process_started=PROCESS_STARTED + 1234567)
        self.assertNotEqual(first, reused)
        self.assertEqual(len({first, reused}), 2)

    def test_process_started_accepts_discovery_filestime_integer(self):
        # discovery.process_identity returns FILETIME ticks as int; the contract
        # canonicalizes to the float type named by the plan so both spellings of
        # the same process start compare equal.
        key = source(process_started=PROCESS_STARTED + 1234567)
        self.assertEqual(key.process_started, float(PROCESS_STARTED + 1234567))
        as_float = source(process_started=float(PROCESS_STARTED + 1234567))
        self.assertEqual(key, as_float)
        self.assertNotEqual(key, source(process_started=float(PROCESS_STARTED)))

    def test_filestime_differences_below_float_resolution_collapse(self):
        # Honest limitation of the plan's `float` field: FILETIME ticks are 100 ns
        # and float64 resolution near 1.335e17 is ~32 ticks (~3.2 us). A reused
        # PID+HWND whose process was created inside that window would alias; this
        # requires the old process to exit and the new one to reuse both IDs within
        # microseconds, so the remaining risk is documented rather than hidden.
        self.assertEqual(source(process_started=PROCESS_STARTED),
                         source(process_started=PROCESS_STARTED + 1))

    def test_exe_path_is_normalized_but_identity_is_not_display_text(self):
        fancy = source(exe_path='"C:/WeChat/Weixin.exe"')
        plain = source(exe_path="c:\\wechat\\weixin.exe")
        self.assertEqual(fancy, plain)
        self.assertEqual(fancy.exe_path, plain.exe_path)

    def test_invalid_source_fields_rejected(self):
        cases = [
            (dict(pid=0), ValueError),
            (dict(pid=-3), ValueError),
            (dict(hwnd=0), ValueError),
            (dict(process_started=0), ValueError),
            (dict(process_started=-1), ValueError),
            (dict(process_started="now"), TypeError),
            (dict(exe_path=""), ValueError),
            (dict(exe_path="   "), ValueError),
            (dict(pid="4242"), TypeError),
            (dict(hwnd="4660"), TypeError),
        ]
        for override, error in cases:
            with self.assertRaises(error, msg=repr(override)):
                source(**override)

    def test_window_target_from_discovery_maps_to_source_key(self):
        from liteapp.discovery import WindowTarget
        target = WindowTarget(HWND, 4242, EXE, PROCESS_STARTED)
        self.assertEqual(SourceKey.from_window_target(target),
                         SourceKey(EXE, 4242, PROCESS_STARTED, HWND))


class AnalysisKeyTests(unittest.TestCase):
    def test_result_accepted_only_while_every_frozen_field_matches(self):
        frozen = analysis()
        self.assertTrue(frozen.accepts(analysis()))
        changes = [
            dict(source=source(process_started=PROCESS_STARTED + 1234567)),
            dict(conversation_epoch=4),
            dict(snapshot_digest="e" * 64),
            dict(binding_revision=3),
            dict(memory_version=6),
            dict(inference_revision=8),
        ]
        for change in changes:
            current = analysis(**change)
            self.assertFalse(frozen.accepts(current), change)
            self.assertEqual(frozen.differences(current), (next(iter(change)),))

    def test_accepts_fails_closed_for_foreign_objects(self):
        frozen = analysis()
        self.assertFalse(frozen.accepts(None))
        self.assertFalse(frozen.accepts("same-digest"))
        with self.assertRaises(TypeError):
            frozen.differences(None)

    def test_invalid_analysis_key_rejected(self):
        cases = [
            (dict(source="not-a-source"), TypeError),
            (dict(source=None), TypeError),
            (dict(snapshot_digest=""), ValueError),
            (dict(snapshot_digest="   "), ValueError),
            (dict(conversation_epoch=-1), ValueError),
            (dict(binding_revision=-1), ValueError),
            (dict(memory_version=-2), ValueError),
            (dict(inference_revision=-1), ValueError),
            (dict(memory_version="5"), TypeError),
        ]
        for override, error in cases:
            with self.assertRaises(error, msg=repr(override)):
                analysis(**override)

    def test_analysis_key_is_hashable_and_frozen(self):
        frozen = analysis()
        self.assertEqual(len({frozen, analysis()}), 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            frozen.memory_version = 99


class EvidenceRefTests(unittest.TestCase):
    def test_evidence_reference_carries_scope_source_and_character_range(self):
        ref = EvidenceRef(scope(), "source-1", "record-9", 10, 42)
        self.assertEqual((ref.scope, ref.source_id, ref.record_id, ref.start, ref.end),
                         (scope(), "source-1", "record-9", 10, 42))
        self.assertTrue(ref.belongs_to(scope()))
        self.assertFalse(ref.belongs_to(scope(account="wx-2")))
        self.assertTrue(ref.matches(scope(), "source-1"))
        self.assertFalse(ref.matches(scope(), "source-2"))

    def test_invalid_evidence_reference_rejected(self):
        cases = [
            (dict(scope=None), TypeError),
            (dict(scope=("wx", "p")), TypeError),
            (dict(source_id=""), ValueError),
            (dict(record_id="  "), ValueError),
            (dict(start=-1), ValueError),
            (dict(end=-1), ValueError),
            (dict(start=5, end=4), ValueError),
            (dict(start="1"), TypeError),
            (dict(end=1.5), TypeError),
        ]
        base = dict(scope=scope(), source_id="s", record_id="r", start=0, end=1)
        for override, error in cases:
            values = dict(base)
            values.update(override)
            with self.assertRaises(error, msg=repr(override)):
                EvidenceRef(**values)

    def test_evidence_reference_is_frozen(self):
        ref = EvidenceRef(scope(), "s", "r", 0, 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ref.end = 2


class CancellationTokenTests(unittest.TestCase):
    def test_token_starts_clear_and_cancels_idempotently(self):
        token = CancellationToken()
        self.assertFalse(token.is_cancelled())
        self.assertFalse(token.cancelled)
        token.cancel()
        token.cancel()
        self.assertTrue(token.is_cancelled())
        self.assertTrue(token.cancelled)
        with self.assertRaises(CancelledError):
            token.raise_if_cancelled()

    def test_raise_if_cancelled_is_noop_before_cancel(self):
        CancellationToken().raise_if_cancelled()

    def test_cancel_from_another_thread_is_visible(self):
        token = CancellationToken()
        started, observed = threading.Event(), []

        def worker():
            started.set()
            deadline = time.monotonic() + 2
            while not token.is_cancelled() and time.monotonic() < deadline:
                time.sleep(0.005)
            observed.append(token.is_cancelled())

        thread = threading.Thread(target=worker)
        thread.start()
        started.wait(2)
        token.cancel()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(observed, [True])


class ConversationStateContractTests(unittest.TestCase):
    """state.py 接入契约后必须保留原有 token/accepts 语义并增加 AnalysisKey 能力。"""

    def test_binding_scope_invalidates_results_from_previous_scope(self):
        state = ConversationState()
        state.connect("window-1")
        state.bind_scope(scope())
        state.update("Alice", [("her", "", "你好")])
        old_token = state.token
        state.bind_scope(scope(person="person-2"))
        self.assertFalse(state.accepts(old_token))
        state.bind_scope(None)
        self.assertIsNone(state.scope)

    def test_bind_scope_rejects_non_scope_objects(self):
        state = ConversationState()
        for bad in ("wx-account-1", ("wx-account-1", "person-1"), 42):
            with self.assertRaises(TypeError):
                state.bind_scope(bad)
        with self.assertRaises(ValueError):
            state.bind_scope(ScopeKey("", "person-1"))

    def test_snapshot_digest_tracks_title_and_text_only(self):
        state = ConversationState()
        state.connect("window-1")
        state.update("Alice", [("her", "", "你好")])
        first = state.snapshot_digest()
        self.assertEqual(first, state.snapshot_digest())
        self.assertEqual(len(first), 64)
        int(first, 16)
        state.update("Alice", [("her", "", "再见")])
        self.assertNotEqual(first, state.snapshot_digest())
        state.update("Bob", [("her", "", "再见")])
        self.assertNotEqual(first, state.snapshot_digest())

    def test_analysis_key_from_state_rejects_result_after_new_frame(self):
        state = ConversationState()
        state.connect("window-1")
        state.bind_scope(scope())
        state.update("Alice", [("her", "", "你好")])
        frozen = state.analysis_key(source(), conversation_epoch=1, binding_revision=2,
                                    memory_version=3, inference_revision=4)
        self.assertTrue(frozen.accepts(state.analysis_key(
            source(), conversation_epoch=1, binding_revision=2,
            memory_version=3, inference_revision=4)))
        state.update("Alice", [("her", "", "你好，再便宜一点")])
        self.assertFalse(frozen.accepts(state.analysis_key(
            source(), conversation_epoch=1, binding_revision=2,
            memory_version=3, inference_revision=4)))

    def test_analysis_key_from_state_rejects_non_source(self):
        state = ConversationState()
        state.connect("window-1")
        state.update("Alice", [("her", "", "你好")])
        with self.assertRaises(TypeError):
            state.analysis_key("window-1", conversation_epoch=1, binding_revision=0,
                               memory_version=0, inference_revision=0)


if __name__ == "__main__":
    unittest.main()
