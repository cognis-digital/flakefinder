"""Command-line interface for FLAKEFINDER.

Subcommands:
  analyze <input>   Detect flaky tests in a CI-history file.
  quarantine <input>  Print only the quarantine list (one test id per line,
                      or JSON with --format json).

Exit codes:
  0  no flaky tests found
  1  bad usage / input error
  2  flaky tests detected (so it can gate CI)
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional

from . import TOOL_NAME, TOOL_VERSION
from .core import FlakeFinderError, FlakeReport, analyze, load_runs

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_FLAKY = 2


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description="Detect flaky tests from CI history and suggest quarantines.",
    )
    p.add_argument("--version", action="version", version=f"{TOOL_NAME} {TOOL_VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("input", help="path to CI-history file (.json, .jsonl, .csv)")
    common.add_argument(
        "--format", choices=("table", "json"), default="table", help="output format"
    )
    common.add_argument(
        "--threshold",
        type=float,
        default=50.0,
        help="flakiness score (0-100) at/above which a test is quarantined",
    )
    common.add_argument(
        "--min-runs",
        type=int,
        default=3,
        help="minimum runs before a mixed-outcome test counts (default 3)",
    )

    a = sub.add_parser("analyze", parents=[common], help="full flakiness report")
    a.set_defaults(func=_cmd_analyze)

    q = sub.add_parser(
        "quarantine", parents=[common], help="emit only quarantine candidates"
    )
    q.set_defaults(func=_cmd_quarantine)
    return p


def _run_report(args: argparse.Namespace) -> FlakeReport:
    runs = load_runs(args.input)
    return analyze(runs, threshold=args.threshold, min_runs=args.min_runs)


def _render_table(report: FlakeReport) -> str:
    lines = []
    lines.append(
        f"Scanned {report.total_runs} runs across {report.total_tests} tests."
    )
    if not report.flaky_tests:
        lines.append("No flaky tests detected. ✓")
        return "\n".join(lines)

    lines.append("")
    lines.append(f"{'SCORE':>6}  {'P/F':>9}  {'FLIPS':>5}  {'DIV':>3}  TEST")
    lines.append("-" * 64)
    for s in report.flaky_tests:
        mark = "!" if s.quarantine else " "
        pf = f"{s.passes}/{s.fails}"
        lines.append(
            f"{s.score:6.1f}{mark} {pf:>9}  {s.flips:>5}  {s.same_commit_divergence:>3}  {s.test}"
        )
    lines.append("")
    if report.quarantine_candidates:
        lines.append(
            f"Quarantine suggestions ({len(report.quarantine_candidates)}) "
            f"[score >= {report.threshold:g}]:"
        )
        for t in report.quarantine_candidates:
            lines.append(f"  - {t}")
    else:
        lines.append("No tests above quarantine threshold.")
    return "\n".join(lines)


def _cmd_analyze(args: argparse.Namespace) -> int:
    report = _run_report(args)
    if args.format == "json":
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(_render_table(report))
    return EXIT_FLAKY if report.flaky_tests else EXIT_OK


def _cmd_quarantine(args: argparse.Namespace) -> int:
    report = _run_report(args)
    if args.format == "json":
        print(json.dumps({"quarantine_candidates": report.quarantine_candidates}, indent=2))
    else:
        if report.quarantine_candidates:
            for t in report.quarantine_candidates:
                print(t)
        else:
            print("# no quarantine candidates")
    return EXIT_FLAKY if report.quarantine_candidates else EXIT_OK


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except FlakeFinderError as e:
        print(f"{TOOL_NAME}: error: {e}", file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    raise SystemExit(main())
