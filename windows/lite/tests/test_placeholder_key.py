import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from liteapp import settings


class PlaceholderKeyWarningTests(unittest.TestCase):
    def test_placeholder_api_key_is_surfaced_as_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
            cfg["api_key"] = "test-key"
            settings.save(cfg, folder)
            loaded = settings.load(folder)
        self.assertIn("测试占位值", loaded.get("_warning", ""))

    def test_real_looking_key_has_no_placeholder_warning(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = settings.load(folder)
            cfg["api_key"] = "sk-realistic-placeholder-for-test"
            settings.save(cfg, folder)
            loaded = settings.load(folder)
        self.assertNotIn("测试占位值", loaded.get("_warning", ""))


if __name__ == "__main__":
    unittest.main()
