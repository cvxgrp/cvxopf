# Independent factorization of the failing CLARABEL KKT system

Independent sparse LU avoids the catastrophic directions returned by CLARABEL's
native QDLDL path on the saved failing KKT system. It does not fully resolve the
numerical difficulty: the first two linear systems still miss the requested
residual tolerance. The evidence supports both severe late-iteration scaling
and sensitivity to the linear factorization path, rather than a minimum-step
cutoff problem or a demonstrated modeling error.

## Capture and comparison

The fixed-load surplus problem and settings are unchanged from
[the preceding trace diagnostic](NATIVE_STEP_REPORT.md). A new control and
instrumented native replay each reproduced every saved primal, slack, and dual
coordinate exactly. The final result remains `InsufficientProgress`, iteration
31, and is not accepted or promoted.

The capture retains the three failing right-hand sides, native returned vectors,
the unregularized symmetric KKT matrix, and its signed static diagonal shift.
All three right-hand sides share exactly the same matrix. The native source
patch, entrypoint, lockfile, binary hash, and build command are retained in
`results/kkt_capture_001/engine.json`.

SciPy SuperLU factored the original matrix and, separately, the matrix with the
recorded static shift. Both used COLAMD ordering, diagonal pivot threshold 1.0,
and `Equil=True`. Up to five residual-correction attempts were permitted for
each right-hand side; only improvements to the original-system residual were
retained. No independent vector was inserted into CLARABEL and there were no
additional optimization calls after capture.

## Residual results

The table evaluates `||rhs - K*x||_inf` for the exact saved binary64 matrix and
vectors using 80-digit arithmetic over every row. Independent values are the
retained refined SuperLU solutions of the unregularized system.

| Right-hand side | Native QDLDL path | Independent sparse LU | Requested tolerance |
| --- | ---: | ---: | ---: |
| Constant RHS | 1.06e25 | 1.44e-2 | 9.83e-10 |
| Affine predictor | 3.56e9 | 1.66e-9 | 1.31e-9 |
| Combined corrector | 4.27e34 | 5.15e13 | 1.28e14 |

The constant-RHS solution's maximum coordinate falls from 7.16e24 on the native
path to approximately 1.26e3 with independent LU. This is a large improvement
in the computed linear solution, not merely a different residual calculation.

Only the combined system meets its requested residual tolerance. Its RHS is
already about 1.28e27 because it was assembled after the bad affine direction;
successfully solving that saved system does not establish a useful corrected
optimization step.

The original-system residual for the constant RHS is 0.0251 when evaluated in
ordinary double precision and 0.0144 at 80 digits. Some rows subtract terms of
order 1e14. Cancellation affects residual measurement, but it does not account
for the native catastrophic error or make the independent result meet 1e-9.
The high-precision check evaluates double-precision solutions; it is not a
high-precision factorization.

The recorded static shift also avoids the native blow-up when factored with
SuperLU, but does not cure the remaining error. Its refined original-system
residuals in double precision are 0.0301, 4.35e-9, and 6.94e13. QDLDL's dynamic
pivot modifications are not reproduced by this static-shift comparison, so it
does not isolate pivoting, ordering, and dynamic regularization individually.

## Matrix scaling and physical identity

The symmetric KKT matrix has 97,794 rows and columns and 494,142 stored nonzeros.
Its nonzero magnitudes range from 3.33e-12 to 2.90e19. The one-norm condition
estimates are approximately 9.2e35 before the static shift and 1.1e28 after it.
These are descriptive estimates using the computed LU inverse action, not
certified condition-number bounds or guarantees of forward accuracy.

The five largest diagonal entries map to generator 29, at bus 69, and its
upper-bound constraints in local intervals 17–21. The upper bound is about
907 MW; retained generation at local interval 20 is 3.62e-13 MW. These are
inactive upper bounds. Their very large late-iteration cone-scaling entries
are distinct from the branch 86–87 SOC that finally restricts the bad step.
Neither observation justifies removing a physical bound.

A global normwise backward error can be misleading here: the native first
solution has a value near 5e-20 by that measure, because its denominator includes
the enormous matrix norm and enormous solution norm. Its residual relative to
the RHS is nevertheless about 1e21. The raw, RHS-relative, normwise, and
componentwise measures are all retained rather than selecting one favorable
number.

## Practical conclusion

The next bounded experiment should test the linear-system treatment within
CLARABEL—an alternative factorization or explicitly controlled KKT scaling and
regularization—on the unchanged optimization problem. The diagnostic motivates
that test but does not show that a full solve with a replacement linear solver
will reach `Solved`. Input scaling alone cannot prevent an interior-point
method's changing cone geometry from creating a badly scaled later KKT system.

Acceptance criteria, costs, physical constraints, and production solver
defaults remain unchanged. This result is specific to the retained CLARABEL
case and does not identify the source of MOSEK or COPT's earlier behavior.

## Evidence and verification

- Capture root: `results/kkt_capture_001/summary.json`, SHA-256
  `da2e991ce33c2b8046c66225e3b627ba1a81785847e4c278ac35d99856993c5e`.
- Factorization analysis: `results/kkt_factorization_001/analysis.json`, SHA-256
  `54c710ae389ac1245a6446a4889a516bd9711d2e4b85a167f3058c1bd3020e44`.
- Full high-precision residual check:
  `results/kkt_residual_check_001/analysis.json`, SHA-256
  `2ddba0c080da28a13aad338c075611684f099ef958d3c85095c0e14761213309`.
- Scripts: `kkt_capture.py`, `kkt_factorization.py`, `kkt_residual_check.py`.
  The latter two perform no optimization calls.

All workers ran serially with the existing 180-second and 4096-MiB limits. The
factorization worker completed in 2.10 seconds at approximately 490 MiB sampled
peak RSS; the high-precision worker completed in 4.19 seconds. Each independent
factorization took approximately 0.10 seconds; this is not an end-to-end solver
timing comparison. The installed solver, main checkout, and dependency lockfile
were not modified.

Verification passed: 124 conditioning diagnostic tests, Ruff lint and format
checks, and whitespace checks. The two existing test warnings concern mixed
OpenMP imports and a zero-norm unit-cone calculation. All additions remain
unstaged and uncommitted in the isolated worktree.
