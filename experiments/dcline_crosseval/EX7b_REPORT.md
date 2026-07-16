# EX7b: is P* feasible in cvxopf's constraint set?

**Date:** 2026-07-15
**Status:** COMPLETE. P* is feasible in cvxopf's constraint set EXCEPT for a
single 1.0 MW loss0 term on link0 -- the exact symmetric mirror of EX6. Combined
with EX6, this confirms from BOTH sides that the cvxopf/Pypower divergence is
DIFFERENT LOCAL OPTIMA, not a constraint-set difference.

## Method

Constraint-by-constraint residual check of P* (the neutralized-Pypower optimum,
results/pstar_full.json, obj-gated to 6249.8659) against cvxopf's constraint
set. Single environment (main cvxopf env), no solver, no live pypower. Direct
mirror of _ex6_proper_constraint_residual.py.

Licensed by the Ybus-agreement result, and additionally re-verified
self-contained here: cvxopf's Ybus vs P*'s OWN recorded Pypower Ybus
(reindexed pp-internal -> cvxopf-internal) max abs diff 4.4e-16 -- AGREE.

Two decode steps unique to EX7b (both verified, not trusted):

1. **Dummy-gen -> p_in/p_out.** P* stores DC terminals as dummy generators. In
   Pypower's dcline userfcn the from-gen injects Pgf=-p_in and the to-gen
   injects Pgt=-p_out, so p_in=-from_dummy_Pg, p_out=-to_dummy_Pg. The from/to
   split is VERIFIED against gen_bus (from-buses [30,7,5] == dcline from;
   to-buses [4,9,9] == dcline to) before use.
2. **Bus reindex.** P*'s rows are Pypower external order [1,2,30,4,5,6,7,8,9];
   mapped into cvxopf internal order via ext_to_int. Turns out to be the
   identity permutation here, but asserted rather than assumed.

**Guardrail sign fix (LOG).** First run manufactured a spurious ~2x residual
(up to 39 MW) at the terminal buses. Cause: applied cvxopf's Convention-B grid
injection (+p_in, +p_out) to P*'s nodal balance. But in P* the terminals are
DUMMY GENERATORS injecting the RAW dummy Pg (= -p_in at from-bus, -p_out at
to-bus) -- the opposite sign. Using the raw dummy Pg, the guardrail closes to
3.1e-9 MW. The bug was in the check, not in P*; caught by the guardrail exactly
as designed.

Script: _ex7b_pstar_in_cvxopf.py. Output: results/ex7b_residual.txt.
Input: results/pstar_full.json (P* full dispatch incl dummy-gen Pg, Ybus, i2e).

## Results (each constraint, labeled residual)

| check | residual | verdict |
|---|---|---|
| YBUS double-check (cvxopf vs P*'s Pypower Ybus) | 4.4e-16 | AGREE |
| Guardrail (gen-side vs Ybus-side) | 3.1e-9 MW / 2.6e-9 MVAr | PASS |
| C1 nodal real balance | 3.1e-9 MW (all 9 buses ~0) | satisfied |
| C2 nodal reactive balance | 2.6e-9 MVAr | satisfied |
| C3 gen P bounds | all within (gen0 at Pmin=90) | satisfied |
| C4 gen Q bounds | all within | satisfied |
| C5 voltage bounds | within (one bus at Vmax=1.1) | satisfied |
| C6 DC box Pmin<=p_in<=Pmax | at bounds (link0 Pmin=1, links1,2 Pmax=10) | satisfied |
| C7 DC coupling law (cvxopf, loss0 DROPPED) | [+1.0, 0.0, 0.0] MW | link0 VIOLATES by loss0 |

(Bound residuals use viol = bound - value / value - bound; negative = satisfied.)

C7 detail: cvxopf's law (loss0 dropped) is p_out = -(1-L1)*p_in [MW]. Link0
(30->4): L1=0.01, P* p_in=1.0, p_out=+0.01. cvxopf requires p_out = -0.99;
P* delivers +0.01; residual exactly +1.0 MW = loss0. Sign is OPPOSITE to EX6's
-1.0 (EX6 measured C* against Pypower's WITH-loss0 law; EX7b measures P* against
cvxopf's DROPPED-loss0 law). Links 1,2: zero.

## Interpretation

P* satisfies EVERY constraint of cvxopf's problem to machine precision EXCEPT
the loss0 term on link0 -- the same single 1.0 MW deliberate model difference
identified in EX6, seen from the other side. cvxopf could sit at P* (feasible)
but found a cheaper basin at C* (obj 5490 vs P*'s 6249.87).

EX6 showed C* feasible in Pypower except loss0-on-link0. EX7b shows P* feasible
in cvxopf except loss0-on-link0. The two optima are therefore MUTUALLY
NEAR-FEASIBLE across a feasible set that differs only by one small, known,
deliberate term. => DIFFERENT LOCAL OPTIMA of the (near-)same nonconvex AC-OPF,
confirmed from both sides. This is the both-sides evidence EX6's caveat called
for.

## Caveats

- Same loss0 caveat as EX6: P* is not 100% feasible in cvxopf; it is off by
  exactly one small, known, deliberate term ("feasible except loss0").
- This is a feasibility/optimality-gap argument, not a proof that no continuous
  path connects the two basins. The nonconvexity of AC-OPF (DNLP via IPOPT)
  makes distinct basins legitimate and expected; EX9 (warm-start cvxopf at P*)
  would test whether cvxopf's solver, started at P*, stays there or falls to C*.

## Next
- EX8: write the consolidated verdict (both sides now in).
- EX9 (optional): warm-start cvxopf at P* to observe basin behavior directly.
