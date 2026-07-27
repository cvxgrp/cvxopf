# Reactive-support tie-breaker experiment

## Status

Planned.

## 1. Purpose

Determine whether the large nondispatchable reactive-power values observed in
the battery terminal-policy AC study are:

1. arbitrary selections from a nonunique set of unpriced reactive dispatches;
2. materially supporting the selected active-power and voltage solution; or
3. a mixture of both.

This is a separate experiment from
`experiments/battery_terminal`. The battery experiment tests storage terminal
targets. This experiment tests interpretation and regularization of reactive
power in the AC model.

The experiment does not initially propose a package-wide reactive-power cost.
It evaluates a secondary optimization criterion as a diagnostic and
solution-selection rule.

## 2. Starting observation

In the staged high-window AC terminal-policy study:

- every local solution reached the 1.10 p.u. voltage upper bound;
- at least one nondispatchable inverter reached essentially its apparent-power
  limit;
- the 12-hour quadratic and equality cases selected nondispatchable reactive
  dispatch with global extrema near -320 and +316 MVAr;
- the matching no-terminal-policy case selected much smaller global extrema;
  and
- generator reactive dispatch also varied substantially.

The current AC objective does not price generator, nondispatchable, or storage
reactive power. Large reactive values may therefore be necessary, arbitrary,
or merely one IPOPT-selected representative of a nonunique feasible set.

The 1.10 p.u. voltage upper bound is not by itself evidence of inadequate
voltage support. Voltage is an optimization variable, generator voltage
setpoints are not enforced by default, and high voltage may reduce active
network losses. The experiment must distinguish voltage-bound activity from
reactive-support necessity.

## 3. Primary question

For a fixed AC operating objective value, how much nondispatchable reactive
power is actually needed?

The primary diagnostic uses a two-stage lexicographic solve.

### Stage 1 — primary AC optimum

Solve the original AC problem:

```text
minimize J(x)
subject to the existing AC network and device constraints.
```

Record:

```text
J_star = attained primary objective
```

along with the complete primal solution and solver diagnostics.

### Stage 2 — minimum renewable-Q representative

Constrain the primary objective to remain near its attained value:

```text
J(x) <= J_star + tau
```

and minimize normalized nondispatchable reactive effort:

```text
R_nd(x) = sum_t sum_n (q_nd[t,n] / S_nd[n])^2.
```

Here:

- `S_nd[n]` is inverter `n`'s apparent-power rating;
- normalization prevents large utility inverters from dominating solely
  because their MVAr scale is larger; and
- `tau` is a documented numerical tolerance, not an economic willingness to
  trade primary cost for lower reactive effort.

The intended interpretation is lexicographic:

```text
first minimize J, then among near-primary-optimal points minimize R_nd.
```

The ridge is a solution-selection rule. It is not initially interpreted as a
physical market price for reactive power.

## 4. Important implementation constraint

The AC formulation is nonconvex and solved through CVXPY's DNLP/IPOPT path.
Before implementing the full experiment, verify that constraining the retained
primary objective expression with

```text
J(x) <= J_star + tau
```

is supported by the DNLP canonicalization for the objective expressions used
in these cases.

If direct lexicographic composition is unavailable or numerically unstable,
use a continuation study:

```text
minimize J(x) + epsilon * R_nd(x)
```

over a descending grid of positive `epsilon` values. Accept a result as a
tie-breaker only when:

- primary-objective degradation is within the predefined tolerance;
- active dispatch and terminal behavior are stable; and
- the reactive solution converges as `epsilon` decreases.

Do not silently replace the lexicographic experiment with one arbitrary ridge
weight.

## 5. Initial experiment matrix

Reuse the same physical system, source data, scenario preparation, and device
ratings as the battery-terminal AC study.

Start with the high-window 12-hour suffix because it exhibited the largest
reactive difference and solved quickly.

Policies:

1. no terminal policy;
2. quadratic terminal cost at 500 MWh with weight 0.05; and
3. hard terminal equality at 500 MWh.

Variants:

1. original primary solution;
2. minimum normalized nondispatchable-Q solution;
3. minimum normalized all-source-Q solution, as a secondary comparison.

The all-source reactive metric is:

```text
R_all =
    sum_t sum_g (Qg[t,g] / Qscale_g[g])^2
  + sum_t sum_n (q_nd[t,n] / S_nd[n])^2
  + sum_t sum_s (b_q[t,s] / S_storage[s])^2.
```

Generator normalization requires an explicit decision. Candidate
`Qscale_g[g]` is:

```text
max(abs(Qgmin[g]), abs(Qgmax[g]))
```

with zero-capability generators excluded from the corresponding sum.

The first matrix is therefore:

```text
1 scenario
× 1 horizon
× 3 terminal policies
× 3 reactive-selection variants
= 9 solutions
```

Proceed to the 24-hour suffix only if:

- all initial cases return usable local optima;
- the primary-objective tolerance behaves as intended; and
- the 12-hour results do not already resolve the main question.

## 6. Why two ridge variants are needed

Minimizing only nondispatchable Q answers the targeted question:

> Can renewable reactive dispatch be reduced without materially changing the
> primary solution?

It may simply transfer reactive responsibility to conventional generators or
storage.

Minimizing normalized reactive effort across all sources answers a different
question:

> What is a low-total-effort reactive allocation among the available devices?

Both are useful:

- `R_nd` diagnoses reliance on renewable support;
- `R_all` diagnoses total reactive nonuniqueness and allocation.

Neither metric establishes actual equipment wear, opportunity cost, or market
value. Those require a separate physical and economic model.

## 7. Quantities to record

### Primary solution

- solver status;
- wall and solver time;
- primary objective;
- operating objective and terminal cost separately;
- maximum constraint violation;
- terminal SoC and deviation;
- active dispatch trajectories;
- physical AC network loss;
- voltage magnitudes and angles.

### Reactive dispatch by device and time

For conventional generators, nondispatchable units, and storage:

- minimum and maximum Q;
- RMS Q;
- normalized RMS Q;
- maximum absolute normalized Q;
- time and device identity of every reported extremum;
- apparent-power utilization at the same device and time;
- real-power dispatch at the same device and time; and
- fraction of time with normalized Q above selected thresholds.

Do not report only global extrema. A global `q_nd` extremum and maximum
apparent-power utilization may occur at different devices and times.

### Voltage behavior

- minimum and maximum voltage by bus;
- hours and buses at voltage bounds;
- change in voltage relative to the primary solution;
- reactive injection by bus at each voltage-bound event; and
- whether the same buses remain voltage-limited after the tie-breaker.

### Allocation shifts

Relative to the original primary solution:

- change in nondispatchable Q norm;
- change in generator Q norm;
- change in storage Q norm;
- change in total normalized Q norm;
- change in active dispatch;
- change in SoC;
- change in curtailment;
- change in active loss; and
- change in primary objective.

## 8. Tolerance selection

`tau` must be tied to observed solver accuracy and objective magnitude.

Use both:

```text
tau = max(tau_abs, tau_rel * abs(J_star)).
```

Select provisional values only after recording:

- IPOPT termination tolerances;
- repeated cold-start objective variability;
- maximum constraint violations; and
- objective differences from re-solving an unchanged problem.

The tolerance must be:

- large enough not to exclude the Stage-1 local solution because of solver
  noise;
- small enough that active dispatch is still meaningfully primary-optimal;
  and
- fixed before comparing terminal policies.

Report sensitivity to at least one tighter and one looser tolerance. Do not
interpret differences inside known cold-start variability.

## 9. Controls

### Control A — repeated original solve

Repeat the original Stage-1 solve from the same flat initialization and, where
practical, from perturbed initializations. This characterizes local-solution
and numerical variability before attributing a difference to the ridge.

### Control B — renewable unity power factor

As a feasibility counterfactual only, impose:

```text
q_nd == 0.
```

This asks whether renewable reactive power is necessary under the remaining
generator and storage capabilities. It is not the primary tie-breaker because
unity power factor is a materially different operating model.

Record whether the problem:

- remains feasible;
- changes active dispatch or terminal SoC;
- transfers Q to generators/storage;
- changes physical losses; or
- changes voltage-bound activity.

### Control C — source-specific Q restriction

If global unity power factor is too coarse, restrict one renewable class at a
time:

- utility solar;
- wind; and
- distributed solar.

This identifies whether apparent support is concentrated in a particular
resource class or location.

Controls B and C are optional follow-ups. They must not be mixed into the
initial lexicographic result.

## 10. Pre-implementation review additions

The following additions refine the numerical protocol without changing the
experiment's primary question or reactive metrics.

### 10.1 Lexicographic closure when Stage 2 improves the primary objective

The Stage-2 constraint

```text
J(x) <= J_star + tau
```

allows Stage 2 to discover a point with a materially lower primary objective
than the Stage-1 local solution. If

```text
J_stage2 < J_star - tau,
```

do not interpret the result as a reactive tie-breaker. Instead:

1. use the improved Stage-2 point to initialize another Stage-1 solve;
2. update `J_star`;
3. rerun both Stage-2 variants from the updated Stage-1 point; and
4. repeat until Stage 2 no longer materially improves `J`.

Do not impose a lower bound on `J` in Stage 2. A genuine improvement in the
primary local solution must remain discoverable. Record every closure
iteration and distinguish a basin improvement from a tie-breaker result.

### 10.2 Stage-2 initialization

Warm-start each Stage-2 solve from the retained Stage-1 primal point. This
starts from a known primary-feasible point and tests secondary selection in
the same local basin as directly as the solver permits.

The `R_nd` and `R_all` variants must each start independently from the same
Stage-1 point. Do not initialize one ridge variant from the result of the
other, because doing so would confound the selection rules.

This use of a warm start is specific to the lexicographic continuation. The
repeated cold and seeded perturbed starts in Control A remain necessary for
characterizing basin and numerical variability.

### 10.3 Primary-objective identity

Retain and record both:

```text
J_star_problem    = Stage-1 prob.value
J_star_expression = retained primary objective expression.value
```

The Stage-2 inequality must use the retained primary objective expression.
Verify that the two recorded Stage-1 values agree within numerical tolerance
before constructing the bound.

For a quadratic terminal policy, `J` includes the terminal penalty. Continue
to report the operating objective and terminal cost separately, but preserve
the complete original primary objective in the lexicographic constraint.

### 10.4 Interpretation of the all-source metric

Report the all-source ridge as three separate contributions:

```text
R_all = R_g + R_nd + R_storage.
```

The proposed sum is device-normalized, not class-balanced. With seven
nondispatchable units, three generators, and one storage unit, device count
implicitly affects the relative contribution of each class. Keep the proposed
metric for the nominal study, state this weighting explicitly, and reserve a
class-balanced alternative for sensitivity analysis only if the conclusion
depends materially on device count.

For every device class, keep three quantities distinct:

1. signed reactive power `Q`;
2. normalized reactive magnitude, such as `abs(Q) / S`; and
3. apparent-power utilization `sqrt(P^2 + Q^2) / S`.

The global reactive-power extremum and maximum apparent-power utilization may
occur at different devices and times.

### 10.5 Nominal matrix versus numerical-control solves

The nine cases in Section 5 are the **nominal comparison matrix**, not the
complete solve count. The reproducibility record must separately enumerate:

- tighter, nominal, and looser `tau` cases;
- repeated cold and seeded perturbed Stage-1 controls;
- any lexicographic-closure iterations; and
- optional unity-power-factor or source-specific counterfactuals.

This distinction prevents numerical controls from being hidden behind the
nominal `1 × 1 × 3 × 3` description.

### 10.6 Remaining nonuniqueness and local KKT evidence

A strictly convex ridge in reactive variables does not necessarily select a
unique complete primal solution. Voltage magnitudes, voltage angles, or active
dispatch may remain nonunique. Reduced reactive effort must therefore not be
described as uniqueness of the complete operating point.

Record whether the Stage-2 primary-objective bound is active and, when the
solver exposes it reliably, its multiplier. Interpret that multiplier only as
local KKT evidence. It is not a globally certified reactive-power price.

## 11. Interpretation rules

### Evidence for unpriced nonuniqueness

Conclude that the original renewable-Q extremes were largely a selection
artifact if the tie-breaker:

- greatly reduces `R_nd`;
- preserves the primary objective within tolerance;
- leaves active dispatch, SoC, and terminal behavior essentially unchanged;
- preserves feasibility and comparable voltage margins; and
- does not merely create comparably extreme generator or storage Q under the
  all-source metric.

### Evidence for structural renewable support

Conclude that renewable Q materially supports the operating point if:

- substantial normalized renewable Q remains under `R_nd`;
- reducing it requires primary-objective degradation or material active
  redispatch;
- voltage feasibility deteriorates as renewable Q is restricted; or
- unity-power-factor controls become infeasible despite available conventional
  and storage reactive capability.

### Mixed result

A likely result is mixed:

- some original Q is nonunique and disappears under the ridge;
- a smaller location- and time-specific component remains necessary; and
- reactive responsibility shifts among device classes.

Report this structure rather than forcing a binary conclusion.

## 12. Engineering limitations

The provisional model assumes:

- nondispatchable inverters can inject or absorb Q anywhere inside their full
  apparent-power circle;
- reactive capability remains available at low or zero renewable real power;
- storage has four-quadrant inverter capability;
- reactive dispatch has no explicit physical or economic cost;
- case9 voltage bounds and network parameters represent the synthesized
  scenario adequately;
- generator voltage setpoints are not enforced by default;
- AC branch thermal limits are unavailable; and
- IPOPT returns local, not globally certified, optima.

Accordingly, a minimum-Q representative is a cleaner solution of the
provisional mathematical model. It is not by itself validation of renewable
plant controls, interconnection requirements, dynamic voltage stability, or
thermal security.

## 13. Proposed implementation

Suggested files:

```text
experiments/reactive_support_tiebreaker/
    README.md
    experiment_log.md
    runner.py
    analysis.py
    reproduce.py
    results/
        .gitignore
```

Reuse scenario and device preparation from `experiments/battery_terminal`
initially so the physical comparison is exact. If reuse creates an undesirable
dependency between experiments, extract shared experiment fixtures only after
the first result establishes what is actually common.

The runner should retain complete builds and trajectories in memory while
writing scalar and device-time diagnostic tables for reproduction.

## 14. Tests

Add focused tests for:

- normalized ridge construction;
- zero generator-Q capability handling;
- Stage-2 primary-objective bound construction;
- status-aware result extraction;
- device/time attribution of extrema;
- separation of nondispatchable-only and all-source metrics;
- tolerance calculation;
- unchanged active quantities in a synthetic nonunique reactive example; and
- persistence of necessary reactive support in a constrained synthetic
  example.

At least one small synthetic case should have analytically obvious reactive
nonuniqueness so the experiment machinery is validated independently of
case9/IPOPT behavior.

## 15. Acceptance gates

### Gate 1 — reproducibility

- Source data hash, repository commit, dirty state, package versions, solver
  options, tolerance, ridge definition, and device specification are recorded.
- The initial matrix can be reproduced with one command.

### Gate 2 — numerical integrity

- Every claimed comparison uses a usable local optimum.
- Stage-2 primary objective remains within its declared tolerance.
- Constraint violations remain within the experiment threshold.
- Results are robust to the documented tolerance sensitivity check.

### Gate 3 — diagnostic completeness

- Reactive extrema are attributed to specific devices, buses, and times.
- Renewable-only and all-source reactive norms are reported separately.
- Active dispatch, SoC, voltage, and loss changes are reported alongside Q
  changes.
- Voltage-upper-bound activity is not described as voltage-support necessity
  without a counterfactual.

### Gate 4 — scope discipline

- The ridge is described as a tie-breaker, not a calibrated reactive price.
- No package default is changed by the experiment.
- Unity-power-factor and capability restrictions are labeled as
  counterfactual model variants.
- AC local optimality and missing branch thermal limits remain explicit.

## 16. Completion criterion

The experiment is complete when it can answer:

1. How much of the observed renewable reactive dispatch disappears under a
   near-lexicographic minimum-Q selection?
2. Where and when does any remaining renewable reactive support occur?
3. Does minimizing renewable Q transfer support to generators or storage?
4. Does minimizing all-source reactive effort preserve the primary terminal
   policy, active dispatch, and voltage feasibility?
5. Is the conclusion stable across the no-policy, quadratic, and equality
   storage-terminal cases?
