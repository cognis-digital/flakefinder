"""FLAKEFINDER - flaky-test detector from CI history with quarantine suggestions.

Feeds on CI test-result history (JUnit-style records flattened to JSON/JSONL/CSV)
and surfaces tests that pass and fail non-deterministically, ranks them by a
flakiness score, and emits actionable quarantine suggestions.

Standard library only. Zero install.
"""

from .core import (
    TestRun,
    TestStats,
    FlakeReport,
    load_runs,
    analyze,
    flakiness_score,
)

TOOL_NAME = "flakefinder"
TOOL_VERSION = "1.0.0"

__all__ = [
    "TOOL_NAME",
    "TOOL_VERSION",
    "TestRun",
    "TestStats",
    "FlakeReport",
    "load_runs",
    "analyze",
    "flakiness_score",
]
