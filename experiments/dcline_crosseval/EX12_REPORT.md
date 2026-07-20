# EX12 — the QED: P\* is suboptimal for Pypower's own problem

**Date:** 2026-07-20
**Script:** `_ex12_cplus_solve.py` → `results/ex12_cplus.txt`
**Probes:** `_ex12_probe_locate.py` (constraint locator), `_ex12_probe_cost.py`
and `_ex12_probe_c4.py` (readout validation)
**Status:** SOLID. QED fires.

## Result

Re-solving cvxopf's own neutralized AC-OPF for `case9_dcline` with link0's loss
coupling **replaced** by Pypower's *with-loss0* law
(`p_out[0] = -(1-L1[0])·p_in[0] + L0[0]`, links 1/2 unchanged) yields **C+**:

- **Fully feasible in neutralized Pypower's own problem** (branch limits
  eliminated), all residuals at machine precision:
  C1 nodal real 1.3e-13, C2 nodal reactive 6.9e-13, C3 gen-P −7e-10,
  C4 gen-Q −282 (interior; see below), C5 voltage −1.2e-11, C6 DC-box −3e-9,
  **C7 with-loss0 coupling = [0, 0, 0] on every link** (the EX6 exception is
  gone).
- **Cheaper than P\*:** C+ objective (Pypower cost model) = **5469.04** vs
  P\* = 6249.87 → **margin 780.83**.

**⇒ P\* is NOT optimal for Pypower's own (neutralized) problem. Pypower
returned a suboptimal local point. Q.E.D.**

The 780 gap is a genuine local-optimum / DNLP-tractability effect, **not** a
model difference — consistent with, and now confirming, the reframing in
[[dnlp-canonicalization-tractability-thesis]].

## Why this succeeds where EX11 failed

EX11 tried to **construct** C+ by a static nudge of `p_out[0]` plus a hand
rebalance of 1 MW on gen2. It failed: loss0 is real power consumed **at the
converter to-bus** and couples through the AC power flow, so a static rebalance
on a gen at a different bus left a **1 MW nodal residual** (EX11 C1 = 1.0 MW at
bus 3). EX12 does not construct — it **solves**, letting IPOPT redistribute the
1 MW through the real network flow. C1 drops from EX11's 1.0 MW to **1.3e-13**.

## Hypothesis confirmed: mild shift, NOT a slide to P\*

The pre-registered hypothesis (user, 2026-07-20) was that imposing loss0 would
shift dispatch only *mildly* and keep C+ in the **C\* basin**, not push it to
P\*. Confirmed:

| point | Pg0 | Pg1 | Pg2 | p_in0 | p_in1 | p_in2 |
|---|---|---|---|---|---|---|
| C\*  | 90.000 | 10.000 | 220.163 | 1 | 2 (box min) | 10 |
| C+   | 90.000 | 10.000 | 219.109 | 1 | 2 | 10 |
| P\*  | 90.000 | 106.140 | 123.480 | 1 | 10 (box max) | 10 |

- `‖Pg(C+) − Pg(C\*)‖ = 1.05 MW` (mild — roughly the loss0 worth on gen2)
- `‖Pg(C+) − Pg(P\*)‖ = 135.60 MW` (nowhere near P\*)
- link1 held at **box-min 2** (C\*'s value), did not swing to P\*'s box-max 10.

C+ is C\* with loss0 absorbed locally, exactly as hypothesised.

## DCP discipline

The assembled AC-OPF is nonconvex by design (sin/cos power flow, DNLP/IPOPT,
`nlp=True` bypasses the DCP check) — `is_dcp()` on the whole problem is `False`
and that is expected. What EX12 certifies is the **one grafted term**: each of
the three replacement loss-coupling equalities was constructed from atoms and
verified in isolation before grafting — all three `is_dcp()=True` with both
sides `AFFINE`. Construct-and-check, not reason-and-ship. No `src/` changes;
the graft reassigns `build.prob` and solves via `build.solve()` (IPOPT,
`nlp=True`), never `build.prob.solve()` directly.

## Two gotchas caught along the way (both are traps that bit twice)

### 1. Mixed cost-model trap — reproduced the EX11 cost bug on first pass
`case9_dcline` gencost **mixes** MODEL=1 (piecewise-linear) and MODEL=2
(polynomial) rows: row0 MODEL=1 (4 breakpoints), row1 MODEL=2 (linear), row2
MODEL=1 (3 breakpoints). The first EX12 cost readout blindly read cols
`4:4+NCOST` as polynomial coeffs — for the MODEL=1 rows those cols are `(x,f)`
**breakpoint pairs**, not coefficients — producing the spurious **11536.85**
(the *exact* number the memory flags as EX11's bug). Fix: dispatch on MODEL,
`np.interp` the PWL breakpoints, `np.polyval` the polynomial rows.
**In-band guard:** the script now re-evaluates C\*'s own dispatch through the
same `_gencost` and asserts it reproduces C\*'s known objective 5490.10 (it
gives 5490.1038) — this guard is what makes the C+ objective trustworthy. See
`_ex12_probe_cost.py`.

### 2. Constraint-locator trap — deleted nodal balance, not the coupling
The HVDC loss coupling is a **single vectorized `(3,)` equality**
`p_out == multiply(coeff_vec, p_in)` (`hvdc.py:268`). The first locator matched
"equality that involves both p_in and p_out" and took the **first** match —
which is the **nodal balance** `p == ... Ch_from@p_in + Ch_to@p_out ...`
(`build.prob.constraints[57]`, shape (9,)), NOT the coupling
(`constraints[61]`, shape (3,)). Deleting nodal balance made the problem
**locally infeasible** (IPOPT: "Converged to a point of local infeasibility").
Fix: match on the coupling being the equality whose **only** variables are
p_in and p_out (`ids == {p_in.id, p_out.id}`). See `_ex12_probe_locate.py`.

### 3. C4 = −282 is benign (not a bug)
Tracked down via `_ex12_probe_c4.py`: `case9_dcline` reactive bounds are
**±300 MVAr** per gen (`d["Qgmin/max"]` = ±3.0 p.u. × baseMVA 100). C+ Qg =
[17.59, 5.79, −4.93] sits far inside, so the worst one-sided slack is
`17.59 − 300 = −282.4` — negative = interior = no violation. Bound lengths
match the extracted Qg (3 and 3), ruling out a dummy-gen misalignment. C4 is
correct; the magnitude just reflects very loose reactive limits.

## Consequence for the investigation

This closes the case9_dcline optima-gap investigation. Combined with:
- EX6/EX7b (C\* and P\* mutually feasible except the loss0 term),
- EX9 (cvxopf holds C\* and descends P\*→C\*),

EX12 adds the missing piece from Pypower's side: a point in the **C\* basin**
that is fully feasible in Pypower's own problem and **cheaper than P\***.
Pypower's P\* is therefore a suboptimal local minimum, not a legitimate
alternate optimum of a different model. The DNLP-tractability reading is the
live explanation.
