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

The PGLib acquisition, deterministic case conversion, authoritative pilot,
independent residual gates, and workstation resource limits are frozen and
hash-verified. S0 and S1 are complete. S1 characterized a 24-hour outer plan
and one bounded six-hour AC endpoint realization under `S1_PROTOCOL.md`;
direct 24-hour AC remains explicitly unauthorized by the S0 resource gate.

Before week-scale execution, an experiment-owned streaming runner must pass
short-horizon, window-by-window equivalence against the public M17 controller.
This prerequisite supplies the checkpoint, resource-observer, and AC-build
release capabilities that the all-at-once public API intentionally does not
provide. P0 covers both nominal execution and deterministic recovery cases,
including target-free/copy recovery, both perturbation families, solver
failure and infeasibility classifications, and complete recovery exhaustion.
`P0_PROTOCOL.md` freezes the compact 6-/24-hour orchestration fixtures, exact
fault matrix, case118 S1 archive gate, and transaction/resume contract. P0 is
complete: the clean consolidated record in `P0_RESULTS.json` passed every
sub-gate and authorized S2. `P0_REPORT.md` gives the human-readable handoff.

The frozen six-hour S0 pilot is executed from a clean checkpoint with:

```bash
uv run python -m experiments.case118_annual_hierarchy.run_s0
```

The ignored gzip artifact retains all four public results, independent audits,
dimensions, timings, solver statistics, peak-memory observations, and source
provenance. A compact tracked scientific record is produced only after the
artifact passes review.
