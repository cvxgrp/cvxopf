# Bounded public SOCP builder/diagnostics checkpoint

Scope: Case9, Case14, and deterministic synthetic branchless, two-bus, and
triangle fixtures; horizons 1 and 3 only. No private data or Tracy execution.

Numerical contract: CLARABEL, `max_iter=200`, `time_limit=20` seconds per solve.
At most 60 SOCP test/evidence solves for this checkpoint, hence a 1,200-second
solver-time ceiling. Stop on a failed check, diagnose it, and rerun only the
affected bounded checks; do not enlarge cases, horizons, or solver budgets.
Existing AC/DC regression tests keep their established settings. No additional
AC optimization experiment is authorized by this checkpoint.

Audit tolerances are the declared `SOCPAuditTolerances` defaults: power 1e-4
MW/MVAr/MVA, energy 1e-5 MWh, squared voltage and SOC violation 1e-6 p.u.^2,
shedding fraction 1e-7; recovery normalized determinant 1e-5, voltage-product
error 1e-5 p.u.^2, cycle angle 1e-5 radians. Normalization and phase floors are
1e-12. Solver status, relaxation feasibility, edge/cycle recoverability and
recovered AC feasibility are separate observations. No certified lower bound.

Positive/negative diagnostic controls are algebraic: known rank-one flat and
non-flat voltages, tight but cycle-inconsistent products, cycle-consistent but
slack products, and deliberately perturbed physical/device residuals. They
require no optimization. Extraction must never solve or call either diagnostic.

This checkpoint implements public wiring, extraction and diagnostics, not M11
closure. Full matched-pair comparison evidence and any bound-validation API
remain a subsequent gate; no AC/SOCP objective-gap claim is made here.
