"""FLAKEFINDER — Flaky-test detector from CI history with quarantine suggestions."""
from flakefinder.core import scan, TOOL_NAME, TOOL_VERSION
__all__ = ["scan", "TOOL_NAME", "TOOL_VERSION"]
