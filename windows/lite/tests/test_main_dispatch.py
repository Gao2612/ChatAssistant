import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main as app_main


class VersionJsonTests(unittest.TestCase):
    def test_version_json_contains_version_and_digest(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = app_main.print_version_json()
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(report.get("app_version"))
        self.assertIn("source_digest", report)
        self.assertNotIn(str(Path.home()), output.getvalue())


class SelfTestCliTests(unittest.TestCase):
    def test_isolated_self_test_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = app_main.run_self_test(["--self-test", "--data-dir", folder])
        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertTrue(report["ok"])

    def test_self_test_failure_returns_nonzero(self):
        with patch("liteapp.self_test.run", return_value={"ok": False, "checks": []}):
            with contextlib.redirect_stdout(io.StringIO()):
                code = app_main.run_self_test(["--self-test"])
        self.assertEqual(code, 1)


class DispatchTests(unittest.TestCase):
    def test_pdf_worker_flag_is_stripped_before_forwarding(self):
        with patch("liteapp.importers.pdf.worker_main", return_value=0) as worker:
            code = app_main.run_pdf_worker(
                ["--lite-pdf-worker", "--path", "synthetic.pdf", "--worker"])
        self.assertEqual(code, 0)
        worker.assert_called_once()
        forwarded = worker.call_args[0][0]
        self.assertNotIn("--lite-pdf-worker", forwarded)
        self.assertIn("--path", forwarded)

    def test_main_dispatches_version_json_without_instance_or_qt(self):
        with patch.object(app_main, "print_version_json", return_value=0) as version, \
                patch.object(app_main, "claim_instance") as claim:
            with patch.object(sys, "argv", ["ChatAssistantLite.exe", "--version-json"]):
                code = app_main.main()
        self.assertEqual(code, 0)
        version.assert_called_once()
        claim.assert_not_called()

    def test_main_dispatches_self_test_before_instance(self):
        with patch.object(app_main, "run_self_test", return_value=0) as self_test, \
                patch.object(app_main, "claim_instance") as claim:
            with patch.object(sys, "argv", ["ChatAssistantLite.exe", "--self-test"]):
                code = app_main.main()
        self.assertEqual(code, 0)
        self_test.assert_called_once()
        claim.assert_not_called()

    def test_main_dispatches_pdf_worker_before_instance(self):
        with patch.object(app_main, "run_pdf_worker", return_value=0) as worker, \
                patch.object(app_main, "claim_instance") as claim:
            with patch.object(sys, "argv",
                              ["ChatAssistantLite.exe", "--lite-pdf-worker", "--path", "x"]):
                code = app_main.main()
        self.assertEqual(code, 0)
        worker.assert_called_once()
        claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
