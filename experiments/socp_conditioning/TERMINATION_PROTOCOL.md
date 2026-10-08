# Three-solver termination diagnostic

Authorized replication: CLARABEL, MOSEK, COPT, in that serial order. One fresh
worker per solver, one thread, 180 seconds and 4096 MiB per worker. No retries or
tuning sweep. This is nonpromotional, isolated, uncommitted diagnostic work.

Use the identical fixed-load surplus 24-hour instance [3308,3332), normalized
device cones, exact fixed Pg/p_nd substitution and the unchanged five-pass joint
scaling rule. Verify the original and transformed matrices against
`fixed_substitution_001/fixed_surplus_scaled`; bind its arm SHA and all execution
sources. No physical model, objective, audit tolerance or production file changes.

Re-express the transformed canonical problem through CVXPY for solver delivery.
Require exact CLARABEL A/P/b/c and cone-order equality before any solve.
MOSEK and COPT use their CVXPY conic paths, including quadratic-objective
epigraphs; MOSEK additionally dualizes. These are the same mathematical problem,
not identical Newton systems. Retain native task files and common-coordinate
primal/dual evidence where available. Invoke all solves through OPFBuild.solve.
For commercial conic paths, factor the nonnegative diagonal quadratic objective
explicitly on its nonzero coordinates (verified to floating-point precision),
avoiding CVXPY's dense LDL fallback on a large singular diagonal matrix.

CLARABEL full tolerances stay at 1e-10, 5000 iterations. Set reduced post-process
tolerances equal to full tolerances to expose the underlying failure instead of
AlmostSolved; retain iteration callbacks, final status/settings/linear-solver
summary and native primal even if CVXPY rejects it. Compare the iterate against
the previous retained solve to check that this diagnostic did not change its path.
MOSEK conic primal/dual feasibility and relative gap: 1e-10, 5000 iterations.
COPT FeasTol/DualTol: 1e-9 (its supported lower limits), 5000 barrier iterations;
reoptimization disabled. Criteria differ by solver: no assertion of equal native
stopping tolerances. Preserve native logs, MOSEK return code and iteration trace,
and COPT status/statistics. Do not infer inaccessible factorization pivots or
refinement residuals from the input matrix or a termination label.

Retain rejected finite iterates for separately labeled physical audits. A solver
failure never becomes a solver success because the physical audit passes. Use
the unchanged independent fixed-load physical/accounting gate. Retain original
objective, named costs, common primal/dual residuals and complementarity, native
status and gap information, source/machine/package context, resource supervision,
immutable result hashes and logs. Inspect results before selecting any further
intervention; this authorization does not include another parameter sweep.
