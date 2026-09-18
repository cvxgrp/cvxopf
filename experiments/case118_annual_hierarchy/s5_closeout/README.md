# Toy annual study closeout data and methods

This package supports [S5_REPORT.md](../S5_REPORT.md), Stage 0a of the
[study plan](../../../plans/case118-tracy-2021-study-plan.md). It contains
the **analytical 2025 UTC** fixture and descriptive results, not Tracy data.
The complete accepted analysis is saved in [S5_RESULTS.json](../S5_RESULTS.json).
Independent result review is CLEAN; the owner committed the scientific
closeout as `9c26366`. S5's formal closure and omission of the optional S6
toy congestion study are recorded in `3513284`.

## What is saved

| File | Meaning |
| --- | --- |
| `final_inputs.npz` | Exact float64 hourly per-load active/reactive arrays and per-device renewable availability; shapes (8760,99), (8760,99), (8760,2), units MW/MVAr/MW. No pickle. |
| `devices.json` | Ordered load/renewable IDs, buses, full generator cost/limit and storage parameters. |
| `aggregate_inputs.csv` | Final gross load, separate wind/solar, their sum, and load-minus-availability, in MW. Timestamp is interval start. |
| `input_summary.json`, `monthly_inputs.csv`, `shortfall_events.json` | Summaries from those arrays; events are non-wrapping and strictly above total Pmax. Monthly events are restricted to each month. |
| `bus_resources.csv` | All 118 bus resource allocations and annual load-minus-available-renewable energy; not physical flows. |
| `hourly_comparison.csv` | Saved dashboard first-action differences joined to independently recomputed annual DC covariates. Controller IDs, archive hashes, policy labels, interventions, and per-battery changes retained. |
| `dispatch_summary.csv` | Signed and absolute difference distributions. Power sums integrate to MWh at one-hour spacing; state-difference sums are deliberately blank. |
| `operation_summary.json` | DC/AC throughput and renewable accounting, generation cost terms, and annual net-generation reconciliation. |
| `dc_branch_loading.csv` | MW flow divided by positive in-service rating; counts ≥95% and ≥99.9%; original zero-based branch rows. No congestion duals. |
| `monthly_recovery_groups.csv` | Means and sample sizes by scenario month and recovery selection. |
| `correlations_*.csv` | Raw, calendar-adjusted, rounded/calendar-adjusted, and intervention-exclusion sensitivity; all 8760 hours except the exclusion sensitivity (8758). |
| `dispatch_source.json` | Original complete dashboard extraction metadata and checkpoint identities. |
| `retained_merge.json` | Exact copy of the accepted annual merge; not a replacement full independent analysis. |
| `prior_analysis_summary.json` | Exact retained partial summary of the previously completed analysis; **not its missing full payload**. |
| `provenance.json` | Source and numerical-artifact hashes, fixture identity, verification scope, software versions, sampled AC hours. |
| `input_heatmaps.png`, `signed_changes.png`, `absolute_changes.png`, `correlations.png` | Figures rendered from this package. |

The full input NPZ is about 12 MB. Keeping the actual arrays makes inspection
possible without reproducing the analytical builder. The comparison table is
about 7 MB and retains the hour-level evidence behind the report. These are
the limited evidence copies needed for closeout; the original ignored archives
and dashboard remain intact. Their broader promotion is Stage 0b.

## Exact input construction

Let h = 0,…,8759, d be one-based day of year, and u the UTC hour 0,…,23.
Let W = 0.035 on weekdays and −0.055 on weekends. The unnormalized load is

```text
L0 = 1 + .10 cos(2π(d−18)/365) + .11 cos(2π(u−18)/24) + W
       + .025 sin(2πh/173+.4) + .018 sin(2πh/619+1.7)
       + .010 cos(2πh/41)
L = round(L0 / annual_mean(L0), 9)

Csolar = clip(max(sin(π(u−6)/12),0)
              × (.78+.22 cos(2π(d−172)/365))
              × clip(.86+.10 sin(2πh/113+.8)+.06 cos(2πh/47),.55,1),0,1)
Cwind = clip(.43+.16 sin(2πh/149+2.1)+.10 sin(2πh/509+.2)
             +.06 cos(2πh/31),.05,.90)
```

Round both capacity factors to nine decimal places. Per-load active and
reactive demand equal their respective PGLib base values times L. Annual
renewable availability is fixed at 15% of annual load energy, half for each
resource. Each resource rating is its energy allocation divided by the sum
of its rounded capacity factors × one hour; hourly MW is rating × factor.
These formulas are deterministic, with no RNG. See
[`scenario.py`](../scenario.py) for operation order and
[`s4_fixture.py`](../s4_fixture.py) for the historical expected hashes.

Siting uses four-medoids clustering, weighting buses by base apparent load,
with shortest-path edge distance max(|x|,1e-6). Storage capacity is allocated
by cluster **active** load; solar uses the largest apparent-load cluster's
medoid, and wind the storage medoid electrically farthest from solar. Actual
bus/device assignments and capacities are saved in the tables above. This is
a deterministic placement heuristic, not an optimized location result.

## What the comparisons measure

All power values are engineering MW after result extraction. Δ denotes the
executed AC first action minus the annual DC schedule at the same interval.

- Net change: sum of signed device changes. L1: sum of absolute device changes.
- Opposing generator redispatch: `(L1 − abs(net))/2`, the smaller of total
  upward and downward movements. It counts paired movements once.
- Storage power is positive for discharge; positive Δb also includes reduced
  charging. End-state differences compare t+1 and obey
  `Δs[t+1] = Δs[t] − delta × Δb[t]` within each shard. Shards begin at the
  common saved boundary states. State differences are not throughput.
- Recovery-selected means that the chosen action is not the original primary.
  In this fixed archive the primary IDs end in `-00-primary_controlling` or
  `/spec-v1-00`; the count is checked against the accepted merge's 258.
  This is not a count of proven primary failures or physical infeasibilities.
- Signpost power fraction is the largest device-wise absolute DC boundary
  energy movement divided by its window duration and power rating. Windows
  are three hours, truncated at shard ends. It is not actual AC headroom.
- Raw correlations are Spearman coefficients (Pearson correlation of average
  ranks). Calendar adjustment demeans **global ranks** within month × hour ×
  weekday/weekend groups. Numerical sensitivity rounds MW/MWh to .001 and
  normalized values to .0001 before ranking. No stochastic independence or
  significance assumption is made. The first net-load ramp is defined as zero;
  there is no December-to-January wrap.

The DC and AC optimizations differ in network physics, device P/Q operating
sets, physical-loss accounting, horizon, realized initial state, and local
solution path. Here `lossy_dc` is a network-flow QP with nodal conservation and
branch MW boxes, without angle-based flow/loop equations; its resistance-weighted
loss proxy is an objective term. It is not the usual phase-angle DC power-flow
model. DC flows and intermediate storage states are also not strongly
identified by their objective. These descriptive differences are not minimum
AC feasibility repairs, global optimality gaps, or causal values of AC control.

## Reproduction and checks

From the repository root, create a fresh reporting directory (no study solve):

```sh
uv run python -m experiments.case118_annual_hierarchy.s5_closeout prepare --destination /tmp/toy-closeout
uv run --extra notebook python -m experiments.case118_annual_hierarchy.s5_closeout render --destination /tmp/toy-closeout
uv run --extra dev pytest tests/test_case118_s5_closeout.py tests/test_case118_s5_execution.py -q
```

Preparation uses the fixed September 17 dashboard snapshot by default, never
an advancing `LATEST` pointer. `--snapshot` allows an explicitly selected
complete snapshot with the same fixture/outer/manifest identities. The output
directory must be new to avoid mixing report versions. Rendering can run on
this saved package alone with NumPy, pandas, and Matplotlib; it does not load
the fixture, outer archive, or AC tree. These are reporting utilities, not a
new execution authority or runner protocol.

The original implementation run used the existing repository environment
via `uv run --no-sync` for preparation, and an existing Python 3.13 environment
with NumPy/pandas/Matplotlib for rendering because Matplotlib was absent from
the repository environment. No dependency lockfile was changed.

Preparation checks all retained dashboard rows against the twelve checkpoint
registries, exact input hashes, and calendar/identity alignment. It reads only
six selected AC windows (first, last, both interventions, and the largest
generator/battery L1 changes) to recompute first-action differences independently.
These checks are **not** a full independent physical audit. The latter remains
the separate one-pass `s5_analysis --promote` gate. The annual DC archive is
read once to recompute covariates and verify the saved active/reactive load
arrays; its accepted identity and storage trajectory are checked without solves.

The completed full reconstruction and historical review gates are described in
the [Stage 0a review checkpoint](../S5_CLOSEOUT_CHECKPOINT.md). The full
analysis passed independent review and was committed in `9c26366`.
