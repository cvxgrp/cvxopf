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

### Single reviewed validation-only source transition

The intentional pause after 505 accepted intervals is the sole supported
cross-version exception. `S5_SOURCE_VERSION_CONTINUATION.json` remains a frozen,
nonexecuting proposal binding the original `41abf63` authority, literal stopping
checkpoints, and the `8a49e92` validation-cost correction. It is not changed into
an execution authority in place. See `S5_VALIDATION_CONTINUATION.md`.

After the resume-support implementation is committed and independently reviewed,
prepare a separate reviewed contract with the exact clean support commit and S5
source fingerprint, unchanged scientific/environment context, and explicit
`review_status="reviewed"`, `launch_authorized=true`, and
`classification="reviewed_s5_source_version_continuation"`. Its successor
numerical authority must retain all frozen limits and include
`source_version_contract_sha256`, the canonical object digest of that contract.
Neither a descendant commit nor another source change is implicitly allowed.

The root requires `--reviewed-continue --source-transition PATH` for initial
publication. Before any worker launch it verifies the frozen stopping artifacts
and full accepted window chains, then atomically publishes one immutable
`source-version-transition.json`. That record holds the reviewed contract,
both authorities, a publication timestamp, and literal JSON snapshots of the
old progress and checkpoints. A failed publication leaves the old pointers
unchanged; an identical retry may reuse the record. Later same-successor retries
may discover this retained record automatically but still require reviewed
continuation. Different contracts or successor contexts are rejected.

Workers must preserve the exact old window-registry prefix and its physical
state chain. Before the first newly accepted interval, the original checkpoint
must match exactly; after advancement, its source field describes the successor
worker, while the immutable transition retains the old prefix attribution.
The original run context, authority, windows, logs, and supervision are not
rewritten. Final analysis verifies incomplete as well as completed segments,
retains both contexts and the transition identity, and sums supervision time
and resource evidence across both invocations. The deliberate stopped period
is not counted as active supervisor runtime; the old supervision timestamp and
transition publication timestamp remain available separately.

This does not permit changing the numerical model, solver, acceptance gates,
timeout policy, storage state, manifest, concurrency, or resource ceilings.

### One reviewed interval-2448 operator intervention

After the validation-only continuation, shard 003 stopped at interval 2448
following a primary timeout and an exceptionally long unsuccessful target-free
solve. The owner separately reproduced causal perturbations 6–8 against the
exact stopped state. Slot 8 passed the unchanged hard-target acceptance gate.

The exact accepted diagnostic result may be promoted once through the reviewed
contract described in `S5_INTERVAL_2448_INTERVENTION.md`. The transaction binds
the stopped live evidence and complete diagnostic evidence, publishes the
immutable interval archive first, and advances shard 003's checkpoint exactly
once. Bypassed slots are labeled as operator-bypassed; they are not rewritten
as solver outcomes. Final audit retains the live timeout, interrupted recovery,
diagnostic timings, selected slot, and source-version boundary separately.

This narrow intervention does not alter the ordinary nine-slot lifecycle for
any other interval and does not authorize another operator-selected result.

### Continuation evidence and abnormal outcomes

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

On subsequent source-version restarts, exactly bound predecessor supervision
remains valid history, but only matching successor supervision can attest to
a completed successor worker. Historical evidence is resolved within the
supplied results directory, including when that directory is copied or restored;
the original repository output location is not required for reconstruction.

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

## Current stopping point

The annual run is paused at interval 2448 with its verified checkpoint and
supervisor-interruption evidence intact. The interval-2448 intervention is an
implementation-only checkpoint: it does not create its successor numerical
authority, apply the diagnostic action to the live checkpoint, restart a worker,
promote a result, or open S6/S7. Those actions require a clean implementation
commit, independent review of the exact transition/authority identities, and a
separate owner-approved continuation.
