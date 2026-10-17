import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

from liteapp import build_info, self_test

ROOT = Path(__file__).resolve().parents[1]


class FakeRunner:
    def __init__(self, outputs):
        self.outputs = outputs
        self.calls = []

    def __call__(self, args, repo):
        self.calls.append(tuple(args))

        class Result:
            returncode = 0
            stdout = self.outputs(args)

        return Result()


class SourceDigestTests(unittest.TestCase):
    def test_digest_changes_when_source_or_file_list_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "app").mkdir()
            (root / "build").mkdir()
            (root / "app" / "main.py").write_text("print('a')\n", encoding="utf-8")
            (root / "app" / "notes.txt").write_text("hello\n", encoding="utf-8")
            (root / "build" / "ignored.py").write_text("print('ignored')\n", encoding="utf-8")
            digest, count = build_info.source_digest(root)
            self.assertEqual(count, 2)
            (root / "app" / "main.py").write_text("print('b')\n", encoding="utf-8")
            changed, count_changed = build_info.source_digest(root)
            self.assertNotEqual(digest, changed)
            self.assertEqual(count_changed, 2)
            (root / "app" / "extra.py").write_text("print('c')\n", encoding="utf-8")
            _, count_extra = build_info.source_digest(root)
            self.assertEqual(count_extra, 3)


class ManifestTests(unittest.TestCase):
    def _sample_root(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        (root / "app").mkdir()
        (root / "app" / "main.py").write_text("print('hello')\n", encoding="utf-8")
        return root

    def test_manifest_roundtrip_and_verify(self):
        root = self._sample_root()
        output = root / "build_info.json"
        info = build_info.write_build_info(output, version="9.9.9", root=root,
                                           repo=Path("does-not-exist"))
        self.assertEqual(info["app_version"], "9.9.9")
        self.assertGreater(info["source_file_count"], 0)
        result = build_info.verify(output, root=root, expect_version="9.9.9")
        self.assertTrue(result["ok"], result["errors"])
        mismatch = build_info.verify(output, root=root, expect_version="1.0.0")
        self.assertFalse(mismatch["ok"])
        self.assertTrue(any("版本" in error for error in mismatch["errors"]))

    def test_verify_fails_after_source_changes(self):
        root = self._sample_root()
        output = root / "build_info.json"
        build_info.write_build_info(output, root=root, repo=Path("does-not-exist"))
        self.assertTrue(build_info.verify(output, root=root)["ok"])
        (root / "app" / "main.py").write_text("print('changed')\n", encoding="utf-8")
        result = build_info.verify(output, root=root)
        self.assertFalse(result["ok"])
        self.assertTrue(any("摘要" in error for error in result["errors"]))

    def test_manifest_contains_no_private_paths_or_secrets(self):
        root = self._sample_root()
        output = root / "build_info.json"
        build_info.write_build_info(output, root=root, repo=Path("does-not-exist"))
        raw = output.read_text(encoding="utf-8")
        self.assertNotIn(str(Path.home()), raw)
        self.assertNotIn("C:\\Users", raw)
        self.assertNotIn("protected_key", raw)
        self.assertNotIn("sk-", raw)
        data = json.loads(raw)
        self.assertEqual(set(data) >= {"schema_version", "app_version", "built_at",
                                       "source_digest", "source_file_count", "git"}, True)
        self.assertNotIn("api_key", json.dumps(data))

    def test_git_state_flags_dirty_and_handles_missing_git(self):
        def outputs(args):
            if args[0] == "rev-parse" and args[1] == "--abbrev-ref":
                return "feat/windows-lite\n"
            if args[0] == "rev-parse":
                return "abc123\n"
            return " M windows/lite/liteapp/ui.py\n"

        runner = FakeRunner(outputs)
        state = build_info.git_state(repo=ROOT, runner=runner)
        self.assertEqual(state["branch"], "feat/windows-lite")
        self.assertEqual(state["commit"], "abc123")
        self.assertIs(state["dirty"], True)
        clean = build_info.git_state(repo=ROOT, runner=FakeRunner(lambda args: ""))
        self.assertIs(clean["dirty"], False)

        def broken(_args, _repo):
            raise OSError("git unavailable")

        self.assertEqual(build_info.git_state(repo=ROOT, runner=broken)["dirty"], None)

    def test_version_consistent_between_code_installer_and_window_title(self):
        iss = (ROOT / "installer" / "ChatAssistantLite.iss").read_text(encoding="utf-8")
        iss_version = re.search(r'#define\s+AppVersion\s+"([^"]+)"', iss)
        self.assertIsNotNone(iss_version)
        ui = (ROOT / "liteapp" / "ui.py").read_text(encoding="utf-8")
        title_version = re.search(r"便捷版\s+([0-9]+\.[0-9]+\.[0-9]+)", ui)
        self.assertIsNotNone(title_version)
        self.assertEqual(build_info.APP_VERSION, iss_version.group(1))
        self.assertEqual(build_info.APP_VERSION, title_version.group(1))


class SelfTestTests(unittest.TestCase):
    def test_isolated_self_test_passes_without_real_profile_or_network(self):
        with tempfile.TemporaryDirectory() as folder:
            report = self_test.run(folder)
        self.assertTrue(report["ok"], [item for item in report["checks"] if not item["ok"]])
        names = {item["name"] for item in report["checks"]}
        self.assertIn("settings_dpapi", names)
        self.assertIn("sqlite", names)
        self.assertIn("import:PySide6", names)
        raw = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(str(Path.home()), raw)
        self.assertNotIn("synthetic-self-test-key", raw)


if __name__ == "__main__":
    unittest.main()
