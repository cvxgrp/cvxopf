# Pivoted LU inside native CLARABEL

Completed 2026-10-06. Replacing the native QDLDL path with SuperLU reduced the
returned primal-dual gap approximately 139-fold and advanced beyond the prior
catastrophic-direction failure. It did not reach the frozen acceptance gate:
CLARABEL stopped with `NumericalError` at iteration 32. Physical residual and
objective-reconstruction checks pass, but native optimality remains unaccepted.
This is a useful numerical improvement on one instance, not a completed fix.

## Declared comparison

The [pre-execution protocol](NATIVE_LU_PROTOCOL.md) authorized exactly two serial
arms: an exact-reproduction QDLDL control, then one SuperLU solve. Both use the
Case118 Tracy fixed-load surplus window [3308,3332), normalized device cones,
joint input scaling, and exact fixed-coordinate substitution. The optimization
problem, costs, physical constraints, and acceptance tests are unchanged.

The native CLARABEL 0.11.1 input files are byte-identical. Full/reduced gap and
feasibility tolerances remain 1e-10, the iteration cap 5,000, and the minimum
termination step 1e-8. No five-pass native KKT scaling is enabled.

At each KKT update, the alternative path factors the complete symmetric matrix
including CLARABEL's original static diagonal shift. It uses SuperLU with
COLAMD ordering, diagonal pivot threshold 1.0, and `Equil=True`, matching the
earlier saved-matrix diagnostic. The same factor supplies every RHS until the
next update. CLARABEL's original refinement loop still measures residuals
against unregularized K. There is no extra bridge-side refinement.

This replaces QDLDL's ordering, factorization, and dynamic pivot treatment as
a package. It does not isolate pivoting or regularization individually.

## Results

| Measure | QDLDL control | SuperLU path |
| --- | ---: | ---: |
| Native status | InsufficientProgress | NumericalError |
| Reported iterations | 31 | 32 |
| Canonical primal objective | 3.12499877308 | 3.12499875200 |
| Canonical dual objective | 3.12499848046 | 3.12499874989 |
| Absolute gap | 2.92617e-7 | 2.10649e-9 |
| Relative gap | 9.36376e-8 | 6.74077e-10 |
| Native primal residual | 5.80305e-14 | 3.02165e-14 |
| Native dual residual | 2.52976e-15 | 5.31055e-16 |
| Reconstructed physical objective | 3.12499876304 | 3.12499875193 |
| Canonical versus physical objective discrepancy | 1.00380e-8 | 7.20530e-11 |
| Independent physical residual checks | Pass | Pass |
| Complete acceptance | Reject | Reject |

The disabled control matches the saved baseline's complete x/s/z vectors bit
for bit. Its exact-reproduction gate passed before the LU worker launched.

LU's relative gap is still 6.74 times its 1e-10 target; its absolute gap is also
above 1e-10. The alternative gap tests therefore both fail. Physical feasibility
is excellent: maximum active nodal-balance residual is 8.789e-9 MW and reactive
nodal-balance residual is 8.674e-10 MVAr. All retained device, network, state,
terminal, reporting, and physical cost-accounting checks pass. Neither those
checks nor the small gap overrides the native rejected status.

## What the linear trace shows

The LU path continues through the region where the QDLDL control produces
catastrophic directions. At LU iteration 30, its combined cone step limit is
1.0, rather than the control's approximately 6.6e-39. These are different
optimization paths and matrices, not another matched saved-matrix comparison.

The last LU Newton update has the following original-coordinate residuals:

| Linear solve | Residual after native refinement | Requested tolerance | Meets tolerance |
| --- | ---: | ---: | --- |
| Constant RHS | 0.485840 | 9.82547e-10 | No |
| Affine predictor | 3.23951e-10 | 1.31221e-9 | Yes |
| Combined corrector | 6.83940e-10 | 1.31205e-9 | Yes |

The final combined cone step limit is 0.90269; the optimizer applies a step
of 0.89367 and reports the improved gap at iteration 32. It then returns
`NumericalError` without another factorization request. All 33 LU
factorizations and 307 factor-based solves reported success, with no bridge
errors. There were 98 top-level linear solves; the extra requests were native
refinement corrections. Of those 98 solves, 36 miss their requested refinement
tolerance, so this does not establish uniformly accurate Newton solves.

The retained trace and native control flow localize termination to the
cone-scaling stage before the next KKT update. The source checks cone scaling
before factorization and can set `NumericalError` there. The present trace
does not identify the offending cone or distinguish loss of strict interiority
from numerical cancellation within that calculation. That is a remaining
diagnostic question, not evidence that the physical problem is infeasible.

## Timing and memory

| Measure | QDLDL control | SuperLU path |
| --- | ---: | ---: |
| Native solver elapsed seconds | 0.895 | 24.771 |
| Supervised worker wall seconds | 5.193 | 28.545 |
| Sampled combined process RSS MiB | 492.00 | 880.64 |

The alternative is a diagnostic bridge, not a production-speed implementation.
Its native elapsed time includes 24.258 seconds in bridge requests. That total
contains 3.544 seconds in LU factorizations, 1.044 seconds in triangular solves,
0.679 seconds in matrix assembly, and 18.991 seconds of serialization,
transport, startup, logging, and other overhead. Factor L plus U peaks at
3,616,695 stored entries. These costs are retained separately rather than
presenting bridge overhead as intrinsic SuperLU performance.

Both workers ran serially with one computational thread and stayed inside the
180-second / 4096-MiB limits. Combined RSS includes the Python worker, native
CLARABEL process, and persistent bridge; the outer supervisor's worker-only
RSS is not the reported combined peak. The bridge was reaped at native teardown.

## Disposition

The alternative factorization path is more promising than the tested native
five-pass scaling. It substantially improves the gap and avoids the observed
catastrophic-direction stop, while preserving strong physical and objective
evidence. It is still not an accepted solver configuration, and the experiment
does not warrant a production replacement or changed tolerances.

Further work would need to distinguish the remaining constant-RHS accuracy
problem from the final cone-scaling failure. No further solve or parameter
sweep was performed. The result remains specific to this retained surplus
instance; it does not resolve the earlier MOSEK or COPT behavior.

## Evidence and verification

- Runner: [native_lu_probe.py](native_lu_probe.py); persistent numerical bridge:
  [lu_bridge.py](lu_bridge.py).
- [Summary](results/native_lu_probe_001/summary.json), SHA-256
  `fcfa277e150db428a1fdd9148217fdd6c2335bb46d413bb88bd8ce109f18122d`.
- Native input SHA-256 shared by both arms:
  `b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184`.
- QDLDL control arm SHA-256:
  `9b18f6c5229859503a715187144bba6d8cf0fb283872d882041680e0b01d95bc`.
- SuperLU arm SHA-256:
  `756623ebc5199c2d8b632396b454ae66593084a63091e2cf981bf458d8995dbc`.
- [Native engine record](results/native_lu_probe_001/engine.json), SHA-256
  `971f9af0941b847b7d3a92f8353a8fea22950e22c5e837984d95be0d7faf08e2`.

The engine record retains the native source patch, entrypoint, Cargo lockfile,
source commit `25540f559592068d0c8a80e46ded1b21760212a1`, binary hash,
compiler version, and build command. Contexts bind the runner, bridge, protocol,
production source identities, packages, and platform. All supervised artifact
hashes and both before/after contexts verified. The main checkout at
`dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a` remains unchanged and clean.

An initial wrapper preflight enumerated three solvers while using CLARABEL-only
options and stopped before output creation or worker launch. The enumeration
was corrected and regression-tested before these two arms ran. There was no
failed numerical attempt, numerical retry, or post-result settings adjustment.

Verification passed: 143 conditioning tests, including nine LU bridge/runner
tests; 122 native library tests; Ruff lint/format and whitespace checks. Known
warnings concern mixed OpenMP imports, a zero-norm cone test, and an upstream
Rust lifetime annotation. No installed solver, production code, or lockfile
was changed. New experimental work remains unstaged and uncommitted.
