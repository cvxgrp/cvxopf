# Matched numerical-preparation comparison

Draft, 2026-10-13. Planning only: no implementation or numerical execution is
authorized. Schedule after the completed E4 study's evidence is finalized;
preserve all historical results. Parent: [M11](../../plans/milestone-11-socp.md).

## Question

How much does the current numerical representation improve accepted-solve
reliability, runtime, and returned economic quality on the **same physical
problem**? Test the observation that preparation made 24-hour problems tractable
and three-hour problems easier, without presupposing a favorable outcome.

This is a fresh within-formulation comparison, not AC versus SOCP accuracy,
an MPC rollout, or a recreation of an old software environment. The
[earlier qualification](QUALIFICATION_REPORT.md) and
[three-hour comparison](TRACY_THREE_STEP_REPORT.md) motivate it but contain
historical accounting qualifications and are not matched timing controls.

## Small proposed matrix: 16 arms

Use the unchanged E3 energy-neutral fixtures below, chosen as a motivating
deficit case and a contrasting transition, not selected from new solve outcomes.

| Fixture | Three-hour input rows | 24-hour input rows |
| --- | --- | --- |
| Large deficit | [1165, 1168) | [1165, 1189) |
| Deficit → surplus | [2439, 2442) | [2439, 2463) |

For each: AC and SOCP, each with baseline and prepared representation. Every
horizon starts and ends at **50% capacity per battery**, with delta = 1 hour.
The three-hour problems have their own terminal obligation; they are not
prefixes of a 24-hour optimized trajectory or retained E4 controller states.
Keep E3's 27 batteries, device identities, profiles, network, shedding permissions,
prices, generator curvature and cycling weight. Use global half-open indexing
and the source's fixed UTC−08:00 calendar.

| Treatment | Numerical representation |
| --- | --- |
| Baseline, both models | `NumericalPreparation()`: original device/cost coordinates, fixed coordinates retained, no canonical scaling, hourly objective assembly |
| Prepared AC | Device normalization, exact fixed-box removal, cycling/shedding cost coordinates, `component_first` objective assembly, no canonical scaling |
| Prepared SOCP | Device normalization, exact fixed-box removal, `joint5` canonical scaling, original cost coordinates and hourly objective assembly |

Use the same current production source and dependencies for both arms, through
`OPFBuild.solve()`. Keep space/time vectorization and automatic-sparse-dispatch
settings identical. This tests the **preparation bundle**, not which individual
transformation caused a change; ablation is outside this short study.

## Matching and acceptance

- Freeze actual input hashes, effective policies, solver versions/settings and
  audit thresholds before execution. Reuse the [prepared E3 controls](../case118_tracy_2021/E3_PREPARED_PROTOCOL.md):
  IPOPT for AC and stock CLARABEL/QDLDL for SOCP, including their existing
  iteration limits and tolerances. Apply identical settings within each pair;
  scaling can change their physical interpretation, so independently audit
  original-unit residuals rather than equating native tolerances with accuracy.
- Use one deterministic cold physical AC start per fixture/horizon, mapped into
  each representation, retaining the actual complete canonical start and maps.
  SOCP uses the same native cold-start policy. No solution-derived starts,
  recovery ladders, hourly fallback, retuning, or additional attempts to improve
  a result. Check physical input/objective equivalence before launching.
- Apply the same current independent original-unit network/device, SoC/endpoint,
  served-load/ENS and cost-accounting gates to both representations. Preserve
  native versus physical objective distinctions and separately report explained
  cycling slack. Reuse E3's audited accounting rules, not the superseded early
  qualification gate. SOCP acceptance is lifted-model feasibility, not AC
  realizability; native convergence is not certified global optimality.

## Bounded execution and retained evidence

Propose serial fresh-process workers, one numerical thread, and a deterministic
pair order alternating baseline-first and prepared-first **within each formulation**
(two of each order for AC, and two of each for SOCP). Owner-approved budgets
(2026-10-13) are **one hour per whole worker** at both T=3 and T=24, 16 GiB
sampled RSS, and an **advisory warning at 16 hours (57,600 s) cumulative supervised
worker effort**, not an automatic stop. Every worker retains its full one-hour
allowance; interrupted effort and cleanup overrun remain counted. The operator
may stop the study at any time. Solver iteration limits,
tolerances, physical constraints and acceptance gates are unchanged. Numerical
failures/timeouts dispose an arm and do not suppress its partner; infrastructure
faults stop safely. Commit and launch gates still apply.

The original, unexecuted proposal was 300 s at T=3 and 600 s at T=24, totaling
7,200 s (two hours) across eight workers of each horizon. That proposal is
superseded by the owner-approved budgets above; it was not the E4 rollout budget.

Reuse existing fixture, prepared-solve, physical-start, audit, archive and process
helpers; do not copy an entire runner. Provide disk-backed results/logs, atomic
publication, status and repeatable resume that reuses completed arms. Retain
interrupted attempts and consumed effort. Operator/infrastructure interruptions
leave the arm pending; an explicitly authorized resume restarts that same arm
from the frozen cold start in a fresh attempt, without skipping it or requiring
a replacement reservation. There is no automatic restart while stopped and no
retry of completed numerical/audit rejections or one-hour timeouts. Rehearse
stop/resume and publication without OPF
solves. Admit stable history once, keep per-arm science checks, and record build,
preparation/solve, audit/archive and total wall clocks separately. Retain native
iterations/convergence, sampled RSS, starts, restored primals and cost components.
Record hardware/load/cooling context; optional thermal telemetry is advisory.
Keep raw evidence in a separate ignored `results/matched_comparison_001/` directory.

## Report and stopping rule

Produce one all-arm table and paired runtime/objective figures, reporting native
status, independent acceptance, residuals, physical costs, ENS, iterations and
memory. Give runtime ratios only for pairs accepted on both sides; show failures
and timeouts explicitly. A timeout supplies a censored bound, not a measured
completion time. Report feasible-but-accounting-rejected outcomes separately.

One pass is a bounded case study, not a runtime-distribution estimate. Different
accepted AC objectives may reflect different local solutions; equal-cost
trajectories need not be identical. No universal speedup, global AC optimality,
infeasibility from solver rejection, or attribution to one preparation component.
Stop after the declared matrix or an operator stop; crossing the cumulative-time
warning alone does not stop useful computation. Additional windows, repetitions or
ablations require a separate decision. Next: review this short design, implement
and rehearse the reused runner, then obtain owner commit and launch approval.
