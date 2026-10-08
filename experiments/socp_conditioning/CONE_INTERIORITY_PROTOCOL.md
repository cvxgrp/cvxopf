# Native cone scaling failure diagnostic

Declared 2026-10-06 before execution. The native SuperLU experiment reduced
the gap approximately 139-fold but stopped at cone scaling, before the next
factorization. This diagnostic identifies the failed operation and distinguishes
loss of strict interiority from cancellation in its binary64 evaluation.

## Fixed comparison

Run one observationally instrumented SuperLU replay of the frozen Case118 Tracy
surplus window [3308,3332). Keep normalized device cones, joint input scaling,
fixed loads, fixed-coordinate substitution, all solver tolerances, regularization,
refinement and SuperLU options unchanged. No KKT scaling. Require native input
SHA `b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184`
and exact returned x/s/z, status and iteration agreement with the retained
`native_lu_probe_001/superlu` arm, SHA
`756623ebc5199c2d8b632396b454ae66593084a63091e2cf981bf458d8995dbc`.
If reproduction fails, retain the evidence and stop; do not tune or retry.

## Capture and reconstruction

Record the failing cone's native row range, kind, exact internal s/z vectors,
the preceding step's s/z/ds/dz, applied step and homogeneous rescaling factor.
Record which cone-scaling check failed and the binary64 intermediate values.
Observation only: no replacement norms, modified steps, clipping or arithmetic.

Recompute SOC margins and determinants with 90-digit arithmetic applied to
the exact retained binary64 inputs. Compare the pre-step and post-step state;
map native rows through retained presolve/substitution and canonical schemas
to the physical constraint and time. Check native row dimensions before mapping.
Returned unscaled solution vectors alone are not evidence of internal interiority.

A strictly interior vector misclassified by a rounded norm motivates stable
cone arithmetic. A genuinely noninterior state motivates step/interiority
protection. An inaccurate direction remains a separate linear-solve issue;
neither diagnosis proves a remedy without a later authorized test.

## Execution and evidence

Use only the authorized isolated worktree and temporary native build. One
serial optimization, one computational thread, 180 seconds and 4096 MiB,
including the native child and persistent bridge. Preflight process and thermal
monitoring. Preserve production code, installed packages and historical outputs.
Publish immutable evidence under `results/cone_interiority_001/`, including
native source patch/binary identity, source and environment context, raw trace,
supervision, complete results and independent physical/accounting checks.

This is diagnostic, not promotional. Save a final report and update the study
index for any outcome. No solver tolerance or acceptance change is authorized.
