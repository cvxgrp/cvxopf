# S5 causal-first speculative recovery

Date: 2026-09-14

Status: v1 activated and retained through the stopped interval-6122 boundary;
the persistence amendment below is implemented but pending independent review.
The original implementation was prepared from
`de78353e42b5c3e23f9789a69788ccc93823c44b` in an isolated worktree. This
document does not itself authorize a restart or execution-authority change.

## Decision and purpose

Replace sequential timeout-then-recovery, for a separately authorized future
S5 continuation, with `causal_first_speculative_v1`:

- Let the original hard-target primary continue after five minutes.
- Use one shared auxiliary solver lane to explore bounded alternative starts,
  beginning with perturbations of the shifted preceding accepted controller.
- Accept the first independently audited hard-target result, regardless of
  which lane produced it.
- If bounded alternatives are exhausted, replay one selected hard-target
  initialization without a solve-time cap while the primary continues, if it
  is still running.

The objective is practical time-to-accepted-controller improvement, not proof
of optimal scheduling. No target softening, physical-model change, objective
change, tolerance relaxation, or outer replanning is included. An uncapped
solve already exists in the current recovery procedure; the new elements are
its ordering and potential overlap with an uncapped primary.

The current [five-minute policy](FIVE_MINUTE_TIMEOUT_POLICY.md) and
[S5 protocol](S5_PROTOCOL.md), together with their retained continuation
records, continue to govern live execution until cutover. Do not edit their
historical meaning or relabel existing attempts as speculative attempts.

## Proposed interval-6122 recovery amendment (review pending)

The stopped interval-6122 evidence showed that the original five-minute
target-free attempt timed out, while the identical retained start returned an
accepted target-free solution in 86.31 seconds when retried after the stop. Its
copied hard-target solve then passed the unchanged acceptance gate in 128.71
seconds. This motivates two narrow persistence changes without stopping or
shortening the primary:

1. If bounded target-free order 4 times out, replay that exact start once with
   a 1,800-second solve budget while the primary continues. Acceptance unlocks
   the ordinary bounded copied/perturbed target-free starts.
2. Preserve the existing uncapped secondary replay chosen from the bounded
   hard-target evidence; it continues racing the primary. Once either uncapped
   contender returns unsuccessfully, use that freed worker for the ordered final
   source sequence `6, 7, 8, 1, 2, 3, 4, 5` without solve-time caps while the
   other contender continues. If both lanes become free, use both for distinct
   final attempts, with at most two final attempts active for the window. The
   shared helper lane still releases after each attempt and requeues behind older
   eligible requests from the peer shard; the vacated primary lane remains local
   to its window. Skip the secondary's exact source/start rather than solving it
   twice. If no secondary can be constructed, begin the final sweep immediately
   in the free helper lane. An accepted target-free retry remains a source only
   and unlocks later hard-target starts; do not allocate its dependent starts
   until that target-free attempt returns. The first accepted hard-target result
   wins. Resource and operator stops remain effective.

Every retry retains a distinct invocation identity, its original or newly
derived complete start, actual timing, result, and audit. The physical model,
hard terminal target, M17 acceptance gate, two-primary/one-helper concurrency,
and memory limits are unchanged. This amendment requires an independently
reviewed source/authority continuation before execution resumes.

## Frozen scheduling rules

### Primary and shared helper

Keep the existing two-shard wave schedule. The third lane is one shared
auxiliary solver process, **not a third shard**. At most three AC solver
processes may run concurrently, subject to the memory gate below.

The primary retains the current flat-at-shard-start / shifted-preceding
initialization. At 300 seconds on its solve clock it becomes eligible for
help; it is not killed or restarted. A primary that returns without an
accepted result becomes eligible immediately. Waiting for the helper never
stops an otherwise running primary.

Order helper requests by eligibility time, then shard ID and global interval.
Run one bounded helper attempt per turn, then requeue an unresolved window
behind already-waiting requests. Do not preempt a helper attempt for another
window. Cancel a queued request if its primary wins. Once an uncapped replay
starts, it occupies the helper until it returns, loses the race, or an
operator/resource stop occurs; v1 does not promise bounded helper waiting.

### Finite helper sequence

Every bounded helper solve gets 300 seconds. Use this order:

| Order | Start and problem | Prerequisite |
|---|---|---|
| 1–3 | Causal perturbations, scales `1e-4`, `1e-3`, `1e-2`; original hard target | Shifted preceding accepted AC controller |
| 4 | Target-free solve from the same named initialization as the primary | None beyond the primary input snapshot |
| 5 | Copy accepted target-free solution into original hard-target problem | Accepted target-free result |
| 6–8 | Target-free perturbations, scales `1e-4`, `1e-3`, `1e-2`; original hard target | Accepted target-free result |

Causal perturbations do **not** depend on a target-free solve. At the first
interval of a shard, no preceding AC controller exists: record orders 1–3 as
unavailable and proceed to order 4. Do not borrow a neighboring shard's
controller or introduce unplanned flat-centered perturbations.

Reuse the current `shifted_start` and `perturbed_start` transformations:
sorted named variables, Fortran-order flattening, additive Gaussian noise
`scale * max(1, abs(value))`, and destination leaf projection. Reuse the seed
formula in `streaming_schema.perturbation_seed`, using the historical source
slots 6/7/8 for causal and 3/4/5 for target-free perturbations. Explicitly
retain source slot, seed, scale, and actual new execution order; source-slot
numbers are not chronological attempt numbers in this policy.

A rejected, timed-out, or unusable target-free solve makes its dependent
copied/perturbed starts unavailable. It does not invalidate causal starts.
An accepted target-free result is only an initialization source: it can never
win the race or advance physical state.

### After bounded alternatives are exhausted

Start at most one uncapped helper replay. Select among previously attempted
hard-target helper starts, never the target-free problem itself:

1. Among returned candidates with complete finite physical variables and
   reconstructible residuals, minimize the maximum constraint violation
   divided by that constraint family's unchanged acceptance tolerance.
   Include network balance, bounds, coupling, and hard-terminal residuals.
   Missing/nonfinite evidence is not a zero residual. A candidate need not
   have accepted solver status to supply this ranking evidence.
2. Break ties by lower finite objective, then earlier helper-sequence order.
   Unavailable objective ranks after finite objectives. This is a heuristic
   for choosing an initialization, not a feasibility or convergence claim.
3. If no such returned candidate exists, choose the earliest timed-out
   hard-target helper start with a retained valid complete initial vector.
4. If neither exists, do not manufacture a candidate or enter an indefinite
   retry loop. Let a still-running primary finish; if it also fails, retain
   the unresolved window and stop that shard under the reviewed stop policy.

For v1, replay the selected attempt's **original initialization**, including
its verified complete IPOPT `x0`, with the solve-time cap removed. Do not
describe this as continuing its final or best iterate. Full `x0` capture does
not establish intermediate-iterate capture. Original-start replay avoids
making an IPOPT callback/iterate-export feature a prerequisite; the ranking
selects the start whose bounded trajectory produced the best available
endpoint evidence, without claiming that replay will reproduce it exactly.

No further automatic round follows an unsuccessful uncapped replay. A live
primary may still win; if all contenders have ended without acceptance, retain
failure rather than advancing state. Operator intervention remains possible
through an explicitly separate decision, not an implicit policy retry.

## Acceptance, clocks, and resources

All controlling candidates solve the identical current window: same physical
initial state, identity order, profiles, hard outer signpost, objective,
solver settings, and acceptance tolerances. Only initialization and the
declared time cap differ. Keep complete canonicalized `x0` capture and the
existing independent physical acceptance gate.

Use the existing solve-phase clock: from immediately before `build.solve()`
through its return, including canonicalization and complete-start verification.
The 300-second primary threshold requests assistance; the 300-second helper
threshold terminates a bounded helper solve. Record build/start-assignment,
solve, audit/archive, queue, cancellation, and total period time separately.
The deadline is supervised externally; retain actual deadline overshoot.
Once a completed result has been received by the supervisor, audit it rather
than discarding it solely because the next polling tick passed the deadline.
The `after_ac_solve` marker stops the solve clock even while the worker is
still extracting, auditing, or serializing. That post-solve work is not charged
to the solve budget; it remains subject to the existing memory checks. Report
return-to-reap post-solve overhead separately (including parent candidate audit
and polling delay). The primary's assistance threshold also excludes post-solve
processing time.

One coordinator owns winner selection. Commit the first result to pass the
full audit and immutable archival, ordered by coordinator acceptance event;
if two are ready in the same event batch, prefer primary, then helper order.
Do not wait for a lower objective. Publish one selected window and advance
the checkpoint exactly once. Preserve any other completed result as
unselected evidence. Terminate and reap the loser before releasing its lane
or starting replacement work. Recovery sources and nonaccepted iterates
never supply an action.

Keep the existing 16 GiB per-worker-tree and 24 GiB simultaneous aggregate
RSS ceilings; auxiliary processes and their descendants count, deduplicated.
The three-process limit is a ceiling, not permission to exceed memory limits.
Before helper launch, require current aggregate RSS plus an 8 GiB auxiliary
reserve to be at or below a 22 GiB pressure threshold. At or above 22 GiB,
cancel speculative work first and retain the interruption. These conservative
v1 operating settings leave 2 GiB below the unchanged aggregate hard ceiling.
The retained interval-2450 side diagnostic peaked at 5,061.16 MiB under its
8 GiB diagnostic limit; this motivates a reserve, not a guarantee that future
helpers will fit. A true hard resource crossing follows the existing retained
stop procedure. Never silently raise either ceiling.

## Artifact and reporting contract

Use a versioned attempt collection with source-role identity separate from
execution order and invocation identity. Do not squeeze concurrency and an
uncapped replay into the old fixed nine-slot chronology, or overwrite the
bounded attempt when replaying it. Reuse shared model construction, starts,
audits, archive utilities, and continuation handling; avoid event-specific
interval code and a duplicate physical formulation.

Retain enough to reconstruct the decision:

- exact problem/state/source identities and complete actual starts;
- eligibility, queue, launch, deadline, return, audit, winner, and cancellation
  events, with consistent monotonic durations and wall timestamps;
- solver statuses, available primals/residuals/objectives, and explicit
  unavailable, timed-out, resource-canceled, or lost-race outcomes;
- uncapped-selection scores, missing-evidence reasons, and chosen source;
- all consumed effort, including losing, interrupted, and replayed attempts;
- selected controller, archived result, and one-step checkpoint transition.

Record the new policy phase and each shard's actual first affected interval.
Keep earlier sequential-policy and operator-intervention records unchanged.
The analyzer must reconstruct both historical and new phases, including
partially completed periods, without counting overlapping solve times as
elapsed period latency. A crash after winner archival must reuse that winner
on restart rather than repeat the race or advance twice.

Report completion, accepted-source counts, helper use and waiting, total
period latency, solve effort, canceled effort, memory/concurrency, and
unresolved windows. Break out policy phases and special interventions. A
first-accepted policy can change the chosen local solution and subsequent
trajectory; compare scientific metrics as well as runtime.

The prior three-window replay supports the sequential five-minute rule on
those selected windows. The annual timing distributions demonstrate expensive
recovery and motivate this change, but do not identify uncapped-primary
counterfactual times. Parallel competition also causes resource contention.
Do not claim a population-average speedup, deterministic winner across runs,
or globally optimal solution from this policy design.

## Implementation and cutover sequence

1. Implement in a separate worktree while current S5 runs unchanged. Use
   the numeric memory-admission settings above and document the exact
   normalized residual mapping using the existing audit tolerances.
2. Test with simulated workers/clocks and retained artifacts: primary/helper
   wins, simultaneous returns, queue fairness, all causal starts unavailable,
   target-free rejection, each selection branch, timeouts, memory pressure,
   loser cleanup, and stop/restart before and after winner publication.
   Independently verify exact-once advancement and historical compatibility.
3. Test production construction/start/audit seams without launching numerical
   work. Do not add a new large numerical study as a prerequisite. Any small
   side replay needs separate authorization and must not consume unapproved
   live-run capacity.
4. Obtain an independent scientific/software review and owner commit review.
   Prepare and rehearse the source/policy continuation on copied records,
   including both root and worker/analyzer paths. No live pause to debug the
   continuation mechanism.
5. Only when ready and explicitly authorized, stop cleanly, capture the final
   actual checkpoints, bind the reviewed successor source and this policy to
   them, and resume. Use the existing continuation abstraction where possible;
   do not make a new chain of interval-specific patches. Preserve completed
   work and report downtime separately.

The implementation checkpoint must freeze the remaining residual-field
mapping; it is an engineering gate, not authorization to
change the causal-first race, physics, or acceptance rule. If that work reveals
a material policy change is needed, return it to the owner before activation.

## Deferred

Intermediate/best-iterate export and restart; adaptive perturbation scales or
budgets; multiple helpers; helper preemption/fairness for uncapped races;
automatic hard-to-soft fallback; a public package-level multistart API; and
controlled population-level throughput optimization. These are not required
to implement this bounded first version.

## Implementation checkpoint — scheduling foundation

The owner authorized implementation on 2026-09-14. The isolated worktree now
contains `s5_speculative_policy.py` and focused non-numerical tests. This first
slice implements invocation/source-slot separation, the causal-first finite
sequence, source availability, primary assistance eligibility, first-accepted
race selection, residual-based original-start replay selection, and a shared
helper queue whose lease is released only after process reaping. The score
utility requires a complete externally supplied residual-limit mapping; its
production full-box audit adapter is not implemented yet.

This module is not yet called by the live or isolated S5 runner. No numerical
authority, source registry, historical schema, or production default changed.

Remaining implementation checkpoints:

- Single-attempt worker and complete-start replay, using existing construction
  and audit functions; frozen full residual mapping and memory-admission values.
- Root/helper dispatch, external time/resource supervision, versioned attempt
  archives, durable winner selection, and exact-once state advancement.
- Historical/new-policy analyzer and continuation integration. Explicit seam
  tests must cover a speculative winner becoming the next causal source,
  completing a shard, passing the wave audit, starting the next shard/wave,
  and restart after winner archival but before checkpoint advancement.
- Copied-record cutover rehearsal and independent review before owner-managed
  commit/activation. A green scheduling unit suite is not a cutover-readiness
  claim.

### Single-attempt seam checkpoint

`s5_speculative_attempt.py` now prepares one invocation through the existing
window validation, model builder, shift/perturbation functions, and acceptance
audit. `streaming_runner.py` exposes the common window validation and opt-in
pre-native start-observer / complete-start replay hooks. A fresh reduction must
match the retained layout and named model values; retained auxiliary values
are restored explicitly. The start packet is written immutably before native
IPOPT entry, and an ordinary solver exception retains a separate result.

Speculative candidate artifacts deliberately omit the legacy record's
sequential acceptance-equals-action fields. Only a coordinator-selected
hard-target candidate can be detached as a `SelectedController`; it retains
its distinct invocation identity while reusing the existing physical source
representation. Construction tests cover its handoff to the next window,
copied target-free availability, and flat initialization at a nonzero shard
start. Synthetic accepted values test lifecycle semantics only; native IPOPT
is intercepted and these tests do not establish physical feasibility.

This is still an internal single-attempt seam, with no annual CLI dispatch.
External deadline supervision, build-free candidate reload/audit, durable
winner/checkpoint publication, new-policy shard/wave audit, source-version
continuation, and the memory/ranking adapters remain to be implemented and
tested before activation. Existing supervisor/continuation regression tests
are baseline protection, not evidence that new-policy wave completion works.

### Coordinator and durable-decision checkpoint

`s5_speculative_supervisor.py` now connects the scheduling kernel through a
typed process/artifact backend: at most two active shard windows and one
shared helper, helper-only solve deadlines, memory admission/pressure handling,
first-audited-batch winner selection, loser reaping, and checkpoint advancement.
The backend remains injectable; the real annual subprocess adapter is not yet
connected. A child start published during a parent polling tick is treated as
an ordinary concurrent event, not a false clock/provenance failure.

`s5_speculative_transaction.py` uses the immutable version-2 winner window as
the durable decision. It retains the prior checkpoint and window-prefix hashes
without embedding its own digest. Publication precedes pointer advancement;
restart can rediscover the pending winner from disk, verify the predecessor,
and apply it exactly once. Existing checkpoint schema/identity fields remain
unchanged, and the transaction stores constant-size predecessor metadata
rather than duplicating the entire growing checkpoint in each archive.

Simulated-worker tests exercise racing returns, deadline/return ordering,
shared-helper fairness, memory cancellation, uncapped replay, failure cleanup,
publication-before-cancellation-before-advancement, recovery after loss of all
supervisor memory, and next-interval completion through the existing checkpoint
validator. These do **not** claim that a version-2 window passes the existing
full shard/wave analyzer: that reader integration and its tests are still next,
along with the concrete subprocess backend and reviewed continuation adapter.

### Subprocess, archive, and continuation integration checkpoint

The isolated implementation now includes the real file-backed subprocess
backend and gated single-attempt worker. Each contender has its own process
group, command/request, phase record, complete canonical start, result/log,
and immutable reaping receipt. The coordinator retains helper eligibility and
unavailable-source events, uses the existing process-tree RSS sampler, and
never applies the five-minute deadline to a primary. No live worker uses this
code yet.

Candidate reload is build-free. It reconstructs the frozen M17 physical audit
and retains acceptance separately from selection. Accepted named solutions
can be restored as target-free or selected causal sources without a live
CVXPY object. The ranking-only residual map contains all existing AC audit
residuals (including terminal and SoC recurrence) plus generator P/Q boxes,
storage SoC bounds and apparent-power circle, fixed-load service equality,
and nondispatchable real-power/availability bounds, apparent-power circle,
and curtailment identity. Power residuals are divided by baseMVA and use the
existing active-balance tolerance; stored-energy bounds use the SoC recurrence
tolerance. Existing audit residuals retain their named tolerances. Missing
or nonfinite evidence remains unrankable. The additional ranking map does not
change the accepted-primal rule. HVDC and sheddable-load extensions are not
silently accepted by this Case118-specific adapter.

Version-2 windows retain the decision before loser cancellation. A separate
cleanup receipt binds every launched contender after reaping, then the
scientific window is validated before exact-once pointer advancement. A
controlled interruption after publication can reuse that winner and its
cleanup receipts; missing reaping evidence after an uncatchable process loss
remains an explicit cleanup requirement, never permission to repeat a winner.
Earlier version-1 windows retain their original reader and policy. New-policy
windows pass through the shared shard audit; parallel solve effort and canceled
effort are reported separately from launch-to-reap elapsed latency. Versioned
wave receipts enter the annual root's completed-peer and next-wave checks,
and the annual analyzer recognizes the distinct process topology.

The annual root selects this path only with the separately reviewed
`recovery_policy=causal_first_speculative_v1` authority, three-solver ceiling,
and matching source/policy continuation record. The ordinary sequential shard
entry rejects that authority rather than mislabeling sequential execution.
The source registry includes the implementation and this plan. Historical
execution fingerprints are not replaced.

`s5_speculative_continuation.prepare_record()` prepares a binding from copied
or stopped root/checkpoint records without writing anything. Its default is
non-executable. Final authorization requires an explicit argument, a stopped
run, and the clean successor context. The actual record and numerical authority
must still be separately reviewed and published at cutover; none exists for
this checkpoint. The proposal records exact old prefixes and first affected
boundaries, while later shards keep their manifest identities and schedule.

Verification uses real **non-solving** child processes, intercepted native
solver calls, synthetic accepted lifecycle fixtures, and retained completed S4
data. Tests cover candidate reload, target-free/controller distinction,
source handoff, archive-before-cleanup, shard completion, completed-peer wave
binding, next-wave refusal/acceptance, and read-only continuation preparation.
These tests are not a numerical performance or feasibility study. The remaining
pre-cutover work is independent review and a copied-record rehearsal of the
actual root/worker/analyzer continuation. Commit, activation, live stop/restart,
and numerical trials remain separately authorized actions.

Prior checkpoint verification: 228 focused/related regression tests passed, including
86 speculative-policy tests. Ruff, strict mypy over the 17 affected implementation
files, and whitespace checks passed. The isolated worktree's S4b regression tests
used a copy of the completed S4 outer archive, verified at SHA-256
`6e7d88e8eed39de4a0141b0fe3c8a146fd2ae298a3d3ddc2768ae57247e87031`;
no scientific artifact was regenerated. Timing interpretation remains explicit:
the new per-window span is launch-to-reap (excluding parent-only finalization),
the construction phase includes start preparation, and the wave elapsed clock
includes root orchestration. These are not interchangeable timing estimands.

### Integration review corrections

Full completed-shard reconstruction is deferred until all wave contenders are
reaped, including after a peer failure. The durable per-window archive and
checkpoint remain available immediately; full shard finalization cannot suspend
supervision of a running peer. A completed peer still receives its independent
audit and summary on a partial wave. No next wave can start before those audits.

The annual analyzer normalizes version-2 resource events into its common wave
lifecycle projection for complete and partial runs. Wave reconstruction checks
one shared helper lease, prior assistance eligibility against the primary's
retained solve/return evidence, and oldest-eligible queue ordering; it does not
infer those rules from the three-process ceiling alone.

Each wave retains a paired UTC/monotonic clock anchor in its progress, launch,
and cleanup receipts and final wave record. Together with the retained phase
timestamps this supports calendar-time reconstruction without file timestamps.
Monotonic differences remain authoritative for durations. Separate post-solve
overhead is retained per invocation and reconstructed into shard reporting.

Correction tests include a one-second helper solve with prolonged post-processing,
complete/partial annual analysis of new-policy waves, delayed full shard audits
after peer success/failure, invalid simultaneous helpers within the total process
ceiling, early assistance, and wall-clock anchor reconstruction. All are
non-numerical seam tests; live execution and authority remain unchanged.

Corrected checkpoint verification: **236 related tests passed**, including all
94 speculative tests. Ruff lint/format checks, strict mypy across the 17
implementation files, and whitespace checks passed. The existing CVXPY OpenMP
import warning was the sole warning. This remains an uncommitted, inactive
checkpoint for independent rereview, not an execution authorization.

### Copied-record rehearsal correction

The first copied-record rehearsal exposed a historical-finalization handoff:
completed shard 003's audit-only worker was valid under its original recovery-audit
transition but was incorrectly rechecked against the newest speculative-policy
transition. `worker_source_matches()` now locates the retained issuer by the
worker's finalization contract digest, then applies the unchanged exact context,
checkpoint, and source checks. Historical workers are not rewritten or re-issued.

Regression coverage includes successive continuations, missing historical issuer,
wrong worker context, and changed checkpoint. A separate non-numerical root test
consumes mixed legacy/version-2 wave schemas, exercises next-wave admission,
merges all 12 synthetic shard summaries, and passes annual analysis. Verification
after this correction: 107 focused tests, Ruff, strict mypy across the 17
implementation files, and whitespace checks passed. The copied-record root and
analyzer rehearsal remains separately retained; these synthetic tests do not
claim any new numerical trajectory or performance evidence.

The full copied-history analyzer then reached a second, report-only compatibility
gap: `_transition_summary()` treated the older recovery-audit continuation as a
base transition and expected its nonexistent `trusted_stopping_point`. Recovery-
audit and retry continuations now use the same summary shape with their distinct
record names and classifications. A full speculative → recovery-audit → retry →
operator/base chain regression verifies those paths and hashes. No numerical
acceptance rule, checkpoint, or historical artifact is changed by either fix.

Final non-numerical rehearsal disposition: **passed**. A private copy of the
actual six-shard history passed historical issuer lookup, all checkpoint/archive
chains, real root/new-policy wave admission, and full annual analysis. The
intentional stop after request preparation but before `Popen` produced a valid
version-2 partial-wave record; every copied checkpoint hash remained unchanged.
Analysis correctly remained partial and unaccepted for S6. Earlier real worker-
entry probes reached the model-construction boundary for both active shards;
no OPF model or solve was run. Simulated fixtures separately verified exact-once
advancement, next-wave admission, and complete mixed-schema root/merge/analysis.

Final verification: **108 focused tests passed**, Ruff lint/format, strict mypy
across 17 implementation files, and whitespace checks passed. No live run,
operational authority, commit, or restart was changed. The test-only successor
identity and authority in the disposable rehearsal directory must not be used
for cutover: the run-managing task must bind the actual final stopping checkpoints
to the actual committed execution context after owner review.
