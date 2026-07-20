# Memory index

- [HVDC plan MVP scope](hvdc-plan-mvp-scope.md) — what the t_case9_dcline test case exercises in the MVP vs full-lossy (M15) milestone
- [case9_dcline oracle gap](case9-dcline-optima-gap.md) — RESOLVED (EX12, 2026-07-20): P* is SUBOPTIMAL for Pypower's own problem, not an alternate optimum. C+ (C* basin, link0 loss0 imposed) is fully Pypower-feasible AND cheaper (5469 vs P* 6249.87, margin 780); hypothesis confirmed C+ stays in C* basin. DNLP-tractability reading live. Traps recorded: mixed PWL/poly cost readout (bit EX11+EX12), HVDC constraint-locator vs nodal balance. Gate 6b stays consistency-based
- [CVXPY affine equality rule](cvxpy-affine-equality-rule.md) — equality constraints must be affine; convex atoms only in objective/inequalities
- [HVDC silent-ignore dispatch constraint](hvdc-silent-ignore-dispatch-constraint.md) — problem.py's single positional dispatch site makes "singlenode signatures unchanged" impossible; Step 3/R4 must be rewritten
- [HVDC plan session handoff](hvdc-plan-session-handoff.md) — state for a fresh session to finish milestone-7-hvdc.md (remaining W6/W7/W8 + F1/F2; decisions locked)
- [Milestone 7 HVDC status](milestone-7-hvdc-status.md) — T0/Step-0 done 2026-07-13; T-vs-Step naming; self-contained dcline oracle; T1 next
- [DNLP canonicalization tractability thesis](dnlp-canonicalization-tractability-thesis.md) — PAPER BASIS: CVXPY DNLP reshapes nonconvex NLPs into more-tractable forms (epigraph etc.) -> good local optima MORE LIKELY (not guaranteed like DCP); cvxopf finding cheaper basins than raw-NLP Pypower may be a formulation-tractability effect. Decisive test: does Pypower HOLD C*?
- [cvxopf session working style](cvxopf-session-working-style.md) — checkpointed increments, verify-don't-trust, approach sign-off, rabbit-hole caution
