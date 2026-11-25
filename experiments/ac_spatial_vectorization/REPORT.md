# AC branch-terminal vectorization: benchmark results

Batching the branch-terminal equations passed this comparison. It made model
construction and derivative setup faster on every case, cut logged Hessian
evaluation time per call by about a factor of three, and lowered sampled peak
memory in every solve pair. End-to-end solve time fell in six of seven cases.
Large surplus was the exception: the batched solve took more IPOPT iterations
and returned a different battery schedule at nearly the same audited cost.

## What we compared

We compared the baseline AC builder (`812aa7d62`) with the branch-terminal
batched builder (`6ab39dac7`) on six 24-hour Case118/Tracy scenarios and the
single-step PGLib 500-bus GOC case. The change batches the four real and reactive
terminal-flow definitions. It does not change the network equations, thermal
limits, nodal balances, numerical preparation, or public API. Both arms use the
same case inputs, physical starting values, and effective IPOPT options within
each pair. Both Tracy arms used the recommended E3/E3+ preparation; both PGLib
500-bus arms used the stock single-step AC builder. No prepared 500-bus pair
was measured. The [benchmark protocol](README.md) describes the cases, controls,
and audit criteria.

Each case had three cold compile-only pairs and one cold solve pair. The runs
used fresh serial workers with one numerical thread, 16 GiB sampled RSS and
3,600 s whole-worker limits, and IPOPT 3.14.19 with a 3,000-iteration limit.
All 42 compile units were measured, and all 14 solves passed the physical audit.
All seven solve pairs also matched on the verified physical start and applied
native options. No solve failed, timed out, or hit the memory limit.

## Model setup and derivative evaluation

The setup columns show the median (min–max) of the three within-pair
baseline/batched ratios for each case. Build, DNLP reductions, and derivative
setup are measured separately before the solve. The last column is a diagnostic
from the accepted solve logs: the SparseDifferentiation Hessian evaluation
subtotal divided by IPOPT's logged number of Lagrangian Hessian evaluations.
It is not a standalone evaluation benchmark or a total IPOPT timing.

| Case | Build (×) | DNLP reductions (×) | Derivative setup (×) | Hessian ms/call, baseline → batched |
| --- | ---: | ---: | ---: | ---: |
| Large surplus | 2.13 (1.86–2.44) | 2.51 (2.27–2.73) | 1.78 (1.76–1.78) | 418.4 → 120.7 |
| Large deficit | 2.01 (1.90–2.16) | 2.39 (2.28–2.44) | 1.82 (1.80–1.84) | 392.4 → 122.1 |
| Deficit depletion | 2.03 (1.99–2.03) | 2.62 (2.40–2.64) | 1.77 (1.72–1.82) | 397.4 → 122.7 |
| Surplus to deficit | 2.05 (1.91–2.10) | 2.54 (2.41–2.76) | 1.81 (1.80–1.84) | 388.5 → 122.9 |
| Deficit to surplus | 2.03 (1.88–2.10) | 2.53 (2.28–2.70) | 1.79 (1.68–1.84) | 388.5 → 119.2 |
| Forced shedding | 2.04 (2.03–2.11) | 2.41 (2.41–2.49) | 1.79 (1.77–1.80) | 393.4 → 118.9 |
| PGLib 500-bus (stock) | 2.70 (2.70–2.74) | 2.70 (2.62–2.74) | 2.03 (1.99–2.08) | 229.3 → 72.4 |

Across all 21 pairs, the smallest speedups were 1.86× for construction, 2.27×
for DNLP reductions, and 1.68× for derivative setup. On the stock 500-bus
case, median build time fell from 0.535 to 0.198 s, reductions from 2.037 to
0.763 s, and derivative setup from 2.713 to 1.344 s. The from-end active-power
definition fell from 23,309 to 37 recursive expression nodes, while the native
problem stayed at 11,892 variables and 13,797 constraints.

Both 500-bus solves took 35 Hessian evaluations, and the logged evaluation
subtotal fell from 8.025 to 2.533 s. Forced shedding also had the same
evaluation count in both arms (88), with the subtotal falling from 34.617 to
10.463 s. Across all seven cases, logged Hessian time per call was 3.16–3.47×
faster with batching.

## Accepted solve pairs

Build-to-extraction is elapsed time from model construction through result
extraction. Native IPOPT time comes from its log. Peak RSS is the supervisor's
sampled worker peak, not a continuous high-water mark. Each solve row is one
matched pair, so it does not estimate run-to-run timing variation.

| Case | Build-to-extraction (s), baseline → batched | Speedup | IPOPT (s), baseline → batched | IPOPT iterations, baseline → batched | Peak RSS (GiB), baseline → batched |
| --- | ---: | ---: | ---: | ---: | ---: |
| Large surplus | 814.71 → 1,016.26 | 0.80× | 802.00 → 1,009.39 | 289 → 391 | 8.37 → 7.51 |
| Large deficit | 243.09 → 192.20 | 1.26× | 230.80 → 185.18 | 119 → 112 | 10.19 → 6.00 |
| Deficit depletion | 132.32 → 103.57 | 1.28× | 120.00 → 96.58 | 73 → 70 | 9.90 → 7.21 |
| Surplus to deficit | 422.16 → 149.64 | 2.82× | 409.74 → 142.76 | 187 → 93 | 9.54 → 6.33 |
| Deficit to surplus | 361.55 → 273.54 | 1.32× | 349.02 → 266.68 | 153 → 136 | 9.57 → 7.50 |
| Forced shedding | 124.95 → 93.82 | 1.33× | 112.69 → 86.90 | 88 → 88 | 10.24 → 7.54 |
| PGLib 500-bus (stock) | 77.80 → 69.82 | 1.11× | 72.22 → 67.44 | 35 → 35 | 8.51 → 3.81 |

The largest absolute objective difference was 0.00588 cost units, in large
surplus (0.012% of its baseline cost). The other six pairs were closer in both
absolute and relative cost. Dispatchable generation (`Pg`) agreed within
0.00203 MW across all pairs. Renewable dispatch (`p_nd`) differed by up to
10.26 MW per device and hour in large surplus and 10.13 MW in large deficit.
Large-surplus battery power differed by up to 8.51 MW per device and hour.
Similar costs and dispatchable generation did not imply the same operating
trajectory.

Across the Tracy solves, audited P and Q balance errors were below `1.98e-8`
and `1.07e-7` per unit, respectively. On the stock 500-bus case they were below
`4.42e-7` MW and `3.01e-6` MVAr. The snapshot audit checks the CVXOPF model as
built, not every PGLib or MATPOWER constraint.

### Large surplus: a slower solve and a different schedule

The batched solve took 24.7% longer even though its logged Hessian evaluation
subtotal fell from 120.926 to 47.199 s. It took 391 IPOPT iterations rather than
289. The audited costs were 49.1960 and 49.1901 for baseline and batched,
respectively, almost entirely storage cycling cost. Both schedules met the
same terminal requirements. The largest device-level differences were 8.51 MW
in battery power and 10.82 MWh in state of charge. Fleet net battery power also
differed by up to 8.11 MW, so the change was not just a redistribution among
devices at fixed fleet power.

These are two physically accepted schedules with similar costs, not a bound
on the range of available schedules or evidence that flexibility caused the
extra iterations. Two questions are worth separating: how far schedules can
move within an operationally negligible cost increase, and how early a solver
finds a schedule in that cost window that passes the physical audit. The latter
would require auditing saved intermediate candidates in physical units;
neither the native objective trace nor IPOPT residuals alone identify a safe
earlier stopping point.

## Conclusion

The evidence supports keeping branch-terminal batching. The setup, evaluation,
and memory improvements are consistent across the tested cases. The solve-time
gain is case-dependent, and the large-surplus slowdown is reported as observed.
It is not a reason to change solver tolerances or stopping rules in this PR.
Profile the remaining construction and reduction costs before deciding whether
to batch thermal limits or change the nodal-balance assembly.

## Retained results

Benchmark 001 stopped during the compile stage after three finalized
measurements. The fourth attempt published a worker result but had no final
supervision receipt, so it remains uncredited. The continuation used the same
scientific settings with supervisor repair `fbb9ea473` and finished all 53
pending units on November 24, 2026 (PST). The 3,600 s accounting charge for the
interrupted attempt is not measured runtime and is excluded from the timing
comparisons above.

The original and continuation archives are retained locally under the ignored
`results/` directory; the raw artifacts are not part of the repository. The
continuation binding SHA-256 is
`7eafafe86d638592a5e9bba054389d51f3595d0611ba819c737705592490bdd7`.
From the repository root, reconstruct the paired timings and audit summaries
from the retained archives without solving:

```sh
uv run --no-sync --extra dev python -m experiments.ac_spatial_vectorization.run report \
  experiments/ac_spatial_vectorization/results/benchmark_001_continuation_001
```

For Hessian evaluation times, read `Hessian evaluation` and `Number of
Lagrangian Hessian evaluations` in each corresponding `worker.log`; these
diagnostics are not part of the paired timing output.
