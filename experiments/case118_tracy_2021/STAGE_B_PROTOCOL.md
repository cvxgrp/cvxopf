# Stage B — targeted DC studies

Status: **proposed pre-execution protocol**, opened after owner approval of
Stage A at `39609190f`. The owner authorized beginning Stage B; the numerical
72-solve comparison grid below is owner-approved. Remaining execution details
are proposed for review, not a record of executed work.
No runner or numerical solve has been started. Annual DC and all AC execution
remain outside this stage.

## Questions

1. Do the approved capacities and siting support useful storage operation and
   full load service under the rated lossy-DC model?
2. How much does collapsing network restrictions change dispatch, curtailment,
   shedding and per-device SOC?
3. How do generator curvature and battery throughput regularization affect
   storage operation, separately and in combination?
4. Are apparent operating restrictions attributable to finite-window endpoints?

Single-node is comparison-only. It does not replace the lossy-DC source of
future AC signposts. Successful DC windows do not establish annual or AC
feasibility.

## Fixed inputs

Use the approved Stage A source, mapping, device fleet and costs. Verify the
retained source/array identities before construction. Do not resize resources,
redraw sites, change branch ratings, or add temporal noise. Use vectorized time
assembly with SCIPY canonicalization and CLARABEL for both DC formulations.
Retain the package's lossy-DC loss weight of **1.0**, explicitly recorded in
each build. This value is proposed here for the numerical protocol; single-node
has no loss proxy. Keep load shedding enabled at the approved uniform
20,763.594 objective units/MWh with full fractional eligibility, fixed across
all economic comparisons. Renewable curtailment has no separate penalty.

## Input-only window selection

All dates below use the source's fixed UTC−08:00 calendar. Stops are exclusive.
These candidate windows were selected from approved input arrays, not solves:

| Candidate | Interval | Hours | Purpose |
|---|---|---:|---|
| Deficit | February 1–March 1 | 672 | Includes the February 19 peak net load and largest continuous deficit event. |
| Persistent surplus | April 1–June 1 | 1,464 | Two-month coupling; includes the May 1 maximum available surplus. |
| Gross-load peak | June 3–June 17 | 336 | Includes June 9's 10,463.20 MW gross-load peak. |
| Ramp | October 11–October 25 | 336 | Includes the largest absolute hourly net-load ramp, October 17 at 23:00. |
| Regional import | July 12–August 9 | 672 | Owner-approved addition covering the bus-87 region's strongest import week and hourly peak. |
| Ordinary control | January 14–January 28 | 336 | Selected by the deterministic median-profile rule below, not a favorable solve. |

For the ordinary control, consider non-wrapping 14-day windows beginning at
midnight. For each, compute mean load, mean available net load, maximum
positive net load, and mean absolute hourly net-load ramp (differences within
the window only). Standardize each feature by its across-candidate interquartile
range, omitting zero-IQR features. Select the window closest in squared
standardized distance to the componentwise median, excluding overlap with the
four event windows; break ties by earliest start. Save all feature values and
the selected start before any solve.

The ordinary-control selection remains frozen against those original four
event windows; adding the regional-import segment does not reselect the control.

Applying that rule selects **January 14 00:00–January 28 00:00**, exclusive
stop. Of 352 possible fortnights, 194 do not overlap the four event windows.
Medians and IQRs are computed over all 352 candidates before excluding overlaps;
the selected squared standardized distance is **0.129260**. Its mean load is
3,501.04 MW, mean net load −2,562.33 MW, peak positive net load 4,935.28 MW,
and mean absolute hourly net-load ramp 1,391.76 MW. “Ordinary” refers to these
four input features, not proven absence of congestion or solver difficulty.
The [full score table](stage_b_selection/ordinary_candidates.csv) and
[selection record](stage_b_selection/ordinary_selection.json) retain the
features, normalization, eligibility and selected result.

### Weekly coverage of the approved periods

Each point below is one of the **359 seven-day windows starting at midnight**
in 2021, at the approved Case118 multiplier **3.0548333420396085**. Colored
points are those weeks **fully contained** in an approved period: the week's
start is at or after the period's start, and its exclusive stop is at or before
the period's exclusive stop. Partially overlapping weeks are not highlighted.
These are weekly metrics, not totals or extrema over the entire multiweek period.

Deficit contributes **22** weeks; persistent surplus **55**; gross-load peak
**8**; ramp **8**; regional import **22**; ordinary control **8** (weekly starts January 14–21).
Black outlined points retain the notebook's named weeks (including energy and
peak low/median/high); callouts give their inclusive calendar dates. A named
week can also belong to a colored group. Overlapping weeks are descriptive
coverage, not independent samples.

![Net energy versus peak net load](stage_b_selection/energy_peak.png)

![Net energy versus minimum net load](stage_b_selection/energy_minimum.png)

![Peak versus minimum net load](stage_b_selection/peak_minimum.png)

Regenerate these static figures with
`uv run --extra notebook python -m experiments.case118_tracy_2021.plot_window_selection`.
The [weekly table](stage_b_selection/weekly_metrics.csv),
[named weeks](stage_b_selection/labeled_weeks.json), and
[provenance](stage_b_selection/provenance.json) retain the plotted values,
membership and input identity. The live notebook is unchanged.

### Spatial-pressure screen

The input-only rule below is declared before evaluating temporal rankings:

- Form four topological regions using active branches as undirected edges.
  Start at bus 1; repeatedly add the bus farthest in unweighted graph distance
  from its nearest existing seed (smallest bus ID breaks ties). Assign every
  bus to its nearest seed, breaking ties by smallest seed ID. This produces
  connected regions without using load/renewable outcomes or geographic claims.
- Retain every crossing branch in source-row order, including parallel lines,
  and sum its positive finite rateA. Fail rather than invent a rating for an
  unrated cut. This sum is a normalization, not AC transfer capability.
- For each region/hour let L be gross load, R all renewable availability,
  G the approved local generator Pmax sum, and B the local battery MW sum.
  Retain signed L−R, potential import max(L−R,0), import remaining after
  maximum local generation max(L−R−G,0), and after maximum discharge
  max(L−R−G−B,0). Retain potential export max(R−L,0) and export remaining
  after maximum charging max(R−L−B,0). Express each nonnegative score in MW
  and divided by the region's cut-rating sum. No optimized dispatch is assumed.
- For each score compare the full-year hourly maximum and maximum 168-hour
  mean (daily-midnight starts, non-wrapping) with maxima inside the selected windows.
  A week counts only if fully contained. Record earliest ties and coverage of
  positive hours at or above the full-year 99th percentile. Zero-only scores
  have no stress event and no meaningful peak-coverage ratio.

Maximum charging/discharging is an optimistic instantaneous capability, not an
energy-feasible schedule. Export is optional because renewables are curtailable;
imports assume full load service. The screen omits reactive support, voltage,
internal regional bottlenecks and simultaneous constraints on different cuts.
It is neither a dispatch prediction nor an infeasibility certificate. Four
regions are a coarse coverage check, not an exhaustive network-cut search.
Any proposed replacement or additional window returns to the owner for
approval; the approved grid now has six windows and 72 solves.

**Screen completed:** see the [spatial-pressure report](SPATIAL_PRESSURE_REPORT.md),
[region/cut definitions and coverage](stage_b_selection/spatial_screen.json),
and [original five-window score table](stage_b_selection/spatial_coverage.csv).
The original windows missed the bus-87 region's July sustained import pressure
and August hourly peak. The owner approved adding **July 12–August 9**, bringing
the grid to **72 solves**. The [updated six-window coverage](stage_b_selection/spatial_screen_approved.json)
retains the new assessment separately from the original screen. December's
pre-generation pressure remains a documented limitation.

## Comparisons and endpoints

The owner-approved baseline is **rho = 1/3 (on)** and **lambda = 0.01
(medium)**. Run the complete grid:

| Generator curvature | rho | Battery throughput penalty | lambda |
|---|---:|---|---:|
| On | 1/3 | Low | 0.0001 |
| On | 1/3 | Medium — baseline | 0.01 |
| On | 1/3 | High | 1.0 |
| Off | 0.001 | Low | 0.0001 |
| Off | 0.001 | Medium | 0.01 |
| Off | 0.001 | High | 1.0 |

“Off” is the label for the almost-linear setting, not exactly zero curvature.
Lambda has units of objective units/MWh of absolute one-way storage throughput;
rho is dimensionless. Apply rho only to the 19 positive-capacity generators,
using c2_i = rho * c1_i / G_i. Preserve the reactive-only entries' inherited
costs. Inherited c0/c1, all capacities, and the uniform shedding penalty stay
fixed across the grid; do not recalibrate shedding per arm.

Run **all six settings × six selected windows × two formulations = 72 convex
solves**, with matched single-node and rated lossy-DC inputs. This full factorial
comparison supports examining each economic factor and their interaction in
every window and formulation. Run shorter horizons first; freeze the complete
arm order before execution. Each arm starts in a fresh process without a
cross-arm warm start.

All 72 arms use per-device **50% initial SOC and hard 50% terminal SOC**, the
same full-year-derived capacities, and the complete selected window, with no
wrapping or hidden padding. These endpoints are experimental conditions, not
inferred annual states. There are no extra free-terminal sensitivity or
conditional confirmation solves in this batch. If the results indicate a
consequential endpoint effect, propose a separate bounded diagnostic.

After reviewing all available outcomes, recommend annual economics. Do not
select settings by comparing raw objectives across changed cost functions;
compare operating quantities and cost components with their coefficients explicit.

## Proposed execution limits

- Sequential fresh-process solves; no concurrent arm competition or retries.
- Exactly 72 planned arms in the approved grid; no additional diagnostic or
  confirmation arms without separate approval. Failure stop rules still apply.
- Per-process wall limit: 30 minutes including construction and extraction.
- Per-process peak RSS limit: 16 GiB. The previous eight-hour aggregate proposal
  is superseded by the 72-arm design: at the proposed 30-minute per-arm limit,
  cumulative supervised arm time is bounded by 36 hours, plus orchestration
  overhead. This is a ceiling, not a runtime estimate.
- Stop the batch on a resource termination, exception, solver failure,
  infeasibility, or failed independent audit; retain the failed arm and report
  it. Do not repair data, loosen tolerances or select another solver in flight.
- Check process-monitoring permissions before launch. Record software, native
  solver settings, source identity and host context. Thermal telemetry is
  contextual evidence where available, not an extra scientific acceptance gate.

Before implementation, specify CLARABEL tolerances and independent residual
thresholds by reusing the relevant qualified DC audit conventions. Document
them here explicitly rather than relying on mutable solver defaults. Acceptance
requires usable finite primal values and independent checks, not status alone.
No new elaborate execution framework: reuse suitable existing supervision and
audit utilities after checking their applicability to this fleet.

## Required analysis and next gate

Save per-device dispatch, original/served/shed load, renewable availability/use,
storage power and boundary SOC. Independently check nodal or aggregate balance,
all present-family operating limits, SOC recurrence and endpoints, branch bounds
where applicable, cost decomposition and ENS. Report the DC quadratic loss proxy
as such, not as independently computed AC physical losses.

Compare shedding, curtailment, throughput, SOC, generation leveling/marginal costs,
binding branch locations and durations, and objective components separately.
Record construction, canonicalization/solve, extraction, total wall time and
peak RSS using clearly defined clocks. Distinguish input-predicted stress from
solved congestion. Fixed-load diagnostics, if needed to interpret shedding,
require an explicit additional bounded decision rather than automatic launches.

Stop for the owner's review of Stage B findings and proposed annual economics,
remaining configuration choices, and annual runtime/resource estimates for
both formulations. Stage C requires separate approval.
