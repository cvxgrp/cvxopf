---
name: dnlp-canonicalization-tractability-thesis
description: Why cvxopf (CVXPY/DNLP) beats raw-NLP Pypower on some AC-OPF. SHARPENED 2026-07-20 by a 2x2 study on 9-bus (PWL cost x HVDC lines): the driver is NONDIFFERENTIABILITY (PWL kinks, corner-seeking DC routing), NOT nonconvexity. Each nondiff feature alone -> both solvers agree exactly; both together -> raw NLP stuck ~12% worse. DNLP epigraph-lifts the nondiff-convex atoms into a smooth surrogate the interior-point method descends. Pypower's answer verifiably suboptimal, not a neutral alternate optimum.
metadata:
  type: project
---

Working thesis about why a CVXPY/DNLP-based OPF tool tends to find better local
optima than hand-rolled raw-NLP formulations. Originated 2026-07-15 from the
[[case9-dcline-optima-gap]] investigation; SHARPENED 2026-07-20 by a controlled
2x2 study (write-up: `experiments/dcline_crosseval/DNLP_ROUTING_AND_PWL_REPORT.md`).

## The mechanism (sharpened): nondifferentiability, not nonconvexity

Most NLP modeling passes the user's problem DIRECTLY to the solver. CVXPY's DNLP
layer does structural type-checking + automatic transformation first, rewriting
nondifferentiable convex atoms into smooth (linear) epigraph equivalents before
IPOPT ever sees them. A primal-dual interior-point method builds a local model
from gradients/Hessians; at a KINK (PWL breakpoint) or an active-set CORNER
(box-cornered control) those derivatives are ill-defined exactly where the
optimum lives, so the raw solver stalls short. DNLP dissolves the kink by
reformulation (PWL cost -> cp.maximum of affine segments = epigraph: one aux var
+ linear inequalities; source `cost.py::_pwl_cost_expr`), so the solver descends
a smooth surrogate. Same move as L1 -> epigraph (`min sum(t) s.t. -t<=x<=t`).

CRITICAL distinction the 2x2 nails: the AC power-flow NONCONVEXITY (sin/cos) is
common to BOTH solvers and is NOT where they diverge -- they agree perfectly on
smooth variants that have the same AC nonconvexity. The divergence tracks added
CONVEX-BUT-NONDIFFERENTIABLE structure.

## The 2x2 evidence (9-bus; factors = PWL cost, HVDC DC lines)

| | no DC lines | with DC lines |
|--|--|--|
| smooth cost | exact agreement | Pg agrees (<2 MW); ~1% routing gap, cvxopf cheaper |
| PWL cost    | exact agreement | ~12% gap, cvxopf cheaper (obj 5490 vs 6250) |

Neither nondiff feature alone trips the raw solver; TOGETHER they do, and the
gap GROWS with the amount of nondiff structure. Every disagreement favors
cvxopf, and Pypower's answer is VERIFIABLY suboptimal each time (a feasible,
cheaper point exists -- EX12 QED margin 780; EX13b: Pypower 34 worse at its OWN
routing). So this is NOT the earlier neutral "different local optima" reading --
it's raw NLP operating on a problem it's not designed for and stopping short.

DC-alone ~1% gap decomposed (EX13c/EX13d, both VERIFIED): (a) ~18-unit GENUINE
cost optimum -- cvxopf's routing equalizes generator marginal costs more tightly
(spread 0.0186 vs 0.0313; a generation-COST effect, NOT loss relief -- losses
actually go the other way); PLUS (b) ~34-unit stuck-solver residual (the nondiff
corner pathology). Both favor cvxopf.

## Status of the mechanism claim

- EMPIRICS: solid. 2x2 pattern, verified mutual-feasibility, verified
  suboptimality from both sides, marginal-cost decomposition -- all instrumented.
- EPIGRAPH MECHANISM: presented as the LIKELY, SOURCE-GROUNDED explanation
  (`_pwl_cost_expr` literally returns `cp.maximum(*affine_pieces)` = the epigraph
  form), NOT a runtime-instrumented measurement. A one-shot attempt to walk the
  canonical program and count aux epigraph rows FAILED: `Expression.atoms()` in
  this CVXPY version takes NO type filter (`.atoms(cp.maximum)` -> TypeError).
  Punted by agreement. If revisited: recurse the expr tree by `type(a).__name__
  == "maximum"` instead of the typed `.atoms()` query, or find the DNLP
  canonical-form accessor (NOT `get_problem_data(..., nlp=True)` -- `nlp` is not
  a valid kwarg there).

## Resolved (supersedes the old "decisive next experiment")
The old open question -- "warm-started at C*, does Pypower STAY or LEAVE?" -- is
MOOT / answered. EX12 (re-solve with loss0 imposed -> feasible-and-cheaper point
in C* basin) + EX13b (Pypower 34 worse at its own routing) together establish
Pypower is stuck, no warm-start needed. The pips x0 hook hunt (old EX10) is not
required for the verdict.

## Boyd + Dan artifact (in progress)
The 2x2 is the basis for a single "proof by code" demo script (build 4 variants,
solve both ways, cross-check feasibility+cost, print tables) to bring to Boyd +
Dan -- "DNLP does something very cool." Report drafted; consolidated demo script
NOT yet written (placeholder ref in the report's Reproducing section).
