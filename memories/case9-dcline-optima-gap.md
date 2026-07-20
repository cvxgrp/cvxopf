---
name: case9-dcline-optima-gap
description: Why cvxopf's case9_dcline AC solve does not value-match the Pypower oracle. RESOLVED (EX12, 2026-07-20): P* is a SUBOPTIMAL local point for Pypower's OWN problem -- not an alternate optimum. NOT a constraint-set difference (EX6+EX7b: C* and P* mutually feasible except one 1 MW loss0 term on link0). cvxopf finds the cheaper basin (C* 5490 vs P* 6249); DNLP-tractability reading is the live explanation. (Filename is a misnomer: branch limits were ruled out.)
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
- **EX12 DONE (2026-07-20) — THE QED FIRES, P* IS SUBOPTIMAL:** re-solved
  cvxopf's neutralized AC-OPF with link0's loss coupling REPLACED by Pypower's
  with-loss0 law (`p_out[0]=-(1-L1[0])*p_in[0]+L0[0]`, links 1/2 unchanged);
  let IPOPT redistribute loss0 through the flow. Result C+ is FULLY feasible in
  neutralized Pypower (C1 nodal 1.3e-13 -- EX11's stuck 1 MW residual GONE;
  C7 with-loss0=[0,0,0] on every link) AND cheaper: C+ obj 5469.04 vs P* 6249.87,
  **margin 780**. => P* is a SUBOPTIMAL local point for Pypower's own problem,
  QED. Hypothesis CONFIRMED (user, pre-registered): C+ stayed in the C* basin
  (||dPg||=1.05 MW, link1 held at box-min 2), did NOT slide to P* (135.6 MW away,
  link1 box-max 10). No src/ changes: graft reassigns `build.prob`, solves via
  `build.solve()`. DCP: each grafted equality verified affine/is_dcp in isolation
  before grafting (whole problem is nonconvex-by-design, nlp=True). Subsumes the
  provisional EX10 question. `_ex12_cplus_solve.py`, `EX12_REPORT.md`,
  `results/ex12_cplus.txt`.
- **TRAP 1 (bit EX11 AND EX12 first pass) — mixed cost model:** case9_dcline
  gencost MIXES MODEL=1 (piecewise-linear, cols 4:4+2*NCOST are (x,f) breakpoint
  PAIRS) and MODEL=2 (polynomial) rows: row0 M1 (4 pts), row1 M2 (linear), row2
  M1 (3 pts). Reading M1 breakpoints as poly coeffs gives the spurious 11536.85
  (the EX11 bug, reproduced). FIX: dispatch on MODEL col, np.interp the PWL,
  np.polyval the poly. GUARD: re-evaluate C*'s own dispatch through the readout
  and assert it reproduces C* obj 5490.10 -- that in-band check is what makes the
  C+ objective trustworthy. `_ex12_probe_cost.py`.
- **TRAP 2 (EX12) — constraint locator:** the HVDC loss coupling is ONE
  vectorized (3,) equality `p_out==multiply(coeff_vec,p_in)` (hvdc.py:268). The
  NODAL BALANCE `p == ... Ch_from@p_in + Ch_to@p_out ...` ALSO contains both HVDC
  vars, so matching "involves both p_in and p_out" hits nodal balance FIRST
  (constraints[57] shape (9,)) not the coupling (constraints[61] shape (3,));
  deleting nodal balance => IPOPT local infeasibility. FIX: match the equality
  whose ONLY vars are p_in/p_out (`ids == {p_in.id, p_out.id}`).
  `_ex12_probe_locate.py`.
- **C4=-282 is BENIGN (EX12):** case9_dcline reactive bounds are +/-300 MVAr
  (d["Qgmin/max"]=+/-3.0 pu * 100); C+ Qg=[17.6,5.8,-4.9] sits far inside, worst
  one-sided slack 17.6-300=-282.4 (negative=interior=no violation). Bound length
  matches extracted Qg (3,3): NOT a dummy-gen misalignment. `_ex12_probe_c4.py`.
- **NAMING:** session labels EX8(void)/EX9(cvxopf warmstart)/EX10(pypower
  warmstart)/EX11(QED) are canonical; they supersede TEST_PLAN.md's original
  EX8(verdict doc)/EX9(warm-start) scheme. TEST_PLAN's EX8 verdict doc was never
  written and is moot given the warm-start results.

## 2x2 STUDY (2026-07-20) — generalizes the gap to a nondifferentiability thesis
Follow-on to EX12. Controlled 2x2 on 9-bus: factors = PWL cost (mixed
MODEL=1/2) x HVDC DC lines. Full prose:
`experiments/dcline_crosseval/DNLP_ROUTING_AND_PWL_REPORT.md`; drives
[[dnlp-canonicalization-tractability-thesis]].
- Neither nondiff feature ALONE trips Pypower: plain 9-bus (smooth) and
  case9_pwl (PWL, NO dc lines) BOTH match Pypower to 1e-4 as COMMITTED oracle
  tests (`TestCase9`, `TestCase9Pwl`; verified passing, `case9_pwl` has no
  dcline). DC+smooth: Pg agrees <2 MW but routing flips; PWL+DC: ~12% gap. =>
  nondiff-feature INTERACTION; driver is nondifferentiability (kinks+corners),
  not the shared AC nonconvexity.
- **Smooth+DC routing flip is REAL, not a bug** (`_ex13_probe_endpoints.py`):
  no from/to endpoint swap on either side. links B(7->9 lossless) + C(5->9 5%)
  both deliver to bus 9, withdraw at 7 vs 5. cvxopf floors B=2/maxes C=10;
  Pypower maxes B=10/idles C~0.
- **Why cvxopf's routing is cheaper (EX13c decomp + EX13d CHECK1, VERIFIED):**
  NOT loss relief (cvxopf carries slightly HIGHER loss). Generation-COST effect:
  cvxopf equalizes marginal cost (2c2*P+c1) more tightly across unconstrained
  gens (spread 0.0186 vs 0.0313). ~18-unit genuine optimum + ~34-unit
  stuck-solver residual (Pypower worse at its OWN routing, EX13b).
- **CHECK2 (epigraph canonicalization) PUNTED:** one-shot introspection failed
  (`Expression.atoms()` no type filter this CVXPY; `get_problem_data(nlp=True)`
  invalid kwarg). Asserted as source-grounded likely explanation
  (`cost.py::_pwl_cost_expr` returns `cp.maximum(*affine_pieces)`).
- Scripts UNCOMMITTED: `_ex13_smoothcost_{pypower,cvxopf}.py`,
  `_ex13b_routing_cvxopf.py`, `_ex13c_routing_decomp.py`,
  `_ex13d_mechanism_checks.py`, `_ex13_probe_endpoints.py`; `_ex_crosseval.py`
  gained optional `gencost=`. Report draft + memories pending commit.

## Consequence for Gate 6b (unaffected by the cause)
Gate 6b uses internal-consistency assertions (nodal balance ~0, the
`p_out = -(1-loss_frac)*p_in` law on fixed-direction links, `hvdc_loss >= 0`,
loss0 UserWarning), NOT an objective/Pg oracle match. See
`tests/test_hvdc.py::TestHVDCCase9DclineConsistency`, [[hvdc-plan-mvp-scope]],
[[milestone-7-hvdc-status]].

HVDC links import via `hvdc_from_dcline(case9_dcline()["dcline"])` with no cost
table (zero-cost, matching the fixture's `del dclinecost`). Three in-service
links: 30->4 [1,10] loss1%, 7->9 [2,10] lossless, 5->9 [0,10] loss5%.