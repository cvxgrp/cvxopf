---
name: case9-dcline-optima-gap
description: Why cvxopf's case9_dcline AC solve does not value-match the Pypower oracle. CONFIRMED (EX6+EX7b, 2026-07) different local optima, not a constraint-set difference: C* feasible in Pypower and P* feasible in cvxopf, each except one 1 MW loss0 term on link0 (symmetric residuals). (Filename is a misnomer: branch limits were ruled out.)
metadata:
  type: project
---

Gate 6b (compare a cvxopf `formulation="ac"` solve of `case9_dcline()` against
`tests/fixtures/case9_dcline_pypower_reference.json`) **cannot be a value-match
oracle test**: dispatch and objective diverge (cvxopf obj ~5490 vs committed
fixture ~6446). The 2026-07-14 cross-eval investigation
(`experiments/dcline_crosseval/`, durable) established WHY.

## VERDICT (EX6, 2026-07-14): different local optima, not a model difference

Built a **neutralized Pypower** model matching cvxopf's constraint set as closely
as possible (branch limits off, dcline dummy-gen reactive pinned 0, terminals
PV->PQ); its optimum **P\*** has obj 6249.87. cvxopf's optimum **C\*** has obj
5490. Same objective (EX4/EX5: cost functions agree at a shared point).

**EX6 (constraint-by-constraint residual of C\* in neutralized Pypower):** C\*
satisfies EVERY constraint to machine precision (nodal real/reactive balance
1e-13, all gen/voltage/DC-box bounds) EXCEPT the **loss0 term on link0**: C7 DC
coupling residual = [-1.0, 0, 0] MW, i.e. link0 off by exactly loss0=1 MW (which
cvxopf drops by design; hvdc.py drops loss0 with a UserWarning). See
`experiments/dcline_crosseval/EX6_REPORT.md`, `results/ex6_residual.txt`.

A 1 MW loss0 term cannot explain the 760 objective gap or the link1 dispatch
swing (C\* runs link1 at p_in=2 = box min; P\* at p_in=10 = box max; link1 is
LOSSLESS, so loss0 is irrelevant to it). => C\* is essentially feasible in
Pypower's problem; the two solvers sit in **different local optima** of the
(near-)same nonconvex AC-OPF. AC-OPF is nonconvex (DNLP via IPOPT), so distinct
basins are legitimate.

**Ruled out as the cause:**
- **Branch limits** (this file's old name): neutralized away and C\* still
  cheaper; not the cause.
- **PWL cost:** matches Pypower exactly on `case9_pwl` (obj 5322.94 both).
- **loss0 / dcline device model as a large effect:** loss0 is exactly 1 MW here;
  the reactive-box/PV-terminal differences were neutralized and C\* remained
  feasible. The device-model difference is real but small, not the 760 driver.

## Method notes (what worked, what didn't)

- **Ybus agreement:** cvxopf vs Pypower Ybus for case9_dcline are floating-point
  identical (max abs diff 4.4e-16) -> DC lines contribute nothing to Ybus. This
  LICENSES testing C\* against cvxopf's own Ybus (no live pypower needed) and
  makes the network side of feasibility a near-tautology. Added as a T7
  package-test deliverable in `plans/milestone-7-hvdc.md` (static fixture).
- **DISCARDED method (EX6-(B)):** feeding C\* through Pypower `runpf` and
  measuring voltage drift is INVALID -- the control (native P\* through the same
  harness) drifted MORE than C\* (0.041 vs 0.011 pu). runpf/runopf freeze
  different variable sets, so the harness manufactures drift. See
  `EX6B_CONTROL_REPORT.md`. Do not resurrect it.

## BOTH-SIDES CONFIRMED (EX7b, 2026-07-15)
- **EX7a DONE, committed `4a18b82`:** regenerated P\* as structured JSON
  (`results/pstar_full.json`, mirror of cstar_full.json), obj-gated to 6249.8659.
  Records real Pg/Qg, from/to dummy Pg, dummy Qg (all 0), Vm/Va, gen_bus, and
  Pypower's own Ybus+i2e. P\* decodes to `p_in=[1,10,10]`, `p_out=[+0.01,-10,-9.5]`.
  Corrects a stale prose sign typo: link0 `to_dummy_Pg=-0.01` so `p_out[0]=+0.01`.
  Script `_ex7a_pstar_full.py`.
- **EX7b DONE (2026-07-15), UNCOMMITTED:** constraint-by-constraint residual of
  P\* in cvxopf's set (`_ex7b_pstar_in_cvxopf.py`, `results/ex7b_residual.txt`,
  `EX7b_REPORT.md`). RESULT: P\* is feasible in cvxopf to machine precision
  (C1/C2 nodal balance 3e-9; C3-C6 satisfied; YBUS agree 4.4e-16) EXCEPT C7 DC
  coupling on link0 = +1.0 MW = loss0 -- the EXACT SYMMETRIC MIRROR of EX6's
  -1.0 (EX6 = C* vs Pypower's WITH-loss0 law; EX7b = P* vs cvxopf's DROPPED-loss0
  law). => "different local optima" now confirmed FROM BOTH SIDES.
- **Sign gotcha (EX7b, recorded so it isn't re-hit):** in P\* the DC terminals
  are DUMMY GENERATORS injecting RAW Pg = -p_in (from-bus), -p_out (to-bus) --
  the OPPOSITE of cvxopf's Convention-B grid injection (+p_in,+p_out). Nodal
  balance for P\* must use the raw dummy Pg; using +p_in/+p_out manufactures a
  spurious 2x residual (up to 39 MW). Caught by the guardrail, as designed.
- **Still open:** EX8 consolidated verdict; EX9 optional warm-start basin test
  (start cvxopf at P\*, see if it stays or falls to C\*).

## Consequence for Gate 6b (unaffected by the cause)
Gate 6b uses internal-consistency assertions (nodal balance ~0, the
`p_out = -(1-loss_frac)*p_in` law on fixed-direction links, `hvdc_loss >= 0`,
loss0 UserWarning), NOT an objective/Pg oracle match. See
`tests/test_hvdc.py::TestHVDCCase9DclineConsistency`, [[hvdc-plan-mvp-scope]],
[[milestone-7-hvdc-status]].

HVDC links import via `hvdc_from_dcline(case9_dcline()["dcline"])` with no cost
table (zero-cost, matching the fixture's `del dclinecost`). Three in-service
links: 30->4 [1,10] loss1%, 7->9 [2,10] lossless, 5->9 [0,10] loss5%.