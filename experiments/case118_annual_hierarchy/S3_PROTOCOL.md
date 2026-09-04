# S3 one-month hierarchy protocol

This protocol is frozen before any S3 OPF execution. S3 extends the accepted
S2 week from the same starting boundary and applies the separately reviewed
`recycle_every_16` worker policy.

## Scientific scope

S3 executes one 720-interval hierarchy on the converted rated PGLib-OPF
Case118 case. The global annual-profile slice is `[3744, 4464)`, or
`2025-06-06 00:00 UTC` through `2025-07-05 23:00 UTC`. Its active-load,
reactive-load, and renewable arrays have S2 as an exact 168-interval prefix;
no new operating window was selected after observing S2. This is only an
exogenous-input prefix check. S3 has a 720-hour outer horizon and imposes its
global terminal equality at boundary 720 rather than 168, so its outer
signposts and first-week executed actions may legitimately differ from S2 and
are never checked against the S2 trajectory or outer plan.

The scenario remains unchanged:

- 15% annual available renewable energy;
- aggregate storage power equal to 5% of annual peak load;
- four-hour storage duration;
- storage at buses 41, 65, 89, and 105, initially at 50% SoC;
- one global equality obligation returning every device to 50% at boundary
  720;
- fixed nonsheddable active and reactive demand; and
- the rated PGLib network with enforced AC `rateA` limits.

`s3_fixture.load_s3_fixture()` is the only materialization path. It freezes the
case, sliced load and renewable arrays, input fingerprint, device identities,
timestamps, policy hash, solve-configuration hash, and combined scenario hash.

## Controller and solver contract

S3 reuses the exact P0/S2 controller and solver configuration:

- one frozen 720-hour lossy-DC outer plan;
- three-hour AC windows with final truncation;
- hard-equality inner signposts;
- causal shifted initialization and the complete nine-slot recovery sequence;
- execution of only the first action from the first accepted controlling
  attempt;
- CLARABEL for the outer layer and IPOPT for AC; and
- the unchanged M17 accepted-status and residual tolerances.

No target rounding, soft fallback, load shedding, archived-trajectory warm
start, or change to perturbation scales or seeds is permitted. Recovery
exhaustion retains the complete failed window and stops without advancing the
physical state.

## Frozen worker-recycling schedule

`recycle_every_16` is expressed only in global completed-interval coordinates.
Planned recycle boundaries are `16, 32, ..., 704`: exactly 44 restarts and 45
worker invocations. Boundary 720 is `study_complete`, not a restart.
Invocation-local counters never reset or reinterpret the global schedule.

At each planned boundary the worker exits normally only after publishing and
verifying the complete window, resource chunk, and checkpoint. The supervisor
then starts a fresh process from that exact checkpoint and immutable outer-plan
artifact. The first invocation constructs and archives the 720-step outer
plan; later invocations verify and load it without rebuilding or overwriting
it.

The recycling comparison characterized only the later load-and-resume worker
path: all of its workers loaded an existing outer artifact. S3 invocation zero
instead constructs the 720-step outer plan in-process before beginning AC
execution. Its memory profile is separately observed under the same external
24 GiB limit and is not assumed to follow the comparison's recycled-worker
measurements.

An abnormal resource, solver, artifact, or provenance outcome stops automatic
execution and retains the partial record. It requires explicit review. A
reviewed continuation may resume the same frozen study from the verified
checkpoint, but no abnormal outcome automatically changes cadence or launches
an unscheduled worker.

## Resource authorization

The S3 supervisor enforces:

- 24 GiB child-process current RSS;
- 60 minutes without a newly verified safe checkpoint;
- four hours per worker invocation;
- 72 hours cumulative across every invocation and reviewed continuation; and
- RSS/checkpoint polling every one second.

Crossing a limit produces an explicit resource classification and never
promotes incomplete work. The cumulative clocks do not reset at planned or
reviewed restarts.

## Required record and analysis

Every outer plan, window, attempt, resource sample, checkpoint, worker result,
and supervision record follows the reviewed streaming archive contract. The
tracked S3 result must retain provenance and artifact integrity identifiers,
the complete restart/classification sequence, per-invocation and
global-interval-indexed RSS evidence, restart-to-first-checkpoint timings,
recovery use, and exact-once trajectory accounting.

Independent reconstruction must verify:

- complete interval coverage and exactly one controlling action per interval;
- storage identity, recurrence, signpost, and final 50% terminal obligation;
- the complete M17 AC acceptance gate for every controlling attempt;
- requested-versus-served fixed load and zero ENS;
- exact-once generation cost, storage cost, curtailment, active losses,
  throughput, voltage, thermal, and signpost summaries;
- causal predecessor and actual-start integrity across all 44 restarts; and
- immutable outer, window, resource, checkpoint, supervision, and provenance
  chains.

S3 is observational evidence for this frozen month, scenario, network,
machine, and solver stack. Completion does not establish annual reliability or
a universal recycling cadence.

## Advancement gate

S3 advances to S4 only after explicit review of the independently reconstructed
record. Automatic execution success is necessary but not sufficient. A partial
trajectory remains a valid resource or solver-boundary result but does not
silently authorize the annual outer study.
