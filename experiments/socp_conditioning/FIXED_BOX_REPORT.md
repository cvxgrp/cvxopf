# Exact fixed-box diagnostic

2026-10-02. One owner-authorized fresh-process Case118 surplus solve; no retry,
tolerance adjustment, deficit/AC solve, or production change. Isolated worktree
base `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`; experiment code uncommitted and
hashed before/after execution. Main study checkout and numerical evidence unchanged.

## Intervention

Normalized device cones and the unscaled objective are retained. Replace only
exact coincident real-power generator/renewable lower/upper inequalities with
equalities, retaining all reactive capability and every other constraint.
840 generator and 981 renewable fixed coordinates give 1,821 new equalities and
remove 3,642 inequalities: canonical counts change from 6,822/23,904 to
8,643/20,262. No near-fixed coordinates are collapsed. The historical input
reference exactly matches the retained cones-only comparator.

## Result: mechanism reduced, convergence not fixed

| Measure | Retained cones-only | Fixed boxes as equalities |
|---|---:|---:|
| Native status | AlmostSolved | **InsufficientProgress** |
| Iterations | 44 | 39 |
| Solve wall time | 1.090 s | 0.969 s |
| Original physical objective | 3.122147605 | 3.127404700 |
| Native canonical objective | 3.122153646 | 3.127423767 |
| Absolute native gap | 0.000141387 | 0.000322593 |
| Relative native gap | 4.529e-5 | 1.032e-4 |
| Storage epigraph excess cost | 6.041e-6 | 1.907e-5 |
| Dual multiplier 2-norm | 194,225,087 | 48,285,316 |
| Original-unit stationarity infinity norm | 1.955e-4 | 7.150e-5 |
| Epigraph stationarity / cost coefficient (maximum) | 0.187% | 0.211% |

The intended opposing fixed-bound rows are gone. The dual norm falls about 75%,
and maximum stationarity improves about 2.7-fold. But the dual gap stalls above
even the reduced relative threshold (5e-5). This **does not validate replacing
fixed bounds as a sufficient convergence fix**. It supports their contribution
to multiplier inflation while showing additional numerical difficulty remains.
The remaining dual norm is dominated by a load-shedding lower-bound block
(norm approximately 48,285,246); the objective coefficient norm is approximately
48,285,319. Those are not coincident bounds and are not removed by this change.

The slightly higher total objective is not evidence of worse physical dispatch:
the comparator includes a small negative shedding cost (-0.00534835) from
permitted numerical bound error. The new shedding cost is +0.00003830.
Storage costs are 3.12749339 versus 3.12735831. Neither result is a certified
optimum; do not clip costs or loosen thresholds to create agreement.

## Failed-solve evidence and offline reconstruction

CVXPY raises SolverError for native InsufficientProgress and does not populate
the public primal. The worker retained its exception, native full x/s/z, solver
statistics/settings, canonical matrices/layout, log and normal process exit.
The parent stopped without launching another solve. Peak RSS was 330.25 MiB,
supervised elapsed time 3.13 s, below the unchanged 4 GiB/180 s budgets.

`fixed_box_analysis.py` rebuilds **without solving**, verifies exact equality of
the rebuilt and retained A/P/b/c, then assigns the retained native physical
coordinates by archived name/shape. All 55 original numerical physical/accounting
residual checks pass; maximum active/reactive balance errors are 1.24e-11 and
2.27e-11 MW/MVAr. The overall audit remains false because the solve did not have
an eligible solver status. Offline evaluation is explicitly labeled and does not
promote or rewrite the failed artifact. Epigraph tightening preserves every
other canonical row and recovers the physical objective exactly to roundoff.

## Evidence and checks

Raw run: `results/fixed_boxes_001/`.

- Binding SHA-256: `13dfeb1f9e82bd5b945b03721483e3ce92197f0d9b130c290f8d508e1a516f06`.
- Arm SHA-256: `8fa23bee74b261ed7281ba78c3d37a52b96301b944cd54ae64295c676e3aced0`.
- Summary SHA-256: `ea23fb725631330df0bb38b5a87714c4a9b39de95b0d83be93c54db0f9c418ee`.
- Separate offline analysis SHA-256: `856098f304ff0931b310ba4982e105ed250930446319dcdd06d494e3834fb0fe`.

48 focused solver-free tests passed; Ruff and whitespace checks are clean.
No automatic follow-up experiment is authorized. This is a bounded negative
convergence result, not a production qualification or evidence of AC infeasibility.
