# Fix DC-line import and repeat the matched nine-bus AC comparison

**Proposed for owner review, 2026-11-18.** Baseline: clean `dcline-fix` at
`fd4d279ff6d5b3f8562dac31f9de3092cff096f7`. This document proposes the
work; it does not authorize implementation, numerical execution, or changes
to historical records.

**The scientific question is conditional:** when CVXOPF/IPOPT and PYPOWER/PIPS
solve the *same physical and economic problem*, do they return comparably
feasible dispatches and costs? Establishing the premise is the first result.
A different numerical answer is neither required nor, by itself, evidence
about the solver. Here “DC line” means a controllable HVDC device in an **AC**
network, not a DC approximation to the network.

## 1. What failed, and what remains valid

In the [case definition](../src/cvxopf/testcases/case9_dcline.py), the lossless
link from bus 7 to bus 9 must send 2–10 MW. The [PYPOWER fixture
builder](../scripts/generate_pypower_fixtures.py) makes its from-terminal
generator withdraw 2–10 MW at bus 7. The [CVXOPF importer](../src/cvxopf/hvdc.py)
copied those same positive bounds onto the *injection* at bus 7. The two
physical ranges are disjoint. Even the sign of an endpoint with **no loss at
all** was different.

The [historical feasibility check](../experiments/dcline_crosseval/_ex12_cplus_solve.py)
reused the CVXOPF injection convention, box, and fixed-loss equation to
declare its own point feasible in PYPOWER. Its 5,469.04 point injected +2 MW
at bus 7, outside PYPOWER's permitted range by at least 4 MW. Separately, the
earlier archived CVXOPF point (`C*`, not the fixed-loss `C+`) had
angle(9) − angle(4) = −3.773°, below the case's −2° limit; PYPOWER's point
sat at −2.000°. Removing a thermal rating does not remove an angle limit.
The later native passive-loss correction (`0c774b70d`) did not repair the
importer, and the [demo](../experiments/dnlp_vs_pypower/demo.py) replaces the
native coupling with the historical experimental equation.

**Disposition:** preserve the existing artifacts as historical evidence.
Neither the smooth-cost (~1%) nor mixed piecewise-linear-cost (~14%) HVDC gap
establishes a solver advantage. The standard no-HVDC controls are unaffected
by this defect. Do not treat the cost of the full, unmodified PYPOWER fixture
as a reference for a comparison with different limits or converter controls.

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
In MW the reference law and coordinate map are

    PT = (1 − LOSS1) PF − LOSS0
    p_in = −PF,    p_out = PT,    loss = PF − PT = −(p_in + p_out).

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
| Fixed loss | Preserve the source's 1 MW fixed loss on 30 → 4. In the CVXOPF **experiment only**, replace its proportional equality with `p_out = −(1−LOSS1) p_in − LOSS0`, in MW, exactly once. Check the two endpoint examples above. No double counting, no offset in p.u. on an MW variable, and no change to the public warning or production physics. |
| Cost and time | Same real-generator rows, row order and one-hour interval. Evaluate each polynomial and each PWL segment/kink in MW, including constants, endpoints and epigraph projection. Verify the summed cost on identical dispatches before comparing costs at different dispatches. |

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
   magnitudes and angles; real-generator P and Q; **both** signed terminal
   injections and external PF/PT; units and reference angle. Keep the two
   native solutions as well. Project physical coordinates, not solver
   auxiliaries; reconstruct required target lifts/epigraphs from them.
2. Implement two independent target evaluators. Given a CVXOPF point, inject
   it into **PYPOWER's actual built model** (from/to dummy Pg are `p_in` and
   `p_out`, not their negatives); evaluate native boxes, linear/nonlinear
   rows and cost without optimizing or allowing slack generation to move.
   Conversely evaluate the mapped PYPOWER point on the **CVXOPF model's own
   constraints and objective**. Also check each point in its source model.
   Confirm the named bus injections and physical loss from raw external rows,
   independently of the importer, with a hand-calculated oracle. Retain the
   worst signed residual, units, and offending ID for each constraint family.
3. Prove the checks can fail. Feed them reversed terminal signs on 7 → 9,
   a reversed fixed-loss offset, a violated 9 → 4 angle floor, a violated
   generator P–Q face, and a changed cost coefficient. Each must fail for
   the intended reason. Replay archived coordinates where retained: `P*`
   is a positive control for its historical PYPOWER target; `C*` under the
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
  require a separate decision.
- Report the four source/target feasibility evaluations per cell and both
  objective evaluations at each accepted point, then compare costs, dispatch,
  physical line directions and losses. If parity or cross-feasibility fails,
  report the offending constraint and **no solver ranking**. If both pass
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
