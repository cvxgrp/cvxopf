---
name: case9-dcline-optima-gap
description: Why cvxopf's case9_dcline AC solve does not value-match the Pypower oracle. NOT a constraint-set difference (EX6+EX7b: C* and P* mutually feasible except one 1 MW loss0 term on link0). Each solver holds its own cold-start basin (cvxopf->C* 5490, Pypower->P* 6249); open question whether cvxopf's DNLP canonicalization finds a systematically better basin or it's genuine bistability. (Filename is a misnomer: branch limits were ruled out.)
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
- **EX8 VOID (2026-07-15):** Pypower-side warm-start did nothing at the solver
  level. runopf discards the case-table VM/VA/PG/QG seed (that pattern is a
  `runpf` idiom; `runopf` cold-inits x0). PROOF: cold vs seed-at-optimum give a
  BYTE-IDENTICAL 24-iteration history, both starting obj 19271.925
  (`_ex8_probe_iters.py`). Do not interpret the void two-arm EX8 output.
- **EX9 DONE (2026-07-15), UNCOMMITTED — real warm-start, cvxopf side:** CVXPY
  passes variable.value to IPOPT as x0. Seeded fully (theta/v/Pg/Qg/p_in/p_out +
  closed-form P_vec/Q_vec + p=Rp@P_vec). RESULT: **cvxopf HOLDS C\* and DESCENDS
  P\*->C\*** (both arms settle to C\*, obj 5490, p_in=[1,2,10]). P-arm iter-0
  obj=6250 (seed reached IPOPT) then monotonically down to 5490. C-arm user-var
  drift 1.3e-10. => within cvxopf's solver P\* is NOT a stable optimum.
  `_ex9_warmstart_cvxopf.py`, `EX9_REPORT.md`.
- **iter-0 inf_pr=25 red herring:** the C\* seed is fully feasible in cvxopf
  (all 62 b.prob constraints <=7.1e-15, `_ex9_probe_seedfeas.py`). The 25 is
  CVXPY's CANONICALIZATION auxiliaries, which .value does NOT seed (62 user
  constraints -> 111 canonical). The 21 control iters are IPOPT reconciling
  hidden auxiliaries; user vars barely move. Trust drift, not iter count.
- **REFRAMING (DNLP thesis):** the gap is likely NOT neutral "symmetric local optima."
  See [[dnlp-canonicalization-tractability-thesis]] — DNLP canonicalization may
  present IPOPT a more tractable landscape than Pypower's raw NLP, so cvxopf
  reaching the cheaper C\* could be a formulation-tractability effect.
- **EX10 PROVISIONAL (2026-07-15) — real Pypower warm-start, pips x0 hook:**
  found the hook (intercept `pips`, overwrite Va/Vm/Pg/Qg in x0; layout len 38 =
  Va[0:9] Vm[9:18] Pg[18:27] Qg[27:36] y[36:38]). Gotchas: ext2int REORDERS gens
  (i2e=[0,1,2,3,6,5,4,7,8]) so seed Pg/Qg must be permuted to internal order;
  the y (PWL) block left at default. RESULT: seed reaches pips (cold iter-0 obj
  19271 vs seeded 15786, so NOT void) and pips converges to P*, NOT C*. BUT
  provisional: never cleanly confirmed the seed lands FEASIBLY (first-principles
  nodal mismatch ~1.5 vs gh_fcn |g|=1e6 disagreed; trajectory balloons to ~1e5).
  Warm start is primal-only (duals/barrier cold), so departure may be
  restoration, not basin rejection. Not settled. `_ex10_warmstart_pips.py`.
- **EX11 NOT SOLID (2026-07-15) — QED-by-construction:** build C+ = C* nudged
  fully Pypower-feasible (link0 p_out -0.99->+0.01, C7 exception gone); if fully
  feasible AND cheaper than P*, P* is suboptimal. FAILED as a static construction:
  the nudge injects +1 MW at the converter bus and a static rebalance on a gen
  at a different bus cannot close nodal balance (C1=1 MW at bus 3) -- loss0
  couples through the AC flow. Cost readout also buggy. `_ex11_cplus_qed.py`
  (bannered partial).
- **EX12 NEXT (the real QED):** don't CONSTRUCT C+, SOLVE for it -- re-solve with
  link0 loss0 IMPOSED, get a genuinely Pypower-feasible point in the C* basin,
  evaluate its objective in Pypower's cost model. Fully feasible AND < 6249.87
  => P* suboptimal, QED. Subsumes the provisional EX10 question.
- **NAMING:** session labels EX8(void)/EX9(cvxopf warmstart)/EX10(pypower
  warmstart)/EX11(QED) are canonical; they supersede TEST_PLAN.md's original
  EX8(verdict doc)/EX9(warm-start) scheme. TEST_PLAN's EX8 verdict doc was never
  written and is moot given the warm-start results.

## Consequence for Gate 6b (unaffected by the cause)
Gate 6b uses internal-consistency assertions (nodal balance ~0, the
`p_out = -(1-loss_frac)*p_in` law on fixed-direction links, `hvdc_loss >= 0`,
loss0 UserWarning), NOT an objective/Pg oracle match. See
`tests/test_hvdc.py::TestHVDCCase9DclineConsistency`, [[hvdc-plan-mvp-scope]],
[[milestone-7-hvdc-status]].

HVDC links import via `hvdc_from_dcline(case9_dcline()["dcline"])` with no cost
table (zero-cost, matching the fixture's `del dclinecost`). Three in-service
links: 30->4 [1,10] loss1%, 7->9 [2,10] lossless, 5->9 [0,10] loss5%.