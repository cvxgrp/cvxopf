# Annual case118 hierarchy experiment

This directory will contain the reproducible scaling study defined in
[`plans/experiment-case118-annual-hierarchy.md`](../../plans/experiment-case118-annual-hierarchy.md).

The experiment tests whether the M17 hierarchical controller can realize a
storage-coupled AC trajectory over progressively longer horizons on the rated
PGLib-OPF case118 network, culminating in 8,760 hourly intervals only if the
declared outer-solve, throughput, memory, and audit gates pass. The repository's
MATPOWER case118 is retained only as an implementation/scale comparator. The
causal congestion control is an otherwise identical converted PGLib case with
only `rateA` set to zero. AC then omits those limits, while lossy DC uses one
frozen, ex-post-verified finite branch-limit sentinel; this is an effectively
unlimited rather than mathematically unconstrained control.

The PGLib acquisition and deterministic case conversion are now frozen and
hash-verified. S0 is continuing with annual input construction, deterministic
storage and renewable placement, the predeclared pilot grid, independent
residual audits, and machine resource budgets. No authoritative annual
scenario or scientific streaming runner is frozen yet.

Before week-scale execution, an experiment-owned streaming runner must pass
short-horizon, window-by-window equivalence against the public M17 controller.
This prerequisite supplies the checkpoint, resource-observer, and AC-build
release capabilities that the all-at-once public API intentionally does not
provide. P0 covers both nominal execution and deterministic recovery cases,
including target-free/copy recovery, both perturbation families, solver
failure and infeasibility classifications, and complete recovery exhaustion.

The frozen six-hour S0 pilot is executed from a clean checkpoint with:

```bash
uv run python -m experiments.case118_annual_hierarchy.run_s0
```

The ignored gzip artifact retains all four public results, independent audits,
dimensions, timings, solver statistics, peak-memory observations, and source
provenance. A compact tracked scientific record is produced only after the
artifact passes review.
