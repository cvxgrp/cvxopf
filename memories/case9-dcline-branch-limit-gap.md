---
name: case9-dcline-branch-limit-gap
description: Why cvxopf's case9_dcline AC solve does not value-match the Pypower oracle. Root cause NOT yet proven; leading candidates are the dcline device-model difference and nonconvex AC-OPF local optima. Branch limits and PWL cost were ruled out.
metadata:
  type: project
---

Gate 6b (compare a cvxopf `formulation="ac"` solve of `case9_dcline()` against
`tests/fixtures/case9_dcline_pypower_reference.json`) **cannot currently be a
value-match oracle test**. The dispatch and objective diverge by a large margin
(cvxopf obj ~5490 vs fixture ~6446). Investigated 2026-07-14 by throwaway
probes (since deleted). **The root cause is NOT yet established** — see the
candidates below. Investigation is ongoing (research mode, before T7).

## What is PROVEN (by direct experiment)

**Branch limits are NOT the cause.** An earlier version of this note wrongly
blamed the tight `rateA=40` on branch 4 (bus6->bus7). Relaxing **all** branch
limits to effectively infinite in the Pypower oracle did **not** move its
dispatch toward cvxopf's (obj 6446 -> 6213, still far from cvxopf's 5490; gen 2
rose only to ~128, never cvxopf's ~220). (cvxopf does ignore branch limits —
Milestone 4 is stubbed — but that is not what breaks this comparison.)

**PWL cost is NOT the cause.** case9_dcline gens 0 and 2 use MODEL=1 PWL costs.
The PWL implementation matches Pypower exactly on a mixed PWL/poly case (see
`TestCase9PwlVsPypower` / `case9_pwl`, obj 5322.94 both). Pypower's PWL
formulation (`makeAy.py`: `Y >= c_i + m*(Pg - p_i)`, minimize `Y`) is the same
epigraph as cvxopf's `cp.maximum` of segment lines.

**loss0 is minor:** row 0's fixed 1 MW loss shifts objective by only ~16.

## Candidate causes (NOT yet distinguished)

The root cause is one or a mix of the following; we have not run the clean
experiments that separate them. Do not state any of these as settled.

1. **DC-line device-model difference (hypothesis, unproven).** Pypower's
   `toggle_dcline` models each DC line as two dummy generators at the terminal
   buses, converting those terminals to **PV buses with reactive bounds
   [Qmin,Qmax]** (voltage-regulating reactive sources), coupled in real power
   by `(1-L1)*Pgf + Pgt = -L0`. cvxopf's HVDC MVP models the link as a pure
   **unity-PF real-power injection** with no reactive term and no voltage
   control at the terminals. This *could* reshape the AC solution — but we have
   not proven it is the operative cause. Note: zeroing the dcline reactive
   bounds QF/QT in Pypower *raised* its objective and barely moved real
   dispatch, but that test left the PV-bus conversion in place, so it did NOT
   cleanly isolate the device model.

2. **Nonconvex AC-OPF local optima (co-equal candidate).** AC-OPF is nonconvex
   (sin/cos power flow; cvxopf uses DNLP via IPOPT, not a convex solver). Two
   solvers can legitimately converge to different local optima of the *same*
   model. So part or all of the gap may be basin selection, not any model
   difference. (An earlier argument here — "two convex solvers can't disagree
   by 950, so the feasible sets must differ" — was INVALID because AC-OPF is
   not convex.)

## Experiments that would distinguish them (not yet run)

- **Feasibility cross-check:** plug Pypower's solved (Pg, Vm, Va) into cvxopf's
  constraint set and measure residuals. Feasible-in-cvxopf => points to local
  optima (#2); infeasible => points to a model difference (#1).
- **Warm-start / multi-start:** solve cvxopf from Pypower's dispatch (and from
  many inits). Stays near Pypower with comparable objective => #2; slides to
  5490 => #1.
- **Model-coincidence:** make cvxopf terminals PV/reactive, or Pypower
  terminals unity-PF PQ, and check convergence.

## Consequence for Gate 6b (unaffected by which cause it is)

Gate 6b uses internal-consistency assertions (nodal balance residual ~0, the
`p_out = -(1-loss_frac)*p_in` loss law on fixed-direction links,
`hvdc_loss >= 0`, and the expected `loss0` UserWarning on import), NOT an
objective/Pg oracle match. This holds regardless of which candidate is the true
cause. See `tests/test_hvdc.py::TestHVDCCase9DclineConsistency`,
[[hvdc-plan-mvp-scope]], [[milestone-7-hvdc-status]].

**Standard case9 contrast (why existing Pypower fixture tests pass):** standard
`case9()` has no dcline, all-polynomial costs, and no binding branch limits at
its solution — cvxopf and Pypower agree there.

HVDC links import via `hvdc_from_dcline(case9_dcline()["dcline"])` with **no
cost table** (Option A: matches the fixture script's `del ppc['dclinecost']`;
links are zero-cost). The three in-service links import as boxes: 30->4 [1,10]
loss1%, 7->9 [2,10] lossless, 5->9 [0,10] loss5%.

**PWL oracle sidebar (good outcome of this investigation):** cvxopf gained
MODEL=1 PWL generator cost support (`cost.py`), validated against Pypower via
the fabricated `case9_pwl` case. An all-PWL case (`case30pwl`) cannot be a
Pypower oracle — `pypower==5.1.19`'s `opf_costfcn` raises on an empty
polynomial-gen set under numpy 2.x — so it ships as
`examples/case30pwl_ac.py`, not a fixture test.
