"""验收用例：导入后本地自动生成联系人基础档案（不调用模型、不泄漏原文）。

覆盖用户新需求（Lead 2026-10-18）：
- build_local_profile 从 HistoryStore 记录/统计生成 relation（默认
  “普通联系人/待确认”）、topics（本地关键词 + 来源格式标签）、important_dates
  （生日/纪念日/日期正则）；
- 保存到 ContactProfileStore 且 model_analyzed=False；needs_model_analysis 判断；
- 档案字段不包含原始聊天正文，只有话题名/计数/规范化日期；
- 已有档案的收藏/亲密度/模型画像在重新导入时保留，但 model_analyzed 重置。
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from liteapp.auto_profile import (
    DEFAULT_RELATION,
    build_local_profile,
    needs_model_analysis,
)
from liteapp.contact_profile import ContactProfile, ContactProfileStore
from liteapp.history import HistoryStore
from liteapp.history_models import ImportSource, ImportedRecord


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def digest(seed):
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


SAMPLE_TEXTS = [
    "客户：预算多少？本周能报价吗？",
    "客户：我生日是 5 月 20 日，不要忘记。",
    "客户：结婚纪念日是 6 月 1 日。",
    "客户：交期两周，发货前通知我。",
    "客户：喜欢红茶，不要香菜。",
]


class AutoProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.history = HistoryStore(self.folder / "memory.sqlite3",
                                    protect=xor_protect, unprotect=xor_unprotect)
        self.scope = self.history.create_person("合成客户", "账号-A")
        self.profile_store = ContactProfileStore(self.folder,
                                                  protect=xor_protect,
                                                  unprotect=xor_unprotect)
        self.import_records(SAMPLE_TEXTS, name="synthetic.html", kind="html")

    def import_records(self, texts, *, name="synthetic.html", kind="html",
                       sender="客户", prefix="seed"):
        source = ImportSource(digest=digest(f"{prefix}:{name}:{kind}"),
                              kind=kind, name=name)
        records = [
            ImportedRecord(text=text, ordinal=index, record_type="message",
                           sender=sender, sent_at="2026-01-01 10:00",
                           locator={"kind": kind, "section": index + 1})
            for index, text in enumerate(texts)
        ]
        return self.history.import_records(self.scope, source, records)

    def build(self, style_profile=None):
        return build_local_profile(self.history, self.scope,
                                   style_profile=style_profile,
                                   profile_store=self.profile_store)

    def test_basic_fields_and_saved_base_profile(self):
        profile = self.build()
        self.assertIsInstance(profile, ContactProfile)
        self.assertEqual(profile.title, "合成客户")
        self.assertEqual(profile.scope_person_id, self.scope.person_id)
        self.assertEqual(profile.relation, DEFAULT_RELATION)
        self.assertIs(profile.model_analyzed, False)
        self.assertIsNotNone(profile.topics)
        self.assertIn("预算与价格", profile.topics)
        self.assertIn("库存与交期", profile.topics)
        self.assertTrue(profile.updated_at)
        self.assertTrue(needs_model_analysis(profile))

        saved = self.profile_store.get("合成客户", self.scope.person_id)
        self.assertIsNotNone(saved)
        self.assertEqual(saved.relation, DEFAULT_RELATION)
        self.assertIs(saved.model_analyzed, False)

    def test_important_dates_are_labelled_and_normalized(self):
        profile = self.build()
        self.assertIsNotNone(profile.important_dates)
        self.assertTrue(any("生日" in item for item in profile.important_dates))
        self.assertTrue(any("5月20日" in item for item in profile.important_dates))
        self.assertTrue(any("纪念日" in item for item in profile.important_dates))
        self.assertTrue(any("6月1日" in item for item in profile.important_dates))
        # 只输出标签+日期，不输出原始句子。
        self.assertFalse(any("不要忘记" in item for item in profile.important_dates))

    def test_multiple_labelled_dates_in_one_message(self):
        self.import_records(["客户：生日 7月8日，结婚纪念日 9月9日。"],
                            name="dates.html", kind="html", prefix="dates")
        profile = self.build()
        self.assertIn("生日：7月8日", profile.important_dates)
        self.assertIn("纪念日：9月9日", profile.important_dates)

    def test_topics_include_source_kind_labels_and_no_raw_text(self):
        self.import_records(["客户：合同附件是 PDF，售后条款也在里面。"],
                            name="synthetic.pdf", kind="pdf", prefix="pdf")
        profile = self.build()
        self.assertIn("HTML", profile.topics)
        self.assertIn("PDF", profile.topics)
        # 档案里不出现合成原文（含独特标记），只有话题名/来源标签/日期。
        unique = "绝密原文-UNIQUE-9F3A"
        self.import_records([f"客户：{unique} 先看报价单。"],
                            name="secret.html", kind="html", prefix="secret")
        profile = self.build()
        haystack = json.dumps(profile.as_dict(), ensure_ascii=False)
        self.assertNotIn(unique, haystack)
        self.assertNotIn("先看报价单", haystack)

    def test_empty_scope_still_creates_base_profile(self):
        empty_scope = self.history.create_person("空记录联系人", "账号-A")
        profile = build_local_profile(self.history, empty_scope,
                                      profile_store=self.profile_store)
        self.assertEqual(profile.relation, DEFAULT_RELATION)
        self.assertIsNone(profile.topics)
        self.assertIsNone(profile.important_dates)
        self.assertIs(profile.model_analyzed, False)
        self.assertTrue(needs_model_analysis(profile))

    def test_style_profile_is_duck_typed_and_merged(self):
        style = SimpleNamespace(prompt_style="提示词风格", display_report="用户报告",
                                reply_style="简短直接")
        profile = self.build(style_profile=style)
        self.assertEqual(profile.style_prompt, "提示词风格")
        self.assertEqual(profile.display_report, "用户报告")
        self.assertEqual(profile.reply_style, "简短直接")
        self.assertIs(profile.model_analyzed, False)

    def test_existing_user_fields_survive_reimport(self):
        self.profile_store.save(ContactProfile(
            title="合成客户", scope_person_id=self.scope.person_id,
            favorite=True, affection_score=0.7, persona="已有模型画像",
            relation="老客户", topics="旧话题", reply_style="旧风格",
            important_dates="旧日期", style_prompt="旧提示", model_analyzed=True))
        profile = self.build()
        self.assertTrue(profile.favorite)
        self.assertAlmostEqual(profile.affection_score, 0.7)
        self.assertEqual(profile.persona, "已有模型画像")
        self.assertEqual(profile.relation, "老客户")
        self.assertEqual(profile.style_prompt, "旧提示")
        # 新导入后基础档案重新生成，需要模型再次逐句分析。
        self.assertIs(profile.model_analyzed, False)
        self.assertTrue(needs_model_analysis(profile))

    def test_needs_model_analysis_after_model_mark(self):
        profile = self.build()
        self.assertTrue(needs_model_analysis(profile))
        profile.model_analyzed = True
        self.profile_store.save(profile)
        reloaded = self.profile_store.get("合成客户", self.scope.person_id)
        self.assertFalse(needs_model_analysis(reloaded))
        self.assertTrue(needs_model_analysis(None))

    def test_default_profile_store_is_history_folder(self):
        try:
            profile = build_local_profile(self.history, self.scope)
        except OSError:
            self.skipTest("Windows DPAPI unavailable in this environment")
        self.assertIsNotNone(profile)
        store = ContactProfileStore(self.folder)
        saved = store.get("合成客户", self.scope.person_id)
        self.assertIsNotNone(saved)
        self.assertIs(saved.model_analyzed, False)


if __name__ == "__main__":
    unittest.main()
