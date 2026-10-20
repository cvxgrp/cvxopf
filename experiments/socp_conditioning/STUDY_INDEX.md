# SOCP conditioning study record

Updated 2026-10-06. This index follows the tested paths from model-level scaling
to native linear algebra. Normalized device cones plus joint input scaling have
produced useful improvements, and the contrasting deficit case reaches `Solved`
with exact fixed-bound cleanup. The difficult surplus case is not fully fixed.
Five-pass native QDLDL scaling worsened convergence. The subsequent native
SuperLU trial improved the gap approximately 139-fold but stopped with a
cone-scaling-stage numerical error before satisfying the frozen gap tolerance.
An exact observational replay now identifies cancellation in the SOC interiority
calculation: a strictly interior dual vector is evaluated as having zero margin.
Compensated square-root fallback corrects that check, but unchanged step-length
and corrector operations still encounter the same zero determinant. The final
iterate and gap remain unchanged; a consistent shared-primitive test is next.
Neither native intervention is qualified for adoption.

All work belongs to the authorized isolated worktree based on
`dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`. Reports and experiment code are
reviewable but uncommitted; raw records stay in ignored `results/`. Production
sources, installed solver packages, and main-study evidence are not modified.
Historical report recommendations describe the decision at that point in the
investigation; the sequence below records the subsequent disposition.

## Final report requirement

Every test must end with a saved final report, including negative results,
aborted attempts, and offline diagnostics. A terminal chat response or raw JSON
alone is not the final report. A declared multi-arm test may share one report,
provided every arm and any failed/repaired attempt is accounted for.

Each report must retain:

- The question, comparator, exact intervention, and unchanged problem/settings.
- Scenario, horizon, solver/version, source identity, limits, and run location.
- Native termination, physical acceptance, objective/accounting checks, and
  timing/memory observations appropriate to that test.
- What the result supports, what remains unresolved, and the decision to retain,
  reject, or investigate further. Distinguish inference from measured evidence.
- Reproduction entrypoints, artifact paths/hashes, verification, and any repair,
  interruption, or departure from the declared procedure.

Before another test begins, declare its changed factor and limits. After it
ends, complete its report and update this index before treating the test as
closed. Keep original records immutable; record later analysis separately.

## Tested paths and outcomes

Evidence locations below are relative to `results/`. The linked reports contain
the quantitative tables and scientific qualifications.

| Test | Evidence | Outcome and disposition | Final report |
| --- | --- | --- | --- |
| Case9 normalization and objective-scaling smoke test | `diagnostic_001` | All native solves succeed, but objective-only scaling fails cross-arm agreement; initial ladder stops. | [Initial diagnostic](REPORT.md) |
| Case118 surplus four-arm comparison | `diagnostic_002` | Normalized capability cones improve physical evidence; native objective alone does not settle accuracy. | [Surplus comparison](SURPLUS_REPORT.md) |
| Whole-objective divisor sweep | `sweep_001` | Larger divisor can report success while original-unit objective evidence worsens; not a general remedy. | [Objective sweep](SWEEP_REPORT.md) |
| Offline epigraph and optimality reconstruction | `optimality_audit_003.json` | Localizes epigraph excess, original-unit stationarity errors, and large cancelling fixed-bound multipliers. | [Optimality audit](OPTIMALITY_REPORT.md) |
| Exactly coincident bounds as equalities | `fixed_boxes_001` | Reduces the identified fixed-bound mechanism but does not fix convergence by itself. | [Fixed bounds](FIXED_BOX_REPORT.md) |
| Native equilibration off and narrower bounds | `equilibration_001`, `equilibration_001_remaining` | Disabled arm has no primal; narrower scaling improves some metrics and worsens stationarity. No accepted remedy. | [Equilibration](EQUILIBRATION_REPORT.md) |
| Default MOSEK comparison | `mosek_001`, `mosek_002` | Native optimal status does not pass original-unit physical/accounting gates. | [MOSEK](MOSEK_REPORT.md) |
| Default COPT comparison | `copt_001`, `copt_002` | Native optimal status does not pass physical/accounting gates. | [COPT](COPT_REPORT.md) |
| Remove optional load shedding across three solvers | `no_shedding_001` | Separates shedding artifacts from the remaining numerical difficulty; does not qualify a universal fix. | [Fixed loads](NO_SHEDDING_REPORT.md) |
| Solver-input matrix conditioning | `matrices_001`, `matrices_002`, `matrices_003` | Large scale disparities motivate equivalent scaling; condition estimates alone do not diagnose termination. No solves. | [Matrix inspection](MATRIX_REPORT.md) |
| Cone-preserving constraint-only scaling | `cone_scaling_001`, `cone_scaling_analysis_001.json` | Improves A's condition estimate but worsens objective and stationarity; reject this rule. | [Constraint scaling](CONE_SCALING_REPORT.md) |
| Joint objective and constraint scaling | `joint_scaling_001`, `joint_scaling_analysis_001.json` | Substantial original-unit accuracy improvement; surplus arms still `AlmostSolved`. | [Joint scaling](JOINT_SCALING_REPORT.md) |
| Stopping criteria, materiality, contrasting deficit | `joint_deficit_001`, `joint_stopping_surplus_001.json`, `joint_stopping_deficit_001.json` | Unchanged rule helps both conditions; gap remains the limiting full-success criterion. | [Joint follow-up](JOINT_FOLLOWUP_REPORT.md) |
| Joint scaling plus exact bounds as equalities | `joint_fixed_boxes_001` | Deficit accepted; surplus not fully converged. Useful but nonuniform improvement. | [Combined cleanup](JOINT_FIXED_BOX_REPORT.md) |
| Substitute exactly fixed coordinates | `fixed_substitution_001` | Smaller equivalent problem; deficit remains accepted, surplus remains `AlmostSolved`. | [Fixed substitution](FIXED_SUBSTITUTION_REPORT.md) |
| Three-solver detailed termination diagnostic | `termination_probe_001` | CLARABEL and MOSEK stall; COPT optimal status fails physical gate. No fully accepted arm. | [Termination](TERMINATION_REPORT.md) |
| Lower CLARABEL minimum-step cutoff | `minimum_step_001` | No change in final x/s/z or stopping iteration; cutoff change is not the fix. | [Minimum step](MINIMUM_STEP_REPORT.md) |
| Native cone step and refinement tracing | `native_step_probe_001`, `native_step_probe_002` | Exact baseline reproduction; catastrophic linear errors precede the tiny allowable step. | [Native trace](NATIVE_STEP_REPORT.md) |
| Capture KKT, independent LU, high-precision residual checks | `kkt_capture_001`, `kkt_factorization_001`, `kkt_residual_check_001` | Independent LU avoids native catastrophic directions but still misses some requested linear tolerances. | [KKT factorization](KKT_FACTORIZATION_REPORT.md) |
| Saved KKT scaling with 0, 5, 10 passes | `kkt_scaling_001` | Five passes improve the affine solve, not the constant RHS; motivates a full native-path test. No optimization calls. | [Saved KKT scaling](KKT_SCALING_REPORT.md) |
| Five-pass scaling inside native QDLDL | `native_kkt_scaling_001` | Exact disabled control; enabled arm stops earlier with a much larger gap. Reject this variant. | [Native KKT scaling](NATIVE_KKT_SCALING_REPORT.md) |
| Replace native QDLDL with pivoted SuperLU | `native_lu_probe_001` | Exact control; gap improves 139-fold, physical/accounting checks pass, but NumericalError leaves the full gate unmet. | [Native LU](NATIVE_LU_REPORT.md) |
| Capture cone scaling failure and reconstruct exact margins | `cone_interiority_001` | Exact SuperLU replay; buses 77–78 at interval 3315 have a strictly interior dual vector whose native norm rounds away the margin. Arithmetic diagnosis, not a tested remedy. | [Cone interiority](CONE_INTERIORITY_REPORT.md) |
| Compensated SOC square-root determinant fallback | `compensated_cone_001` | Ten native arithmetic fixtures pass; two false zeros corrected. Next factor succeeds, but unchanged determinant callers cause zero step/nonfinite RHS; no new iterate or accepted optimum. | [Compensated determinant](COMPENSATED_CONE_REPORT.md) |
| Shared signed/shifted compensated SOC determinants | `shared_cone_001` | Twelve native arithmetic cases pass; gap improves 2.26×, but an interior exact step rounds to an exterior dual cone point. Physical checks pass; native NumericalError remains. | [Shared determinant](SHARED_CONE_REPORT.md) |
| Practical SuperLU check at relative gap 1e-9 | `practical_lu_001` | Surplus and contrasting deficit both native Solved with unchanged physical/accounting gates; surplus vectors exactly match prior SuperLU. Compensated arithmetic disabled. Experimental backend, not production or three-solver qualification. | [Practical convergence](PRACTICAL_LU_REPORT.md) |
| Stock CLARABEL QDLDL and Faer | `builtin_linsolvers_001` | Both accept deficit; neither reaches 1e-9 on surplus. All physical residual checks pass. Faer is not a replacement for the observed SuperLU improvement; Pardiso unavailable, not tested. | [Built in backend comparison](BUILTIN_LINSOLVER_REPORT.md) |
| Four original conditions with shedding and relative gap 1e-6 | `four_conditions_001` | All eight QDLDL/Faer arms native Solved and physically accepted with only interventions 1, 2 and 4. Four pass the complete gate; deficit/ramp-up pairs exceed the unchanged absolute canonical-objective check by small relative amounts. | [Four condition results](FOUR_CONDITIONS_REPORT.md) |

## Repairs and supplemental analyses

Failures are retained rather than silently replaced by successful retries:

- MOSEK 001 completed a solve before a result-publication failure; 002 is the
  unchanged numerical repeat. See the MOSEK report's publication-repair section.
- COPT's first record required evidence-publication repair; both roots and the
  relation between them remain described in the COPT report.
- The equilibration no-primal outcome required analysis handling before the
  remaining arm; the split roots and provenance qualification are explicit.
- Matrix inspection 001 was interrupted during a spectral estimate; 002
  retained nonconvergent estimates; 003 is the completed inspection. None
  invoked an optimizer. The matrix report covers all three.
- Native trace 001 failed adapter preflight before any optimizer invocation;
  002 is the corrected, exact-reproduction run.
- Native LU's initial wrapper preflight failed solver enumeration before
  creating output or launching a worker. It was corrected and regression-tested;
  `native_lu_probe_001` contains only the two declared numerical arms.
- Joint fixed-bound analysis was corrected after execution without rewriting
  raw evidence. Its report distinguishes original and verified analysis.
- Optimality audit 001/002 are development analysis snapshots, not additional
  optimization trials; 003 is the final reconstruction.
- `joint_surplus_trajectory_overlay.json` and `_v2.json` bind the plotted
  aggregate SoC, battery power, and renewable trajectories to retained joint
  scaling arms. They are visualization supplements to the joint follow-up,
  not new solves or additional optimality certificates.

## Current scientific position

Pending before production adoption: [solver-independent acceptance decision](ACCEPTANCE_DECISION.md).
Separate practical primal feasibility/accounting, optimality evidence, native
termination and AC realizability. No acceptance policy is changed by this note.

Three different notions of scaling have now been tested and must remain distinct:
device-cone normalization, fixed input-data scaling, and the changing native
barrier KKT scaling. Improving the condition estimate of one matrix does not
ensure an accurate original-unit optimum or a reliable Newton step.

Physical residual acceptance, native solver success, and original objective
reconstruction are separate checks. A test is not accepted by selecting only
the favorable one. Native KKT scaling failed native success and objective
reconstruction despite passing physical residual checks. Native LU passed
physical and objective reconstruction checks but still failed native success;
neither is promoted.

At the original 1e-10 target, no complete surplus remedy or general three-solver
qualification has been established. Factorization, dynamic regularization, and refinement remain
relevant. The latest replay localizes the cone-scaling stop to a zero computed
determinant despite a positive exact-input margin; this motivates a focused
compensated-arithmetic test, not automatic acceptance. That narrow test now
corrects scaling but exposes the same arithmetic problem in other determinant
callers. Consistent shared SOC arithmetic now improves the gap by another
2.26×, but exposes an exact-arithmetic interior update that rounds outside
the cone. A rounded-update interiority guard is a next test candidate, not
a demonstrated remedy. A separately declared practical test now accepts both
surplus and deficit with SuperLU and relative gap 1e-9, preserving feasibility
and physical audits. Compensated arithmetic is not needed for those results.
This qualifies those two experimental solves, not a production backend or
MOSEK/COPT. Earlier rejected records are unchanged. This index authorizes no
further experiments.
