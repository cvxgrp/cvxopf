---
name: dnlp-canonicalization-tractability-thesis
description: Why cvxopf (CVXPY/DNLP) beats Pypower on some AC-OPF. 2x2 study on 9-bus (PWL cost x HVDC lines): PWL costs add genuine nondifferentiability (objective kinks); DC lines add routing freedom that FLATTENS the landscape (NOT nondiff corners -- corrected 2026-07-20, user caught the overclaim). Each feature alone -> methods agree exactly; combined -> a flat ridge studded with kink-separated basins, Pypower ~14% worse and verifiably suboptimal for its own problem. DNLP epigraph-lifts the kinked costs. Mechanism (reformulation vs solver) not yet isolated.
metadata:
  type: project
---

Working thesis about why a CVXPY/DNLP-based OPF tool tends to find better local
optima than hand-rolled raw-NLP formulations. Originated 2026-07-15 from the
[[case9-dcline-optima-gap]] investigation; SHARPENED 2026-07-20 by a controlled
2x2 study (write-up: `experiments/dnlp_vs_pypower/REPORT.tex`).

## The mechanism (CORRECTED 2026-07-20): two DIFFERENT contributions

IMPORTANT correction: the earlier "DC adds nondifferentiable corner structure"
framing was WRONG (user caught it). Box/bound constraints and their active-set
corners are squarely in an interior-point solver's wheelhouse -- NOT a source of
nondifficulty. The two features contribute differently:

- PWL costs add GENUINE nondifferentiability: each convex PWL cost is a
  pointwise max of affine segments, a KINK at every breakpoint where the
  gradients/Hessians an interior-point method needs are ill-defined. DNLP
  dissolves this by epigraph reformulation (PWL -> cp.maximum of affine = one
  aux var + linear inequalities; source `cost.py::_pwl_cost_expr`). Same move as
  L1 -> epigraph. This is the classic, real reason disciplined reformulation
  helps, and why DNLP is unbothered by PWL costs.
- DC lines add ROUTING FREEDOM that FLATTENS the landscape: a controllable
  injection with a capacity box + affine loss law is smooth and convex. What it
  adds is many nearly cost-equivalent routings (links B/C both feed bus 9 from
  different source buses) -- a shallow, nearly flat ridge. On a flat ridge a
  local solver can settle at different points (the DC-only case: dispatch
  agrees, only routing differs ~1%).

The big ~14% gap appears only COMBINED: routing freedom multiplies near-optimal
points AND the routing choice changes which cost segment each gen sits on, so the
flat ridge becomes studded with shallow kink-separated basins; the raw
formulation lands in a worse one, the DNLP (smooth-epigraph) formulation a
better one.

CRITICAL distinction the 2x2 nails: the AC power-flow NONCONVEXITY (sin/cos) is
common to BOTH methods and is NOT where they diverge -- they agree perfectly on
smooth variants that share the same AC nonconvexity. NOT YET ISOLATED: whether
the combined-cell gap is DNLP-reformulation quality vs IPOPT-beats-pips solver
differences vs flat-ridge local-optimum lottery. Open test: feed pips the
DNLP-canonicalized problem, see if the gap closes.

## The 2x2 evidence (9-bus; factors = PWL cost, HVDC DC lines)

| | no DC lines | with DC lines |
|--|--|--|
| smooth cost | exact agreement | Pg agrees (<2 MW); ~1% routing gap, cvxopf cheaper |
| PWL cost    | exact agreement | ~14% gap, cvxopf cheaper (obj 5469 vs 6250) |

Neither feature alone makes the methods diverge; TOGETHER they do (PWL kinks +
DC routing freedom -> flat ridge with kink-separated basins). Every disagreement
favors cvxopf, and Pypower's answer is VERIFIABLY suboptimal each time (a
feasible, cheaper point exists -- EX12 QED margin ~780; EX13b: Pypower 34 worse
at its OWN routing). So this is NOT a neutral "different local optima" reading:
Pypower reaches a genuinely worse feasible point. WHY (DNLP-reformulation vs
IPOPT-vs-pips solver diff vs flat-ridge lottery) is NOT yet isolated.

DC-alone ~1% gap decomposed (EX13c/EX13d, both VERIFIED): (a) ~18-unit GENUINE
cost optimum -- cvxopf's routing equalizes generator marginal costs more tightly
(spread 0.0186 vs 0.0313; a generation-COST effect, NOT loss relief -- losses
actually go the other way); PLUS (b) ~34-unit residual where Pypower is stuck
short of optimum at its OWN routing. Both favor cvxopf.

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
