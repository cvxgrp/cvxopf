# Fix DC-line import and repeat the matched nine-bus AC comparison

**Proposed for owner review, 2026-11-18.** Baseline: clean `dcline-fix` at
`fd4d279ff6d5b3f8562dac31f9de3092cff096f7`. This document proposes the
work; it does not authorize implementation, numerical execution, or changes
to historical records.

**CVXOPF allows power to flow either way; the nine-bus case allows only one
direction on each link.** Our “from” and “to” labels name the two ends of an
HVDC line in the AC network. The power bounds determine whether the link can
send power forward, in reverse, or both ways. When the bounds allow both ways,
the current native model treats the link as lossless.

The [case file](../src/cvxopf/testcases/case9_dcline.py) specifies three
in-service links: **30 → 4 (1–10 MW), 7 → 9 (2–10 MW), and 5 → 9 (0–10 MW)**.
It prescribes the allowed direction and range, not a fixed dispatch. The
optimizer chooses how much to send within each range.

**The importer reversed all three directions.** Take the lossless 7 → 9 link.
In [PYPOWER](../scripts/generate_pypower_fixtures.py), bus 7 sends 2–10 MW and
bus 9 receives it. In CVXOPF, positive terminal power means the link supplies
the bus. By copying the positive bounds directly, the
[importer](../src/cvxopf/hvdc.py) made bus 7 *receive* 2–10 MW from bus 9.
The fix is to translate the case bounds at import, keeping CVXOPF's
production/consumption convention unchanged.

**The historical model also turned a loss into a gain.** On the 5% loss link
between buses 5 and 9, the [archived CVXOPF dispatch](../experiments/dcline_crosseval/results/cstar_full.json)
had bus 9 supply **9.5 MW** and bus 5 receive **10 MW**. The modeled line
created **0.5 MW**: it delivered more power than it drew. A physical transfer
in that same reverse direction with 10 MW sent would deliver only 9.5 MW to
bus 5. The old cost comparison therefore cannot tell us whether reversing
the links would save money.

## 1. Why the comparison must be repeated

The two solvers were given different physical problems. Before comparing
dispatch or cost again, we must establish that they enforce the same
constraints and price the same dispatch identically.

The [historical feasibility check](../experiments/dcline_crosseval/_ex12_cplus_solve.py)
reused the CVXOPF injection convention, box, and fixed-loss equation to
declare its own point feasible in PYPOWER. Its 5,469.04 point injected +2 MW
at bus 7, outside PYPOWER's permitted range by at least 4 MW. Separately, the
earlier archived CVXOPF point (`C*`, not the fixed-loss `C+`) had
angle(9) − angle(4) = −3.773°, below the case's −2° limit; PYPOWER's point
sat at −2.000°. Removing a thermal rating does not remove an angle limit.
The later fix (`0c774b70d`) corrected the native link's loss equation, but
not the importer's direction. The [demo](../experiments/dnlp_vs_pypower/demo.py)
bypasses that fix by replacing the native coupling with the historical equation.

**Disposition:** preserve the existing artifacts as historical evidence.
Neither the smooth-cost (~1%) nor mixed piecewise-linear-cost (~14%) HVDC gap
establishes a solver advantage. Withdraw the routing-versus-loss explanation
as well: the [demo's loss calculation](../experiments/dnlp_vs_pypower/demo.py)
counted the 5–9 link's 0.5 MW **gain** as a 0.5 MW **loss**. The standard
no-HVDC controls are unaffected by these defects. Do not treat the cost of the
full, unmodified PYPOWER fixture as a reference for a comparison with
different limits or converter controls.

## 2. Fix the boundary, not CVXOPF's sign convention

**Keep the native contract:** `p_hvdc_in` and `p_hvdc_out` are the link's
signed real-power injections into the grid at the **from** and **to** buses,
respectively (MW). Positive supplies the bus; negative consumes power at the
bus. Both enter nodal balance with a plus sign. Native `HVDCLink` bounds
and time-series overrides are already in these coordinates. Do not change
them, change the incidence matrices, swap the buses, or flip results on export
from the solver. Preserve native passivity: reported loss is minus the sum of
the two injections.

**Translate the external record once, at import.** In PYPOWER's `dcline`
table, `PF` and `PT` are signed *along the declared from-to direction*.
The link and converters together withdraw `PF` from the from bus and supply
`PT` to the to bus, net of all losses. The reference law in MW is

    PT = (1 − LOSS1) PF − LOSS0
    total physical loss = PF − PT.

The native CVXOPF link models proportional loss only. Its from injection is
`p_in = −PF`; its to injection `p_out` is **gross** of any fixed converter
withdrawal. With zero fixed loss, `p_out = PT`. With a separate fixed
withdrawal in the matched study, `PT = p_out − LOSS0`.

For a forward-only row, the from-injection box is therefore
`[−PMAX, −PMIN]`. For example, sending 10 MW with 5% proportional loss
means **bus 5 supplies 10 MW and bus 9 receives 9.5 MW**:
external `(PF, PT) = (10, 9.5)`; native `(p_in, p_out) = (−10, +9.5)`.
`PF` is not a binding OPF schedule; derive delivery from the loss law rather
than treating a stale input `PT` as a constraint.

| Active case row (external IDs) | External limits (MW) | Native from-injection box (MW) | Hand check for the matched study |
|---|---:|---:|---|
| 30 → 4 | [1, 10] | [−10, −1] | At 10 sent, 8.9 received, 1.1 lost including fixed loss. At 1 sent, the to bus also supplies 0.01 MW to cover converter losses. |
| 7 → 9 | [2, 10] | [−10, −2] | At 2 sent, injections at buses 7 and 9 are −2 and +2. |
| 5 → 9 | [0, 10] | [−10, 0] | At 10 sent, injections are −10 and +9.5. |

The 5 → 8 row is out of service. The table gives physical case limits;
PYPOWER's separate dummy-generator adjustment near zero is addressed below.

**Other rows must have a defined outcome.** Do not apply the forward-box
shortcut blindly. In [PYPOWER's source](https://github.com/rwl/PYPOWER/blob/master/pypower/toggle_dcline.py),
nonnegative limits constrain the from end, and negative limits constrain the
to end. Map each endpoint inequality through the actual terminal law. For a
lossless line they reduce to the negated, reversed box even for reverse or
two-way flow. For reverse or zero-straddling boxes with nonzero losses, the
external signed affine law and our native passive law need not agree.
**Proposed public policy:** accept the physically representable cases; reject
these non-equivalent reverse/two-way imports with the row, bus IDs and reason,
rather than silently reversing direction, creating power, or removing losses.
Do not restrict directly constructed native links. Retain the documented
forward-row MVP approximation (warn when dropping `LOSS0`); explicitly
document unity power factor and discarded converter voltage/reactive controls.
For bus 30 sending 10 MW, that approximate public import delivers **9.9 MW**,
not the source case's **8.9 MW**. Make this distinction visible to the caller;
when nonzero reactive capability or requested converter control is discarded,
identify the affected rows and omitted feature. The benchmark below restores
the fixed loss and explicitly matches converter controls. Full converter
modeling remains [separate work](milestone-15-full-lossy-hvdc.md).

For every supplied `dclinecost` row paired with an active link, require
`MODEL == 2` **before** interpreting `NCOST` or reading coefficients.
Otherwise raise `ValueError` identifying the original input row and its
`MODEL`. Test with a valid two-point PWL row (`MODEL=1, NCOST=2`) after an
inactive link: it must be rejected at its original index, not read as a linear
polynomial. No PWL link-cost support is proposed. For supported polynomials,
check the native magnitude cost against the *signed* external `PF` cost over
the allowed interval or reject the row. Test constant, linear and quadratic
terms at nonzero transfer. The benchmark uses **zero link cost on both sides**.

**Importer acceptance (no solver needed).** In `tests/test_hvdc.py`, replace
the positive-maximum assertion with literal expected endpoint injections,
limits and bus IDs from the table. Check zero, pinned and interval bounds,
inactive rows, lossless reverse/two-way imports, unsupported combinations,
and the fixed-loss warning. Exercise actual builder balances at assigned
values, including two links sharing bus 9, and keep native forward/reverse
and multistep regressions. A deliberately unflipped import must fail the
lossless 7 → 9 test. Internal balance alone and import/export round trips are
not oracles for physical direction.

**Documentation is a deliverable of the fix.** Put the sign key, worked 10 MW
example, bounds translation, units, and supported/approximate/rejected
features together in `docs/modeling.md` and the importer docstring. Update
the case9 examples, misleading test comments, and live developer guidance to
use “from-bus injection” versus “power sent”; label printed results the same
way. Explain the behavior change for callers who previously negated inputs
by hand. Cross-reference rather than rewriting historical milestone records.

## 3. Freeze one matched experiment before any solve

Keep the new protocol, scripts, and compact evidence in a separately named
version under `experiments/dnlp_vs_pypower/`; give it a fresh, locally
ignored raw-results directory. Do not overwrite either existing reference
JSON, the old cross-evaluation, or the standard CI fixtures.

Proposed design: derive all four cells from **one** canonical
`t_case9_dcline` base. Toggle only (i) the three active links versus none
and (ii) the case9 smooth quadratic costs versus the original mixed PWL/linear
generator costs. The old no-link controls used different case files, including
a different minimum output at bus 1. Keep them as external regression checks;
do not use them as the no-link cells of a causal two-by-two comparison.
This is a named, matched variant, not an unmodified stock-case solve.

| Item | Proposed common specification and pre-solve proof |
|---|---|
| Network and devices | Compare the actual case tables and derived constraints by external bus ID, original branch row, generator and link row, including bus 30, parallel devices at a bus, base MVA, shunts, taps, shifts, demands, reference angle, voltage boxes, real/reactive generator bounds and any remaining active rows. Compare admittance matrices in the same bus order; that check alone says nothing about device signs. |
| Branch thermal limits | No thermal rating may restrict the feasible set. Disable the limits in CVXOPF; in PYPOWER, use the provably redundant per-branch MVA ratings below with apparent-power limits (`OPF_FLOW_LIM=0`). These extra numerical rows are part of the PYPOWER representation and must be disclosed in any performance comparison. |
| Branch angle differences | Keep the two complete [PYPOWER angle rows](https://github.com/rwl/PYPOWER/blob/master/pypower/makeAang.py) on **unwrapped radians**: `−2π ≤ θ₁ − θ₄ ≤ 2.48π/180` and `−2π/180 ≤ θ₉ − θ₄ ≤ 2π` (external bus IDs). Once a row is selected, its opposite ±360° endpoint is finite too. Add study-local CVXOPF constraints, leaving production defaults unchanged. Before solving, align angle columns by bus ID and compare both assemblies to these two expected rows: +1 at from, −1 at to, zero elsewhere, **both** lower and upper bounds. Dropping either opposite endpoint must fail the check. |
| Generator capability | The source defines sloped upper and lower P–Q limits for generators at buses 2 and 30. Enumerate the active [PYPOWER capability rows](https://github.com/rwl/PYPOWER/blob/master/pypower/makeApq.py) as built; impose equivalent study-local rows in CVXOPF using the same MW/MVAr units. Check limits at both defining points and a violating point. Separate P and Q boxes are insufficient. |
| Converter terms | Same terminal IDs, effective bounds, no link cost, zero terminal Q and no converter voltage control on either side. Preserve any coincident real generator's own Q capability. Match the post-transform effective zero-endpoint bound (the fixture adapter applies a `−1e−6` MW from-dummy maximum on the 5 → 9 link); identify it separately from the user-facing case limit. |
| Fixed loss | Keep the production HVDC proportional-loss equation intact on **all three links**. On the CVXOPF side only, add one fixed, non-sheddable 1 MW real-power withdrawal (0 MVAr) at bus 4 when 30 → 4 is in service. It is absent from no-link cells. PYPOWER already includes this loss in its coupling: add no load there. Attribute the withdrawal to its converter, not to ordinary customer demand. |
| Cost and time | Same real-generator rows, row order and one-hour interval. Evaluate each polynomial and each PWL segment/kink in MW, including constants, endpoints and epigraph projection. Verify the summed cost on identical dispatches before comparing costs at different dispatches. |

**Pre-solve load check.** [Explicit loads replace the entire MATPOWER load fleet](../docs/modeling.md#first-class-loads-and-explicit-load-shedding).
For CVXOPF link-enabled cells, pass `loads=[*loads_from_matpower(case["bus"]), converter]`, never `loads=[converter]` alone.
Before solving each cell, check assembled ordinary demand by external bus: 90 MW / 30 MVAr at bus 5,
100 MW / 35 MVAr at bus 7, and 125 MW / 50 MVAr at bus 9, zero elsewhere.
That totals **315 MW / 115 MVAr in every cell**. Only CVXOPF link-enabled cells have the separate
**1 MW / 0 MVAr at bus 4**, bringing their total explicit demand to 316 MW / 115 MVAr.
PYPOWER accounts for that withdrawal in the link equation, not as an extra load.

**Proof that the thermal ratings are redundant.** For an in-service branch
with terminals $f,t$, let $u_f,u_t$ be their voltage-magnitude upper bounds
in p.u. and $B$ the case base MVA. Use the branch's actual terminal admittances
(including tap, phase shift and line charging). Since
$I_f=Y_{ff}V_f+Y_{ft}V_t$ and the terminal power is $B V_f\overline{I_f}$,
the triangle inequality bounds the apparent power at *every* allowed voltage.
The two bounds, in MVA, are

$$
U_f=B u_f (|Y_{ff}|u_f+|Y_{ft}|u_t),\qquad
U_t=B u_t (|Y_{tf}|u_f+|Y_{tt}|u_t).
$$

Set each in-service branch's study-only `RATE_A` to the larger of its two
bounds rounded up to whole MVA, plus 1 MVA. Retain a table by source branch
row of both bounds, the rating, and its margin. Cross-check with PYPOWER's
assembled `Yf` and `Yt`; require finite inputs and
`max(U_f, U_t) < RATE_A < 1e10` for every branch so the bound is strict and
the pinned solver includes the row. Observed flows are not a redundancy proof.

**Pre-solve callback gate (all four cells, no optimizer iterations).** With
all `RATE_A=0`, the pinned [constraint callback](https://github.com/rwl/PYPOWER/blob/master/pypower/opf_consfcn.py)
returns a (0,1) inequality array incompatible with [PIPS assembly](https://github.com/rwl/PYPOWER/blob/master/pypower/pips.py);
the [Hessian callback](https://github.com/rwl/PYPOWER/blob/master/pypower/opf_hessfcn.py)
also fails on empty branch indices. Use the finite-rating path above, not a
patch to PYPOWER. For each assembled model in the pinned environment:

1. At the starting point, call `opf_consfcn` with PIPS's actual constrained
   branch indices and corresponding `Yf`/`Yt` rows. Require a finite 1-D
   inequality vector with two entries per in-service branch, the expected
   Jacobian dimensions and finite entries. Exercise PIPS's concatenation with
   the actual linear inequalities and its Jacobian stacking without reshaping
   the callback return.
2. Independently call `opf_hessfcn` at the same point with the same branch
   slice, correctly sized nonzero equality/inequality multipliers and a
   nonzero objective weight. Require a finite square Hessian of the model's
   decision dimension. Record both gate outcomes; either failure blocks runs.

Use the project environment for CVXOPF and a separate environment pinned to
PYPOWER 5.1.19, NumPy 2.2.6 and SciPy 1.18.0 for PIPS. The existing
dummy-generator adapter works around upstream indexing issues; validate its
**assembled** generator bounds, bus
assignments, coupling columns/RHS and zero-cost rows against external row
identities and the pinned source. Do not take matrix column order or output
keys named `p_hvdc_in` as proof of physical meaning. Inventory both builds'
active constraint families and objective terms. Every row must have a
counterpart, be an equivalent auxiliary representation, or be demonstrably
redundant. An unexplained difference stops the study.

## 4. Qualify the cross-check before trusting a new answer

1. Define an interchange record in engineering units: external IDs; voltage
   magnitudes and angles; real-generator P and Q; units and reference angle.
   For each link keep the native signed injections, the fixed converter
   withdrawal, the two **net** bus injections, and external PF/PT as distinct
   fields. Keep both native solutions. Reconstruct target lifts/epigraphs
   from physical coordinates rather than copying solver auxiliaries.
2. Check each dispatch in its source model and in the **other implementation's
   actual built model**, without re-solving or letting slack generation move.
   CVXOPF → PYPOWER: set from/to dummy Pg to `(p_in, p_out − LOSS0)`.
   PYPOWER → CVXOPF: assign native `(p_in, p_out) = (−PF, PT + LOSS0)`;
   leave the fixed withdrawal already in the CVXOPF build in place. Evaluate
   the target's boxes, linear/nonlinear rows and cost. Retain the worst signed
   residual, units and offending ID for each constraint family.

   Audit **each link before summing losses**. From the external case row and
   net terminal readings, check that MW drawn minus MW delivered (`PF − PT`
   on these forward links) agrees with both `LOSS1 × PF + LOSS0` and the
   native proportional loss `−(p_in + p_out)` plus the fixed withdrawal.
   Require nonnegative physical loss within the frozen MW tolerance. Check
   the named bus balances independently: use either gross delivery minus the
   withdrawal or net delivery, never both net delivery and a second withdrawal.
   Before any solve, verify both mappings on 30 → 4 at 10 MW sent (9.9 MW
   gross, 8.9 MW net, 1.1 MW lost) and at 1 MW sent (0.99 MW gross,
   **−0.01 MW net**, 1.01 MW lost).
3. Prove the checks can fail. Feed them reversed terminal signs on 7 → 9,
   a reversed or double-counted converter withdrawal, a violated 9 → 4 angle
   floor, a violated generator P–Q face, and a changed cost coefficient. Each must fail for
   the intended reason. For the archived 5–9 link, the loss check alone must
   reject 9.5 MW drawn and 10 MW delivered (physical loss = −0.5 MW),
   independently of the direction check. A report that calls the archived
   gain a positive loss must also fail. Replay archived coordinates where
   retained: `P*` is a positive control for its historical PYPOWER target; `C*` under the
   historical forward-flow reading produces a **23 MW** bus-9 discrepancy
   and fails the angle floor. The retained `C+` from-injection alone disproves
   its PYPOWER box claim; do not fabricate a missing full-precision vector.
4. Freeze tolerances, solver settings, initialization rules, identities,
   expected active rows and scripts in a reviewed protocol **before** new
   numerical output. Proposed physical gates: max real/reactive residual or
   box violation ≤ 0.001 MW/MVAr; voltage ≤ 1e−6 p.u.; angle ≤ 1e−5 degrees;
   same-point objective/component agreement ≤ `1e−4 + 1e−6 × |cost|` in
   objective units. Exact IDs, signs and effective model coefficients are
   structural checks, not subject to those solution tolerances. Freeze a
   separate material cost-difference threshold for comparing *different*
   solutions. No post-hoc tolerance change to earn a pass.

**Hold point:** require independent review of the physical contract, both
assembled models (including the rating proof and complete angle rows),
passing results from both callback checks in every cell, the negative controls
and the protocol. Owner approval is then required for execution. Unresolved
parity or a failed callback stops the study before any benchmark solve.

## 5. Rerun, interpret, and retire the unsupported claim

- Run the four frozen cells once per solver (eight primary solves), with
  explicit options, captured starts/effective initialization where observable,
  native statuses and tolerances. Use `build.solve()` for CVXOPF. No hidden
  retries or cross-starts. Retain the clean commit, versions, input/settings
  hashes, native primals, objectives, component costs, residuals and logs in
  a new run directory. A failure remains a result. Additional diagnostics
  require a separate decision. Testing deliberately reversed link directions
  is a different scientific question and is not part of these eight solves.
- Report the four source/target feasibility evaluations per cell and both
  objective evaluations at each accepted point, then compare costs, dispatch,
  physical line directions and losses. If parity, cross-feasibility or any
  per-link loss audit fails, report the offending row and residual: **no solver
  ranking, aggregate loss breakdown, or routing explanation**. If all pass
  and costs agree within the declared threshold, say so even if routing
  differs. A materially cheaper cross-feasible point supports a better
  observed feasible value to the stated tolerances; it does not prove global
  optimality or isolate DNLP canonicalization from solver and initialization.
- At the first approved implementation checkpoint, label the old comparison
  README/report and demo verdict as superseded, without deleting raw evidence.
  Inventory downstream claims (including presentations, developer notes and
  manuscript mentions) for owner disposition; do not silently rewrite papers.
  Publish a separately named corrected report and reproduction instructions
  after review, whether the old gap vanishes, survives, or remains unresolved.
- Close only after importer regressions, cross-check tests and the repository
  suite pass, and an independent reader can reproduce the signed transfers
  and paired verdicts from the retained inputs and outputs. Full M15 converter
  support, broad AC formulation changes, and other studies are outside scope.

## 6. Historical claim inventory

The importer checkpoint marks the old comparison README, report and demo,
and the EX12 report and handoff, as superseded. It leaves reference inputs,
outputs and solver artifacts untouched. Other tracked sources carrying the
old interpretation, for owner disposition:

- [Milestone 7](milestone-7-hvdc.md) (historical closeout, Gate 6b and capstone)
  and the [full lossy HVDC plan](milestone-15-full-lossy-hvdc.md) (historical
  motivation).
- [Repository research notes](../memories/MEMORY.md), especially the
  [case9 gap](../memories/case9-dcline-optima-gap.md),
  [DNLP tractability](../memories/dnlp-canonicalization-tractability-thesis.md)
  and [HVDC status](../memories/milestone-7-hvdc-status.md) entries.
- The remaining [cross-evaluation probes](../experiments/dcline_crosseval/)
  describe the earlier models and must not be read as a matched rerun.

No other manuscript or presentation carrying the specific headline claim was
identified in the tracked source search. Copies distributed outside the
repository were not checked and need separate owner disposition.
