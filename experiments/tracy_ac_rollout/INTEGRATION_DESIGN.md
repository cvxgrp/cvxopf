# E4 narrow integration design

Status: design proposed for owner review, 2026-10-12. **No implementation,
public API change, qualification solve or numerical execution authorized.**

Scope: [the E4 protocol](PROTOCOL.md), consuming retained plans and executing
prepared AC windows with the existing sheddable-load model. Inspection baseline:
`socp` at `b11c4418fbd8f81294a1a26a1c053bb896f3b216`, plus the uncommitted
E4 planning and separate diagnostic packages. Function names below identify
current code; new names are proposed, not implemented interfaces.

## 1. Design decision

Add a private, typed retained-signpost input and reuse the existing AC window,
start and advancement machinery. Keep study scheduling, artifact loading,
supervision and independent scientific audits in the experiment. Do not fake
an outer solve, add an arbitrary formulation-adapter framework, or expose a
new public hierarchy API for this experiment.

The shared unit is **one requested AC window and its accepted first action**.
M17 continues obtaining its targets from a live DC planner; E4 obtains targets
from a frozen, independently checked physical-energy table. Both use the same
storage replacement, frame slicing, causal transformations and state-transition
checks. E4's supervisor schedules windows and attempts; it does not implement a
second copy of those operations.

Existing public `HierarchicalInputs` and `solve_hierarchical_opf` retain their
preparation and sheddable-load rejection guards. E4 uses a separately validated
private entry, with explicit supported capabilities. This is not an
`allow_unsupported=True` escape hatch.

## 2. Actual reuse boundaries

| Responsibility | Existing owner / helpers | E4 decision |
| --- | --- | --- |
| Physical input ownership, copying and identities | `hierarchical.py`: `_device_ids`, `_validate_frame`, `_copy_frame`; `_hierarchical_solver.py`: `_ExecutionInputs`, `_execution_snapshot` | Extract/reuse neutral physical-input validation and snapshot construction, separating public M17 capability rejection from data validation. Do not instantiate or mutate a rejected `HierarchicalInputs`, and do not copy its entire validator. |
| Window construction | `_hierarchical_solver.py`: `_aligned`, `_inner_storage`, `_build_window` | Reuse with validated E4 inputs, explicit AC options and vectorized assembly. Keep a single implementation of frame slicing and storage replacement. |
| Retained target selection | `_outer_target` currently requires `OuterPlanRecord` | Factor the array/identity lookup from its record wrapper. E4 adds explicit original-final-target handling; M17's target semantics remain unchanged. |
| Physical variable starts | `_complete_start`, `_assign_start`, `_shifted_start`, `_perturbed_start`, `_solution_values`; `_ac_start_mapping.py`: `stepwise_template`, `stepwise_values`, `pack_start`, `project_stepwise_values` | Reuse, qualifying shedding-variable coverage and T=3/2/1 behavior. Shift restored physical variables, not cost-coordinate or reduced IPOPT vectors. |
| Numerical preparation and solve | `OPFBuild.solve`, `_ac_preparation.solve_prepared_ac`, `solve_at_verified_boundary`, `_cost_coordinates` | E4 calls `build.solve()`. Reuse all existing transformations, canonical verification, restoration, failure cleanup and preparation evidence. |
| Per-window recovery | `_registry`, `_window_attempts`, `_execute_attempt`, `_empty_attempt`, `_attempt_solution` | Separate slot/source selection from M17-specific solving/auditing and outer-record access. Reuse selection/state semantics with an internal typed attempt executor; do not copy the ladder. E4's assembly-phase wrapper uses the reviewed slot sequence, not a new source-selection implementation. |
| State advancement | First-action recurrence and advancement in `solve_hierarchical_opf` | Extract one small pure advancement helper, used by both callers; accepted-result checks precede advancement. |
| Full scientific audit | `e3_prepared.audit` / `e3.audit` → `stage_b.audit_result`, plus `stage_d_physics.audit_ac_network` and `audit_reactive_channels`; prepared native checks in `e3_prepared` | Reuse from the experiment, with actual window inputs and native/preparation evidence. Keep independent equations separate from builder expressions. |
| Process supervision and archives | `run_e3.supervise`, existing immutable artifact and replay helpers | Reuse the existing supervisor; narrowly parameterize protocol lookup/resource limits if needed. No second polling/termination loop, global cohort monkey-patch or new annual supervisor. |
| Executed metrics | `_executed_record`, `_summary` cover only M17's original accounting | Do not treat these as complete E4 totals. Add load-aware executed accounting in the experiment from shared numeric audit outputs; retain original M17 schema/values. |

### Why existing records are not interchangeable

`OuterPlanRecord` requires an `OPFBuild`, a lossy-DC solve audit and a result.
An archived SOCP, copper-plate or AC signpost source is not that object. Do not
construct a dummy DC build or fabricate `accepted_primal=True` to fit it.

`HierarchicalResult` validates its live outer-plan registry and fixed recovery
structure. It must not be used as an E4 container with invented plan entries.
The experiment needs a small envelope around real window attempts, executed
actions and its retained source. Reuse applicable leaf records, not the
semantically incompatible whole result type.

## 3. Proposed module ownership

Only the following narrow package boundary changes are proposed:

- New private `src/cvxopf/_ac_rollout.py`: solver-free retained-plan/window/
  cursor contracts, target lookup and accepted-first-action advancement. No
  filesystem, subprocess, experiment or optimizer imports. Use NumPy and
  ordinary immutable data records; no framework or dynamic registration.
- Existing `_hierarchical_solver.py`: reuse/refactor window construction,
  causal starts and recovery selection behind those small contracts. Legacy
  wrappers retain existing signatures, records, default policies and behavior.
  Do not move the whole controller simply to change file organization.
- Existing `hierarchical.py`: share neutral validation where necessary without
  weakening its public capability guards. Public exports and solve signature
  stay unchanged. New records need not be exported from `cvxopf.__init__`.
- Existing `_ac_preparation.py`, `_cost_coordinates.py`, `_ac_start_mapping.py`:
  remain the authoritative numerical transformations. Changes only if a
  demonstrated seam needs a small observer/validation extension, never a
  second implementation.
- `experiments/tracy_ac_rollout/`: source loader, E4 capability/config validation,
  supervisor adapter, attempt executor/auditor, rollout artifact envelope and
  analysis. These call shared package helpers and existing experiment support.
  Package modules never import these files.

The dependency direction is experiment → private rollout/solver helpers →
public builder and component-owned mathematics. Pure rollout contracts do not
import `_hierarchical_solver`, avoiding a new circular dependency. No change
to `load.py` physics is expected.

## 4. Typed retained-plan interface

Proposed frozen record in `_ac_rollout.py` (illustrative signatures, not code
to implement before design approval):

```python
@dataclass(frozen=True)
class RetainedEnergyPlan:
    plan_id: str
    source_formulation: Literal["ac", "socp", "lossy_dc", "singlenode_dc"]
    source_archive_sha256: str
    source_commit: str
    source_classification: str
    source_use_disposition: Literal["accepted", "owner_approved_qualified"]
    source_use_record_sha256: str
    qualification_flags: tuple[str, ...]
    physical_input_sha256: str
    global_start: int
    interval_count: int
    delta_hours: float
    storage_device_ids: tuple[str, ...]
    boundary_soc_mwh: np.ndarray       # time-first (T+1, ns), including initial
```

All arrays are owned copies, finite and read-only; frozen dataclasses alone do
not protect caller-owned arrays. Validate unique nonempty IDs, positive delta,
integer contiguous coverage and exact shape. `global_stop` and boundary indices
are derived, not independent mutable fields. Source formulation is provenance,
not a dispatch instruction: consuming a plan launches no convex or AC outer solve.

The experiment loader verifies archive hashes, source context, original
classifications and owner source-use records, then constructs this value.
The record constructor checks structure, not scientific acceptance by fiat.
The loader binds the original source plus its qualification/use record; it
does not need to change historical artifacts to create the E4 manifest.

The physical-input digest uses the existing representation-neutral fixture
comparison: network/device data, profiles, economics and original boundaries,
excluding intentional formulation/numerical-representation differences. The
loader must compare actual reconstructed input data as well as digest schemas;
an AC digest and DC digest are not assumed comparable because both are hashes.

At admission, align columns once to the receiving fleet by explicit IDs;
reject missing, duplicate or extra devices. Check all states against capacity
and source tolerances, and initial/final states against original obligations.
No clipping, normalization to percentages or interpolation. Preserve both
the original source order/hash and the receiver-aligned mapping in evidence.
The three numerically qualified E3+ donors require explicit per-source
disposition; do not make a general `accept_inaccurate` switch.

Two companion pure records make handoff semantics explicit:

```python
@dataclass(frozen=True)
class ACWindowRequest:
    arm_id: str
    plan_id: str
    local_start: int
    local_stop: int
    global_start: int
    global_stop: int
    storage_device_ids: tuple[str, ...]
    initial_soc_mwh: np.ndarray
    target_soc_mwh: np.ndarray
    source_endpoint_soc_mwh: np.ndarray
    target_kind: Literal["retained_boundary", "original_final_obligation"]

@dataclass(frozen=True)
class RolloutCursor:
    completed_intervals: int
    storage_device_ids: tuple[str, ...]
    realized_soc_mwh: np.ndarray
    preceding_controlling_attempt_id: str | None
```

`request_window(plan, cursor, original_final_soc, window_steps)` checks cursor
identity/coverage, calculates e=min(k+W,T), and records the exact original
final target when e=T. Global indices must equal plan offset plus local indices.
Initial state comes from the cursor, never the donor's boundary k after k=0.
The original final target and original initial state are validated physical
inputs, not additional fields derived from a rounded source endpoint.

`advance_cursor(request, accepted_action)` checks matching arm/plan/window,
controlling acceptance and the first-step recurrence, then returns a new cursor.
Use reconstructed `s_next = s_initial - delta*b_first` after checking agreement
with the solved state, matching M17's existing advancement. Retain both solved
and reconstructed values/residual. The experiment archives accepted evidence
before committing this advancement; replay validates each interval exactly once.

## 5. Window input and execution boundary

Use a validated private physical snapshot, sharing `_ExecutionInputs` fields
and neutral validation, rather than a second public input dataclass. E4's named
capability validator supports AC/vectorized/T>=1, the retained ideal-storage
Tracy fleet, explicit identity-aligned loads (including shedding), and the
approved standalone AC preparation. It must reject unrepresented temporal
coupling and unsupported fleets before construction. This initial experiment
does not advertise generic support for arbitrary load mixtures or HVDC.

An experiment-local typed `PreparedRolloutConfig` holds window length, AC
`OPFOptions`, existing `LayerSolveConfig` for IPOPT, acceptance settings,
initialization/recovery selection and assembly phases. Reuse
`NumericalPreparation` rather than inventing fields for its scales. Resource
budgets remain a separate supervisor configuration. Do not manufacture a DC
`LayerSolveConfig` or route AC options through M17's shared outer/inner options.

Refactor common recovery selection to consume a private `ACAttemptExecutor`
protocol: a window/slot/source request in, a completed or failed attempt record
out. This is an internal seam for the legacy executor and the E4 executor,
not a plugin API. The legacy implementation preserves `_execute_attempt`'s
current solving/auditing path. E4's implementation calls `build.solve()` and
combines the prepared/native, physical and endpoint audit before marking any
attempt controlling. A target-free role can supply a start but never an action.

Replace dependencies on an entire `OuterPlanRecord` inside per-window attempt
helpers with only the plan ID and window target they actually need, using
legacy wrappers to preserve public behavior. Reuse `ACAttemptRecord`'s slot
payload validation where compatible; its `outer_plan_id` can carry the retained
plan ID in E4, resolved by E4's source registry, not by a fake `OuterPlanRecord`.
Keep scenario-local iterations consistent with existing record validation and
retain source-global offsets explicitly in the E4 envelope.

The E4 envelope also retains assembly phase, preparation/native evidence and
complete audit components. Phase-qualified attempt IDs prevent collisions
when the same ordinal occurs in component-first and hourly phases. Do not
extend the legacy nine-slot result validator to claim an E4 phase registry.
Factor slot/source selection once and test each wrapper's declared ordering.
Exact slots, seeds and resource ceilings remain the protocol's pending choices.

## 6. Prepared starts: preserve the complete production path

Calling `_solve_ac_with_verified_x0` directly on the physical build is not an
adequate substitute for `build.solve()`: `solve_prepared_ac` constructs the
cost-coordinate graph, uses its canonicalization, retains coordinate maps,
restores physical variables and handles unusable results. Its existing
`solve_at_verified_boundary` applies normalization/fixed-coordinate reduction.
All of this stays authoritative.

Retain physical named starts plus actual assigned/adjusted/reduced canonical
vectors and maps from `build.preparation_evidence`. Reuse
`e3_prepared.prepared_start`, `verify_prepared_start` and `restore_ac` for the
independent study checks where compatible. A prospective canonical vector is
not proof that IPOPT received it; actual handoff evidence must agree. Any small
observer needed to expose existing evidence belongs at the production boundary,
not in a monkey-patched solver or copied canonicalization pipeline.

`load_shed_fraction` has one solver row per **sheddable** load, whereas public
served/shed arrays cover the whole load fleet. Map it by prepared load identities
and sheddable indices, not by assuming those shapes are interchangeable.
Physical fractions shift through the existing start-mapping machinery. Rebuild
cost-coordinate weights from the destination demand and delta; never shift
old cost-valued coordinates when the load profile has changed. Destination
eligibility/caps, zero or negative demand, fixed boxes and cost auxiliaries
must be handled by existing load/preparation semantics and checked in tests.

`variable.project` alone does not enforce explicit affine bounds. If a new
initialization adjustment is needed for shedding, define it as a documented
start-only transform using destination component bounds; do not alter source
signposts or feasible sets. Qualify copied target-free and perturbed starts as
well as primary shifts. Preserve the realized initial SoC unperturbed.

## 7. Audit and executed accounting

M17 `_device_injections` already consumes served-load P/Q, but its residual
set does not independently establish full device feasibility or shedding
economics. Its `_executed_record`/`_summary` omit shedding cost and ENS. Neither
is sufficient as the E4 scientific acceptance/accounting gate unchanged.

Use the existing E3 prepared audit chain and original-unit
`stage_b.audit_result` with window-sliced inputs. This audit explicitly assumes
the approved all-loads-sheddable/no-HVDC fleet; validate that capability instead
of quietly generalizing it. `stage_d_physics` independently reconstructs AC
network and reactive channels. Reuse prepared native objective/restoration
checks, keeping cycling slack warnings distinct from unexplained errors.

An experiment-owned executed-action record wraps the controlling attempt and
retains first-step physical device/network outputs, solved/reconstructed next
SoC, load IDs, served P/Q, shed MW, ENS by load, and cost components. Recompute
per-action cost and ENS with shared numeric audit helpers; never distribute
a window total over its hours. Extend/factor those helpers narrowly if needed,
preserving historical audit defaults and results. Do not use modeled expression
values as the independent accounting oracle.

Final analysis concatenates only executed actions and audits the complete
24-hour physical trajectory against original inputs/obligations. Recoveries,
forecasts and target-free attempts contribute runtime but no executed ENS or
physical operating cost. The forced-shedding lower-bound witness applies to
the completed day. Preserve partial trajectories and source numerical flags.

## 8. Implementation slices after separate approval

| Slice | Deliverable / review gate |
| --- | --- |
| I1 — Pure handoff | Retained-plan loader and typed plan/window/cursor; immutable identities, actual fixture matching, qualified-source disposition, exact endpoint/index tests. No solve. |
| I2 — Shared window seam | Neutral snapshot/window and advancement reuse; retain M17 guards and compatibility. Mock executor proves recovery dependencies, acceptance-only advancement and phase-qualified evidence. No solve. |
| I3 — Prepared/shedding seam | Production solve path wired with complete start/native/restoration evidence and independent load audits; non-solving intercepted-boundary/failure tests. No qualification solve without approval. |
| I4 — Supervised study wrapper | Reuse supervisor/archive/replay, freeze exact recovery/settings/budgets, deterministic arm schedule and complete/partial analysis. No study execution. |

Required focused regressions include shuffled/missing/extra IDs; initial and
terminal boundary tolerances without clipping; T=3→3→2→1; load fraction versus
public-fleet indexing; changing/zero/negative active demand and reactive signs
at the shared component/start level; explicit shedding caps; cost-coordinate
rescaling; failed/native-unusable results; target-free nonadvancement;
first-action ENS/cost exactly once; and legacy guard/default/result preservation.
The experiment audit remains limited to its declared positive-demand fleet,
even when reusable mapping helpers have broader component-level tests.

Before any numerical qualification, choose a small bounded seam test and obtain
owner approval. Do not require a new broad matrix by default. Review/commit and
separate E4 launch approval follow the parent protocol; this design creates no
output directories, solver attempts, authority records or source bindings.

## 9. Owner decision at this checkpoint

Approve or revise this private retained-plan/window integration, including
sharing recovery selection via a typed executor and retaining E4's full audit
at the experiment boundary. Public M21/M25 generalization remains deferred.
Implementation may begin only after that approval. Resource limits, exact
recovery table and engineering comparison thresholds remain separate pending
execution-design choices; they are not implicit defaults in these new types.
