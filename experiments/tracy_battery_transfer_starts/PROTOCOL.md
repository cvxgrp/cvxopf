# Alternative starts for the unresolved Tracy battery transfers

## Question and scope

Can alternative initializations produce independently accepted AC dispatches
for the three previously unresolved fixed battery schedules? This is a separate
recovery experiment, not a revision of the original single-attempt experiment.
The owner approved implementation of the initialization-only ladder. Execution
requires review of this protocol, an owner-created clean full commit and separate
launch approval; implementation approval is not numerical-launch approval.

The origin is `../tracy_battery_transfer/results/transfer_001`, bound to clean
commit `4fbd85f3bd80a573edac4da284e325804f1d77a3`. Require its completed invocation,
18 finalized supervision records, verified resource journals and completion
manifest hashes. Retain hashes of all historical attempt files and root records.
Do not edit, resume, relabel or replace any origin artifact. Original case 13
(SOCP) timed out; 14 (lossy DC) and 15 (copper plate) ended in native rejection.

Only these three donor cases participate: ordinary deficit-to-surplus,
energy-neutral leg, T=24, global input hours [2439, 2463). Use the same retained
E3 inputs, donor archives, storage identities and matched unrestricted AC estimate.
Require unchanged historical production sources and installed numerical binaries
at preflight; the new runner and protocol receive their own execution binding.

## Identical mathematical problems

Reuse the original transfer constructor and independent native-restoration audit.
Every battery real-power value remains equality-pinned, unrounded and unrepaired,
at every hour. Initial SoC, ideal dynamics, inverter ratings, capacity, load,
renewables, generation, network limits and objective remain unchanged. The battery
API continues to remove the redundant terminal equality; the original endpoints
remain independently audited. Nonbattery coordinates and battery reactive power
remain free within the original model constraints.

Every numerical call solves full AC with approved production normalization,
exact fixed-coordinate removal, cycling/shedding cost coordinates and
component-first objective assembly. Keep prepared E3 IPOPT settings unchanged.
No convex solve, hourly decomposition, target-free solve, unpinning, homotopy,
continuation, schedule repair, voltage/reactive penalty or gate relaxation occurs.

## Frozen seven-slot ladder

Process donors in order SOCP, lossy DC, copper plate. Each donor has these slots:

| Slot | Center | Relative perturbation scale |
|---|---|---|
| 0 | Fresh stock flat physical start | None |
| 1–3 | Retained matched AC start with donor b/implied SoC | 1e-4, 1e-3, 1e-2 |
| 4–6 | Fresh stock flat physical start | 1e-4, 1e-3, 1e-2 |

Generate flat values using the existing production physical-start helper after
clearing retained assignments. These are stock CVXPY/production starts, not a
new balanced power-flow solution. Replace b and all SoC boundaries with the
exact pinned schedule and implied trajectory in every center and trial.

Reuse the hierarchical perturbation helper's sorted historical logical names,
Fortran flattening, NumPy RNG and formula
`scale * max(1, abs(center_coordinate)) * standard_normal`. Use the registered
`ShiftedRecoveryConfig` scales and seed base 17000000. Freeze each seed as
`base + 100 * original_case_id + 10 * center_code + scale_index`, with
matched-AC code 1, flat code 2 and scale indices 1, 2, 3. These are retrospective
matched-reference diagnostics, not causal controller starts.

The shared helper ordinarily perturbs storage; restore b and soc unchanged in
both raw and assigned records before use. Restore structurally fixed generator
outputs, unavailable renewable real power and reference angle. Preserve leaf
attribute projection from the production helper. Other defining equalities need
not hold at initialization; no projection onto AC feasibility is claimed. The
production canonical handoff applies its existing exact fixed-coordinate policy.
Record battery-circle start residuals for the original matched center and each
assigned alternative separately, not as acceptance decisions. Preserve all
predeclared slot dispositions, including not-needed-after-acceptance and unlaunched
slots when a run stops.

Retain raw and assigned named starts, center/source hashes, complete prospective
canonical x0 and reduced coordinates. For completed numerical calls verify that
prospective x0 agrees with the actual preparation evidence. Every trial's
mathematical input, schedule, source, policy and original matched-start hashes
must match the origin binding. Bind all 21 prospective trials before launching.

## Supervision, stopping and acceptance

Run one fresh process at a time with one numerical thread. Approved launch
ceilings are 3600 supervised wall seconds and 16384 MiB sampled RSS per attempt,
21 launches and 75600 cumulative supervised worker-seconds. Preparation and
auditing consume the wall budget too. Require read-only process/RSS permission
and valid thermal telemetry before launch; reuse the existing supervisor.
Power source is recorded as timing context only: battery operation, or unavailable
power-source telemetry, does not prevent a launch or reject a result. This
owner-approved correction replaces the original mandatory AC-power gate.
Do not launch if process/RSS or thermal monitoring is unavailable. Resource samples corroborate process
liveness, and finalized supervision is required for acceptance.

Native rejection or timeout consumes a slot and advances to the next start.
Stop that donor on its first independently audited compatible result; subsequent
slots are skipped, not run for a better objective. Stop the experiment on audit
failure, infrastructure/monitoring failure, interruption, RSS ceiling, operator
STOP or cumulative-budget exhaustion. No automatic restart or resume is offered.
The explicit power-stop continuation below is the only authorized exception.
An unfinished or unsupervised candidate never advances or accepts the ladder.

Native status zero and the original physics, accounting, pin, implied-SoC and
endpoint audits are mandatory. Cycling gaps remain separately reported warnings.
Status reconstructs the declared graph/start and independently replays native
results and manifest/resource evidence without optimization or writes.

Report numerical attempts separately from three case outcomes, first accepted
slot, all failures/timeouts, partial artifact counts and supervised effort. Keep
original and recovery outcomes separate. Success establishes an operating estimate
for that exact schedule and demonstrates recovery with another initialization;
exhaustion does not certify physical infeasibility. Comparisons to matched AC
remain local model-conditioned estimates, not global optimality or truth claims.
Successful-first stopping is a feasibility diagnostic, not a multistart optimum
comparison. Source E3/E3+ classifications and historical Stage D remain untouched.

## Explicit continuation after the power-source stop

The owner authorized continuing the invocation that stopped between workers
because AC power was not detected. Use `--continue-from` and a **fresh** output
directory. Never alter the old binding, invocation records, logs or archives.
Hash every predecessor file, independently replay its finalized supervision,
completion manifests and audits without optimization, and retain those file
hashes and its path in the new binding. Reject an unfinished predecessor, an
audit/infrastructure failure, exhausted budget, or a different stop reason.
This is a single transition from the original power-stopped invocation, not a
general resume facility or automatic retry policy.

The new execution binds the clean current commit supplied by the owner at launch;
it is **not** hardcoded to the first runner commit. The historical source identity
remains historical. Only this experiment's runner, tests, protocol and checkpoint
may differ across this correction; model construction, production code, numerical
binaries, solver options, retained inputs and all 21 prospective canonical starts
must remain identical. Current workers enforce the new binding normally.

Carry forward completed outcomes, consumed slots, launches and supervised seconds
without rerunning any consumed slot. Global call numbering continues from the
predecessor. Keep inherited and new launches separately visible, and enforce the
same 21-launch/75600-worker-second ceiling over both invocations together.
The first completed SOCP flat-start rejection therefore remains call 1; the first
new worker is call 2, trial 2, matched-AC perturbation scale 1e-4, seed 17001311.

## Commands

Non-solving tests (optimizer calls are forbidden in the test suite):

```sh
uv run --extra dev python -m pytest -q experiments/tracy_battery_transfer_starts/test_starts.py experiments/tracy_battery_transfer/test_transfer.py
```

Read-only preflight reconstructs and canonicalizes all 21 trials; it creates no
run directory and never optimizes:

```sh
uv run --extra dev python -m experiments.tracy_battery_transfer_starts.run --preflight
```

Only after owner review, commit and separate execution approval:

```sh
uv run --extra dev python -m experiments.tracy_battery_transfer_starts.run --authorize-execution --commit FULL_COMMIT_SHA
```

Default output is a fresh `results/starts_001` within this experiment. Only fresh
direct children of this experiment's results directory are permitted.

```sh
uv run --extra dev python -m experiments.tracy_battery_transfer_starts.run --status
```

Preflight the explicit continuation (no solves or run-directory creation):

```sh
uv run --extra dev python -m experiments.tracy_battery_transfer_starts.run --preflight --continue-from experiments/tracy_battery_transfer_starts/results/starts_001 --output experiments/tracy_battery_transfer_starts/results/starts_002
```

After owner commit, launch the already owner-authorized remaining ladder:

```sh
uv run --extra dev python -m experiments.tracy_battery_transfer_starts.run --authorize-execution --commit FULL_NEW_COMMIT_SHA --continue-from experiments/tracy_battery_transfer_starts/results/starts_001 --output experiments/tracy_battery_transfer_starts/results/starts_002
```

Read the combined outcome using `--status --output
experiments/tracy_battery_transfer_starts/results/starts_002`. Its inherited
evidence is reconstructed under the recorded historical identity; no historical
optimization is repeated and no source result is reclassified.
