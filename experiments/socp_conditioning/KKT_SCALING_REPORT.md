# Scaling the saved CLARABEL KKT system

Five-pass symmetric scaling greatly reduced the estimated condition number
and made the saved affine-predictor solve meet its residual tolerance. It did
not fix the constant-RHS solve. Ten passes were not consistently better.
This was a linear-system diagnostic with **zero optimization calls**, not a
successful full optimization solve. Final report completed 2026-10-06.

## Question and method

Use the same three failing linear systems captured in the
[independent factorization study](KKT_FACTORIZATION_REPORT.md). Their shared
symmetric KKT matrix has dimension 97,794 and 494,142 nonzeros after expansion
of triangular storage. Test 0, 5, and 10 symmetric infinity-norm Ruiz passes
on both the original matrix K and K plus its recorded signed static shift E.

For target M, factor D M D, solve for y with RHS D rhs, then map x = D y.
Each pass clips its positive factor to [1e-8,1e8]; cumulative D is clipped to
[1e-16,1e16]. All arms use SuperLU, COLAMD ordering, diagonal pivot threshold
1.0, and `Equil=True`. Up to five mapped residual corrections are assessed
against original unregularized K. Final residuals are evaluated using 80-digit
arithmetic on the exact retained binary64 matrix and vectors.

The zero-pass arms reproduce the prior independent LU vectors bit for bit.
Neither this study nor its residual checks feed directions back into CLARABEL.
QDLDL dynamic pivot regularization is not reproduced by SuperLU.

## Results in original coordinates

Residual columns are the infinity norm of rhs minus K x, after refinement.
Condition numbers are descriptive one-norm estimates using the computed LU
inverse action, not certified bounds.

| Factored target | Passes | Condition estimate | Constant RHS residual | Affine residual | Combined residual |
| --- | ---: | ---: | ---: | ---: | ---: |
| K | 0 | 9.16e35 | 1.4386e-2 | 1.6625e-9 | 5.1481e13 |
| K | 5 | 1.65e16 | 1.6425e-2 | 8.3170e-10 | 5.7730e13 |
| K | 10 | 1.79e16 | 1.5813e-2 | 3.2001e-9 | 6.7629e13 |
| K + E | 0 | 1.10e28 | 2.9084e-2 | 4.3635e-9 | 3.7210e13 |
| K + E | 5 | 9.58e15 | 2.4516e-2 | 1.1250e-9 | 3.9244e13 |
| K + E | 10 | 1.02e16 | 2.4580e-2 | 1.0333e-9 | 2.4427e13 |
| Requested tolerance | — | — | 9.8255e-10 | 1.3123e-9 | 1.2784e14 |

Every constant-RHS result fails. Both five-pass affine results pass; ten
passes lose that success for unregularized K. Every combined result passes,
but that RHS was formed after an inaccurate predictor and is approximately
1.28e27 in magnitude. It does not establish a useful optimization direction.

Five passes bring row maxima to approximately [0.6395,1.0]; ten bring them to
[0.9861,1.0]. Better balance does not ensure smaller original-coordinate
residuals. Even the improved condition estimates remain around 1e16.

## Disposition

The result justified testing five passes inside the native solver, rather than
increasing the pass count or declaring a fix from an improved condition estimate.
That subsequent test is reported separately in
[Native KKT scaling](NATIVE_KKT_SCALING_REPORT.md). It changes the factorization
path throughout optimization and must not be conflated with this saved-matrix
experiment. No scientific gate or production setting changed here.

## Evidence and verification

- Runner: [kkt_scaling.py](kkt_scaling.py).
- [Analysis](results/kkt_scaling_001/analysis.json), SHA-256
  `f92501a5ece7525074a2008415e0bac007bccfbf19b1248e3b15c96787229b50`.
- [Supervision](results/kkt_scaling_001/supervision.json), SHA-256
  `10bd53818ad64da6905bd3e5848a7535c413fa92a61cbcc9f482971a896ef671`.
- Captured source systems and their identities are bound through the analysis
  context and `kkt_capture_001/summary.json`.

One serial worker completed all six arms in 11.009 seconds at 682.66 MiB sampled
peak RSS, within 180 seconds and 4096 MiB. The analysis retains each D vector,
direct and refined solution, original-coordinate errors, scaling history,
condition estimate, and factor timing. The original checkpoint verification
passed 128 conditioning tests; current verification including the next trial
passes 134. Main checkout, dependencies, and installed solvers were unchanged.
