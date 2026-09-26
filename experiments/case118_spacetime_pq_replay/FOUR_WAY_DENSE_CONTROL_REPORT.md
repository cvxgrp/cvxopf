# Hour 6047: four-way repeat with sparse dispatch disabled

Completed 2026-09-22. **All four conditions converged and passed the existing
acceptance checks.** The previous default-dispatch four-way run rejected all four
at 3,000 iterations. Keeping CVXPY 1.9.3 / sparsediffpy 0.6.1 and setting
`cvxpy.settings.SPARSE_DENSITY_THRESHOLD = 0.0` in each fresh worker restores
convergence in this interval, including with both time and spatial vectorization.

## Exactly what was varied

All four modes use **sparse P/Q variable storage** (`sparse_pq=True`). The four-way
comparison concerns expression/constraint vectorization:

| Mode | Time assembly | Spatial P/Q batching |
|---|---|---|
| none | stepwise | off |
| time_only | vectorized | off |
| spatial_only | stepwise | on |
| both | vectorized | on |

“None” does not mean dense P/Q storage. Disabling CVXPY's density dispatch is a
separate control: it prevents automatic conversion of low-density dense constant
matrix products to the sparse differentiation binding. It does not densify the
application's P/Q variables or remove spatial batching.

The current checkout was HEAD `93294af124e1135388dc5d8d444a32b0b16755c2`, with the
new experiment harness uncommitted and content-hash bound. Core package source
and `streaming_runner.py` match the earlier four-way binding; intervening retained
path/reporting changes are enumerated in the new binding. Each condition exactly
reproduced its own prior full canonical x0, normalized variable layout, raw and
assigned named starts, and preparation record. Bounds and effective options were
checked against a no-solve preparation for each condition before native entry.

The frozen primary request covers the same three hours starting at hour 6047,
with the same causal initialization, SoC endpoints, numerical solver settings,
and acceptance criteria. One fresh process per condition ran sequentially, with
no retries, helper solves, recovery, or iteration-limit changes. Native logging
uses `print_level=5`; the captured incoming option is `print_level=0` before this
logging-only override. Source, package inventory, native binary hashes, and thread
environment verified before and after the workers. No installed package files,
production defaults, or dependency constraints were changed.

## Results

| Mode | Earlier default dispatch | Control result | Control iterations | Control IPOPT seconds | Control solve-phase seconds |
|---|---|---|---:|---:|---:|
| none | rejected at 3,000; 717.402 s | optimal, accepted | 339 | 212.517 | 220.162 |
| time_only | rejected at 3,000; 284.574 s | optimal, accepted | 70 | 31.903 | 35.289 |
| spatial_only | rejected at 3,000; 448.483 s | optimal, accepted | 190 | 98.332 | 103.320 |
| both | rejected at 3,000; 196.344 s | optimal, accepted | 76 | 32.188 | 34.088 |

Earlier times in the second column are native IPOPT times **to rejection**;
they are not successful-solve benchmarks. Solve-phase time includes
canonicalization and boundary verification overhead. These are single observations,
not repeated timing estimates. Both and time-only have similar native time here;
the small solve-phase difference is insufficient evidence of a general speedup.

Stepwise modes report 9,120 variables, 10,597 constraints, and 412,431 stored
Jacobian entries. Time-vectorized modes report 9,124 variables, 10,601 constraints,
and 412,439 stored Jacobian entries. All four report 10,104 Hessian entries.
These are stored structure sizes, not counts of numerically nonzero derivatives.
Equal counts do not establish equal coordinate ordering or trajectories.

## Numerical results and limits

| Mode | Extracted objective | Native unscaled constraint violation | Native scaled overall NLP error |
|---|---:|---:|---:|
| none | 266883.7161239854 | 7.64e-14 | 2.50e-9 |
| time_only | 266883.7172815133 | 5.11e-8 | 5.11e-8 |
| spatial_only | 266883.7238073657 | 4.41e-8 | 4.41e-8 |
| both | 266883.7159979859 | 5.30e-8 | 5.30e-8 |

Time-only exactly matches every retained named variable and extracted result of
the historical successful solution reproduced with the old packages. The other
representations converge to accepted, economically close solutions, not identical
dispatches. The objective range is 0.00781 (about 2.93e-8 of the objective).

Between time-only and both, maximum absolute differences include 0.0472 MW in
generator active power, 0.0434 MW in battery active power, 0.0419 MWh in SoC,
0.000121 pu in voltage magnitude, and 0.00566 degrees in angle. Reactive
differences are larger: 20.4 MVAr in generator Q, 10.2 MVAr in battery Q, and
20.6 MVAr in nondispatchable Q. Across all four conditions, the largest pairwise
generator-Q difference is 154.3 MVAr and nondispatchable-Q difference 278.6 MVAr.
Unpriced reactive allocation is a plausible explanation; this test does not
establish uniqueness or prove the cause of those differences. Small objective
differences must not be described as identical physical operating points.

All existing physical audits passed. Across these four runs, retained active and
reactive balance residuals were at most 1.39e-13 pu; SoC recurrence residuals at
most 1.49e-12 MWh; terminal SoC residuals at most 8.67e-13 MWh. These balance
audits use lifted injections and are not an independent voltage/Ybus recomputation.
Native constraint violations above supply the full solver's constraint check.
Full scaled/unscaled termination metrics and every audit residual are retained
in the compact evidence. IPOPT's `optimal` status is local convergence, not a
global optimum certificate.

Thermal collection recorded 43 samples with no errors; average CPU temperature
spanned 44.1–68.8°C. The owner's external-fan confirmation remained in effect.
Process observations were saved throughout each worker.

## Consequence for the project decision

This supports the second option: retain the new dependency pair and evaluate an
explicit compatibility policy for automatic sparse dispatch. Spatial batching
does not need to be rolled back to make this particular interval converge.
Time-only retains the strongest exact historical equivalence; both is also
accepted and similarly fast here. A project-wide policy has not been adopted.

Before adoption, define the setting's scope explicitly (it is process-global,
not a documented per-problem solve option), retain reproducible package versions,
and validate the chosen configuration on an agreed broader regression sample.
Do not silently change the setting on package import. This one interval does not
establish general robustness or the mechanism inside IPOPT/MUMPS. The earlier
initial-point oracle equality checks remain bounded to time-only; this four-way
repeat did not add derivative finite-difference checks or a full trajectory audit.

## Evidence and reproduction

- Design: `FOUR_WAY_DENSE_CONTROL.md` (frozen at launch).
- Harness: `four_way_dense_control.py`.
- Analysis: `analyze_dense_control.py`; rerun using
  `UV_CACHE_DIR=/tmp/cvxopf-provenance-uv uv run --no-sync --extra dev python -m experiments.case118_spacetime_pq_replay.analyze_dense_control`.
- Reviewable evidence: `artifacts/provenance/four_way_dense_control.json`, including
  log/result hashes, full termination/audit metrics, and pairwise solution differences.
- Raw evidence: `results/case118_6047_four_way_dense_control/`, including launch
  binding, all four preparation boundaries, complete starts/results, IPOPT logs,
  process observations, and thermal telemetry. Preserve these files in place.

The raw root is intentionally immutable; the runner refuses to reuse existing
output directories. The analysis rerun checks the bound sources and environment.
This is a local retained-fixture experiment, not yet a portable upstream bundle.
