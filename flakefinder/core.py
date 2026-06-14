"""Core flaky-test detection engine.

Input model: a flat list of test-execution records, each carrying at minimum a
test identifier and an outcome. Optional fields (commit, branch, ci_run,
timestamp, duration) sharpen the analysis. The engine groups by test, computes
per-test pass/fail statistics, and detects flakiness via two independent signals:

  1. Mixed outcomes: a test that both passed AND failed in the history window.
  2. Same-commit divergence: a test that produced different outcomes on the
     EXACT same commit -- the strongest possible flake evidence, since the code
     under test did not change between those runs.

A flakiness score in [0, 100] is derived from outcome entropy, flip frequency
(how often consecutive runs change result), and same-commit divergence. Tests
at or above a configurable threshold get a quarantine suggestion.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable, Optional

# Outcomes we treat as a pass vs. a fail. Anything else (skipped, xfail) is
# ignored for flakiness purposes -- a skip carries no pass/fail signal.
_PASS = {"pass", "passed", "ok", "success", "succeeded", "green"}
_FAIL = {"fail", "failed", "failure", "error", "errored", "broken", "red"}
_IGNORE = {"skip", "skipped", "xfail", "xpass", "pending", "disabled"}


class FlakeFinderError(Exception):
    """Raised on unrecoverable input problems."""


@dataclass
class TestRun:
    """A single test execution record."""

    test: str
    outcome: str  # normalized to 'pass' / 'fail' / 'ignore'
    commit: Optional[str] = None
    branch: Optional[str] = None
    ci_run: Optional[str] = None
    timestamp: Optional[str] = None
    duration: Optional[float] = None


@dataclass
class TestStats:
    """Aggregated stats and flakiness verdict for one test."""

    test: str
    total: int
    passes: int
    fails: int
    flips: int
    same_commit_divergence: int
    score: float
    is_flaky: bool
    quarantine: bool
    reason: str
    commits_seen: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["score"] = round(self.score, 2)
        d["fail_rate"] = round(self.fails / self.total, 4) if self.total else 0.0
        return d


@dataclass
class FlakeReport:
    """Full analysis result."""

    total_runs: int
    total_tests: int
    flaky_tests: list[TestStats] = field(default_factory=list)
    quarantine_candidates: list[str] = field(default_factory=list)
    threshold: float = 50.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_runs": self.total_runs,
            "total_tests": self.total_tests,
            "flaky_count": len(self.flaky_tests),
            "threshold": self.threshold,
            "flaky_tests": [t.to_dict() for t in self.flaky_tests],
            "quarantine_candidates": self.quarantine_candidates,
        }


def _normalize_outcome(raw: Any) -> str:
    s = str(raw).strip().lower()
    if s in _PASS:
        return "pass"
    if s in _FAIL:
        return "fail"
    if s in _IGNORE:
        return "ignore"
    # Unknown strings: be conservative. Treat numeric exit-style 0 as pass.
    if s in {"0", "true"}:
        return "pass"
    if s in {"1", "false"}:
        return "fail"
    return "ignore"


def _coerce_run(rec: dict[str, Any]) -> Optional[TestRun]:
    """Map a loose dict into a TestRun, tolerating common field-name variants."""
    def pick(*keys: str) -> Optional[Any]:
        for k in keys:
            if k in rec and rec[k] not in (None, ""):
                return rec[k]
        return None

    name = pick("test", "name", "test_name", "testcase", "classname_name", "id")
    outcome = pick("outcome", "status", "result", "state")
    if name is None or outcome is None:
        return None
    dur = pick("duration", "time", "elapsed")
    try:
        dur = float(dur) if dur is not None else None
    except (TypeError, ValueError):
        dur = None
    return TestRun(
        test=str(name),
        outcome=_normalize_outcome(outcome),
        commit=_str_or_none(pick("commit", "sha", "revision", "commit_sha")),
        branch=_str_or_none(pick("branch", "ref")),
        ci_run=_str_or_none(pick("ci_run", "run_id", "build", "build_id", "job")),
        timestamp=_str_or_none(pick("timestamp", "time_stamp", "date", "started_at")),
        duration=dur,
    )


def _str_or_none(v: Any) -> Optional[str]:
    return None if v is None else str(v)


def load_runs(path: str) -> list[TestRun]:
    """Load test-execution records from a JSON, JSONL, or CSV file.

    JSON  : either a list of objects, or {"runs": [...]} / {"results": [...]}.
    JSONL : one JSON object per line.
    CSV   : header row with recognizable column names.
    """
    if not os.path.exists(path):
        raise FlakeFinderError(f"input file not found: {path}")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except PermissionError:
        raise FlakeFinderError(f"permission denied reading: {path}") from None
    except OSError as exc:
        raise FlakeFinderError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError:
        raise FlakeFinderError(
            f"file is not valid UTF-8 text: {path}"
        ) from None
    ext = os.path.splitext(path)[1].lower()

    records: list[dict[str, Any]]
    if ext == ".csv":
        records = list(csv.DictReader(io.StringIO(text)))
    elif ext == ".jsonl" or (ext not in (".json", ".csv") and _looks_jsonl(text)):
        records = []
        for ln, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as e:
                raise FlakeFinderError(f"bad JSONL on line {ln}: {e}") from e
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise FlakeFinderError(f"bad JSON in {path}: {e}") from e
        if isinstance(data, dict):
            for key in ("runs", "results", "tests", "records"):
                if isinstance(data.get(key), list):
                    data = data[key]
                    break
            else:
                raise FlakeFinderError(
                    "JSON object must contain a 'runs'/'results'/'tests' list"
                )
        if not isinstance(data, list):
            raise FlakeFinderError("JSON input must be a list of records")
        records = data

    runs: list[TestRun] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        run = _coerce_run(rec)
        if run is not None:
            runs.append(run)
    if not runs:
        raise FlakeFinderError("no usable test records found in input")
    return runs


def _looks_jsonl(text: str) -> bool:
    stripped = text.lstrip()
    if not stripped.startswith("{"):
        return False
    # Multiple top-level objects on separate lines => JSONL.
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return len(lines) > 1 and all(ln.strip().startswith("{") for ln in lines[:3])


def flakiness_score(passes: int, fails: int, flips: int, divergence: int, total: int) -> float:
    """Compute a flakiness score in [0, 100].

    Components:
      * entropy   - how balanced pass/fail is (max at 50/50). A test that always
                    fails is NOT flaky; it is simply broken (entropy ~ 0).
      * flip_rate - fraction of consecutive-run transitions that change outcome;
                    captures alternating green/red noise.
      * diverge   - same-commit divergence is the strongest signal and is given
                    a large additive bonus.
    """
    if total <= 0 or (passes == 0 or fails == 0) and divergence == 0:
        # No mixed outcomes and no same-commit divergence -> not flaky.
        return 0.0

    p = passes / total
    q = fails / total
    entropy = 0.0
    for x in (p, q):
        if x > 0:
            entropy -= x * math.log2(x)  # in [0, 1] for two classes

    max_flips = max(total - 1, 1)
    flip_rate = flips / max_flips

    base = (0.55 * entropy + 0.45 * flip_rate) * 100.0
    diverge_bonus = min(divergence, 5) * 8.0  # up to +40
    return min(base + diverge_bonus, 100.0)


def analyze(
    runs: Iterable[TestRun],
    threshold: float = 50.0,
    min_runs: int = 3,
) -> FlakeReport:
    """Group runs by test and produce a FlakeReport.

    Runs are assumed to be in chronological order as provided; flips are counted
    over that order. Tests with fewer than ``min_runs`` executions are reported
    only if they show same-commit divergence (which needs no history depth).
    """
    if not (0.0 <= threshold <= 100.0):
        raise FlakeFinderError(
            f"threshold must be between 0 and 100 (got {threshold})"
        )
    if min_runs < 1:
        raise FlakeFinderError(
            f"min-runs must be at least 1 (got {min_runs})"
        )
    runs = list(runs)
    if not runs:
        return FlakeReport(total_runs=0, total_tests=0, threshold=threshold)
    by_test: dict[str, list[TestRun]] = {}
    for r in runs:
        if r.outcome == "ignore":
            continue
        by_test.setdefault(r.test, []).append(r)

    flaky: list[TestStats] = []
    for test, trs in by_test.items():
        outcomes = [t.outcome for t in trs]
        total = len(outcomes)
        passes = outcomes.count("pass")
        fails = outcomes.count("fail")

        flips = sum(1 for a, b in zip(outcomes, outcomes[1:]) if a != b)

        # Same-commit divergence: any commit that saw both pass and fail.
        commit_outcomes: dict[str, set[str]] = {}
        for t in trs:
            if t.commit:
                commit_outcomes.setdefault(t.commit, set()).add(t.outcome)
        divergence = sum(1 for o in commit_outcomes.values() if {"pass", "fail"} <= o)
        commits_seen = len(commit_outcomes)

        mixed = passes > 0 and fails > 0
        if not mixed and divergence == 0:
            continue  # deterministic (always-pass or always-fail) => skip
        if total < min_runs and divergence == 0:
            continue  # not enough evidence yet

        score = flakiness_score(passes, fails, flips, divergence, total)
        is_flaky = score > 0.0
        if not is_flaky:
            continue

        quarantine = score >= threshold
        if divergence > 0:
            reason = (
                f"same-commit divergence on {divergence} commit(s): identical code "
                f"produced both pass and fail"
            )
        else:
            reason = (
                f"mixed outcomes ({passes} pass / {fails} fail) with {flips} "
                f"green<->red flip(s) across {total} runs"
            )

        flaky.append(
            TestStats(
                test=test,
                total=total,
                passes=passes,
                fails=fails,
                flips=flips,
                same_commit_divergence=divergence,
                score=score,
                is_flaky=True,
                quarantine=quarantine,
                reason=reason,
                commits_seen=commits_seen,
            )
        )

    # Highest flakiness first; ties broken by more runs (more evidence).
    flaky.sort(key=lambda s: (s.score, s.total), reverse=True)
    candidates = [s.test for s in flaky if s.quarantine]

    return FlakeReport(
        total_runs=len(runs),
        total_tests=len(by_test),
        flaky_tests=flaky,
        quarantine_candidates=candidates,
        threshold=threshold,
    )
