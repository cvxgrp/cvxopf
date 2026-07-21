# Handoff: Milestone 7 (HVDC) + case9_dcline cross-eval investigation

## A. Milestone 7 HVDC status (committed, branch `dcline`)

Done and committed:
- `results.py` HVDC extraction (`p_hvdc_in`/`p_hvdc_out`/`hvdc_loss`).
- `cost.py` MODEL=1 piecewise-linear generator costs (convex → exact `cp.maximum` of segment lines; nonconvex → numpy lower convex hull + warning).
- PWL Pypower oracle: fabricated `case9_pwl` (case9 + mixed PWL/poly gencost) via `generate_testcases.py::_fabricate_case9_pwl` + `generate_pypower_fixtures.py::_case9_pwl`; `TestCase9PwlVsPypower` passes (exact match 5322.94).
- `case30pwl` shipped as an example (`examples/case30pwl_ac.py`), **not** a fixture: pypower 5.1.19 `opf_costfcn` raises on all-PWL (empty polynomial-gen set) under numpy 2.x.
- T6 Gate 6 (`TestHVDCResultExtraction`) + Gate 6b (`TestHVDCCase9DclineConsistency`) in `tests/test_hvdc.py`. Gate 6b is **consistency-based** (balance, loss law, `hvdc_loss>=0`, loss0 warning), not a Pypower value-match. Full suite 815 pass.

Next milestone work (not started): T7 (check `docs/milestone-7-hvdc.md`). Future feature surfaced by this investigation: HVDC terminal reactive as a Q box (MVP) → apparent-power circle `P²+Q²≤S²` (correct converter model, like batteries/renewables). Log as a decision; don't slip into current scope.

## B. Research investigation: why does cvxopf's case9_dcline solve not match Pypower?

### B.0 Background — the two models and why they differ

`case9_dcline` is Pypower's `t_case9_dcline`: the 9-bus case (buses renamed, so bus 3 → external id **30**) with a **`dcline` table** of 4 DC lines (one out of service). We solve OPF two ways and they disagree:
- **cvxopf** solves it as an AC-OPF where each DC line is a **unity-power-factor real-power injection**: variables `p_in` (from-bus) and `p_out` (to-bus), coupled by a loss law `p_out = −(1−loss_frac)·p_in`, and **no reactive power at the terminals** (Q≡0). cvxopf currently does **not** enforce branch flow limits (Milestone 4 is a stub).
- **Pypower** (`toggle_dcline`, which our `scripts/generate_pypower_fixtures.py::_dcline_to_gens` reproduces) models each DC line as **two dummy generators** — one at the from-bus, one at the to-bus — coupled in real power by `(1−L1)·Pgf + Pgt = −L0`. Each dummy gen also has a **reactive box** `[Qmin,Qmax]` (from the dcline table; here ±10 MVAr) and its terminal bus is set to PV. Pypower **does** enforce branch flow limits.

The committed fixture (branches on, reactive free) gives obj **6446**; cvxopf gives **5490**. The investigation is to explain that gap rigorously, not hand-wave it.

### B.1 The "neutralized Pypower" model — what it is and why

To compare like-for-like, we build a **neutralized** version of the Pypower dcline problem that strips away every Pypower feature cvxopf lacks, so that (ideally) the two become the *same* optimization problem. Three neutralizations, each independently verified safe/equivalent earlier in the session:

1. **Branch limits off** — set all branch `rateA` to a huge value (1e5). Rationale: cvxopf enforces no branch limits (M4 stub), so to match it we remove Pypower's.
2. **Terminal reactive pinned to zero** — set the dummy generators' `Qmin=Qmax=0`. Rationale: cvxopf's terminals are unity-PF (Q≡0); this forces Pypower's terminals to unity PF too.
3. **Terminal buses reverted PV → PQ** — so terminal voltage floats instead of being voltage-regulated. Rationale: match cvxopf's PQ terminals. (Verified this is a **no-op** in OPF mode both here and on case57 — Pypower OPF doesn't hard-pin PV voltage anyway — so it changes nothing, but we do it for cleanliness.)

The neutralized model still uses `_dcline_to_gens` + the real-power coupling userfcn (`_make_coupling_userfcn`), `dclinecost` deleted (DC lines zero-cost), solved with `runopf`. **Its optimum is called P\*** and has obj **6249.87**. Note this is different from both the committed fixture (6446) and the partial-neutralization values — see the ladder below. **P\* (6249.87) is the correct point to compare against cvxopf's C\* (5490)**, because P\* is the closest Pypower analog to cvxopf's model.

Key puzzle that motivated the rigorous test: **cvxopf's C\* (5490) is cheaper than P\* (6249) under the *same* objective.** If cvxopf's model were a strict subset of Pypower's (more constrained: Q≡0 is tighter than Q∈[−10,10]), C\* could not be cheaper. So cvxopf must be *less* constrained somewhere — the prime suspect being the branch-limit stub (confirmed: at C\*, branch 4 carries 135 MVA against a 40 MVA rating). The models differ in *both directions* (cvxopf tighter on reactive, looser on transmission), which is exactly why neither is a subset of the other and why the objectives can cross.

### B.2 The four-way cross-evaluation — goal and design

**Goal:** determine *which* of three explanations is operative — (i) different objective, (ii) different constraint set, or (iii) different local optima of the *same* problem. AC-OPF is nonconvex (sin/cos power flow; cvxopf uses DNLP via IPOPT), so local optima are *possible* — though the user believes AC-OPF is "nearly convex" and multiple optima are unlikely, so (iii) would be a surprising finding requiring strong proof.

The method compares the two optima (C\* from cvxopf, P\* from neutralized Pypower) **at fixed points, without re-solving** — this cleanly separates "different model" from "different basin":

- **EX4** — evaluate C\* under Pypower's objective; **EX5** — evaluate P\* under cvxopf's objective. If the two objective *functions* disagree at a shared point → cost-representation bug. If they agree → objectives are the same, move on.
- **EX6** — is C\* *feasible* in (neutralized) Pypower's constraint set? **EX7** — is P\* feasible in cvxopf's constraint set?
- **EX8 — verdict via truth table:**
  - Objectives disagree → cost bug (not basins).
  - Objectives agree but a point is infeasible in the other's set → **genuine constraint-set difference** (residual location identifies which constraint).
  - Objectives agree **and** both points mutually feasible, yet the solvers returned different optima → **genuine local optima** → then EX9.
- **EX9 (conditional)** — warm-start each solver at the *other's* optimum. If it stays → that point is a valid local optimum for it → basins. If it slides away → that point is infeasible/suboptimal → model difference.

### B.3 Points under study (durable in `results/`)

- **C\*** = `cstar.json`: obj 5490.10, Pg [90, 10, 220.16], p_in [1, 2, 10], p_out [−0.99, −2, −9.5], full Vm/Va stored.
- **P\*** = `ex1_pstar.txt`: obj 6249.8659, Pg [90, 106.14, 123.48], dummy Pg [−1, −10, −10, −0.01, 10, 9.5] → PF [1, 10, 10], PT [0.01, 10, 9.5], Q=0, full Vm/Va stored.

**Objective ladder (all verified):**
- 6446 — committed fixture (branches on, Q free)
- 6213 — branches off, Q free (partial neutralization)
- 6249.87 — **fully neutralized** (branches off, Q=0, PQ terminals) = **P\***
- 5490 — cvxopf **C\***

(Note: zeroing reactive *raised* the objective 6213 → 6249, i.e. removing the reactive resource makes Pypower's problem harder — correct direction. Also: P\* runs link0 (30→4) at PF=1, its minimum — the **same** as C\* (p_in=1) — so the DC dispatches partially coincide, which sharpens the feasibility question.)

### B.4 Verified point-mapping (EX3, round-trip both directions)

The two representations differ (cvxopf: 3 gens + p_in/p_out + internal bus order; Pypower: 3 real + 6 dummy gens + external bus ids). The mapping, round-trip-verified:
- Gens: cvxopf Pg[k] ↔ Pypower real gen[k], buses [1,2,30], direct.
- Buses: internal→ext {0:1, 1:2, 2:30, 3:4, 4:5, 5:6, 6:7, 7:8, 8:9}; Vm/Va direct.
- DC line ↔ dummy gens: `cvxopf p_in[k] = PF[k] = −(pypower from-dummy Pg[k])`; `cvxopf p_out[k] = −PT[k] = −(pypower to-dummy Pg[k])`.
- **Nodal-injection sign (critical, confirmed via cvxopf's own `p` vector):** DC terminals enter the nodal balance as **+p_in / +p_out** (Convention B — both `+`, per `hvdc.py::hvdc_injections`). Verified: bus 5 (link2 from-terminal) net injection = −0.80 pu = (−0.90 load) + (+0.10 DC). **This sign is where reconstruction bugs keep happening — trust cvxopf's `p` variable directly rather than re-deriving.**

### B.5 Four-way test status

- ✅ EX1 (P\*), ✅ EX2 (C\*), ✅ EX3 (mapping round-trip).
- ✅ **EX4/EX5: objectives AGREE.** Both solvers' reported objectives match a direct curve evaluation of the shared cost (gen0 PWL, gen1 `24.035P−403.5`, gen2 PWL; DC zero-cost), rel diff <1e-5. Under this shared objective, C\* costs 5490 and P\* costs 6249 → C\* genuinely ~760 cheaper. **The objective is not the cause.**
- ❌ **EX6 first attempt INVALID** — a hand-built nodal-injection reconstruction used the wrong DC sign (−p_in/−p_out instead of +/+). Residuals came out exactly 2× the DC flows (the sign-flip signature). **Do not trust `ex6_Cstar_in_pypower.txt`.**
- ⏳ **EX6-(B) WRITTEN, NOT YET RUN** — `_ex6b_powerflow.py`. Reconstruction-free: fixes C\*'s dispatch as setpoints in the neutralized Pypower case, runs Pypower's **own** `runpf` from C\*'s voltages, and compares the solved voltages back to C\*. **Interpretation watch-list:** `runpf success = True`? `max|dVm|` and `max|dVa|` ≈ 0? slack gen Pg ≈ 90 (C\*'s slack)? All clean → C\* is a consistent operating point in Pypower's network → **C\* feasible in Pypower → divergence is local optima** (would then need EX9 to confirm, and would be surprising). Any off → genuine network/constraint difference (and which readout is off says what). Caveats: real-gen Q isn't in C\*, so real-gen buses are set PV at C\*'s Vm (Q free) and the slack absorbs real-power imbalance — so this tests *network/power-flow consistency*, not the OPF inequality set.
- ⬜ EX7 (is P\* feasible in cvxopf's constraints?) not started. A cvxopf-side analog: plug P\*'s dispatch/voltages into cvxopf's constraint expressions and measure residuals — or warm-start cvxopf at P\*.
- ⬜ EX8 verdict; ⬜ EX9 (conditional) warm-start basin test.

### B.6 Leading interpretation (provisional, NOT yet proven)

Because C\* overloads branch 4 (135 vs 40 MVA) and P\* respects it, and because the objectives are identical, the most likely verdict is **genuine constraint-set difference, dominated by branch limits** (cvxopf's M4 stub lets it reach a cheaper, transmission-infeasible point), with the terminal reactive box a secondary difference. But EX6/EX7 have not yet confirmed this cleanly — **do not write it up as settled.** In particular, EX6-(B) should show C\* is *network-consistent* in Pypower (power flow fine) but the OPF differs because of the *limit* cvxopf ignores; EX7 should show P\* is feasible-but-suboptimal in cvxopf (cvxopf could do better by overloading br4). If instead both points are mutually feasible and it's truly basins, that changes the story — hence run the experiments before concluding.

### B.7 Immediate next action

Have the user run:
```
uv run experiments/dcline_crosseval/_ex6b_powerflow.py 2>/dev/null; echo exit=$?
```
Interpret per the EX6-(B) watch-list (B.5). Then do EX7, then the EX8 verdict.

### B.8 Memory to update when resolved
memories/case9-dcline-branch-limit-gap.md currently states the root cause is unproven, with candidates (dcline device model vs nonconvex local optima) and branch-limits/PWL ruled out as sole cause. Update it once EX6-(B)/EX7/EX8 settle the question — and keep the certainty calibrated to what's actually proven. (The memory's filename is now a slight misnomer since branch-limits-alone were ruled out; the real story is "multiple model differences + which one dominates," so consider renaming when resolved.)

### B.9 We're doing science right now, not shipping code

As the user tells his students, "The difference between doing science and messing around is writing stuff down." While we are experimenting, we are working in the ./experiments/ folder and we are saving results/outputs to read back from disc. We want a durable record, not temporary skipe files and print statements. 
