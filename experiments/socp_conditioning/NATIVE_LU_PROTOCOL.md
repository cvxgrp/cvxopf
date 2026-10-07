# Native CLARABEL pivoted LU diagnostic

Declared 2026-10-06 before execution. Test whether replacing the native QDLDL
path with pivoted SuperLU prevents inaccurate Newton directions and achieves
the unchanged acceptance gate on the frozen 24-hour fixed-load surplus case.
The saved-matrix LU improvement motivates this experiment but does not predict
full-solve success. No production adoption or further sweep is authorized here.

## Fixed problem and numerical controls

Case118 Tracy window [3308,3332), normalized device cones, fixed load service,
exact fixed-coordinate substitution, and the previously declared joint input
scaling remain unchanged. Both arms must have identical native input JSON,
SHA-256 `b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184`.

CLARABEL 0.11.1 uses its frozen full/reduced gap and feasibility tolerances
1e-10, maximum 5,000 iterations, minimum termination step 1e-8, original static
regularization, and original iterative-refinement rules. Costs, physical sets,
and acceptance tests are unchanged. Native KKT five-pass scaling is disabled.

## Two serial arms

1. Disabled QDLDL control. Require exact equality of returned x/s/z with the
   retained `minimum_step_001/clarabel` arm before starting the second arm.
2. One enabled SuperLU arm. At every factorization, receive the complete
   upper-triangular KKT with CLARABEL's original-coordinate static shift,
   reconstruct its symmetric sparse matrix, and factor with COLAMD ordering,
   diagonal pivot threshold 1.0, and `Equil=True`. Reuse that factor for every
   RHS until the next refactorization. CLARABEL performs refinement against
   its original unregularized matrix; the bridge performs no extra refinement.

SuperLU replaces QDLDL's ordering and dynamic pivot treatment. This is a
linear-solver-path comparison, not an isolated pivoting or regularization test.
The mathematical optimization problem is unchanged. No tolerance adjustments,
automatic retries, or additional full-problem arms follow a rejection.

## Isolation and resource controls

Use the already authorized isolated cvxopf worktree and temporary Rust build.
Do not replace the installed CLARABEL wheel or change production sources.
A persistent Python/SciPy bridge supplies SuperLU through serial JSON requests.
It inherits the native worker's supervised process group; normal native teardown
kills and reaps it, and process-group termination covers abnormal stops.

One optimization worker and one computational thread at a time. Each arm has
180-second and 4096-MiB limits. Native-side sampling includes the Python worker,
native process, and native descendants, including the bridge. Validate process
and thermal monitoring before launch. Raw outputs are immutable under
`results/native_lu_probe_001/`; partial/failure evidence remains there.

## Evidence and decision

Bind runner/bridge/protocol source hashes, dependency/platform context, native
source commit and patch, entrypoint, lockfile, and compiled binary hash. Retain
every optimizer iteration, original-coordinate refinement residual, native
status, complete result vectors, physical audit, and objective reconstruction.

Record factor and triangular-solve times, matrix-assembly time, total bridge
request time, worker wall time, and combined sampled RSS. The remainder of
bridge request time includes serialization, transport, startup, logging and
other overhead; it is not a pure IPC latency measurement. This diagnostic
does not establish production solver performance.

Success requires native `Solved`, passing independent physical/accounting
checks, objective reconstruction within the existing gate, and verified input
identity. Improved but rejected results remain rejected. Save a final report
and update the study index for success, failure, or resource termination alike.
