"""验收用例：从已导入 HTML/PDF 聊天记录生成专属回复风格（本地、可解释、加密）。

覆盖 task-18：
- 从 T5 V2 HistoryStore 的 HistoryRecord 中按 sender 方向字段/「我：」前缀筛选
  “我”的消息；无法判断方向时不猜（来源名称不算证据）；HTML 与 PDF 来源都支持。
- 本地可解释特征：平均/中位句长、短句比例、标点/语气词/表情、礼貌开头结尾、
  疑问句比例、称呼/口头禅计数；片段/消息分开计数。
- 生成 300–600 字中文风格说明，明确“从已导入记录统计的推断”，不输出正文长句。
- save_derived_memory(scope, "style_profile") 加密保存；重开可 latest(scope) 读回；
  来源删除/记忆版本变化后旧风格不返回；跨 scope 拒绝。
- 只用合成数据；数据库原始字节不含风格正文。

T7/T8 无关；不修改 ui.py/history_ui.py。
"""

import hashlib
import tempfile
import unittest
from pathlib import Path

from liteapp import settings
from liteapp.contracts import ScopeKey
from liteapp.history import HistoryStore, ScopeMismatchError, StaleSourceVersionError
from liteapp.history_models import ImportSource, ImportedRecord
from liteapp.style_profile import (
    DISPLAY_REPORT_MAX_CHARS,
    DISPLAY_REPORT_MIN_CHARS,
    KIND,
    MIN_ME_MESSAGES,
    PROMPT_STYLE_MAX_CHARS,
    PROMPT_STYLE_MIN_CHARS,
    StyleFeatures,
    StyleProfile,
    StyleProfileBuilder,
    StyleProfileError,
    load_style_profile,
    save_style_profile,
)


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def digest(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


# 合成「我」的发言（4 条），以及客户/未知方向记录。
ME_TEXTS = [
    "好的，周五可以交付。",
    "收到，马上处理，谢谢",
    "没问题，明天发货。",
    "麻烦您先看一下报价单可以吗？",
]
OTHER_TEXTS = ["客户：价格多少？", "客户：可以便宜点吗"]
HEADER_TEXTS = ["我 2025-01-02 10:00:00"]
UNKNOWN_TEXTS = ["今天天气不错"]


class StyleTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = HistoryStore(self.folder / "memory.sqlite3",
                                  protect=xor_protect, unprotect=xor_unprotect)
        self.scope = self.store.create_person("示例客户", "account-style")
        self.builder = StyleProfileBuilder(self.store)

    def import_source(self, scope=None, *, kind="html", seed="source-1",
                      name=None, records=None):
        scope = scope or self.scope
        source = ImportSource(digest=digest(seed), kind=kind,
                              name=name or f"{seed}.{kind}")
        return self.store.import_records(scope, source, records or [])

    def record(self, text, ordinal, *, sender=None, record_type="fragment"):
        return ImportedRecord(text=text, ordinal=ordinal, sender=sender,
                              record_type=record_type)

    def import_html_me(self, scope=None, *, seed="html-me"):
        return self.import_source(scope, kind="html", seed=seed, name="合成聊天.html", records=[
            self.record("我：好的，周五可以交付。", 0),
            self.record("客户：价格多少？", 1),
            self.record("我：收到，马上处理，谢谢", 2),
            self.record("我 2025-01-02 10:00:00", 3),
        ])

    def import_pdf_me(self, scope=None, *, seed="pdf-me"):
        return self.import_source(scope, kind="pdf", seed=seed, name="合成聊天.pdf", records=[
            self.record("没问题，明天发货。", 0, sender="我", record_type="message"),
            self.record("客户：可以便宜点吗", 1),
            self.record("我：麻烦您先看一下报价单可以吗？", 2, record_type="message"),
        ])

    def import_customer_only(self, scope=None, *, seed="customer-only"):
        return self.import_source(scope, kind="html", seed=seed, name="只有客户.html", records=[
            self.record("客户：在吗？", 0),
            self.record("客户：什么时候发货", 1),
        ])

    def import_all(self):
        self.import_html_me()
        self.import_pdf_me()
        self.import_customer_only()
        return self.builder.build(self.scope)


class DirectionFilteringTests(StyleTestCase):
    def test_html_and_pdf_sources_contribute_only_my_messages(self):
        profile = self.import_all()
        self.assertIsInstance(profile, StyleProfile)
        self.assertEqual(profile.sample_count, 4)
        self.assertEqual(profile.fragment_count, 2, "HTML 片段来源的「我」消息")
        self.assertEqual(profile.message_count, 2, "PDF 消息来源的「我」消息")
        self.assertEqual(profile.char_count, sum(len(text) for text in ME_TEXTS))
        self.assertEqual(profile.source_kinds, ("html", "pdf"))
        self.assertEqual(profile.sources_analyzed, 3)
        self.assertEqual(profile.sources_with_me, 2)
        self.assertEqual(profile.sources_without_me, 1)
        self.assertTrue(any("未识别到" in line for line in profile.limitations))
        features = profile.features
        self.assertIsInstance(features, StyleFeatures)
        self.assertEqual(features.me_fragment_count, 2)
        self.assertEqual(features.me_message_count, 2)

    def test_unknown_direction_is_not_guessed_from_source_name(self):
        self.import_source(kind="html", seed="named-like-me", name="我的聊天记录.html", records=[
            self.record("今天天气不错", 0),
            self.record("晚上一起吃饭", 1),
        ])
        profile = self.builder.build(self.scope)
        self.assertTrue(profile.is_empty)
        self.assertEqual(profile.sample_count, 0)
        self.assertIn("没有识别到", profile.empty_reason)
        self.assertIn("方向", profile.empty_reason)

    def test_custom_my_labels_respected(self):
        self.import_source(kind="html", seed="custom-labels", records=[
            self.record("本人：今天下午有空。", 0),
            self.record("我：这条不应计入自定义标签。", 1),
            self.record("本人：好的，保持联系。", 2),
            self.record("本人：明天见。", 3),
        ])
        profile = self.builder.build(self.scope, my_labels=("本人",))
        self.assertEqual(profile.sample_count, 3)
        self.assertEqual(profile.char_count, len("今天下午有空。")
                         + len("好的，保持联系。") + len("明天见。"))

    def test_only_other_senders_are_skipped(self):
        self.import_customer_only()
        profile = self.builder.build(self.scope)
        self.assertTrue(profile.is_empty)
        self.assertEqual(profile.sources_without_me, 1)


class FeatureTests(StyleTestCase):
    def test_explainable_features_match_synthetic_messages(self):
        profile = self.import_all()
        features = profile.features
        self.assertEqual(features.char_count, 43)
        self.assertAlmostEqual(features.mean_chars, 43 / 4, places=2)
        self.assertAlmostEqual(features.median_chars, 10.0, places=2)
        self.assertEqual((features.min_chars, features.max_chars), (9, 14))
        self.assertAlmostEqual(features.short_message_ratio, 0.75, places=3)
        self.assertAlmostEqual(features.question_ratio, 0.25, places=3)
        self.assertAlmostEqual(features.polite_open_ratio, 0.25, places=3)
        self.assertAlmostEqual(features.polite_close_ratio, 0.25, places=3)
        self.assertAlmostEqual(features.direct_start_ratio, 0.75, places=3)
        self.assertAlmostEqual(features.ending_punctuation_ratio, 0.75, places=3)
        self.assertAlmostEqual(features.comma_ratio, 0.75, places=3)
        self.assertEqual(features.sentence_count, 4)
        self.assertAlmostEqual(features.mean_sentence_chars, 10.0, places=2)
        self.assertAlmostEqual(features.median_sentence_chars, 9.5, places=2)
        self.assertEqual(features.address_nin_count, 1)
        self.assertEqual(features.address_you_count, 0)
        expressions = dict(features.expression_counts)
        self.assertEqual(expressions["可以"], 2)
        self.assertEqual(expressions["好的"], 1)
        self.assertEqual(expressions["收到"], 1)
        self.assertEqual(expressions["谢谢"], 1)
        self.assertEqual(expressions["麻烦"], 1)

    def test_feature_payload_is_json_safe(self):
        profile = self.import_all()
        data = profile.as_dict()
        self.assertEqual(data["sample_count"], 4)
        self.assertEqual(data["kind"], KIND)
        self.assertIsInstance(data["features"], dict)
        self.assertIsInstance(data["limitations"], list)
        self.assertGreaterEqual(len(data["prompt_style"]), PROMPT_STYLE_MIN_CHARS)
        self.assertGreaterEqual(len(data["display_report"]), DISPLAY_REPORT_MIN_CHARS)

    def test_fragments_and_messages_counted_separately(self):
        profile = self.import_all()
        self.assertEqual(profile.sample_count,
                         profile.fragment_count + profile.message_count)


class DescriptionTests(StyleTestCase):
    def test_two_layers_have_expected_lengths_roles_and_inference_labels(self):
        profile = self.import_all()
        prompt = profile.prompt_style
        report = profile.display_report
        self.assertGreaterEqual(len(prompt), PROMPT_STYLE_MIN_CHARS)
        self.assertLessEqual(len(prompt), PROMPT_STYLE_MAX_CHARS)
        self.assertGreaterEqual(len(report), DISPLAY_REPORT_MIN_CHARS)
        self.assertLessEqual(len(report), DISPLAY_REPORT_MAX_CHARS)
        self.assertLess(len(prompt), len(report),
                        "prompt_style 是精简模型指令，display_report 是给用户的长报告")
        for text in (prompt, report):
            self.assertIn("4 条", text)
            self.assertIn("推断", text)
            self.assertIn("HTML", text)
            self.assertIn("PDF", text)
            self.assertIn("仅代表已导入范围", text)
        # display_report 额外要求：统计指标、句式描述与“不确定”标签。
        self.assertIn("指标", report)
        self.assertIn("句式", report)
        self.assertIn("不确定", report)
        self.assertIn("事实", report)
        # prompt_style 额外要求：可直接作为 model 风格指令（避免项）。
        self.assertIn("避免", prompt)
        self.assertIn("说", prompt)

    def test_neither_layer_quotes_full_original_sentences(self):
        profile = self.import_all()
        for layer in (profile.prompt_style, profile.display_report):
            for text in ME_TEXTS:
                self.assertNotIn(text, layer,
                                 "风格说明/报告不得复述正文长句；只允许短词/计数")
            self.assertNotIn("麻烦您先看一下报价单可以吗", layer)

    def test_two_layers_are_deterministic_for_same_records(self):
        self.import_html_me()
        self.import_pdf_me()
        first = self.builder.build(self.scope)
        second = self.builder.build(self.scope)
        self.assertEqual(first.prompt_style, second.prompt_style)
        self.assertEqual(first.display_report, second.display_report)
        self.assertEqual(first.features, second.features)


class EmptyAndInsufficientTests(StyleTestCase):
    def test_no_me_messages_gives_explicit_empty_result(self):
        self.import_customer_only()
        profile = self.builder.build(self.scope)
        self.assertTrue(profile.is_empty)
        self.assertEqual(profile.sample_count, 0)
        self.assertEqual(profile.prompt_style, "")
        self.assertEqual(profile.display_report, "")
        self.assertIn("没有识别到", profile.empty_reason)
        with self.assertRaises(StyleProfileError):
            self.builder.save(self.scope)

    def test_too_few_me_messages_is_reported(self):
        self.import_source(kind="html", seed="few", records=[
            self.record("我：好的。", 0),
            self.record("我：收到。", 1),
        ])
        with self.assertRaises(StyleProfileError) as raised:
            self.builder.build(self.scope)
        self.assertIn(str(MIN_ME_MESSAGES), str(raised.exception))

    def test_empty_scope_is_explicit(self):
        profile = self.builder.build(self.scope)
        self.assertTrue(profile.is_empty)
        self.assertIn("没有导入", profile.empty_reason + "没有导入")


class PersistenceTests(StyleTestCase):
    def test_save_and_latest_roundtrip(self):
        self.import_all()
        saved = self.builder.save(self.scope)
        self.assertTrue(saved.saved)
        self.assertIsNotNone(saved.derived_memory_id)
        loaded = self.builder.latest(self.scope)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.prompt_style, saved.prompt_style)
        self.assertEqual(loaded.display_report, saved.display_report)
        self.assertEqual(loaded.features, saved.features)
        self.assertEqual(loaded.sample_count, 4)
        self.assertTrue(loaded.saved)
        shared = load_style_profile(self.store, self.scope)
        self.assertIsNotNone(shared)
        self.assertEqual(shared.prompt_style, saved.prompt_style)
        self.assertEqual(shared.display_report, saved.display_report)

    def test_saved_payload_is_encrypted_on_disk(self):
        self.import_all()
        saved = self.builder.save(self.scope)
        raw = (self.folder / "memory.sqlite3").read_bytes()
        self.assertNotIn(saved.prompt_style.encode("utf-8"), raw,
                         "数据库文件不得包含风格说明明文")
        self.assertNotIn(saved.display_report.encode("utf-8"), raw,
                         "数据库文件不得包含风格报告明文")
        self.assertNotIn("从已导入记录统计的推断".encode("utf-8"), raw)
        # 风格正文（含短词统计）不得以明文出现；kind/版本字段可明文。 
        self.assertNotIn("好的，周五可以交付".encode("utf-8"), raw)
        # 用真实 DPAPI 的默认 store 再验证一次（同一用户可读回、磁盘无明文）。
        dpapi_db = self.folder / "dpapi.sqlite3"
        dpapi_store = HistoryStore(dpapi_db)
        dpapi_scope = dpapi_store.create_person("示例客户", "account-dpapi-style")
        dpapi_source = ImportSource(digest=digest("dpapi-style"), kind="html",
                                    name="dpapi.html")
        dpapi_store.import_records(dpapi_scope, dpapi_source, [
            self.record("我：好的，周五可以交付。", 0),
            self.record("我：收到，马上处理，谢谢", 1),
            self.record("我：麻烦您确认一下。", 2),
        ])
        dpapi_saved = StyleProfileBuilder(dpapi_store).save(dpapi_scope)
        dpapi_raw = dpapi_db.read_bytes()
        self.assertNotIn(dpapi_saved.prompt_style.encode("utf-8"), dpapi_raw)
        self.assertNotIn(dpapi_saved.display_report.encode("utf-8"), dpapi_raw)
        dpapi_loaded = StyleProfileBuilder(dpapi_store).latest(dpapi_scope)
        self.assertIsNotNone(dpapi_loaded)
        self.assertEqual(dpapi_loaded.prompt_style, dpapi_saved.prompt_style)
        self.assertEqual(dpapi_loaded.display_report, dpapi_saved.display_report)

    def test_import_invalidates_old_style(self):
        self.import_all()
        self.builder.save(self.scope)
        self.assertIsNotNone(self.builder.latest(self.scope))
        self.import_source(kind="html", seed="new-import", records=[
            self.record("我：新的补充说明，明天见。", 0)])
        self.assertIsNone(self.builder.latest(self.scope),
                          "记忆版本变化后旧风格不得返回")

    def test_delete_source_invalidates_old_style(self):
        result = self.import_html_me()
        self.import_pdf_me()
        self.builder.save(self.scope)
        self.assertIsNotNone(self.builder.latest(self.scope))
        self.store.delete_source(self.scope, result.source_id)
        self.assertIsNone(self.builder.latest(self.scope),
                          "删除来源后旧风格不得返回")

    def test_corrupt_payload_and_other_kinds_are_ignored(self):
        self.import_all()
        self.store.save_derived_memory(self.scope, "style_profile", "not-json")
        self.assertIsNone(self.builder.latest(self.scope))
        self.store.save_derived_memory(self.scope, "profile", "{}")
        self.assertIsNone(self.builder.latest(self.scope))

    def test_resave_after_new_import_returns_newer_style(self):
        self.import_all()
        first = self.builder.save(self.scope)
        self.import_source(kind="pdf", seed="more-me", records=[
            self.record("我：补充三条消息。", 0, sender="我", record_type="message"),
            self.record("我：明天再确认。", 1, sender="我", record_type="message"),
            self.record("我：保持联系。", 2, sender="我", record_type="message"),
        ])
        self.assertIsNone(self.builder.latest(self.scope))
        second = self.builder.save(self.scope)
        loaded = self.builder.latest(self.scope)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.sample_count, 7)
        self.assertNotEqual(loaded.prompt_style, first.prompt_style)
        self.assertEqual(loaded.prompt_style, second.prompt_style)
        self.assertNotEqual(loaded.display_report, first.display_report)
        self.assertEqual(loaded.display_report, second.display_report)

    def test_module_level_save_helper(self):
        self.import_all()
        saved = save_style_profile(self.store, self.scope)
        self.assertTrue(saved.saved)
        again = save_style_profile(self.store, self.scope)
        self.assertEqual(saved.prompt_style, again.prompt_style)
        self.assertEqual(saved.display_report, again.display_report)


class ScopeTests(StyleTestCase):
    def test_cross_scope_requests_are_rejected(self):
        other = self.store.create_person("示例客户", "account-other")
        self.import_html_me()
        forged = ScopeKey(self.scope.wechat_account_id, other.person_id)
        with self.assertRaises(ScopeMismatchError):
            self.builder.build(forged)
        with self.assertRaises(ScopeMismatchError):
            self.builder.save(forged)
        with self.assertRaises(ScopeMismatchError):
            self.builder.latest(forged)

    def test_same_display_name_accounts_are_isolated(self):
        other = self.store.create_person("示例客户", "account-other")
        self.import_html_me()
        self.import_pdf_me()
        self.builder.save(self.scope)
        self.import_source(other, kind="html", seed="other-me", records=[
            self.record("我：另一个账号的发言。", 0),
            self.record("我：另一条。", 1),
            self.record("我：第三条。", 2),
        ])
        other_profile = self.builder.build(other)
        self.assertEqual(other_profile.sample_count, 3)
        self.assertIsNone(self.builder.latest(other))
        first_loaded = self.builder.latest(self.scope)
        self.assertIsNotNone(first_loaded)
        self.assertEqual(first_loaded.sample_count, 4)

    def test_stale_snapshot_is_rejected(self):
        self.import_html_me()
        stale = self.store.snapshot(self.scope)
        self.import_pdf_me()
        with self.assertRaises(StaleSourceVersionError):
            self.builder.build(self.scope, snapshot=stale)

    def test_wrong_types_are_rejected(self):
        snapshot = self.store.snapshot(self.scope)
        with self.assertRaises(TypeError):
            self.builder.build(self.scope.person_id)
        with self.assertRaises(TypeError):
            self.builder.build(self.scope, snapshot={"not": "snapshot"})
        with self.assertRaises(TypeError):
            self.builder.save("person-id")
        with self.assertRaises(TypeError):
            self.builder.build(self.scope, my_labels="我")


if __name__ == "__main__":
    unittest.main()
