# Final four-condition Case118 replay analysis

Refreshed 2026-09-25. **Complete: 504/504 accepted windows, 126/126 in each condition.** The last condition finished at 23:24:12 UTC (4:24:12 p.m. Pacific). Monitoring is disabled; no further solves were launched for this analysis.

## Findings

Combined time and spatial vectorization is the preferred candidate in this sample under the explicit disabled-dispatch policy. Across all 126 matched windows with original population weights, mean winner-solve time decreased **43.7%** (58.65 → 33.02 s) and window latency decreased **43.1%** (69.92 → 39.80 s) versus fresh no-vectorization. It was faster on 110/126 winner solves and 112/126 window latencies. These are weighted per-window statistics, not sums interpreted as elapsed study time.

The 120 historical primary-winner cohort independently shows 43.9% lower arithmetic mean solve time and 42.5% lower latency; its paired geometric speedups are 1.952× and 1.780×. Keep that cohort distinct from the six historical helper winners below. Similar costs do not establish identical dispatch or state trajectories.

## Execution and scope

Bound execution commit: `ae7b043397b9995ab2ecfd64799c59eed48016af`. All four conditions used CVXPY 1.9.3, sparsediffpy 0.6.1, sparse P/Q **storage**, and `SPARSE_DENSITY_THRESHOLD=0.0` before model construction. Disabled sparse dispatch is an experiment policy, not a change to project-wide defaults.

| Condition | Time vectorization | Spatial P/Q batching | Elapsed minutes | Helpers launched / won |
|---|---|---|---:|---:|
| none | off | off | 126.10 | 16 / 6 |
| time_only | on | off | 70.93 | 7 / 1 |
| spatial_only | off | on | 81.67 | 8 / 2 |
| both | on | on | 57.20 | 4 / 3 |

Conditions ran sequentially in the displayed order, with two primaries on distinct historical shards and one shared helper slot. External fan confirmation and thermal admission preceded solves. Requests, starts, sample, policy, seeds and weights were frozen. Elapsed condition time includes final cleanup; window latency ends at winner reaping, before final loser cleanup. Winner-solve time includes canonicalization and start persistence as well as IPOPT; build includes historical-initializer reconstruction.

## Weighted timing across all 126 windows

| Condition | Winner solve (s) | Window latency (s) | Initialization preparation (s) |
|---|---:|---:|---:|
| none | 58.652 | 69.916 | 0.552 |
| time_only | 42.785 | 49.713 | 0.550 |
| spatial_only | 46.491 | 54.955 | 0.560 |
| both | 33.023 | 39.799 | 0.549 |

## Factorial comparisons: 120 historical primary winners

Every contrast has 126 matched accepted pairs; means/geometric speedups in this table use the same 120 primary-cohort windows and original weights. Faster counts include all 126. A speedup above one favors the second condition.

| First → second | Mean solve reduction | Mean latency reduction | Geometric solve / latency speedup | Faster solve / latency (of 126) |
|---|---:|---:|---:|---:|
| none → time_only | 25.8% | 27.2% | 1.711× / 1.604× | 114 / 115 |
| none → spatial_only | 19.6% | 20.4% | 1.416× / 1.354× | 111 / 112 |
| none → both | 43.9% | 42.5% | 1.952× / 1.780× | 110 / 112 |
| time_only → spatial_only | -8.2% | -9.3% | 0.827× / 0.844× | 27 / 26 |
| time_only → both | 24.5% | 21.0% | 1.140× / 1.109× | 101 / 101 |
| spatial_only → both | 30.2% | 27.7% | 1.379× / 1.315× | 108 / 108 |

Time effects are none→time_only and spatial_only→both; spatial effects are none→spatial_only and time_only→both. Both factors help in both settings. Their gains are not multiplicatively independent: the weighted primary geometric interaction T_none·T_both/(T_time·T_spatial) is **1.241 for solve** and **1.220 for latency** (one denotes independence). Combined gains are smaller than multiplication of the separate gains would predict.

## Distribution and overhead

| Condition | Primary solve p50 / p90 / p95 / p99 (s) | Primary latency p50 / p90 / p95 / p99 (s) | Primary mean build (s) |
|---|---|---|---:|
| none | 38.74 / 123.83 / 155.02 / 201.15 | 44.53 / 131.11 / 161.33 / 206.98 | 1.375 |
| time_only | 23.46 / 87.15 / 113.80 / 494.45 | 29.11 / 92.18 / 119.25 / 499.18 | 0.921 |
| spatial_only | 26.05 / 72.54 / 158.71 / 297.00 | 32.14 / 78.50 / 175.28 / 439.14 | 1.189 |
| both | 21.52 / 70.63 / 118.47 / 223.43 | 26.24 / 75.94 / 123.92 / 313.98 | 0.824 |

Combined vectorization has 16 slower winner solves and 14 slower window latencies than fresh none. The largest solve-time regressions by seconds are:

| Window | None solve (s) | Both solve (s) |
|---|---:|---:|
| 7009 | 72.45 | 308.85 |
| 6458 | 96.71 | 286.95 |
| 4457 | 19.58 | 137.77 |
| 6864 | 36.18 | 145.41 |
| 5929 | 116.73 | 223.43 |
| 5548 | 35.17 | 99.08 |

## Six historical helper-winner windows

Cohort membership is fixed by history; it does not mean a helper was used in every fresh condition. Each cell is winner order; winner-solve seconds / window-latency seconds. Order zero is primary.

| Window | None | Time only | Spatial only | Both |
|---|---|---|---|---|
| 6794 | 1; 56.30 / 368.33 | 0; 41.42 / 46.63 | 0; 20.13 / 25.98 | 0; 11.39 / 16.74 |
| 3270 | 0; 114.37 / 120.95 | 0; 17.90 / 23.64 | 0; 23.68 / 29.67 | 0; 177.72 / 182.72 |
| 6164 | 1; 215.28 / 529.15 | 0; 25.35 / 30.37 | 0; 27.79 / 32.96 | 0; 22.26 / 27.42 |
| 5879 | 0; 346.12 / 352.03 | 0; 71.50 / 77.13 | 0; 68.86 / 74.24 | 1; 31.52 / 342.38 |
| 8297 | 1; 38.64 / 134.96 | 0; 22.71 / 28.41 | 0; 30.10 / 35.69 | 0; 22.98 / 28.33 |
| 6458 | 3; 96.71 / 1021.09 | 3; 36.61 / 959.45 | 5; 164.32 / 1425.27 | 1; 286.95 / 598.68 |

## Attempts, races and integrity

| Condition | Attempts | Accepted attempt returns | Rejected | Budget timeout | Lost race |
|---|---:|---:|---:|---:|---:|
| none | 142 | 127 | 1 | 8 | 6 |
| time_only | 133 | 126 | 0 | 3 | 4 |
| spatial_only | 134 | 127 | 0 | 4 | 3 |
| both | 130 | 126 | 0 | 0 | 4 |

Accepted attempt returns include target-free helper results that are not executable winners. All 539 attempts are accounted for; 35 are helpers. There are 492 primary winners and 12 helper winners. Fresh none→both changes winner order on eight windows: 5879, 5929, 6164, 6455, 6458, 6794, 7033, 8297. Changes are operational outcomes, not evidence of equivalent per-attempt solver paths.

Independent counts confirm 504 winner records, 507 attempt-result records, and
539 launches with 539 lifecycle receipts marked reaped; there are no unresolved
attempts. All 3,202 recorded lifecycle artifact hashes verify. These artifact
types count different things and are not expected to have equal counts.
In the combined condition, helpers raced on 5879, 6455, 6458 and 7009.
Helper order 1 / slot 6 won the first three; primary order 0 / slot 0 won 7009,
and its losing helper was canceled/reaped. Window latencies were respectively
342.38, 323.89, 598.68 and 313.98 seconds. The three losing primaries were
canceled/reaped. No combined-condition helper reached its solve budget.

The final 6458 race in both was won by helper order 1 / slot 6: solve 286.95 s, window 598.68 s. Fresh none needed helper order 3 (1021.09 s window), spatial-only needed order 5 (1425.27 s), and time-only needed order 3 (959.45 s).

The analyzer verified condition sample hashes, bound provenance/configuration, effective threshold receipts, retained lifecycle artifact hashes, and primary-start evidence. No unavailable primary start or auxiliary multiset mismatch was reported. This is not a claim that native coordinate layouts match across temporal representations.

## Numerical interpretation

All 504 winners pass the retained acceptance gate. No fresh pair exceeds the 0.1% objective-change inspection threshold; the largest absolute relative change over all six comparisons is approximately **0.01691%**. This does not establish identical operating points or global optimality.

Winner statuses are optimal / optimal_inaccurate: none 122 / 4, time-only
122 / 4, spatial-only 118 / 8, and both 120 / 6. Thus acceptance does not mean
every winner had an unqualified optimal status; all passed the existing gate.

For none→both, maximum absolute differences across all windows/times/devices include Pg 85.54 MW, storage active power 91.69 MW, SoC 91.69 MWh, Qg 185.72 MVAr, storage reactive power 132.87 MVAr, nondispatchable reactive power 568.18 MVAr, voltage magnitude 0.05394 p.u., and wrapped angle difference 1.659 degrees. Maxima need not occur in the same window. Full six-pair dispatch differences and objective flags are retained in the raw summary.

Acceptance is the existing status/residual policy. Its injection-balance audit uses lifted injections and does not independently reconstruct every nonlinear network equation from voltage and Ybus. Do not describe this analysis as a new independent nonlinear feasibility certification.

## Memory, temperature and limitations

| Condition | Peak sampled worker / aggregate RSS (GiB) | CPU temperature range (°C) |
|---|---:|---:|
| none | 10.65 / 16.36 | 40.5–74.9 |
| time_only | 4.14 / 11.60 | 49.4–79.5 |
| spatial_only | 6.55 / 13.74 | 51.0–78.9 |
| both | 2.69 / 7.54 | 47.1–77.6 |

RSS values are sampled maxima; aggregate includes the supervisor. Temperatures are machine-wide observations, not controlled covariates. There is one replay per condition, a fixed sequential condition order, concurrency within each condition, and varying helper competition. This supports a practical candidate choice on this frozen sample, not universal speedups, a replicated causal benchmark, or a production-wide compatibility policy. The earlier sparse-dispatch trigger finding remains separate from this performance comparison.

## Evidence and reproduction

`results/case118_four_way_120plus6_dense_control/four_way_summary.json` and each condition’s comparison.csv/summary.json were refreshed with the existing no-solve analyzer:

```sh
UV_CACHE_DIR=/tmp/cvxopf-provenance-uv uv run --no-sync --extra dev python -m experiments.case118_spacetime_pq_replay.analyze_study
```

Compact reviewed evidence: `artifacts/study_final_summary.json`. Raw inputs, failed/canceled attempts, launch/completion receipts, RSS and thermal telemetry remain in the ignored study root. No result inputs were replaced. No project defaults, dependencies or solve code changed.

Independent scientific review completed on 2026-09-25 with no material findings
remaining. The reviewer reconstructed weighted timing means, six contrasts,
geometric speedups, faster counts, status/attempt counts and winner changes from
frozen samples and retained lifecycle phases without calling the analyzer.
The reviewer verified all 3,202 lifecycle artifact hashes, all 124 bound source
hashes, summary bindings, objective-change maximum, none→both dispatch maxima,
and combined-condition race outcomes/reaping. The angle wording was clarified
to explicitly identify wrapped differences. This closes the final analysis
checkpoint, not a new nonlinear-feasibility certification or production policy.
Owner retains staging/commit/publication control.
