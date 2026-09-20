# Independent Case118 vectorization replay

Replay 126 three-hour AC windows from the completed **toy** Case118 annual
hierarchical study using time-vectorized construction. Compare with recorded
stepwise timings; do not rerun stepwise or feed replay solutions forward.

The frozen sample (`outputs/case118_vectorization_replay/sample.json`, seed
20260920) contains 120 accepted primary winners and six accepted helper winners
from the post-intervention `causal_first_speculative_v1` population. Operator
insertions and shortened end-of-shard windows are excluded. Select eleven from
each lower decile, then seven each from p90–95, p95–99, and p99–100. Select one
helper winner from each winner-latency sextile. Sample without replacement within
strata, retaining inclusion probabilities for population-weighted summaries.

Each window retains its original loads, renewable availability, storage initial
state, terminal target, cost, solver configuration, and historical preceding
controller initialization. Original named initial values are regenerated and
checked exactly against the retained start, then mapped to time-last variables.
Vectorized SoC adds its fixed initial boundary. Helper perturbations use the
original stepwise initializer and original seed before mapping, preserving its
random draw order. New target-free helper solutions may seed later helpers in
that same window. Uncapped retries replay the complete new vectorized start.

The original `SpeculativeSupervisor`, `SubprocessBackend`, and `WindowRace`
implement the unchanged two-primary/one-helper ladder, helper eligibility,
budgets, physical acceptance audit, memory policy, and cancellation. Primary
solves have no added timeout. Frozen random window order is admitted subject to
two distinct original shard IDs; this preserves the supervisor contract without
introducing state dependence. Winner identities are allowed to change.

Run once, preserving all attempts and source hashes:

```sh
uv run --extra dev python -m experiments.case118_vectorization_replay.sample
uv run --extra dev python -m experiments.case118_vectorization_replay.run
```

The first command has already frozen the sample; existing output directories
cause an error rather than overwrite evidence. The second creates `run/`.
Historical artifacts remain read-only.

After completion, recompute the comparison table, weighted summaries, and figure:

```sh
uv run --extra dev python -m experiments.case118_vectorization_replay.analyze
```

Partial analysis is explicitly marked incomplete and is kept in the output
directory. A completed analysis also writes reviewable `artifacts/` files next
to these scripts, including the frozen sample. Figures require Matplotlib
(available through the project's `notebook` extra). Raw start/result/phase and
lifecycle records remain in the separate output directory.

Compare the same `before_ac_solve` → `after_ac_solve` phase on both sides
(canonicalization plus solver, including start persistence). Also report window
latency, construction, initialization reconstruction overhead, memory, numerical
acceptance, objective differences, and changed winners. Historical timings are
paired observations, not a simultaneous controlled machine-load comparison.
Helper-winner cohorts include historical race waiting; distinguish winner solve
time from total window latency. Treat failed/canceled attempts explicitly and
do not silently resample. An objective difference exceeding 0.1% merits inspection;
AC is nonconvex and its reactive dispatch remains unregularized.


Completed results are in [REPORT.md](REPORT.md). The final scientific review by
`cvxopf-review` returned clean on 2026-09-20 after independently checking sample
coverage, matched timings and numerical outcomes, the final helper winner,
attempt accounting, artifact hashes, and the telemetry cutoff. The focused
replay/analysis and original race/attempt/integration suite passed all 123 tests;
Ruff checks passed. This completes the experiment record for owner review and
does not automatically close M14.
