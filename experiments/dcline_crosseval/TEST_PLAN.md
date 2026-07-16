# case9_dcline cross-eval: consolidated test plan (finished + TODO)

**Last updated:** 2026-07-14. This file supersedes the EX-plan framing in
HANDOFF.md (B.2/B.5/B.6/B.7), which is now stale. LOG.md is the running record;
this file is the plan + status. Reports: EX6_REPORT.md, EX6B_CONTROL_REPORT.md,
CONSTRAINT_ENUMERATION.md.

## The question

cvxopf AC-OPF on case9_dcline gives obj ~5490 (C*); the neutralized Pypower
analog gives 6249.87 (P*); the committed fixture gives 6446. Why does cvxopf
find a cheaper point? Same problem in different local optima, or genuinely
different problems (objective and/or constraint set)?

## Two points under study (durable in results/)

- **C\*** = cvxopf optimum. obj 5490.10, Pg [90, 10, 220.16], p_in [1,2,10],
  p_out [-0.99,-2,-9.5]. Full dispatch incl Qg/p_net/q_net in
  `results/cstar_full.json` (use this, not the older cstar.json which lacks Qg).
- **P\*** = neutralized Pypower optimum. obj 6249.8659, real Pg
  [90, 106.14, 123.48], dummy Pg [-1,-10,-10,-0.01,10,9.5] -> PF [1,10,10],
  PT [0.01,10,9.5], Q=0. Full Vm/Va in `results/ex1_pstar.txt`.

**Neutralized Pypower** = the Pypower dcline problem stripped to match cvxopf:
branch limits off (rateA 1e5), dummy-gen reactive pinned 0 (unity PF), terminals
PV->PQ. Built by `_dcline_to_gens` + `_make_coupling_userfcn` in
`scripts/generate_pypower_fixtures.py`, dclinecost deleted (zero-cost lines).

## Method

Compare the two optima at FIXED points (no re-solving, so no basin ambiguity)
until the final warm-start step. Separate "different model" from "different
basin":
- EX4/EX5: does the shared objective agree at each point?
- EX6/EX7: is each point feasible in the OTHER's constraint set?
- EX8: verdict from the truth table.
- EX9: warm-start basin test (the one step that DOES re-solve).

---

## Steps and status

### EX1 - record P* (DONE)
Neutralized Pypower solve. obj 6249.8659, user-terminal verified.
File: results/ex1_pstar.txt. Script: _ex_crosseval.py.

### EX2 - record C* (DONE)
cvxopf solve. obj 5490.10. File: results/cstar.json (+ cstar_full.json from
EX6c with Qg/p_net/q_net). Script: _ex2_cstar.py, _ex6c_cstar_full.py.

### EX3 - point mapping (DONE)
Round-trip verified both directions. cvxopf Pg[k] <-> Pypower real gen[k];
buses internal->ext {0:1,1:2,2:30,3:4,4:5,5:6,6:7,7:8,8:9};
p_in[k] = PF[k] = -(from-dummy Pg[k]); p_out[k] = -PT[k] = -(to-dummy Pg[k]).
DC terminals enter nodal balance as +p_in/+p_out (Convention B). TRUST cvxopf's
own p_net/q_net for the DC sign; do not re-derive (this bit us 3x).
File: results/ex3_roundtrip.txt.

### EX4/EX5 - objectives agree (DONE)
Both solvers' objectives match a direct curve eval of the shared cost at their
own point (rel diff <1e-5). The objective is NOT the cause.
CAVEAT (provenance): results/ex45_objective.txt evaluated EX5 at the WRONG P*
(the partial-neutralization 6213 point, not 6249.87). The agreement conclusion
holds (property of the cost representation); the stale "cheaper by 723" number
does not -- correct gap is 5490 vs 6249.87 = ~760.

### EX6 - is C* feasible in neutralized Pypower? (DONE)
**Two dead ends then a clean result:**
- EX6 first attempt: hand-built nodal reconstruction, WRONG DC sign, invalid.
  Do not trust results/ex6_Cstar_in_pypower.txt.
- EX6-(B): feed C* through Pypower runpf, measure voltage drift. DISCARDED --
  the control (native P* through the same harness) drifted MORE than C*
  (0.041 vs 0.011 pu), proving the harness manufactures drift. runpf/runopf
  freeze different variable sets. See EX6B_CONTROL_REPORT.md. Do not resurrect.
- **EX6-proper (the valid one):** constraint-by-constraint residual of C*
  against neutralized Pypower, no solver, no PV/slack freedom. Licensed by the
  Ybus-agreement check (cvxopf vs Pypower Ybus max abs diff 4.4e-16, identical).
  Guardrail PASS (recon vs p_net/q_net, 1e-16). Result: C1 real balance
  1.7e-13, C2 reactive 6.2e-13, C3-C6 all satisfied, C7 DC coupling law
  residual [-1.0, 0, 0] MW -- link0 off by exactly loss0=1 MW.
  => C* is feasible in neutralized Pypower EXCEPT the 1 MW loss0 term on link0.
  Script: _ex6_proper_constraint_residual.py. Output: results/ex6_residual.txt.
  Report: EX6_REPORT.md.

### EX7 - is P* feasible in cvxopf's constraint set? (IN PROGRESS)
Symmetric to EX6. Plug P*'s dispatch/voltages into cvxopf's constraint
expressions and measure residuals (mirror the EX6-proper constraint-by-constraint
style: nodal P/Q balance, gen P/Q bounds, voltage bounds, DC box, DC loss law).
Expectation: P* is feasible-but-suboptimal in cvxopf (cvxopf could sit at P* but
found a cheaper basin). Note cvxopf's loss law has NO loss0, so P*'s link0
(p_out=+0.01 at p_in=1) will NOT satisfy cvxopf's
p_out=-(1-loss1)*p_in=-0.99 -- expect a symmetric ~1 MW loss0 residual on link0
from the other direction. Everything else expected feasible.
Split into EX7a (regenerate P*) + EX7b (residual check).

**EX7a - regenerate P* as structured artifact (DONE).** ex1_pstar.txt is prose
only; EX7 needs P*'s full solved state as JSON (parallel to cstar_full.json) so
EX7b has an independent guardrail witness. _ex7a_pstar_full.py re-solves the SAME
neutralized model (reusing _ex_crosseval.solve_neutralized), gates on
obj==6249.8659, and writes results/pstar_full.json: real Pg/Qg, from/to dummy Pg,
dummy Qg (all 0, unity PF confirmed), Vm/Va, gen_bus (for split verification), and
Pypower's OWN Ybus (real/imag) + i2e map. gen_bus=[1,2,30,30,7,5,4,9,9] confirms
the positional dummy split (from-buses [30,7,5], to-buses [4,9,9]).
P* decodes to: p_in=[1,10,10], p_out=[+0.01,-10,-9.5]. NOTE: this corrects the
stale prose "PT=[0.01,10,9.5]" -- link0 to_dummy_Pg is -0.01, so p_out[0]=+0.01.

**EX7b - constraint-by-constraint residual (TODO).** Single environment, no
pypower needed (Ybus agrees; also double-checked against the recorded Pypower
Ybus in pstar_full.json). Mirror EX6-proper: guardrail (gen-side recon vs
Ybus-side V*conj(YV)) first, then C1-C7. Write results/ex7_residual.txt +
EX7_REPORT.md.

### EX8 - verdict (TODO, after EX7)
Truth table:
- Objectives disagree -> cost bug. (RULED OUT: EX4/EX5 agree.)
- Objectives agree, a point infeasible in the other's set -> genuine
  constraint-set difference; residual location names the constraint.
- Objectives agree AND both points mutually feasible, yet different optima
  -> genuine local optima -> EX9.
Current standing after EX6: C* is feasible in Pypower except a 1 MW loss0 term
(too small to explain the 760 gap or the link1 full-range swing). Strong
evidence for LOCAL OPTIMA. EX7 confirms from the P* side; then finalize EX8.

### EX9 - warm-start basin test (TODO, conditional on EX8 = local optima)
The decisive re-solve. Warm-start Pypower's OPF at C*'s FULL operating point --
generator levels AND generator Q AND bus voltages (Vm/Va) AND the DC dummy-gen
Pg -- then let it re-optimize (do NOT pin the lines; they optimize freely).
- Converges near 5490 (far below 6249) -> C* is a valid Pypower basin cvxopf
  found and Pypower's default start missed -> LOCAL OPTIMA CONFIRMED, cvxopf
  found the better one.
- Snaps back to 6249 -> C* is not a Pypower basin -> reopen model-difference.
CAVEAT: C* is NOT exactly Pypower-feasible (off by 1 MW loss0 on link0, EX6 C7),
so Pypower cannot land on C* to the decimal -- expect ~5490 plus a small
loss0-sized correction. Near 5490 and far from 6249 is the success signal.
WHY seed the full point, not gens only: a gen-only warm start lets the solver
re-pick Q/V/DC freely and may roll back to P*, making a negative result
ambiguous. Seeding the full operating point makes "stays vs slides" meaningful.
The symmetric test (warm-start cvxopf at P*) is also informative.

---

## Discipline (carried from LOG.md)
- Work in experiments/; write results to results/ and read back from disk.
- Unique filename per run; read back; do not cp from reused /tmp names.
- Trust cvxopf's own p_net/q_net for DC signs; guardrail any reconstruction
  against them BEFORE interpreting downstream residuals.
- When a test yields a number whose meaning is unclear, run it on a
  known-answer control before interpreting (EX6-(B) lesson).

## Related
- Memory: memories/case9-dcline-optima-gap.md
- Milestone plan: plans/milestone-7-hvdc.md (Ybus-agreement test added to T7).
- Ybus check: _ybus_dump_cvxopf.py, _ybus_compare.py, results/ybus_compare.txt.
