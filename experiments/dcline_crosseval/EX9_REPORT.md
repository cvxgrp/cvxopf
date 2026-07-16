# EX9: cvxopf-side warm-start — does cvxopf hold P*?

**Date:** 2026-07-15
**Status:** COMPLETE. cvxopf HOLDS C* (its own optimum) and DESCENDS from P* to
C*. Within cvxopf's solver, P* is not a stable optimum. This is the first real
warm-start result (EX8 on the Pypower side was void) and it motivates the
canonicalization-tractability thesis (see below + the durable memory).

## Method

CVXPY passes `variable.value` straight to IPOPT as the initial point x0, so
unlike Pypower's runopf (EX8, which discards the case-table seed — proven by
identical 24-iter cold/warm histories, `_ex8_probe_iters.py`), cvxopf CAN be
warm-started. Build the case9_dcline AC problem once, set every variable.value
to a fully self-consistent seed, solve with verbose=True, read the IPOPT iterate
log + solved dispatch.

Full x0 (seed everything reconstructable): theta=Va[rad], v=Vm, Pg/Qg in per-unit
(MW/baseMVA), p_hvdc_in/out in MW; P_vec/Q_vec reconstructed in closed form from
V via the exact Section-2 trig, then p=Rp@P_vec, q=Rp@Q_vec. Verified complete:
the pre-solve probe (`_ex9_probe_seedfeas.py`) shows all 10 user variables set
and ALL 62 b.prob constraints satisfied to <=7.1e-15 at the C* seed.

Two arms:
- C-arm CONTROL: seed C* (cvxopf's own optimum) -> expect stay.
- P-arm INFORMATIVE: seed P* (neutralized-Pypower optimum) -> stay or fall?

Script: `_ex9_warmstart_cvxopf.py`. Output: results/ex9_warmstart_cvxopf.txt.

## Results

| arm | seed | solved obj | solved p_in | user-var stacked drift | verdict |
|---|---|---|---|---|---|
| C-arm (control) | C* (5490) | 5490.1038 | [1,2,10] | 1.28e-10 | stays at C* |
| P-arm (informative) | P* (6250) | 5490.1038 | [1,2,10] | 1.385e+02 | falls P* -> C* |

Both settle to C* in objective AND variables (c*->c*, p*->c*; C*->C*, P*->C*).

IPOPT iterate trajectories (the decisive signal):
- C-arm: iter-0 obj 5514, 21 iters, -> 5490.10.
- P-arm: iter-0 obj **6250** (= P*, so the seed reached IPOPT), then walks
  monotonically DOWN 6250 -> 5606 -> 5565 -> 5529 -> ... -> 5490, 23 iters.

The iter-0 objectives DIFFER between arms (5514 vs 6250) — direct proof the seed
reaches IPOPT at the user-variable level, unlike the void EX8.

## The iter-0 inf_pr=25 red herring (why the control took 21 iters)

A true warm-start control should exit in ~1 iter; c*->c* took 21, and both arms
showed IDENTICAL iter-0 inf_pr=2.50e1. The pre-solve probe explains it: the C*
seed is FULLY FEASIBLE in cvxopf's user-level constraints (worst violation
7.1e-15). The 25 is not our seed — it is CVXPY's CANONICALIZATION layer. b.prob
has 62 user constraints; IPOPT solves 94 eq + 17 ineq over 107 vars after
canonicalization introduces auxiliary variables. CVXPY seeds the USER variables
from our .value but NOT the canonical auxiliaries, so the canonical system is
infeasible at iter 0 regardless of seed (hence identical inf_pr across arms).
The 21 iters are IPOPT reconciling hidden auxiliaries to our (already-feasible)
user point — the user variables barely move (drift 1.3e-10). Trust the drift,
not the iteration count.

## Interpretation — the canonicalization-tractability thesis

(Full framing: memory `dnlp-canonicalization-tractability-thesis`. Per the user
2026-07-15, this is the basis of a paper.)

CVXPY DNLP does DCP-style structural analysis + automatic transformation of the
nonlinear problem before the solver sees it (e.g. rewriting sum(abs(x)) to its
smooth epigraph form, avoiding the nondifferentiable kink that trips IPOPT).
For a NONCONVEX NLP this does not buy DCP's global-optimality guarantee, but it
buys a TRACTABILITY PRIOR: a better-conditioned landscape where the local
solver is MORE LIKELY to reach a good basin.

Through this lens the case9_dcline gap is not a neutral "two symmetric local
optima." cvxopf reaches the CHEAPER C* (5490) and, warm-started at Pypower's
costlier P* (6250), DESCENDS to C* and will not hold P*. That is consistent
with DNLP presenting IPOPT a more tractable landscape than Pypower's hand-rolled
dcline userfcn + raw NLP — i.e. the gap may be a FORMULATION-TRACTABILITY effect,
not seed luck or an intrinsic property of P*.

## Caveats

- SUPPORTS, does not PROVE the thesis. Confounders still open: seed luck (cold
  vs warm basin-of-attraction), solver internals (IPOPT vs pips), the 1 MW loss0
  model diff between the two problems.
- One-directional: EX9 shows cvxopf won't hold P*. The symmetric Pypower-side
  question (does Pypower HOLD C*?) is not yet answered because EX8 is void.

## Next — the decisive experiment

Real Pypower warm-start (find the pips/opf x0 hook, not the runopf case-table
pattern): warm-start Pypower at C*.
- Pypower STAYS at C* -> both solvers agree C* is a valid optimum; only the
  cold-start basin differs -> points at formulation/canonicalization (thesis
  supported).
- Pypower LEAVES C* -> C* may be a cvxopf-formulation artifact; thesis
  complicated.
