# Milestone 19 — First-class loads and explicit load shedding

**Status:** implementation in progress; Stages 0–6 complete

**Depends on:** Milestone 16 and M16+ component ownership and shared assembly;
the objective-time-units decision in `correctness-api-hardening.md`

**Enables:** explicit energy-not-served studies; controlled resilience
extensions of Milestone 17; stable load identity for future demand-side models

## 1. Goal

Make loads first-class, composable grid devices and allow selected loads to be
sheddable within the same single- or multistep optimization used for all other
network and device decisions.

This milestone has two inseparable parts:

1. Replace formulation-local subtraction of anonymous bus-table `PD` and `QD`
   arrays with a shared `Load` device model that owns fixed active and reactive
   withdrawal.
2. Extend that device model so an explicitly configured subset of loads may
   reduce its withdrawal at a sufficiently large linear value-of-lost-load
   cost.

Load shedding is not a separate generator-like device. It is an optional
feasible-set extension of the load whose service is being interrupted. This
keeps the load input, its time-series identity, its active/reactive physics,
its shedding bound, and its results under one owner.

The milestone must preserve:

- one optimization solve over the complete requested horizon;
- DCP-valid load and shedding expressions in every formulation;
- MATPOWER-style case compatibility;
- existing positional `df_P` and `df_Q` compatibility;
- the shared M16+ component-assembly boundary;
- stable objective scaling by `delta`; and
- explicit absence semantics: no load is sheddable unless the user configures
  it to be sheddable.

## 2. Scientific model

### 2.1 Exogenous load

Load $i$, connected to bus $k(i)$, has active and optional reactive demand
at step $t$:

$$ P^{\mathrm{load}}_{t,i}, \qquad Q^{\mathrm{load}}_{t,i}. $$

The active and reactive values are exogenous problem data, not optimization
variables. Static values supply a single-step model and multistep fallback;
aligned time-series channels may replace them at each step. Positive
$P^{\mathrm{load}}$ is ordinary withdrawal. A negative value is preserved as
signed net injection for MATPOWER compatibility, remains fixed at that slice,
and is never eligible for shedding.

A fixed load contributes signed nodal injections

$$ p^{\mathrm{inj}}_{t,i}=-P^{\mathrm{load}}_{t,i}, \qquad q^{\mathrm{inj}}_{t,i}=-Q^{\mathrm{load}}_{t,i}. $$

Bus shunts remain part of the network model. They are not `Load` devices and
are not sheddable under M19.

### 2.2 Sheddable load

Define the active demand eligible for shedding:

$$ P^{\mathrm{eligible}}_{t,i} = \max(P^{\mathrm{load}}_{t,i},0). $$

Define the exogenous eligibility mask

$$ m_{t,i} = \mathbf 1 \left\{ P^{\mathrm{load}}_{t,i}>0 \right\}. $$

For a sheddable load, introduce an interruption fraction
$\alpha_{t,i}$:

$$ 0 \leq \alpha_{t,i} \leq \rho_i m_{t,i}, \qquad 0 < \rho_i \leq 1. $$

The eligibility inequality is always present. When active demand is zero or
negative, $m_{t,i}=0$ and the same constraint forces
$\alpha_{t,i}=0$. The optimization graph therefore does not add or remove
constraints when a load trajectory crosses zero. Both
$P^{\mathrm{eligible}}$ and $m$ are prepared exogenous data and can later
be bound through CVXPY parameters for repeated M17 solves.

The active powers shed and served are

$$ P^{\mathrm{shed}}_{t,i} = \alpha_{t,i}P^{\mathrm{eligible}}_{t,i}, \qquad P^{\mathrm{served}}_{t,i} = P^{\mathrm{load}}_{t,i}-P^{\mathrm{shed}}_{t,i}. $$

When reactive demand is defined, the same fraction applies:

$$ Q^{\mathrm{shed}}_{t,i} = \alpha_{t,i}Q^{\mathrm{load}}_{t,i}, \qquad Q^{\mathrm{served}}_{t,i} = (1-\alpha_{t,i})Q^{\mathrm{load}}_{t,i}. $$

The load therefore contributes

$$ p^{\mathrm{inj}}_{t,i} = -P^{\mathrm{served}}_{t,i}, \qquad q^{\mathrm{inj}}_{t,i} = -Q^{\mathrm{served}}_{t,i}. $$

Using one fraction for both channels:

- preserves the load's signed instantaneous power factor;
- avoids division by active load;
- handles leading as well as lagging reactive demand;
- keeps the feasible set affine; and
- ties every shedding decision to the associated load value at that problem
  slice.

If active load is zero or negative at a step, shedding is constrained to zero
at that step. A reactive-only load is fixed in the M19 model; otherwise
reactive demand could be removed at zero active-energy cost.

The continuous fraction represents interval-average curtailed power under the
piecewise-constant time model. It is energy-equivalent to some duration-based
interruptions, but not generally equivalent in network physics, generation
cost, storage operation, ramping, or chronology. Any duration-fraction
interpretation therefore requires additional aggregation assumptions outside
the optimization. M19 does not model within-interval switching chronology.

### 2.3 Linear value-of-lost-load cost

The shedding stage-cost rate is

$$ J^{\mathrm{shed}}_t = \sum_{i\in\mathcal S} \nu_i P^{\mathrm{shed}}_{t,i}, $$

where $\nu_i$ has units of objective units per MWh and
$\mathcal S$ is the set of sheddable loads. Shared objective assembly
integrates this rate:

$$ J^{\mathrm{shed}} = \sum_t \Delta t J^{\mathrm{shed}}_t. $$

The coefficient is finite, positive, and chosen to make load shedding the most
expensive modeled marginal source of nodal-balance relief. Renewable
curtailment remains a zero-cost metric of interest. Load shedding is an
explicitly penalized reliability outcome.

M19 uses one optimization problem. It does not introduce:

- a lexicographic objective;
- a second feasibility-restoration solve;
- an anonymous emergency slack;
- a Pareto-front sweep; or
- post-solve load curtailment.

### 2.4 Exact-penalty phase transition

The controlled phase-transition result assumes:

- continuous dispatch;
- convex, nondecreasing generation cost;
- feasible full service;
- no minimum-output condition that forces overgeneration;
- no storage, network, terminal, or other intertemporal marginal value; and
- one common shedding coefficient, or an explicitly ordered heterogeneous
  service policy.

Let $V(D)$ be the optimal dispatch value as a function of served demand over
the feasible demand set $\mathcal D$. A sufficient condition is

$$ \nu > \sup \left\{ g \mid D\in\mathcal D, \quad g\in\partial V(D) \right\}. $$

Here the right-hand side denotes the largest supported subgradient of the
convex dispatch value function. Under the stated convex monotone assumptions,
the largest supported generator segment slope is a convenient conservative
bound. For one increasing convex quadratic generator, this is its marginal
cost at $P_{\max}$. For convex piecewise-linear generation, it is the
largest active segment slope.

Above this threshold, serving another feasible MWh is always cheaper than
shedding it. The served-load solution is invariant to further increases in
$\nu$; there is no unresolved generation-versus-service Pareto front.

M19 will test the result under those assumptions. It will not claim that one
raw generator cost curve or the generator-only bound is automatically
sufficient for every networked, multistep problem. Congestion, storage
opportunity value, terminal penalties, and heterogeneous shedding costs can
change the relevant marginal comparison. The public coefficient remains
explicit because the package cannot infer a universal sufficiently large value
across arbitrary objective units and device costs.

## 3. Proposed public device and data model

### 3.1 Load

Approved public surface:

```python
Load(
    bus=5,
    p_load_mw=90.0,
    q_load_mvar=30.0,
    device_id="load_5",
    shedding_cost_per_mwh=None,
    max_shed_fraction=1.0,
)
```

Proposed field semantics:

- `bus`: external MATPOWER bus identifier.
- `p_load_mw`: finite signed static net demand and multistep fallback.
  Positive values are withdrawal; negative values are fixed net injection and
  have zero shedding eligibility.
- `q_load_mvar`: optional signed static reactive load and multistep fallback.
  `None` means no static reactive fallback. An aligned `df_load_q` trajectory
  may still define the reactive channel; if neither exists, reactive demand is
  zero and recorded as undefined in metadata.
- `device_id`: required, unique, nonempty external identity for static results
  and time-series alignment. Imported loads receive deterministic IDs derived
  from external bus identity.
- `shedding_cost_per_mwh`: `None` for a fixed load; a finite positive value
  makes the load sheddable.
- `max_shed_fraction`: maximum interruptible fraction, with
  $0 < \rho_i \leq 1$. It has no effect when
  `shedding_cost_per_mwh is None`.

Using `None` as the fixed-load policy keeps absence explicit. A zero or
negative shedding cost is invalid rather than a hidden alternate mode.

### 3.2 Public build inputs

The multistep signature must make the legacy positional inputs optional so the
explicit-load mode is callable:

```python
build_opf(
    case,
    ...,
    loads=[...],
)

build_opf_multistep(
    case,
    df_P=None,
    df_Q=None,
    *,
    T,
    loads=None,
    df_load_p=None,
    df_load_q=None,
    ...,
)
```

There are exactly two valid multistep input modes:

1. **Imported-load mode:** `loads is None`. Legacy `df_P` and `df_Q` are
   required and retain their current positional bus-table meaning.
2. **Explicit-load mode:** `loads` is supplied. Legacy `df_P` and `df_Q` must
   be `None`. Identity-aligned `df_load_p` and `df_load_q` may override the
   static values; omitted channels use their static fallbacks.

An explicit empty `loads=[]` selects explicit-load mode and therefore models
zero ordinary loads. It does not fall back to the case.

The semantic contract is:

- every explicit load has a stable input channel;
- every time-series value aligns by `device_id`;
- an optional reactive channel aligns to the same load identity;
- shedding bounds use that load's value at the current step; and
- positional bus-load tables are accepted only through an explicit legacy
  compatibility path.

Public validation selects the mode before dispatching to any private
formulation builder. All three private builders receive the same normalized
load collection and aligned data. Type annotations and docstrings must
distinguish the now-optional legacy tables without changing the behavior of
existing positional calls.

### 3.3 MATPOWER and legacy compatibility

When `loads=None`, the builder converts MATPOWER bus `PD` and `QD` data into
internal `Load` objects before component preparation. The formulation-local
network equations must not subtract the original arrays again.

For multistep compatibility, existing positional `df_P` and `df_Q` columns
remain associated with MATPOWER bus-table order. The importer maps those bus
channels onto the corresponding internal loads.

The compatibility converter should retain one internal load channel per bus,
including zero-valued static loads. This gives existing `(T, nb)` time-series
inputs a deterministic one-to-one mapping and permits a bus with zero static
load to receive positive time-series load later.

When explicit `loads` are supplied:

- they replace the MATPOWER `PD` and `QD` load model;
- they are never added on top of the anonymous bus loads;
- their time series align by `device_id`; and
- non-`None` legacy positional load tables are rejected.

The imported and explicit paths must produce the same prepared representation
before shared component assembly.

Negative active input is preserved in that representation as signed fixed net
demand. Its prepared shedding-eligible value is
`max(p_load_mw, 0)`. This is an explicit model rule, not a validation bypass in
the MATPOWER importer. Input metadata and results retain the signed value so
users can distinguish withdrawal from imported net injection.

### 3.4 Marginal-cost calibration helper

Provide a narrow public analysis helper:

```python
bound = max_generation_marginal_cost(generators)
voll = max(user_positive_floor, 3.0 * bound)
```

`max_generation_marginal_cost(...)` returns a plain `float` so common
two-to-five-times-bound calibration remains concise. It computes the largest
supported dispatchable-generator marginal cost:

- zero for a constant cost;
- the linear coefficient for a linear cost;
- $2c_2P_{\max}+c_1$ for a convex nondecreasing quadratic;
- the largest supported segment slope for a convex PWL cost; and
- the maximum of those values across the supplied fleet.

The helper validates that the supplied cost models satisfy the assumptions
needed for that calculation. An empty fleet or an unsupported/nonconvex cost
model raises a clear error rather than returning a misleading bound.

A constant-cost fleet correctly produces a bound of zero. Because
`shedding_cost_per_mwh` must be finite and positive, callers then supply any
positive coefficient consistent with their objective units. The helper does
not invent a universal policy floor; the concise general pattern is
`max(user_positive_floor, multiplier * bound)`.

This is a generator-only diagnostic, not an automatic VOLL selector or a
system-wide sufficiency certificate. It does not inspect congestion, storage,
terminal policies, or other intertemporal marginal values. The helper never:

- modifies a `Load`;
- applies a hidden safety factor;
- emits a policy warning based only on generator costs; or
- rejects a user-selected shedding coefficient.

If provenance is useful for examples or debugging, an internal companion may
retain the binding generator, cost type, and evaluation point. The common
public path remains a directly multiplicable scalar.

## 4. Component ownership and architecture

`load.py` owns:

- the `Load` data class;
- field and collection validation;
- external bus validation and incidence construction;
- static load vectorization;
- active/reactive time-series alignment;
- fixed and served-with-shedding network injections;
- shedding variable specifications;
- shedding operating constraints;
- per-step value-of-lost-load cost;
- load and shedding metadata;
- per-step served/shed reporting expressions; and
- horizon energy-not-served expressions.

The load adapter owns the translation between `Load` methods and the private
M16+ component contract.

`generator.py` owns `max_generation_marginal_cost(...)`, since the calculation
depends only on dispatchable-generator feasible ranges and cost models. The
load model consumes only the user-selected `shedding_cost_per_mwh`; it does not
import or call the diagnostic.

### 4.1 Atomic prepared-load updates

Signed active load, eligible active demand, and the eligibility mask encode one
invariant:

$$ P^{\mathrm{eligible}} = \max(P^{\mathrm{load}},0), \qquad m = \mathbf 1 \left\{ P^{\mathrm{load}}>0 \right\}. $$

They must never be exposed as three independently mutable parameter channels.
The prepared load object or adapter owns one atomic update operation that:

1. accepts and validates the signed active-load trajectory;
2. derives eligible demand and the mask from that trajectory;
3. checks shapes and finite values for all derived arrays; and
4. assigns all associated CVXPY parameter values as one logical operation
   before the next solve.

No public or private caller may directly update the eligible-demand or mask
parameters. The implementation should calculate and validate all new arrays
before assigning any parameter, so a failed update cannot leave a partially
updated model.

Public parsers own:

- MATPOWER-to-`Load` conversion;
- legacy `df_P`/`df_Q` compatibility;
- selection of static versus time-series input data; and
- passing aligned exogenous load values into the component request.

Formulation builders continue to own:

- AC, lossy-DC, or single-node network equations;
- formulation-owned network variables and constraints; and
- balance equations using generic aggregate component injections.

After migration, formulation builders must not independently subtract `Pd`,
`Qd`, or `Pd_total`. That arithmetic belongs to the load contribution.

### 4.2 M16+ extension proof

M19 is the first repository-supported, non-toy device added after M16+. It must
demonstrate the promised boundary:

```text
Load model and adapter
        ↓
one centralized component registration
        ↓
generic variables, injections, constraints, costs, metadata, and expressions
        ↓
all formulation builders consume aggregates without load-specific assembly
```

Ordinary public API and parser plumbing is expected. Component-specific
mathematical composition inside each formulation builder is not.

### 4.3 Scaling ownership

Load data enters the device model in MW and MVAr. As with other engineering-unit
devices, the load injection hook creates the established scalar
`inv_base_mva` `cp.Parameter`. Shared assembly binds it to `1 / baseMVA`.

The load adapter must not pre-divide engineering-unit input values and then
apply the parameter again.

## 5. Formulation contracts

### 5.1 AC

- Fixed or served active and reactive withdrawal enters the corresponding
  nodal balances.
- Shedding uses one fraction for both channels.
- Negative reactive demand remains signed and is relieved proportionally.
- Voltage-dependent load models are not introduced.
- Bus shunts remain network-owned and nonsheddable.

### 5.2 Lossy DC

- Only active served load enters nodal balance.
- Explicit `df_load_q` is accepted for cross-formulation input portability.
  Reactive load data is retained as known input metadata but ignored by the
  network formulation.
- Supplying `df_load_q` emits a warning that reactive input was retained as
  metadata but is not used in lossy-DC optimization.
- The same active shedding variable, bound, and cost are used as in AC.
- The lossy-DC `rp^2` objective term remains a network-flow penalty; it does
  not withdraw physical losses from nodal balance.

### 5.3 Single-node DC

- Device-level active served loads aggregate into the copper-plate balance.
- Load and shedding results retain device identity and original bus metadata
  even though network location is collapsed.
- Reactive channels are not modeled.
- Explicit `df_load_q` is accepted and retained as known input metadata.
  Supplying it emits a warning that the data is not used in single-node-DC
  optimization.
- Nodal congestion and location claims are not made from this formulation.

## 6. Result and expression contract

Lock the following public result names before shedding implementation:

| Result key | Meaning | Units / shape |
|---|---|---|
| `p_load` | signed exogenous active-load input | MW; `(nload,)` or `(T, nload)` |
| `q_load` | signed exogenous reactive-load input, with zero values where undefined | MVAr; `(nload,)` or `(T, nload)` |
| `p_load_served` | signed active net demand remaining after shedding | MW; `(nload,)` or `(T, nload)` |
| `q_load_served` | signed reactive withdrawal remaining after shedding; AC only | MVAr; `(nload,)` or `(T, nload)` |
| `p_load_shed` | nonnegative active load shed | MW; `(nsheddable,)` or `(T, nsheddable)` |
| `q_load_shed` | signed reactive load shed; AC only | MVAr; `(nsheddable,)` or `(T, nsheddable)` |
| `load_shed_fraction` | interruption fraction | dimensionless; `(nsheddable,)` or `(T, nsheddable)` |
| `p_load_shed_total` | aggregate active load shed by interval | MW; scalar or `(T,)` |
| `energy_not_served_by_load` | horizon active energy not served for each sheddable load | MWh; `(nsheddable,)` |
| `energy_not_served` | sum across sheddable loads | MWh; scalar |
| `load_shedding_cost` | integrated horizon shedding cost | objective units; scalar |

Lock the corresponding `OPFBuild.expressions` names:

- per-step: `p_load`, `q_load`, `p_load_served`, `q_load_served`,
  `p_load_shed`, `q_load_shed`, `load_shed_fraction`, and
  `p_load_shed_total`;
- integrated stage cost: `load_shedding_cost`; and
- once-per-horizon: `energy_not_served_by_load` and `energy_not_served`.

Formulations that do not model reactive power retain `q_load` as known input
data but do not publish `q_load_served` or `q_load_shed` as modeled results.

Load metadata keys are:

- `nload`, `nsheddable`, and `Cload`;
- `load_device_ids`;
- `load_bus_external` and `load_bus_internal`;
- `load_has_reactive`;
- `load_is_sheddable`;
- `sheddable_load_indices`;
- `sheddable_load_device_ids`;
- `load_max_shed_fraction`; and
- `load_shedding_cost_per_mwh`.

For single-step builds, interval arrays use the package's established
device-vector shape. For multistep builds, they use `(T, nload)` or
`(T, nsheddable)` as appropriate. Energy not served and integrated shedding
cost are scalar horizon expressions:

$$ E^{\mathrm{NS}}_i = \sum_t \Delta t P^{\mathrm{shed}}_{t,i}. $$

$$ E^{\mathrm{NS}} = \sum_{i\in\mathcal S} E^{\mathrm{NS}}_i. $$

The unsuccessful-result and conditional-schema rule is exact:

- Load input, identity, location, reactive-channel, and policy metadata are
  always present. This includes `nsheddable`, `load_is_sheddable`,
  `sheddable_load_indices`, `sheddable_load_device_ids`, maximum fractions,
  and shedding-cost coefficients even when `nsheddable == 0`.
- `p_load` and `q_load` are exogenous problem data and remain populated after
  an unsuccessful solve.
- When `nsheddable == 0`, `p_load_served` and AC `q_load_served` are known
  constants equal to the corresponding inputs and remain populated without a
  primal solution.
- When `nsheddable > 0`, served-load arrays depend on
  `load_shed_fraction`. Without a usable primal, each complete served-load
  array is `None`; extraction does not mix known fixed rows with unavailable
  controllable rows.
- Shedding variables, modeled shedding expressions, and numerical shedding
  results are present only when `nsheddable > 0`. Without a usable primal,
  those numerical results are `None`.
- `energy_not_served_by_load`, `energy_not_served`, and
  `load_shedding_cost` follow the same conditional and no-primal rule.

The result schema never infers shedding from balance residuals.

## 7. Validation contract

Validate:

- finite numeric static active and reactive load values;
- required, unique, nonempty `device_id` values for every explicit load;
- deterministic unique IDs for imported loads;
- valid external bus IDs;
- finite positive shedding cost when configured;
- $0 < \texttt{max_shed_fraction}\leq 1$;
- unique load-channel ownership;
- aligned active/reactive time-series identities and shapes;
- finite time-series values;
- optional reactive trajectories when `q_load_mvar is None`;
- nonnegative shedding eligibility computed as `max(p_load, 0)`;
- zero shedding whenever active load is zero or negative; and
- no duplicate explicit load identity.

Negative active values represent fixed net injection rather than ordinary
load. They remain in signed load input and served-power accounting but have
zero shedding eligibility. A trajectory that crosses zero changes eligibility
step by step without changing device identity.

## 8. Implementation stages

Each stage receives a clean review and commit boundary.

### Stage 0 — Characterization and frozen compatibility baseline

**Status:** complete — checkpoint commit `f8f01a9`

- Characterize current static and multistep `Pd`, `Qd`, and `Pd_total` data.
- Lock single- and multistep variable, data, expression, and result schemas
  across all three formulations.
- Record numerical objectives and active/reactive balances for a
  representative MATPOWER case across all three formulations.
- Include zero static load with positive future time-series load.
- Include negative reactive demand.
- Characterize unsuccessful solves and `delta != 1`.
- Record current positional `df_P`/`df_Q` behavior.

No production load behavior changes in this stage.

As-built evidence is in `tests/test_load_characterization.py`:

- exact pre-M19 load-related build and result schemas;
- AC, lossy-DC, and single-node numerical baselines in single-step and
  intentional multistep modes;
- `delta=0.5` objective totals;
- active and reactive balance reconstruction from internal per-unit variables;
- static negative active demand and a positional positive-to-zero-to-negative
  multistep trajectory, including nodal signs and single-node aggregation;
- negative reactive demand in the solved AC fixture;
- positional legacy DataFrame columns;
- zero static load with positive time-series demand;
- intentional multistep `T=1` schemas and objective equivalence;
- unsuccessful-result behavior; and
- explicit absence of all future first-class-load and shedding keys.

Verification at the S0 stopping point:

- 25 focused characterization tests passed;
- 1,501 full-suite tests passed;
- Ruff passed;
- mypy passed; and
- `git diff --check` passed.

### Stage 1 — First-class fixed-load model

**Status:** complete — checkpoint commit `481643c`

- Add the complete approved `Load` dataclass, including the future shedding
  policy fields, and stabilize its validation now.
- At the S1 preparation/adapter boundary, raise a clear temporary
  `NotImplementedError` whenever `shedding_cost_per_mwh is not None`; do not
  place this guard in `Load` construction and do not silently treat a
  configured sheddable load as fixed.
- Add static preparation, incidence, and engineering-unit injections.
- Add fixed-load adapter capabilities for AC, lossy DC, and single-node DC.
- Use prepared load data as the source of truth for metadata and constant
  reporting expressions.
- Publish `p_load`, `q_load`, and `p_load_served` in every formulation; publish
  `q_load_served` only in AC.
- Preserve fixed served-load values during unsuccessful extraction without
  requiring a primal solution.
- Distinguish undefined reactive load (`q_load_mvar=None`, numerical zero,
  `load_has_reactive=False`) from explicitly configured zero reactive load
  (`q_load_mvar=0.0`, numerical zero, `load_has_reactive=True`).
- Keep single-node reporting device-level while aggregating only the network
  injection.
- Support an empty sequence in direct device and adapter functions with
  correctly shaped zero-length outputs; do not change shared empty-request
  assembly behavior in S1.
- Prove DCP conformance per expression and constraint.
- Unit-test active and reactive sign conventions.

As built, `Load` exposes the complete approved public field set, including
the dormant shedding policy. Field-local validation occurs during object
construction; case-relative bus membership and collection-wide identity
uniqueness are validated during preparation. The private adapter rejects a
configured shedding cost with the temporary S1 `NotImplementedError`, so no
approved shedding configuration can be silently interpreted as fixed load.

The adapter remains intentionally absent from the centralized production
registry in S1. Direct adapter assembly proves fixed active and reactive
withdrawal signs, engineering-unit scaling, device-level reporting, empty
device-vector behavior, and empty horizon contributions in AC, lossy DC, and
single-node DC. Consequently, S1 adds the device contract without changing
existing builder algebra, public build inputs, or legacy result schemas.

Verification at the S1 stopping point:

- 23 focused fixed-load tests passed;
- 1,524 full-suite tests passed;
- Ruff passed;
- strict mypy passed with `load.py` added to the checked surface; and
- `git diff --check` passed.

### Stage 2 — Complete legacy conversion and shared assembly migration

**Status:** complete — checkpoint commit `29202d3`

- Convert case bus `PD`/`QD` into internal loads.
- Convert legacy positional multistep `df_P`/`df_Q` into the same prepared
  load representation.
- Register loads once in the centralized component registry.
- Remove formulation-local load subtraction.
- Preserve exact numerical balance and objective behavior.
- Preserve external and internal bus identity.
- Pass single- and multistep numerical-equivalence gates in AC, lossy DC, and
  single-node DC before the checkpoint commit.
- Prove the formulation builders contain no load-specific mathematical
  assembly after parser preparation.

The conversion and removal of `Pd`, `Qd`, and `Pd_total` subtraction are one
atomic change. No checkpoint may leave multistep builders without a valid load
injection source.

As built, the MATPOWER compatibility converter creates one fixed `Load` per
original bus row, including zero-demand rows, with deterministic identity
`load_bus_<external_id>`. Static and legacy positional multistep channels are
normalized in engineering units before component preparation. Existing
per-unit `Pd`, `Qd`, `Pd_total`, and `Pd_series` fields remain available as
compatibility metadata but no longer participate independently in balance
construction.

All formulation balance helpers now receive only the generic aggregate
component injection. The common registry contains the load adapter once, and
AC, lossy DC, and single-node DC publish fixed load expressions through the
shared expression channel. For backward compatibility, lossy and single-node
DC continue accepting legacy `df_Q=None`; that absence is represented by a
zero reactive reporting trajectory and remains absent from optimization.

Verification at the S2 stopping point:

- pre-M19 objectives and dispatch baselines are unchanged within their frozen
  solver tolerances in all three formulations and both horizon modes;
- signed active and reactive channels, nonconsecutive external identities,
  positional multistep alignment, and intentional multistep `T=1` pass;
- static architecture gates prove formulation balance helpers contain no
  independent `Pd`, `Qd`, or `Pd_total` arithmetic;
- 15 focused S2 migration tests were added;
- 1,539 full-suite tests passed;
- Ruff and strict mypy passed; and
- `git diff --check` passed.

### Stage 3 — Identity-aligned explicit-load time series

**Status:** complete — checkpoint commit `80221b0`

- Make legacy `df_P` and `df_Q` optional in the public callable signature.
- Implement and validate the two input modes defined in Section 3.2.
- Add the approved identity-aligned time-series surface for explicit loads.
- Retain static fallback behavior.
- Permit `df_load_q` when `q_load_mvar is None`.
- In explicit-load lossy-DC and single-node-DC modes, accept `df_load_q`,
  retain it as input metadata, and emit the formulation-specific warning from
  Section 5; do not reject or silently optimize with it.
- Validate finite values, identity sets, order independence, and `T`.
- Reject mixed explicit and legacy inputs before private-builder dispatch.
- Update typing and all three private builder surfaces.
- Verify multistep `T=1` remains intentionally multistep.
- Distinguish all three public states: `loads=None` selects legacy MATPOWER
  fallback; `loads=[]` selects explicit zero-load mode with complete empty-load
  metadata; and `loads=[...]` selects explicit first-class loads.
- Require an explicitly selected component family to participate in
  preparation and metadata publication even when its collection is empty.
  Choose the narrow shared-assembly mechanism only after testing whether the
  ordinary explicit-load request can satisfy that requirement.

As built, the public boundary selects exactly one load-input mode before any
formulation builder is invoked. ``loads=None`` retains the positional
MATPOWER-compatible path; supplying a load sequence, including ``loads=[]``,
selects identity-aligned explicit-load mode and rejects legacy ``df_P`` and
``df_Q``. Missing explicit trajectories fall back to tiled static device
values, while supplied frames are reordered by unique ``Load.device_id`` and
validated for exact identity, finite values, and horizon length.

One narrow private assembly flag, ``ComponentRequest.participates_when_empty``,
allows an explicitly selected empty component family to run preparation and
publish its complete zero-length schema. It does not change the default rule
that absent empty collections are skipped. Compatibility ``Pd``, ``Qd``,
``Pd_total``, and multistep load-series metadata are derived from the selected
first-class load inputs, so explicit loads replace rather than supplement the
MATPOWER bus demand. Explicit reactive trajectories are portable across all
formulations; the DC formulations retain and report them with a warning but do
not include them in optimization.

Verification at the S3 stopping point:

- all three public load states (fallback, explicit nonempty, and explicit
  empty) are distinguished and retain their documented schemas;
- identity reordering, static fallback, a time-varying reactive channel with
  no static fallback, finite-value and horizon validation, and mixed-mode
  rejection pass;
- multistep ``T=1`` retains its time dimension and matches the corresponding
  single-step objective in all three formulations;
- 19 focused S3 API tests were added;
- 1,558 full-suite tests passed;
- Ruff and strict mypy passed; and
- ``git diff --check`` passed.

### Stage 4 — Activate the sheddable-load feasible set and cost

**Complete; checkpoint commit `79d0e8c`.**

**Scaling decision:** retain the established builder-bound
``inv_base_mva`` parameter contract in M19. The S4 spike demonstrated that an
immutable build-time base scalar makes the convex load-shedding path DPP and
improves repeated small-case solves, but a load-only exception would create an
inconsistent cross-device scaling boundary. Graph identity is preserved;
CVXPY canonicalization is not cached through DPP under the retained
parameter-product representation. A DPP-oriented change is deferred to a
separate review of all engineering-unit devices.

- Remove the temporary S1 adapter guard and activate the already-defined
  shedding policy fields.
- Create builder-owned shedding variables through `VariableSpec`.
- Add served/shed active and reactive expressions.
- Prepare an always-present exogenous eligibility mask $m_{t,i}$ and
  nonnegative eligible demand $P^{\mathrm{eligible}}_{t,i}$.
- Implement the single atomic prepared-load update operation from Section 4.1;
  do not expose independent eligible-demand or mask mutation paths.
- Before selecting a representation, compare:
  - leaf bounds $0\leq\alpha\leq\rho$ plus the explicit eligibility
    inequality $\alpha\leq\rho m$; and
  - fully explicit lower and upper eligibility inequalities
    $0\leq\alpha\leq\rho m$.
- Record Python construction, canonicalization/setup, solve time, solver
  iterations, variable counts, equality counts, and inequality counts for
  representative single- and multistep AC problems.
- Verify the two representations are mathematically and numerically equivalent
  in lossy DC and single-node DC.
- Bind load, eligible-demand, and eligibility-mask data through CVXPY
  parameters in the spike, update trajectories across positive, zero, and
  negative active demand, and verify the same graph is reused without changing
  variables or constraints.
- Before each re-solve, assert that signed load, eligible demand, and mask all
  hold the newly derived values. Include a deliberately invalid update and
  verify that no parameter is partially changed.
- Select and document one representation, then use it uniformly in
  single- and multistep builds.
- Exercise zero and negative active-load slices where the always-present
  eligibility inequality forces $\alpha=0$.
- Add the chosen device-owned bounds and linear stage-cost rate.
- Verify shared `delta` integration exactly once.
- Publish per-step and horizon expressions collision-safely.
- Confirm fixed loads add no optimization variables.
- Add and test the scalar `max_generation_marginal_cost(...)` analysis helper
  without coupling it to build-time validation or automatic policy selection.

**As built:** only configured sheddable loads receive one builder-owned
interruption-fraction variable per step. Production uses the fully explicit
affine inequalities

$$
\alpha_{t,i} \geq 0,
\qquad
\alpha_{t,i} \leq \rho_i m_{t,i}.
$$

Signed active demand, eligible active demand, and the eligibility mask are
held by one private synchronized parameter controller. Its atomic update
derives the latter two channels from signed demand before assigning any
parameter. Active shedding is

$$
P^{\mathrm{shed}}_{t,i}
=
\alpha_{t,i}\max(P^{\mathrm{load}}_{t,i},0).
$$

AC reactive relief uses the same fraction and preserves the sign of the
configured reactive channel.
Reactive-only, zero-active, and negative-active loads therefore remain fixed.

The load adapter publishes the integrated stage cost as
`load_shedding_cost`; `load_cost` is not part of the schema. It publishes
device-level and aggregate ENS exactly once through the horizon hook. The
public `max_generation_marginal_cost(...)` helper is a generator-only
diagnostic in cost-per-energy units. It neither selects VOLL nor introduces a
load-to-generator build dependency.

The representation spike and its reproducible artifacts are recorded in
`experiments/load_shedding_s4/`. Explicit inequalities and leaf bounds were
numerically equivalent in AC, lossy DC, and single-node DC, with no meaningful
leaf-bound performance advantage. Graph identities survive atomic trajectory
updates, but the retained cross-device `inv_base_mva` parameter product is not
DPP; M19 therefore makes no cached-fast-resolve claim.

Verification at the S4 stopping point:

- positive, zero, negative, leading/lagging reactive, reactive-only, mixed
  fixed/sheddable, and fractional-cap behavior pass;
- stage cost and per-device/aggregate ENS are integrated by `delta` exactly
  once, and fixed-load builds remain variable-free;
- the cost-name override, default, absence, and collision paths pass generic
  component-assembly tests;
- the marginal-cost helper covers active feasible polynomial and PWL
  intervals, constant cost, inactive generators, and unsupported assumptions;
- 437 focused characterization, API, component-contract, result-schema, and
  AC DNLP tests passed;
- the complete suite passed with 1,587 tests;
- Ruff, configured strict mypy, and `git diff --check` passed.

The present hook contract reconstructs algebraically identical load
served/shed expressions for injection, cost, and reporting. All copies share
the same variables and synchronized parameters, and numerical reconstruction
is tested. Avoiding those duplicate expression nodes would require a shared
per-step derived-expression channel in the M16+ contract; it is recorded as
contract-level technical debt rather than addressed with an S4 cache or
device-specific workaround. As an explicit maintenance guardrail, any future
edit to one of these reconstructed formulas must update the corresponding
injection, cost, and reporting paths together and preserve the cross-hook
reconstruction tests. With that guardrail, the duplication is accepted narrow
technical debt rather than unfinished S4 work.

### Stage 5 — Results and unsuccessful-solve behavior

**Complete; checkpoint commit `3ac344a`.**

- Implement the exact result and expression names locked in Section 6.
- Extract input, served, and shed quantities.
- Add aggregate shed power, energy not served, and cost.
- Preserve conditional formulation schemas.
- Retain exogenous input arrays and metadata after unsuccessful solves.
- Verify partial-primal and no-primal behavior.
- Verify engineering units and single-/multistep shapes.

**As built:** result extraction is conditional on the built load model, not
solver status. Exogenous `p_load` and `q_load` values are read from their
parameter or constant expressions and remain populated without a primal
solution. Fixed-load served values remain available as known constants. When
`nsheddable > 0`, served, shed, fraction, per-interval total, and ENS values
are extracted only from complete expression values; a missing value at any
step makes the corresponding complete array unavailable rather than mixing
known and unknown rows or intervals.

The conditional schema exactly follows Section 6. AC publishes signed
reactive served and shed quantities; lossy DC and single-node DC retain only
the exogenous reactive input. `energy_not_served_by_load` is a horizon MWh
vector, `energy_not_served` is its scalar sum, and `load_shedding_cost` is the
integrated scalar objective contribution. Without a usable shedding primal,
the two ENS results are `None` and the scalar shedding cost is `NaN`, matching
the package-wide unavailable-cost policy. Load identity, location, channel,
and shedding-policy metadata remains available in `OPFBuild.data` and is not
duplicated into the numerical result dictionary.

Verification at the S5 stopping point:

- successful single- and multistep extraction passes for AC, lossy DC, and
  single-node DC, including exact shapes and engineering-unit reconstruction;
- fixed and sheddable alignment, signed reactive reconstruction, per-interval
  totals, per-load/aggregate ENS, and heterogeneous integrated cost pass;
- unsolved builds preserve exogenous inputs and the complete metadata schema
  while returning the locked conditional unavailable values;
- a deliberately partial multistep shedding primal cannot publish incomplete
  served, shed, fraction, cost, or ENS results;
- 396 focused result, load, characterization, migration, component-contract,
  and formulation tests passed;
- solver-certified infeasibility in both convex formulations retains load
  inputs while withholding all unavailable served, shed, fraction, cost, and
  ENS values;
- the complete suite passed with 1,602 tests;
- Ruff, configured strict mypy, and `git diff --check` passed.

### Stage 6 — Scientific and formulation verification

**Complete; checkpoint commit pending.**

- Prove the single-node phase transition numerically.
- Demonstrate below-threshold economic shedding.
- Demonstrate invariance above the sufficient threshold.
- Demonstrate adequacy-limited shedding when full service is impossible.
- Exercise AC active/reactive relief.
- Exercise lossy-DC congestion and nodal location.
- Exercise single-node aggregation.
- Exercise storage, nondispatchable generation, and terminal policies over
  multiple intervals.

**As-built scientific evidence:** the controlled single-node quadratic case
uses one generator with

$$
C(P)=2P+0.05P^2,
\qquad
C'(P)=2+0.1P,
$$

an 80 MW feasible load, and a generator-only maximum marginal-cost bound of
12 objective units/MWh. At a shedding coefficient of 9, the optimum is the
analytic interior point: 70 MW generated and 10 MW not served. At 12.1, the
load is fully served. Coefficients of 24 and 60—two and five times the
diagnostic bound—leave dispatch, service, and operating objective invariant
within solver tolerance. The test states and enforces the theorem's controlled
assumptions; it does not promote this generator-only diagnostic to a universal
network or intertemporal certificate.

A separate two-generator PWL case verifies the fleet value-function
interpretation. Its supported slopes are 5 and 10 objective units/MWh. At a
shedding coefficient of 9, the 50 MW low-cost segment is filled and 30 MW is
not served rather than using the second segment. At 10.1, all 80 MW is served;
a coefficient of 30 leaves that result invariant. A supply-adequacy case
separately proves that fixed 100 MW demand is infeasible against 60 MW of
capacity, while the identical explicitly sheddable demand solves with 40 MWh
of reported ENS in a one-hour interval.

Network evidence uses nonzero shedding rather than solve status alone. In AC
case9 with total real generation capped at 250 MW, bus-5 shedding is about
66.7 MW and reactive relief is about 22.2 MVAr, preserving the configured
one-third reactive-to-active ratio and reconstructing both input channels. In
a two-bus 50 MW/MVA transfer case, identical 80 MW load is fully served at the
generator bus. At the remote bus, lossy DC binds at 50 MW and sheds 30 MW;
full AC binds the from-terminal apparent-power limit at 50 MVA and sheds about
30.2 MW. Thus location matters under congestion in both network formulations.

The multistep interaction case contains one fixed and one sheddable load, one
dispatchable generator, one zero-cost nondispatchable source, and one ideal
storage device. Nondispatchable availability is fully used in the first
low-scarcity interval; storage remains at 40 MWh, then discharges 20 MWh in the
second interval. A 20 MWh hard terminal equality is met and the remaining
second-interval shortfall is reported as 20 MWh ENS. Without the terminal
reserve requirement, storage discharges to zero and ENS is zero. Device-level
served-load results sum exactly to the single-node balance in both intervals,
so reduced dispatch is never presented without its reliability outcome.

Verification at the S6 stopping point:

- seven deterministic scientific tests cover the quadratic and PWL phase
  transitions, adequacy restoration, AC active/reactive relief, AC and
  lossy-DC congestion/location, single-node aggregation, and multistep
  storage/nondispatchable/terminal interaction;
- 699 broader load, result, storage, nondispatchable, single-node, AC, and DC
  formulation tests passed;
- the complete suite passed with 1,609 tests;
- Ruff, configured strict mypy, and `git diff --check` passed; and
- no production implementation change was required by S6.

### Stage 7 — Documentation, examples, and roadmap handoff

- Add a first-class-load example.
- Add a single-node phase-transition example.
- Add a networked multistep storage and renewable example with partial
  shedding.
- Document VOLL and energy units.
- Explain zero-cost renewable curtailment versus high-cost load shedding.
- Update M17 to distinguish its no-shedding baseline from later resilience
  extensions.
- Add the M17 anti-concealment reporting gate from Section 10.
- Record the as-built public and internal contracts in this plan.

## 9. Verification matrix

### 9.1 Fixed-load migration

- Static MATPOWER import matches pre-M19 objectives and primals.
- Multistep positional load tables match pre-M19 behavior.
- AC active and reactive balances match exactly at fixed assigned states.
- Lossy-DC active balance matches.
- Single-node aggregate demand matches.
- Sparse and dense AC modes agree.
- Single-step and multistep `T=1` retain their intended schema distinction.

### 9.2 Load identity and data alignment

- Reordered identity-labeled columns produce identical models.
- Missing, duplicate, and extra IDs fail clearly.
- Static fallback and time-varying inputs agree when numerically equal.
- A zero static load may become positive through its time-series channel.
- A `None` static reactive fallback may receive an aligned reactive
  trajectory.
- Explicit reactive trajectories are portable across formulations: AC uses
  them, while both DC formulations retain them as input metadata and emit the
  documented not-modeled warning.
- Signed active input crossing zero is preserved while shedding eligibility
  follows `max(p_load, 0)` step by step.
- Multiple loads may share a bus without losing identity.
- One load channel cannot be defined twice.

### 9.3 Shedding algebra

- Fixed loads create no shedding variables.
- Zero or negative active load forces zero shedding and zero shed fraction.
- Positive-to-zero-to-negative parameter updates reuse the same optimization
  graph and only change exogenous values.
- Every such update changes signed load, eligible demand, and eligibility mask
  together; invalid updates leave all prior parameter values intact.
- `max_shed_fraction` limits interruption exactly.
- Full shedding is possible only when the fraction is one.
- Active and reactive served/shed identities reconstruct the input load.
- Leading and lagging reactive loads retain their signed ratio.
- Reactive-only load remains nonsheddable.
- All load-device expressions and constraints are DCP.
- The chosen bound representation has recorded AC DNLP structural and timing
  evidence.

### 9.4 Cost and time

- Shedding cost is linear and nonnegative.
- `delta != 1` scales stage cost and energy not served once.
- Single-step interval cost uses its supplied `delta`.
- Multistep totals reconstruct from named stage and horizon expressions.
- No lexicographic or second-solve path exists.

### 9.5 Economic phase transition

- Below the dispatchable marginal-cost threshold, economic shedding occurs in
  the controlled single-node example.
- Immediately above the threshold, feasible load is fully served.
- Larger coefficients leave the solution invariant within solver tolerance.
- Two-to-five-times-bound calibration is concise and reproducible using
  `max_generation_marginal_cost(...)`.
- Multiple-generator tests use the dispatch value-function subgradient bound.
- Quadratic and PWL dispatchable costs provide valid conservative segment-slope
  bounds under the theorem assumptions.
- The theorem's assumptions are stated alongside the numerical result.

### 9.6 Adequacy and network behavior

- Shedding restores feasibility when available supply cannot serve all demand.
- Shedding never exceeds contemporaneous load.
- Congestion makes load location relevant in AC and lossy DC.
- AC reactive relief enters with the correct sign.
- Storage may reserve energy for later high-scarcity intervals.
- Lower dispatchable energy is never reported as an improvement without
  reporting energy not served.

### 9.7 Results and schemas

- Successful results expose the documented units and shapes.
- Configured load identities survive all formulations.
- No-primal outcomes retain exogenous load arrays and metadata.
- No-primal outcomes set optimization-dependent served/shed values to `None`.
- With no sheddable loads, served-load constants remain available even without
  a primal solution.
- DC schemas do not claim reactive optimization results.
- `sheddable_load_indices` and `sheddable_load_device_ids` map every shedding
  column explicitly.
- Aggregate energy not served equals the sum of
  `energy_not_served_by_load`.

## 10. Documentation and examples

The user-facing documentation must explain:

- loads are first-class devices rather than anonymous balance constants;
- MATPOWER bus loads are converted automatically;
- explicit load channels align by stable identity;
- only loads configured with a shedding cost are interruptible;
- shedding is optimized simultaneously with generation, storage, and network
  operation;
- the linear VOLL coefficient is chosen sufficiently large, without a
  lexicographic solve;
- partial interval shedding is supported under the piecewise-constant model;
- reactive relief uses the same shed fraction as active demand;
- renewable curtailment remains a zero-cost metric of interest; and
- energy not served is integrated using `delta`.

Examples:

1. **MATPOWER compatibility:** unchanged case solve through automatically
   imported `Load` objects.
2. **Phase transition:** single-node sweep immediately below and above the
   maximum dispatchable marginal cost.
3. **Networked multistep response:** storage, nondispatchable generation, and
   sheddable loads over scarcity intervals, reporting both cost and energy not
   served.

### 10.1 Milestone 17 anti-concealment gate

When an M17 AC corrective window enables sheddable loads, a successful AC solve
must never be summarized merely as “AC feasible.” Every hierarchical result
must separately retain and report:

- DC-planned demand;
- AC input demand;
- AC-served demand;
- corrective active and reactive shedding;
- energy not served; and
- whether the AC window would have been infeasible or target-inconsistent
  without corrective shedding, when that counterfactual is explicitly run.

Corrective shedding is a reliability outcome and evidence of disagreement
between the upper-layer plan and lower-layer executable operation. It must not
silently convert failure of the DC plan into an unqualified hierarchical
success.

## 11. Explicit non-goals

- Lexicographic load-service optimization.
- Two-stage feasibility restoration.
- Anonymous always-on balance slacks.
- Endogenous load recovery or rebound.
- Minimum interruption or restoration duration.
- Within-interval switching chronology.
- Frequency dynamics.
- General demand response or utility maximization.
- Voltage-dependent ZIP or exponential load models.
- Shedding bus shunts.
- Multiple priority blocks inside one `Load`.
- Integer on/off interruption decisions.
- Automatic inference of a universal sufficiently large VOLL coefficient.

## 12. Resolved review decisions

- The public class is `Load`.
- `shedding_cost_per_mwh: float | None` selects fixed versus sheddable
  behavior; no separate Boolean is added.
- The public multistep API has the two input modes defined in Section 3.2.
- Signed negative active demand is preserved but has zero shedding
  eligibility.
- Shedding result keys are conditional on `nsheddable > 0`; load input and
  served-load keys always exist.
- `max_generation_marginal_cost(...)` returns a directly multiplicable scalar
  generation-only reference bound. It does not select or certify VOLL.
- The AC DNLP representation of shedding bounds is selected only after the
  Stage 4 structural experiment.
