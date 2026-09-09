# Case118 S5 annual hierarchical execution protocol

## Status and authority

**Pre-execution implementation checkpoint. Numerical execution is not
authorized.** S4b accepted the immutable annual shard manifest for S5 use, but
`S5_EXECUTION_AUTHORITY.json` deliberately authorizes planning and
implementation only. A separate `S5_NUMERICAL_EXECUTION_AUTHORITY.json`, absent
from this checkpoint, must bind a reviewed clean implementation commit and its
complete execution-source fingerprint before any worker or output directory is
created. That authority also binds the canonical twelve-shard, six-wave annual
registry digest; manifest-use authority alone cannot launch work.

S5 executes the frozen 8,760-hour Case118 hierarchy represented by
`S4B_SHARD_MANIFEST.json`. It does not alter the accepted S4 outer plan,
reselect boundaries, tune solver behavior, change the three-hour AC window or
one-hour stride, or weaken the hard-equality and M17 acceptance gates.

## Frozen annual schedule

The twelve manifest shards are executed in six ordered waves:

```text
wave 0: shard 000, shard 001
wave 1: shard 002, shard 003
wave 2: shard 004, shard 005
wave 3: shard 006, shard 007
wave 4: shard 008, shard 009
wave 5: shard 010, shard 011
```

Each wave requests two fresh worker processes. A later wave starts only after
both shards in the preceding wave have independently completed and passed
artifact reconstruction. Completion order within a wave is irrelevant. The
fixed order is an operational schedule, not a claim that adjacent shards have
an in-memory dependency: every worker uses only installed source, the immutable
manifest and outer inputs, its manifest entry, and its own checkpoint/output
directory.

The resource gates are the manifest's 16 GiB current-RSS limit per worker and
24 GiB simultaneous aggregate current-RSS limit. Process-tree sampling and
deduplication are identical to the accepted S4b demonstration. The supervisor
itself is reported separately. There is no additional hidden shard or wave
wall limit; every primary attempt retains the typed 300-second budget and the
unchanged causal recovery lifecycle.

## Persistence and abnormal outcomes

Every window archive is immutable and precedes checkpoint advancement. Every
completed shard has one immutable result. Every wave retains an immutable
supervision record with process identities, logs, current-RSS/CPU samples,
resource triggers, return codes, worker results, and artifact checks. The root
progress record is atomic and binds the ordered wave records and verified
completed shard results.

Each zero-exit worker is audited independently, including when its peer fails
or the wave crosses a resource boundary. Its complete worker result remains
bound to that supervision record. Such a completed peer can count toward annual
coverage after reviewed continuation; the abnormal wave classification remains
in the record. Final merging uses the reconstructed scientific summaries, whose
hashes exclude the worker's additional process and execution metadata.

Resource crossings, worker failures, supervisor failures, interruptions,
provenance mismatches, and artifact failures stop automatic execution before a
later wave. They retain a partial scientific record and never change shard
boundaries, concurrency, timeout policy, or recovery behavior. Continuation
requires an explicit reviewed flag, exact authority/source/checkpoint
validation, and an immutable continuation record. A completed shard is never
rerun; only incomplete members of the stopped wave may resume.
Before recording continuation or launching a worker, the runner reconstructs
every completed shard before the retained wave coordinate and any completed
peer in that wave. Missing, rejected, or provenance-mismatched prefix evidence
prevents continuation.

A retained completed result must also match the worker payload in a durable
supervision record with its zero exit, process identity, log hash, and exact
root authority/context. If a crash occurs after result publication but before
supervision publication, or the publishing worker exits nonzero, continuation
rejects the unbound result before writing authorization or launching work. The
result and window archives remain intact. Recovery requires restoration of
matching retained supervision evidence or a separately reviewed audit-only
reconciliation; this implementation does not manufacture process evidence or
automatically rerun the completed shard.

Catchable `SIGTERM` and `KeyboardInterrupt` terminate and join live worker
process groups before the root partial record is published and the
interruption is reraised. Uncatchable process or host failure is recovered only
from immutable shard/checkpoint/supervision evidence and requires review.

## Independent reconstruction and promotion

The analyzer independently verifies:

- the manifest-use and numerical authority records;
- the historical execution commit, source fingerprint, clean context, fixture,
  outer, policy, solver, manifest, and S4b qualification identities;
- all twelve shard intervals, boundary states, window/archive/checkpoint
  chains, controlling attempts, hard targets, and complete M17 AC audits;
- the six-wave chronological lifecycle, requested and observed concurrency,
  process-tree resource samples, trigger priority, return codes, logs, and
  worker-result bindings;
- deterministic merge rejection/ordering and exact annual coverage;
- additive costs, losses, curtailment, throughput, timeout/recovery counts and
  timings, plus extremal residuals and resource peaks.

Every wave's authority and execution context must equal the root context.
Resource triggers are derived from the retained samples: a crossing cannot be
omitted, and a declared crossing must reproduce its sampled value. Completion
coverage counts independently verified, zero-exit shard outcomes across all
waves, including retained successful peers in reviewed abnormal waves.

Partial analysis is printable but cannot occupy the tracked destination.
`S5_RESULTS.json` is written canonically and immutably only when all twelve
shards, all six waves, the annual merge, provenance, resources, and independent
scientific audits pass. The promoted result is the S5 scientific record; raw
window and supervision artifacts remain ignored but hash-bound.

## Stopping point

This checkpoint implements and tests the annual authority gate, worker scope,
fixed-wave supervisor, reviewed continuation, deterministic merge, independent
analysis, and complete-only promotion. It does not create numerical authority,
execute a shard, create the default S5 output directory, promote a result, or
open S6/S7.
