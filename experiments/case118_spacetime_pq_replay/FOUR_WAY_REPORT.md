# Hour 6047 four-way vectorization results

Commit: `56a531b5f0d6f6d737d83a449b07714973553622`. One fresh, isolated primary attempt per condition, fixed sequential order. CVXPY 1.9.3 / sparsediffpy 0.6.1; frozen physical inputs, start and numerical settings. Full provenance and logs are in `results/case118_6047_four_way/`.

All four attempts reached the 3,000-iteration limit and were rejected. Disabling both vectorization controls did not restore convergence in the current branch/environment. This does not isolate a dependency regression or establish a root cause; shorter times are times to rejected termination, not successful-solve speedups.

| Condition | Status | Accepted | Iterations | Native IPOPT (s) | Solve phase (s) |
|---|---|---|---:|---:|---:|
| none | user_limit | False | 3000 | 717.402 | 723.441 |
| time_only | user_limit | False | 3000 | 284.574 | 286.528 |
| spatial_only | user_limit | False | 3000 | 448.483 | 451.879 |
| both | user_limit | False | 3000 | 196.344 | 197.464 |

Total wall time: 1690.29 s. Sum of solve phases: 1659.31 s. Mean: 414.83 s; median: 369.20 s. Completed throughput: 8.52 attempts/hour; accepted throughput: 0.00/hour (0/4 accepted).

Solve phase includes canonicalization, exact start capture/persistence and solver return; native IPOPT time comes from its log. Preparation includes reconstructing the historical model/start and rebuilding the selected representation, so it is not pure build time. Mean/median describe four different conditions, not timing replication.

| Comparison | Second / first solve time | Difference (s) |
|---|---:|---:|
| time_only vs none | 0.396 | -436.91 |
| spatial_only vs none | 0.625 | -271.56 |
| both vs none | 0.273 | -525.98 |
| spatial_only vs time_only | 1.577 | +165.35 |
| both vs time_only | 0.689 | -89.06 |
| both vs spatial_only | 0.437 | -254.41 |

## Native termination and physical audits

IPOPT native metrics and extracted physical audits are distinct evidence. Rejected objectives are not feasible-cost comparisons. Native metrics below are unscaled final-summary values, which can differ from the last printed iteration.

| Condition | Native constraint violation | Native dual infeasibility | Extracted active balance (pu) | Extracted reactive balance (pu) | SoC recurrence (MWh) | Terminal SoC (MWh) |
|---|---:|---:|---:|---:|---:|---:|
| none | 5.2913e-05 | 7.37497e+10 | 5.99167e-07 | 3.76209e-06 | 8.83442e-06 | 1.26655e-06 |
| time_only | 0.00132648 | 3.89466e+11 | 2.08489e-07 | 1.67991e-07 | 1.901e-07 | 1.58515e-07 |
| spatial_only | 13.2718 | 2.80082e+18 | 8.37126e-11 | 4.66923e-12 | 0.000442103 | 7.81597e-14 |
| both | 0.0106138 | 2.06751e+08 | 0.000273147 | 0.00634154 | 0.00450974 | 0.00340185 |

## Verification and limitations

Recorded execution source hashes verified from unchanged files or the bound Git commit; retained start artifact hashes verified.

Thermal observations: 168 samples; errors: []; CPU min/median/max 39.0/52.6/63.1 C. Machine-wide telemetry does not isolate solver power or establish throttling.

Canonical start comparisons across the spatial toggle:
- none / spatial_only: full x0 exact = True; normalized layout exact = True.
- time_only / both: full x0 exact = True; normalized layout exact = True.

Comparison with the preceding isolated two-way diagnostic (historical, not additional fresh observations):
- spatial_only: extracted result exact = True; physical residuals exact = True; full x0 exact = True; normalized layout exact = True.
- both: extracted result exact = True; physical residuals exact = True; full x0 exact = True; normalized layout exact = True.

One attempt per condition and fixed order do not establish replicated performance or a root cause. Historical successful runs used a different dependency environment. No retries, recovery attempts, or additional solves were run.
