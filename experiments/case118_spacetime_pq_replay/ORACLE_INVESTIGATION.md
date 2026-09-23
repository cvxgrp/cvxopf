# Hour 6047: nonlinear oracle investigation

Scope authorized 2026-09-22: source-change inventory and a no-solve comparison of
functions and derivatives at the frozen starting point. Subsequent requested
deliverables are a stable project configuration, upstream issue details, and a
colleague email. The historical time-only model remains the scientific fixture.

## Starting-point evidence

The full canonical expression fingerprints agree between the old and new stacks:
ordered constraint blocks, scalar row expansion, variable shapes and positions,
atom types/metadata, and complete constant/parameter values. Constraint rows can
therefore be compared without an inferred permutation. All 9,124 starting
coordinates agree.

At that point the objective, all 10,601 constraint values, objective gradient,
and coordinate-aligned Jacobian values agree exactly. Three Lagrangian Hessians
also agree exactly: objective factor one, with zero, all-one, and seeded random
constraint multipliers. This is a bounded comparison at one point, not a proof
of global derivative correctness or a finite-difference validation.

The old stack reports 412,439 stored Jacobian entries; the new stack reports
36,167. Removing exact zeros leaves identical matrices with 35,451 nonzero
entries. Both report 10,104 Hessian structure entries.

An additional no-solve control on the new stack, setting
`cvxpy.settings.SPARSE_DENSITY_THRESHOLD=0.0` in that fresh process only, restores
412,439 stored Jacobian entries. This is a diagnostic use of an internal setting,
not yet a recommendation for project code.

## Source inventory

The installed wheels differ in 69 CVXPY files and four sparsediffpy files
(excluding bytecode). The latter count includes its compiled extension, whose
internal implementation cannot be reconstructed merely from the shipped binding
headers. It must not be read as a complete upstream C/C++ source-change inventory.

CVXPY's `ipopt_nlpif.py` and `nlp_solver.py` are byte-identical. The relevant
diff-engine change in `converters.py` detects dense constant left-multiplication
matrices with density below 0.05 and sends them through the sparse CSR binding.
`helpers.py` changes CSR matrix conversion to CSR array conversion. `registry.py`
adds dense/sparse and parametric quadratic-form handling. The DNLP geo-mean
canonicalizer also changed; applicability to this fixture must be checked rather
than assumed. Other package changes and all text diffs are retained in the raw
inventory.

## Completed discriminating solve control

The owner authorized this one additional solve. With CVXPY 1.9.3/sparsediffpy
0.6.1 unchanged and density dispatch disabled in that fresh process, the attempt
converged in 70 iterations, was accepted, and reproduced all historical named
solution values and extracted result fields exactly. Native IPOPT time was
29.940 seconds. Process and thermal telemetry were retained, with the external
fan confirmed on. See `artifacts/provenance/dense_route_control.json` and the raw
`oracle-investigation/dense-control-run/` records.

The no-solve density control also matched the entire old Jacobian COO coordinate
sequence and values, not merely its size. Together these controls localize the
trigger to CVXPY's new density-based conversion behavior. Sparse factorization
ordering, pivoting, or related effects inside IPOPT/MUMPS remain hypotheses about
the mechanism; no derivative-value defect has been demonstrated.

`dense_route_control.py` is ready to run the new compatible dependency pair with
the one process-local density setting disabled. It retains the historical model,
full x0, numerical options, original acceptance audit, source/binary checks, and
iteration cap. It uses the existing verified solve harness and accepts `--dry-run`.
The native attempt tested whether restoring the old sparsity representation
restores convergence, and it did. No package file was patched.

Do not choose a project-wide monkey patch based solely on this control. If the
setting is causal, the choice for production should weigh a temporary supported
legacy stack against a narrowly documented compatibility mechanism and upstream
guidance, followed by repository validation.

## Evidence

Raw outputs are in
`results/hour6047_environment_reproduction/oracle-investigation/`:
`a-02`, `b-01`, `b-dense-01`, `source-inventory.json`, and `source-diffs/`.
`a-01` retains an initial diagnostic serialization failure before native entry;
it is not a solver attempt. No native optimization occurred in these captures.

`inspect_historical_oracles.py` installs a fake native problem, evaluates CVXPY's
actual oracle object, and exits via a dedicated sentinel. It reuses the unchanged
verified preparation/solve harness. Its phase label `before_native_solve` refers
to entry into the fake object for this diagnostic; it is not evidence of IPOPT
execution. The `native_optimization_run=false` oracle record is authoritative.

Compact cross-environment comparisons are in
`artifacts/provenance/oracle_comparison.json`. `reproduce_sparse_dispatch.py` is
a standalone no-solve demonstration with a dense 24-by-24 identity matrix and
a 24-by-3 variable: stored Jacobian entries are 1,728 / 72 / 1,728 for old,
new-default, and new-density-disabled cases, while the numerical Jacobian is
exactly the same identity. The analytical gradient and Jacobian checks passed
in all three cases. This small example does not reproduce OPF nonconvergence.

Communication drafts: `UPSTREAM_ISSUE_DRAFT.md` and `COLLEAGUE_EMAIL_DRAFT.md`.
Neither has been submitted or sent. Project stabilization choice is pending the
owner's answer; the main environment has not been changed.
