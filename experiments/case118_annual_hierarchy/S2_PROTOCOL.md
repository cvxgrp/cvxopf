# S2 one-week hierarchy protocol

This protocol is frozen before any S2 OPF execution. S2 is the first
scientific use of the P0 streaming runner on the rated Case118 network.

## Scientific scope

S2 executes one 168-interval hierarchy on the converted rated PGLib-OPF
Case118 case. It asks whether the frozen controller can complete a continuous
week with exact storage-state handoff, fixed nonsheddable demand, enforced AC
`rateA` limits, bounded memory, and auditable causal recovery.

The interval is global profile indices `[3744, 3912)`: `2025-06-06 00:00 UTC`
through `2025-06-12 23:00 UTC`. This extends the reviewed S1 day forward and
contains the S0/S1 selected six-hour opportunity window. The interval was
selected before sequential Case118 results existed. There is one rated-network
trajectory; the matched effectively-unlimited sensitivity remains outside S2.

S2 uses the first predeclared pilot point unchanged:

- 15% annual available renewable energy;
- aggregate storage power equal to 5% of annual peak load;
- four-hour storage duration;
- four electrically sited ideal storage devices, each initially at 50% SoC;
- one global equality obligation returning each device to 50% at boundary 168;
- fixed, nonsheddable active and reactive loads; and
- the deterministic annual load, wind, and solar profiles.

`s2_fixture.load_s2_fixture()` is the only materialization path. It freezes the
converted case, all three sliced trajectory arrays, complete hierarchical input
fingerprint, device identities, timestamps, policy hash, solve-configuration
hash, and combined scenario hash.

## Controller and solver contract

S2 reuses the exact P0 policy and solver configuration:

- one frozen 168-hour lossy-DC outer plan;
- three-hour AC windows with final truncation;
- hard-equality inner signposts;
- causal shifted initialization with the complete nine-slot recovery sequence;
- execute only the first action from the first accepted controlling attempt;
- CLARABEL for the outer layer and IPOPT for AC; and
- the unchanged M17 accepted-status and residual tolerances.

No target rounding, soft fallback, load shedding, frozen-trajectory warm start,
or change to perturbation scales/seeds is permitted. Recovery exhaustion
archives the failed window and terminates without advancing state.

## Resource authorization

The authoritative run uses a fresh supervised child process. Limits are:

- 16 GiB child-process current RSS;
- 60 minutes without a newly published safe checkpoint;
- 48 hours total wall time; and
- supervisor RSS/checkpoint polling at intervals no longer than one second.

The supervisor may terminate the child during an outer or AC solve. The last
verified checkpoint remains the only resumable physical state; incomplete
work is never promoted. A resource crossing is classified `resource_limit`,
not solver failure or infeasibility. An unexplained child exit is
`worker_failure`. Resume is permitted only from verified artifacts under the
same clean execution-source commit, scenario hash, policy hash, and solver
hash. A run terminated by a declared resource limit is a completed S2 resource
boundary and is not silently retried into a different protocol.

The in-process safe-boundary observer independently requests termination when
current RSS exceeds 16 GiB or elapsed wall time exceeds 48 hours. This does not
replace the external supervisor; it provides a graceful path when a solve
returns before the parent must terminate it.

## Required record and accounting

Every outer plan, window, attempt, resource sample, checkpoint, and termination
record follows the reviewed P0 archive contract. A compact tracked result must
retain artifact names, sizes and SHA-256 hashes plus:

- execution commit, clean state, source fingerprints, scenario/policy/solver
  hashes, platform, and complete solver versions;
- completion status, interval coverage, controlling slot per interval, and
  recovery-use counts;
- construction/solve/retention timings and current RSS observations;
- realized SoC, executed storage power, and exact recurrence reconstruction;
- generation and cycling costs, curtailment, branch-terminal active losses,
  voltage and thermal maxima, and signpost deviation, counted from executed
  first intervals exactly once;
- storage throughput and final global-terminal deviation by device; and
- proof that fixed served load equals requested load and ENS is zero.

Predicted overlapping-window values and terminal penalties remain diagnostic;
they are never summed as realized operating totals. Outer objective values are
retained once and are not added to AC operating costs.

## Advancement gate

S2 advances to S3 only if all 168 intervals complete and independent
reconstruction confirms:

- continuous ID-aligned SoC recurrence and the final 50% equality obligation;
- no missing or duplicated interval and exactly one controlling action per
  interval;
- every controlling solve satisfies the complete M17 AC acceptance gate;
- no concealed load shedding or nonzero ENS;
- exact-once agreement for every published trajectory aggregate;
- verified artifact/checkpoint/resource chains; and
- no resource or provenance violation.

A shorter trajectory remains a valid, explicitly partial scientific record,
but it does not authorize S3. S2 makes no reliability claim beyond this one
synthetic week, one network, and one storage/renewable construction.
