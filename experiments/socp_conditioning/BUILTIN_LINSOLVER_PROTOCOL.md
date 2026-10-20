# Built in CLARABEL linear solver comparison

Declared 2026-10-06 before execution. Test the installed unmodified CLARABEL
0.11.1 wheel, explicitly selecting QDLDL and Faer, on the same surplus and
deficit prepared problems as `practical_lu_001`. Exactly four serial fresh
workers: surplus QDLDL, surplus Faer, deficit QDLDL, deficit Faer. QDLDL is
a contemporaneous control at the newly declared 1e-9 relative-gap tolerance.
Auto is not a separate numerical treatment; identify actual backend explicitly.

Constructor-only availability checks (no solve) show QDLDL and Faer available.
Panua reports unavailable; MKL is not enabled on this ARM build. Retain these
checks, but do not install libraries, seek licenses or run unavailable arms.

Retain normalized device cones, exact fixed-coordinate substitution, joint
canonical scaling, surplus fixed loads and deficit optional shedding. All
settings match the practical SuperLU check except `direct_solve_method`.
Relative gap remains 1e-9; absolute gap and feasibility remain 1e-10, reduced
tolerances 1e-10, minimum step 1e-8, max_iter 5000 and one thread. Acceptance
still requires native Solved and the unchanged physical/accounting gates.
The independent acceptance-policy decision remains pending.

Require exact native input equality against each practical-check payload,
except the named backend. Capture actual backend, full settings, native
primal/dual/slack, trace, public results, exceptions, resource measurements,
audits and timing. Compare attained costs and solution differences descriptively;
do not require coordinate equality in weakly identified directions.

Use only the authorized isolated worktree. No native patches or bridge calls;
no compensated arithmetic or experimental KKT scaling. Supervise every worker
at 180 seconds / 4096 MiB, one computational thread, and stop on process/resource
or provenance failures. Retain ordinary solver rejection and continue the
predeclared matrix without retries. Bind installed extension hash and version,
sources, previous artifact identities, results and logs under immutable
`results/builtin_linsolvers_001/`. Save a final report and update the index.
