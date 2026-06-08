# Demo 01 - Basic flaky-test detection

A small team exports the last week of CI test results into a single JSONL file,
one record per test execution. Some tests are rock-solid, one is genuinely
broken (always fails), and two are flaky in different ways:

- `test_payment_retry` alternates green/red across runs (timing/race noise).
- `test_async_upload` shows **same-commit divergence**: on commit `c3` it both
  passed and failed without any code change -- the strongest flake signal.
- `test_legacy_import` always fails (broken, NOT flaky) -- FLAKEFINDER must not
  flag it as flaky.
- `test_login` and `test_render` always pass (clean).

## Run it

```bash
python -m flakefinder analyze demos/01-basic/ci_history.jsonl
```

Expected: `test_async_upload` ranks highest (same-commit divergence) and is a
quarantine candidate, `test_payment_retry` is flagged flaky, and the always-fail
/ always-pass tests are NOT in the flaky list. Exit code is 2 (flaky found).

JSON output for tooling:

```bash
python -m flakefinder analyze demos/01-basic/ci_history.jsonl --format json
```

Quarantine list only:

```bash
python -m flakefinder quarantine demos/01-basic/ci_history.jsonl
```
