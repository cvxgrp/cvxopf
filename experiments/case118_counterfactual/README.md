# Matched Case118 AC counterfactuals

This implements the retained toy R1/R2/G/B study in
[the protocol](../../plans/case118-toy-ac-counterfactual-protocol.md).
It preserves the historical toy objective (including battery weight 1.0).
Tracy cost changes and the deferred horizon study are outside this code.

Implementation is not numerical launch authority. Select episodes with context,
review the exact windows and protocol settings, independently review and commit
the code, then obtain the separate numerical go-ahead. No OPF solve was needed
to build or test this package; the numerical pilot remains outstanding.

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
  --output outputs/counterfactual-candidates

uv run --extra dev python -m experiments.case118_counterfactual.data \
  --start CONTEXT_START --stop CONTEXT_STOP \
  --output outputs/counterfactual-context-EPISODE
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
  --protocol REVIEWED_PROTOCOL.json --output outputs/counterfactual-RUN
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
the interrupted one. The already planned one-window pilot is the checkpoint
for deciding whether further windows add distinct information.

## Verification

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
