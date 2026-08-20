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
| S4 | Define typed public controller inputs, policies, records, and result schema. |
| S5 | Implement hierarchical orchestration above the existing public builders. |
| S6 | Verify state alignment, failure paths, multiple-storage identity, `W=1`, and final truncated windows. |
| S7 | Re-run the frozen experiment through the public API and establish window-by-window equivalence. |
| S8 | Complete documentation, example, flowchart update, and milestone handoff. |

P1 is prerequisite device-API hardening, not hierarchical-controller
implementation. S3 results must be reviewed before S4 freezes public defaults.
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

**Complete; checkpoint commit pending.**

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
