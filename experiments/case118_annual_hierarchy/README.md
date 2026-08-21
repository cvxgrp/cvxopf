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

No scientific runner or scenario is frozen yet. The next step is S0 protocol
review: pinned PGLib acquisition and conversion, annual input construction,
storage placement and sizing, renewable placement, congestion-sensitivity
scope, and machine resource budgets.

Before week-scale execution, an experiment-owned streaming runner must pass
short-horizon, window-by-window equivalence against the public M17 controller.
This prerequisite supplies the checkpoint, resource-observer, and AC-build
release capabilities that the all-at-once public API intentionally does not
provide. P0 covers both nominal execution and deterministic recovery cases,
including target-free/copy recovery, both perturbation families, solver
failure and infeasibility classifications, and complete recovery exhaustion.
