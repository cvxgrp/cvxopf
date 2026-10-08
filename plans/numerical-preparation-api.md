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

The production-path checkpoint is described in
[the Tracy verification protocol](../experiments/ac_production_verification/PROTOCOL.md):
four matched pairs (ordinary T=3, forced-shedding T=3/T=6/T=24) through public
`build.solve()`, with the cost-coordinate flag off/on. It preserves cycling
warnings, independently replays native/physical accounting and restoration,
and uses fresh supervised evidence. The approved run at `31f8fb0013a9a19109a0294752c50037d075eed8`
finalized all eight attempts: seven accepted and the cost-coordinate forced
T=24 arm stopped at the 180-second wall limit. Earlier experimental qualification
of that same physical case converged; this timeout does not establish infeasibility
or invalidate the earlier solution.

The owner approved preparation of the
[two-arm objective-assembly diagnostic](../experiments/ac_objective_assembly/PROTOCOL.md)
to investigate the discrepancy. Both arms retain the production coordinate map
and public solve path; only experiment-local objective assembly changes.
The comparison changes summation grouping and its induced canonical ordering
together, so it cannot separate their effects. Inputs, starts, physics, solver
settings and acceptance checks remain matched. Historical evidence stays
unchanged. The approved diagnostic at `40692e9022ba9a0c0d199e1a6190339cb363fac1`
finished both forced-shedding T=24 attempts: hourly timed out at 180 seconds;
component-first converged in 132.94 worker seconds, with a cycling warning and
the same reconstructed physical cost as the historical successful solution.
This is localized evidence, not a universal objective-assembly rule.

The [four-formulation comparison](../experiments/objective_assembly_qualification/PROTOCOL.md)
tests hourly/component-first pairs across AC, SOCP, lossy DC
and copper plate, ordinary T=3 and forced T=3/T=6/T=24. AC keeps its approved
production cost coordinates; the convex formulations keep original economic
coordinates and their approved preparation. The approved launch at
`9032c19c83aff75255d3c2d1d574b061297b11de` stopped before any worker or optimizer
started because its protocol envelope did not match the shared supervisor.
`results/qualification_001` remains unchanged, with failure-record hashes in
the comparison protocol. The correction restores that handoff contract and
adds a non-solving real-supervisor regression. After review, clean commit
`3fb7489d021a458ec60aa5bd66737c84210adc98` and owner launch approval,
`qualification_002` finalized 32 attempts: 25 accepted, six SOCP audit
rejections and one AC timeout, with 21 advisory cycling warnings. It retains
31 result archives, 31 native archives and 31 completion manifests, plus 32
finalized supervision records; the timeout has no completed result archive.

| Formulation | Hourly accepted | Component-first accepted | Other outcomes |
| --- | --- | --- | --- |
| AC | 3/4 | 4/4 | Forced T=24 hourly timeout |
| SOCP | 1/4 | 1/4 | Forced T=3/T=6/T=24 audit rejection in both arms |
| Lossy DC | 4/4 | 4/4 | None |
| Copper plate | 4/4 | 4/4 | None |

The initial invocation stopped after 31 arms because thermal telemetry was
unavailable. The owner separately authorized the final copper-plate arm; its
start/finish and `report-final-arm.json` are supplemental records in the same
ignored execution directory. Original invocation records remain unchanged.
The final arm retained the original numerical settings and passed acceptance.
Total sampled worker time was 587.77 seconds of the 5760-second budget.

The AC forced T=24 component-first arm converged in 137.84 worker seconds,
while hourly hit the 180-second limit. Its retained physical trajectories
matched the earlier component-first diagnostic exactly, with physical cost
11852950486.055317 and cycling slack 0.004628212372312357 (an advisory warning).
There is no two-accepted-arm cost/trajectory comparison for this timed-out pair.
Shorter AC cases showed no timing advantage; the convex comparison does not
support changing their defaults. Timeout and numerical/audit rejection are
not proofs of infeasibility. E3 and Stage D remain held.

## Named AC objective assembly checkpoint

The owner approved the reviewed API concept: retain `"hourly"` as the
compatibility default and add opt-in `"component_first"` to immutable
`NumericalPreparation`. Initially, the new mode requires standalone vectorized
AC with `cost_coordinates=True`; unsupported formulation/assembly combinations
fail before optimization. Prepared hierarchy remains M21 work. This field
means objective summation grouping, not temporal assembly or interval duration.

Production vectorized assembly now retains complete ordered stage/boundary
contributions and typed cost-coordinate ownership. The private solve graph
sums ordinary component costs, priced cycling leaves, priced shedding leaves,
then terminal costs. Generator constants/PWL costs and HVDC remain intact;
duration is applied exactly once and terminal costs are outside integration.
Zero-priced entries retain identity coordinates and no priced cycling epigraph.
Unsupported partial coordinate ownership or a caller-replaced objective raises
explicitly rather than silently omitting a cost. There are no experiment imports,
objective-name scans, global patches, retries or automatic representation changes.

The build snapshots the option; success and native-failure preparation evidence
record it. The original physical graph remains the public accounting authority,
with unchanged advisory warning semantics. Regression coverage compares the
production canonical layout, complete start, objective and constraint evaluations
against the successful experimental construction without solving Tracy. It also
checks integration, zero/parameter weights, absent devices, HVDC/PWL/terminal
costs, restoration, warnings, failure cleanup, unchanged PYPOWER fixtures and
unsupported combinations. These tests are not a new scientific qualification.

After implementation review and a clean owner commit, a separately authorized
production-path Tracy forced T=24 qualification remains required. Another full
32-arm matrix is not automatically necessary. The evidence supports offering
the complete qualified AC configuration as an opt-in option, not a universal
speedup, a convex policy change or a production default change.

Documentation-site follow-up requested by the owner: organize getting-started
examples, formulation/assembly support, numerical options, economic warnings,
result interpretation and an experiment-evidence index into navigable docs.
Site tooling, hosting and publication are a separate task; no site is built or
published in this checkpoint. The README now carries a concise selection guide.

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
    objective_assembly: Literal["hourly", "component_first"] = "hourly"
```

Validate actual booleans and the closed scaling and objective-assembly literals. No configurable number
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
