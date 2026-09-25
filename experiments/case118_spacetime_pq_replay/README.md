# Combined time and spatial P/Q vectorization replay

## Current study: four fresh conditions

The owner selected four fresh replays on 2026-09-23. See
[FOUR_WAY_STUDY.md](FOUR_WAY_STUDY.md) for the current protocol and
[STUDY_READINESS.md](STUDY_READINESS.md) for checks and launch gates.
Each condition reuses the frozen 120+6 sample with the new dependency pair and
disabled automatic sparse dispatch. The original protocol below remains as
historical context for the interrupted combined-only run.

Read-only preflight (does not create the study output or launch workers):

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.study --preflight
```

After independent review, owner commit, and launch authorization with current
fan confirmation:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.study \
  --commit "$(git rev-parse HEAD)" --fan-on
```

Output: `results/case118_four_way_120plus6_dense_control/`, with `none`,
`time_only`, `spatial_only`, and `both` subdirectories. The root must not exist.
No automatic resume or retry. Analyze partial or completed results without solves:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.analyze_study
```

## Historical combined-only protocol

Run the exact same 120 primary-winner and six helper-winner Case118 windows once,
using the committed combined-vectorization implementation. Compare against the
recorded original stepwise and time-only replay timings. See [PLAN.md](PLAN.md)
for the frozen protocol. There is no spatial-only timing condition.

The entry point reuses the existing replay worker and unchanged supervisor:
two primary windows on distinct original shards, with one shared helper. The
historical sample, starts, physical requests, race policy, and acceptance audit
are retained. Helper attempts can make the solver-attempt count exceed 126.

Work from the normal repository checkout. First inspect the read-only preflight:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.run --preflight
```

This validates all retained historical references, the frozen sample, previous
replay completion, dependency transition, and unused output destination. It
reports any uncommitted changes but does not create output or launch workers.
CVXPY must be 1.9.3 and sparsediffpy 0.6.1; other recorded software versions
must match both retained studies.

After a clean independent review and the owner's review, commit, and launch
authorization, bind the exact committed HEAD and confirm that the fan is on:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.run \
  --commit "$(git rev-parse HEAD)" --fan-on
```

Launch rejects a dirty checkout, incorrect commit, changed frozen sample, or
existing destination. It records commit/source hashes, software versions,
retained comparison hashes, fan status, and worker configuration. Machine-wide
temperature/frequency telemetry runs during the replay; missing telemetry is
explicitly recorded. Primary solves receive no new timeout. Interruptions
preserve partial evidence and stop/reap the supervisor's workers.

All generated data stays under the ignored
`experiments/case118_spacetime_pq_replay/results/case118_spacetime_pq_replay/`. The previous replay and its report remain
unchanged. Analyze complete or partial results without launching more solves:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.analyze
```

The output includes `three_way_comparison.csv`, `three_way_summary.json`, and
`REPORT.md`, plus the detailed stepwise comparison and diagnostics in
`comparison.csv` and `summary.json`. Partial comparisons are labeled and only
describe completed windows. Copy a reviewed final report and selected compact
evidence into this experiment directory for owner review; raw runs stay ignored.

The paired historical comparison also includes a dependency upgrade and changing
machine conditions. It does not isolate a causal vectorization speedup. Inspect
objective differences above 0.1%, numerical audits, and changed winners before
interpreting timing differences.

For the separately authorized two-solve investigation of the failed primary at
hour 6047, see [PRIMARY_DIAGNOSTIC.md](PRIMARY_DIAGNOSTIC.md). That diagnostic
compares stepwise with time-vectorized assembly, with spatial P/Q vectorization
enabled in both, and does not resume or modify the 120+6 replay.

The subsequent [four-way diagnostic](FOUR_WAY_DIAGNOSTIC.md) repeats hour 6047's
isolated primary under all four time/spatial combinations in the current
environment. Select it explicitly with `diagnose_primary --four-way`; it has
its own reviewed source transition and fresh output directory.


The completed four-way results are in [FOUR_WAY_REPORT.md](FOUR_WAY_REPORT.md)
and `artifacts/four_way/summary.json`. Reconstruct and verify them without a solve:

```sh
uv run --locked --extra dev python -m experiments.case118_spacetime_pq_replay.analyze_primary
```

Add `--write` to regenerate those two reviewable files. Original run records in
`results/` are never rewritten. See [RESULT_LOCATIONS.md](RESULT_LOCATIONS.md)
for the checked relocation from the retired root scratch directory.

The maintained analyzers use exact historical-file mappings from this study's
relocation manifest and the earlier time-only replay's manifest, loaded once per
analysis operation. Result-location/hash verification checks preservation; it
does not replace scientific review of the four-way report or its conclusions.
