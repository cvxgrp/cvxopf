# Stage C — matched annual DC comparison

Status: **both annual DC solves completed and independently accepted** at clean
execution commit `034ea6b9e5d9dd4276d6e742847f547c19a1047d`.
See [the report](STAGE_C_REPORT.md) for results and retained evidence. User
review 2 is pending; no AC execution is authorized. The following records the
reviewed execution contract. The owner
approved proceeding with Stage C at **rho = 1/3 (on)** and **lambda = 0.01
(medium)**, and approved **4 hours / 16 GiB worker RSS per solve**. Review and
commit the implementation checkpoint before binding and launching it. This
does not authorize AC execution.

## Scientific comparison

Exactly two fresh-process, sequential solves: single-node DC first, then lossy
DC. Both use all 8,760 Tracy 2021 hourly intervals in the fixed UTC−08:00
calendar, the verified Stage A source and arrays, and the same individual
device fleet. Retain the 5,000 MW dispatchable fleet, four-average-load-hour
ideal storage fleet, 50% initial and terminal SoC per battery, and uniform
load-shedding penalty 20,763.594 objective units/MWh. No sizing, siting, source,
or boundary-policy changes are part of Stage C.

Use vectorized assembly, SCIPY canonicalization, and CLARABEL with
`tol_gap_abs=tol_gap_rel=tol_feas=1e-10`, `max_iter=5000`, `max_threads=1`, and
`warm_start=False`, unchanged from the completed Stage B batch. Loss weight
remains 1.0 for lossy DC. Its flow-squared objective proxy is not an energy
withdrawal in nodal balance and is not a measured AC loss. Single-node has no
branch flow or loss-proxy contribution.

The lossy-DC trajectory remains the proposed source of AC SoC signposts;
single-node is a comparison only. Neither solution establishes AC feasibility.

## Resource budget and stopping

- One worker at a time; release/reap it before starting the next formulation.
- **14,400 seconds per worker**, covering preparation, construction,
  canonicalization, solve, extraction, audit, and archiving.
- **16,384 MiB sampled worker-PID RSS**, checked every second. Descendant and
  parent-process memory are not monitored. This is not a process-tree or
  whole-machine memory cap, or a guarantee of observing subsecond peaks.
- Total supervised worker time is bounded by eight hours, plus termination
  latency. Parent preparation and independent reconstruction add time and memory
  outside the worker ceiling. Report parent preflight and reconstruction timing
  separately; parent memory is not recorded. Do not run parent reconstruction
  concurrently with the next solver worker.
- Stop on resource termination, monitoring failure, exception, solver rejection,
  infeasibility, or failed independent physical/accounting audit. Retain partial
  records and available logs; do not retry, resume, loosen tolerances, change
  solver, or increase resources automatically.
- Check RSS monitoring permissions before output creation. Record host/software,
  native CLARABEL settings and convergence diagnostics, phase timing, sampled
  RSS and UTC execution times. External thermal telemetry is contextual, not
  a scientific acceptance condition.

## Implementation and evidence

`run_stage_c.py` supplies the two-arm specification and Stage C protocol identity
to the existing `run_stage_b.py` execution path. Stage B's defaults and retained
specification remain unchanged. Reuse `verified_inputs()`, `inputs_for_arm()`,
and `audit_result()` rather than duplicating scientific models or audits.

Require a clean tree and exact full execution commit. Bind the Stage A manifest,
owner CSV, this protocol, package versions, solver settings, resource limits,
and arm definitions. Use a fresh output directory, immutable result/completion
files, and hash-checked full primal archives. Reconstruct each accepted arm in
the parent before proceeding; the offline analyzer repeats that independent
physical/accounting reconstruction without solving. Acceptance requires public
`optimal` and the unchanged Stage B tolerances, including per-device bounds,
balance, SoC recurrence/endpoints, load/renewable reporting and cost accounting.
Native solver evidence is retained alongside this gate.

Retain interval results and all 8,761 storage boundaries with device identities.
Archive both arms separately; preserve Stage B evidence. Record annual DC as
executed without implying AC authorization. A partial pair is not a complete
Stage C comparison.

After owner review/commit, from the repository root:

```sh
uv run python -m experiments.case118_tracy_2021.run_stage_c \
  --commit FULL_REVIEWED_COMMIT --approve-resource-budget
uv run python -m experiments.case118_tracy_2021.run_stage_c --analyze
```

The acknowledgement flag confirms the documented 4-hour/16-GiB budget; it
does not override it. Defaults write to ignored `results/stage_c/`. Launch
requires actual process-monitoring permission, not merely shell permission.

## Outputs and user review 2

Deliver the annual comparison report and read-only marimo results notebook
specified in the [parent plan](../../plans/case118-tracy-2021-study-plan.md).
Include full-year input and matched output heatmaps, interactive aggregate and
device time series, and **lossy DC minus copper plate** differences with shared
units, aligned identities, and zero-centered difference colors. Verify that
inputs agree. Do not invent single-node branch values. Distinguish interval
powers from boundary SoC, and label the boundary convention on heatmaps.

Report annual/monthly energy and cost components, generation, curtailment,
shedding, storage throughput/endpoints, congestion and computational resources.
Separate construction, canonicalization/solve, extraction/audit, archiving and
parent reconstruction; use native solve and CVXPY compilation times where
available without pretending they sum exactly to wall time.

Stop for **user review 2** after the annual comparison. No AC solves, shard
derivation, new sensitivities, or automatic follow-on numerical studies.
