# Stage B — DC comparison results and discussion

## Scope of these notes

These notes capture the owner's observations while exploring the completed
72-arm batch in `results/stage_b_maxiter5000/` using
[the results notebook](results_notebook.py). The comparison design is recorded
in [the Stage B protocol](STAGE_B_PROTOCOL.md). The retained batch's
`binding.json`, `study-result.json`, and per-arm `result.json.gz` files identify
the inputs, settings, and numerical results.

This is an initial qualitative discussion, not a complete quantitative
closeout. The precise segment and selector combination viewed during the
conversation were not recorded; the observations below describe the example
being inspected, not a claim that every arm behaves identically.

## Observations from the results explorer

### Generator curvature has surprisingly little aggregate effect

The owner found the similarity between generator curvature “off” and “on”
to be the largest surprise. Stronger quadratic generation costs were expected
to produce more visibly different net-load leveling by the batteries, but the
aggregate trajectories looked similar.

Here, “off” means rho = 0.001 and “on” means rho = 1/3: weak versus strong
curvature, not exactly linear versus quadratic costs. The quadratic cost is
applied to individual generators, not directly to aggregate generation.
Consequently, similar aggregate trajectories need not imply similar dispatch
among individual generators.

Possible explanations include incentives already supplied by the linear
generation costs and avoidance of renewable curtailment, and operating limits
that restrict how much storage timing can change. These are interpretations
to investigate, not mechanisms established by the visual comparison alone.

### Copper plate changes dispatch levels more than temporal behavior

In the inspected example, the main aggregate difference between copper plate
and the lossy-DC network solution was the dispatchable generation level.
Copper plate could achieve lower aggregate dispatch, including periods with
near-zero dispatchable generation when the network solution still required
nonzero generation.

Despite this difference in level, the temporal behavior looked nearly
identical in important respects: bulk charging and discharging behavior and
the timing of dispatchable generation schedules were largely preserved.
This is a qualitative similarity, not equality of trajectories or a constant
offset between them.

A plausible explanation is that the network requires local generation where
imports are constrained, even when renewable energy is available elsewhere.
Confirming this explanation for the observed hours requires inspecting branch
flows, congestion, and curtailment. Aggregate storage agreement can also hide
differences between individual batteries.

## Implications and directions to explore

### Copper-plate planning for MPC

The owner noted that the preserved temporal behavior is promising for a
computationally practical model predictive control (MPC) formulation. A cheap
copper-plate planner could capture the dominant storage timing, repeatedly
update its plan from realized SoC and forecasts, and provide guidance to a
network-aware execution layer.

The planner would not be the final authority on feasible network dispatch.
The relevant next question is whether its guidance produces useful,
network-realizable first actions under closed-loop execution. Similar
full-horizon aggregate schedules alone do not establish that result or the
feasibility of device-level SoC signposts.

### Spend computational savings on additional dimensions

The owner also highlighted the opportunity to expand in other dimensions,
including multiple scenarios and Monte Carlo simulations. Reduced network
detail could make room for:

- Scenario-based MPC that chooses a shared immediate action across several
  possible futures, with later decisions respecting what is known at the time.
- Monte Carlo evaluation of a controller over many realized input and event
  trajectories, measuring distributions of cost, shedding, and recovery.
- Sensitivity studies of storage sizing, forecasts, and operating policies.

Scenario-based optimization chooses actions under uncertainty; Monte Carlo
evaluation measures how a policy performs across realizations. Neither has
been demonstrated by this deterministic comparison batch. The motivating
tradeoff is to retain useful temporal behavior while spending less computation
on spatial detail and more on uncertainty and repeated evaluation.

These are research directions, not a change to the approved study: single-node
remains a comparison formulation, and lossy DC remains the planned source of
SoC signposts for full-physics AC realization.
