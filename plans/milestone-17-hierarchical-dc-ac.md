# Milestone 17 — Hierarchical DC→AC receding-horizon dispatch

The capstone milestone: the concrete implementation of the project vision
stated in the README ("solve the convex `lossy_dc` formulation over the full
planning horizon ... then use the AC formulation over a short receding horizon
to verify and correct for true network physics, with SoC targets inherited
from the convex layer as boundary constraints").

Two-layer structure:

- **Upper layer — long-horizon plan.** Solve `lossy_dc` (convex, globally
  optimal) over the full multi-day horizon. Extract the SoC trajectory
  `soc*(t)`.
- **Signposts, not setpoints.** Only the **SoC waypoints** are passed down to
  the AC layer — *not* generator dispatch, voltages, or branch flows. The AC
  layer re-optimizes everything else against true network physics; it is only
  told what stored energy to arrive at, at each checkpoint. Passing full
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
- **Depends on M4** for AC branch-flow limits. Without thermal limits, the
  lower layer can check nonlinear power flow, voltage bounds, and device
  feasibility, but cannot support the stronger claim that the DC plan is
  executable on the transmission network.
- **Subsumes the convex-tracks-AC validation study.** The open-loop
  special case (single AC window, no recession; replay the DC SoC plan through AC and
  measure the feasibility/correction gap) is the natural validation artifact of
  this milestone — it is the currently-unfilled "temporal × cross-formulation"
  cell. The `case9_storage_{ac,dc}_24h.py` examples already supply ~80% of its
  inputs (identical 24h scenario in both formulations, each self-verifying its
  own SoC dynamics and operating region).

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
8. re-solve or advance the outer plan according to the specified replanning
   rule; and
9. repeat through the experiment horizon.

This manual runner is an executable design specification. It must resolve and
freeze:

- DC-to-AC state-index alignment;
- exactly which quantities cross the layer boundary;
- hard equality, reserve-floor, and soft-target behavior;
- realized-state feedback;
- outer-plan replanning cadence;
- behavior after AC infeasibility or an unattainable terminal target;
- intermediate-build and trajectory retention; and
- diagnostic and result schemas.

The outer DC plan is responsible for selecting coherent start and terminal
SoCs. Short AC windows do not independently reconstruct charging opportunities
that occurred before their inherited initial state.

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
- open-loop replay and closed-loop replanning;
- DC-versus-AC battery, generation, renewable, voltage, and physical-loss
  corrections;
- terminal deviation, solve status, constraint violation, and runtime.

Window length is selected for AC correction and computation. Energy
opportunities before a window are represented in its inherited DC-planned
initial state; an arbitrary half-capacity reset is not an M17 boundary
condition.

Forecast error, contingencies, load shedding, and AC thermal limits are
important to the larger battery-resilience thesis but should not all be added
to the first orchestration experiment. Once the hierarchy itself is validated,
those become controlled resilience-study extensions.

### Experiment acceptance gates

#### Gate 1 — frozen protocol

- Scenario, device system, horizon, window length, policies, and metrics are
  fixed before the public M17 implementation.
- State indexing is documented with a small hand-checkable example.
- Manual execution is deterministic up to documented solver variability.
- Every solve and state transition is retained for audit.

#### Gate 2 — open-loop realization

- DC-planned endpoint pairs are tested in aligned AC windows.
- Deviations in battery trajectory and active dispatch are reported.
- Physical AC losses and voltage constraints are distinguished from DC
  objective penalties.
- AC local-optimality and missing thermal-limit caveats remain explicit.

#### Gate 3 — closed-loop execution

- The first AC action, not the full predicted window, updates realized state.
- Outer replanning begins from that realized state.
- Hard-target failure and soft-target deviation have explicit, tested
  behavior.
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
