"""验收用例：联系人专属档案（加密 JSON、标题规范化、scope 区分、收藏状态）。

用户需求（Lead 2026-10-18）：为每个微信联系人保存专属档案（风格提示词、显示报告、
画像摘要、亲密度、收藏），导入 HTML/PDF 全量记录后按联系人复用；本模块只做本地
加密持久化与 StyleProfile 衔接，不改 ui.py/history_ui.py。

只使用合成标题/风格文本；测试注入 XOR 变换，不依赖 Windows DPAPI；默认路径/默认
DPAPI 仅在隔离的 tests profile 下验证一次。
"""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from liteapp import settings
from liteapp.contact_profile import (
    FILENAME,
    ContactProfile,
    ContactProfileStore,
    normalize_title,
    profile_from_style,
)
from liteapp.contracts import ScopeKey


def xor_protect(raw):
    return bytes(value ^ 0xA5 for value in raw)


def xor_unprotect(raw):
    return bytes(value ^ 0xA5 for value in raw)


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.store = ContactProfileStore(self.folder,
                                         protect=xor_protect,
                                         unprotect=xor_unprotect)

    def profile(self, title="田子夜", scope=None, **kwargs):
        values = dict(style_prompt="专属风格提示：温和、直接。",
                      display_report="风格报告：平均句长 12 字。",
                      persona="合成画像摘要：关注交期与价格。",
                      affection_score=0.8)
        values.update(kwargs)
        return ContactProfile(title=title, scope_person_id=scope, **values)


class SaveReadTests(StoreTestCase):
    def test_save_and_read_back_all_fields(self):
        stored = self.store.save(self.profile())
        self.assertEqual(stored.title, "田子夜")
        self.assertTrue(stored.updated_at)  # 未提供时由 store 补时间
        reloaded = self.store.get("田子夜")
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.style_prompt, "专属风格提示：温和、直接。")
        self.assertEqual(reloaded.display_report, "风格报告：平均句长 12 字。")
        self.assertEqual(reloaded.persona, "合成画像摘要：关注交期与价格。")
        self.assertAlmostEqual(reloaded.affection_score, 0.8)
        self.assertFalse(reloaded.favorite)
        self.assertTrue(reloaded.updated_at)
        self.assertEqual(self.store.all(), [reloaded])

    def test_save_normalizes_title_and_returns_copy(self):
        saved = self.store.save(self.profile(title="  田子夜 "))
        self.assertEqual(saved.title, "田子夜")
        self.assertEqual(self.store.get("田子夜").title, "田子夜")
        self.assertEqual(self.store.get("  田子夜  ").title, "田子夜")
        # 返回值是拷贝：外部改写不污染仓库。
        saved.style_prompt = "被改写的副本"
        self.assertEqual(self.store.get("田子夜").style_prompt,
                         "专属风格提示：温和、直接。")

    def test_overwrite_same_title_and_scope_updates_in_place(self):
        self.store.save(self.profile(style_prompt="第一版"))
        self.store.save(self.profile(style_prompt="第二版"))
        self.assertEqual(self.store.get("田子夜").style_prompt, "第二版")
        self.assertEqual(len(self.store.all()), 1)

    def test_missing_profile_returns_none(self):
        self.assertIsNone(self.store.get("不存在"))
        self.assertFalse(self.store.delete("不存在"))
        self.assertEqual(self.store.all(), [])

    def test_delete_existing_profile(self):
        self.store.save(self.profile())
        self.assertTrue(self.store.delete("田子夜"))
        self.assertIsNone(self.store.get("田子夜"))
        self.assertEqual(self.store.all(), [])

    def test_data_and_favorite_persist_across_new_store(self):
        self.store.save(self.profile())
        self.store.set_favorite("田子夜", True)
        reopened = ContactProfileStore(self.folder, protect=xor_protect,
                                      unprotect=xor_unprotect)
        profile = reopened.get("田子夜")
        self.assertIsNotNone(profile)
        self.assertTrue(profile.favorite)
        self.assertEqual(profile.style_prompt, "专属风格提示：温和、直接。")
        self.assertEqual(len(reopened.all()), 1)

    def test_explicit_updated_at_is_preserved(self):
        self.store.save(self.profile(updated_at="2026-01-02T03:04:05"))
        self.assertEqual(self.store.get("田子夜").updated_at, "2026-01-02T03:04:05")


class TitleNormalizationTests(StoreTestCase):
    def test_nfkc_and_whitespace_are_the_same_key(self):
        self.store.save(self.profile(title="ＡＢＣ 公司"))
        self.assertEqual(self.store.get("ABC 公司").title, "ＡＢＣ 公司")
        self.assertEqual(self.store.get("abc公司").title, "ＡＢＣ 公司")
        self.store.save(self.profile(title="ABC公司", style_prompt="覆盖版"))
        self.assertEqual(len(self.store.all()), 1)
        self.assertEqual(self.store.get("ＡＢＣ 公司").style_prompt, "覆盖版")

    def test_normalize_title_matches_contact_key_semantics(self):
        self.assertEqual(normalize_title(" Ａ Ｂ Ｃ "), "abc")
        self.assertEqual(normalize_title("田 子 夜"), "田子夜")
        self.assertEqual(normalize_title("  \t\n "), "")


class ScopeSeparationTests(StoreTestCase):
    def test_same_title_different_scope_coexist(self):
        self.store.save(self.profile(title="田子夜", scope="person-a",
                                     style_prompt="A 的风格"))
        self.store.save(self.profile(title=" 田 子 夜 ", scope="person-b",
                                     style_prompt="B 的风格"))
        self.assertEqual(len(self.store.all()), 2)
        self.assertEqual(self.store.get("田子夜", "person-a").style_prompt, "A 的风格")
        self.assertEqual(self.store.get("田子夜", "person-b").style_prompt, "B 的风格")
        # 未给 scope 且同名多档案：拒绝猜测，返回 None。
        self.assertIsNone(self.store.get("田子夜"))

    def test_delete_one_scope_keeps_the_other(self):
        self.store.save(self.profile(title="田子夜", scope="person-a"))
        self.store.save(self.profile(title="田子夜", scope="person-b"))
        self.assertFalse(self.store.delete("田子夜"))  # 有歧义，不删
        self.assertTrue(self.store.delete("田子夜", "person-a"))
        self.assertIsNone(self.store.get("田子夜", "person-a"))
        self.assertIsNotNone(self.store.get("田子夜", "person-b"))

    def test_empty_scope_is_none_scope(self):
        self.store.save(self.profile(scope="  "))
        self.assertIsNotNone(self.store.get("田子夜"))
        self.assertIsNone(self.store.get("田子夜").scope_person_id)


class FavoriteTests(StoreTestCase):
    def test_toggle_favorite_existing_profile(self):
        self.store.save(self.profile())
        saved = self.store.set_favorite("田子夜", True)
        self.assertTrue(saved.favorite)
        self.assertTrue(self.store.get("田子夜").favorite)
        self.assertTrue(saved.updated_at)
        self.store.set_favorite("田子夜", False)
        self.assertFalse(self.store.get("田子夜").favorite)

    def test_favorite_missing_profile_creates_minimal_profile(self):
        created = self.store.set_favorite("新联系人", True)
        self.assertTrue(created.favorite)
        self.assertEqual(created.title, "新联系人")
        self.assertTrue(created.updated_at)
        self.assertEqual(self.store.get("新联系人").favorite, True)

    def test_favorite_with_scope(self):
        self.store.save(self.profile(scope="person-a"))
        self.store.save(self.profile(scope="person-b"))
        with self.assertRaises(ValueError):
            self.store.set_favorite("田子夜", True)  # 歧义必须显式给 scope
        self.store.set_favorite("田子夜", True, "person-a")
        self.assertTrue(self.store.get("田子夜", "person-a").favorite)
        self.assertFalse(self.store.get("田子夜", "person-b").favorite)

    def test_empty_title_rejected(self):
        with self.assertRaises(ValueError):
            ContactProfile(title="   ")
        with self.assertRaises(ValueError):
            self.store.set_favorite("", True)
        self.assertIsNone(self.store.get(""))
        self.assertFalse(self.store.delete(""))


class EncryptionTests(StoreTestCase):
    def test_file_does_not_contain_plaintext_style_or_persona(self):
        self.store.save(self.profile())
        raw = self.store.path.read_bytes()
        self.assertTrue(raw)
        for plaintext in (b"\xe7\x94\xb0\xe5\xad\x90\xe5\xa4\x9c",  # 田子夜
                          "专属风格提示".encode("utf-8"),
                          "合成画像摘要".encode("utf-8"),
                          b"display_report", b"persona"):
            self.assertNotIn(plaintext, raw)
        self.assertFalse(raw.startswith(b"{"))

    def test_file_roundtrips_through_injected_protection_only(self):
        self.store.save(self.profile(style_prompt="加密往返"))
        raw = self.store.path.read_bytes()
        # 只有注入的 unprotect 能读回，直接 JSON 解析失败。
        with self.assertRaises(ValueError):
            json.loads(raw)
        self.assertEqual(self.store.get("田子夜").style_prompt, "加密往返")

    def test_corrupt_file_is_fail_soft(self):
        self.store.path.write_bytes(b"this is not a protected profiles file")
        self.assertEqual(self.store.all(), [])
        self.assertIsNone(self.store.get("田子夜"))
        self.assertFalse(self.store.delete("田子夜"))
        # 下一次保存可恢复，并把损坏文件备份。
        self.store.save(self.profile(style_prompt="恢复后"))
        self.assertEqual(self.store.get("田子夜").style_prompt, "恢复后")
        self.assertTrue(self.store.path.with_name(self.store.path.name + ".corrupt").exists())

    def test_wrong_or_failing_unprotect_is_fail_soft(self):
        self.store.save(self.profile())
        broken = ContactProfileStore(self.folder,
                                     protect=xor_protect,
                                     unprotect=lambda raw: (_ for _ in ()).throw(ValueError("bad")))
        self.assertEqual(broken.all(), [])
        self.assertIsNone(broken.get("田子夜"))

    def test_default_path_is_under_data_dir(self):
        store = ContactProfileStore()
        self.assertEqual(store.path.name, FILENAME)
        self.assertEqual(store.path.parent, settings.data_dir())

    def test_default_dpapi_roundtrip_in_isolated_profile(self):
        try:
            store = ContactProfileStore(self.folder)
            store.save(self.profile())
            raw = store.path.read_bytes()
        except OSError:
            self.skipTest("Windows DPAPI unavailable in this environment")
        self.assertFalse(raw.startswith(b"{"))
        self.assertEqual(store.get("田子夜").style_prompt, "专属风格提示：温和、直接。")


class ExtendedProfileTests(StoreTestCase):
    def test_new_profile_fields_roundtrip(self):
        self.store.save(self.profile(
            relation="客户/采购负责人", topics="预算与价格（3 条）；来源：HTML 1 个、PDF 1 个",
            preferences="喜欢先看报价单", taboos="不要打电话，先发微信",
            reply_style="简短、直接、礼貌", important_dates="生日：5月20日；纪念日：6月1日",
            persona="合成画像摘要", model_analyzed=True))
        reloaded = self.store.get("田子夜")
        self.assertEqual(reloaded.relation, "客户/采购负责人")
        self.assertIn("预算与价格", reloaded.topics)
        self.assertEqual(reloaded.preferences, "喜欢先看报价单")
        self.assertEqual(reloaded.taboos, "不要打电话，先发微信")
        self.assertEqual(reloaded.reply_style, "简短、直接、礼貌")
        self.assertIn("生日", reloaded.important_dates)
        self.assertEqual(reloaded.persona, "合成画像摘要")
        self.assertIs(reloaded.model_analyzed, True)

    def test_new_fields_default_to_none_and_not_analyzed(self):
        profile = ContactProfile(title="仅标题")
        self.assertIsNone(profile.relation)
        self.assertIsNone(profile.topics)
        self.assertIsNone(profile.preferences)
        self.assertIsNone(profile.taboos)
        self.assertIsNone(profile.reply_style)
        self.assertIsNone(profile.important_dates)
        self.assertIs(profile.model_analyzed, False)
        for key in ("relation", "topics", "preferences", "taboos", "reply_style",
                    "important_dates", "model_analyzed"):
            self.assertIn(key, profile.as_dict())

    def test_old_payload_without_new_fields_is_readable(self):
        legacy = {
            "schema_version": 1,
            "kind": "contact_profile",
            "title": "旧档案联系人",
            "scope_person_id": None,
            "style_prompt": "旧风格提示",
            "display_report": "旧报告",
            "persona": "旧画像",
            "affection_score": 0.4,
            "updated_at": "2026-01-01T00:00:00",
            "favorite": True,
        }
        profile = ContactProfile.from_dict(legacy)
        self.assertEqual(profile.title, "旧档案联系人")
        self.assertIsNone(profile.relation)
        self.assertIsNone(profile.topics)
        self.assertIsNone(profile.preferences)
        self.assertIsNone(profile.taboos)
        self.assertIsNone(profile.reply_style)
        self.assertIsNone(profile.important_dates)
        self.assertIs(profile.model_analyzed, False)
        # 旧档案保存后仍可按旧字段读回，新字段保持 None。
        self.store.save(profile)
        reloaded = self.store.get("旧档案联系人")
        self.assertEqual(reloaded.persona, "旧画像")
        self.assertTrue(reloaded.favorite)
        self.assertIs(reloaded.model_analyzed, False)


class ProfileFromStyleTests(unittest.TestCase):
    def test_duck_typed_style_profile(self):
        style = SimpleNamespace(prompt_style="提示词风格", display_report="用户报告")
        profile = profile_from_style("  田子夜 ", style,
                                     scope=ScopeKey("account-1", "person-1"),
                                     persona="画像摘要", affection=0.6)
        self.assertEqual(profile.title, "田子夜")
        self.assertEqual(profile.scope_person_id, "person-1")
        self.assertEqual(profile.style_prompt, "提示词风格")
        self.assertEqual(profile.display_report, "用户报告")
        self.assertEqual(profile.persona, "画像摘要")
        self.assertAlmostEqual(profile.affection_score, 0.6)
        self.assertFalse(profile.favorite)
        self.assertTrue(profile.updated_at)

    def test_dict_style_and_missing_fields(self):
        profile = profile_from_style("联系人", {"prompt_style": "P"},
                                     scope="person-x")
        self.assertEqual(profile.style_prompt, "P")
        self.assertIsNone(profile.display_report)
        self.assertIsNone(profile.persona)
        self.assertIsNone(profile.affection_score)
        self.assertEqual(profile.scope_person_id, "person-x")

        empty = profile_from_style("联系人", SimpleNamespace())
        self.assertIsNone(empty.style_prompt)
        self.assertIsNone(empty.display_report)

    def test_empty_display_name_rejected(self):
        with self.assertRaises(ValueError):
            profile_from_style("   ", SimpleNamespace(prompt_style="x"))


if __name__ == "__main__":
    unittest.main()
