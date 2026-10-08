# Joint scaling with exact fixed-bound cleanup

## Outcome

Exactly three new, serial Case118 24-hour CLARABEL solves were run. The deficit
case now reaches native `Solved` and passes the unchanged experiment gate.
Both surplus cases remain `AlmostSolved`, although all three physical audits
pass. This is a useful but nonuniform improvement, not a complete fix or a
qualification of other solvers.

All arms retain normalized device cones and the previously frozen five-pass
joint P/A/b/c scaling rule. Only exactly coincident real-power bounds on Pg
and p_nd were replaced by equalities. Costs, physical inputs, solver settings,
and acceptance tolerances were unchanged. In particular, absolute gap,
relative gap, and feasibility tolerances remain 1e-10.

## Matched evidence

Comparators are the corresponding previously retained joint-scaled arms,
without fixed-bound cleanup. Relative gaps are native solver statistics;
they are not physical residuals or condition numbers.

| Case | Before | After | Relative gap before | Relative gap after | New iterations | Native solve time |
|---|---|---|---:|---:|---:|---:|
| Surplus, shedding | AlmostSolved | AlmostSolved | 4.230e-9 | 4.573e-7 | 33 | 0.758 s |
| Surplus, fixed load | AlmostSolved | AlmostSolved | 9.622e-8 | 4.453e-8 | 31 | 0.712 s |
| Deficit, shedding | AlmostSolved | **Solved** | 3.792e-10 | **7.238e-11** | 38 | 0.894 s |

The absolute gaps after cleanup are 1.429e-6, 1.392e-7, and 1.115e-4,
respectively. The deficit passes through the relative-gap criterion: its
objective is approximately 1,540,766.73, whereas the surplus objective is
approximately 3.125. Both surplus cases still fail the requested gap gate,
not feasibility. No status was promoted from `AlmostSolved` to `Solved`.

Original-coordinate stationarity infinity norms improve in all three cases:
4.014e-5 to 2.636e-7; 2.970e-11 to 1.271e-11; and 1.340e-4 to 2.567e-5.
These dimensionful residuals are reported separately from the solver's
normalized stopping criteria. The fixed-load surplus dual-vector norm drops
from 2.287e6 to 8.220e4, consistent with removing its dominating coincident
bound pairs. Its gap nevertheless remains above the requested threshold.

All independent physical audits pass. The largest residual/tolerance ratio
across the three cases is 0.000946, under 0.095% of its allowed tolerance.
Scaling costs approximately 0.17–0.18 s per arm. Peak supervised RSS is
489.8–520.8 MiB. These single observations do not establish a runtime speedup.

## Mathematical and provenance checks

Before optimization, exact canonical row-multiset checks proved the only
change was replacing each selected opposite bound pair with one equality:
1,821 coordinates for either surplus case and 2,437 for deficit. Canonical
variable order, linear/quadratic objective coefficients, objective offset,
all other equality/inequality rows, and SOC coefficients/RHS were preserved.
Near-coincident bounds are deliberately not collapsed.

The offline analyzer verifies each immutable worker/completion/supervision
chain, historical comparator hash, physical input identity, canonical cleanup,
unchanged scaling-rule output, transformed matrices, and the x/s/z mappings
back into original coordinates. No coincident Pg/p_nd inequality pairs remain.
Execution and analysis source identities are recorded separately. Everything
remains in the authorized isolated worktree; production code is unchanged.

## Post-processing correction

All three workers completed and retained their results. The original parent
then stopped before publishing `summary.json`: its ordinary-precision
gap-identity reconstruction differed from the deficit native gap by 1.802e-6,
exceeding the existing 1e-6 absolute comparison tolerance. Raw artifacts were
not rewritten, and no solve was repeated.

The offline audit now evaluates the cancellation-sensitive algebraic identity
with 40-digit Decimal arithmetic on the exact retained binary floats and also
retains the original float64 calculation. NumPy longdouble was checked and
has only 53 mantissa bits on this host; it is not used as the final precision fix.
The deficit discrepancy becomes 2.039e-7, within the **unchanged** comparison
threshold; strict default checks pass for all three arms. This is an analysis
arithmetic correction, not a relaxation of solver or physical acceptance.
The saved `verified_analysis.json` explicitly records the original parent
post-processing failure and is nonpromotional. It supersedes the retained
intermediate `offline_analysis.json`, which used platform longdouble arithmetic.

Raw root: `results/joint_fixed_boxes_001/`.
Offline record SHA-256:
`5cf21433785f839f58c2d3a3bbfa1fba72da9df4eaeff1cce5fbbc51ce1cde5b`.
Runner: `joint_fixed_boxes.py`; offline analysis: `joint_fixed_box_analysis.py`.

## Interpretation

Cleanup removes a real representation degeneracy and, combined with scaling,
is sufficient for the tested deficit case. It does not uniformly improve the
gap: the surplus-with-shedding gap worsens by about 108 times while remaining
economically tiny. Physical solution quality and full native convergence are
distinct questions. These are SOCP relaxation results, not recovered AC
feasibility certificates. Further experiments or production adoption require
a separate decision.
