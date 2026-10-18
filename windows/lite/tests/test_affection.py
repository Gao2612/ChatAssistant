"""好感度本地估算（liteapp.affection）验收测试。

只用合成消息；纯本地启发式，不含真实聊天、网络或模型调用。
"""
import unittest

from liteapp.affection import (
    AFFECTION_DIMENSIONS,
    BOUNDARY_CAP,
    AffectionProfile,
    DimensionScore,
    boundary_signal_score,
    estimate_affection,
    level_label_for,
)
from liteapp.history_models import HistoryRecord

WARM = [
    ("other", "她", "在吗？最近怎么样？我最近在准备考试，有点累。"),
    ("other", "她", "注意休息，别太累，早点睡。"),
    ("other", "她", "我觉得你说得对，其实我之前也这么想。"),
    ("other", "她", "周末要不要一起吃饭？我订好了位置。"),
    ("other", "她", "想你，抱抱，最喜欢和你聊天了。"),
    ("other", "她", "其实我最近有点担心工作的事，想跟你说说。"),
    ("other", "她", "那就这么定了，不见不散。"),
]
COLD = [
    ("other", "她", "嗯"),
    ("other", "她", "哦"),
    ("other", "她", "忙"),
    ("other", "她", "再说吧"),
    ("other", "她", "随便"),
    ("other", "她", "知道了"),
]
HOSTILE = [
    ("other", "她", "别联系我了"),
    ("other", "她", "我不想聊，先这样吧"),
]


def history_record(sender, text, *, record_id="r1", ordinal=0):
    return HistoryRecord(record_id=record_id, source_id="synthetic-source",
                         ordinal=ordinal, record_type="message", sender=sender,
                         sent_at=None, text=text, locator=None, quality=None,
                         char_count=len(text))


class ScoreDirectionTests(unittest.TestCase):
    def test_warm_messages_score_higher_than_cold_messages(self):
        warm = estimate_affection(WARM)
        cold = estimate_affection(COLD)
        self.assertGreater(warm.score, cold.score)
        self.assertGreater(warm.score, 50)
        self.assertNotEqual(warm.level_label, "低")
        self.assertEqual(warm.boundary_applied, False)
        self.assertEqual(warm.other_messages, len(WARM))

    def test_weights_match_reference_and_sum_to_one_hundred(self):
        self.assertEqual([item["key"] for item in AFFECTION_DIMENSIONS],
                         ["initiative", "engagement", "care", "openness",
                          "intimacy", "action"])
        self.assertEqual([item["weight"] for item in AFFECTION_DIMENSIONS],
                         [15, 20, 20, 15, 20, 10])
        self.assertEqual(sum(item["weight"] for item in AFFECTION_DIMENSIONS), 100)

    def test_care_and_action_dimensions_react_to_specific_signals(self):
        care = estimate_affection([
            ("other", "她", "注意休息，别太累，感冒就早点睡。"),
            ("other", "她", "还好吗？需要我陪你吗？"),
        ])
        care_dim = next(dim for dim in care.dimensions if dim.key == "care")
        self.assertGreaterEqual(care_dim.level, 3)
        self.assertGreaterEqual(care_dim.evidence_count, 2)

        action = estimate_affection([
            ("other", "她", "周末一起吃饭吧，我订好了位置。"),
            ("other", "她", "那就这么定了，不见不散。"),
        ])
        action_dim = next(dim for dim in action.dimensions if dim.key == "action")
        self.assertGreaterEqual(action_dim.level, 3)
        self.assertGreaterEqual(action_dim.evidence_count, 2)

    def test_hostile_messages_lower_initiative_level(self):
        profile = estimate_affection(HOSTILE)
        initiative = next(dim for dim in profile.dimensions
                          if dim.key == "initiative")
        self.assertLessEqual(initiative.level, 1)
        self.assertTrue(profile.boundary_applied)


class BoundaryTests(unittest.TestCase):
    def test_boundary_signal_caps_total_score(self):
        profile = estimate_affection(WARM + [
            ("other", "她", "但我觉得我们还是保持距离吧，别再联系我了。"),
        ])
        self.assertTrue(profile.boundary_applied)
        self.assertLessEqual(profile.score, BOUNDARY_CAP)
        self.assertEqual(profile.status, "clear")  # explicit rejection is clear
        self.assertGreaterEqual(profile.confidence, 0.7)
        self.assertNotEqual(profile.level_label, "很高")

    def test_boundary_score_is_explainable_and_thresholded(self):
        self.assertGreaterEqual(boundary_signal_score(["我们还是保持距离吧"]), 0.8)
        self.assertEqual(boundary_signal_score(["有空一起吃饭吗"]), 0.0)
        self.assertLess(boundary_signal_score(["别误会"]), 0.8)

    def test_friendly_friendship_phrase_is_not_a_boundary(self):
        profile = estimate_affection([
            ("other", "她", "有空一起吃饭吗？我们是朋友啊，谢谢你。"),
            ("other", "她", "最近怎么样？"),
        ])
        self.assertFalse(profile.boundary_applied)
        self.assertGreater(profile.score, 25)


class DirectionTests(unittest.TestCase):
    def test_my_own_messages_are_never_treated_as_other(self):
        only_mine = estimate_affection([
            ("me", "我", "我喜欢你，想你，抱抱，注意休息，早点睡。"),
        ])
        self.assertEqual(only_mine.other_messages, 0)
        self.assertEqual(only_mine.my_messages, 1)
        self.assertEqual(only_mine.score, 0)
        self.assertEqual(only_mine.sample_messages, ())
        intimacy = next(dim for dim in only_mine.dimensions
                        if dim.key == "intimacy")
        self.assertEqual(intimacy.evidence_count, 0)

        mixed = estimate_affection([
            ("me", "我", "我喜欢你，想你，抱抱。"),
            ("other", "她", "嗯"),
        ])
        self.assertEqual(mixed.other_messages, 1)
        self.assertEqual(mixed.my_messages, 1)
        mixed_intimacy = next(dim for dim in mixed.dimensions
                              if dim.key == "intimacy")
        self.assertEqual(mixed_intimacy.evidence_count, 0)
        self.assertNotIn("我喜欢你", " ".join(mixed.sample_messages))

    def test_unknown_direction_messages_are_skipped(self):
        base = [history_record("other", "在吗？最近怎么样？"),
                ("her", "她", "注意休息，早点睡")]
        with_unknown = base + [
            (None, "未知", "我喜欢你，想你，抱抱，注意休息。"),
            ("system", "系统", "服务器重启"),
            {"who": "unknown", "body": "这条方向未知"},
            history_record(None, "方向未知的导入记录", record_id="r2", ordinal=1),
        ]
        skipped = estimate_affection(with_unknown)
        self.assertEqual(skipped.other_messages, 2)
        self.assertEqual(skipped.my_messages, 0)
        self.assertEqual(skipped, estimate_affection(base))

    def test_history_record_and_ui_tuple_inputs_are_supported(self):
        messages = [
            history_record("other", "周末一起吃饭吧，我订好了位置。"),
            history_record("me", "好的，我很期待。", record_id="r2", ordinal=1),
            ("her", "她", "那就这么定了，不见不散。"),
        ]
        profile = estimate_affection(messages)
        self.assertEqual(profile.other_messages, 2)
        self.assertEqual(profile.my_messages, 1)
        action = next(dim for dim in profile.dimensions if dim.key == "action")
        self.assertGreaterEqual(action.evidence_count, 2)
        self.assertEqual(len(profile.sample_messages), 2)


class SampleAndStatusTests(unittest.TestCase):
    def test_insufficient_samples_status(self):
        empty = estimate_affection([])
        self.assertEqual(empty.status, "insufficient")
        self.assertEqual(empty.confidence, 0.0)
        self.assertEqual(empty.score, 0)

        one = estimate_affection([("other", "她", "你好")])
        self.assertEqual(one.status, "insufficient")
        self.assertLessEqual(one.confidence, 0.35)

        rich = estimate_affection(WARM)
        self.assertEqual(rich.status, "clear")
        self.assertGreater(rich.confidence, 0.5)

    def test_empty_input_does_not_crash(self):
        for value in ([], (), iter(())):
            profile = estimate_affection(value)
            self.assertIsInstance(profile, AffectionProfile)
            self.assertEqual(profile.score, 0)
            self.assertEqual(profile.status, "insufficient")
            self.assertEqual(len(profile.dimensions), 6)
            self.assertEqual(profile.other_messages, 0)
            self.assertEqual(profile.my_messages, 0)

    def test_result_is_deterministic(self):
        first = estimate_affection(WARM)
        second = estimate_affection(list(WARM))
        self.assertEqual(first, second)
        self.assertEqual(first.dimensions, second.dimensions)


class ContractTests(unittest.TestCase):
    def test_output_contract_fields_and_ranges(self):
        profile = estimate_affection(WARM)
        self.assertIsInstance(profile, AffectionProfile)
        self.assertIsInstance(profile.score, int)
        self.assertGreaterEqual(profile.score, 0)
        self.assertLessEqual(profile.score, 100)
        self.assertIsInstance(profile.boundary_applied, bool)
        self.assertGreaterEqual(profile.confidence, 0.0)
        self.assertLessEqual(profile.confidence, 1.0)
        self.assertIn(profile.status, ("insufficient", "ambiguous", "clear"))
        self.assertIsInstance(profile.sample_messages, tuple)
        self.assertLessEqual(len(profile.sample_messages), 3)
        self.assertEqual(len(profile.dimensions), len(AFFECTION_DIMENSIONS))
        for dim in profile.dimensions:
            self.assertIsInstance(dim, DimensionScore)
            self.assertIsInstance(dim.level, int)
            self.assertGreaterEqual(dim.level, 0)
            self.assertLessEqual(dim.level, 4)
            self.assertIsInstance(dim.evidence_count, int)
            self.assertGreaterEqual(dim.evidence_count, 0)
        self.assertEqual(profile.level_label, level_label_for(profile.score))
        self.assertIn(profile.level_label, ("低", "一般", "友好", "较好", "高", "很高"))

    def test_level_label_thresholds_are_monotonic(self):
        expected = [(0, "低"), (19, "低"), (20, "一般"), (34, "一般"),
                    (35, "友好"), (54, "友好"), (55, "较好"), (69, "较好"),
                    (70, "高"), (84, "高"), (85, "很高"), (100, "很高")]
        for score, label in expected:
            self.assertEqual(level_label_for(score), label)
        ordered = ["低", "一般", "友好", "较好", "高", "很高"]
        self.assertEqual([level_label_for(score) for score in (0, 25, 45, 60, 75, 95)],
                         ordered)


if __name__ == "__main__":
    unittest.main()
