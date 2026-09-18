# Annual case118 hierarchy experiment

This directory contains the reproducible scaling study defined in
[`plans/experiment-case118-annual-hierarchy.md`](../../plans/experiment-case118-annual-hierarchy.md).

**Current status:** S5 is complete and formally closed. All 8,760 hourly AC
actions and the annual merge passed independent reconstruction. The
owner-approved [S5 report](S5_REPORT.md) and [complete result](S5_RESULTS.json)
were committed in `9c26366`; [artifact promotion](S5_ARTIFACT_DISPOSITION.md)
and analysis tools were committed in `0ae9d86`.

This is an operator-assisted, partitioned synthetic toy-data trajectory,
including the retained recovery amendments and interventions. It is not the
Tracy study or evidence of uninterrupted autonomous annual execution.
The [Stage 0c follow-up findings](../case118_counterfactual/README.md#stage-0c-findings)
are separate experiments about dispatch adjustments and battery value.
The owner chose to omit the optional S6 PGLib active-power-increase sensitivity:
no congestion study will be conducted on the toy data. `accepted_for_s6=true`
remains an eligibility result, not a requirement to execute S6. The annual
analysis and reproducibility record proposed as S7 are complete, closing this
study. Any future Tracy congestion study requires a separate design and
authorization. The remaining text describes the original design and
early-stage execution history.

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
