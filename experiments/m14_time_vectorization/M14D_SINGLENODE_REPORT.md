# M14d single-node DC checkpoint

The explicit `singlenode_dc` vectorized builder is implemented for owner review.
It reuses the qualified DC device hooks, adds one horizon-wide copper-plate
balance, integrates stage costs once with `delta`, and retains terminal policies
and public result shapes. Stepwise remains the default. AC is not implemented
by this checkpoint; the required lossy-DC/AC Case9 closure comparison remains.

## Initial Tracy comparison

Run:

```sh
uv run --extra dev python -m experiments.m14_time_vectorization.run_m14d_singlenode
```

The source is the existing ignored `battery_terminal/data/9q9wtp_gen_and_load.csv`,
SHA-256 `45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f`.
The selected week is December 22–28, 2021, fixed UTC−08:00. This bounded
single-node test takes its first 3 and 24 hours before running all 168 hours;
this does not freeze the later AC ladder or T=3 slice.

Inputs use the existing Case9 spatial mapping and common source multiplier
`315 / 1138.7624473656565`, with no extra resource scaling. The prior quadratic
generation costs and 10–105/130/115 MW limits are retained. Storage is one
150 MW, 1,000 MWh unit, initially 500 MWh, with hard terminal equality at
500 MWh and throughput cost 0.01. Renewable ratings are 1.1 times the selected
full week's site maxima, held fixed across prefixes and representations. The
interval is one hour. There is no curtailment cost or single-node loss penalty.
This comparison uses hard terminal equality; all seven terminal-policy variants
are covered by the focused contract tests.

Each row runs once in a fresh process with CLARABEL defaults. Stepwise uses
CPP; vectorized uses SCIPY as required by its data structures. The intervention
is vectorization. These are initial observations, not a statistical speedup claim.
Input preparation and imports are excluded from time measurements; process peak
RSS includes them. Explicit canonicalization precedes `build.solve()`, whose
wall time includes cached compilation and solver-interface overhead. Native
solver time is reported separately and is not added again to total time.

| T | Assembly | Build s | Canonicalize s | Solve wall s | Solver s | Extract s | Total s | Peak RSS MiB |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 3 | Stepwise | .00689 | .01531 | .00123 | .00030 | .00012 | .02355 | 161.7 |
| 3 | Vectorized | .00435 | .01431 | .00123 | .00024 | .00016 | .02005 | 161.2 |
| 24 | Stepwise | .02422 | .10785 | .00580 | .00238 | .00060 | .13847 | 171.6 |
| 24 | Vectorized | .00422 | .01458 | .00309 | .00212 | .00013 | .02202 | 161.0 |
| 168 | Stepwise | .17161 | .81482 | .03957 | .01769 | .00417 | 1.03017 | 247.0 |
| 168 | Vectorized | .00481 | .01600 | .02148 | .01930 | .00017 | .04247 | 162.8 |

At T=168, source variable objects fall from 672 to 4, constraint objects from
1,849 to 4, and parameter objects from 504 to 3. Bounds remain enforced as
qualified leaf bounds: fewer explicit constraint objects does not mean fewer
physical limits. The vectorized model retains one additional scalar initial
SoC boundary (2,017 versus 2,016 source scalar variables).

## Numerical outcomes and schedule differences

All six solves are `optimal`. Objective relative differences at T=3/24/168 are
`1.15e-10`, `7.64e-11`, and `7.03e-10`. At T=168 the objectives are
316,499.152337 and 316,499.152560. Independent checks reconstruct aggregate
balance, SoC dynamics and terminal equality, device bounds, and generation plus
cycling cost. The largest audit residual across all runs is below `1e-9` in
the audit's physical or objective units.

The owner-requested investigation threshold of 0.1% is exceeded by some primal
trajectories, despite negligible generation and objective differences. For the
168-hour runs, maximum differences over the entire trajectories are 2.594 MW
battery power, 4.031 MWh SoC, and 14.856 MW individual renewable output. These
are not differences at the terminal timestamp: both runs finish at the required
500 MWh, with terminal SoC errors of 0 and `5.68e-14` MWh. The 4.031 MWh
difference occurs within the horizon. For the 24-hour runs, the corresponding
trajectory maxima are 2.161 MW, 4.245 MWh, and 26.194 MW.

These were investigated by assigning each complete solution and their midpoint
to **both actual CVXPY model graphs**, without resolving or changing costs.
All assignments satisfy the explicit constraints within `2.3e-13`; clipping
solver roundoff to vector leaf bounds moves a coordinate by less than `6e-14`.
All evaluated objectives remain within `7.1e-10` relative of the stepwise
optimum. This supplies numerical evidence for alternative optimal schedules:
strictly convex generation costs closely fix dispatch, while linear battery
throughput and free renewable curtailment leave scheduling freedom. It is not a
symbolic uniqueness certificate, and no claim of identical device trajectories
is made. No tolerance or cost was changed to remove these differences.

The JSON record retains exact timings, source graph dimensions, audits,
trajectory-difference metrics, cross-graph/midpoint probes, package versions,
platform, and production/fixture/runner hashes. Rerunning replaces that local
result file with a new observation.

## Input and output time series

The figures below show the complete December 22–28 week at the **Case9 input
scale** used in the solves. Inputs follow the Tracy notebook's weekly view:
load and net load with a 0.5-alpha fill between them, and available renewables
stacked with DG solar at the bottom. Net load uses available renewable power,
before curtailment.

![Tracy weekly inputs at Case9 scale](../../experiments/m14_time_vectorization/results/singlenode_vectorization/tracy_week_inputs.png)

Output plots compare stepwise (solid) and vectorized (dashed) solutions, with
signed differences **vectorized minus stepwise** in the right column. Battery
power is positive when discharging. Power values are timestamped at interval
start; SoC is shown at all 169 boundaries, including the initial and terminal
500 MWh values. The maximum SoC difference is inside the week.

![Battery power and state of charge, with differences](../../experiments/m14_time_vectorization/results/singlenode_vectorization/battery_trajectories.png)

Renewable curtailment is available power minus renewable output. The plot shows
the total and all seven individual sites, grouped by resource. Since both runs
use identical availability, the difference in curtailment is the negative of
the difference in renewable output. Individual allocations differ more than
aggregate curtailment, whose maximum difference is 2.594 MW.

![Total and individual renewable curtailment, with differences](../../experiments/m14_time_vectorization/results/singlenode_vectorization/renewable_curtailment_trajectories.png)

The three dispatchable generators nearly coincide between representations.
Their differences are shown on separate scales: the largest individual
difference is approximately `2.02e-5` MW.

![Total and individual generator dispatch, with differences](../../experiments/m14_time_vectorization/results/singlenode_vectorization/generator_dispatch_trajectories.png)

The original timing record retained summary metrics but omitted hourly arrays.
These figures use a separate fresh-process capture of the two 168-hour solves,
with the same source hashes, inputs, and settings. Objectives and all four
maximum trajectory differences reproduce the original record. The timing table
and its JSON record are unchanged. Complete captured trajectories and inputs
are saved alongside the figures in the ignored local directory
`experiments/m14_time_vectorization/results/singlenode_vectorization/`; the images are local report attachments.
Regenerate them from the repository root with:

```sh
uv run --extra notebook python -m experiments.m14_time_vectorization.plot_singlenode_comparison
```

## Verification and review scope

`uv run --extra dev pytest tests/ -q`: **2,831 passed, 1 skipped**, in 168.77 s.
The 22 new focused tests pass. `uv run --extra dev ruff check src tests`, the
configured `uv run --extra dev mypy`, and `git diff --check` also pass.

The focused tests cover imported and explicit loads, collapsed bus indexing,
T=1 single-step parity, full result schemas, nonunit time integration, active
load shedding, all terminal policies, renewable limits, static broadcast data,
minimal cases, supported polynomial/PWL costs, empty optional devices,
nonconsecutive bus IDs, ignored HVDC, initialization, caller constraints,
unsolved/infeasible result handling, and SCIPY enforcement.

The review register is **public tool**, covering API compatibility, correctness,
and ordinary failure handling. Hostile-actor hardening, AC vectorization, and
changes to costs or the hierarchy algorithm are outside this checkpoint.
