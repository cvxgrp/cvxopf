# Side-by-side constraint enumeration: cvxopf AC vs neutralized Pypower

Purpose: before running mutual-feasibility checks (EX6/EX7), write down the two
constraint sets *from the actual code* so any residual is interpretable. The
claim under test is \"these are the same OPF problem\"; do not assume it.

Sources read (2026-07-14):
- cvxopf: `src/cvxopf/hvdc.py`, `src/cvxopf/ac_problem.py`
- pypower: `scripts/generate_pypower_fixtures.py` (`_dcline_to_gens`,
  `_make_coupling_userfcn`), `src/cvxopf/testcases/case9_dcline.py` (dcline table)
- C* build recipe: `experiments/dcline_crosseval/_ex2_cstar.py`

## The case9_dcline `dcline` table (in-service rows 0,1,3; row 2 is off)

| link | fbus->tbus | Pmin | Pmax | loss0 | loss1 | dclinecost c1 |
|------|-----------|------|------|-------|-------|---------------|
| 0    | 30->4     | 1    | 10   | **1** | 0.01  | 0             |
| 1    | 7->9      | 2    | 10   | 0     | 0     | 0             |
| 3    | 5->9      | 0    | 10   | 0     | 0.05  | **7.3**       |

## cvxopf AC constraint set (case9_dcline, C*)

Built via `build_opf(case, formulation=\"ac\", hvdc=hvdc_from_dcline(case[\"dcline\"]))`.
NOTE: `hvdc_from_dcline` called WITHOUT dclinecost -> all links cost_coeffs=(0,0,0).
Default OPFOptions: enforce_vset=False, enforce_branch_limits=False (stub),
sparse_pq=True.

Per-bus, per-generator, per-link constraints:
- theta[ref] == 0                                  (ref angle fix)
- v in [vmin, vmax]  (bounds on the variable)      (voltage magnitude box)
- P_vec/Q_vec == trig(theta,v) on Ybus pattern     (AC power-flow defs)
- p == Cg@Pg - Pd + hvdc_inj_p                     (real nodal balance)
- q == Cg@Qg - Qd                                  (reactive nodal balance; HVDC absent -> unity PF)
- Pg in [Pgmin, Pgmax], Qg in [Qgmin, Qgmax]       (gen bounds)
- HVDC box:  Pmin <= p_in <= Pmax                  (per link)
- HVDC loss: p_out == coeff * p_in                 (per link, affine branch)
     link0 (Pmin>=0): coeff = -(1 - 0.01) = -0.99   ->  NO loss0 term
     link1 (Pmin>=0): coeff = -(1 - 0)    = -1.00
     link3 (Pmin>=0): coeff = -(1 - 0.05) = -0.95
- HVDC injection into balance: (1/baseMVA)*(Ch_from@p_in + Ch_to@p_out), BOTH '+'
- NO branch flow limits (M4 stub)
- NO terminal reactive (Q at terminals identically 0)
- NO terminal voltage setpoint pin (enforce_vset=False)
Objective: poly_cost(gencost, baseMVA*Pg) + 0 (HVDC zero-cost)

## Neutralized Pypower constraint set (P*)

Built via `_dcline_to_gens(orig)` + `_make_coupling_userfcn(orig)`, dclinecost
deleted, then neutralized: branch rateA->1e5, dummy Qmin=Qmax=0, terminals PV->PQ.
Each DC line -> two dummy gens (from-gen fg, to-gen tg).

- Standard AC-OPF power balance / flow eqns for the 9-bus network.
- real gens: Pg in [Pgmin,Pgmax], Qg in [Qgmin,Qgmax]  (same as cvxopf)
- from-gen box: fg[PG] in [-Pmax, -Pmin]  =>  p_in = -fg[PG] in [Pmin, Pmax]   MATCHES cvxopf box
- coupling law: (1 - L1)*Pgf + Pgt == -L0/baseMVA
     with Pgf = -p_in, Pgt = -p_out (EX3 mapping):
       (1-L1)*(-p_in) + (-p_out) = -L0/baseMVA
       => p_out = -(1-L1)*p_in + L0/baseMVA           <-- CARRIES loss0 term
     link0: p_out = -0.99*p_in + 1/100 = -0.99*p_in + 0.01
     link1: p_out = -p_in
     link3: p_out = -0.95*p_in
- dummy Q pinned 0 (neutralization)  -> unity PF, MATCHES cvxopf
- terminals PQ (neutralization)      -> no voltage pin, MATCHES cvxopf enforce_vset=False
- branch limits off (rateA=1e5)      -> MATCHES cvxopf no-branch-limit

## Differences that remain AFTER neutralization

### Difference #1 (REAL, still present): loss0 on link0
cvxopf loss law:   p_out = -(1-loss1)*p_in            (drops loss0)
pypower loss law:  p_out = -(1-L1)*p_in + L0/baseMVA  (keeps loss0)

For link0 both sides sit at p_in=1 (box min). Delivered power at bus 4 (to-bus):
- cvxopf:  p_out = -0.99      -> injects 0.99 MW at bus 4  (C* p_hvdc_out[0]=-0.99 CONFIRMED)
- pypower: p_out = -0.99 + 0.01 = -0.98 ... BUT check sign/units.
  In terminal terms Pt = Pf - (loss0 + loss1*Pf) = 1 - (1 + 0.01) = -0.01.
  So pypower delivers ~ -0.01 (essentially nothing) at bus 4.
  (P* PT[0] = 0.01 per LOG -- magnitude matches; sign bookkeeping to nail down.)
=> ~1 MW difference in injection at bus 4 between the two models. This is the
   documented loss0 drop (CLAUDE.md Milestone 7 table). It is a genuine
   constraint-set difference, NOT yet quantified as the cause of the 760 gap.

### Difference #2 (NOT a difference for C* vs P*): dclinecost c1=7.3 on link3
Full case9_dcline has dclinecost c1=7.3 on link3. BUT:
- pypower P*: dclinecost deleted (zero-cost DC lines).
- cvxopf  C*: hvdc_from_dcline called WITHOUT dclinecost -> cost_coeffs=(0,0,0).
Both sides dropped it. So objectives can still agree (consistent with EX4/EX5).
CONSEQUENCE: C* is NOT the model you'd get from the full case9_dcline; it is a
deliberately cost-stripped variant chosen to match P*. Worth stating explicitly.

## PROVENANCE FLAG on EX4/EX5 (results/ex45_objective.txt)
That file evaluates EX5 against P_Pg = [90, 101.97, 127.73], obj 6213 -- the
PARTIAL-neutralization point, NOT the true fully-neutralized P* (Pg =
[90, 106.14, 123.48], obj 6249.87, per LOG CORRECTION 2026-07-14).
- The objective-AGREEMENT conclusion (cost functions match at a shared point) is
  a property of the cost representation and is probably still valid.
- The specific numbers \"P* costs 6213, cheaper by 723\" are STALE. The correct
  comparison is C* 5490 vs P* 6249.87 = gap ~760.
- ACTION: recompute EX5 against the true P* before quoting the gap. Do not
  delete ex45_objective.txt; annotate it.

## Where this leaves EX6/EX7
The enumeration shows the two models are IDENTICAL except for the loss0 term on
link0 (Difference #1). If that ~1 MW injection difference is the whole story,
then:
- EX6 (C* in pypower's set): C* has p_out[0]=-0.99, but pypower requires
  p_out[0] = -0.99*p_in + 0.01 = -0.98. So C* should VIOLATE pypower's link0
  coupling by ~0.01 pu (~1 MW) -- a clean, predicted residual at exactly one
  constraint. If EX6 shows that and only that, Difference #1 is confirmed as
  the (a) model difference.
- But a ~1 MW loss0 term causing a 760-unit objective gap is implausible on its
  face (1 MW * marginal cost ~ tens, not hundreds). So EITHER loss0 is not the
  dominant cause and something else differs, OR the two optima are different
  basins of the (nearly) same problem. EX6/EX7 residuals decide.

## OPEN QUESTION raised by enumeration
If the ONLY constraint-set difference is a ~1 MW loss0 term, it cannot by itself
explain a 760 objective gap. That points AWAY from \"constraint-set difference\"
and toward \"different local optima\" -- the surprising branch. Must run EX6/EX7
to confirm, but flag now that the leading interpretation (B.6: constraint-set
difference dominated by branch limits) is looking WEAKER after enumeration:
branch limits are neutralized away, and the remaining difference (loss0) is tiny.