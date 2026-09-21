# S1 24-hour scaling protocol

This protocol is frozen before any S1 OPF execution. S1 characterizes the
first scaling step beyond the six-hour S0 boundary without performing a
monolithic 24-hour AC solve.

## Inputs and interval

S1 uses the authoritative S0 pilot point:

- 15% annual available renewable energy;
- aggregate storage power equal to 5% of annual peak load; and
- four-hour storage duration.

The common 24-hour interval is boundaries 3744–3768, corresponding to
`2025-06-06 00:00` through `23:00` UTC. It contains the S0-selected six-hour
window at boundaries 3757–3763. Rated PGLib and the matched, effectively
unlimited PGLib-derived network use identical exogenous trajectories and
device fleets.

Every outer storage unit begins at 50% SoC and has an equality obligation to
return to 50% at boundary 24. Loads remain fixed and nonsheddable.

## Predeclared record registry

S1 retains exactly these records for each network:

1. `outer_lossy_dc_24h`: construct, solve, extract, and independently audit
   the 24-hour lossy-DC problem.
2. `endpoint_ac_6h`: if the outer result is accepted, construct a six-hour AC
   endpoint-realization problem for global boundaries 3757–3763. Its initial
   and terminal SoCs are the outer plan's local boundaries 13 and 19,
   respectively. Solve from the project flat start and independently audit.
3. `direct_ac_24h`: do not construct or solve. Retain status
   `not_authorized_by_s0_resource_gate`.

The endpoint realization is a bounded hierarchy-component measurement, not a
closed-loop trajectory and not the P0 streaming runner. No first action is
executed and no trajectory operating total is inferred from it.

If an outer solve is not accepted, its endpoint record is retained as
`source_unavailable` and no AC problem is constructed. Records never disappear
in response to failures.

## Measurements

Executed records retain:

- Python construction, solve-call, and total wall time;
- available solver setup/solve time and iterations;
- scalar variable, equality, explicit inequality, other-constraint, and
  constraint-object counts;
- process RSS immediately before construction, after construction, and after
  solving;
- status, exception, required-field availability, device identity, and every
  independently reconstructed residual;
- objective and compact scientific summaries; and
- source commit, dirty state, source fingerprints, scenario hashes, software,
  solver, platform, and resource-policy provenance.

The parent supervisor samples child RSS at intervals no longer than one
second. Each network runs in a fresh child process so retained CVXPY/IPOPT
state from one network cannot contaminate the next memory measurement. The
atomic outer checkpoint marks the start of the AC phase for its separate
45-minute clock.

Before constructing its outer problem, each worker records its own commit,
cleanliness, runner and source fingerprints, scenario hashes, software and
solver versions, and platform. It records the same context again after its
last checkpoint. The parent freezes its context before launching either
worker and requires both worker snapshots to match it. A mismatch prevents
advancement even if numerical solve evidence was retained.

## Resource and classification rules

The S0 limits apply unchanged:

- 16 GiB child-process RSS;
- 45 minutes for any AC solve;
- two hours for the complete S1 execution; and
- direct AC authorized only through six hours.

At a child RSS or wall-time limit, the parent terminates the child and writes
an explicit `resource_limit` record with the last observation. This is not a
solver failure or infeasibility result. A process exit without a verified
child artifact is `worker_failure`.

If a complete accepted solve checkpoint exists when a later resource crossing
is observed, its numerical evidence remains unchanged, but the authoritative
worker classification is `resource_limit` and the worker is ineligible for
ordinary advancement.

The two 24-hour direct-AC slots are always
`not_authorized_by_s0_resource_gate`. S1 must not call a builder, CVXPY
canonicalization, or IPOPT for either slot.

Accepted primals use the unchanged M17 residual tolerances and complete audit
implemented by `audit_probe()`.

## Advancement gate

S1 advances to P0 when:

- both 24-hour outer problems have accepted independently audited primals;
- both bounded endpoint records are accepted or retain an unambiguous solver,
  source, or resource-boundary classification;
- both forbidden direct-AC slots are present and prove no execution occurred;
- all task artifacts and the combined summary are written atomically; and
- the result establishes which cost—construction, nonlinear solution, or
  memory—limits the next scale step.

S1 does not establish sequential controller equivalence. P0 remains required
before week-scale execution.
