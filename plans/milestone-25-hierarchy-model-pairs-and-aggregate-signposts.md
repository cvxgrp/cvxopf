# Milestone 25 — Model-independent hierarchies and aggregate signposts

**Status:** draft for review; no implementation or numerical execution authorized

**Date:** 2026-10-01

**Foundation:** M17's validated hierarchy and M16+ shared component assembly.
This plan specializes and extends [M21](milestone-21-configurable-hierarchy.md);
it does not introduce a competing hierarchy engine or declare M21 complete.
M11 gates configurations using SOCP, but not the initial same-model DC work.

## 1. Purpose and relationship to existing work

Separate two decisions that the current lossy-DC-to-AC controller combines:

1. Which supported network model runs at each layer?
2. What obligation does an upstream plan impose on the downstream model?

Support compatible registered model pairs, including the same formulation at
both layers, and add an aggregate storage-energy equality as an alternative
to device-by-device terminal signposts. The new equality is a first explicit
cross-device coupling class, not another operating constraint on each battery.

M21 already proposes typed layer adapters, retained handoffs, and eventual
three-layer composition. M25 supplies the concrete two-layer implementation
and qualification scope for model-pair selection, same-model consistency, and
aggregate obligations. Reuse any M21 implementation available when work starts;
otherwise implement its necessary two-layer foundation once. Three-layer
scientific comparisons and M21's remaining completion gates stay with M21.
Reconcile roadmap cross-references at the implementation checkpoint rather than
maintaining two independent orchestration implementations.

The current M11 SOCP build proceeds independently. This draft does not expand
that build into hierarchy execution, change its network mathematics, or
authorize another Tracy experiment.

## 2. Model-pair contract

Model choice is independent at each layer. Do not encode a required ordering
of physical fidelity, convexity, or formulation identity. Examples include:

| Upstream model | Downstream model | Role |
| --- | --- | --- |
| `lossy_dc` | `ac` | Existing M17 compatibility baseline |
| `singlenode_dc` | `lossy_dc` | Coarse planning to networked realization |
| `singlenode_dc` | `socp` | Copper-plate planning to relaxed AC network |
| `lossy_dc` | `lossy_dc` | Same-model, shorter-horizon consistency control |
| `singlenode_dc` | `singlenode_dc` | Small transparent consistency control |
| `socp` | `socp` | Same-model relaxed-network control after M11 |
| `socp` | `ac` | Relaxation planning to independently checked AC realization |

These are reference configurations, not a hard-coded pair whitelist. A pair
is supported when both registered adapters declare the required build, state,
handoff, result, solver, and audit capabilities. Future registered formulations
must not require another bespoke controller. This is not an arbitrary-problem
or third-party plugin API; unsupported capabilities fail before solving.

Each layer declares its formulation, horizon, temporal assembly, solver options,
acceptance tolerances, and initialization/recovery policy. Repeated formulation
IDs are valid; layer IDs remain distinct. Identical formulation names alone do
not establish identical mathematical problems or objectives.

Initial scope uses a common interval grid and delta, a fixed accepted upstream
plan, receding downstream windows, and one executed interval per window. Reject
unsupported resampling, differing deltas, or advancement policies rather than
silently interpolating states. Final windows truncate at the upstream horizon.
Adaptive horizons, repeated upstream replanning, and generalized strides are
separate extensions.

Convex layers use their supported convex solver and audit path. AC retains its
DNLP path and reviewed causal recovery. Do not apply AC's nine-slot recovery
tree, voltage initialization, or IPOPT evidence requirements indiscriminately
to convex layers. Conversely, do not remove them from M17 compatibility mode.

## 3. Two endpoint-signposting policies

Let s(k) be the vector of individual storage energies in MWh at global boundary
k, with columns aligned to stable device IDs. A window begins at t, spans W
intervals, and ends at e = min(t + W, T). Its initial state is always the actual
individual realized state, not the upstream prediction at t.

### 3.1 Individual equality — compatibility default

For each device i:

\[
s_i(e)=s_i^{\mathrm{outer}}(e).
\]

Preserve the current per-device terminal policy, target selection, and audits.
Existing public defaults must not silently become aggregate targets. Existing
soft or target-free recovery policies retain their established meanings;
introducing fleet equality does not implicitly create a fleet soft-penalty mode.

### 3.2 Fleet sum equality — new explicit opt-in

\[
\sum_{i\in\mathcal S}s_i(e)
=\sum_{i\in\mathcal S}s_i^{\mathrm{outer}}(e).
\]

The initial supported fleet is the complete identity-matched storage collection
shared by the two layers. Sum absolute energy in MWh, not SoC fractions,
capacity-weighted fractions, or unweighted average percentages. Do not silently
drop unavailable devices or treat bus IDs as device identities. Multiple
colocated devices remain distinct. Selecting fleet equality without storage is
an input error, not a vacuous successful handoff.

All individual capacities, initial states, dynamics, real/reactive operating
sets, and network constraints remain in force. Aggregate equality releases the
spatial allocation of final energy; it does not aggregate the physical devices
or replace them with one fictitious battery. It does not impose each outer
device target in addition to the sum.

The policy applies at every downstream window endpoint, including the final
truncated window. If a study also requires a distinct final per-device
obligation, it must declare that combination explicitly. It is not an implicit
property of fleet signposting. Inherited device terminal constraints/costs must
be inventoried: the hierarchy replaces only its own generated signposts, never
silently erases caller obligations or stacks a hidden per-device penalty onto
an aggregate-only policy. Unsupported or ambiguous combinations are rejected.

For ideal storage, positive b denotes discharge and

\[
s_{i,k+1}=s_{i,k}-\Delta t\,b_{i,k}.
\]

At W=1, individual equality fixes every battery's active power, while fleet
equality fixes only its sum. Across multiple steps, fleet endpoint equality
fixes net fleet energy change, leaving both timing and spatial allocation free.
These identities are tests for the current ideal-storage model, not assumptions
to impose on future lossy storage.

## 4. Cross-device coupling architecture

The common mathematical form is

\[
A\,s(e)=z,\qquad z=A\,s^{\mathrm{outer}}(e).
\]

Individual equality corresponds to A = I; fleet equality to a single all-ones
row. This shared form should guide implementation without requiring an arbitrary
user-supplied matrix API. The first public policies are individual and fleet
equality. Named regional groups are an extension point, not a completion gate.

### Ownership

- **Storage module:** authoritative device parameters, state transitions,
  bounds, operating constraints, and costs. No dependency on the hierarchy.
- **Shared cross-device coupling assembly:** resolves declared IDs to existing
  variable views and constructs the affine boundary relation once. Reusable by
  standalone multistep builds and hierarchy-built windows; not duplicated in
  every network builder. It neither creates substitute device variables nor
  reconstructs their dynamics.
- **Hierarchy:** chooses the policy, boundary, participating identities, and
  numeric target from an accepted upstream result; binds the coupling into the
  downstream build.
- **Audit:** independently computes the numeric coupling residual from extracted
  individual states, without calling the CVXPY constraint's violation method.

Before coding, inspect the existing horizon hooks and `coupling_constraints`
boundary and select the smallest typed extension that can bind already-created
variables. Do not introduce a parallel component framework or a generic symbolic
constraint language. Public names and exact module placement are design decisions
for the first reviewed implementation slice, not implied by this draft.

Coupling contributions must pass DCP checks directly. Only AC network physics
may use DNLP. Bind vectorized and supported stepwise state views through shared
assembly, preserving single-step/T=1 shape and boundary conventions.

### Typed handoff evidence

Retain at least:

- Source/destination layer IDs and formulations, source plan identity, and policy.
- Ordered device IDs, participating fleet, boundary index, horizon, and delta.
- Individual upstream target vector and derived aggregate target in MWh.
- Actual individual starting state and resulting endpoint state.
- Enforced residuals and their declared tolerances; individual deviations remain
  diagnostic, not rejection criteria for fleet equality.
- Solver termination, independently audited acceptance, and executed action ID.

Keep state transfer separate from target transfer: the next window receives
every individual realized post-action SoC even when the obligation is a sum.
Archive/checkpoint/resume must retain that same identity-aligned state. A fleet
total cannot reconstruct missing individual state.

## 5. Same-model identity and optimal-substructure gate

The intended sanity check is that shortening an otherwise identical convex
problem should not manufacture an improvement over its global optimum when
the necessary boundary information is preserved. Define the claim precisely.

### 5.1 Fixed-window consistency

Given a globally optimal upstream trajectory, extract a window with:

1. Identical model, network/device data, costs, time grid, and constraints.
2. Exact upstream individual state at both endpoints.
3. Every other intertemporal condition needed to splice the window into the
   unchanged prefix/suffix. Storage SoC alone is sufficient only when no other
   cross-boundary coupling exists. Future ramping/commitment states, cumulative
   budgets, or caller couplings require explicit treatment or rejection.
4. The same restricted objective: integrated stage costs, applicable boundary
   costs only, and no newly introduced terminal penalty or repeated whole-horizon
   terminal cost. A terminal cost is constant for this comparison only when its
   arguments are fixed by the boundary contract.

The upstream restriction is feasible. A strictly better downstream segment
would improve the full upstream solution by substitution, contradicting global
optimality. This argument does not require a strictly convex objective.

Numerical tests compare audited feasibility, named costs and total objective
within predeclared solver-gap-aware tolerances. Retain available primal/dual
evidence; numerical solver success alone is not an exact global certificate.
Test coordinate equality only for quantities shown to be uniquely determined,
or in an explicitly qualified unique-solution fixture. Do not require equality
of nonunique branch flows or intermediate storage paths, and do not add a hidden
regularizer merely to make a test pass.

### 5.2 Rolling identity is a stronger claim

A local solve can choose a different equally optimal path inside a fixed window.
Executing its first step may move the realized state away from the original
outer trajectory. The next window then has a different initial condition, so
the original restricted trajectory is no longer automatically a feasible
reference. Local fixed-window equivalence does not by itself establish exact
whole-rollout identity or recursive feasibility.

Use a small fixture with an analytically identified unique relevant trajectory
to qualify full rolling identity. Separately test nonunique fixtures, reporting
alternate optimal local realizations honestly. Window costs are not added
across overlapping solves; only executed actions contribute realized accounting.

### 5.3 Aggregate equality is not an identity test

Fleet equality allows a different per-device endpoint, which need not splice
into the original suffix. Even with the same convex model, lower window cost
or different dispatch can be a legitimate consequence of weakening the handoff.
The reference outer restriction should still be feasible when started at its
own individual state, but its cost need not be optimal for the enlarged set.

For nonconvex AC-to-AC pairs, retain feasible-reference and local-result checks;
do not claim the globally optimal substructure conclusion from a local IPOPT
result. Same-model SOCP consistency concerns the relaxation, not AC feasibility.

## 6. Scientific meaning and limitations

The comparison asks whether downstream network physics needs a different
spatial allocation of storage energy, rather than a different total reserve.
Report fleet endpoint error and individual deviations separately, along with
dispatch, shedding, curtailment, losses where physically modeled, objective
components, solve effort, recovery, and later-window outcomes.

Aggregate storage energy is not necessarily deliverable at the location of
future demand. Fleet equality does not promise adequate locational reserves,
recursive feasibility, reduced cost over the full horizon, or improved runtime.
Two independent shards with equal total boundary energy cannot be merged unless
their individual boundary states also match. This milestone does not modify the
existing annual shard manifest or authorize aggregate-only shard joins.

An accepted SOCP layer is an accepted relaxed-model result, not an AC execution
certificate. Only a matched valid relaxation with independently checked
infeasibility evidence can certify an AC negative; model ordering alone supplies
no containment or bound claim. Existing lossy DC is not presumed to relax AC.
Retain unknown/local-solver-failure outcomes rather than promoting them to proof
of physical infeasibility. These semantics follow M11 and the relevant adapters.

## 7. Implementation sequence

| Slice | Deliverable and stop point |
| --- | --- |
| A — Inventory and contracts | Map M17/M21 ownership, implemented formulation capabilities, temporal coupling, and compatibility fixtures. Freeze typed layer-pair and coupling contracts. |
| B — Model-independent two-layer execution | Reuse/refactor the existing controller behind capability-based adapters. Preserve the default lossy-DC-to-AC path and formulation-specific solve/recovery behavior. |
| C — Same-model controls | Qualify single-node and lossy-DC fixed-window equivalence; distinguish unique rolling identity from nonunique local solutions. |
| D — Cross-device endpoint coupling | Add standalone and hierarchical fleet equality through shared assembly, identity alignment, policy evidence, and independent residual audits. |
| E — SOCP pair coverage | After M11 acceptance, qualify copper-plate-to-SOCP, SOCP-to-SOCP, and SOCP-to-AC with correct relaxation/physical semantics. |
| F — Bounded comparison and documentation | Run separately declared small comparisons of individual versus aggregate policies, document results and limitations, and reconcile M21/roadmap status. |

Each slice stops for review before broadening scope. Do not delete the dedicated
M17 compatibility path before regression evidence supports replacement. No new
parallel scheduler, helper-racing policy, or recovery-policy redesign is needed.

## 8. Verification and execution gates

Use public small networks and synthetic device cases for mandatory tests; private
Tracy data is not a CI dependency. Required coverage includes:

- Unchanged M17 individual-target behavior, outcomes, costs, causal starts, and
  attempt provenance, including recovery and failure paths.
- Distinct layer IDs with equal formulation IDs; supported capability-based
  pairs; early rejection of unsupported solvers, states, or handoffs.
- Exact slicing of interval data and boundary targets, non-unit delta, W=1,
  multiple windows, and final truncation.
- Same-model restricted feasibility and objective equivalence under the stated
  assumptions, plus a unique-fixture rolling identity check.
- Nonunique optimum and omitted cross-boundary-coupling negative controls so
  unsupported identity claims do not become test expectations.
- Two-battery examples with the same fleet energy but different individual
  endpoint allocations: accepted by fleet policy, rejected by individual policy.
- Independent target reconstruction, device-order permutations, colocated
  devices, missing/duplicate identities, malformed shapes, and nonfinite values.
- Individual physical limits and dynamics remain enforced under fleet equality;
  no hidden per-device signpost or inherited target penalty survives the switch.
- Complete individual state advancement and resume, despite aggregate targets;
  failed/unaccepted attempts never supply executed actions.
- DCP checks on coupling contributions and correct solver routing across supported
  formulations and temporal representations.
- Formulation-specific audits, stable unsuccessful-result handling, and explicit
  distinction between SOCP relaxation feasibility and AC feasibility.

Before any numerical qualification, freeze exact fixtures, solver versions and
options, tolerances for each residual and cost comparison, per-solve/total wall
and RSS budgets, and stop rules in a reviewed protocol. Aggregate tolerances
have MWh units and a stated scale convention; do not silently multiply a
per-device tolerance by fleet size or accept cancellation of individual physical
violations. Report raw per-device deviations alongside the aggregate residual.

Record construction, canonicalization, solve, audit, and orchestration timing
separately where observable. Scientific comparisons retain the same exogenous
inputs, device physics, costs, initial state, and solver configuration; the
signposting policy is the intentional treatment. Freeze scripts before execution
and retain provenance and raw results under the experiment's own results folder.
There is no annual scaling requirement for this milestone.

## 9. Deferred scope

- Regional/group sum policies, weighted energy couplings, reserve envelopes, and
  aggregate soft penalties beyond the initial individual/fleet equality options.
- Charge/discharge **sign** schedules, whether individual or aggregate. These
  mean affine direction constraints, not fixed power levels; their deadband and
  idle semantics need a separate policy decision.
- New lossy storage physics, unit commitment, or reactive regularization.
- Automatic model selection, adaptive horizons/strides, resampling, automatic
  repair of infeasible targets, and guarantees of recursive feasibility.
- Arbitrary external formulations, a dynamic plugin system, or an unrestricted
  cross-device symbolic modeling language.
- Three-layer scientific completion, which remains in M21; modifications to
  historical experiments, shard execution, or another Tracy run.

## 10. Completion criteria

M25 is complete when registered model pairs use one typed two-layer path,
including same-model pairs; fixed-window consistency and appropriately scoped
rolling identity are qualified; individual and fleet endpoint equalities work
through a shared cross-device coupling boundary; individual realized states and
formulation-specific audits remain intact; the M17 default remains compatible;
and the bounded comparisons document both benefits and failures without claiming
aggregate energy is spatially interchangeable. SOCP examples require accepted
M11 capabilities. Completion does not automatically close M21 or authorize an
experiment using a newly recommended policy.
