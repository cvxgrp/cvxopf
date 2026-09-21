# One Tracy week, five solutions

Open `notebooks/tracy_week_solutions.py` to inspect December 22–28, 2021 at
Case9 scale. The notebook reads a saved bundle and never runs a solver.

```sh
uv run --extra notebook marimo edit notebooks/tracy_week_solutions.py --sandbox --no-token
```

The initial bundle was assembled on 2026-09-20 from retained single-node
trajectory captures, two fresh lossy-DC solves, the original vectorized AC
solve, and both stepwise AC timeouts. Five successful solutions and one failed
mode are shown; the failed mode has two attempts, not two trajectories.

| Mode | Objective | Generation MWh | Curtailment MWh | Total seconds |
|---|---:|---:|---:|---:|
| Single-node stepwise | 316,499.152337 | 17,410.73 | 17,524.50 | 0.960 |
| Single-node vectorized | 316,499.152560 | 17,410.73 | 17,524.50 | 0.039 |
| Lossy DC stepwise | 326,369.682858 | 18,639.11 | 18,752.88 | 1.120 |
| Lossy DC vectorized | 326,369.682866 | 18,639.11 | 18,752.88 | 0.065 |
| AC vectorized | 341,477.711444 | 19,715.74 | 18,642.78 | 116.768 |
| AC stepwise | unavailable | unavailable | unavailable | timeouts at 180 and 1,800 s |

The comparison holds the hourly electrical inputs, fleet, costs, renewable
ratings, time step and storage boundary policy fixed. The analysis verifies
input arrays against the saved single-node inputs and checks source hashes
against the recorded experiments. Each saved trajectory passes its corresponding
current mathematical graph, including objective reconstruction; network trajectories
also pass the independent physical audit. No public solver code changed.

Representation pairs compare the same mathematics. Network formulations model
different feasible sets and losses: single-node has no network; lossy DC uses
branch-flow limits and a quadratic loss penalty without real-power losses in
balance; AC models reactive support, voltage and two-terminal branch limits.
The AC solution supplies 1,186.73 MWh of real losses. Its 1,076.63 MWh additional
generation relative to lossy DC and 110.10 MWh less curtailment together supply
those losses; both have zero net battery energy over the week. This accounting
does not isolate the causal economic effect of any one AC constraint.

All costs are in model objective units. The DC loss penalty is not labeled as
physical lost energy. Timings are separate single-run observations on different
dates. Single-node timings here come from its retained trajectory capture,
not the earlier timing-only report. No controlled speedup estimate or global
AC optimality claim is made. Unpriced dispatch allocations can be nonunique.

The explorer includes a baseline selector, hour range, generator/site selectors,
shared input plots, battery power and all 169 SoC boundaries, renewable output
and curtailment, dispatch, signed differences, selected-window statistics,
oriented branch power, and AC voltage/reactive channels. SoC comparisons include
both boundaries; power statistics use the half-open interval [start, end).

## Artifacts and rebuilding

- `TRACY_WEEK_COMPARISON_RESULTS.json`: compact summary, checks and provenance.
- `outputs/tracy_week_comparison/comparison.json`: full input/trajectory bundle.
- `outputs/tracy_week_comparison/summary.csv`: exportable summary.
- `outputs/tracy_week_comparison/lossy_dc_run/`: fresh worker results, logs and
  execution metadata captured before the workers launched.

Rebuild from retained local artifacts without solving:

```sh
uv run --extra notebook python -m experiments.m14_time_vectorization.analyze_tracy_week
```

The original fresh lossy-DC capture used `--run-lossy-dc`. That option refuses to
overwrite its existing run directory. Do not delete retained evidence merely
to rerun it. Raw Tracy data and prior trajectory artifacts are local, ignored
inputs; a fresh checkout alone does not contain them. The notebook's inline
dependencies cover inspection; rebuilding uses the repository environment.
