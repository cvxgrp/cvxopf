# Retained solver-input matrix conditioning

Offline inspection of the same 24-hour surplus problem, with and without load
shedding, for CLARABEL, MOSEK and COPT. No optimization solves, production
changes, or modifications to the live study. Evidence is in
`results/matrices_003/`; `matrix_conditioning.py` reproduces the analysis.
The six supervision records bind each analysis and its input context by SHA-256.
COPT's reconstructed native model re-exports byte-identically to each retained
MPS; its solve method is replaced by an explicitly non-solving stub.

## Measured input conditioning

Approximate rectangular 2-norm condition numbers, before solver presolve or
internal equilibration:

| Input matrix | Shedding: raw | Fixed loads: raw | Shedding: normalized | Fixed loads: normalized |
|---|---:|---:|---:|---:|
| CLARABEL full A | 908,324 | 908,324 | 1.447 | 1.446 |
| CLARABEL equality rows | 703.0 | 703.0 | Unavailable | 2.832 |
| MOSEK dualized full A (all equality rows) | 908,324 | 908,324 | 2.583 | 2.583 |
| COPT full linear A | Structurally rank deficient | Structurally rank deficient | Structurally rank deficient | Structurally rank deficient |
| COPT equality rows | 1,787.5 | Unavailable | 3.538 | Unavailable |

Normalization means five alternating algebraic row/column 2-norm passes. It is
**not** a cone-preserving reformulation, a replay of solver equilibration, or a
claim that the solver's actual Newton system has this condition number.

The estimates use sparse Gram shift-invert iteration, followed by a check of
the singular triplet in the original matrix coordinates. Gram formation squares
conditioning, so these are numerical estimates, not certified spectral bounds.
For the raw CLARABEL and MOSEK matrices, sigma_max is approximately 647.082,
sigma_min approximately 0.000712391, and checked smallest-triplet residuals are
below 6e-13. Nonconverged estimates remain explicitly unavailable.

The matrices differ in representation, not just dimensions:

| Solver input | With shedding: rows x columns | Fixed load: rows x columns |
|---|---:|---:|
| CLARABEL quadratic/conic slack form | 85,206 x 25,179 | 80,454 x 22,803 |
| MOSEK dualized conic form | 25,179 x 86,118 | 22,803 x 81,366 |
| COPT primal form with explicit cone coordinates | 86,118 x 81,027 | 81,366 x 78,651 |

COPT's full linear matrix omits cone geometry and native variable bounds. Its
rank deficiency is not evidence that its complete optimization problem or KKT
system is singular. Likewise, redundant/opposite inequality rows and a singular
quadratic objective matrix need not be modeling errors.

## Scale disparities

All six A matrices retain nonzero coefficients from approximately 0.0005783 to
386.47, a spread of 668,262. CLARABEL's nonzero row norms span a factor 805,573;
its nonzero column norms span a factor 49,128. Removing shedding barely changes
these extrema or the estimated full-A condition number.

The linear objective is a separate problem:

| Original-primal objective coefficients | With shedding max/min nonzero | Fixed-load max/min nonzero |
|---|---:|---:|
| CLARABEL | 654,205,173 | 1,245,816 |
| COPT (quadratic costs conified) | 779,855,569 | 1,485,094 |

The shedding coefficient maximum is 6,542,051.73, versus a storage throughput
coefficient of 0.01 in the CLARABEL input. After removing shedding, the largest
remaining linear coefficient is 12,458.1564. In MOSEK's dualized model this cost
scale disparity appears in the equality right-hand side, not its objective.
Coefficient spreads are not themselves matrix condition numbers.

## Interpretation

There is now direct evidence of substantial, algebraically removable scaling
imbalance in the input matrices. Removing shedding addresses an additional
objective-scale disparity but does not remove the full-A imbalance. The
equality-only CLARABEL matrix is much better conditioned than its full A, so
the evidence does not point exclusively to ill-conditioned nodal equations.

This is not yet a diagnosis of solver termination. Cone boundary geometry,
coincident bounds, nearly inactive/weakly priced directions, and evolving
barrier/KKT scaling remain distinct considerations. The different solver forms
also prevent ranking solvers by these input condition numbers alone.

A justified next investigation would localize the weak singular directions to
device families and design cone-compatible variable/equation scaling, while
checking objective and dual-residual scaling separately. None of that model
transformation or any further numerical optimization was performed here.

## Execution and verification

- `matrices_001`: stopped our analysis worker after the original PROPACK
  routine did not promptly honor a Python alarm; retained as an interrupted
  analysis attempt. It never invoked an optimizer.
- `matrices_002`: completed coefficient/structure inspection with bounded
  ARPACK callbacks; most smallest-singular-value estimates did not converge.
- `matrices_003`: completed all six inspections with the additional sparse
  factorization estimates. Workers ran serially, single-threaded, under the
  existing 180-second/4-GiB limits; maximum sampled RSS was 548.875 MiB.
- All six final contexts and supervision-bound artifact hashes verified.
- Six matrix tests cover known spectra, transpose equivalence, structural
  deficiency, zero coefficients, and callback budget enforcement; Ruff passed.
- Changes remain in the authorized isolated worktree, unstaged and uncommitted.
