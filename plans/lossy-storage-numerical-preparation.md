# Lossy storage: numerical-preparation extension

**Status:** proposed design, 2026-11-12. Planning only; no implementation or
numerical execution authorized by this document.

**Baseline inspected:** `main` at `b6edcc72c`, after owner closeout of M18's
first implementation. This is the numerical-preparation follow-up in
[M18 Section 13](milestone-18-lossy-storage.md#13-deferred-follow-up-backlog),
not a reopening of its accepted device physics.

## 1. Purpose and scope

Allow standalone `lossy_storage=` builds to use the existing immutable
`NumericalPreparation` policy through public `build.solve()`. Reuse the current
component assembly, normalization, cost-coordinate, solver-bridge, restoration,
and evidence machinery. Do not introduce a lossy-specific solver path.

The scientific claim is **equivalence of the physical optimization problem and
correct restoration of its results**, not identical floating-point trajectories,
better conditioning in every case, guaranteed complementarity, or a speedup.

Preserve:

- Public engineering units and `ls_*` result meanings; the net injection is
  `p_dis - p_ch` and the rating applies to that net power and reactive power.
- Monthly retention `r = (1 - self_discharge_per_month)**(delta/730)`, the
  affine energy recurrence, explicit nonnegative split powers and SoC bounds.
- Positive throughput rate `lambda*(p_ch+p_dis)`, integrated once by `delta`;
  all terminal constraints/costs remain unchanged, with terminal cost once.
- The 0.001 MW extraction check, `SimultaneousOperationError`, and attached
  unchanged results. Numerical preparation cannot turn split operation into
  an accepted exclusive-mode schedule or suppress the check.
- Existing disabled defaults, ideal-storage and load behavior, solver defaults,
  tolerances, public graph identities and preparation evidence semantics.

Excluded: hierarchy/rollout integration, fallback or rounding, new physical
bounds (including a charge-plus-discharge hull), leaf-bound migration, new
solvers, new preparation-policy fields, tolerance tuning, defaults changes,
new long-running experiments, or modifications to historical study artifacts.

## 2. Supported combinations to qualify

Keep the current formulation/assembly matrix. Support means standalone
single-step, explicit stepwise multistep and vectorized multistep unless noted.

| Policy | AC | SOCP | Lossy DC / single-node DC |
|---|---|---|---|
| Disabled | Exact existing path | Exact existing path | Exact existing path |
| `normalize_device_limits=True` | Rating-normalized smooth squared inverter inequality | Direct unit-radius SOC per device/interval | Still rejected |
| `exact_fixed_boxes=True` | Existing Pg/ND selection only | Existing Pg/ND selection only | Existing Pg/ND selection only |
| `canonical_scaling="joint5"` | Still rejected | Existing stock CLARABEL bridge | Existing stock CLARABEL bridge |
| `cost_coordinates=True` | Existing AC mechanism extended to affine lossy throughput | Still rejected | Still rejected |
| `objective_assembly="component_first"` | Still requires vectorized multistep plus cost coordinates | Still rejected | Still rejected |

Fields compose only in combinations already permitted by
`NumericalPreparation.validate_formulation()` and `validate_assembly()`.
Prepared AC retains IPOPT; prepared convex paths retain CLARABEL. Existing
restrictions on solver options, Hessian mode and prepared warm starts remain.
Assigned primal starts are distinct from unsupported solver warm-start flags.

`exact_fixed_boxes` does **not** select charge/discharge nonnegativity, SoC
initial/terminal equalities, reference angles or arbitrary user constraints.
No new `VariableBoxFamily` or exact-box family is needed. Storage is carried
through the generic transformations, not eliminated by a new heuristic.

## 3. Existing helpers and ownership

Paths in this table are under `src/cvxopf/`.

| Owner | Reuse / narrow extension |
|---|---|
| `storage_lossy.py` | Remains the authority for operating constraints, injections, recurrence, throughput rate, loss reporting and complementarity diagnostics. Add optional normalization arguments to `operating_constraints`; leave its disabled branch structurally unchanged. |
| `storage.py::_normalized_capability_constraints` | Already accepts `cp.Expression`; call it with `p_dis-p_ch`, `b_q`, ratings and the SOC/smooth selector. Broadcast existing native device ratings for time-last arrays; do not duplicate disk equations or move all device models. |
| `_component_adapters.py::_capability_options` | Forward existing preparation flags to the lossy operating hook in both step and vectorized assembly. Existing result ownership/name remains `ls_cost`. |
| `_component_adapters.py::_lossy_storage_*` | Add typed cost-coordinate ownership for AC and remove/refine `_require_disabled_preparation` only as the corresponding capabilities become qualified. All formulation builders remain generic. |
| `_component_adapter.py`, `_component_assembly.py` | Retain typed stage/horizon contributions, `collect_cost_terms`, and complete `ObjectiveCostContribution` coverage. Make only the generic cost-contract changes described below; no named lossy-device orchestration. |
| `_numerical_preparation.py` | Reuse policy validation, `ExactBoxBinding`, `resolve_fixed_map`, `PreparationEvidence`, `prepared_options` and `clear_prepared_result`. Keep exact Pg/ND selection and all unsupported modes unchanged. |
| `_convex_preparation.py` | Reuse `substitute`, `joint_scales`, `scaled_data`, `restore` and `solve_prepared_convex`. There should be no lossy-specific branches in canonical scaling or dual reconstruction. |
| `_ac_preparation.py` | Reuse `ReducedOracles`, `solve_at_verified_boundary` and `solve_prepared_ac`; consume extended typed bindings rather than dispatching on device names. |
| `_cost_coordinates.py` | Extend the existing complete-rate ownership contract for two affine leaves; reuse solve-local graph copying, scale maps, canonicalization, restoration, cost evidence and warnings. |
| `_hierarchical_solver.py::_complete_start` / `_solve_ac_with_verified_x0` | These existing helpers are also used by standalone prepared AC. Extend only initial-boundary completion to `ls_soc[:,0]` from `ls_initial_soc`; preserve supplied free coordinates and the complete native-start verification path. This is not controller support. |
| `results.py`, `socp_diagnostics.py` | Retain existing extraction and independent physical audit/recovery. Exercise them on restored prepared solutions; do not add prepared-only result schemas or reconstruct physics in the solver bridge. |

## 4. AC cost-coordinate design

### Current limitation

`CostCoordinateTerm` currently has one variable, one weight expression and one
rate. `ObjectiveCostContribution` requires that one declared term own the whole
stage rate. Ideal cycling uses an absolute-value cost and shedding uses a
linear cost. Lossy throughput has **two** independent nonnegative variables
but **one** complete stage rate.

Do not attach two current terms to the same full rate: the replacement map
would overwrite that rate and component-first coverage would reject it. Do not
classify charge/discharge as shedding, apply `abs` to them, or scale net power
instead. Those choices either misdescribe the cost, introduce unnecessary
epigraphs, or fail to preserve independent charge/discharge coordinates.

### Proposed narrow typed extension

Evolve the existing private contract, rather than retaining a parallel legacy
and lossy-specific mechanism:

- A small immutable **coordinate leaf** owns its builder variable, weight
  expression and closed mathematical kind: existing `cycling` (absolute),
  existing `shedding` (affine), or new `linear_cycling` (affine throughput).
- The existing **`CostCoordinateTerm` becomes the complete-rate owner**:
  original `rate`, interval-reduction axis, and an ordered tuple of leaves.
- Ideal storage and shedding each supply a one-leaf term. Lossy storage supplies
  one term with ordered charge and discharge leaves, both using the same
  per-device aging weights. Step and vectorized hooks describe the same math.
- Preserve the complete-coverage guard in `ObjectiveCostContribution`: exactly
  one owner of its stage-rate expression. Reject duplicate leaf ownership and
  incomplete/overlapping rate declarations. Do not weaken the guard simply to
  admit two terms. The component must construct the declared full rate from
  the same leaf subtotal expressions, so coverage is established by typed
  construction/identity rather than numeric sampling at one point.
- `transform_cost_coordinates` substitutes every leaf, reconstructs the group's
  complete rate once, and substitutes that rate once. Restoration bindings
  retain the owning term and leaf identities, not variable-name matching.

For each device and interval, define

```text
s = delta * aging_weight
y_ch = s * p_ch
y_dis = s * p_dis
```

The integrated throughput contribution is `sum(y_ch + y_dis)`; physical
variables are restored by division by `s`. The hourly transformed **rate**
contains `(y_ch+y_dis)/delta`, because existing assembly supplies the time
integral. Component-first sums the already cost-valued leaves directly.
Neither path applies `delta` twice. The terminal terms are not part of this map.

Both split leaves remain affine in the solve graph. Their nonnegativity stays
in the original constraints after substitution; introduce no cycling epigraph
for them. They scale by the existing throughput price, not efficiency or
retention. `ls_soc` and `ls_b_q` remain in their existing physical coordinates.

Keep ideal cycling/shedding substitution, coordinate order, canonical layout,
zero-weight identity behavior and evidence unchanged for builds without lossy
storage. Group `linear_cycling` with the cycling contributions in component-first
ordering, preserving component/leaf order; shedding still follows cycling.
The historical qualified ideal-only ordering must remain exactly reproducible.

### Accounting and failure semantics

- Retain both lossy leaf scales and complete physical starts in
  `cost_coordinate_maps`, including native failures where evidence is available.
- Retain the existing `cycling_physical_cost` as the ideal absolute-value
  cycling reference for its epigraph warning threshold. Report affine lossy
  throughput separately (e.g. `linear_throughput_physical_cost`) and include it
  once in the total physical objective. Large lossy throughput must not enlarge
  the tolerance for unrelated ideal-storage epigraph slack. Record zero
  epigraph excess for lossy leaves: there is no auxiliary slack to subtract.
  Only tagged ideal-storage abs auxiliaries contribute to cycling slack.
- Continue retaining native objective, reconstructed physical objective and
  unexplained accounting residual. An affine mismatch must not be absorbed as
  an explained cycling-epigraph discrepancy.
- Preserve existing advisory warning formulas/behavior and native status;
  test mixed ideal/lossy fleets with the same injected ideal epigraph slack and
  increasing affine throughput to verify that the ideal warning is unchanged.
  Numerical evidence is not a physical acceptance certificate.
- Repeated solves must resnapshot weights where the existing contract uses
  Parameters, preserve public graph identities, and clear stale primals on
  failure. No new mutable policy or cached alternate problem is required.

## 5. Equivalence and regression gates

Start with algebra and construction tests, then small repository-owned solved
fixtures. Do not duplicate the earlier broad experimental matrices or depend
on ignored Tracy records. Extend the existing test modules where their
contracts already fit; use a dedicated lossy-preparation module only for the
new cross-component fixtures, not a second preparation test framework.

| Gate | Required tests / existing home |
|---|---|
| Disabled compatibility | `test_numerical_preparation.py` / `test_storage_lossy.py`: omitted vs explicit disabled policy preserves variable shapes/attributes, constraints, objective construction, default solve path and result keys. Cover no lossy devices, empty fleet, lossy-only and mixed fleets. Preserve existing ideal/load canonical-layout regressions. |
| Capability equivalence | Extend normalized-region and direct-SOC tests in `test_numerical_preparation.py`: heterogeneous ratings; single-step, `(ns,1)` and `(ns,T)` layouts; feasible boundary and violated points; charge, discharge, nonzero reactive power and split-operation points. Compare the normalized and original net-power regions. Assert direct 3D SOCs without norm epigraphs and DCP for all device terms. Do not accidentally add a hull or per-split rating bound. |
| Cost algebra | `test_cost_coordinates.py`: arbitrary assigned physical arrays, unequal positive weights/efficiencies, non-unit delta, both hourly and component-first where supported. Check transformed and original objective/constraints, independent leaf start maps, unchanged Parameters/IDs, full-rate ownership rejection, and no lossy abs auxiliary. Include ideal plus lossy plus shedding, generation constants/PWL, HVDC and terminal costs. Zero-weight ideal/load paths remain covered; zero lossy weight remains rejected. |
| Exact-box and scale composition | `test_preparation_bridges.py`: prepared lossy builds with fixed and near-fixed Pg/ND, with and without actual fixed entries. Verify only permitted coordinates/rows are eliminated; lossy recurrence and user coupling remain. Test convex identity and `joint5` maps, SOC block scaling, primal restoration and dual reconstruction through existing bridge checks. |
| Complete AC starts | Extend verified-start tests: distinct explicitly assigned charge/discharge/reactive/SoC values; correct vectorized initial SoC for each device; unchanged free values; complete physical → cost → reduced native `x0` and inverse map. Native layout includes all canonical auxiliaries, not just original leaves. Include nonzero initial energy, mixed fleets and T=1. Do not introduce a second solver/start mapper. |
| Physical matched solves | Small Case9 baseline/prepared pairs across all four formulations and single/stepwise/vectorized layouts, including T=1. Use representative allowed individual flags and combined policies, not every Cartesian combination. Independently check balance, net power/reactive capability, recurrence, energy bounds, terminal residuals, conversion loss, leakage and split threshold in MW/MWh. Exercise the existing SOCP audit on restored values. |
| Objective equivalence | Reconstruct generation, device throughput, other active component costs and terminal cost from physical results/inputs. Retain native/physical discrepancy separately. Compare objectives in convex matched problems at declared accuracy; compare AC local solutions without asserting global optimality or unique trajectories. Test lossless reduction to ideal storage and a non-unit-delta heterogeneous fleet. |
| Failures and diagnostics | `test_preparation_bridges.py`, `test_cost_coordinates.py`, `test_storage_lossy.py`: forced native exception, unusable/nonfinite/no-primal return, failed second solve, unsolved schema and changed Parameters. No scaled values or stale `ls_*` arrays may leak publicly. Replay the existing full-battery/no-curtailment counterexample: preparation must still raise `SimultaneousOperationError` with physical-unit attached results. Warnings-as-errors retain the established behavior. |
| Unsupported paths | Retain convex cost-coordinate rejection, AC joint-scaling rejection, DC normalization rejection, single/stepwise component-first rejection, unsupported solver flags, and hierarchy/prepared-rollout lossy limitations. Replace the blanket lossy-policy rejection test only for capabilities implemented in that slice. |

### Numerical interpretation and tolerances

Use existing physical test tolerances where they apply: e.g. `ENERGY_ATOL=1e-5`
MWh and `SPLIT_TOLERANCE_MW=1e-3`. Specify engineering-unit balance/capability
and cost tolerances in the new fixtures before solving, using the existing
preparation tests as the baseline; do not loosen solver settings to make a pair
pass. Evaluate nonlinear capability residuals consistently (MW/MVA versus
squared quantities must not share an unconverted threshold).

At prescribed points, algebraic maps and physical values should agree to
floating-point precision. Across independently optimized solutions, require
feasibility and objective/accounting agreement, not exact SoC or split-power
coordinates in nonunique problems. Coordinate equality is a gate only for a
controlled fixed/unique fixture or restoration of the **same** solver point.
An AC local-basin difference triggers diagnosis of the matched comparison, not
automatic proof that the transformation changed the problem. `optimal_inaccurate`
and solver failure are retained distinctly; do not count them as unqualified
equivalence successes merely because extraction returned arrays.

## 6. Implementation sequence for later authorization

1. **Base preparation support.** Reuse normalized capability constraints and
   existing fixed/scaling bridges; cover start-boundary completion. Replace
   the blanket lossy guard with a narrow temporary rejection of lossy AC cost
   coordinates until step 2 is complete. Do not silently leave lossy throughput
   untransformed when that option is requested. Review this bounded slice.
2. **Complete-rate coordinate contract.** Implement the generic multi-leaf
   ownership described in Section 4, migrating existing one-leaf declarations
   without representation drift. Add lossy AC declarations, component-first
   composition, restoration/accounting and failure tests. Review before removing
   the remaining temporary guard. No interim public fields or compatibility
   scaffolding should remain after this slice.
3. **Qualification and documentation.** Run targeted suites and the complete
   regression suite, inspect independent physical/accounting results, then
   update the support matrix, modeling guide and M18 backlog. A bounded
   numerical performance study, if useful, requires a separate protocol and
   authorization; these tests establish supported behavior, not speedup.

Completion means the supported matrix is implemented, equivalent under the
stated checks, and documented; existing paths remain compatible and deferred
work remains deferred. Owner review/commit comes after clean verification.
This plan authorizes neither those implementation steps nor any solves today.
