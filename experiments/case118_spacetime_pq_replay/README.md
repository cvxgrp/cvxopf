# Combined time and spatial P/Q vectorization replay

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
`outputs/case118_spacetime_pq_replay/`. The previous replay and its report remain
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
