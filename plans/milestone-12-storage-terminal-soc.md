# Milestone 12 — Storage terminal state of charge

**Status:** complete — implemented and verified 2026-07-27
**Depends on:** Milestone 5 (storage), Milestone 16 (component ownership)
**Enables:** Milestone 17 (hierarchical DC→AC receding-horizon dispatch)

## 1. Goal

Add an optional terminal state-of-charge specification to
`StorageUnitIdeal`. A storage unit may have:

- no terminal target (the current behavior);
- a hard terminal equality;
- a hard zero-shortfall constraint;
- a two-sided linear or quadratic terminal-deviation cost; or
- a one-sided linear or quadratic terminal-shortfall cost.

The contract must be identical in AC, lossy DC, and single-node DC. Storage
continues to own its feasible set, temporal coupling, and cost contribution;
formulation builders only compose the storage-owned constraints and
expressions.

This milestone supplies the SoC-signpost mechanism required by M17. It does
not implement the hierarchical controller itself.

## 2. Current model and insertion points

For each storage unit `s`, the current ideal-storage dynamics are

```text
soc[0, s] = initial_soc[s] - delta * b[0, s]
soc[t, s] = soc[t-1, s] - delta * b[t, s],  t >= 1,
```

with `0 <= soc[t, s] <= capacity[s]`. Positive `b` is discharge into the
network.

`storage.coupling_constraints(...)` owns the dynamics and is called after the
time-step loop by all three formulations. The hook is best understood as
owning the device's horizon-level temporal feasible set: state transitions
and temporal boundary conditions. A terminal boundary is not itself a
cross-step constraint, so M12 must keep this distinction visible internally
even though the existing public hook composes both.

`storage.storage_cost_expr(...)` owns the per-step L1 cycling cost. M12 should
add a separate storage-owned terminal-cost surface rather than add terminal
logic to each formulation or blur cycling and terminal costs.

For a one-step problem, `soc[0]` is the end-of-step state. A terminal
specification therefore has a well-defined and formulation-independent
meaning for both `build_opf` and `build_opf_multistep`.

## 3. Recommended boundary

Add optional terminal-policy fields to each `StorageUnitIdeal`. The device
object is the authoritative source of its terminal policy, just as it is for
capacity, initial SoC, and aging cost.

Do not introduce a general problem-data parameterization seam in M12.
Presently, a receding-horizon AC controller must rebuild each window because
load and other problem data are not CVXPY parameters. Making only terminal
targets updateable would be a partial M13 implementation without eliminating
the rebuild. M17 can construct the storage objects for each window using the
next DC signpost; M13 can later parameterize the complete repeated-solve path.

The storage module should expose two distinct contributions:

1. `coupling_constraints(...)` composes private SoC-dynamics and hard-terminal
   helpers.
2. `terminal_cost_expr(...)` returns the selected soft terminal penalty.

Hard terminal conditions belong with temporal coupling, not with the
per-step operating region. Soft terminal penalties belong in the objective,
not as relaxed constraints.

Do not add a new public `terminal_constraints(...)` hook to every device.
That would enlarge the M16 component interface for a storage-specific
requirement. Do not rename the existing public hook in M12; clarify its
horizon-level temporal meaning instead.

## 4. Mathematical contract

Let `q_s` denote `soc[T-1, s]`, the final post-step state represented by the
current indexing, and let `q_target,s` be its configured terminal target.

Hard equality:

```text
q_s = q_target,s.
```

Hard zero-shortfall constraint:

```text
q_s >= q_target,s.
```

Two-sided soft relaxations of equality:

```text
linear:     rho_s * |q_s - q_target,s|
quadratic:  rho_s * (q_s - q_target,s)^2
```

One-sided soft relaxations of the reserve floor:

```text
shortfall_s = max(q_target,s - q_s, 0)

linear:     rho_s * shortfall_s
quadratic:  rho_s * shortfall_s^2
```

All four soft forms are convex. The one-sided forms penalize only failure to
meet the reserve floor; arriving with additional stored energy has zero
terminal penalty.

The implementation must use CVXPY's negative-part atom:

```python
deviation = soc_list[-1][s] - target
shortfall = cp.neg(deviation)
```

The quadratic one-sided cost is `cp.square(shortfall)`. Do not reconstruct the
negative part with auxiliary variables or formulation-specific constraints.

The terminal term is added once per horizon, never once per time step and
never multiplied by `T` or `delta`. It is a boundary-value penalty, not an
integral stage cost.

There is no terminal band in M12.

## 5. Invariants

- Existing models are unchanged when no terminal target is configured.
- A target is specified in MWh and must be finite and within
  `[0, capacity]`.
- A hard equality adds exactly one equality per configured storage unit.
- A hard zero-shortfall mode adds exactly one inequality per configured unit.
- A terminal cost adds no feasibility constraint beyond the existing SoC bounds.
- A soft weight must be finite and strictly positive.
- Hard-constraint and soft-cost modes are mutually exclusive for a given unit.
- Terminal logic is implemented once in `storage.py`; AC, lossy DC, and
  single-node DC do not reproduce its mathematics.
- The single-step and multistep builders use the same storage-owned hooks.
- M12 does not add charge/discharge inefficiency, terminal bands,
  time-varying targets, or a general parameter-update API.

## 6. Proposed implementation stages

### Stage 1 — Lock the public contract

Implement the approved dataclass fields below, validation rules, objective
units, and result keys.

### Stage 2 — Extend storage-owned data and validation

- Add the selected optional terminal-policy fields to `StorageUnitIdeal`.
- Validate target, policy, and weight combinations with indexed error
  messages.
- Extend `_storage_static_data`, `_prepare_data`, and `_build_metadata` only
  with fields that downstream code or result reporting actually needs.
- Preserve all existing defaults.

### Stage 3 — Add storage-owned terminal composition

- Split the body of `coupling_constraints(...)` into clearly named private
  SoC-dynamics and hard-terminal helpers, then compose them through the
  existing public hook.
- Add a collection-level `terminal_cost_expr(...)` for soft targets.
- Keep the existing per-step cycling-cost helper unchanged.
- Do not create optimization variables in `storage.py`.

### Stage 4 — Compose through all formulations

- Add the terminal penalty once, after the horizon variables exist.
- Retain separate expressions for cycling cost and terminal cost.
- Use the same component calls in AC, lossy DC, and single-node DC.
- Confirm that one-step builders apply a target to the post-step SoC.

### Stage 5 — Results and documentation

- Publish only the agreed terminal target/deviation/cost quantities.
- Add a storage example showing unconstrained, equality, zero-shortfall, and
  soft-cost behavior on the same small case.
- Document infeasibility as an expected outcome when a hard constraint is not
  reachable within the horizon.
- Update the roadmap and M17 dependency text.

## 7. Test gates

### Gate 1 — validation

- Target below zero, above capacity, NaN, and infinity are rejected.
- Invalid or contradictory policy/weight combinations are rejected.
- Existing `StorageUnitIdeal(...)` construction remains valid.

### Gate 2 — component unit tests

- No target returns no terminal constraint and zero/no terminal cost.
- Equality produces the expected equality on `soc_list[-1]`.
- Zero-shortfall mode produces the expected inequality on `soc_list[-1]`.
- Each linear/quadratic, one-/two-sided cost produces the expected convex
  expression and no terminal constraint.
- One-sided costs use `cp.neg(soc_list[-1][s] - target)`, are zero above the
  target, and are positive below it; two-sided costs are not zero above it.
- Multiple units may independently use none, equality, shortfall, and
  soft-cost modes.
- The terminal penalty is counted once for `T > 1`.

### Gate 3 — formulation conformance

For AC, lossy DC, and single-node DC:

- a reachable equality is attained within solver tolerance;
- a reachable zero-shortfall constraint is met and permits a terminal state
  above target;
- deliberately unreachable hard modes are infeasible;
- increasing a two-sided soft weight weakly reduces absolute deviation in a
  case with a genuine energy/cost tradeoff;
- increasing a one-sided soft weight weakly reduces terminal shortfall;
- no-target objective and dispatch match the pre-M12 baseline;
- `T=1` uses the post-step SoC as the terminal state.

### Gate 4 — result contract

- Agreed terminal quantities have consistent shapes and engineering units.
- Cycling cost remains separately identifiable from terminal penalty.
- Absent terminal policies do not create misleading result keys, unless the
  selected result policy explicitly chooses stable keys with neutral values.

### Gate 5 — regression

- Ruff passes.
- Full test suite passes.
- Existing storage, AC, lossy-DC, and single-node examples remain valid.

## 8. Approved public contract

### 8.1 Fields

Use flat fields consistent with the existing device dataclasses:

```python
terminal_soc: float | None = None
terminal_constraint: str | None = None
# None, "equality", or "shortfall"
terminal_cost: str | None = None
# None, "linear", "quadratic", "shortfall_linear", or "shortfall_quadratic"
terminal_weight: float | None = None
```

`terminal_constraint` and `terminal_cost` are mutually exclusive. A target is
required when either is configured. A positive weight is required exactly
when a cost is configured. With no constraint or cost, all three associated
values must remain `None`; this rejects inert configuration rather than
silently ignoring it.

A nested terminal-policy dataclass would encode the variants more strongly,
but it would introduce a new pattern not used by the other device models.
Separate fields for every variant would make invalid combinations harder to
understand. The flat explicit policy fields are the conservative choice.

The `"shortfall"` constraint mode enforces zero terminal shortfall,
`soc[-1] >= terminal_soc`. The shared name makes the connection to
`"shortfall_linear"` and `"shortfall_quadratic"` explicit.

### 8.2 Cost units

- Linear weights have units of objective units/MWh.
- Quadratic weights have units of objective units/MWh².
- The terminal term is not scaled by `delta`.

The current multistep builders sum per-step generation and cycling costs
without multiplying the stage objectives by `delta`. Consequently, for
`delta != 1 hour`, the terminal weight is relative to the package's current
summed-stage objective and should not automatically be interpreted as a
physical dollar coefficient. M12 must document this existing convention and
must not repair stage-cost discretization as an incidental terminal-policy
change. A separate objective-units decision should address it consistently
for every component.

### 8.3 Time indexing

`soc_list[t]` is the state after dispatch interval `t`; the initial state is
exogenous and is not stored in `soc_list`. Therefore, the M12 terminal state
is always `soc_list[-1]`. When M17 maps an upper-layer signpost to an AC
window ending at global interval `k`, it must use the upper-layer post-step
state for that same interval `k`. This convention must be tested explicitly
to prevent a one-step signpost offset.

### 8.4 Result schema

Publish only:

- signed terminal deviation `soc[-1] - target`, shape `(ns,)`, MWh; and
- aggregate terminal penalty, scalar objective units, when a soft terminal
  cost is active.

The signed deviation is negative for a shortfall and positive for a surplus,
so a separate shortfall result would be redundant. Terminal result keys are
conditional on at least one active terminal policy. The deviation retains
shape `(ns,)`; entries for units without a terminal policy are `NaN`. This
preserves device ordering without inventing a physical target for an
unconfigured unit.

### 8.5 Meaning of `storage_cost`

Preserve the current meaning of `expressions["storage_cost"]` as cycling cost
for backward compatibility. Add `expressions["storage_terminal_cost"]` only
when at least one soft terminal cost is active. Do not report a ceremonial
zero terminal-cost expression for hard-only or inactive policies.

### 8.6 Constraint/cost coexistence

The contract rejects a simultaneous hard constraint and soft cost on
the same unit. The matching soft cost would be identically zero whenever the
hard constraint is satisfied, while a mismatched pair would be difficult to
interpret as a relaxation.

## 9. Explicitly out of scope

- The M17 receding-horizon controller and DC-to-AC orchestration.
- General CVXPY parameterization or repeated-solve acceleration (M13).
- AC branch-flow limits (M4), although M4 is a prerequisite for the full M17
  network-executability claim.
- Lossy storage, binary charge/discharge modes, degradation state, or standby
  loss.
- Generator or HVDC ramp constraints.

## 10. Completion record

- Added device-configurable terminal equality and zero-shortfall constraints.
- Added two-sided linear/quadratic deviation costs and one-sided
  linear/quadratic shortfall costs using `cp.neg`.
- Kept hard constraints and soft costs mutually exclusive.
- Composed the storage-owned horizon constraint and terminal-cost hooks
  through AC, lossy DC, and single-node DC, for single- and multistep builds.
- Added signed terminal-deviation and aggregate terminal-cost results.
- Added a runnable single-node/AC comparison example.
- Added behavioral acceptance tests for terminal-weight tradeoffs, hard-policy
  infeasibility, reserve-floor surplus, no-policy compatibility, and
  exactly-once horizon-cost accounting across all three formulations.
- Documented hard-policy infeasibility and terminal-weight units/scaling.
- Verified Ruff and the full test suite: 984 tests passed.
