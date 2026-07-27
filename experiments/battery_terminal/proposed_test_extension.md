# Proposed test extension — time-resolution sensitivity

## Status and scope

This is an optional, small extension to the battery terminal-policy
experiment. It is **not** the main purpose of the study.

The primary experiment remains the validation and characterization of the new
storage terminal-target features:

- hard terminal equality;
- hard zero-shortfall constraint;
- two-sided linear and quadratic terminal costs;
- one-sided linear and quadratic shortfall costs;
- terminal-target value functions;
- soft-weight response; and
- terminal-policy locality in the SoC trajectory.

This extension asks one secondary robustness question:

> Does the observed relationship between stage costs and a once-per-horizon
> terminal target change when the same physical horizon is represented at a
> different temporal resolution?

It should be run after the primary terminal-policy results are stable. Its
results should be reported separately and should not expand the main policy
sweep.

## Motivation

The current experiment uses hourly source data with:

```text
delta = 1 hour
```

At that resolution, summing hourly-average power values also gives energy in
MWh numerically, and the current objective convention is easy to overlook.
Storage dynamics use `delta`, while stage objective terms are currently
summed without multiplying by `delta`. Terminal penalties are correctly added
once per horizon.

The proposed extension is a diagnostic for that convention. It is not needed
to establish that the terminal-target API works correctly.

## Experimental question

Choose one representative physical horizon and solve equivalent
representations at multiple time steps:

| Representation | Step duration | Number of steps for 24 hours |
|---|---:|---:|
| Hourly baseline | 1.0 h | 24 |
| Half-hour | 0.5 h | 48 |
| Quarter-hour | 0.25 h | 96 |

The preferred initial horizon is the final 24 hours of the existing
`moderate` representative window. It has meaningful storage use and avoids
turning the extension into another large scenario cross-product.

The comparison should answer:

1. Does terminal SoC remain consistent across resolutions?
2. Does the operating dispatch remain approximately consistent?
3. How does the terminal penalty compare with the accumulated stage
   objective?
4. Which differences arise from temporal interpolation and which arise from
   objective scaling?

## Signal construction

The existing observations are hourly-average powers. A finer grid must not
invent additional energy or alter the physical scenario.

For the first diagnostic, use zero-order hold:

```text
each hourly-average value is repeated over its subintervals
```

Thus:

- a 1-hour value appears once at `delta=1`;
- the same value appears twice at `delta=0.5`;
- the same value appears four times at `delta=0.25`.

Apply this construction consistently to:

- active load;
- reactive load; and
- every nondispatchable availability channel.

Do not interpolate with splines or linear ramps in the initial test.
Interpolation would introduce a second experimental question about
intra-hour trajectories.

Validate energy preservation explicitly:

```text
sum(hourly power) * 1.0
    == sum(half-hour power) * 0.5
    == sum(quarter-hour power) * 0.25
```

within floating-point tolerance, for every aggregate source and load channel.

## Fixed physical system

Reuse the existing experiment specification unchanged:

- case9 network;
- dispatchable generators and costs;
- renewable-site placement and inverter ratings;
- one bus-7 ideal battery;
- 150 MVA storage power rating;
- 1,000 MWh storage capacity;
- 500 MWh initial SoC; and
- lossy-DC formulation.

Device ratings must be prepared from the existing full experiment scenarios,
not resized from the 24-hour extension window.

## Policies

Keep the extension deliberately small. Use three policies:

1. no terminal policy;
2. hard equality at 500 MWh; and
3. quadratic terminal cost at 500 MWh using the currently approved weight.

These cover:

- the unconstrained terminal reference;
- exact terminal recovery; and
- the tradeoff between operating cost and terminal deviation.

Do not rerun all seven policy modes unless the three-policy diagnostic reveals
a material resolution effect.

The proposed matrix is therefore:

```text
1 physical horizon
× 3 resolutions
× 3 policies
= 9 solves
```

## Two objective conventions

The extension should distinguish the package's current behavior from a
diagnostic physical-time convention.

### Convention A — current package behavior

Use the library unchanged:

```text
objective = sum(stage terms) + terminal term
```

This is the authoritative result for the current API.

### Convention B — diagnostic time-integrated stage objective

For comparison only:

```text
objective = delta * sum(stage-rate terms) + terminal term
```

Do not implement Convention B by patching library source inside the
experiment. Prefer one of:

- an explicit experimental objective reconstruction when it is sufficient for
  analysis; or
- a small experimental builder that composes the same constraints and clearly
  labels the altered objective convention.

If reconstruction cannot reproduce the optimized Convention-B dispatch, stop
at the Convention-A sensitivity result rather than creating an opaque local
fork of the OPF builder.

Any package-level objective change belongs to the separate
`plans/correctness-api-hardening.md` decision process.

## Measurements

For each solve, record:

- status;
- total objective;
- operating objective excluding terminal cost;
- terminal cost;
- terminal SoC;
- signed and absolute terminal deviation;
- minimum and maximum SoC;
- minimum and maximum battery power;
- total dispatchable generation energy:
  `delta * sum_t sum_g Pg[t,g]`;
- total renewable curtailment energy:
  `delta * sum_t sum_n curtailment[t,n]`;
- storage charge throughput:
  `delta * sum_t max(-b[t], 0)`;
- storage discharge throughput:
  `delta * sum_t max(b[t], 0)`;
- maximum branch utilization; and
- maximum constraint violation.

Retain named objective contributions where available:

- generator cost;
- line-loss regularizer;
- storage cycling cost; and
- storage terminal cost.

If the current build does not expose every contribution separately, report
only quantities recoverable without duplicating internal cost mathematics.

## Comparisons

Use the hourly case as the reference within each policy and objective
convention.

Report:

- absolute and relative objective differences;
- terminal-SoC differences;
- energy-total differences;
- storage-throughput differences;
- maximum differences between hourly dispatch and subinterval-aggregated
  dispatch; and
- whether policy rankings change with resolution.

Aggregate fine-grid powers back to hourly means before comparing trajectories:

```text
hourly mean of two half-hour powers
hourly mean of four quarter-hour powers
```

Compare SoC only at common hourly boundaries.

## Expected interpretation

Under zero-order-held physical data and ideal storage:

- energy totals should agree closely across resolutions;
- hard terminal equality should remain at 500 MWh;
- common-boundary SoC and dispatch should be similar when the objective
  convention is resolution-consistent;
- under the current unscaled stage-sum convention, refining the grid may
  increase stage-objective influence relative to the once-per-horizon terminal
  cost; and
- the quadratic soft-policy terminal deviation may therefore change with
  resolution even though the physical horizon is unchanged.

These are hypotheses, not acceptance criteria. Congestion, active-set
changes, storage aging, solver tolerances, and nonunique schedules may produce
additional differences.

## Acceptance gates

### Construction

- All representations cover exactly the same physical interval.
- Every load and availability channel preserves energy under resampling.
- Device specifications and terminal-policy parameters are identical.
- `delta` matches the constructed time grid.

### Numerical validity

- Every reported optimal solve has finite primal values.
- Maximum constraint violations remain within the experiment's existing
  tolerance.
- Hard equality reaches 500 MWh within tolerance.
- Energy metrics include `delta` explicitly.

### Reporting

- Current package behavior and any diagnostic integrated convention are
  labeled separately.
- No result is presented as a change to the primary terminal-feature
  conclusions.
- Differences caused by interpolation are excluded from the initial
  zero-order-hold study.
- Raw summary rows and reproducibility metadata are retained.

## Suggested implementation

Add one module:

```text
experiments/battery_terminal/resolution_study.py
```

with:

- a pure helper that repeats hourly scenario frames and returns the matching
  `delta`;
- energy-preservation validation;
- a nine-case Convention-A runner;
- common-boundary aggregation helpers; and
- a tabular summary dataclass consistent with the existing experiment
  runners.

Add focused tests for:

- frame lengths and index cadence;
- per-channel energy preservation;
- common-hour aggregation;
- terminal equality at two resolutions; and
- explicit use of `delta` in every reported energy metric.

The main reproduction command need not include this extension initially. It
may be added after the study design and outputs are accepted.

## Stop condition

The extension is complete when it can state, for the selected 24-hour
moderate window, whether the principal terminal-policy observations are
robust to a change in numerical time resolution.

It should not grow into:

- a new scenario-selection study;
- an intra-hour forecasting model;
- a full AC cross-product;
- a redesign of storage physics; or
- the package-level objective-units implementation.

