# Tracy AC cost-coordinate diagnostic

Owner-authorized on 2026-10-07: test cost-valued coordinates without writing
an explicit epigraph or changing the physical problem or economic weights.
This is an experimental diagnostic, not production qualification or a default
change. Historical qualification and E3 evidence are read-only; Stage D stays
held.

## Matched matrix

Use the existing Tracy three-hour energy-neutral fixture, global input hours
`[1165, 1168)`, with the same device identities, hourly inputs, initial and
terminal SoC, and deterministic physical initialization as qualification calls
024 and 025. Run four fresh attempts, in this order:

1. Unprepared AC, original coordinates.
2. Unprepared AC, cost-valued coordinates.
3. Combined device-limit normalization and exact-fixed elimination, original
   coordinates.
4. The same combined preparation, cost-valued coordinates.

For strictly positive battery aging weights `w`, substitute
`u = delta * w * b`; replace integrated cycling cost with `sum(abs(u))`.
For strictly positive load demand and shedding weights `c`, substitute
`v = delta * c * demand * shed_fraction`; replace integrated shedding cost
with `sum(v)`. All existing physical constraints are copied by exact affine
substitution. No physical equality or operating set is added or removed, and
CVXPY remains responsible for absolute-value canonicalization. Restore physical
coordinates before extraction and physical auditing. Unsupported zero scales,
other objective terms, or non-vectorized inputs fail before solving.

Generator costs and all solver controls are unchanged. Use the six explicit
IPOPT controls in `numerical_preparation.fixture.solver_options('ac')`, verbose
logging, no warm start, one numerical thread, no retries or initialization
search. Each process is bounded by 180 seconds and sampled 16 GiB RSS; at most
four launches and 720 cumulative worker-seconds. Existing supervision, resource
sampling, STOP handling, atomic archives, and the verified IPOPT x0 boundary
are reused. A timeout/rejection is not evidence of infeasibility.

## Evidence and checks

Before execution, record HEAD **and exact working-source hashes**, installed
numerical package/binary hashes, this protocol, physical-input hashes,
historical manifests/hashes, and each arm's transformed coordinates and
settings. The owner has authorized this quick experiment before a commit:
an uncommitted workspace is explicitly recorded, never called a clean-commit
run. Sources must remain unchanged while workers run. Raw records go into a
new, never-overwritten ignored directory. No historical run is resumed.

Validate objective and physical-constraint equivalence without optimization,
including both retained AC dispatches and nonzero synthetic device values.
Retain full canonical x0/layout, native primal/multipliers/objective, physical
starts/restoration, named costs, original-unit audits, logs, and supervision.
Independently replay archived primals and completion hashes without solving.
Report canonical-versus-physical cost, cycling epigraph excess, maximum slack,
IPOPT convergence, constraint residuals, physical dispatch, fleet and
device-aligned trajectory changes, elapsed time, RSS, and objective scaling.

Keep physical feasibility and economic accounting separate. The prospective
accounting comparison is `1e-4 + 1e-6 * abs(physical_cost)`; historical acceptance
is not reclassified. Passing that check alone is not an optimality certificate.
No solver settings are adjusted after seeing results. No automatic execution
extension, production edits, commits, or promotion follows this diagnostic.

Preflight must verify process/RSS access, AC power, and one macmon thermal
sample in the same execution environment. Obtain sandbox permission before
launching if necessary. Complete independent scientific review before execution
and review the resulting analysis before handing it back to the owner.
