# Milestone 17 — Hierarchical DC→AC receding-horizon dispatch

The capstone milestone: the concrete implementation of the project vision
stated in the README ("solve the convex `lossy_dc` formulation over the full
planning horizon ... then use the AC formulation over a short receding horizon
to verify and correct against the modeled nonlinear AC network physics, with
SoC targets inherited from the convex layer as boundary constraints").

Two-layer structure:

- **Upper layer — long-horizon plan.** Solve `lossy_dc` (convex, globally
  optimal) over the full multi-day horizon. Extract the SoC trajectory
  `soc*(t)`.
- **Signposts, not setpoints.** Only the **SoC waypoints** are passed down to
  the AC layer — *not* generator dispatch, voltages, or branch flows. The AC
  layer re-optimizes everything else against the fuller modeled AC network
  physics; it is only told what stored energy to arrive at, at each
  checkpoint. Passing full
  setpoints down would over-constrain the AC problem and defeat the purpose.
  This discipline is the core design decision of the milestone.
- **Lower layer — short AC window.** A 3–5 step AC-OPF over a receding horizon.
  The inherited SoC signpost enters as a **terminal constraint**
  (`soc[end] == soc*`) or a **terminal cost** (`ρ · ‖soc[end] − soc*‖`) — the
  hard/soft choice is a design axis to expose, and reuses the terminal-SoC
  machinery from Milestone 12.
- **Receding horizon.** The AC window advances, re-inheriting the next signpost
  from the DC plan at each step.

Dependencies and rationale:

- **Depends on M16.** A two-layer solver that shares device models across the
  DC and AC formulations should be built *after* the components compose
  uniformly (M16). Building it earlier would re-entrench per-formulation
  duplication.
- **Depends on the M16+ and correctness-hardening decisions.** Shared typed
  component assembly should be complete before the controller adds another
  orchestration layer. Finite temporal-input validation and the
  objective-time-units decision must also be complete so DC and AC windows
  cannot silently assign different economic meaning to different temporal
  resolutions. See `plans/milestone-16-plus-component-adapters.md` and
  `plans/correctness-api-hardening.md`.
- **Depends on M12** for the terminal-SoC hard-constraint-vs-soft-penalty
  machinery the AC window consumes.
- **Depends on M4**, now complete, for AC branch-flow limits. The lower layer
  can therefore check nonlinear power flow, voltage bounds, device
  feasibility, and both terminal thermal limits when assessing whether the DC
  plan is executable on the transmission network.
- **Subsumes the convex-tracks-AC validation study.** Two distinct open-loop
  studies fill the currently unfilled "temporal × cross-formulation" cell.
  **Endpoint realization** gives one aligned AC window the initial and
  terminal SoCs selected by DC. **Open-loop sequential execution** freezes one
  outer DC plan while successive AC windows execute their first actions
  without outer replanning. Neither is called closed loop. The
  `case9_storage_{ac,dc}_24h.py` examples already supply much of the common
  scenario structure.

This milestone is why the formulation ladder, storage SoC coupling, M16
composability, and cheap multi-formulation runs exist — it is where that
infrastructure is cashed in.

## Companion experiment

M17 has one companion experiment:

```text
experiments/hierarchical_battery_resilience/
```

The experiment begins before implementation and continues after
implementation. It is not merely a unit or integration test.

### Phase 1 — pre-implementation executable specification

Before introducing a public hierarchical-controller abstraction, manually
orchestrate the existing DC and AC builders:

1. solve the long-horizon `lossy_dc` plan;
2. retain its complete post-step SoC trajectory;
3. select an aligned short AC window;
4. initialize that window from the current realized SoC;
5. use the DC post-step SoC at the same global endpoint as the AC terminal
   signpost;
6. solve AC and execute the first action;
7. update the realized state;
8. apply the explicitly selected frozen or `replan_every_step` outer policy;
   and
9. repeat through the experiment horizon.

The state and window recurrence is:

- `e_k` is the realized device-aligned SoC vector before interval `k`;
- controller iteration `k` begins its outer solve or frozen-plan lookup at
  `e_k`;
- `W_k = min(W, H - k)` and the AC window covers intervals
  `k, ..., k + W_k - 1`;
- a DC plan created at iteration `j` stores local boundary `ell` for global
  boundary `j + ell`;
- `frozen` uses `e_dc[0][k + W_k]`, while `replan_every_step` uses
  `e_dc[k][W_k]`; both refer to global boundary `k + W_k`;
- current results omit the initial boundary: local boundary 0 comes from
  `storage_initial_soc`, while boundary `ell >= 1` comes from SoC result index
  `ell - 1`;
- only the first AC action `b_ac[k]` is executed; and
- for ideal storage, `e_{k+1} = e_k - delta * b_ac[k]`.

The protocol must include hand-checkable `T=3, W=2` and `W=1` examples. All
state transfer is keyed by stable storage identity; positional coincidence is
not part of the contract. Because `StorageUnitIdeal` does not yet expose a
device ID, S0 must characterize the gap and a dedicated prerequisite slice
immediately afterward must establish the backward-compatible identity
contract. Every M17 storage unit supplies an explicit, unique, nonempty ID,
even in a one-battery run. Generated legacy labels are build-local positional
labels, not cross-build identity.

This manual runner is an executable design specification. It must resolve and
freeze:

- DC-to-AC state-index alignment;
- exactly which quantities cross the layer boundary;
- hard-equality and quadratic-soft target behavior;
- realized-state feedback;
- outer-plan replanning cadence;
- behavior after AC infeasibility or an unattainable terminal target;
- intermediate-build and trajectory retention; and
- diagnostic and result schemas.

The outer DC plan observes its realized initial SoC and optimizes subject to a
configured per-device terminal obligation at the original global boundary
`H`. Every shortened replan retains the same target, policy, and any soft
weight at that boundary. The normative baseline uses the hard energy-neutral
policy `e_H = e_0`; the prepared scenario freezes the actual initial value,
provisionally 50% of capacity. Short AC windows do not independently
reconstruct charging opportunities that occurred before their inherited
initial state.

The endpoint-fixed subsection findings in
`experiments/battery_terminal` are the immediate precursor: a restriction of
the long DC plan is independently DC-optimal, and selected inherited DC
endpoints are AC-realizable while AC reoptimizes network dispatch.

### Phase 2 — post-implementation scientific and software validation

After the M17 public abstraction exists, run the frozen experiment through
that API. The manual runner remains the reference implementation.

The post-implementation phase must establish:

- public-API results reproduce the accepted manual protocol;
- signposts are aligned without a one-step offset;
- realized SoC is propagated correctly;
- automatic outer replanning matches explicit reconstruction;
- hard/soft target and fallback behavior match the frozen decisions;
- intermediate plans, AC windows, statuses, and diagnostics are retained; and
- conclusions are stable across the approved scenarios and window lengths.

The execution backend changes from manual orchestration to the M17 API; the
physical system, scenario, policy definitions, and acceptance metrics do not.

### Initial experiment scope

The first experiment isolates hierarchical orchestration:

- one or two fixed multi-day scenarios;
- long-horizon loss-penalized DC outer planning;
- short receding AC windows, provisionally 3–5 steps;
- DC-derived SoC signposts at aligned window endpoints;
- realized-state feedback;
- hard-equality and quadratic-soft terminal policies;
- endpoint realization, frozen-plan sequential execution, and
  `replan_every_step` closed-loop execution under identical perfect forecasts;
- fixed nonsheddable loads, with M19 corrective shedding disabled;
- enforced `rateA` limits with both-terminal utilization and residual
  diagnostics;
- DC-versus-AC battery, generation, renewable, voltage, and physical-loss
  corrections;
- terminal deviation, solve status, constraint violation, and runtime.

Window length is selected for AC correction and computation. Energy
opportunities before a window are represented in its inherited DC-planned
initial state; an arbitrary half-capacity reset is not an M17 boundary
condition.

Forecast error, contingencies, corrective load shedding, alternate branch
ratings, soft thermal limits, and topology changes are important to the larger
battery-resilience thesis but are excluded from the first orchestration
experiment. Once the hierarchy itself is validated, those become controlled
resilience-study extensions.

### Experiment acceptance gates

#### Gate 1 — frozen protocol

- Scenario, device system, horizon, window length, policies, and metrics are
  fixed before the public M17 implementation.
- Scenario acquisition or synthesis is reproducible without untracked local
  data. Exact timestamps, transformations, device definitions, and hashes of
  prepared arrays are retained.
- State indexing is documented with a small hand-checkable example.
- Manual execution is deterministic up to documented solver variability.
- Every solve and state transition is retained for audit.
- Every outer plan is retained once under a stable ID; AC attempts reference
  that plan and their selected local/global boundary rather than duplicating
  complete signpost trajectories.

#### Gate 2 — open-loop realization

- DC-planned endpoint pairs are tested in aligned AC windows.
- Deviations in battery trajectory and active dispatch are reported.
- Physical AC losses and voltage constraints are distinguished from DC
  objective penalties.
- AC local-optimality remains explicit, and both terminal thermal-limit
  residuals are retained as network-executability diagnostics.

#### Gate 3 — closed-loop execution

- The first AC action, not the full predicted window, updates realized state.
- Realized economic and physical totals use only executed first intervals,
  integrated once by `delta`; overlapping predictions and terminal penalties
  remain solve diagnostics and are not double-counted.
- Outer replanning begins from that realized state.
- Hard-target failure and soft-target deviation have explicit, tested
  behavior.
- No action is executed from a solve without an accepted primal result. A
  hard-target failure, target-independent AC infeasibility, solver failure, and
  a successful soft solve with nonzero deviation remain distinct outcomes.
- AC initialization is selected through an explicit typed policy. The
  reproducibility policy `flat_only` performs exactly the historical project
  flat start. `shifted_with_recovery` is the operational policy selected for
  the hard-equality M17 reference workflow from the reviewed S3b evidence. The
  first window begins with a controlling flat start carrying the configured
  inner terminal policy. Later windows begin from the preceding accepted
  controlling AC prediction, shifted one
  step and reconciled with the newly realized SoC. After an unaccepted
  controlling attempt, the policy may solve the corresponding
  terminal-policy-free problem from that same causal start, copy an accepted
  solution into a new controlling attempt carrying the configured inner
  terminal policy, and
  apply only the predeclared
  deterministic perturbations of explicitly named causal sources.
- Every initialization attempt retains its source, complete attempt outcome,
  solver status, residuals, and runtime. A target-free solve temporarily
  removes the hard terminal constraint or soft terminal cost solely to
  construct an initialization. Its action is never executed, and the
  configured terminal policy is restored unchanged in every controlling
  attempt.
- Exhausting an initialization policy is reported as an unresolved solve
  failure, not as physical or modeled infeasibility. Raw solver-reported
  `infeasible` status is retained without being promoted to a global
  infeasibility certificate.
- Any hard-to-soft retry is preselected, retained as a separate fallback
  attempt, and never implemented as anonymous slack or silent relaxation.
- No stale DC SoC state is propagated after AC execution.

#### Gate 4 — implementation equivalence

- The M17 API reproduces the manual runner within solver tolerances.
- Intermediate outer and inner builds correspond window by window.
- Result schemas contain the information required to explain divergence.
- Differences are attributable to documented implementation choices, not
  changed experimental inputs.

#### Gate 5 — scientific interpretation

- Success means preservation of long-horizon battery-energy intent under AC
  correction, not reproduction of DC generator or renewable setpoints.
- SoC signpost deviation is reported as inter-layer disagreement.
- Results do not claim general resilience until disturbances and resilience
  outcomes such as unserved energy are explicitly modeled.
- In later extensions that enable M19 sheddable loads, an AC window that uses
  corrective shedding is not reported merely as “AC feasible.” Retain
  DC-planned demand; AC input and served demand; corrective active and
  reactive shedding; and aggregate and per-load energy not served as separate
  outcomes so shedding cannot conceal failure of the upper-layer plan. When a
  no-shedding counterfactual is explicitly run, also report whether that AC
  window would have been infeasible or target-inconsistent.

### Required experiment layout

```text
experiments/hierarchical_battery_resilience/
    README.md
    protocol.md
    manual_runner.py
    runner.py
    analysis.py
    reproduce.py
    experiment_log.md
    results/
        .gitignore
```

`manual_runner.py` is implemented in Phase 1. `runner.py` is added in Phase 2
and uses the public M17 API. Empty placeholders need not be importable Python
modules; the stub documents their intended ownership until implementation.

## Implementation stages

S0–S3 are pre-public-controller specification and reference implementation.
S4–S8 define, implement, and validate the public controller only after the
manual protocol and scientific results are accepted.

| Stage | Outcome |
|---|---|
| S0 | Re-baseline the current APIs and characterize DC/AC result, status, SoC indexing, and missing storage-identity contract. |
| P1 | Implement storage identity: append optional `device_id`, validate supplied IDs, publish aligned IDs, preserve them through derived windows, and reject non-explicit or mismatched identity at the M17 boundary. |
| S1 | Freeze a reproducible experiment protocol and prepared scenario. |
| S2 | Implement the auditable manual reference runner. |
| S3 | Run the predeclared endpoint, frozen-plan, and replanned experiments; select any public defaults without changing the frozen variants. |
| S3b | Evaluate the candidate causal hard-target initialization-recovery policy over the complete frozen replanned trajectory. |
| S4 | Define typed public controller inputs, outer/terminal/initialization policies, attempt records, and result schema. |
| S5 | Implement hierarchical orchestration and explicit initialization recovery above the existing public builders. |
| S6 | Verify state alignment, initialization and failure paths, multiple-storage identity, `W=1`, and final truncated windows. |
| S7 | Re-run hard and soft `flat_only` through the public API against their frozen S3 baselines; reproduce hard `shifted_with_recovery` against the reviewed S3b trajectory; and optionally evaluate soft shifted recovery as separately labeled evidence. |
| S8 | Complete documentation, example, flowchart update, and milestone handoff. |

P1 is prerequisite device-API hardening, not hierarchical-controller
implementation. S3 and S3b results must be reviewed before S4 freezes public
defaults.
No scientific protocol is revised merely to make the S5 implementation appear
successful.

### S0 stopping point

**Complete; checkpoint commit `1f3efaf`.**

S0 records the current contract in
`experiments/hierarchical_battery_resilience/S0_REPORT.md` and
`tests/test_m17_characterization.py`:

- storage SoC results contain `T` post-step states and omit the initial
  boundary;
- conceptual local boundary 0 comes from `storage_initial_soc`, while boundary
  `ell >= 1` maps to result index `ell - 1`;
- solved single-step and multistep `T=1` result schemas remain intentionally
  distinct and satisfy the same one-interval recurrence and terminal policy;
- the ideal-storage recurrence and global terminal equality agree across AC,
  lossy DC, and single-node DC;
- shortened replans restart their local boundary index at zero;
- unsolved builds retain schema and exogenous inputs but have no usable storage
  primal; and
- pre-P1 storage had no stable identity metadata, confirming P1 as a real
  prerequisite rather than protocol polish.

The package has no centralized accepted-primal predicate. The approved manual
runner rule executes an action only for `optimal` or `optimal_inaccurate`, with
every required field finite and all frozen physical and policy residual checks
satisfied. The role-specific minimum field table is frozen in the S0 report;
conditional devices extend the required set. `user_limit` and incomplete
primals are diagnostic only.

Verification: 10 focused tests and the complete 1,637-test suite passed. Ruff,
configured strict mypy, and `git diff --check` passed. S0 changes no production
implementation.

### P1 storage-identity prerequisite

**Complete; checkpoint commit `24b5aa5`.**

`StorageUnitIdeal.device_id` is an optional final field, preserving existing
positional construction. Preparation validates supplied IDs as unique,
nonempty strings and publishes aligned `storage_device_ids` plus
`storage_device_id_is_explicit` through `OPFBuild.data` and extracted results,
including unsuccessful or unsolved builds. Omitted IDs receive collision-safe
build-local positional labels; those labels are explicitly not stable
cross-build identity. M17 will require the explicitness mask to be true for
every participating storage unit and will perform set matching and ID-based
alignment at its orchestration boundary.

Verification: 337 focused storage, component-contract, adapter-
characterization, M17-characterization, and result tests passed. The complete
1,650-test suite passed, together with Ruff, configured strict mypy, and
`git diff --check`.

### S1 stopping point

**Complete; checkpoint commit `47345dc`.**

The normative checked-in scenario is `tracy_high_96h_v1`: the reviewed
96-hour sustained-energy-deficit Tracy window, scaled and spatially allocated
by the existing deterministic battery-terminal procedure. The experiment
freezes `H=96`, `W=5`, the 150 MVA / 1,000 MWh bus-7 battery with explicit ID
`battery_bus_7`, 500 MWh initial and global terminal energy, separate hard-
equality and quadratic-soft inner policies, quadratic weight `0.05`, the two
approved outer policies, fixed nonsheddable first-class loads, enforced AC
`rateA` limits, and explicit residual tolerances.

The three prepared arrays and a complete machine-readable manifest are
committed under `experiments/hierarchical_battery_resilience/prepared_scenario`.
Their loader verifies timestamps, cadence, shapes, column order, and file and
numeric-array SHA-256 hashes. The optional regeneration script verifies the
authorized raw composite's recorded hash before applying the documented
transformation. The ignored raw source is not required for a clean-checkout
run.

The scenario loader is also the single build-ready materialization boundary.
It verifies the live case9 `baseMVA`, bus, and branch data against the manifest
and constructs the case, options, all typed device fleets, aligned trajectory
frames, and typed horizon/policy/tolerance configuration. S2 therefore owns no
parallel manifest-to-model translation.

The frozen audit contract distinguishes AC component-to-network balance from
lossy-DC reporting consistency and nodal balance. AC scales both reconstructed
device injections and reported `p_net`/`q_net` to per unit. DC separately
checks device injection against reported `p_net` in MW and independently
checks `(A @ p_flows + p_net) / baseMVA` using a duplicate-safe incidence
matrix reconstructed in original branch-row order.

Verification: 11 S1 scenario tests and the complete 1,661-test suite passed.
Ruff and `git diff --check` passed.

### S2 stopping point

**Complete; checkpoint commit `52c2896`.**

`experiments/hierarchical_battery_resilience/manual_runner.py` is the
auditable pre-public-controller reference implementation. It consumes only
the verified `load_frozen_scenario()` contract and the existing public
multistep OPF builder. It introduces no reusable controller abstraction.

The runner implements endpoint realization, frozen-plan sequential execution,
and `replan_every_step` sequential execution. Hard-equality and quadratic-soft
inner policies remain separate predeclared runs. Every outer plan is retained
once under a stable ID; every controlling and target-free diagnostic AC
attempt references that plan and retains its complete build, result, status,
timing, identity, boundary-index, policy, and residual record.

The normative endpoint pair is frozen as the two equal-length 18-hour
sections `[32, 50)` and `[60, 78)`, named
`crosses_saturation_boundary_32_50` and `within_regime_60_78`. The comparison
tests endpoint realization across a storage saturation boundary and within one
decoupled operating regime, using cases selected from the prior
battery-terminal analysis before M17 outcomes were observed.

The implementation distinguishes globally indexed frozen signposts from
locally indexed shortened replans, including intentional `W=1` and final
window truncation. State is aligned by explicit storage ID and advances only
from the first action of an accepted controlling AC solve. Failed controlling
solves retain a diagnostic attempt and terminate without advancing state; no
fallback action or anonymous relaxation is introduced.

Endpoint realization returns a study-level record that owns its single outer
plan. If that plan is unaccepted, the complete failed plan and audit remain
available with zero AC realizations and an explicit termination reason; the
runner does not raise away the evidence.

Frozen AC and DC residuals are independently reconstructed. Realized
accounting uses only executed first intervals, integrates generation cost,
storage cycling cost, curtailment, and branch-terminal active loss exactly once
by `delta`, and retains the system-injection loss cross-check. Overlapping
predicted-window objectives and terminal penalties remain solve diagnostics
and are never summed as realized operating cost.

The trajectory summary also retains maximum executed-interval voltage and
thermal violations, cumulative absolute accepted-window signpost deviation,
and total wall time across every outer, controlling, and diagnostic solve.
These aggregations are produced by the runner rather than delegated to S3.

Focused orchestration tests cover identity alignment, local/global signpost
selection, `T=3, W=2`, `W=1`, final truncation, frozen versus replanned plan
counts, failed-action nonexecution, endpoint-plan reuse, distinct DC reporting
and nodal-balance diagnostics, and realized accounting. A real frozen outer
solve and first five-step hard-target AC window pass the complete audit
contract. Full S3 trajectories have deliberately not yet been run or
interpreted. Verification: 33 focused S0–S2 tests and the complete 1,673-test
suite passed; Ruff and `git diff --check` were clean.

### S3 review point

**Authoritative frozen experiment and hard-replanning diagnostic complete;
public-policy decisions recorded below.**

The complete scientific record is in
`experiments/hierarchical_battery_resilience/S3_REPORT.md`. Both reviewed
18-hour DC endpoint pairs were realized by accepted AC solves with exact
terminal SoC while allowing different interior trajectories. Both fixed-plan
96-hour variants completed. The hard variant returned to 500.0 MWh; the
quadratic-soft variant finished at 344.4 MWh.

Neither stepwise-replanned variant completed. The hard-target run terminated
at interval 35 after a target-conditioned AC solve returned infeasible while
the target-free diagnostic solved. The predeclared follow-up subsequently
established that this exact target-constrained problem is modeled-feasible and
that the original outcome depended on initialization in the frozen
formulation/interface/solver stack. The soft-target run reached interval 95,
where accumulated energy deviations made the final outer hard terminal
equality infeasible even at the aggregate power-adequacy level.

The result demonstrates that replanning alone does not provide recursive
feasibility. Remaining-horizon viability protection is a separately staged
controller extension rather than part of the initial M17 implementation: the
experiment establishes the need but does not select among terminal-feasible
envelopes, reserve constraints, or other viability mechanisms. M17 must expose
realized state, signpost deviation, and outer-plan failure without claiming
that soft tracking preserves terminal viability. It must also distinguish an
unsuccessful local nonlinear solve from modeled infeasibility. The frozen
baseline remains unchanged and reproducible through `flat_only`.

The authoritative experiment was executed from clean infrastructure commit
`0cd65b1a1c809b81813389f58fde6559a161d147`; the tree was clean before and
after execution, and the execution/model sources were unchanged during the
run. The tracked
`experiments/hierarchical_battery_resilience/S3_RESULTS_METADATA.json` records
the source fingerprints, software and scenario context, artifact integrity
identifiers, and result summary. The numerical findings reproduced the
preliminary run without changing the frozen protocol.

Verification: 40 focused M17 characterization, scenario, runner, and artifact
tests and the complete 1,680-test suite passed. Ruff and `git diff --check`
were clean. Local result artifacts passed their recorded size and SHA-256
checks.

### S3 hard-replanning diagnostic gate

**Complete.** The authoritative S3 baseline is closed and remains unchanged.
The interval-35 hard-target event received the named, predeclared follow-up in
`experiments/hierarchical_battery_resilience/S3_HARD_REPLAN_DIAGNOSTIC_PLAN.md`.
The tracked report and manifest are
`experiments/hierarchical_battery_resilience/S3_HARD_REPLAN_DIAGNOSTIC_REPORT.md`
and
`experiments/hierarchical_battery_resilience/S3_HARD_REPLAN_DIAGNOSTIC_RESULTS.json`.

All 14 registered calls used verified 930-coordinate IPOPT starting vectors.
The project flat start returned `infeasible`, while all six alternate
initializations produced accepted solutions of the identical archived-state,
`849.9999996548939` MWh target problem. The successful frozen-window,
target-free, shifted-preceding, and three deterministic-perturbation
constructions therefore establish modeled feasibility and
initialization-dependent behavior. An otherwise identical flat-start attempt
also succeeded after changing only the target to canonical 850.0 MWh,
providing separate nearby-problem sensitivity evidence. These are claims about
the frozen formulation/interface/solver stack, not a universal IPOPT defect or
a global characterization of the nonlinear solution set.

The diagnostic fixes the following S3b evaluation contract:

- `flat_only` preserves the exact no-retry scientific baseline;
- `shifted_with_recovery` is the candidate causal policy, using only the
  preceding accepted target-constrained AC prediction, a newly accepted
  target-free solution, and bounded deterministic perturbations of explicitly
  named causal sources;
- all attempts are explicit and retained, and only an accepted solve of the
  original target-constrained problem may provide an executed action; and
- failure after the configured attempts is `unresolved_failure`, not a claim
  that the requested endpoint is physically or globally infeasible.

The feasibility diagnostic's successful frozen-window source and its
perturbations are retrospective resources, not causally available controller
inputs. They prove existence and mechanism but do not validate the candidate
online policy. `shifted_with_recovery` becomes the recommended operational
policy only if the predeclared full-trajectory S3b experiment supports that
designation, and any recommendation is limited to the frozen scenario rather
than asserted as a universal default across networks or operating conditions.

The verified-start implementation spike is complete in commit `8dff8d1`. It
proved that policy-supplied model values map into the complete canonicalized
IPOPT starting vector, characterized the 745 model-owned and 185
reduction-added coordinates for the frozen five-step problem, and retained the
complete mapping. Those counts characterize this problem rather than define a
universal API invariant. S4–S6 must validate the generated mapping for `W=1`,
truncated windows, multiple storage units, and changed constraint structures.

### S3b causal recovery gate

**Complete; checkpoint commit `2482c0d`.** The
candidate causal study is specified in
`experiments/hierarchical_battery_resilience/S3B_CAUSAL_RECOVERY_PROTOCOL.md`.
It repeats the complete frozen `replan_every_step` hard-target trajectory using
only the project flat start, the immediately preceding accepted controlling AC
prediction, and target-free solutions constructed at the current iteration.
Every retry is retained, only an accepted target-constrained result may execute,
and complete canonicalized IPOPT starts are verified.

S3b excludes target rounding, hard-to-soft fallback, load shedding, solver
retuning, and retrospective frozen-trajectory solutions. It reports both total
runtime and attempt-type decomposition, including the fraction of windows that
accept the first shifted start. Perturbations use the accepted target-free
center first and the original causal flat/shifted center second, at scales
`1e-4`, `1e-3`, and `1e-2`. Seeds follow the frozen arithmetic rule in the
protocol.

The clean execution from commit `6c4214c` completed all 96 intervals. The
shifted primary attempt controlled 94 of 95 later windows; interval 80 recovered
through one accepted target-free solve followed by one accepted copied-start
hard solve. No perturbation was required. See
`experiments/hierarchical_battery_resilience/S3B_REPORT.md` and
`S3B_RESULTS_METADATA.json` for interpretation and provenance. These results
support `shifted_with_recovery` as the typed operational policy for the frozen
reference workflow, while `flat_only` remains available for baseline
reproduction. This is not a universal default across networks or solver stacks.

### S4 typed public contract — draft for review

**In progress.** S4 defines the public types and validation contract before S5
implements orchestration. It must not import experiment modules or silently
turn the S2/S3b runner into production code. The public implementation will
reconstruct the reviewed mathematics from existing public OPF builders and
prove equivalence in S7.

#### Proposed public entry point

Use one typed physical-input bundle and one typed controller-policy bundle:

```python
solve_hierarchical_opf(
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig = HierarchicalSolveConfig(),
) -> HierarchicalResult
```

`HierarchicalInputs` owns the network case, `OPFOptions`, horizon length,
`delta`, explicit device fleets, and identity-aligned load,
nondispatchable-generation, and HVDC trajectory tables. The first public M17
path requires explicit first-class loads and explicit storage IDs; it does not
add a second legacy positional-load compatibility surface. Existing MATPOWER
conversion remains available before constructing the bundle. Inputs are
defensively copied or normalized at the controller boundary so the run cannot
observe caller mutation.

This bundle is preferred to mirroring the full `build_opf_multistep()`
signature. Physical inputs and controller decisions remain separate, and new
device trajectories can extend one typed record without repeatedly expanding
the controller function signature.

`OPFOptions` configures model construction, not solver invocation. The
controller-facing input therefore requires `options.init_flat is True`.
`flat_only` and the first attempt of `shifted_with_recovery` both promise the
historical generated flat start, so `init_flat=False` is rejected before any
build rather than silently normalized. The initialization policy is the sole
authority for AC starting-point behavior, and every executed attempt records
the generated or assigned named start actually supplied.

#### Per-layer solve configuration

`HierarchicalSolveConfig` contains two immutable, defensively copied
`LayerSolveConfig` values:

- `outer`, defaulting to CLARABEL with convex/DCP invocation; and
- `ac`, defaulting to IPOPT with DNLP invocation.

Each layer configuration separates a solver identifier from a structurally
read-only mapping of solver keyword arguments. The mapping cannot contain
`solver` or `nlp`: those are normalized from the layer and solver selection so
one setting cannot contradict another. Keys must be nonempty strings, and
values must be defensively copied and valid for the selected solver boundary.
S4 locks a solver-specific allow-list with value type and range checks for the
CLARABEL and IPOPT controls exposed by M17; unknown keys are rejected before
the first build. The public mapping is not an unchecked passthrough to
`cp.Problem.solve()`.

Before constructing the first plan, M17 verifies that both selected solvers are
installed and compatible with their roles. The initial public contract supports
CLARABEL for the convex lossy-DC outer layer and IPOPT for the DNLP AC layer.
Other solver names fail clearly rather than inheriting unverified assumptions
about nonlinear canonicalization, warm starts, or complete-`x0` mapping. This
closed pair still permits legitimate CLARABEL and IPOPT controls such as
iteration limits, tolerances, and verbosity through their separate mappings.
Future solver support requires its own representation, status, and
initialization verification.

The exact normalized outer and AC solve configurations are retained once in
`HierarchicalResult` provenance. Every plan and attempt references its layer
configuration; records do not duplicate mutable caller dictionaries. S7 uses
the frozen reference configurations to establish solver-stack equivalence.

#### Proposed policy types

The public string values are closed typed literals, consistent with existing
device-policy conventions:

- `OuterPolicy = Literal["frozen", "replan_every_step"]`;
- `InnerTerminalPolicy = Literal["hard_equality", "quadratic_soft"]`; and
- `InitializationPolicy = Literal["flat_only", "shifted_with_recovery"]`.

`flat_only` performs one historical project flat-start attempt and no retry.
It remains the baseline-reproduction policy. `shifted_with_recovery` implements
the reviewed recovery sequence and is the operational policy for the M17
reference workflow. For `hard_equality`, this designation is supported by the
complete S3b trajectory. Applying the same initialization mechanism to
`quadratic_soft` is a supported API extrapolation pending dedicated empirical
validation; it does not inherit the hard-policy evidence claim. Its
perturbation scales and deterministic seed base are explicit fields in the
policy bundle, with the reviewed S3b values as defaults; they are never hidden
process-global state.

`HierarchicalPolicy` also owns the AC window length, quadratic-soft weight when
applicable, and a frozen
`HierarchicalAcceptanceTolerances` value. The controller validates policy
combinations before any solve. In particular, a quadratic weight is required
and finite positive only for `quadratic_soft`; no automatic hard-to-soft
fallback is implied by either initialization policy.

Accepted solver statuses are not user-configurable policy. M17 fixes the
eligible set to `{"optimal", "optimal_inaccurate"}` and then requires every
applicable finite-field, identity, and residual gate to pass. `user_limit`,
raw infeasibility statuses, and all other outcomes remain diagnostic and can
never supply an executed action.

The outer terminal obligation remains device-configured; the controller does
not duplicate storage terminal semantics. M17 preserves the supported storage
configuration, including no terminal obligation. When a target or terminal
cost is configured, replanning changes the observed initial state and shortened
local horizon but retains the same global terminal boundary, target, constraint
mode, and cost policy. The frozen reference workflow specifically requires the
reviewed equality target at the global boundary; that experiment choice is not
promoted to a universal controller requirement.

#### Proposed records

The public result is a typed audit tree rather than only a final trajectory:

- `HierarchicalSolveAudit`: raw status, accepted-primal outcome, missing or
  nonfinite fields, residuals, exception, solver timing, and iterations;
- `OuterPlanRecord`: stable plan ID, local/global boundary indices, aligned
  storage IDs and signposts, build, extracted result, and audit;
- `ACAttemptRecord`: stable attempt ID, closed slot state, registered role and
  transformation, source attempt ID, local/global window, exact target,
  conditionally retained build and assigned model start, canonicalized
  IPOPT-start evidence when applicable, result, audit, reason, and whether it
  supplied the executed action;
- `ExecutedIntervalRecord`: only the accepted first action and its realized
  physical/economic accounting; and
- `HierarchicalResult`: all retained plans and attempts, executed intervals,
  aligned realized SoC and battery-power trajectories, completion coverage,
  termination information, normalized per-layer solve provenance, and
  non-double-counted trajectory summaries.

Build objects remain retained in plan and attempt records for the initial M17
API. This makes the public result auditable and supports S7 window-by-window
equivalence. A later compact-retention mode may be added only after the full
record is stable; it is not part of M17.

All nine `shifted_with_recovery` slots are registered per attempted AC window,
including skipped and unavailable dependencies. `flat_only` registers exactly
one controlling slot. Policy-opportunity counts include construction failures;
actual solver-call counts include only attempts that reached the solver.

`AttemptSlotState` is the closed literal
`Literal["executed", "construction_error", "source_unavailable",
"not_needed_after_acceptance"]`. The state determines the valid payload:

| Slot state | Build | Assigned model start | Solver/`x0` evidence | Extracted result | Audit | Reason |
|---|---|---|---|---|---|---|
| `executed` | required | required, including the generated flat start | required; complete `x0` and mapping retained | required, including unsuccessful schema-stable extraction | required | optional |
| `construction_error` | optional | optional | absent | absent | absent | required |
| `source_unavailable` | absent | absent | absent | absent | absent | required |
| `not_needed_after_acceptance` | absent | absent | absent | absent | absent | required |

For `construction_error`, a build may exist when initialization assignment or
canonicalization fails, and an assigned start may exist if failure occurs after
its construction. Neither is fabricated when failure occurs earlier. Once the
canonicalized start has been verified and the solver interface is called, the
slot is `executed` even if the solver errors, returns an ineligible status, or
produces an unusable primal. Such outcomes retain their result and audit rather
than being relabeled as construction failures.

No final `HierarchicalResult` contains a `pending` slot. Pending is permitted
only as private transient orchestration state before a registered slot is
resolved to one of the four public states. Record validation rejects every
state/payload combination not allowed by the table.

#### Exact termination and execution contract

- Only an accepted controlling AC attempt carrying the configured inner
  terminal policy can advance realized state.
- A target-free recovery attempt temporarily removes either the hard terminal
  constraint or the soft terminal cost solely to construct initialization
  data. Its result is never executed.
- Failure of an outer plan terminates before registering an AC window.
- Exhaustion of an AC initialization sequence retains the complete window and
  terminates without advancing state.
- A solver status alone never defines executability; every required
  formulation and participating-device field must be finite and every frozen
  residual gate must pass.
- `solver_certified_infeasible`, `solver_failure`, `unusable_primal`,
  `construction_error`, `source_unavailable`, and
  `not_needed_after_acceptance` remain distinct.
- Realized costs, curtailment, losses, and future ENS use executed first
  intervals exactly once. Predicted window objectives and terminal penalties
  remain attempt diagnostics.

#### S4 checklist

- [x] Accept the S3b evidence and select `shifted_with_recovery` for the M17
  reference workflow while retaining `flat_only`.
- [ ] Approve the input-bundle boundary instead of a mirrored builder
  signature.
- [ ] Approve explicit recovery parameters in the immutable policy bundle.
- [ ] Approve retaining `OPFBuild` objects in the initial public audit tree.
- [ ] Lock exact public type, field, and exported function names.
- [ ] Lock validation and conditional-record rules for all supported devices.
- [x] Separate immutable outer and AC solver configurations and retain their
  normalized values in result provenance.
- [x] Make the initialization policy authoritative by rejecting
  controller-facing `OPFOptions(init_flat=False)`.
- [x] Fix accepted statuses to residual-checked `optimal` and
  `optimal_inaccurate`; do not expose them as policy.
- [x] Define the closed AC-attempt slot states and their conditional payloads.
- [x] Label hard shifted recovery as S3b-validated and soft shifted recovery
  as a supported extrapolation pending a dedicated experiment.
- [ ] Add the typed module and constructor-only tests without implementing the
  solve loop.
- [ ] Review and checkpoint S4 before S5 orchestration begins.
