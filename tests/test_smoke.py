"""Smoke + behavioral tests for FLAKEFINDER. Standard library only, no network."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flakefinder import TOOL_NAME, TOOL_VERSION, analyze, load_runs  # noqa: E402
from flakefinder.core import TestRun, flakiness_score  # noqa: E402
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
        rc = cli.main(["quarantine", DEMO, "--threshold", "101"])
        self.assertEqual(rc, cli.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
