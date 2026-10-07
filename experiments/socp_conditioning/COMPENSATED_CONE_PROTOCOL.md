# Compensated SOC determinant diagnostic

Declared 2026-10-06 before execution. Test whether compensated arithmetic fixes
the identified false-zero interiority check and permits further convergence on
the unchanged fixed-load Case118 Tracy surplus problem [3308,3332).

## One numerical change

In native CLARABEL 0.11.1, only `_sqrt_soc_residual` gains an opt-in fallback
when the ordinary determinant is finite and nonpositive and the leading
coordinate is positive. Preserve the original positive-result path exactly.
Do not replace step-length formulas, other cone operations, KKT factorization,
regularization, iterative refinement, tolerances, objectives or physical sets.

The fallback scales the input by a power of two, then computes the signed sum
of squares using FMA product residuals and error-free TwoSum additions, with
one accumulated correction. Return `scale * sqrt(compensated_determinant)`
only for a finite strictly positive determinant; otherwise retain rejection.
There is no clipping, positive floor, or new acceptance tolerance. This is a
double-precision compensated calculation, not an exact sign certificate for
every possible input. It addresses the captured case and is not a production
qualification of the entire numerical range.

## Preflight and single replay

First exercise the actual native helper on the captured dual vector, adjacent
leading-coordinate values, exact boundaries, exterior vectors, and power-of-two
rescalings. Retain native outputs and compare determinants against 90-digit
arithmetic on the exact binary64 inputs. No optimization calls in this gate.

Then run one fresh SuperLU-path optimization with the fallback enabled. Require
the unchanged native input SHA
`b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184`.
Compare with the exact observational replay in `cone_interiority_001`, whose
arm SHA is `2c3509ff4da97edc65a7a9c4061463120554fec676ca6d6851bc94250ff1652f`.
Check that iteration evidence is unchanged before the first fallback, and
retain every fallback vector/determinant and any subsequent failure. No retry,
parameter sweep, or second changed arm follows a rejection.

## Limits and decision

Use the authorized isolated worktree and temporary native build, not installed
solvers or production sources. One worker and computational thread, 180 seconds,
4096 MiB including the native process and persistent bridge. Preflight process
and thermal monitoring. Bind the native source patch and binary, test output,
runner/protocol, environment and input identities. Publish immutable evidence
under `results/compensated_cone_001/` and save a final report for any outcome.

Native `Solved`, independent physical/accounting acceptance, and the unchanged
objective reconstruction gate are all required. Getting past the old failure
alone is progress, not accepted optimality. Keep raw failures and distinguish
arithmetic correction from any remaining convergence or linear-solve limitation.
