# Memory index

- [Harness tool issues](harness-tool-issues.md) — stale/out-of-order tool results, false Edit echoes, Read truncation; verify with Grep -n
- [HVDC plan MVP scope](hvdc-plan-mvp-scope.md) — what the t_case9_dcline test case exercises in the MVP vs full-lossy (M15) milestone
- [case9_dcline oracle gap](case9-dcline-optima-gap.md) — cvxopf's case9_dcline AC solve doesn't value-match Pypower (C* 5490 vs P* 6249.87 neutralized). DIFFERENT LOCAL OPTIMA confirmed BOTH SIDES (EX6+EX7b, 2026-07): C* feasible in Pypower and P* feasible in cvxopf, each except one 1 MW loss0 term on link0 (symmetric residuals). Branch limits + PWL cost RULED OUT. EX8 verdict/EX9 warm-start open. Gate 6b consistency-based
- [CVXPY affine equality rule](cvxpy-affine-equality-rule.md) — equality constraints must be affine; convex atoms only in objective/inequalities
- [HVDC silent-ignore dispatch constraint](hvdc-silent-ignore-dispatch-constraint.md) — problem.py's single positional dispatch site makes "singlenode signatures unchanged" impossible; Step 3/R4 must be rewritten
- [HVDC plan session handoff](hvdc-plan-session-handoff.md) — state for a fresh session to finish milestone-7-hvdc.md (remaining W6/W7/W8 + F1/F2; decisions locked)
- [Milestone 7 HVDC status](milestone-7-hvdc-status.md) — T0/Step-0 done 2026-07-13; T-vs-Step naming; self-contained dcline oracle; T1 next
- [cvxopf session working style](cvxopf-session-working-style.md) — checkpointed increments, verify-don't-trust, approach sign-off, rabbit-hole caution
