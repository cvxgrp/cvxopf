# Annual Tracy DC comparison

## Result

Both full-year, 8,760-hour problems completed with CLARABEL status `Solved`
and public status `optimal`, and passed the worker and independent offline
physical/accounting audits. No meaningful load shedding was needed. No retries
or resource-limit increases were used. This completes the annual DC numerical
pair, not user review 2 or AC qualification.

The network changes annual dispatch levels appreciably while preserving much
of the aggregate temporal behavior. Relative to copper plate, lossy DC requires
**453.249 GWh more dispatchable energy (+10.59%)**, uses correspondingly less
renewable energy, and increases battery throughput by **3.35%**. Its native
solver time is **8.25 times** the copper-plate time; total worker time is
**6.73 times** as long.

## Inputs and comparison

The paired runs use the same owner-provided Tracy composite, scaled and mapped
to the approved PGLib Case118 fleet. They are not the older analytical-profile
benchmark. The source calendar is fixed UTC−08:00 throughout 2021; there are no
DST adjustments or missing hours. Annual demand is **30,761.962 GWh**, and
available renewables total **58,842.772 GWh**. Surplus annual energy does not
eliminate hourly shortages, storage limitations, or spatial restrictions.

Both formulations retain all individual devices: 54 generator rows (19 with
positive active capacity), 99 loads, 119 renewable channels, and 27 ideal
batteries. Dispatchable capacity is 5,000 MW. Storage capacity is
14,046.558 MWh with 4,682.186 MW aggregate power capability. Every battery
starts and ends at 50% SoC. Generator curvature is **rho = 1/3**, and battery
throughput penalty is **lambda = 0.01**. The uniform shedding penalty remains
20,763.594 objective units/MWh.

Single-node removes spatial network restrictions but does not aggregate or
retune the fleet. Lossy DC adds rated branch flows and a quadratic flow-loss
cost proxy. That proxy is **not an energy withdrawal in nodal balance**, nor
an estimate of realized AC loss. Differences therefore compare these two
formulations, not congestion alone isolated from every objective difference.

## Annual operation

| Quantity | Copper plate | Lossy DC |
|---|---:|---:|
| Dispatchable energy (GWh) | 4,279.168 | 4,732.417 |
| Renewable energy used (GWh) | 26,482.793 | 26,029.545 |
| Renewable curtailment (GWh) | 32,359.978 | 32,813.227 |
| Battery absolute throughput (GWh) | 8,006.597 | 8,275.078 |
| Peak aggregate dispatchable power (MW) | 3,711.887 | 3,711.891 |
| Hours with aggregate generation below 0.001 MW | 5,954 | 4,509 |
| Energy not served (MWh; numerical residue) | 2.06e-7 | 6.68e-7 |

Throughput sums absolute power across devices and hours; it includes both
charging and discharging, not just energy delivered from storage. Both fleet
SoC trajectories span essentially zero to full capacity. Their initial and
final aggregate SoC are 7,023.279 MWh, with per-device endpoints independently
checked. The annual balance explains the almost exact exchange between extra
dispatchable energy and extra curtailment: ideal storage has no energy loss
and returns to its initial state; shedding is negligible.

The network does not materially raise the annual aggregate generation peak
in this pair, but reduces the number of near-zero-generation hours by 1,445.
That difference in counts does not by itself identify the same hours in each
set; inspect the aligned time series for hour-specific comparisons.

### Temporal agreement

These are Pearson correlations and RMS differences across all 8,760 matched
hours, without detrending, rescaling, or shifting time. SoC uses the start of
each interval for this calculation.

| Aggregate trajectory | Correlation | RMS difference |
|---|---:|---:|
| Dispatchable generation | 0.9894 | 132.56 MW |
| Battery net power | 0.9819 | 234.06 MW |
| Battery SoC | 0.9847 | 738.55 MWh |

This quantifies the owner's Stage B observation: the dominant aggregate timing
can remain similar even when network restrictions require more dispatchable
generation. It does not imply interchangeable device-level trajectories or
network-realizable copper-plate signposts. Correlation can also be high because
both solutions share the same input cycles; it is not a controller-performance
metric.

### Agreement during sustained net-load stress

The owner observed that the formulations appear to agree more closely during
sustained high-net-load periods around December 19 than during renewable-rich
periods. A retrospective check of **December 18–21 inclusive** (96 hourly
intervals, fixed UTC−08:00) supports that observation for this window:

| RMS difference | Full year | December 18–21 |
|---|---:|---:|
| Aggregate dispatchable generation (MW) | 132.56 | 61.56 |
| Aggregate battery power (MW) | 234.06 | 58.74 |
| Aggregate battery SoC (MWh) | 738.55 | 181.69 |
| Bus-100 generator power (MW) | 110.68 | <1e-9 |
| Bus-9 battery power (MW) | 54.91 | 40.13 |
| Bus-9 battery SoC (MWh) | 154.16 | 114.27 |

These RMS values use matched hourly powers and interval-start SoC; each is
the square root of the mean squared lossy-DC-minus-copper-plate difference.
The device identities are `generator_row_44_bus_100` and `storage_bus_9`.
They have the largest absolute annual RMS power differences within their
respective fleets; these are not rankings normalized by device size.

Both formulations hold the bus-100 generator at its **501.15 MW maximum**
throughout these 96 hours, explaining its nearly exact agreement. On December
19 alone, the bus-9 SoC RMS difference is **53.85 MWh**, or **2.14%** of its
2,511.89 MWh capacity. Agreement is closer, not exact, and the December window
was inspected after seeing the results rather than selected as an independent
validation sample.

### Renewable surplus and the timing of differences

Grouping all annual intervals by **available net load before curtailment or
storage** gives the following descriptive comparison. Surplus means net load
<0 (5,611 hours); nonnegative net load covers the remaining 3,149 hours.

| RMS difference | Renewable-surplus hours | Nonnegative-net-load hours |
|---|---:|---:|
| Aggregate generation (MW) | 149.15 | 96.17 |
| Aggregate battery power (MW) | 280.78 | 109.23 |
| Aggregate battery SoC (MWh) | 866.85 | 422.42 |
| Bus-100 generator power (MW) | 133.03 | 50.41 |
| Bus-9 battery power (MW) | 40.76 | 73.67 |
| Bus-9 battery SoC (MWh) | 170.47 | 119.70 |

The aggregate differences are larger during surplus hours, consistent with
spatial access to renewable energy being an important source of disagreement.
However, this is not a monotonic rule that greater aggregate surplus produces
greater disagreement: July has the largest monthly RMS generation difference,
whereas May has the most negative monthly mean available net load. Location,
network restrictions, and storage history matter as well as aggregate abundance.

Bus-9 **power** is a notable exception: it differs more during nonnegative
net-load hours. Storage can carry the consequences of earlier charging choices
into later discharge periods; this is a plausible interpretation, not a causal
decomposition established by these grouped statistics.

### Questions to carry into AC qualification

Track whether the closer agreement during sustained December stress survives
AC realization, both for the fleet and these identified devices. Also track
whether renewable-rich/congested periods require larger departures from the
lossy-DC plan in battery power, SoC, dispatch, curtailment, or load service.
Inspect reactive support, voltage and apparent-power limits when explaining
new AC/DC differences: none is tested by copper-plate/DC agreement.

Compare AC actions with the lossy-DC guidance at aligned executed intervals,
retaining realized initial states and window endpoints. A receding-horizon AC
trajectory is not a matched full-year optimization, so departures can reflect
horizon/controller effects as well as network physics. Copper plate remains
a descriptive reference, not an alternative source of AC signposts in the
approved study. These observations motivate analysis during the separately
approved qualification; they do not establish that low-renewable periods will
be easy for AC or authorize additional solves.

### Where the network matters

At least one branch reaches 99.999% of its active-power rating in **6,248
hours**; **76 of 186** branches meet that threshold at least once. Maximum
utilization is effectively 100%, with no audited branch-bound violation.
The most frequently binding branches are:

| Branch buses | Hours at or above 99.999% |
|---|---:|
| 110 → 111 | 5,754 |
| 86 → 87 | 2,293 |
| 77 → 82 | 1,934 |
| 80 → 99 | 1,875 |
| 70 → 71 | 1,838 |

Binding hours are threshold-based observations, not shadow prices or proof
that relaxing a particular line would produce a specified economic benefit.
July accounts for about **128.58 GWh** of the extra dispatchable generation,
followed by August (**68.03 GWh**) and June (**46.55 GWh**). Full monthly energy,
cost and boundary-state tables are retained in
[`stage_c_summary/monthly.csv`](stage_c_summary/monthly.csv).

## Objective components

Costs are objective units, not asserted market dollars. Both arms use identical
device-cost coefficients; the loss proxy applies only to lossy DC.

| Component | Copper plate | Lossy DC |
|---|---:|---:|
| Generation | 101,079,714.401 | 109,380,720.046 |
| Battery throughput | 80,065.974 | 82,750.777 |
| Shedding | 0.0043 | 0.0139 |
| DC loss proxy | Not present | 9,484.130 |
| Total | 101,159,780.379 | 109,472,954.967 |

The objective increases by approximately **8.22%**, mostly through generation
cost, not the explicit loss-proxy cost. The annual pair fixes curvature and
throughput weight, so it does not independently test their sensitivity.

## Timing and memory

Single-node ran first and lossy DC second, sequentially on the same host from
clean execution commit `034ea6b9e5d9dd4276d6e742847f547c19a1047d`. Both used
vectorized construction, SCIPY canonicalization, CLARABEL with a 5,000-iteration
cap, one solver thread, and 1e-10 absolute-gap, relative-gap, and feasibility
tolerances. Neither used a warm start.

| Measurement | Copper plate | Lossy DC |
|---|---:|---:|
| Model construction (s) | 0.024 | 0.030 |
| CVXPY compilation diagnostic (s) | 3.329 | 5.285 |
| Native solver (s) | 58.965 | 486.581 |
| Canonicalization + solve wall (s) | 63.755 | 494.462 |
| Extraction + worker audit (s) | 0.063 | 0.090 |
| Archive writing (s) | 12.064 | 17.853 |
| Total worker (s) | 76.208 | 512.730 |
| Supervised lifetime (s) | 77.428 | 514.463 |
| Iterations | 38 | 204 |
| Peak sampled worker-PID RSS (GiB) | 6.368 | 7.678 |

Compilation and native solver times are diagnostics within the larger solve
phase, not additional phases to add to it. Parent input checks and independent
reconstruction are outside the worker times and recorded separately in
`study-result.json`. Memory is sampled once per second, not a continuous peak
or a measurement of parent/whole-machine usage.

Most of the timing difference is in solving rather than Python construction.
Network constraints increase problem size and the observed iteration count,
but this single sequential pair does not isolate which mechanism caused how
much of the slowdown. This is an observed paired timing result, not a
repeatability benchmark or an estimate of AC runtime.

## Validation and retained evidence

Both worker audits and the separate offline reconstruction passed. Maximum
nodal/aggregate balance residual is **2.22e-11 MW**, maximum SoC recurrence
residual **2.24e-12 MWh**, and maximum per-device terminal residual
**2.28e-13 MWh** (rounded upward). Objective-accounting residuals are
**1.49e-8** objective units. The negligible positive shedding is solver
numerical residue, not meaningful service interruption.

The read-only loader verifies hashes, exact input equality and device alignment
before plotting. [`stage_c_summary/summary.json`](stage_c_summary/summary.json)
retains the run/artifact hashes and machine-readable numerical summary. Full
archives, logs, native convergence diagnostics, resource samples and the
independent `analysis.json` remain under ignored `results/stage_c/`.

To reproduce the compact summaries into a fresh destination:

```sh
uv run python -m experiments.case118_tracy_2021.annual_results \
  --output experiments/case118_tracy_2021/results/my_annual_summary
```

## Interactive inspection and next decision

Launch the required notebook from the repository root:

```sh
uvx marimo run --sandbox experiments/case118_tracy_2021/annual_results_notebook.py
```

It includes selectable full-year input heatmaps, matched output heatmaps on
shared scales, signed **lossy DC − copper plate** heatmaps, date-selectable
input/output/difference time series, device-level comparisons, congestion,
residuals and annual/monthly tables. Calendar SoC heatmaps use interval-start
states; time series retain all selected boundaries, including the final one.
No missing-data fallback or new solves occur in this notebook.

The system exhibits substantial storage operation and frequent rated-network
congestion while serving essentially all demand in both DC formulations. The
high annual renewable surplus and curtailment are consequences to inspect at
**user review 2**, not reasons to silently rescale the approved inputs.

These results strengthen the motivation to investigate copper-plate planning
for MPC and uncertainty-rich studies, but do not validate those controllers.
The approved hierarchy remains lossy DC → AC. The next decision is owner
acceptance or revision of the fixture in light of this annual comparison,
followed by separately authorized bounded AC qualification. No AC solve or
shard derivation is authorized by this report alone. The owner subsequently
approved lossy-DC shard calculation; see the separate
[boundary report](SHARD_BOUNDARIES.md) for its partition and retained states.
That approval does not authorize AC execution.
