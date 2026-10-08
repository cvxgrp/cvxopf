# Practical SuperLU tolerance check

Declared before execution on 2026-10-06. Run two serial fresh workers:
the fixed-load surplus window [3308,3332), then the existing contrasting
deficit window with its original shedding policy. Reuse the retained
`fixed_substitution_001` prepared problems exactly, including normalized
device cones, exact Pg/p_nd substitution and the frozen joint scaling rule.
Do not remove shedding from the deficit scenario.

Use the native experimental SuperLU path with its original static shift,
COLAMD ordering, diagonal pivot threshold 1, Equil=True, and unchanged native
refinement. Disable shared and square-root-only compensated cone arithmetic,
native KKT scaling and capture switches. Change only full `tol_gap_rel` from
1e-10 to 1e-9 relative to the previous SuperLU diagnostic. Keep `tol_gap_abs`
and `tol_feas` at 1e-10, reduced tolerances at 1e-10, max_iter=5000 and
min_terminate_step_length=1e-8. Require native Solved, not AlmostSolved.

For surplus, verify exact native payload equality to the retained SuperLU
input except the single declared setting. For both cases, reconstruct and
verify the original and transformed canonical matrices against the frozen
substitution artifacts before solving. Preserve case-specific audit semantics.
Retain complete native input/output, primal/dual/slack vectors, physical
results, native trace, solver exceptions, physical/accounting checks, original
objective reconstruction, timing and combined process RSS. Keep original
physical tolerances and the objective reconstruction threshold 1e-4 unchanged.

One worker and one computational thread at a time, 180 seconds and 4096 MiB
per worker including native process and bridge. Stop on process/resource or
artifact/provenance failure. Ordinary solver rejection is retained; continue
to the contrasting condition without rescue, retry or settings changes.
Two optimizer calls maximum. No MOSEK/COPT rerun is authorized by this check.

Bind sources, native engine patch and binary, historical arm hashes and exact
solver settings before execution. Use immutable `results/practical_lu_001/`.
Save a final report and update the study index whether successful or not.
This is an experiment-local prospective tolerance decision, not retroactive
promotion of rejected records or a changed production default. Explain the
Python/cvxopf implementation boundary and limits of transfer to commercial
solvers without claiming their convergence from CLARABEL evidence.
