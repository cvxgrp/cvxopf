# Case118 toy AC look-ahead study: separate design checkpoint

Prepared 2026-09-17. Owner requested this separate, scoped study and clarified
that it varies **look-ahead horizon only**, with hourly updates unchanged.
Plan it before Stage 0b; choose periods and execute after the findings of the
[AC analysis study](case118-toy-ac-analysis-design.md) in Stage 0c.
The comparison choices below are proposals, not numerical launch authority.

## Question and relationship to the first study

Does a modest change in AC look-ahead alter the realized physical operation,
storage timing, common operating cost, or computational effort on selected
toy episodes, relative to the current three-hour controller?

The first study explains observed changes and tests local operational value
under matched three-hour boundaries. It does not compare controller horizons.
Use its episode cards and counterfactual findings to select periods where
look-ahead could matter, and include an ordinary comparison. Preserve both
studies as separate records: a longer one-shot counterfactual window and a
rolling controller with longer look-ahead answer different questions.

This study targets a few informative examples, not an optimal-horizon search,
an annual value estimate, or a change to the Tracy controller by default.

## Period and horizon selection after the first analysis

Proposed scope: at most two informative episodes plus one ordinary comparison.
For each selection record the first study's finding, the unresolved question,
and why changing look-ahead could answer it. Examples include a battery phase
shift extending beyond three hours, substantial observed battery-flexibility
value, or an event whose physical adjustment changes before/after a ramp.
Do not select on favorable horizon-study outcomes or replace failed periods.

Choose the common evaluation span from the event's duration and the charge,
discharge, and recovery context. A calendar day is a candidate, not a mandatory
length. Keep each numerical episode within one shard and retain its boundary
provenance; do not join artificial resets into a continuous experiment.

Proposed horizon set has only three values:

- **One hour**, as an explicit limiting case. With ideal storage and hard
  hourly DC SOC signposts, this fixes active battery movement from one boundary
  to the next. It is not a shorter horizon with unchanged storage flexibility.
- **Three hours**, the present nominal horizon, rerun under the same episode
  initial state and boundary treatment as the other variants.
- **One longer horizon**, selected from the timescale revealed by the first
  analysis, for example six or twelve hours. These are alternatives, not a
  request to sweep both. Freeze the choice before horizon-study solves.

The one-hour anchor and example longer values remain proposed design choices.
Select final values and evaluation spans together: a longer horizon should
have a meaningful interior segment before terminal shortening dominates.
If the first study offers no distinct second informative episode, use fewer
periods rather than fill a quota. Do not multiply the R1/R2/G/B arm matrix by
these horizons; this is a separate controller comparison.

## Common rolling comparison

For an episode `[s,e)`, start every variant from the same device-aligned
`SOC_DC[s]`. Keep the hourly input resolution, one-hour update/execute interval,
exogenous inputs, device/network parameters, objective, and physical acceptance
checks unchanged. Each variant executes only the first action of each accepted
AC window and carries its own resulting physical SOC forward.

Proposed terminal convention: at hour `t`, use window end
`u = min(t + H, e)` and hard terminal target `SOC_DC[u]`. Thus every variant
finishes the episode at the common `SOC_DC[e]`, while no solve extends beyond
the common evaluation end. Record how many steps use shortened windows and
show the interior and terminal portion separately. Do not assume historical
S5 first actions form the matched three-hour baseline: initial states and
end-of-episode treatment can differ, so rerun that baseline with the variants.

All variants use the same outer trajectory and terminal-target rule. Varying H
changes both the look-ahead information and the time/location of the hard SOC
target, hence the allowed storage trajectory. Interpret the result as the
effect of horizon under this signpost policy, not a pure value-of-information
estimate. A different terminal policy is a separate sensitivity, not an
automatic additional arm.

Use the controller's original renewable real-power freedom, including any
permitted curtailment. The first study's fixed-renewable restriction served
its repair/economic decomposition; it should not silently redefine this
controller comparison. Keep renewable rules identical across horizons and
report curtailment explicitly.

Reuse the same two-main/one-helper execution setup and implemented S5 revision-2
escalation ladder as specified in the [AC analysis design](case118-toy-ac-analysis-design.md#owner-directed-execution-setup-two-main-lanes-and-one-shared-helper).
Keep the existing memory gates and numerical tolerances. Parallelize independent
episode/horizon trajectories across main lanes; each trajectory advances hourly
in causal order. The shared helper assists slow solves, not a third independent
trajectory. Bind that shared initialization/recovery procedure in the protocol.
Target-free remains applicable at every horizon, including H=1: the one-hour
case determines battery movement through its terminal equality, without the
separate battery-power locks used in R1/R2/G. Removing that equality therefore
frees movement and can supply a recovery initialization. This does not
guarantee a numerical benefit. Target-free remains source-only; restore and
audit the hard target before accepting any controlling action. The first
study's open fixed-battery recovery decision does not remove this path here.
It must handle different horizon shapes without transferring unmatched
variables or using a future solution from another variant as if it were causal
history. Use each variant's own preceding accepted controller where the chosen
procedure calls for a shifted start. Local-solver outcomes remain part of the
realized policy; do not attribute all differences to physics alone.

## Standard outputs and interpretation

Reuse the first study's input, dispatch, network-margin, and storage plots.
For each horizon report:

- Executed hourly Pg/Qg, battery P/Q/SOC, renewable use/curtailment, physical
  losses, and constraint margins, with bus/device identities.
- Net/L1/opposing generator changes relative to the outer plan, plus direct
  comparisons between matched horizon trajectories.
- Generation and storage-aging costs integrated over the same **executed**
  interval; throughput, losses, and curtailment over that interval; common
  initial/final SOC and independent recurrence/balance checks.
- Changes in common executed cost in objective units, percent of the matched
  three-hour baseline when defined, and per MWh served. Do not sum overlapping
  look-ahead objectives or compare raw objectives over unequal durations.
- Completed/attempted steps, recovery and unresolved outcomes, construction,
  solve/audit time, total elapsed effort, and sampled memory. A cheaper policy
  may be more expensive to compute; keep these outcomes separate.

Equal endpoints remove simple stored-energy depletion as a cost advantage;
they do not establish global optimality or eliminate local-solver and boundary
effects. If a variant stops early, retain its partial trajectory and cause.
Do not present incomplete-period totals as a whole-period economic comparison.
No local failure proves physical infeasibility. Report shared-prefix details
as diagnostics without replacing the intended full-episode comparison.

## Implementation, budgets, and sequence

Reuse the public AC builder, rolling-state/archive/audit support, and standard
episode report. Required additions are a horizon parameter, the explicit
common-end convention, matched episode initialization, and executed-trajectory
comparison. Do not redesign the annual scheduler or add a generic experiment
framework. Verify horizon slicing, end shortening, common initial/final states,
per-variant causal history, hourly execution, and cost integration.

1. Plan and review this separate study before Stage 0b; preserve its required
   source evidence and reusable tooling in the Stage 0b disposition.
2. Complete and review the first AC analysis in Stage 0c. Select the horizon
   periods and values from its findings, with reasons and ordinary comparison.
3. Specify the exact comparison protocol and required small implementation
   changes. Independently review and commit them before numerical execution.
4. Qualify the longest proposed horizon with an explicitly bounded pilot,
   retaining its result. Use measured memory/time and the number of hourly
   controller steps to propose the full study budget before launch.
5. Run only the approved matched episodes. Record completed, unresolved, and
   explicitly deferred questions in the Stage 0c closeout.

The first study's 24 comparison stages and provisional time envelope do **not**
cover this study; neither counts nor budgets imply one solver call per step.
If there are k horizons and episode lengths L_j hours, even with one accepted
solve per step the rolling comparison requires `k * sum_j L_j` controlling
steps; recovery adds attempts. Count pilot work and every retry in the separate
budget. Preserve the shared ladder's per-attempt timing and memory gates;
set total attempts/work, study wall time, and stop behavior once period lengths
and horizons are selected. Do not substitute a new short per-window timeout
for the existing slow-solve escalation. Three-hour memory measurements cannot guarantee that the longer horizon will fit.
Do not start another annual run or expand horizons to obtain tidy outcomes.

The user-managed end-of-Stage-0c PR merge, local-main verification, and fresh
Tracy-study branch gate remain unchanged. No Stage A input generation begins
as part of this design work.

## Review record

Owner direction and sequencing clarified. `cvxopf-discuss` supplied read-only
scientific input on matched periods, terminal inventory, signpost timing,
executed-action accounting, and limited scope. `cvxopf-review` independently
reviewed this design and its links to the first study and governing plan:
CLEAN, no actionable findings. The review covered matched rolling states,
terminal shortening, the one-hour limiting case, renewable policy, causal
initialization, partial results, separate budgets, and stage ordering. Exact
horizons, periods, starts/recovery, and resource limits remain later protocol
choices. That CLEAN review preceded the owner-directed parallel-recovery
amendment; independent narrow review of that amendment also returned CLEAN.
Ready for owner design review;
no numerical launch is authorized.
The subsequent arm-applicability review confirmed this distinction, including
the relevance of target-free at H=1 despite its fixed movement when targeted.
No new solves, artifact promotion, or implementation are performed here.
