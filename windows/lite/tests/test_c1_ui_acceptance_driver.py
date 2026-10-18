"""Regression checks for version-aware C1 desktop acceptance discovery."""
import os
import runpy
import shutil
import unittest
from unittest.mock import patch

from liteapp import build_info


class C1AcceptanceDriverVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Importing the driver sets an isolated LOCALAPPDATA for its child app.
        # Keep that behavior contained while exercising its pure title helpers.
        with patch.dict(os.environ, {"LOCALAPPDATA": os.environ.get("LOCALAPPDATA", ""),
                                     "QT_QPA_PLATFORM": "offscreen"}):
            cls.driver = runpy.run_path(
                os.path.join(os.path.dirname(__file__), "..", "tools",
                             "c1_ui_acceptance.py"),
                run_name="c1_ui_acceptance_test")
        shutil.rmtree(cls.driver["ISOLATED"], ignore_errors=True)

    def test_source_window_title_uses_current_build_version(self):
        self.assertEqual(self.driver.get("CURRENT_APP_VERSION"), build_info.APP_VERSION)
        self.assertEqual(self.driver.get("SOURCE_WINDOW_TITLE"),
                         f"微信实时助手 · 便捷版 {build_info.APP_VERSION}")

    def test_source_window_filter_rejects_old_candidate_versions(self):
        is_source_title = self.driver.get("is_source_app_title")
        self.assertTrue(callable(is_source_title))
        self.assertTrue(is_source_title(self.driver["SOURCE_WINDOW_TITLE"]))
        self.assertFalse(is_source_title("微信实时助手 · 便捷版 0.5.8"))
        self.assertFalse(is_source_title("微信实时助手 · 便捷版 0.5.7"))


if __name__ == "__main__":
    unittest.main()
