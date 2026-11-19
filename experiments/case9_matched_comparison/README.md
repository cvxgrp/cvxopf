# Matched Case9 AC preflight

Use this harness to check that CVXOPF and PYPOWER represent the same four
Case9-derived AC problems before comparing optimization results. See the
[protocol](PROTOCOL.md) for the model choices and limitations.

From the repository root, supply a Python interpreter with `PYPOWER==5.1.19`,
`numpy==2.2.6` and `scipy==1.18.0`. PYPOWER runs in that separate environment;
the parent uses the project's development environment.

```sh
uv run --offline --no-sync --extra dev python \
  -m experiments.case9_matched_comparison.preflight \
  --run-id preflight_001 --pypower-python /path/to/pinned/python
```

Use the same command with `--resume` after an interruption. It checks the
bound inputs, reuses verified receipts and gives unfinished cells a new
attempt directory. A gate failure stops the preflight; inspect the retained
worker stderr and failure record. If the source or protocol changes, choose
a fresh run ID so earlier attempts remain intact.

```sh
uv run --offline --no-sync --extra dev python \
  -m experiments.case9_matched_comparison.preflight \
  --run-id preflight_001 --status
```

Requests, callback vectors, receipts, failure details and worker logs are
written only to the experiment's locally ignored `outputs/` directory.
Status checks their publication hashes without importing PYPOWER or
redoing the model checks. Attempts are serial, with a 30-second deadline
for each isolated callback probe. The harness has no solve or benchmark
launch command. See the separate [benchmark runner guide](BENCHMARK.md)
for binding and launching the eight solves after approval.

To run the interruption and publication tests, point
`CASE9_PINNED_PYPOWER_PYTHON` at the same interpreter and run:

```sh
uv run --offline --no-sync --extra dev python -m pytest -q experiments/case9_matched_comparison
```

Without the variable, the lifecycle and binding tests still run; the checks
that exercise the two assembled models are skipped.
