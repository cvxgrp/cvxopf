# Retained-plan prepared AC rollouts

Implementation checkpoint, 2026-10-12. No numerical qualification or OPF run
has been launched. Owner review, commit, explicit compute ceilings and separate
launch approval remain required. Existing study evidence is not rewritten.

## What is implemented

The six retained cases and four SoC sources form 24 arms. Each arm executes
one independently audited first action of a three-hour full AC window, then
uses realized SoC in the next window. The final horizons are two and one hours.
Only hard endpoint SoC is prescribed; donor battery powers are never pinned.
At every window ending at boundary 24, use the original exact final obligation.

The private `_ac_rollout` contracts own immutable signposts and accepted-action
state transitions. `_prepared_rollout` reuses the existing hierarchy's window
slicer, storage API replacement and physical start transformations. Public M17
guards, defaults, result validators and source files remain untouched. Numerical
preparation, cost coordinates, verified actual IPOPT x0 and physical restoration
remain the production `OPFBuild.solve()` path. This is not a general M21 API.

Source admission checks finalized manifests and artifact hashes, original
classifications, receiving identities, reconstructed physical inputs and exact
source-use receipts. The three qualified E3+ donors remain qualified sources,
not retrospectively accepted solutions. AC reference arms receive only their
SoC signposts; their network points are not used as starts.

## Two primaries and one shared helper

Two primary lanes parallelize different arms. A third process is one globally
shared helper, not another arm. Reuse S5's `SpeculativeSupervisor`, `WindowRace`,
`SubprocessBackend` and archive-before-checkpoint transaction. At 300 solve-phase
seconds a primary becomes eligible for assistance, but keeps solving. A rejected
primary is eligible immediately. Helper eligibility and fairness are shared
across the two active arms, with at most three concurrent solver processes.

The extended S5 revision-2 ladder is reused unchanged within each assembly
phase:

| Invocation | Source slots | Solve-phase ceiling / prerequisite |
| --- | --- | --- |
| Primary 0 | 0 | No solve-time cap; cold first window, causal shift later |
| Bounded 1–3 | 6, 7, 8 | 300 s each; preceding accepted controller required |
| Bounded 4 | 1 | 300 s; target-free diagnostic from primary initialization |
| Extended 10 | 1 | Exact-start retry for a timed-out order 4; 1,800 s |
| Bounded 5–8 | 2, 3, 4, 5 | 300 s each; accepted target-free source required |
| Secondary 9 | Selected bounded hard-target source | Original-start replay without solve-time cap |
| Final 11–18 | 6, 7, 8, 1, 2, 3, 4, 5 | No solve-time cap; use a freed lane, skip secondary's start |

Perturbation scales and seeds come from the existing S5 policy. Secondary
selection uses complete normalized physical residual evidence, or the earliest
eligible timeout when such evidence is unavailable. It never resumes a final
iterate. The longer target-free retry remains diagnostic only. Final recovery
can use a returned primary's vacated lane while the shared helper stays leased;
this does not add a fourth process or stop a still-running primary.

First independently audited hard-target success wins; simultaneous audited
returns prefer the primary. Publish that winner, cancel/reap losers, then
advance exactly once. Every attempt gets a distinct arm/phase/window/invocation
identity, including repeated starts. No other arm's prediction can seed it.

Component-first is the first phase for every window. Only after its entire
race is exhausted may hourly assembly run the same physical window/target.
There is no state advancement between phases. After both phases fail, retain
that arm's prefix and unresolved boundary and let other arms continue. An
infrastructure or resource failure stops the whole run for review instead of
being counted as physical infeasibility.

## Read-only checks

```bash
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python -m experiments.tracy_ac_rollout.run --preflight
UV_CACHE_DIR=/private/tmp/cvxopf-uv-cache uv run --extra dev python -m experiments.tracy_ac_rollout.run --status --output experiments/tracy_ac_rollout/results/rollout_001
```

Preflight reconstructs the retained inputs, builds all 24 first windows and
captures their prospective prepared canonical x0. It creates no output directory
and does not call an optimizer. Status independently reconstructs completed
starts/native restoration/audits, supervision/resources, executed accounting
and the selected window chain; unsupervised candidates never become actions.
Read-only replay tolerates a later Git bookkeeping-only commit, but not changed
bound source bytes, physical inputs, numerical adapters or evidence.

Window audits reuse prepared E3 native/restoration/objective checks and the
original engineering-unit device, network, reactive, voltage and both-terminal
audits. Cycling slack remains advisory when explained. A complete trajectory
gets a separate original-obligation physical audit and forced-shedding witness
check. Cost/ENS are recomputed from executed first actions once, not from sums
of overlapping window objectives. Native solved and reconstructed realized SoC
remain distinct; the executed trajectory carries reconstructed energy. Coverage
completion and independently audited day completion are separate counts.
Partial prefixes are explicit and not compared as completed days. Continuous
full-AC cost/ENS differences and eligible retained fixed-transfer comparisons
are reported; historical comparisons are labelled as retained evidence, not
new numerical audits. Failed final trajectory audits remain visible;
no global-optimality or approximation-quality threshold is claimed.

## Future preparation and execution gates

After review and an owner commit, `--prepare --budgets <reviewed-json>` freezes a
new named directory under this experiment's ignored `results/`. The budget JSON
must explicitly contain positive `max_launches`, `total_worker_seconds`,
`build_seconds`, `post_solve_seconds`, and optionally `poll_seconds` (default 1).
There is no inferred launch/worker-time allowance. Build and post-return ceilings
are infrastructure stops, not deadlines on a continuing primary solve. Failed,
diagnostic, canceled and accepted contenders all consume the global ceilings.

RSS/admission remain S5's reviewed 16,384 MiB per worker, 24,576 MiB aggregate,
8,192 MiB helper reserve and 22,528 MiB pressure threshold. Process-tree and
RSS monitoring must work before any launch and remain available during the run.
Thermal-sampling failures are recorded as advisory alerts, not reasons to stop
a solve or cancel a peer. Battery power is timing context only. Native IPOPT controls and the 3,000
iteration default remain those of prepared E3; no tolerances/gates are retuned.

`--run` requires an independently reviewed authority JSON with exactly:

- `schema: 1`, `experiment: "tracy_ac_rollout"`, `execution_authorized: true`;
- the prepared `binding_sha256` and exact clean `execution_commit`;
- `primary_workers: 2`, `helper_workers: 1`, `maximum_solver_processes: 3`;
- `recovery_policy: "causal_first_speculative_v1"`, `recovery_revision: 2`;
- `budgets` identical to the prepared binding.

No authority record is created by preflight or preparation. Standalone worker
invocation requires that authority, a supervised launch PID and the bound
request. A previously attempted output cannot be rerun or overwritten. After
interruption, status exposes any durable-but-not-advanced winner; a separately
reviewed continuation/reconciliation is required. There is no automatic resume
that could duplicate a completed solve or silently elect a different winner.

Numerical seam qualification, engineering comparison thresholds and launch
budgets remain owner decisions. Source E3/E3+ qualifications and historical
Stage D/annual execution holds remain unchanged.

## Explicit telemetry-stop continuation

After the owner-authorized operational fix and clean commit, use
`--prepare-continuation --output <original-root>` to independently replay the
stopped predecessor and freeze all inherited immutable hashes, exact checkpoints,
consumed attempt count and supervised effort. The only admitted predecessor is
the original finalized thermal-telemetry stop, with no live workers or pending
durable winners. This does not create execution authority or launch a solver.

Create the matching reviewed `continuations/continuation_001/authority.json`,
then invoke `--resume --output <original-root> --authority <that-file>` once.
New attempts and outcome/monitoring records live under that generation, while
the original binding, authority, stopped outcome and attempt bytes are untouched.
Original arm checkpoints advance only through the existing audited exact-once
transaction. The new clean context and authority are bound separately from the
old scientific input binding. Read-only historical comparison allows only the
declared operational runner/documentation edits; all physical, preparation,
solver, input, audit and environment dependencies remain checked.

Completed controllers are restored as source lookups, never rerun. Interrupted
unpublished attempts remain censored, and any fresh attempt has a distinct
generation/path identity even when its window/slot is repeated. All inherited
attempt reservations and worker-seconds count against the original 2,304-launch
and 172,800-worker-second ceilings; two primaries and one shared helper remain
unchanged. A continuation already attempted cannot be launched again. Status
combines original and continuation evidence and uses the latest generation's
outcome, not the historical stopped marker.
