# Case118 follow-up — feasibility repair and economic value of AC adjustments

**Status: protocol draft for the approved post-S5 analysis design; no numerical launch.**
Promoted during Stage 0b from the 2026-09-16 proposal. The completed annual toy
record is [S5_REPORT.md](../experiments/case118_annual_hierarchy/S5_REPORT.md).
The owner approved the estimands and episode-first framework in the
[analysis design](case118-toy-ac-analysis-design.md); exact windows, tolerances,
initializations, and compute budget still require the Stage 0c protocol checkpoint.
Preserve the original toy run unchanged. Promotion does not authorize new solves.

**Owner scope update, 2026-09-17:** this first AC analysis remains worthwhile
as a bounded study whose methods will be replicated on Tracy. The separate toy
look-ahead-horizon study is deferred. The toy's c2 = 1e-4 generator curvature
and 1.0 battery throughput penalty do not represent the intended temporal
economics. Interpret economic improvements here as conditional on that
historical objective; neither small nor large battery value transfers to the
revised Tracy model without repeating the analysis. Do not expand this study
into exhaustive toy diagnostics or change its historical costs.
Separate generation-cost change, absolute-throughput change, its penalty cost,
and total cost in the matched report. Rescheduling existing throughput does not
itself increase the cycling penalty. A small observed B-versus-G improvement
applies to the chosen window, fixed endpoints, and historical economics; it
does not establish small value for longer energy shifts or revised costs.

## 1. Question and scope

The observed AC rollout differs from the accepted annual DC realization in two
ways: midday opposing generator redispatch, strongest in summer, and structured
storage/generation timing shifts, particularly on spring/early-summer weekdays.
We want to distinguish:

1. **Feasibility:** how much real-power adjustment is needed to find an accepted
   AC realization of the DC prescription?
2. **Economics:** how much common, AC-evaluated operating cost can be saved by
   additional generator redispatch, and then by allowing storage rescheduling?
3. **Scheduling freedom:** can materially different trajectories be feasible
   with little economic difference?

A large schedule difference is not itself evidence of a large benefit. Small
repair and small cost savings would be an informative result, supporting a
future selective AC-realization policy. No result here establishes security
under contingencies, annual reliability, global AC optimality, or a deployment
rule for skipping AC solves.

This is a small, deliberately post-hoc diagnostic of selected windows, not an
annual savings estimate or an unbiased estimate of how frequently AC matters.

## 2. Common model and matched boundary conditions

Use the accepted conditioned Case118 S4 outer archive and the exact annual
fixture: generator costs, device identities, hourly profiles, network data,
storage ratings, and load-service requirements. Retain the generator curvature
used in the completed experiment; do not retune costs to sharpen this result.

All arms for a selected interval `[t, t+W)` use:

- the **same AC formulation**, physical limits, exogenous inputs, and duration;
- `delta = 1 hour` and initially `W = 3`, matching the rollout's nominal window;
- initial storage state `soc_DC[t]` and hard terminal state `soc_DC[t+W]`,
  aligned by device ID;
- identical generator active/reactive limits, inverter apparent-power circles,
  voltage bounds, and both-terminal branch apparent-power constraints;
- unchanged nonsheddable loads and the complete frozen M17 AC acceptance gate;
- freely optimized reactive controls and voltages, not inherited DC placeholders;
- fixed renewable real-power dispatch equal to the accepted DC trajectory,
  with renewable reactive power still free within its original capability.

Fixing renewable real output isolates generator/storage effects. Conclusions
are conditional on that restriction. If it prevents an accepted realization,
report the unresolved restricted comparison; do not silently introduce
curtailment. An explicitly labeled renewable-flexibility arm would be a later
protocol amendment, not an automatic rescue.

**Why start from DC SoC?** The annual AC rollout has already accumulated state
differences. Starting from its realized state while fixing DC battery power
could contradict the common terminal target before network physics is even
considered. The matched DC start makes the DC battery schedule exactly
endpoint-compatible, subject to the frozen numerical tolerances.

These counterfactuals do not replay the exact historical S5 state. Its retained
first actions identify interesting windows and provide descriptive context;
they are not interchangeable with a matched-window optimum. Do not sum the
first actions of three overlapping S5 windows and call that one three-hour
optimization result.

## 3. Nested counterfactuals

Let `g_DC`, `b_DC`, and `r_DC` denote retained dispatchable generation, battery
power (positive discharge), and renewable dispatch over the window. Let
`C_AC(x)` be the common integrated device operating cost evaluated on an
accepted AC solution. Define generator departure in engineering units:

```text
D_g(x) = delta * sum over hours and generators |g(x) - g_DC|  [MWh]
```

| Arm | Battery real power | Generator real power | Optimization purpose |
|---|---|---|---|
| R1: small-repair search | Fixed to `b_DC` at every hour/device | Free within original limits | Minimize `D_g` under exact AC constraints |
| R2: economical small repair | Fixed to `b_DC` | Free, with `D_g <= D_R1 + epsilon_repair` | Minimize common `C_AC` within the retained repair budget |
| G: generator flexibility | Fixed to `b_DC` | Free within original limits | Minimize common `C_AC`, without the repair budget |
| B: generator and battery flexibility | Free within original power/SoC limits and common endpoints | Free within original limits | Minimize common `C_AC` |

Renewable real dispatch is fixed to `r_DC` in every arm. Battery reactive
power remains free even when its real power is fixed. Other common device and
network constraints are unchanged. R1's departure objective is a diagnostic
objective; its physical solution is also evaluated under `C_AC`.

The R2 stage is essential: a least-deviation solution need not be economical
among equally small repairs. Do not attribute avoidable R1 tie-breaking cost
to the value of generator flexibility. `epsilon_repair` is a separately
declared MWh optimization tolerance, frozen before solves, not an adjustable
physical-acceptance tolerance.

The underlying feasible sets for R2, G, and B are nested. Retain an accepted
R2 solution as a feasible incumbent for G, and an accepted G solution as an
incumbent for B. Fresh solves may improve them; worse or failed local solves
must not erase the incumbent. Report both the new solver outcome and the
best retained feasible candidate, including whether no improvement was found.
Initialization transfers require full model-variable identity/shape checks;
capture the complete reduced IPOPT starting vector actually supplied.

### Loss balancing is not opposing redispatch

Fixing every DC active-power setpoint would generally violate AC balance
because the DC model does not procure real losses through its nodal equations.
Do not count that trivial failure as evidence of a need for large spatial
redispatch. All arms allow generator active-power adjustment and report, for
each hour:

```text
net generation change       = sum_g(g_AC - g_DC)
generator L1 change         = sum_g |g_AC - g_DC|
opposing redispatch         = (generator L1 - |net generation change|) / 2
```

Reconstruct the full AC/DC aggregate balance, including renewable output,
storage injections, and modeled shunts, to explain the net difference. Label
loss and shunt terms explicitly rather than assuming all net change is branch
loss. In G/R arms, battery and renewable real powers are fixed; in B, their
balance contribution must still be accounted for.

R1 minimizes total generator departure, **not opposing redispatch alone**.
Physical losses also depend on dispatch. The retained result is a feasible
small-repair witness, not a globally certified minimum necessary repair.
Failure to find an accepted R1/R2/G solution is unresolved, not proof of AC
infeasibility. B may still be attempted and retained as a witness, but missing
restricted comparators prevent the corresponding economic decomposition.

## 4. Cost comparison and scientific outputs

Compute all costs from the same AC device-cost expressions and independently
reconstruct them numerically, with identical integration and terminal treatment:

```text
C_AC = delta * sum_t(component stage-cost rates) + terminal cost once
```

Retain generation and storage-aging costs separately, plus any other present
component/terminal terms. R1's deviation objective and R2's repair-budget
constraint are not component costs. Do not add a DC flow-loss proxy to AC cost;
physical losses already affect AC generation. Do not subtract an archived DC
objective from a new AC objective and call the difference an AC benefit.

For accepted comparable candidates, report:

- `C_R2 - C_G`: observed gain from relaxing the generator-departure budget;
- `C_G - C_B`: observed gain from permitting storage rescheduling;
- `C_R2 - C_B`: total observed gain from relaxing both restrictions;
- each gain in objective units, percent of the R2 baseline when nonzero, and
  per MWh of served load; do not assume inherited cost units are calibrated
  market dollars;
- generator net/L1/opposing changes; battery power L1 and signed changes;
  every SoC boundary, throughput, losses, and binding/near-binding physical
  constraints;
- all independent residuals and added schedule-lock/repair-budget residuals;
- solver status, iterations, termination, wall times, complete starts, and
  whether the selected candidate was a new solution or retained incumbent.

Because AC is nonconvex, an observed feasible cost reduction demonstrates an
available improvement; lack of improvement does not prove none exists.
Feasibility tolerance is not an optimality certificate. Explain small gains in
light of retained solver convergence and cost-reconstruction accuracy; do not
claim they resolve the global economics.

Evaluate **whole matched windows**. First-action costs may be reported for
context but cannot price a temporal shift fairly. Fixed equal endpoints avoid
crediting one arm for depleting more stored energy. Three-hour windows establish
local value under a common signpost, not the value of redesigning an entire day.
The order G then B assigns interactions to the incremental battery-flexibility
step; this is a conditional decomposition, not a unique causal allocation.

## 5. Episodes first, then a small matched-window sample

Inspect approximately 48 hours of surrounding retained data before selecting
exact solve windows. The current design proposes six episode cards: three diagnostic episodes covering
spatial, temporal, and joint behavior, and three ordinary comparisons, then six three-hour matched
windows (24 comparison stages, **not** 24 solver calls). Follow the
[approved design](case118-toy-ac-analysis-design.md) for category allocation
and episode evidence; exact dates and eligibility are settled in the protocol.
A longer context view does not itself authorize longer counterfactual solves.
A longer common-endpoint solve requires a specific unresolved question and
its own comparison and resource budget.

The original eight-window ranking was a proposal awaiting episode review; it
is superseded as the default selection workflow, not an executed experiment.
Its opposing-generator and battery L1 ranks remain possible descriptive
screening covariates. Do not select only isolated three-hour extremes without
checking the surrounding charging, discharge, congestion, and recovery episode.

Use the accepted final 8,760-hour toy record. Keep windows within the declared
source and storage boundaries. Retain month, weekday, hour, load/net load,
renewable conditions, branch-loading distribution, DC storage states/power,
endpoint movement, historical recovery policy, and operator interventions.
Do not replace selected windows because a new solve fails. Ordinary episodes
are descriptive comparisons, not randomized or necessarily season-matched controls.

Freeze the episode cards, candidate table, exact intervals, selection-script
hash, source artifact hashes, and matched inputs before counterfactual solves.
Existing dashboard correlations motivate the study; they are not confirmatory
evidence. Do not extrapolate annual value from this outcome-selected sample.

## 6. Implementation and execution gates

Implement in a separate experiment directory after S5 closure, using public
build/solve and result APIs. Append schedule constraints against correctly
identified variables; generator optimization variables are per-unit, whereas
retained Pg and storage variables use MW. Retain the underlying production
cost expressions when replacing the R1 objective. Do not alter core device
costs, the authoritative S4 trajectory, S5 archives, or notebook collectors.

Required low-cost verification before execution:

- identity alignment and units for generator/renewable/battery locks;
- fixed DC storage power reproduces every DC SoC boundary and terminal target;
- same exogenous inputs, physical constraints, and costs across all arms;
- R1 absolute-departure epigraph and R2 budget are modeled/audited correctly;
- feasible-incumbent transfer preserves constraints and objective accounting;
- independent complete M17 AC audit, including reactive balance, inverter and
  branch limits, curtailment/branch-loss checks, SoC recurrence, and endpoint;
- non-unit-delta unit test for departure units and cost integration, despite
  the authoritative scenario being hourly;
- unsuccessful solves retain unavailable-result schema, exception/status,
  deadline evidence, and partial artifacts without being called infeasible;
- immutable artifact publication and read-only source access.

Before launch, the protocol must freeze exact IPOPT options, initialization
and incumbent-transfer order, `epsilon_repair`, added-constraint tolerances,
cost-reconstruction tolerance, per-stage wall/RSS limits, and total study
budget. Numerical acceptance must retain the frozen physical tolerances.
Reuse the reviewed S5 execution arrangement: two main workers and one shared
helper, with independent matched windows parallelized and each window's arm
chain ordered. Retain `causal_first_speculative_v1` revision 2, persistence,
resource admission, and the escalation ladder described in the
[analysis design](case118-toy-ac-analysis-design.md#owner-directed-execution-setup-two-main-lanes-and-one-shared-helper).
All helper calls, retries, and alternatives count against the reviewed budget.

Arm-specific initialization still needs the protocol decision recorded in
that design. For R1/R2/G, fixed battery real power plus initial SoC and recurrence
already imply terminal SoC: removing only the terminal equation adds no physical
freedom. A target-free solve is therefore not a substantive relaxation for these
arms; any use as a representation heuristic is unproven. Prefer the explicit
arm-specific adaptation using applicable primary/prior-arm starts, preserving
battery locks and the R2 repair budget. Do not silently disable dependent helper
starts. In B, target-free remains relevant as an initialization source because
battery power is free, but only the fully target-conditioned audited result can
be a scientific candidate. These choices must be reviewed before execution.
An interrupted or timed-out optimization is not an infeasibility certificate.

Separate build/canonicalization, solve, audit, and archival timing. This is a
feasibility/economics experiment, not a solver-runtime benchmark. Bind clean
execution commit/source fingerprint, machine/software context, source annual
record, matched-window manifest, solver configuration, and analysis provenance.
Raw results stay in a separate ignored directory; a compact reviewed report
may be tracked later. No new run may overwrite previous evidence.

## 7. Interpretation and stopping rule

| Observed result | Supported interpretation |
|---|---|
| Small feasible repair and small economic improvements | Large rollout coordinate changes may not carry much local operational value |
| Small repair but material feasible cost improvement | AC realization can be economically useful even when feasibility repair is easy |
| Large retained repair, or restricted solves unresolved | Potential feasibility/solver difficulty worth localizing; not proof that large repair is necessary |
| Accepted G and cheaper B | Storage rescheduling offers an observed local economic benefit under the common endpoint |
| Restricted solves unresolved but B accepted | Flexible storage provides an AC witness; necessity remains unproven |

Report cost-gain curves/tables rather than inventing a post-hoc economic pass
threshold. If an operational materiality threshold is desired, the owner must
choose it before viewing these counterfactual outcomes. No automatic policy
promotion follows from a favorable result.

Stop after the reviewed matched-window record. Only then consider a bounded
follow-up: a longer common-endpoint window for the daily timing hypothesis,
renewable flexibility, a matched-state rollout diagnostic, or prospective
testing of DC-based AC-invocation scores. Do not expand this first study into
annual counterfactual AC execution or a global-optimality research project.

## 8. Review checklist and next handoff

- [x] S5 final scientific record available; Stage 0b source disposition is recorded separately.
- [x] Owner approved the estimands and episode-first initial framework; exact sample remains pending.
- [ ] Counterfactual protocol, selected-window manifest, and exact budgets frozen.
- [ ] Implementation and low-cost qualification independently reviewed.
- [ ] Clean implementation committed and separate numerical launch authorized.
- [ ] Every attempted stage retained; accepted solutions independently audited.
- [ ] Compact report separates feasible witnesses, observed savings, unresolved
      outcomes, historical schedule differences, and nonconvex limitations.

Related records: [Tracy transition plan](case118-tracy-2021-study-plan.md),
[annual study plan](experiment-case118-annual-hierarchy.md),
[S5 protocol](../experiments/case118_annual_hierarchy/S5_PROTOCOL.md), and the
accepted S4/S5 records and amendments resolved at the post-run freeze.
