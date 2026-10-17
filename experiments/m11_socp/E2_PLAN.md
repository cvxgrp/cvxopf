# Matched AC/SOCP evidence — bounded E2 execution plan

2026-10-01. E1 is committed at `5dd963bb8`. This slice implements and executes
the approved E2 gate only. No Tracy execution, hierarchy change, or automatic
M11 closure. No new public comparison API: reusable cases/checks belong in
`tests/`; this directory owns the runner, evidence and report.

## Frozen cases and matching

Four pairs: repository Case9 and Case14 single-step, plus the same deterministic
two-bus mixed-device T=3 case in vectorized and explicit stepwise assembly.
Standard cases use delta=1, voltage setpoints disabled and both-terminal ratings
enabled. All pairs use full admittances (`sparsity_tol=0`), identical original
case arrays, device objects, time series, identities, objectives and policies.
Reject caller coupling and any differing model input before building/comparing.
AC initialization and representation flags are not additional physics, but are
also held identical in this bounded comparison. No DC loss proxy.

Mixed case: reuse E1's resistive two-bus network with generator Pmax=50 MW;
delta=0.5 h, source voltage setpoint enabled, demand P=[20,90,30] MW and
Q=[2,9,3] MVAr at bus 2. Explicit load allows up to complete proportional
P/Q shedding at cost 1000/MWh. Renewable availability [8,0,4] MW, 10 MVA
inverter. Two ideal batteries at bus 2: (12 MVA, 12 MWh, initial 6 MWh,
hard final 6 MWh, aging 0.1/MWh) and (3 MVA, 4 MWh, initial 2 MWh,
quadratic final target 3 MWh, weight 7/MWh², aging 0.1/MWh).
HVDC from bus 1 to 2 has signed input box [-2,-1] MW, loss 3%, and
cost coefficients (0.2,0.1,0.01). This is a complete replacement load fleet.
The peak demand exceeds aggregate active supply capability, forcing nonzero
shedding; the hard battery must shift energy between intervals. No tuning to
obtain exact SOCP recovery is permitted or necessary.

## Solvers, budget and stopping

AC: IPOPT/DNLP, flat builder start, max_iter=500, max_cpu_time=60 s,
tol=acceptable_tol=1e-8. SOCP: CLARABEL, max_iter=200, time_limit=20 s,
tol_gap_abs=tol_gap_rel=tol_feas=1e-9. Use `OPFBuild.solve()` only.
Run each experimental pair in a fresh subprocess, parent timeout 180 s for
the entire pair including construction/analysis. No retries or alternative starts.
Maximum 24 new E2 optimization calls across experiment and new test execution
(12 per formulation, at most 960 s of declared solver limits). The planned
experiment spends eight calls; one targeted numerical test pass and the final
full suite may spend eight each. Existing regression tests retain their own
settings. Count failures too. Stop on a failed solve/audit/comparison, retain
the partial record/log, diagnose, and seek direction if a model/policy or budget
change is needed. Do not proceed to later experimental pairs after failure.

Before execution, retain Git HEAD, dirty state, exact reviewed-input/source
hashes (including uncommitted E2 sources), package versions, hardware and settings.
No source edits during the bounded run. Raw complete primals and logs are in a
fresh ignored `results/e2/` directory; compact evidence/report are tracked.

## Acceptance and reporting

Use existing SOCPAuditTolerances: power 1e-4 MW/MVAr/MVA, energy 1e-5 MWh,
voltage-squared/cone 1e-6 pu², fraction 1e-7; recovery rank/product/cycle
thresholds 1e-5, floors 1e-12. Solver success is necessary, not sufficient.
Independently evaluate AC voltages through direct complex admittances and
retain all device primal coordinates when lifting them. Both original AC and
lifted relaxation must pass physical/device audits; lifted recovery must preserve
that AC point up to an island angle gauge. Reconstruct objective components
numerically from inputs and primal with rtol=1e-8, atol=1e-6; delta multiplies
rates once and never the terminal cost. Lifted and original components must
match. Compare SOCP estimate <= feasible AC objective with allowance
`2e-5 + 2e-6*abs(AC objective)`; never clip a negative gap.
Mixed stepwise/vectorized SOCP objectives use the same tolerance, not identical
nonunique trajectories. Report shedding/ENS, physical AC-branch and HVDC losses,
bus shunts, all cost components, relaxation and recovered AC audits, rank and
cycle diagnostics. Require meaningful (>1e-3 MW) storage movement and
(>1e-3 MWh) shedding in both mixed formulations, not golden trajectories.

Record build, reduction-chain application, inclusive solve wall and available
native solve time separately. Chain-application timing is an instrumented subset
of solve wall, not a second solve or a complete solver-setup measurement. Missing
native timing/dual evidence is null, never zero. Record model dimensions and
available conic dimensions. This is not a speed benchmark. SOCP primal objective
is a numerical optimum estimate, not a certified lower bound; AC is only a local
feasible upper-bound candidate. Failed forest recovery does not prove AC
infeasibility. Complete tests and report, then stop for owner review.
