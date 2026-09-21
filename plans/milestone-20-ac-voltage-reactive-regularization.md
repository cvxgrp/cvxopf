# Milestone 20 — AC voltage and reactive-dispatch regularization

**Status:** planned

**Depends on:** Milestone 4 (AC operating limits), Milestone 8
(nondispatchable reactive capability), Milestone 16+ (typed component
contributions and shared assembly), and correctness/API-hardening Track C
(objective time units)

**Related work:** Milestone 17 may consume the resulting AC policy, but its
frozen S3 experiment and interval-35 follow-up remain unchanged. M20 does not
retroactively alter their objectives or conclusions.

## 1. Goal

Determine why AC solutions frequently place reactive-power variables or
voltage magnitudes at their bounds, then add an optional and scientifically
documented way to select well-centered AC operating points when the observed
behavior is caused by an unpriced or weakly identified degree of freedom.

The milestone must distinguish three cases:

1. **Physical support requirement.** A reactive limit or voltage bound is
   active because network balance, branch limits, voltage limits, and device
   capability genuinely require it.
2. **Economic indifference or nonuniqueness.** Multiple AC-feasible points
   have the same or practically identical primary operating cost, and the
   current objective provides no preference among their voltage/reactive
   coordinates.
3. **Local nonlinear-solver selection.** IPOPT converges to different
   stationary points or different active sets from different initializations.

Regularization is appropriate in the second case and may improve numerical
selection in the third. It must not be described as eliminating a physical
support requirement in the first.

## 2. Current scientific baseline

The present AC objective prices active-power generation and any configured
device operating costs. It ordinarily does not price:

- deviation of bus-voltage magnitude from a preferred reference;
- dispatchable-generator reactive output;
- nondispatchable reactive support; or
- storage reactive support.

All corresponding physical limits remain enforced. Therefore, reactive power
at a bound is not by itself a correctness defect. In an unpriced direction,
the nonlinear solver is free to select any locally optimal feasible value,
including a value at or near a limit.

The existing model is the compatibility baseline. With M20 disabled, builders
must preserve the exact pre-M20 variables, expressions, constraints,
objective, metadata, initialization, and result schemas.

## 3. Scope

M20 covers optional AC-only operating-point preferences for:

- voltage-magnitude deviation from explicit references;
- dispatchable-generator reactive dispatch;
- nondispatchable-generator reactive dispatch; and
- storage reactive dispatch.

It also adds diagnostics that distinguish reactive-limit activity,
voltage-bound activity, objective displacement, and solver sensitivity.

The initial public implementation should use smooth quadratic terms. They are
compatible with the existing continuous AC DNLP and add no integer variables,
mode decisions, or physical constraints.

## 4. Non-goals

M20 does not introduce:

- voltage or reactive-power security constraints beyond the existing limits;
- contingency analysis, voltage-stability margins, or continuation power
  flow;
- optimal power-flow guarantees for the nonconvex AC model;
- an automatic feasibility-restoration solve;
- generator capability curves beyond the current reactive bounds;
- switched shunts, capacitor-bank stages, controllable taps, or controllable
  phase shifters;
- reactive-power markets or a claim that the preference weight is a market
  price;
- reactive HVDC capability;
- lexicographic global optimization; or
- changes to lossy-DC or single-node physics.

The characterization stage may evaluate a secondary or epsilon-constrained
solve as an experiment. That does not make a second solve part of the public
M20 API.

## 5. Mathematical candidates

### 5.1 Voltage-reference preference

For AC bus-voltage magnitudes \(V_{i,t}\),

\[
J_V
=
\Delta t\,\lambda_V
\sum_{t,i} w_i^V
\left(V_{i,t}-V_i^{\mathrm{ref}}\right)^2.
\]

The reference is a preference, not an additional equality. Reference
selection must be explicit and deterministic. The API-lock stage must decide
among:

- an explicitly supplied per-bus reference;
- the MATPOWER generator voltage setpoint where one unambiguous active
  generator controls the bus;
- the input bus `VM` value; and
- an explicit nominal fallback such as 1.0 p.u.

Conflicting generator setpoints at one bus must not be silently averaged.
References must lie inside the corresponding voltage bounds unless an explicit
validation policy states otherwise.

### 5.2 Reactive-dispatch preference

For a reactive-capable device \(d\), use a normalized ridge:

\[
J_{Q,d}
=
\Delta t\,\lambda_{Q,k}
\sum_t w_d^Q
\left(
  \frac{Q_{d,t}-Q_d^{\mathrm{ref}}}{Q_d^{\mathrm{scale}}}
\right)^2,
\]

where \(k\) identifies the device class. The initial candidate reference is
zero MVAr for devices without a configured reactive setpoint. This is only an
operating preference; it does not assert that zero reactive support is
physically optimal.

`Q_scale` must be positive, finite, and derived deterministically from the
device capability or supplied explicitly. Fixed-reactive devices contribute
zero rather than causing division by zero. Normalization prevents a large
device from dominating only because its values are measured on a larger MVAr
scale.

Dispatchable, nondispatchable, and storage reactive terms require separate
weights. The scientific evidence may support a light nondispatchable ridge
without supporting the same treatment for dispatchable generators, whose
reactive output is an intentional voltage-control resource.

### 5.3 Units and time integration

Every regularization term is a stage-cost rate and is integrated exactly once
by `delta`. Terminal costs remain outside this time integral.

If the main objective is expressed in currency per hour, each regularization
weight supplies the units needed to produce the same objective-rate units.
The documentation must call these terms **operating preferences** or
**regularization contributions** unless a user deliberately supplies an
economic interpretation. A dimensionless score must not be added to a
currency objective without a weight carrying the required units.

## 6. Scientific characterization

Before adding a public option, measure the existing behavior across the
bundled AC cases and representative multistep studies. At minimum, record:

- minimum distance to every lower and upper reactive bound;
- count and identity of reactive-capable devices within a frozen engineering
  tolerance of a bound;
- minimum distance to bus-voltage bounds;
- voltage-reference RMS and maximum deviation;
- reactive output and normalized reactive utilization by device class;
- active generation cost, other existing objective contributions, and losses;
- branch utilization and all accepted-primal residuals;
- IPOPT status, iterations where available, runtime, and initialization; and
- variation across a predeclared set of deterministic initializations.

Absolute and normalized bound-activity tolerances must be frozen before
interpreting results. Raw equality feasibility tolerances are not necessarily
appropriate engineering thresholds for declaring that a device has
materially exhausted its reactive capability.

### 6.1 Counterfactual experiments

Use controlled, predeclared comparisons:

1. baseline objective with the project-default initialization;
2. baseline objective with alternate deterministic initializations;
3. voltage-only regularization;
4. reactive-only regularization, separately by device class;
5. combined voltage and reactive regularization; and
6. a fixed logarithmic weight sweep.

For each accepted solve, compare the primary pre-regularization operating
objective separately from the augmented objective. Never infer improvement
from the augmented scalar alone.

Where useful, a diagnostic secondary solve may constrain the primary
objective to remain within a predeclared absolute/relative tolerance and then
minimize the preference score. Because the AC problem is nonconvex, this is
only evidence relative to the accepted first solution; it is not a proof of a
global lexicographic optimum.

### 6.2 Interpretation gate

Regularization is supported only when the evidence shows that it:

- reduces unwanted voltage/reactive dispersion or initialization sensitivity;
- preserves all physical residuals and operating limits;
- has a measured and acceptable effect on the primary economic objective;
- does not systematically replace required reactive support with voltage or
  branch-limit violations; and
- behaves consistently across the selected cases and horizons.

If a reactive limit remains active under a weight sweep, report that evidence
rather than escalating the weight until the bound becomes inactive.

## 7. Public API questions to freeze

The implementation should converge on one typed policy object, provisionally:

```python
ACRegularizationPolicy(
    voltage_weight=0.0,
    dispatchable_q_weight=0.0,
    nondispatchable_q_weight=0.0,
    storage_q_weight=0.0,
    voltage_reference=...,
    q_reference=...,
    q_normalization=...,
)
```

The exact fields are not frozen by this initial plan. Stage 2 must resolve:

- whether references are global policies, aligned arrays, or device fields;
- whether per-device overrides are necessary in the first release;
- validation of negative, nonfinite, missing, or zero scales;
- the behavior of a nonzero AC policy requested with a DC formulation;
- expression and result names;
- warning behavior; and
- whether policy metadata is retained on unsuccessful solves.

The default must be an exact disabled state. Prefer rejecting a nonzero
AC-only policy on a DC formulation over silently suggesting that it was
applied. If a formulation capability registry handles the distinction, the DC
capability must be explicit rather than an accidental dropped hook.

## 8. Architecture

Voltage magnitude belongs to formulation-owned AC network physics, while
reactive dispatch belongs to reactive-capable component models. Preserve that
boundary:

- the AC network layer contributes the voltage-reference expression;
- dispatchable, nondispatchable, and storage adapters contribute their own
  reactive regularization expressions;
- shared assembly aggregates named contributions generically; and
- DC/single-node formulations expose no reactive or voltage expression.

Do not move AC voltage variables into a component adapter or duplicate
reactive objectives in formulation builders. If the current typed contracts
lack a suitable optional network-preference or component-preference hook, add
the smallest typed contract that makes the ownership honest.

No regularization term should require a new optimization variable or
constraint. Construct sums with elementwise operations or `sum_squares`;
avoid dense diagonal matrices. Assembly work should scale linearly with the
number of buses, reactive-capable devices, and time steps.

## 9. Result and diagnostic contract

When enabled, retain separately named contributions such as:

- `voltage_regularization_cost`;
- `dispatchable_q_regularization_cost`;
- `nondispatchable_q_regularization_cost`;
- `storage_q_regularization_cost`; and
- `ac_regularization_cost`.

Names are provisional until the API lock. The aggregate must equal the sum of
its published parts and enter the main objective exactly once.

Also expose or provide deterministic analysis helpers for:

- distance to reactive lower and upper bounds;
- normalized reactive utilization;
- distance to voltage lower and upper bounds; and
- deviation from configured references.

Successful single-step and multistep shapes must follow the established result
contract. Unsuccessful solves retain configuration and identity metadata,
while unavailable primal-derived arrays are `None` and unavailable scalar
contributions are `NaN`.

## 10. Correctness and compatibility gates

### 10.1 Disabled-policy equivalence

With no policy or all-zero weights:

- do not add zero-valued CVXPY expressions merely for convenience;
- preserve the exact objective expression and DNLP structure;
- preserve numerical baselines and result-key presence; and
- preserve all formulation and horizon behavior.

### 10.2 Objective accounting

For every enabled contribution:

- verify hand-calculated single-step values;
- verify multistep summation;
- verify `delta` scaling exactly once;
- verify T=1 multistep agreement with the single-step contract;
- verify that terminal costs are unchanged and unscaled by this stage-cost
  integration; and
- verify aggregate-versus-component reporting equality.

### 10.3 Scientific behavior

Include cases where:

- an unpriced reactive degree of freedom is demonstrably nonunique;
- reactive support is physically necessary and remains nonzero;
- a device legitimately remains at its reactive limit;
- a voltage reference is interior to its bounds;
- an invalid or conflicting reference is rejected;
- dispatchable, nondispatchable, and storage reactive terms are independently
  enabled;
- multiple reactive-capable devices share a bus;
- no reactive-capable optional devices are configured;
- branch limits are binding; and
- alternate initializations reach different or equivalent stationary points.

Do not write tests that require every nonconvex solve to reproduce a unique
reactive vector unless regularization makes that uniqueness part of the
explicit contract.

### 10.4 Performance and numerical conditioning

Measure canonicalization and solve time on representative single- and
multistep cases. Very small weights may be numerically invisible; very large
weights may distort active dispatch or worsen scaling. Freeze supported weight
ranges or provide explicit guidance from evidence rather than relying on the
phrase "light ridge."

## 11. Implementation stages

### Stage 0 — Characterize the existing AC solutions

- Freeze rail/activity metrics and engineering tolerances.
- Run bundled-case and multistep baselines.
- Measure initialization sensitivity and identify representative cases for
  physical support, nonuniqueness, and local-solver selection.
- Publish a characterization report before choosing defaults.

**Stopping point:** reviewed evidence identifies which variables and device
classes, if any, justify regularization.

### Stage 1 — Objective and solver experiment

- Implement experiment-local voltage and per-device-class reactive ridges.
- Run the predeclared ablations and weight sweep.
- Measure primary-objective displacement, constraint activity, residuals, and
  solver behavior.
- Evaluate a diagnostic secondary-objective formulation without committing it
  to the public API.

**Stopping point:** select the supported mathematical terms and defensible
weight guidance, or conclude that production regularization is not supported.

### Stage 2 — Freeze the API and units

- Define the typed policy, references, normalization, validation, formulation
  capability, expression names, and unsuccessful-result behavior.
- Lock default-disabled structural equivalence.
- Document whether any default nonzero weight is scientifically justified.

**Stopping point:** reviewed public and internal contracts with no unresolved
unit or formulation ambiguity.

### Stage 3 — Implement owned objective contributions

- Add the AC network voltage contribution.
- Add component-owned dispatchable, nondispatchable, and storage reactive
  contributions.
- Aggregate them through the typed assembly path.
- Preserve explicit DC/single-node behavior.

**Stopping point:** focused structural and hand-calculation tests pass without
changing disabled-policy baselines.

### Stage 4 — Results and diagnostics

- Publish named contribution values and aggregate regularization.
- Add bound-distance, utilization, and reference-deviation analysis.
- Preserve unsuccessful-solve schemas and aligned device identity.

**Stopping point:** result reconstruction agrees with modeled expressions for
all supported horizon shapes.

### Stage 5 — Scientific and regression verification

- Run the approved case matrix and initialization comparisons.
- Verify time integration, economic displacement, active limits, residuals,
  and performance.
- Run the full repository suite and static checks.

**Stopping point:** the implementation satisfies the Stage 1 scientific gates
and introduces no disabled-policy regression.

### Stage 6 — Documentation and extension proof

- Document the distinction between physical reactive support, nonuniqueness,
  and regularized selection.
- Add a concise AC example with before/after diagnostics.
- Update the architecture flowchart if a network-preference hook is added.
- Record implications for M17 hierarchical AC windows without changing the
  frozen M17 experiments.

**Stopping point:** public behavior, units, limitations, and extension path are
clear enough to reproduce and critique.

## 12. Acceptance criteria

M20 is complete only when:

- the pre-implementation characterization is preserved;
- enabled terms have explicit references, normalization, and units;
- default-disabled builds are structurally and numerically equivalent to the
  pre-M20 baseline;
- reactive limits and voltage bounds remain physical constraints;
- each contribution is integrated by `delta` exactly once;
- component and network ownership remains explicit;
- DC behavior is explicit and cannot be mistaken for applied AC
  regularization;
- reported contributions reconstruct the augmented objective;
- primary economic displacement and performance are quantified; and
- documentation does not claim that regularization proves physical necessity,
  uniqueness, voltage stability, or global AC optimality.

## 13. Principal risks

- **Misdiagnosing physical support as degeneracy.** Characterize before
  regularizing and retain active-limit diagnostics.
- **Changing economics accidentally.** Report the original objective and each
  preference contribution separately.
- **Poor scaling.** Normalize reactive quantities and evaluate weight ranges.
- **False uniqueness claims.** AC remains nonconvex even with a strictly convex
  preference in selected variables.
- **Solver-result dependence.** Use predeclared initializations and preserve
  all outcomes, not only successful or aesthetically pleasing solutions.
- **Architecture leakage.** Keep voltage preferences in the AC network layer
  and reactive preferences in component adapters.
- **Historical contamination.** Do not rerun or reinterpret frozen M17 results
  under an M20 objective without a separately declared experiment.
