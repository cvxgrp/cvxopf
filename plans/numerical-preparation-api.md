# Typed numerical preparation design

## Approved AC cost-coordinate extraction

The standalone production policy now includes opt-in ``cost_coordinates=False``.
The owner approved the AC method after the
[AC qualification](../experiments/ac_cost_qualification/REPORT.md); the
[convex comparison](../experiments/convex_cost_qualification/REPORT.md) explicitly
refrains from adopting cost rescaling for SOCP, lossy DC and copper plate.
Those formulations reject this field when enabled. No default or hierarchy
change is included; per-layer prepared execution remains M21 work.

Component adapters declare typed coordinate terms alongside their original
stage rates. A solve-local graph substitutes cost-valued cycling/shedding
variables, leaving the public physical graph, Parameters, constraints and result
schema intact. Rates are time-integrated once; terminal and unrelated objective
terms remain untouched. Zero weights use identity scaling; current Parameter
weights are snapshotted anew for each solve. No experiment imports or global
monkey-patching are promoted.

Native objective and reconstructed physical objective are both retained in
immutable preparation evidence, together with scale/start maps, signed cycling
epigraph excess, non-cancelling absolute slack, limits and warning flags.
Cycling discrepancies use ``1e-4 + 1e-6*abs(physical cycling cost)`` as an
advisory diagnostic, not a rejection gate. Explained cycling slack is excluded
from the separate unexplained total-accounting discrepancy. A public
``CostAccuracyWarning`` retains native status and returned physical values.
These diagnostics are not a scientific acceptance policy or feasibility proof.

General regressions cover algebra, physical restoration, forced shedding,
zero weights, PWL/terminal costs, repeated solves and failure cleanup, plus
both baseline and prepared AC paths against unchanged PYPOWER case9,
case9-PWL, case14 and case57 fixtures. Fixture tolerances and historical
qualification evidence remain unchanged. Regression testing does not claim
new Tracy qualification or authorization to resume E3/Stage D.

Prepared 2026-10-06 on `socp`, following preservation commit `eb511da3f` and the
[approved transfer plan](socp-conditioning-integration-transfer.md). This is the
approved API/protocol design with implementation progress recorded
below. Qualification and execution remain separately authorized gates under the
[qualification protocol](../experiments/numerical_preparation/QUALIFICATION_PROTOCOL.md).

## Assembly implementation checkpoint

The committed assembly checkpoint `ea2d8939c` introduced the immutable public
policy, formulation validation, component-owned normalized device limits,
explicit exact-box identities, and their propagation through all standalone
assembly modes. At that checkpoint every enabled policy raised
`NotImplementedError` from `OPFBuild.solve()` before a numerical call. Public
variables, result projections and the disabled solver path remain intact.
Direct `build.prob.solve()` bypasses the supported preparation boundary.

The solver-boundary checkpoint adds exact coordinate maps, a build-local stock
CLARABEL bridge with optional joint scaling, and a stock IPOPT oracle wrapper
at the shared verified-start boundary. It restores original-dimensional
solutions before CVXPY inversion and publishes immutable native and restoration
diagnostics through `OPFBuild.preparation_evidence`. Enabled standalone solving
is available through these boundaries; defaults remain disabled. Unit and
regression tests are not the bounded scientific qualification matrix.

Qualification, prospective E3 changes, and execution remain subsequent gates.
Prepared hierarchy remains rejected before any layer build, including
when mutable hierarchical options have changed after input construction;
its layer-specific policy work remains in Milestone 21. The existing streaming
hierarchy fingerprint omits only the new disabled field to preserve frozen
physical-input hashes and rejects enabled preparation rather than silently
hashing it as baseline. Preserved investigation code, protocols, reports,
inventories, raw evidence, and original E3 are unchanged. The isolated
investigation checkout remains retained.

## Public selection and unchanged baseline

Add an immutable, exported `NumericalPreparation` value, selected through a new
`OPFOptions.numerical_preparation` field with a disabled default:

```python
@dataclass(frozen=True)
class NumericalPreparation:
    normalize_device_limits: bool = False
    exact_fixed_boxes: bool = False
    canonical_scaling: Literal["none", "joint5"] = "none"
    cost_coordinates: bool = False
```

Validate actual booleans and the closed scaling literal. No configurable number
of passes, scale limits, objective divisor, tolerance, or plugin registry.
Preparation is a build-time representation choice; changing it requires a new
build. The disabled path continues through the existing `OPFBuild.solve()`
without changes to solver selection, graph construction, starts, or restoration.
All initial defaults remain disabled, even after qualification; any proposed
default change needs an explicit formulation-specific disposition and review.

| Formulation | Normalized device limits | Exact fixed boxes | Joint canonical scaling |
| --- | --- | --- | --- |
| SOCP | Storage/ND unit-radius SOCs | Pg and ND real power | Optional `joint5` |
| Lossy/single-node DC | Not applicable; reject `True` | Pg and ND real power | Optional `joint5` |
| AC | Storage/ND smooth squared inequalities | Pg and ND real power | Reject anything except `none` |

The options compose independently where applicable. No policy changes physical
ratings, units, objective weights, shedding, terminal constraints, solver
tolerances, or formulation. Convex preparation initially supports stock CLARABEL
only; AC preparation supports the existing IPOPT path only. Reject unsupported
solver paths, implicit multi-start execution (`best_of`), prepared warm starts,
and `accept_unknown` before any numerical call. The disabled path retains its
existing supported solver/options behavior.

Prepared hierarchical execution is explicitly deferred. The hierarchy currently
passes one `HierarchicalInputs.options` object to both DC and AC builds; the
formulation-specific policies above cannot be propagated unchanged across those
layers. Reject any enabled preparation field at hierarchical entry, before
building or solving an outer layer. Do not silently discard inapplicable fields.
The disabled hierarchy remains unchanged. Introducing independently selected
layer policies belongs to
[Milestone 21](milestone-21-configurable-hierarchy.md#35-numerical-preparation-per-layer),
with its own API decision and bounded qualification, not this standalone
extraction or its 25-call matrix.

## Typed ownership, boxes, and coordinates

Use the existing `_temporal_assembly.py` authority: `PreparedBoxBounds`,
`prepare_box_bounds`, `VariableBoxFamily`, and `box_representation_decision`.
Do not introduce another temporal broadcaster, box-policy table, device schema,
or variable-name classifier. Generator boxes remain per-unit internally;
storage/ND remain MW/MVAr/MWh. Generator cost still receives MW.

Add a private frozen `ExactBoxBinding` containing the family, original variable
ID and shape, copied prepared bounds, and identities of its defining equality
constraints. Exact coordinates are selected only where finite prepared lower
and upper faces are exactly equal under ordinary floating-point equality
(`lower == upper`), never by a proximity tolerance. NaN/invalid boxes fail at
the existing validation boundary. Near-fixed boxes remain two-sided boxes.
Only Pg and ND real-power families participate; do not eliminate Qg, voltage,
SoC, reference angles, shedding, or arbitrary user equalities.

Extend the operating-hook return contract with a typed
`OperatingSetContribution(constraints, exact_boxes=())`; carry that metadata
through existing step/vectorized contributions to private `OPFBuild` bindings.
Adapters construct bindings after builder-owned variables exist. A shared box
emitter consumes prepared bounds and the existing representation decision:

- Disabled: emit exactly the current constraints/leaf attributes.
- Enabled: express the selected exact entries as defining equalities, retain
  non-fixed bounds in their currently qualified representation, and attach the
  equality identities. For DC leaf boxes, remove only the fixed entries from
  leaf bounds; their new explicit equality is the single bound authority.
- Preserve other component inequalities and every user coupling constraint.
  Select canonical equality rows through inverse-chain constraint identity,
  never a variable name, positional guess, or arbitrary one-coefficient row.

Resolve variable slices through current reduction inverse data in Fortran order,
including introduced auxiliary coordinates. A private immutable
`FixedCoordinateMap` owns full/free indices, fixed values, and kept/dropped row
maps. It validates disjoint/exhaustive indices, dimensions, finite values, and
round trips. Stored arrays are defensive, read-only copies. With no selected
coordinates it is an identity map. Reject a completely empty solver coordinate
space explicitly rather than fabricating native convergence. Selected box
faces are build-time numeric data; parameterized selected boxes are unsupported
initially. Other existing Parameters may change between solves: recanonicalize
and construct fresh maps/evidence for every solve, with no prepared solver cache.

Normalize capability sets in their component modules, selected by adapter
context. SOCP uses direct `cp.SOC(ones, [p/S, q/S], axis=0)` constraints
without norm epigraph variables, preserving the investigated canonical cone
representation; AC uses
`square(p/S) + square(q/S) <= 1`. Positive finite `S` remains validated. All
device constraints remain DCP-valid; only the AC network is DNLP. `storage.py`
continues to import no `cvxopf` modules: helpers receive a plain boolean rather
than importing the preparation policy. DC gets no artificial capability cone.

Public CVXPY variable identities, shapes, component ownership, and physical
result fields stay intact. Fixed-coordinate reduction happens at the solver
boundary, not by replacing public variables with expressions or rebuilding the
model. Preserve named costs and existing result projections.

## Convex preparation and restoration

`_numerical_preparation.py` owns the policy, typed bindings/maps, and immutable
attempt evidence. `_convex_preparation.py` owns canonical mathematics and a
build-local stock-CLARABEL bridge called by `OPFBuild.solve()`. Production imports
no experiment code and modifies no process-global solver method or registry.

Obtain fresh `P,A,b,c`, cone dimensions, solving chain, and inverse data through
the existing canonicalization backend. Keep the symmetric quadratic matrix for
transformation calculations; apply the CLARABEL adapter's upper-triangle
delivery convention only at the solver boundary. In `x = E*y + f`:

```text
Pfree = E.T * P * E
cfree = E.T * (c + P*f)
bfree = b - A*f
offset = c.T*f + 0.5*f.T*P*f
```

Remove only defining equality rows proved redundant by the typed bindings.
Other rows use the same substitution and remain in the original cone order.
Carry the reduction offset separately from CVXPY's original objective constant.

For `joint5`, use exactly five simultaneous square-root infinity-norm passes
from the preserved `joint_scaling.py` mathematics. Variable maxima combine
`P`, columns of `A`, and `abs(c)`; constraint maxima combine rows of `A` and
`abs(b)`. Share one maximum across each SOC block, leave zero maxima unchanged,
and clip cumulative positive `D,R` to `[1e-6, 1e6]`. Deliver
`D*Pfree*D`, `R*Afree*D`, `R*bfree`, and `D*cfree`. No global objective multiplier.
Initially allow zero, nonnegative, and SOC cones only; other cone types fail
preflight rather than being silently rescaled. Reject nonfinite transformed
data or maps before solving.

Call the stock adapter with cache disabled. Retain native status, primal/dual
costs, gaps, residuals, iterations, time, and transformed vectors separately.
Restore `y = D*xhat`, retained `s = shat/R`, and `z = R*zhat`; insert fixed
coordinates and zero slacks for removed equalities. Reconstruct removed equality
multipliers from original stationarity, clearly labelled reconstructed rather
than native. Restore both native cost offsets, then feed the original-dimensional
solver result through the original adapter inversion and CVXPY inverse chain.

Publish normal CVXPY statuses and physical results only after successful shape,
finite-map, and restoration checks. Original-coordinate primal residuals,
stationarity, complementarity, cone membership, and objective reconstruction are
numerical evidence, not feasibility/optimality certificates. For non-solution
statuses, preserve native diagnostics and normal failure status but do not
publish transformed vectors as physical primals/duals or reinterpret a scaled
infeasibility certificate. Clear stale values/evidence on failures and exceptions;
unavailable restoration checks stay unavailable, not passed.

## AC preparation and verified starts

`_ac_preparation.py` shares only typed fixed-coordinate maps; it does not apply
convex scaling or introduce general Jacobian/Hessian equilibration. Compose it
at the existing build-local IPOPT boundary used by
`_hierarchical_solver.py::_solve_ac_with_verified_x0`, not a second unverified
execution path. Ordinary prepared AC `build.solve()` reuses that boundary
helper, while hierarchical AC retains its disabled preparation behavior and
existing verified capture. Sharing the boundary is not authorization to enable
preparation through the currently shared hierarchical options.

Reuse `_ac_start_mapping.py` and the existing complete-start/canonical-layout
capture. Preserve original variable/constraint/Parameter identities. Capture
the complete assigned canonical start (including auxiliaries) before reduction.
Set selected exact coordinates to their defining values deterministically and
record that adjustment, then select free coordinates. Capture full assigned,
full adjusted, and actual reduced IPOPT `x0`, with layouts and round-trip checks.
Free start coordinates do not change; the initial SoC boundary is not perturbed.
An explicit start inconsistent with an exact box is adjusted transparently,
not treated as a different random initialization. Qualification uses the same
physical start with exact coordinates already satisfied for each matched arm.

The local oracle bridge evaluates original objective/constraints at `E*y+f`.
Its derivatives are `E.T*grad`, kept-row/free-column Jacobian, and
`E.T*H*E`. Filter and remap the corresponding Jacobian/Hessian sparsity indices
alongside values; do not just shorten dense arrays. Insert zero multipliers for
dropped rows while computing the reduced Hessian. Retain all other constraints
and bound faces. Restore original primal values before the existing IPOPT/CVXPY
inversion. Stock IPOPT currently publishes no public constraint duals; preserve
that contract. Native multipliers and reconstructed fixed-row multipliers may
be retained as diagnostic evidence only, with bound multipliers included in the
stationarity accounting.

Isolate CVXPY-private adapter access at these two solver boundaries, check its
required capabilities, and test against the installed project environment.
Reuse the stock IPOPT option resolution and derivative oracles; do not establish
another solver-default table. Initially require exact Hessians for prepared AC;
reject other Hessian modes before execution. Qualification requires native
IPOPT status 0, not merely CVXPY `optimal_inaccurate` or feasible-point status.
Ordinary public status mapping remains unchanged; qualification is a separate
acceptance decision, not a new promise of a global AC optimum.

## Implementation tests and later study integration

Add new production-focused tests separate from transferred historical tests:

- Policy validation, disabled equivalence, component DCP ownership, absent
  devices, exact/near-fixed masks and unit correctness; reject enabled
  hierarchical preparation before any layer build or numerical call.
- Single-step, vectorized and stepwise shape/order/identity coverage; no-fixed
  identity; objective constants/cross terms; scaling/cone/restoration algebra.
- Equality dual reconstruction with user coupling and bounds; stale cleanup,
  unsupported statuses/settings, repeated solves and changed Parameters.
- AC complete/adjusted/reduced starts, auxiliary coordinates, SoC boundary,
  finite-difference oracle checks and sparse derivative index/value alignment.
- Stubbed stock solver success/failure paths plus existing component, SOCP,
  hierarchy/start and Tracy runner regressions. No optional commercial/native
  dependencies or imported experimental monkey-patching.

Run proportionate full-suite checks and the separately approved bounded
qualification before any default proposal. Record per-formulation qualified,
not-qualified, or incomplete disposition; do not treat unit tests as numerical
qualification or one timing sample as a speedup result.

Only afterward prepare prospective E3 code/configuration under the original
plan: SOCP-specific preparation and acceptance settings, unchanged component
and physical gates, replay validation of maps/native evidence, separate DC/AC
dispositions, and a new `results/e3_prepared` root. Do not cross-source resume or
rewrite original E3. No fresh execution binding/start/launch is created until a
clean reviewed execution commit and separate authorization. Stage D stays held;
the isolated investigation checkout stays retained.
