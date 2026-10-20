# Shared compensated SOC determinant diagnostic

Declared 2026-10-06 before execution. Extend the previously tested signed
compensated determinant consistently across native SOC determinant consumers.
The square-root-only trial corrected scaling, but step-length and corrector
operations still evaluated the same strictly interior vector as having zero
determinant.

## Changed numerical operation

With an explicit experimental switch, `_soc_residual` uses the existing
power-of-two-normalized FMA/TwoSum signed sum of squares for every finite valid
input, not just false-zero inputs. Rescale the signed determinant to the
original coordinates without clipping. This automatically reaches square-root
scaling, inverse Jordan operations, corrector offsets and the signed quadratic
coefficients used by step selection. Negative determinants of search directions
must remain negative; determinant evaluation is not an interiority predicate.

The shifted residual helper forms the same rounded binary64 `z + alpha*dz`
coordinates, then calls the same compensated determinant. Disable the earlier
square-root-only rescue switch. Preserve all remaining formulas, including
mixed bilinear products, discriminants, root selection, max-step fraction,
regularization, refinement and the SuperLU path. No clipping, positive floor,
step backtracking or tolerance changes. Unsupported/nonfinite helper inputs
retain the original native arithmetic rather than being labeled interior.

Because all determinant evaluations change, early rounding and the full solve
path may differ from the reference. Record the first iteration difference;
do not require an identical prefix or assume it reaches the same late iterate.

## Arithmetic gate and replay

First check actual native shared calculations on the previously captured
vector, adjacent values, exact boundaries, interior/exterior points, signed
directions and powers-of-two rescalings against 90-digit exact-input arithmetic.
Verify a shifted evaluation agrees with the common primitive and that the
previous falsely zero step becomes positive. Retain the native records.

Run exactly one fresh optimization on the frozen Case118 Tracy surplus window
[3308,3332), normalized device cones, joint scaling, fixed loads and exact
fixed-coordinate substitution. Require unchanged native input SHA
`b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184`.
The reference is the square-root-only arm at `compensated_cone_001/compensated`,
SHA `4a7b8896fd68ef1f8bae7beaf561067cd8281e55c58efe0cdd2f99ac10bab15c`.
Do not retry, sweep parameters or add another solver arm after rejection.

## Controls and disposition

Use the authorized isolated worktree and temporary native source/build only.
One optimization worker and one computational thread, 180 seconds, 4096 MiB
including native child and bridge. Preflight process and thermal monitoring.
Bind protocol/runner/analysis, native patch/binary, fixture records, software
and platform context. Save immutable raw outputs in `results/shared_cone_001/`.

Acceptance still requires native `Solved`, independent physical/accounting
checks and the unchanged objective reconstruction gate. Report solver status,
gap, residuals, objective, timings, RSS, arithmetic changes and any new failure.
Save a final report and update the index regardless of outcome. This is not
production adoption or a numerical-range guarantee for all SOC problems.
