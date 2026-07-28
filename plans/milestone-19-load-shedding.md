# Milestone 19 — Explicit load shedding

**Status:** planned

**Depends on:** Milestone 16 (component ownership), objective-units decision in
`correctness-api-hardening.md`

## 1. Goal

Add load shedding as an explicit, first-class device with:

- a positive nodal-injection contribution;
- an interval cap derived from the active load at its bus;
- a high linear cost representing value of lost load;
- physically consistent active and reactive load relief in AC;
- the same device-owned composition contract across AC, lossy DC, and
  single-node DC; and
- explicit unserved-power and unserved-energy results.

The device is generator-like in the nodal balance but is not a
`DispatchableGenerator`. Its semantics, costs, and results must remain
distinguishable from physical generation.

## 2. Device semantics

For shedding device \(i\) at bus \(k(i)\), define

\[
0
\leq
p^{\mathrm{shed}}_{t,i}
\leq
\rho_i P^{\mathrm{load}}_{t,k(i)},
\qquad
0\leq\rho_i\leq 1.
\]

Here \(\rho_i\) is the maximum sheddable fraction. The MVP may default to
\(\rho_i=1\), but the field should be explicit so critical-load studies do not
require a later API break.

The upper bound is rebuilt from the current static or time-series nodal load at
every interval. A device attached to a bus with zero active load has a zero
upper bound and contributes nothing. The builder may omit such structurally
inactive variables, provided the result contract remains deterministic.

The first implementation should require at most one shedding device per bus.
Allowing multiple demand classes at one bus requires either individual
first-class loads or a shared aggregate cap; independently giving several
devices the full nodal-load cap would permit shedding more load than exists.

## 3. Network contribution

With the package's signed-injection convention, active load shedding enters the
nodal balance with the same positive sign as generation:

\[
C_{\mathrm{shed}}p_t^{\mathrm{shed}}.
\]

This is an equivalent reduction in withdrawal, not physical generation.
Component names, result keys, and documentation must preserve that distinction.

### AC reactive-load relief

Active-only relief is generally inconsistent when the underlying load has
reactive demand. For \(P^{\mathrm{load}}_{t,k(i)}>0\), preserve the
instantaneous power factor:

\[
q^{\mathrm{shed}}_{t,i}
=
\frac{Q^{\mathrm{load}}_{t,k(i)}}
     {P^{\mathrm{load}}_{t,k(i)}}
p^{\mathrm{shed}}_{t,i}.
\]

Both active and reactive relief enter the corresponding AC nodal balances as
positive injections. When active load is zero, set both relief terms to zero;
do not divide by zero or implicitly make a reactive-only demand sheddable.

Lossy DC consumes only active relief. Single-node DC uses the aggregate active
contribution while retaining device-level results.

## 4. Cost and preference semantics

Use a linear stage cost

\[
J_{\mathrm{shed}}
=
\sum_{t,i}
\Delta t\,
\nu_i p^{\mathrm{shed}}_{t,i},
\]

under the planned physical-time objective convention. The coefficient
\(\nu_i\) has units of currency per MWh and represents value of lost load.
Until the package-wide objective-units decision is implemented, do not add this
term with an inconsistent private scaling convention.

A finite coefficient is the primary model. It encodes an economic preference,
not merely an artificial feasibility slack. If shedding is cheaper at the
margin than serving load, the optimizer is allowed to shed.

### Exact-penalty phase transition

For the studied single-node, single-device case, there is a clean threshold:
once the shedding coefficient exceeds the marginal dispatchable-generation
cost at maximum output, the optimal served-load solution is invariant to
further increases in the shedding weight whenever full service is feasible.
Above this threshold the finite-cost model acts as an exact penalty for load
service.

M19 must formulate this result precisely, state its assumptions, and reproduce
it numerically. The networked and multi-device generalization must not be
asserted without analysis: congestion, generator bounds, storage opportunity
value, terminal policies, and heterogeneous shedding costs can change the
relevant marginal threshold.

Lexicographic minimization is not the default. It is a distinct policy for the
non-economic statement “minimize unserved energy before considering every
bounded operating cost.” It may be retained as a comparison or future option,
but M19 should lead with the finite value-of-lost-load formulation.

## 5. Proposed public surface

The concrete name remains an implementation-stage decision. The preferred
semantic direction is a separate class such as `LoadSheddingUnit`, passed
explicitly through a `load_shedding=` collection.

Candidate fields:

- `bus`
- `max_fraction`
- `cost_per_mwh`
- optional stable identifier or name

The device should not store a duplicated static load value. Its interval
availability comes from the case load or aligned time-series load supplied to
the problem builder.

Before implementation, resolve:

- exact class and argument names;
- whether absence means no shedding, with no implicit emergency slack;
- validation and behavior for duplicate bus assignments;
- behavior for negative or reactive-only nodal loads;
- whether heterogeneous load classes are in the MVP or deferred; and
- whether a convenience constructor creates one unit per loaded bus.

## 6. Component ownership

The load-shedding component owns:

- validation and bus incidence;
- per-step availability derived from nodal load;
- active and reactive network contributions;
- AC, lossy-DC, and single-node operating constraints;
- stage cost;
- metadata; and
- result contribution.

Problem builders compose these contributions. They must not recreate caps,
power-factor relief, or value-of-lost-load costs independently.

The device has no temporal state, but its cost and availability are
time-indexed. It therefore participates in horizon objective assembly without
adding cross-step constraints.

## 7. Result contract

Expose, at minimum:

- active load shed by interval and device;
- reactive load shed where AC is modeled;
- aggregate active load shed by interval;
- energy not served over the horizon in MWh;
- shedding-cost contribution; and
- device bus and identifier metadata sufficient to map results to loads.

Configured shedding keys must follow the package's unsuccessful-solve result
policy. Do not infer shedding from a nodal residual.

## 8. Verification gates

### Gate 1 — Component algebra

- Zero and full caps.
- Time-varying load caps.
- `max_fraction` behavior.
- Bus incidence and positive-injection sign.
- AC power-factor preservation.
- No division by zero at zero-active-load buses.
- Duplicate-device validation.

### Gate 2 — Economic phase transition

- Reproduce the single-node, single-device threshold against the marginal
  generator cost at maximum output.
- Show that the solution is invariant to larger shedding coefficients above
  the threshold when full service is feasible.
- Show the below-threshold regime in which economic shedding occurs.
- Separate numerical solver tolerance from the theoretical threshold.

### Gate 3 — Adequacy and infeasibility

- Demonstrate that shedding restores feasibility when demand exceeds available
  generation and storage supply.
- Verify that the cap prevents shedding more load than exists.
- Verify that buses with no load contribute no shedding.
- Verify energy-not-served accounting for `delta != 1`.

### Gate 4 — Network formulations

- AC: active and reactive balances include consistent relief.
- Lossy DC: active relief composes with network flow constraints.
- Single-node DC: aggregate balance and device-level results agree.
- Congested cases demonstrate that nodal location matters.

### Gate 5 — Intertemporal storage studies

- Compare dispatchable-priority, battery-priority, and optimal storage control
  when load shedding is available.
- Verify that lower dispatchable energy is never presented as an improvement
  without reporting unserved energy.
- Exercise terminal constraints and costs, including energy reservation for
  later scarcity.

## 9. Documentation and examples

Add:

- one single-node phase-transition example;
- one networked resilience example with storage and nondispatchable
  generation;
- explicit value-of-lost-load units;
- active/reactive relief conventions; and
- a statement that renewable curtailment remains a zero-cost metric of
  interest while load shedding carries a high positive cost.

## 10. Out of scope

- Endogenous load recovery or rebound.
- Minimum interruption duration.
- Restoration sequencing.
- Frequency dynamics.
- General demand response with utility functions.
- Implicit always-on feasibility slacks.
