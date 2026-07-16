---
name: dnlp-canonicalization-tractability-thesis
description: The thesis behind the case9_dcline cvxopf-vs-Pypower gap — CVXPY DNLP canonicalization reshapes nonconvex NLPs into more tractable forms that make good local optima more likely (not guaranteed like DCP), so cvxopf systematically finding cheaper basins than raw-NLP tools (Pypower) may be a formulation-tractability effect, not seed luck.
metadata:
  type: project
---

**This is the basis of a paper, per the user (2026-07-15).** Not just an
explanation of one experiment's numerics — a general thesis about why a
CVXPY/DNLP-based OPF tool may systematically outperform hand-rolled nonlinear
formulations at finding good local optima.

## The thesis

Most nonlinear modeling languages pass the user's problem **directly** to the
NLP solver. CVXPY's DNLP layer instead does DCP-style **structural type
checking + automatic transformation** first, rewriting nonsmooth/awkward
constructs into solver-friendly equivalents before IPOPT/pips ever sees them.

Toy example (the user's): L1-min. Input as `sum(abs(x))` and you hand IPOPT a
nondifferentiable kink at 0 that trips up its Newton steps. DNLP recognizes the
structure and emits the **epigraph form** (`min sum(t) s.t. -t <= x <= t`),
smooth everywhere — same optimum, radically better numerical tractability.

Key distinction from DCP: for a **nonconvex** NLP, canonicalization does NOT buy
global-optimality (impossible — nonconvex). It buys a **tractability prior**:
the reshaped landscape is better-conditioned, so the local solver is MORE LIKELY
to converge cleanly and land in a good basin. "More likely," not "guaranteed."

## Why it reframes the case9_dcline gap ([[case9-dcline-optima-gap]])

Earlier framing: cvxopf finds C* (obj 5490), Pypower finds P* (obj 6250) =
neutral "different local optima," no privileged basin. The canonicalization
thesis makes this NOT neutral:
- cvxopf reaching the CHEAPER C* may be *because* DNLP presents IPOPT a
  better-conditioned landscape than Pypower's hand-rolled `dcline` userfcn +
  raw NLP.
- Pypower settling at the costlier P* may reflect its rawer formulation, not
  P* being intrinsically "correct."
- EX9 evidence is consistent: within cvxopf's landscape C* dominates — cvxopf
  HOLDS C* and DESCENDS from P* to C* (p*->c*), and P* is not even a stable
  cvxopf optimum.

Honest status: experiments SUPPORT this, don't yet PROVE it. Confounders to
rule out: seed luck, solver internals (IPOPT vs pips), the loss0 model diff.

## The decisive next experiment (sharpened EX8 / "does Pypower hold C*?")

Real Pypower warm-start (NOT the void EX8 — runopf ignores the case-table seed,
see [[case9-dcline-optima-gap]] EX8 note). Ask: **warm-started at C*, does
Pypower STAY or LEAVE?**
- STAYS -> both solvers agree C* is a valid optimum; the only difference is
  which basin each finds FROM COLD -> points squarely at
  formulation/canonicalization as the differentiator (thesis supported).
- LEAVES -> C* is a cvxopf-formulation artifact; thesis complicated.

This requires finding the real pips/opf x0 hook (opf_setup / the pips `opt`
initial-point path), since the documented bus[VM/VA]/gen[PG/QG] pattern is a
`runpf` idiom that `runopf` discards.
