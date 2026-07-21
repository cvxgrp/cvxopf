# EX6: is C* feasible in neutralized-Pypower's constraint set?

**Date:** 2026-07-14
**Status:** COMPLETE. C* is feasible in neutralized-Pypower EXCEPT for a single
1.0 MW loss0 term on link0. This is strong evidence the cvxopf/Pypower
divergence is DIFFERENT LOCAL OPTIMA, not a constraint-set difference.

## Method

Constraint-by-constraint residual check of C* (cvxopf's optimum) against the
neutralized-Pypower constraint set. Single environment, no solver, no PV/slack
freedom (unlike the discarded EX6-(B); see EX6B_CONTROL_REPORT.md).

Licensed by the Ybus-agreement result (ybus_compare.txt: cvxopf vs Pypower
Ybus max abs diff 4.4e-16 -- floating-point identical). Because the two
frameworks build the same network admittance, C* is tested against cvxopf's own
Ybus and the dcline constants directly; no pypower import needed.

Guardrail first (LOG B.4): component-reconstructed injections
(Cg@Pg - Pd + Ch_from@p_in + Ch_to@p_out) cross-checked against cvxopf's stored
p_net/q_net BEFORE any downstream residual, so a sign/units bug is caught, not
propagated. Guardrail PASSED at 1e-16.

Script: _ex6_proper_constraint_residual.py. Output: results/ex6_residual.txt.
Input: results/cstar_full.json (C* full dispatch incl Qg, p_net, q_net).

## Results (each constraint, labeled residual)

| check | residual | verdict |
|---|---|---|
| Guardrail (recon vs p_net/q_net) | 1.1e-16 MW / 0 MVAr | PASS |
| C1 nodal real balance | 1.7e-13 MW | satisfied |
| C2 nodal reactive balance | 6.2e-13 MVAr | satisfied |
| C3 gen P bounds | all within, margin to bound | satisfied |
| C4 gen Q bounds | all within | satisfied |
| C5 voltage bounds | within (one bus at Vmax=1.1) | satisfied |
| C6 DC box Pmin<=p_in<=Pmax | at bounds (link0 Pmin, link1 Pmin, link3 Pmax) | satisfied |
| C7 DC coupling law (with loss0) | [-1.0, 0.0, 0.0] MW | link0 VIOLATES by loss0 |

(Bound residuals use viol = bound - value / value - bound; negative = satisfied.)

C7 detail: Pypower's law is p_out = -(1-L1)*p_in + L0 [MW]. Link0 (30->4):
L1=0.01, L0=1, C* p_in=1.0, p_out=-0.99. Required p_out = -0.99+1.0 = 0.01;
C* delivers -0.99; residual exactly -1.0 MW = loss0. Links 1,3: zero.

## Interpretation

C* satisfies EVERY constraint of the neutralized-Pypower problem to machine
precision EXCEPT the loss0 term on link0 -- a 1.0 MW injection difference that
was dropped from cvxopf's model BY DESIGN (CLAUDE.md Milestone 7; hvdc.py drops
loss0 with a UserWarning).

A 1 MW loss0 discrepancy cannot explain either the 760-unit objective gap
(C* 5490 vs P* 6249.87) or the full-range link1 dispatch swing (C* runs link1
at p_in=2, its box min; P* at p_in=10, its box max; link1 is lossless, so loss0
is irrelevant to it). C* is therefore essentially feasible in Pypower's problem.

=> The two solvers land on DIFFERENT POINTS of the (near-)same feasible set.
This is strong evidence for DIFFERENT LOCAL OPTIMA -- the branch the handoff
(B.6) flagged as surprising and requiring proof. EX6 supplies the C*-side
evidence. The prior leading interpretation (constraint-set difference dominated
by branch limits) is now REJECTED: branch limits were neutralized away, and the
only residual constraint difference (loss0) is far too small.

## Caveats

- loss0 IS a real model difference; C* is not 100% feasible in Pypower. But it
  is off by exactly one small, known, deliberate term. "Feasible except loss0."
- This is the C* side only. EX7 (is P* feasible in cvxopf's constraint set?) is
  the symmetric check and should show P* feasible-but-suboptimal in cvxopf
  (i.e. cvxopf could sit at P* but found a cheaper basin). Run EX7 to confirm
  the local-optima verdict from both sides before finalizing EX8.

## Next
- EX7: plug P* dispatch into cvxopf's constraint expressions (same residual
  style), or warm-start cvxopf at P*.
- EX8 verdict once both sides are in.
- EX9 (warm-start basin test) if EX8 lands on local optima.