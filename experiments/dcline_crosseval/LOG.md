# case9_dcline: cvxopf vs Pypower cross-evaluation

**Question.** cvxopf AC-OPF on case9_dcline gives obj ~5490; the Pypower oracle
gives ~6446. Why? Same problem in different local optima, or genuinely
different problems (objective and/or constraint set)?

**Method.** Four-way cross-evaluation at the two fixed optima (no re-solving,
so no basin ambiguity):
- EX4/EX5: evaluate each optimum under the *shared* objective -> do the cost
  functions agree?
- EX6/EX7: is each optimum feasible in the *other's* constraint set?

**Neutralized Pypower model** (to make the two models as identical as possible;
each neutralization independently verified safe/equivalent earlier):
- branch limits off (rateA -> 1e5)
- dummy-gen reactive pinned to 0 (Qmin=Qmax=0) -> unity PF like cvxopf
- terminal buses reverted PV -> PQ (no-op in OPF; verified vs case57)

## Points under study
- **C\*** (cvxopf optimum): obj 5490.10, Pg [90, 10, 220.16],
  p_in [1,2,10], p_out [-0.99,-2,-9.5].  File: results/cstar.json
- **P\*** (neutralized Pypower optimum): obj 6213.39, Pg [90, 101.97, 127.73],
  dummy Pg [-10,-2.28,-10, 8.9,2.28,9.5], dummy Q [0]*6.

  NOTE: neutralized Pypower (6213) is NOT the committed fixture (6446); the
  fixture has branches on + reactive free. 6213 is the branches-off + Q=0
  variant, the closest match to cvxopf's model.

## Verified mapping (EX3, round-trip passed both directions)
- gens: cvxopf Pg[k] <-> Pypower real gen[k], buses [1,2,30], direct.
- buses: internal->ext {0:1,1:2,2:30,3:4,4:5,5:6,6:7,7:8,8:9}; Vm/Va direct.
- DC line <-> dummy gens:
    cvxopf p_in[k]  =  PF[k]  = -(from-dummy Pg[k])
    cvxopf p_out[k] = -PT[k]  = -(to-dummy Pg[k])
  (This corrected a sign bug in _probe_feas.py, which had used
   p_in = from-dummy Pg = -PF.)

## Results log
- EX1 DONE: P* recorded (obj 6213.39, Vm/Va too). results/ex1_pstar.txt
- EX2 DONE: C* recorded. results/cstar.json
- EX3 DONE: mapping round-trip verified both dirs. results/ex3_roundtrip.txt
- EX4/EX5 DONE: objectives AGREE (rel diff <1e-4, rtol=1e-4).
  results/ex45_objective.txt
    EX4 C* direct 5490.07 vs cvxopf 5490.10 (rel 7e-6)
    EX5 P* direct 6213.36 vs pypower 6213.39 (rel 6e-6)
    The ~0.03 abs gap = PWL-cost-variable vs np.interp rounding, benign.
    (An earlier abs-threshold=1e-2 gave a false DISAGREE; fixed to relative.)
- EX6/EX7: pending.

## Established so far
- SAME objective (EX4/EX5 agree). Not a cost-representation bug.
- Under that shared objective: C* costs 5490.10, P* costs 6213.36 ->
  cvxopf's point is genuinely ~723 CHEAPER.
- Therefore the divergence is NOT the objective. It is either (a) C* is
  infeasible in Pypower's (neutralized) constraint set -> different problem,
  or (b) different local optima. EX6 (is C* feasible for Pypower?) and EX7
  (is P* feasible for cvxopf?) decide this.

## Note on method
Ephemeral stdout proved unreliable (stale/duplicated tool-result blocks).
All results now written to results/*.txt and read back from disk. Scripts
and outputs are the lab notebook; keep them, curate at the end (do not rm).

PROVENANCE BUG caught 2026-07-14: the first ex1_pstar.txt was a copy of the
WRONG /tmp file -- it held the A+B experiment (_probe_neutralize.py: branches
relaxed + reactive zeroed, obj 6249.87, dummy Pg [-1,-10,-10,-0.01,10,9.5]),
NOT the EX1 neutralized-model run. User's terminal `cat` exposed the mismatch.
Fix: regenerated EX1 via _ex_crosseval.py into a UNIQUE filename (defeats stale
echoes + collisions), verified, then promoted to canonical ex1_pstar.txt.
Lesson: unique-name-per-run + read-back; don't cp from reused /tmp names.

CORRECTION 2026-07-14 (verified via user terminal, not my tool reads):
The true EX1 P* is obj 6249.8659, NOT 6213.39. I had mis-attributed 6213
(which is the branches-off-but-reactive-STILL-FREE partial case) as P*. The
fully-neutralized model (_ex_crosseval.py: branches off + Q=0 + PQ terminals)
gives 6249.8659, and this matches the independent A+B probe (also ~6249.87).

CONFIRMED EX1 P* (fully neutralized Pypower), user-terminal verified:
  obj      = 6249.8659
  real Pg  = [90.0, 106.1427, 123.4818]
  dummy Pg = [-1.0, -10.0, -10.0, -0.01, 10.0, 9.5]
  -> PF = [1, 10, 10], PT = [0.01, 10, 9.5]
  dummy Q  = [0]*6
  Vm = [1.1,1.09714,1.08683,1.09495,1.08335,1.1,1.08877,1.09991,1.07499]
  Va(deg) = [0,2.9221,4.3964,-2.4668,-4.1524,0.9545,-1.9143,-0.2293,-4.4668]

Objective ladder (all user-verified or multi-read consistent):
  6446  committed fixture   (branches ON,  Q free)
  6213  partial neutralize  (branches off, Q free)
  6249.87 FULL neutralize   (branches off, Q=0, PQ terminals)  <- P*
  5490  cvxopf C*
Note: zeroing reactive raises 6213 -> 6249 (removing a resource; correct dir).
Note: EX1 P* runs link0 (30->4) at PF=1 (min) -- SAME as cvxopf C* (p_in=1).
canonical file: results/ex1_final.txt (promoted to ex1_pstar.txt).

## EX6 RESULT (2026-07-14): C* feasible in neutralized Pypower EXCEPT loss0
Method changed after two dead ends -- see reports:
- EX6-(B) runpf drift test DISCARDED (EX6B_CONTROL_REPORT.md): the control (feed
  native P* through the same harness) drifted MORE than C* (0.041 vs 0.011 pu),
  proving the harness manufactures drift; it cannot judge feasibility.
- EX6-proper: direct constraint-by-constraint residual (EX6_REPORT.md).

Licensed by Ybus agreement (ybus_compare.txt: cvxopf vs Pypower Ybus max abs
diff 4.4e-16, floating-point identical -> DC lines contribute nothing to Ybus;
network side of feasibility is guaranteed).

EX6-proper result (results/ex6_residual.txt), guardrail PASS (1e-16):
  C1 real balance   1.7e-13 MW      satisfied
  C2 reactive bal   6.2e-13 MVAr    satisfied
  C3 gen P bounds                   satisfied
  C4 gen Q bounds                   satisfied
  C5 voltage bounds                 satisfied
  C6 DC box                         satisfied (p_in at box bounds)
  C7 DC coupling law  [-1.0,0,0] MW link0 violates by exactly loss0=1 MW

VERDICT: C* is feasible in neutralized Pypower EXCEPT the loss0 term on link0
(1 MW, dropped by design). Too small to explain the 760 objective gap or the
link1 full-range swing (C* p_in=2 vs P* p_in=10, link1 lossless). => the gap is
DIFFERENT LOCAL OPTIMA of the (near-)same problem, NOT a constraint-set diff.
Prior leading interp (B.6, branch-limit-dominated constraint diff) REJECTED:
branch limits neutralized away; only residual diff (loss0) far too small.

New artifacts: results/cstar_full.json (C* full dispatch incl Qg/p_net/q_net),
results/ybus_cvxopf.json, results/ybus_compare.txt, results/ex6_residual.txt,
scripts _ex6c_cstar_full.py, _ybus_dump_cvxopf.py, _ybus_compare.py,
_ex6b_control_pstar.py, _ex6_proper_constraint_residual.py.

PROVENANCE CORRECTION: results/ex45_objective.txt evaluated EX5 at the WRONG P*
(P_Pg=[90,101.97,127.73], obj 6213 -- the partial-neutralization point), not the
true fully-neutralized P* (obj 6249.87). The objective-AGREEMENT conclusion
(cost functions match at a shared point) still holds (property of the cost
representation, not the point). The stale number is the gap: correct is C* 5490
vs P* 6249.87 = ~760, not "cheaper by 723". Do not re-quote 6213/723.

TODO carried to milestone plan: Ybus-agreement test added as a T7 deliverable in
plans/milestone-7-hvdc.md (commit Pypower Ybus as a static fixture; no live
pypower in tests/).

NEXT: EX7 (is P* feasible in cvxopf?) -> EX8 verdict -> EX9 (warm-start basin
test) if EX8 confirms local optima.