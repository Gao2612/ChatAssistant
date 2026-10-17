"""R-09 验收用例：`--self-test --data-dir` 不得写入疑似正式用户目录。

自检必须只在空白隔离目录（或临时目录）里执行；指向真实 profile 时应拒绝并
返回失败报告，而不是在用户目录下创建 data/ 子目录。全部使用合成标记文件与临时目录。
"""

import json
import tempfile
import unittest
from pathlib import Path

from liteapp import self_test


class SelfTestIsolationTests(unittest.TestCase):
    def test_profile_like_directory_is_rejected_without_touching_it(self):
        for marker in ("settings.json", "memory.sqlite3", "instance.lock"):
            with self.subTest(marker=marker):
                with tempfile.TemporaryDirectory() as folder:
                    base = Path(folder)
                    sentinel = base / marker
                    sentinel.write_text("synthetic-sentinel", encoding="utf-8")
                    report = self_test.run(str(base))
                    self.assertFalse(report["ok"])
                    failed = {item["name"]
                              for item in report["checks"] if not item["ok"]}
                    self.assertIn("isolated_data_dir", failed)
                    self.assertEqual(sentinel.read_text(encoding="utf-8"),
                                     "synthetic-sentinel")
                    self.assertFalse((base / "data").exists())
                    self.assertFalse(any(path.name.startswith("selftest-")
                                         for path in base.iterdir()))

    def test_empty_explicit_directory_is_marked_not_isolated_and_cleaned(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            report = self_test.run(str(base))
            self.assertTrue(report["ok"],
                            [item for item in report["checks"] if not item["ok"]])
            self.assertIs(report["isolated"], False)
            self.assertFalse((base / "settings.json").exists())
            self.assertFalse((base / "data").exists())
            self.assertEqual([path.name for path in base.iterdir()], [])

    def test_default_mode_still_uses_temporary_isolated_dir(self):
        report = self_test.run()
        self.assertTrue(report["ok"],
                        [item for item in report["checks"] if not item["ok"]])
        self.assertIs(report["isolated"], True)
        raw = json.dumps(report, ensure_ascii=False)
        self.assertNotIn(str(Path.home()), raw)


if __name__ == "__main__":
    unittest.main()
