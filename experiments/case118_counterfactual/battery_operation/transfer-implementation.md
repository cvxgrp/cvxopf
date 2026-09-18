# Cost of prescribed battery energy shifts: implementation checkpoint

Status (2026-09-17): owner authorized step 3; independent implementation review
is CLEAN under the scientific review standard. No step-3 numerical solves have
been run. Execution awaits the source checkpoint described below.

## Scope and implementation

Twelve primary solves: the same three windows, each with a 1 MWh and a 5 MWh
prescribed battery energy shift, in AC and DC. The first hour charges and the
third hour discharges; the saved pre-outcome device weights determine the
allocation. Initial and terminal SoC, renewable real power, costs, and physical
limits remain unchanged. Original DC references remain intact for reporting.

- `transfers.py` reconstructs schedules, verifies prior results and consumed
  resources, coordinates six serial DC solves followed by six AC comparisons,
  and retains cost components and signed cost changes per MWh transferred.
- `model.py`, `dc.py`, and `worker.py` accept an explicit battery schedule only
  for the economic fixed-battery comparison and audit that lock independently.
- `runner.py` and `mechanism.py` reuse the existing supervisor and process
  lifecycle handling. Each AC comparison starts from its earlier fixed-battery
  solution; that solution is not an eligible incumbent for the altered schedule.
  Two main workers share one helper, with explicit start perturbations and the
  existing escalation ladder. Target-free recovery does not apply.
- `transfer-protocol.json` binds the prior study, summary, historical plans,
  and pre-outcome transfer checks by hash. No previous baseline is re-solved.
- `tests/test_case118_battery_transfers.py` checks units and signs, schedule
  locks, source roles, resource accounting, cost/proxy separation, and successful
  and interrupted phase orchestration without native Case118 solves.

The previous phase consumed 421.902525 active seconds, 638.241710 aggregate
worker seconds, six AC attempts and six DC solves. Remaining limits are
10,378.097475 active seconds, 20,961.758290 aggregate worker seconds, 42 AC
attempts including recovery, and six DC solves. The per-worker limit remains
5,400 seconds. Preparation derives these limits from the saved records.

## Verification

- 157 targeted transfer, mechanism, counterfactual and speculative-worker tests
  passed. Two subsequent orchestration cases were added; the resulting focused
  transfer file passed all eight tests.
- Ruff formatting and lint passed for the changed Python files.
- Exact-case read-only preparation verified prior lifecycle artifacts, source
  identities, all six fixed-battery baseline audits and all six transfer schedules.
- Independent review reproduced 43 focused tests and independently checked
  transfer signs, energy amounts and endpoint preservation. No actionable
  correctness findings remained; current-status documentation was aligned.

These checks establish implementation behavior, not numerical convergence or
scientific outcomes for the prescribed transfers.

## Execution-source checkpoint

The agreed [plan](../../../plans/case118-toy-battery-mechanism-test.md) requires
independently reviewed, committed implementation before numerical execution.
The owner retains commit control. The existing alternative,
`--snapshot-reviewed-worktree`, requires explicit owner approval and retains
immutable runtime source bytes and provenance; it is not an automatic waiver.

After that disposition, the prepared command is:

```sh
uv run --extra dev python -m experiments.case118_counterfactual.transfers \
  --protocol experiments/case118_counterfactual/battery_operation/transfer-protocol.json \
  --output experiments/case118_counterfactual/results/battery_transfers_20260917
```

Keep DC's native loss proxy separate from generation/storage costs and physical
AC losses. Report prescribed-minus-fixed cost changes, with negative values
meaning savings. These are finite schedule comparisons and nonconvex feasible
witnesses, not nodal dual prices or certified global optima.
