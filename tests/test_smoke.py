"""Smoke + behavioral tests for FLAKEFINDER. Standard library only, no network."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flakefinder import TOOL_NAME, TOOL_VERSION, analyze, load_runs  # noqa: E402
from flakefinder.core import flakiness_score  # noqa: E402
from flakefinder import cli  # noqa: E402

DEMO = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "demos",
    "01-basic",
    "ci_history.jsonl",
)


class TestMeta(unittest.TestCase):
    def test_tool_identity(self):
        self.assertEqual(TOOL_NAME, "flakefinder")
        self.assertTrue(TOOL_VERSION)


class TestScoring(unittest.TestCase):
    def test_always_pass_not_flaky(self):
        self.assertEqual(flakiness_score(10, 0, 0, 0, 10), 0.0)

    def test_always_fail_not_flaky(self):
        # Broken, not flaky.
        self.assertEqual(flakiness_score(0, 10, 0, 0, 10), 0.0)

    def test_mixed_is_flaky(self):
        self.assertGreater(flakiness_score(5, 5, 5, 0, 10), 0.0)

    def test_divergence_boosts_score(self):
        no_div = flakiness_score(3, 3, 2, 0, 6)
        with_div = flakiness_score(3, 3, 2, 2, 6)
        self.assertGreater(with_div, no_div)

    def test_score_bounded(self):
        self.assertLessEqual(flakiness_score(50, 50, 99, 5, 100), 100.0)


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.report = analyze(load_runs(DEMO))

    def test_demo_loads(self):
        self.assertGreater(self.report.total_runs, 0)
        self.assertGreater(self.report.total_tests, 0)

    def test_clean_tests_not_flaky(self):
        flaky_names = {t.test for t in self.report.flaky_tests}
        self.assertNotIn("test_login", flaky_names)
        self.assertNotIn("test_render", flaky_names)

    def test_broken_test_not_flagged(self):
        flaky_names = {t.test for t in self.report.flaky_tests}
        # Always-fails => broken, not flaky.
        self.assertNotIn("test_legacy_import", flaky_names)

    def test_flaky_tests_detected(self):
        flaky_names = {t.test for t in self.report.flaky_tests}
        self.assertIn("test_payment_retry", flaky_names)
        self.assertIn("test_async_upload", flaky_names)

    def test_same_commit_divergence_detected(self):
        upload = next(t for t in self.report.flaky_tests if t.test == "test_async_upload")
        self.assertGreaterEqual(upload.same_commit_divergence, 1)

    def test_divergent_test_quarantined(self):
        self.assertIn("test_async_upload", self.report.quarantine_candidates)

    def test_report_serializable(self):
        s = json.dumps(self.report.to_dict())
        self.assertIn("flaky_tests", s)


class TestCli(unittest.TestCase):
    def test_analyze_json_exit_code(self):
        rc = cli.main(["analyze", DEMO, "--format", "json"])
        self.assertEqual(rc, cli.EXIT_FLAKY)

    def test_analyze_table(self):
        rc = cli.main(["analyze", DEMO, "--format", "table"])
        self.assertEqual(rc, cli.EXIT_FLAKY)

    def test_quarantine_subcommand(self):
        rc = cli.main(["quarantine", DEMO])
        self.assertEqual(rc, cli.EXIT_FLAKY)

    def test_bad_input_usage_error(self):
        rc = cli.main(["analyze", os.path.join(os.path.dirname(DEMO), "nope.json")])
        self.assertEqual(rc, cli.EXIT_USAGE)

    def test_high_threshold_no_quarantine_but_still_flaky(self):
        # threshold=100 is the maximum valid value; nothing scores above 100 so
        # quarantine list is empty => EXIT_OK, but flaky tests are still found.
        rc = cli.main(["quarantine", DEMO, "--threshold", "100"])
        self.assertEqual(rc, cli.EXIT_OK)

    def test_threshold_out_of_range_is_usage_error(self):
        # threshold > 100 is rejected; argparse.error() raises SystemExit(2).
        with self.assertRaises(SystemExit) as cm:
            cli.main(["analyze", DEMO, "--threshold", "101"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_threshold_negative_is_usage_error(self):
        with self.assertRaises(SystemExit) as cm:
            cli.main(["analyze", DEMO, "--threshold", "-1"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_min_runs_zero_is_usage_error(self):
        with self.assertRaises(SystemExit) as cm:
            cli.main(["analyze", DEMO, "--min-runs", "0"])
        self.assertNotEqual(cm.exception.code, 0)


class TestCoreEdgeCases(unittest.TestCase):
    def test_analyze_empty_runs_returns_empty_report(self):
        from flakefinder.core import TestRun
        report = analyze([], threshold=50.0)
        self.assertEqual(report.total_runs, 0)
        self.assertEqual(report.total_tests, 0)
        self.assertEqual(report.flaky_tests, [])

    def test_analyze_all_ignored_outcomes(self):
        from flakefinder.core import TestRun
        runs = [
            TestRun(test="t1", outcome="ignore"),
            TestRun(test="t1", outcome="ignore"),
        ]
        report = analyze(runs, threshold=50.0)
        self.assertEqual(report.flaky_tests, [])

    def test_load_runs_missing_file_raises(self):
        from flakefinder.core import FlakeFinderError
        with self.assertRaises(FlakeFinderError) as cm:
            load_runs("/nonexistent/path/data.jsonl")
        self.assertIn("not found", str(cm.exception))

    def test_load_runs_malformed_jsonl_raises(self):
        import tempfile, os
        from flakefinder.core import FlakeFinderError
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            f.write('{"test": "t1", "outcome": "pass"}\n')
            f.write("NOT JSON\n")
            fname = f.name
        try:
            with self.assertRaises(FlakeFinderError) as cm:
                load_runs(fname)
            self.assertIn("bad JSONL", str(cm.exception))
        finally:
            os.unlink(fname)

    def test_load_runs_empty_file_raises(self):
        import tempfile, os
        from flakefinder.core import FlakeFinderError
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
        ) as f:
            fname = f.name
        try:
            with self.assertRaises(FlakeFinderError) as cm:
                load_runs(fname)
            self.assertIn("no usable", str(cm.exception))
        finally:
            os.unlink(fname)

    def test_analyze_threshold_out_of_range_raises(self):
        from flakefinder.core import FlakeFinderError, TestRun
        runs = [TestRun(test="t", outcome="pass")]
        with self.assertRaises(FlakeFinderError):
            analyze(runs, threshold=150.0)

    def test_analyze_min_runs_zero_raises(self):
        from flakefinder.core import FlakeFinderError, TestRun
        runs = [TestRun(test="t", outcome="pass")]
        with self.assertRaises(FlakeFinderError):
            analyze(runs, min_runs=0)


if __name__ == "__main__":
    unittest.main()
