# Correctness and API hardening after Milestones 12 and 16

**Status:** complete — Tracks A, B, and C implemented and verified
**Relationship to M16+:** independent except where noted
**Nature of work:** small correctness fixes, one result-contract decision, and
one package-wide scientific-units decision

## 1. Goal

Address the correctness and API issues identified in the scientific and
architecture review of Milestones 12 and 16 without folding unrelated policy
changes into the M16+ refactor.

The work is split into three independently reviewable tracks:

1. finite temporal-input validation;
2. unsuccessful-solve result-schema consistency;
3. objective time-discretization and units.

The single-node HVDC behavior is not a correctness defect. It is an explicit
null model and is specified and tested in the M16+ plan.

## 2. Review-finding traceability

Every moderate-to-low finding from the M12/M16 review has an explicit owner:

| Review finding | Severity | Disposition | Owning work |
|---|---:|---|---|
| Non-finite `delta` accepted | Moderate | Fix and add boundary tests | Hardening Track A |
| Single-node silently drops HVDC | Moderate in review | Confirmed as the correct null model; make the null capability explicit and test it | M16+ §4 and Gate 4 |
| Stage objectives are not time-scaled | Moderate scientific debt | Implemented and verified: all stage-cost rates are integrated by `delta` | Hardening Track C |
| Infeasible results omit configured-device keys | Low–moderate | Adopt and test a stable build-dependent schema | Hardening Track B |
| Component interface is conventional rather than substitutable | Low–moderate architecture debt | Add typed adapters/protocols and a shared assembler | M16+ §§3–6 |
| Single-step builders skip memoryless coupling hooks | Low | Invoke every applicable horizon hook through the common one-element horizon path | M16+ §3.4 and Gate 2 |

No review finding is deferred without either a planned implementation or an
explicitly accepted model decision.

## 3. Track A — finite temporal-input validation

**Status:** implemented and verified on `critical-path`

### Problem

`_validate_temporal_delta` currently rejects zero and negative values when
storage is active, but accepts `NaN` and positive infinity. These values enter
the SoC transition coefficient and create a model with non-finite physical
data.

### Contract

`delta` must be:

- a real scalar;
- finite;
- strictly positive.

This applies to every single- and multistep build. Track C locks the
time-integrated objective convention, so `delta` scales every stage-cost rate
even when no storage or other temporally coupled device is present. The former
behavior in which zero or negative `delta` was ignored without storage is not
retained.

Validation belongs at the public problem boundary before any CVXPY expression
is created. Component coupling methods may defensively validate direct calls,
but the public boundary is authoritative.

### Implementation stages

1. Add characterization tests for `NaN`, positive/negative infinity,
   nonnumeric values, NumPy scalars, and booleans.
2. Implement a narrow finite-positive scalar validator with clear messages.
3. Use it unconditionally from both `build_opf` and
   `build_opf_multistep`.
4. Confirm valid `float`, integer, and NumPy real scalar values preserve stored
   `storage_delta`.

### Acceptance gates

- Invalid `delta` values raise `ValueError` or `TypeError` before builder
  dispatch, regardless of active components.
- No CVXPY problem can be built with a non-finite storage transition
  coefficient through the public API.
- Single- and multistep paths use the same validator.

All Track A acceptance gates are covered by public-entry-point tests,
including non-finite and nonnumeric inputs, booleans, NumPy real scalars,
storage-free builds across all formulations, and explicit pre-dispatch
failure checks.

This track should land before M16+ so the refactor inherits the corrected
boundary.

## 4. Track B — unsuccessful-solve result schema

**Status:** complete — result schemas are initialized from the built model;
unavailable primal and derived arrays use `None`, scalar objective and cost
quantities use `NaN`, and successful result schemas remain unchanged

### Problem

Successful result extraction adds configured device keys, but unsuccessful
extraction returns early with only formulation-core keys. Thus the schema
depends on both model configuration and solver success. Hard terminal policies
make unsuccessful solves an expected, meaningful outcome.

### Decision to lock

Adopt a stable schema based on the built model:

- if a variable or expression exists in `OPFBuild`, its public result key is
  present even when no primal value exists;
- array-valued primal quantities use `None` when unavailable;
- scalar objective/cost quantities use `NaN`;
- configured terminal deviation is `None` when terminal SoC is unavailable,
  rather than manufacturing an array of numerical deviations;
- keys for components absent from the build remain absent;
- soft terminal cost keys remain conditional on a configured soft policy;
- hard-only terminal policy results include terminal deviation but not
  terminal cost.

This preserves conditional component schemas while making them independent of
solver success.

### Recommended structure

Replace formulation-specific early returns with two phases:

1. initialize the result dictionary from formulation and configured-device
   schema;
2. fill values that are available.

Device result helpers must be safe when variables have `None` values.
Derived quantities such as curtailment and HVDC loss must remain `None` unless
all operands exist.

### Implementation stages

1. Write a schema matrix for formulation × component × success state.
2. Add infeasible convex fixtures for storage hard terminal policies and
   another device-independent infeasible case.
3. Refactor result initialization without changing successful result values.
4. Add AC `user_limit`/no-primal characterization where reproducible, without
   asserting that IPOPT certifies infeasibility.
5. Update result documentation and examples of status-first consumption.

### Acceptance gates

- Successful result dictionaries are backward compatible.
- Configured-device keys are the same for optimal and no-primal outcomes.
- Derived results never perform arithmetic on `None`.
- All formulations follow the same missing-value conventions.
- Tests assert exact keys, shapes, `None`, and `NaN` policy.

All gates are covered by formulation × horizon × component schema tests,
reproducible convex infeasibility fixtures, partial-primal derived-value
tests, and AC hard-terminal status characterization.

This track may proceed in parallel with early M16+ adapter work, but its result
changes should land before M16+ deletes old orchestration so equivalence tests
can distinguish intentional schema changes from refactor regressions.

## 5. Track C — objective time discretization and scientific units

**Status:** implemented and verified on `critical-path`

### Pre-implementation problem

Before Track C, storage dynamics multiplied power by `delta`, but multistep
generation, line loss, storage cycling, and HVDC stage costs were summed
without `delta`. Terminal penalties correctly occurred once at the horizon
boundary. Therefore a fixed physical horizon represented at a finer
resolution received more stage-cost weight relative to its terminal cost.

This was documented behavior, not an accidental M12 regression. Track C
resolved it before cross-resolution or hierarchical economic comparisons are
presented as physically invariant.

### Decision study

Before implementation, classify every objective coefficient by units:

| Term | Former expression | Likely physical interpretation |
|---|---|---|
| Generator cost | `g(P_t)` | currency/hour at dispatch `P_t` |
| DC line loss term | `loss_weight * r*p_t^2` | weighted power loss, not inherently currency/hour |
| Storage cycling | `aging_weight * abs(b_t)` | formerly objective units/MW per interval |
| HVDC cost | polynomial in `abs(p_in_t)` | coefficient-dependent, conventionally currency/hour |
| Future load shedding (M19) | `value_of_lost_load * p_shed_t` | currency/hour; integrates to currency |
| Terminal linear | `rho * abs(q_T-target)` | objective units |
| Terminal quadratic | `rho * square(q_T-target)` | objective units |

The study considered whether the package objective should be:

1. a discretized integral over physical time, in which all stage rates are
   multiplied by `delta`; or
2. a sum of per-interval costs, in which user coefficients already incorporate
   interval duration.

### Approved units and compatibility decision (2026-07-28)

Adopt the discretized-integral convention:

```text
total objective
    = delta * sum_t stage_rate_t
    + horizon_boundary_terms.
```

This was implemented as an immediate correction:

- do not add a compatibility option;
- do not preserve the old unscaled behavior for `delta != 1`;
- document the behavior change in release notes and objective docstrings;
- retain numerical compatibility at `delta = 1`; and
- do not mix the implementation into M16+ numerical-equivalence commits.

Use an abstract objective unit `U`, normally currency:

- stage contributions have units `U/hour`;
- multiplying by `delta` gives `U` for one interval;
- the multistep objective has units `U` over the modeled horizon;
- horizon-boundary terms, including terminal penalties, already have units
  `U` and are not multiplied by `delta`; and
- a single-step objective is the total `U` over its interval, not a rate.

Consequently, `delta` is meaningful in every build, not only when a temporal
device is active, and must always be finite and strictly positive.

### Objective-term units contract

| Contribution | Stage-rate expression | Coefficient units | Integrated contribution |
|---|---|---|---|
| Generator polynomial/PWL cost | `g(Pg_MW)` | chosen so `g` is `U/hour`; MATPOWER costs are conventionally currency/hour | `delta * g(Pg_MW)` in `U` |
| Storage cycling | `aging_weight * abs(b_MW)` | `aging_weight`: `U/MWh` of one-way throughput | `delta * aging_weight * abs(b_MW)` in `U` |
| HVDC polynomial cost | `h(abs(p_in_MW))` | polynomial coefficients chosen so `h` is `U/hour` | `delta * h(abs(p_in_MW))` in `U` |
| DC flow-loss regularizer | `loss_weight * sum(r_pu * p_flow_pu^2)` | `loss_weight`: `(U/hour)` per numerical unit of the per-unit loss proxy | `delta * loss_weight * L` in `U` |
| Future load shedding | `value_of_lost_load * p_shed_MW` | `value_of_lost_load`: `U/MWh` | `delta * VOLL * p_shed_MW` in `U` |
| Terminal linear | `rho_1 * abs(q_T-target)` | `rho_1`: `U/MWh` | once-per-horizon `U` |
| Terminal quadratic | `rho_2 * square(q_T-target)` | `rho_2`: `U/MWh^2` | once-per-horizon `U` |

For a degree-`k` generator or HVDC monomial `c_k P^k`, `c_k` has units
`(U/hour)/MW^k`. In particular, a linear coefficient can equivalently be
written `U/MWh`.

The DC regularizer needs special care:

```text
L = sum_e r_e,p.u. * p_e,p.u.^2
```

is a dimensionless numerical proxy for instantaneous resistive loss on the
system base. It is not debited from nodal balance and is not automatically a
currency rate. `loss_weight` supplies both its objective scaling and its
`U/hour` interpretation. If a user wants to approximate an economic loss rate
using marginal energy value `kappa` in `U/MWh`, the base-aware scaling is
approximately:

```text
loss_weight = kappa * baseMVA,
```

so `loss_weight * L` is in `U/hour`. The existing default `loss_weight=1.0`
remains a unit-normalized regularization default, not a claim of calibrated
physical loss cost.

### Design questions

The following are resolved:

- `delta` scales every stage contribution automatically.
- storage cycling is charged on energy throughput,
  `aging_weight * delta * abs(b_t)`;
- future M19 load shedding uses
  `delta * value_of_lost_load * p_shed_t`;
- MATPOWER `gencost` output is treated as a stage cost rate;
- single- and multistep objectives both report total objective units over
  their modeled interval/horizon; and
- no compatibility option is introduced.

One policy question remains outside the time-units correction: whether the
default numerical value of `loss_weight` should ever become economically
calibrated. Track C documents its units but does not change that default.

### Required experiment

Construct one physical horizon at at least two discretizations, for example:

- 4 × 1-hour intervals;
- 16 × 0.25-hour intervals.

Use piecewise-constant loads and availability representing the same physical
signals. Compare:

- objective;
- generator energy;
- storage energy throughput;
- final SoC;
- terminal deviation;
- component objective contributions.

Under the selected physical convention, refining the grid should converge
rather than multiply the relative stage weight.

### Resolution-experiment result (2026-07-27)

The required diagnostic experiment is complete. It used the final 24 hours of
the high-stress battery-terminal window at `delta = 1`, `0.5`, and `0.25`
hours, with 24, 48, and 96 steps respectively. Subhourly load and
nondispatchable availability were constructed by zero-order hold, device
ratings and policy coefficients were held fixed, and storage dynamics used
the matching `delta`. The maximum per-channel source-energy discrepancy was
`4.55e-13` MWh, so differences cannot be attributed to changed physical
inputs.

The experiment retained the current objective convention,

```text
sum_t stage_cost_t + terminal_cost.
```

The principal results were:

| Policy | Terminal SoC, 1 h | Terminal SoC, 0.5 h | Terminal SoC, 0.25 h |
|---|---:|---:|---:|
| None | 0.000 MWh | 0.000 MWh | approximately 0 MWh |
| Equality at 500 MWh | 500.000 MWh | 500.000 MWh | 500.000 MWh |
| Quadratic, `w = 0.05` | 350.122 MWh | 215.718 MWh | approximately 0 MWh |

For the no-policy and equality cases, hourly-aggregated active-power
trajectories and common-boundary SoCs agreed to approximately `4.3e-6` or
better. Their operating objectives multiplied by approximately two and four
under refinement because the same stage-rate values were counted two or four
times. Equality remained operationally invariant because it fixes the
endpoint independently of objective scaling.

The soft quadratic policy changed materially. If `V(q_T)` denotes the hourly
operating value function, zero-order-hold refinement under the current
convention gives

```text
(1 / delta) * V(q_T) + w * (q_T - target)^2.
```

Multiplying the complete objective by the positive scalar `delta` shows that
the optimizer is equivalently determined by

```text
V(q_T) + (delta * w) * (q_T - target)^2.
```

Thus a fixed package terminal weight behaves like the hourly-equivalent weight
`delta * w`. The 0.5-hour endpoint exactly reproduced the earlier hourly
soft-weight result at `w = 0.025`; at 0.25 hours the effective weight was
`0.0125`, too small to move the optimum away from zero SoC. This is a
controlled demonstration that the present convention changes the relative
economic meaning of a once-per-horizon terminal penalty when numerical time
resolution changes.

This evidence supports the approved discretized-integral convention,

```text
delta * sum_t stage_rate_t + terminal_cost,
```

The `lossy_dc` term remains an objective penalty, not a physical loss
withdrawal from nodal balance.

Reproducible implementation, tests, tables, and interpretation are in
`experiments/battery_terminal/resolution_study.py`,
`tests/test_battery_terminal_resolution_study.py`, and the executable
`experiments/battery_terminal/report.py`.

### Implementation result (2026-07-30)

Shared component assembly now owns the equal-step integration operation:

```text
delta * sum_t stage_cost_rate_t.
```

All six formulation/horizon builders pass their complete stage-cost rates
through that operation. AC and single-node DC include component rates; lossy
DC includes the component rates and its network-loss regularizer. Aggregated
horizon terminal cost is added afterward and is not scaled.

`OPFBuild.expressions` retains integrated `generator_cost`, conditional
`storage_cost` and `hvdc_cost`, and `dc_loss_cost` for lossy DC. These named
expressions reconstruct the operating objective and preserve the existing
`storage_cost` result field with corrected energy-throughput units.

The high-window resolution experiment was rerun under the implemented
convention. The quadratic policy now terminates at 350.122 MWh at
`delta = 1`, `0.5`, and `0.25` hours; its objective is 83,500.145 objective
units at all three resolutions to solver tolerance. The maximum
common-boundary SoC discrepancy is `1.55e-5` MWh. The earlier results above
remain the diagnostic record of the former unscaled convention.

Public regression tests cover all three formulations in single- and
multistep modes, single-step `delta != 1`, named-cost reconstruction,
terminal-cost separation, and source-independent zero-order-hold refinement.
The README and objective docstrings record the intentional behavior change;
`delta = 1` numerical baselines remain unchanged.

### Implementation stages after decision

1. Retain separate named expressions for each objective contribution.
2. Apply the selected scaling centrally to every stage contribution.
3. Keep terminal contributions outside the stage integral.
4. Publish or expose enough contribution values to audit units.
5. Update all objective docstrings, examples, and tests.
6. Add cross-resolution invariance/convergence tests.
7. Preserve a typed stage-cost path for future M19 load-shedding
   contributions; do not require a formulation builder to splice an emergency
   penalty directly into the total objective.
8. Rename experiment metadata `time_step_hours` to
   `base_time_step_hours`, while retaining the explicit resolution grid.
9. Add a source-independent synthetic regression test for effective terminal
   weight equivalence under the old convention, then update its expected
   result to resolution invariance with Track C.
10. Keep the value-function refinement argument explicitly scoped to
    stage-separable models with ideal storage and zero-order-held inputs.

### Acceptance gates

- Every objective term has documented units.
- `delta` enters objective construction in one centralized place.
- Terminal penalties remain once-per-horizon and unscaled by `delta`.
- `T=1, delta=1` preserves the current baseline.
- Cross-resolution experiments behave according to the selected convention.
- Single-step `delta != 1` scales the interval cost and is tested.
- Invalid `delta` is rejected even without storage.
- No compatibility mode preserves the unscaled convention.

All acceptance gates are covered by unit, formulation-level, and
cross-resolution regression tests.

### Future application: Milestone 19 load shedding

The objective-units decision is a prerequisite for
`milestone-19-load-shedding.md`. Load shedding will be an explicit
generator-like positive injection with a high linear value-of-lost-load cost
and a per-step cap derived from nodal load. Its coefficient should have a
stable physical interpretation across time resolutions.

The hardening work should keep the following future requirements possible
without implementing M19 here:

- component-owned positive active and reactive balance contributions;
- a named, typed per-stage load-shedding cost contribution;
- conditional result keys for configured shedding devices in both successful
  and no-primal outcomes;
- energy-not-served accounting using `delta`; and
- exact-penalty studies in which the finite shedding coefficient crosses the
  relevant marginal service-cost threshold.

Do not add an anonymous feasibility slack during hardening. Absence of an
explicit M19 device must continue to mean that load shedding is unavailable.

This track is decided and must be implemented before Milestone 17.
Implementation may follow M16+ because central stage-cost assembly makes the
change smaller and gives `delta` one authoritative insertion point.

## 6. Work ordering

### Integration policy

The hardening tracks and M16+ are **interleaved at deliberate dependency
boundaries**. By project-owner decision on 2026-07-28, the work may remain on
the shared `critical-path` branch rather than using a separate branch and pull
request for each scope. Clean commit boundaries preserve independent review:

```text
finite-delta fix
        ↓
M16+ characterization and typed contribution foundations
        ↓
unsuccessful-result schema fix
        ↓
M16+ component migration and shared-assembly completion
        ↓
objective-units implementation
        ↓
Milestone 17
```

This ordering is coordinated because later work benefits from earlier
contracts, but the scopes remain separate:

- Track A is a standalone input-correctness change.
- Early M16+ establishes characterization baselines and internal interfaces
  without changing numerical behavior.
- Track B is a standalone public result-contract change. It lands before M16+
  removes the old orchestration so later equivalence tests target the final
  intended result schema.
- The remainder of M16+ retains a strict no-physics and no-numerical-behavior
  change invariant.
- Track C implementation follows shared stage-cost assembly so `delta` has one
  authoritative insertion point. Its approved behavior change does not land
  inside the M16+ refactor.

In particular, result-schema changes and objective scaling must not be hidden
inside M16+. Combining them would weaken the refactor's numerical-equivalence
gate and make regressions difficult to attribute.

Recommended order:

1. **Track A:** finite `delta` validation — small, isolated correctness fix.
2. **M16+ Stages 0–2:** characterize and introduce typed contributions.
3. **Track B:** unsuccessful result schema — independent public-contract fix.
4. **M16+ Stages 3–6:** complete shared assembly and null capabilities.
5. **Track C implementation:** apply the approved objective-units contract on
   top of centralized M16+ stage contribution assembly, before M17.

Track A and the M16+ characterization tests can be developed independently.
Track C must not be smuggled into the M16+ numerical-equivalence refactor.

## 7. Explicitly closed review item — single-node HVDC

The accepted model is:

```text
HVDC contribution to singlenode_dc = null
```

Rationale: single-node DC removes the network and collapses both HVDC
terminals into one node. A transmission link has no remaining state or
constraint in that abstraction. Adding link losses alone would be an
inconsistent partial retention of eliminated network physics.

The required follow-up is architectural explicitness and conformance testing
in M16+, not a warning, rejection, aggregate-loss term, or correctness fix.

## 8. Non-goals

- No full lossy/sign-switching HVDC work from Milestone 15.
- No storage efficiency or degradation-state model.
- No general CVXPY parameterization seam.
- No component adapter implementation in this plan.
- No objective scaling change outside the dedicated Track C implementation.
