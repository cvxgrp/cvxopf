# Selected segment escalation

This opt-in report-only interface selects exact global hours and complete
five-/six-bus loops near explicitly specified buses or branches. Routine
three-/four-bus checks and their positive-export trigger remain unchanged.
No OPF solving, dispatch repair, constraint or tolerance change, or historical
artifact write is permitted. Results are printed to stdout, never saved over
an existing checkpoint.

## Selection semantics

Require nonempty global-hour indexes within the original source horizon.
Deduplicate and sort them. Preserve the full battery schedule and all input
frames. For global hour h in a horizon beginning at s, use the original
schedule and frame offset h-s, not the position in the selected-hour list.
Do not reset initial SoC, crop the horizon, or rebuild a shortened OPF model.

Focus seeds are external MATPOWER bus IDs and/or zero-based original active
branch-row indexes. A selected branch seeds both physical endpoints. Row IDs
are not indexes into the active-only table. Reject unknown buses, negative or
out-of-range rows, inactive rows, invalid hours and negative hop radii rather
than widening or clipping the request.

Expand seeds by the requested nonnegative number of active undirected corridor
hops, default zero. Parallel members count as one corridor hop; transformers
also count as one hop. Select complete five-/six-bus cycles containing at
least one bus in this neighborhood. The rest of a selected cycle may lie
outside the neighborhood. Bus and branch seeds form a union; selecting a
branch does not require every selected loop to traverse that exact row.

Only the list of loop work is reduced. Retain the entire physical network,
every separate parallel member, original transformer data, loads, generators,
battery pins and time series for the necessary cut calculations. Never turn
the neighborhood into a reduced physical network or fabricate boundary flows.

Record requested hours and offsets, seeds, expanded neighborhood, selected
cycles, omitted hour/cycle counts and upper bounds on computation. No nearby
cycles is an explicit empty selection, not a certificate of feasibility.
Loops/hours omitted by the selector are unexamined, not passed.

## Preview and execution

The CLI requires one retained transfer job, exact hours, focus seeds and a
nonempty escalation reason. It previews by default. Preview performs retained
identity/audit replay and topology selection only: no interval proof searches
and no OPF solves. `--run` explicitly enables the selected interval checks.
`--schedule ac-control` uses the accepted AC schedule for the selected job's
scenario, rather than its donor schedule.

Execution inherits the frozen forced-subdivision and multigraph mathematics.
Bypass the positive-export trigger only for selected long-loop/hour pairs.
Keep 64 visited boxes per dispatch search, including its root; static geometry
root checks are cached separately. Report actual/reused/logical visits and all
selected dispositions, including unsupported geometry. Never infer whole-
schedule compatibility from a selected segment result. Any exclusion must
cover the entire root voltage box for that selected loop/hour; other outcomes
remain unresolved. Contradictions with retained accepted witnesses are fatal
diagnostic failures, not reasons to relabel the witness.

The CLI replays exactly one chosen historical transfer without optimization,
preserves native rejections/timeouts, and verifies historical hashes plus every
pre-existing analysis file before/after. It does not invoke the 18-transfer
batch driver or launch numerical workers. Physical pins, SoC/dynamics/endpoint
audits, source E3/E3+ qualifications and historical Stage D stay unchanged.

Synthetic and mocked no-solve tests verify exact hour offsets, scope, branch
identities, unsupported/empty results, full-network retention, proof equivalence,
and witness/provenance guards. Routine defaults and immutable checkpoint hashes
must remain intact. This remains a necessary-condition diagnostic, not a
complete infeasibility solver, acceptance gate or global-optimality claim.
