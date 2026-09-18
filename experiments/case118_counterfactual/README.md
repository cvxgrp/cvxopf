# Case118: AC dispatch adjustments and battery operation

This experiment examines the feasibility and economic value of adjustments
from DC dispatch to AC operation, as specified in
[the protocol](../../plans/case118-toy-ac-counterfactual-protocol.md).
It preserves the historical toy objective (including battery weight 1.0).
Tracy cost changes and the deferred horizon study are outside this code.

Two sets of results are complete and independently reviewed:

- [AC dispatch adjustments](ac_dispatch_adjustments/REPORT.md): one three-hour
  window comparing small generator adjustments, their cost, further generator
  redispatch, and battery rescheduling.
- [Battery operation in AC and DC](battery_operation/REPORT.md): three
  three-hour windows testing whether shifting battery energy across time lowers
  operating cost in each model. The owner has authorized the separate 1 and
  5 MWh transfer comparisons. Their implementation has passed independent
  review; numerical execution awaits the execution-source checkpoint.

Reports, launch protocols, context, and analysis scripts live in the two folders
above. Retained solver artifacts live in this experiment's `results/` directory,
following the existing experiment convention; only that directory is Git-ignored.
See [the relocation record](RESULT_LOCATIONS.md) for the unchanged execution
records' original paths. New numerical work still requires its agreed review
and launch authorization.

## Model and evidence

`model.py` builds exact slices using the existing public-builder wrapper.
Renewable real power is fixed to the retained DC schedule in every arm; reactive
controls remain free. R1/R2/G additionally fix battery real power. All arms use
the same DC initial and terminal SoC, network/device constraints, and costs.
R1 minimizes integrated generator absolute departure in MW-hours using an
explicit epigraph. R2 minimizes the original AC cost subject to the retained
R1 departure plus the explicitly supplied MWh allowance. G removes that budget;
B also frees battery power. Original production-cost expressions survive the
R1 objective replacement. Only B permits target-free initialization sources.

The independent audit combines the existing physical gate with generator and
device boxes/circles, input/lock/budget checks, reconstructed generation/storage
costs, and voltage-derived nodal injections and branch-terminal flows. This last
check prevents internally inconsistent reported flow arrays from passing just
because their aggregate balance happens to close. It retains frozen physical
tolerances; added comparison tolerances are explicit protocol inputs.

Reports retain generator net/L1/opposing changes, signed/L1 battery changes,
every SoC boundary, device throughput, generation and throughput costs, losses,
shunts, curtailment, and balance reconciliation. Economic comparisons use whole
matched windows and preserve missing comparisons as unavailable. Timing shifts
with unchanged throughput incur no extra throughput penalty. Findings are
conditional feasible witnesses and observed improvements, not global optima.

## Read-only episode inspection

From the repository root:

```sh
uv run --extra dev python -m experiments.case118_counterfactual.data \
  --output experiments/case118_counterfactual/results/candidates-RUN

uv run --extra dev python -m experiments.case118_counterfactual.data \
  --start CONTEXT_START --stop CONTEXT_STOP \
  --output experiments/case118_counterfactual/results/context-EPISODE
```

Both destinations must be new. The first command ranks trailing six-hour
averages within each shard and preserves individual metrics and ranks; it does
not select windows. Use the saved dashboard and this table to inspect diagnostic
and ordinary episodes. The second extracts only the requested retained first
actions, DC references, source hashes, physical limits, and reset annotations.
Context can cross a shard boundary; solve windows cannot. No automatic grouping
threshold or episode sample is silently chosen. The reviewed episode decision
and context JSON precede the solve-window manifest.

The reference adapter binds ordered inputs to the accepted S4 fingerprint,
outer archive and shard manifest. Generator identity uses its full ordered
device table because the current generator class has no independent device ID.
Battery and renewable IDs are explicit. Do not feed it Tracy data under a toy
identity; Tracy will need an adapter to its own accepted references.

## Execution and explicit configuration

`runner.py` reuses `SpeculativeSupervisor`, `SubprocessBackend`, the single shared
helper, process-group cleanup, and unchanged 16 GiB worker / 24 GiB aggregate,
22 GiB pressure / 8 GiB helper-reserve gates. Independent windows occupy at most
two main lanes; each window advances R1 → R2 → G → B sequentially. The only
change to the existing supervisor is an optional race factory whose default
preserves the annual policy.

Owner-approved fixed-battery initialization uses the primary flat start for R1
and the accepted prior-arm physical solution for R2/G, with explicit perturbations
at the existing scales and seeds. Historical slots 6/7/8 now mean perturbations
of that declared center, **not** a preceding-hour causal controller. A helper
becomes eligible after 300 native-solve seconds (or primary failure), receives
the existing 300-second budget, then may proceed to the retained-start uncapped
secondary/final attempts. Target-free and its dependent slots are explicitly
inapplicable for these fixed-battery arms; no 1,800-second target-free retry is
performed there. B retains the existing target-free/copy/perturb/retry ladder;
preceding-hour causal sources are unavailable for isolated windows.

The worker reuses the existing verified-IPOPT-start solve adapter. It saves
every original variable and the full reduced IPOPT vector before native entry.
Cross-arm transfer checks every physical name and shape; only the explicit
repair epigraph is added/reconstructed or removed. When a retained candidate has
a small, physically tolerated leaf-bound residual, only its assigned start is
projected to the declared leaf bounds; raw and assigned values are both saved,
and the scientific candidate is unchanged. Replays restore the complete original
start and check the reduction layout. Target-free results are sources
only. Every accepted source/candidate is independently audited by the parent.

The first accepted new candidate wins the stage race, as in S5; this does not
certify its local or global optimality. The coordinator then compares it with
the destination-audited incumbent. Worse or failed searches cannot erase that
incumbent. Total cost and its components are retained separately.

The run protocol is a JSON object with these **required** fields; no numeric
example is provided that could accidentally become a scientific default:

| Field | Meaning |
| --- | --- |
| `windows` | List of `id`, `start`, `steps`, `selection_reason`, and `context: {path, sha256}` referencing reviewed context JSON |
| `fixed_battery_initialization` | `explicit_start_perturbations` |
| `epsilon_repair_mwh` | R2 allowance added to the retained R1 departure |
| `tolerances` | `lock_mw_abs`, `repair_mwh_abs`, `cost_abs`, `cost_rel` |
| `ac_options` | Explicit validated IPOPT options; review against the frozen S5 settings |
| `max_attempts` | Whole-study maximum, counting main/helper/replay calls |
| `study_wall_seconds` | Whole-study elapsed-time stop |
| `worker_wall_seconds` | Declared end-to-end worker stop, including construction; separate from helper solve clocks |
| `total_worker_seconds` | Sum of worker elapsed times, including canceled work |

After separate launch approval, use:

```sh
uv run --extra dev python -m experiments.case118_counterfactual.runner \
  --protocol REVIEWED_PROTOCOL.json \
  --output experiments/case118_counterfactual/results/ac_dispatch_adjustments-RUN
```

The CLI requires a clean committed tree and a new destination. It saves the
protocol, exact window arrays, source/implementation identities, environment,
requests, phase clocks, x0, results, independent audits, lifecycle receipts,
memory samples, stage choices and summary. It never overwrites accepted S5 data.

Exhausted recovery or a total-resource stop ends this bounded run, cancels and
reaps all contenders, and saves partial stages with available incumbents. It
does not call a local failure physical infeasibility or launch extra B-only
diagnostics. Such a follow-up can be proposed separately. There is no automatic
resume or retry: a later run uses a fresh directory and explicit disposition of
the interrupted one. The completed one-window comparison and any separately
approved follow-up are documented in the reports above.

## Verification

### Battery operation in AC and DC

`mechanism.py` implements the first phase of the separately reviewed
[battery operation plan](../../plans/case118-toy-battery-mechanism-test.md).
It runs six DC solves serially, then the three AC G/B chains through the
existing 2+1 supervisor. G is the fixed-battery F arm in this study's language;
no R1/R2 repair solves are launched. Original R1/R2/G/B behavior is unchanged.

```sh
uv run --extra dev python -m experiments.case118_counterfactual.mechanism \
  --protocol REVIEWED_PHASE_ONE_PROTOCOL.json \
  --output experiments/case118_counterfactual/results/battery_operation-RUN
```

The explicit protocol adds `phase: "fixed_free"`, `dc_options`, and
`max_dc_attempts: 12` to the common protocol fields. The latter is the
whole-study DC cap; this phase performs six calls, with no automatic retry.
Set the unused `epsilon_repair_mwh` to zero. `max_attempts` is the cumulative
AC attempt cap. The phase summary records consumed AC/DC counts, active wall
time and summed worker elapsed time for the later transfer phase. The DC
consumption is deducted before starting AC. Each phase uses a fresh output
directory; no automatic restart or continuation is provided.

DC keeps its native loss-proxy objective term, independently reconstructed
using per-unit flows, resistance, time-step duration and the original loss
weight. Both native objective and generation/storage cost sum are reported.
Independent physical, device-bound, input, schedule and cost audits run in
the DC worker and again in the parent. A free-battery candidate cannot erase
a cheaper destination-audited fixed-battery incumbent. Failed DC attempts
stop this phase with partial evidence instead of silently retrying or being
called infeasible. The shared process backend uses DC phase labels and the
same process-group cleanup and hard RSS limits, without speculative DC jobs.

The entry point normally requires a clean committed implementation. A proposed
alternative for an explicitly approved uncommitted execution is
`--snapshot-reviewed-worktree`: it retains all runtime Python source bytes
(including new, nonignored modules), their verified fingerprint, the base
commit, tracked Git diff, and untracked test/plan files in an immutable snapshot.
The study manifest references that snapshot by hash, and each worker still
checks the exact source before/after its solve. This flag does not confer
permission to bypass the agreed commit checkpoint; resolve that disposition
with the owner before using it. The original counterfactual CLI retains its
clean-tree gate.

The phase-one entry point **stops for owner result review after fixed/free
comparisons**. The owner reviewed those results and authorized the prescribed
energy-transfer comparisons. They use the separate entry point below; the
completed results and consumed budgets remain retained.

### Cost of prescribed battery energy shifts

`transfers.py` implements step 3: add 1 or 5 MWh of first-hour charging and
third-hour discharging to the DC battery schedule in each selected window.
The weights were saved before the fixed/free results. Generator dispatch is
reoptimized with battery real power fixed to the prescribed schedule; SoC
endpoints, renewable dispatch and all physical limits are unchanged.

The launch protocol binds the previous study and summary, historical plans,
and full-precision transfer prechecks by hash. Preparation re-audits both
models' fixed-battery baselines and checks their source identities, verifies
the previous lifecycle artifacts and work ledger, reconstructs all six
prescribed schedules, and checks storage power/SoC/endpoints before launch.

Six DC solves run serially, followed by six independent AC comparisons using
the existing two main lanes and shared helper. Each AC solve starts from its
window's accepted fixed-battery solution. That source is not eligible as an
incumbent for the altered schedule. Explicit start perturbations and replays
preserve the new battery lock; target-free recovery is inapplicable.

The previous phase's 421.902525 active seconds, 638.241710 aggregate worker
seconds, six AC attempts and six DC solves remain consumed. The remaining
limits are read from those saved records, not reset for this phase. Summary
records retain both this phase's use and cumulative use, including failed or
canceled work. An unresolved solve retains partial evidence without a new
schedule, smaller transfer, substituted baseline or automatic retry for DC.

After implementation review and the execution-source checkpoint:

```sh
uv run --extra dev python -m experiments.case118_counterfactual.transfers \
  --protocol experiments/case118_counterfactual/battery_operation/transfer-protocol.json \
  --output experiments/case118_counterfactual/results/battery_transfers_20260917
```

The same clean-commit gate and explicitly authorized snapshot alternative
apply. The output includes the full schedules, baseline references, original
DC references, effective remaining limits and execution provenance. Report
cost changes as prescribed minus fixed, so a negative change is a saving.
Keep generation, throughput penalty, common device costs and DC loss proxy
separate; report native and device-cost changes per MWh transferred. These
are finite schedule comparisons, not nodal dual prices or certified optima.

### Tests

```sh
uv run --extra dev pytest tests/test_case118_counterfactual.py \
  tests/test_case118_s5_speculative_policy.py \
  tests/test_case118_s5_speculative_attempt.py \
  tests/test_case118_s5_speculative_supervisor.py -q
```

Tests use an analytically feasible synthetic zero-flow network, altered arrays
that must fail independent audits, non-unit time steps, full named transfer,
real canonicalization with native IPOPT intercepted, and the adapted recovery
ladder. These verify implementation contracts, not Case118 numerical convergence.
Read-only construction additionally checked all four arms on actual frozen
Case118 hours 100–102 and a three-hour context extraction; those hours are a
software smoke check, not a selected scientific episode.
