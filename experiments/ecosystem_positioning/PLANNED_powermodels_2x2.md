# PLANNED EXPERIMENT: cvxopf vs PowerModels.jl — 2x2 (DC line x PWL cost)

**Status:** planned, not started. Parked behind M16 and M17 — this strengthens
the *methods/positioning* story, not the current component-unification work.
**Do not start until M16 is complete.**

**Motivation.** The DNLP-canonicalization tractability finding
(see [[dnlp-canonicalization-tractability-thesis]],
`experiments/dnlp_vs_pypower/`) currently rests on a cvxopf-vs-**Pypower** 2x2
(PWL cost x DC line). Pypower is the weakest possible foil for a *solve-
performance* claim: unmaintained MATLAB port. The first skeptic move is "you
beat the tired old tool; try a serious one." **PowerModels.jl is that serious
tool** — the research community's reference for AC-OPF formulation-and-solve,
actively developed, same strong NLP solver class (IPOPT), and its users care
about exactly the thing being measured (solve behavior on hard instances).

## What a PowerModels comparison buys

1. Closes the biggest UNVERIFIED cell in `README.md` (PowerModels is the
   comparator flagged as needing the most hedging).
2. Upgrades the claim from "beats the legacy tool" to "differs from the field
   standard." Advantage persists -> publishable; advantage vanishes -> the
   effect was partly Pypower-specific, which is also valuable to know.
3. **Isolates the open mechanism (reformulation vs solver).** Both PowerModels
   and cvxopf can target IPOPT. Hold the solver fixed, vary only the
   canonicalization (PowerModels JuMP formulation vs cvxopf DNLP
   canonicalization) -> the difference is attributable to canonicalization.
   This is the cleanest available handle on the "why," which
   [[dnlp-canonicalization-tractability-thesis]] records as unresolved.

## Design (2x2, mirroring dnlp_vs_pypower)

- **Axis 1: PWL cost** vs smooth/polynomial cost.
- **Axis 2: DC line present** vs absent.
- Same 9-bus base case as the existing 2x2 so results are directly comparable
  to the Pypower study.
- Metrics: objective, solve iterations/time, and — the point of the study —
  behavior on the flat-routing / nondifferentiable-cost cells (where the
  Pypower 2x2 showed the flat ridge + kink basins).

## Confound control — REQUIRED for the mechanism claim (the hard part)

A naive comparison measures "cvxopf's whole stack vs PowerModels' whole stack,"
NOT "DNLP canonicalization vs not." To attribute any difference to
canonicalization, pin ALL of the following across both tools:

- **Same solver + same options.** Both -> IPOPT, identical tolerances,
  `print_level`, linear solver (MUMPS), max iters. Different IPOPT defaults
  between JuMP and cyipopt will otherwise masquerade as a canonicalization
  effect.
- **Same starting point (x0).** Match initialization (flat start vs warm) and
  variable layout. The `dcline_crosseval` experiment already showed how fiddly
  x0-layout / warm-start matching is (see its EX9/EX10 probes) — reuse that
  hard-won knowledge.
- **Same variable bounds and reference-bus handling.**
- **PWL-lift isolation.** PowerModels epigraph-lifts PWL costs its own way;
  cvxopf DNLP does its own. If they lift differently, part of any difference is
  the cost formulation, not network canonicalization. The 2x2 structure is
  well-suited to separate this: compare the PWL-off cells first to establish a
  network-only baseline, THEN turn PWL on.
- **PowerModels formulation choice.** Pin `ACPPowerModel` (polar) vs
  `ACRPowerModel` (rectangular) explicitly and record which — cvxopf's DNLP is
  polar-ish (theta, v), so `ACPPowerModel` is the like-for-like baseline;
  `ACRPowerModel` is a second, informative data point, not the primary.

## Reproducibility pattern (match existing convention)

Julia is out-of-process. Follow the `dnlp_vs_pypower` pattern: generate
PowerModels references in an **isolated Julia env**, commit the resulting JSON
(objective, dispatch, iters, solve status per 2x2 cell), and do the comparison
+ analysis in Python against the committed JSON. Do NOT add Julia/PowerModels
to the package environment. Mirrors how Pypower references are already handled.

## Honest risk

Done carelessly (unmatched solver options / x0 / formulation), this produces a
number a PowerModels expert will correctly pick apart, and it would measure
stack-vs-stack, not the mechanism. Done as a controlled same-solver comparison,
it is the single highest-value addition to the methods claim — potentially
turning "empirical result, mechanism open" into "empirical result WITH mechanism
evidence." The value is entirely conditional on the confound control above.

## Definition of done

- [ ] 2x2 run for cvxopf and PowerModels with pinned solver/options/x0/bounds.
- [ ] PWL-off baseline established before PWL-on cells.
- [ ] Committed offline JSON references (isolated Julia env).
- [ ] Written finding: does the DNLP-canonicalization advantage persist vs the
      field-standard tool, and what does the same-solver control say about
      reformulation-vs-solver?
- [ ] Update `README.md` §2 (PowerModels row) and §6 TODO on completion.
