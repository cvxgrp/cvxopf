# case9_dcline: cvxopf vs Pypower cross-evaluation

**Question.** cvxopf AC-OPF on case9_dcline gives a lower objective than the
Pypower oracle. Same problem in different local optima, or genuinely different
problems (objective and/or constraint set)?

**Method.** Four-way cross-evaluation at the two fixed optima (no re-solving):
- EX4/EX5: evaluate each optimum under the *shared* objective -> do the cost
  functions agree?
- EX6/EX7: is each optimum feasible in the *other's* constraint set?

**Neutralized Pypower model** (to make the two models as identical as possible;
each neutralization independently verified earlier):
- branch limits off (rateA raised out of range)
- dummy-gen reactive pinned to zero (Qmin=Qmax=0) -> unity PF like cvxopf
- terminal buses reverted PV -> PQ (no-op in OPF; verified vs case57)

## Points under study
- **C\*** (cvxopf optimum). Values in results/cstar.json,
  results/cstar_full.json (full dispatch incl Qg/p_net/q_net).
- **P\*** (fully-neutralized Pypower optimum). Values in results/ex1_pstar.txt
  (canonical, promoted from results/ex1_final.txt).

## Verified mapping (EX3, round-trip passed both directions)
- gens: cvxopf Pg[k] <-> Pypower real gen[k], buses [1,2,30], direct.
- buses: internal->ext {0:1,1:2,2:30,3:4,4:5,5:6,6:7,7:8,8:9}; Vm/Va direct.
- DC line <-> dummy gens:
    cvxopf p_in[k]  =  PF[k]  = -(from-dummy Pg[k])
    cvxopf p_out[k] = -PT[k]  = -(to-dummy Pg[k])
  (Corrected a sign bug in _probe_feas.py, which had used
   p_in = from-dummy Pg = -PF.)
  Details in results/ex3_roundtrip.txt.

## Task log
- EX1 DONE: P* recorded. results/ex1_pstar.txt
- EX2 DONE: C* recorded. results/cstar.json
- EX3 DONE: mapping round-trip verified both dirs. results/ex3_roundtrip.txt
- EX4/EX5 DONE: objectives AGREE. results/ex45_objective.txt
    Use a relative threshold, not absolute (abs threshold gave a false
    DISAGREE; small residual is PWL-cost-variable vs np.interp rounding).
- EX6 DONE: see result below.
- EX7 pending.

Objective ladder (values in the results files):
  committed fixture   (branches ON,  Q free)
  partial neutralize  (branches off, Q free)
  FULL neutralize     (branches off, Q=0, PQ terminals)  <- P*
  cvxopf C*
Note: zeroing reactive raises the partial-neutralize objective toward the
full-neutralize objective (removing a resource; correct direction).
Note: P* runs link0 (30->4) at minimum PF -- same as cvxopf C*.

## Established
- SAME objective (EX4/EX5 agree). Not a cost-representation issue.
- Under the shared objective, C* is cheaper than P*.
- The divergence is NOT the objective. Either (a) C* is infeasible in
  Pypower's neutralized constraint set, or (b) different local optima.

## EX6 RESULT: C* feasible in neutralized Pypower EXCEPT loss0
Method: direct constraint-by-constraint residual (EX6_REPORT.md).
Licensed by Ybus agreement (results/ybus_compare.txt: cvxopf vs Pypower Ybus
agree to floating-point -> DC lines contribute nothing to Ybus; network side
guaranteed).

Result in results/ex6_residual.txt (guardrail PASS):
  C1 real balance                   satisfied
  C2 reactive balance               satisfied
  C3 gen P bounds                   satisfied
  C4 gen Q bounds                   satisfied
  C5 voltage bounds                 satisfied
  C6 DC box                         satisfied (p_in at box bounds)
  C7 DC coupling law                link0 violates by exactly loss0

VERDICT: C* is feasible in neutralized Pypower EXCEPT the loss0 term on link0
(dropped by design). Too small to explain the objective gap or the link1 swing
(C* vs P* differ at link1). => the gap is DIFFERENT LOCAL OPTIMA of the
(near-)same problem, NOT a constraint-set diff. Prior branch-limit interp (B.6)
rejected: branch limits neutralized away; loss0 residual far too small.

Artifacts: results/cstar_full.json, results/ybus_cvxopf.json,
results/ybus_compare.txt, results/ex6_residual.txt; scripts
_ex6c_cstar_full.py, _ybus_dump_cvxopf.py, _ybus_compare.py,
_ex6_proper_constraint_residual.py.

TODO (milestone plan): Ybus-agreement test as T7 deliverable in
plans/milestone-7-hvdc.md (commit Pypower Ybus as static fixture; no live
pypower in tests/).

NEXT: EX7 (is P* feasible in cvxopf?) -> EX8 verdict -> EX9 (warm-start basin
test) if EX8 confirms local optima.