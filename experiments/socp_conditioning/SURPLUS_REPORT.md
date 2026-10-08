# Case118 surplus conditioning comparison

2026-10-02, owner-approved descriptive continuation after the Case9 objective
comparison failed. That earlier record and its threshold remain unchanged.
All four fresh-process arms completed serially in about 22.4 seconds, while the
original E3 study continued. There were no production-code or active-checkout
changes, no retries, and no changes to numerical acceptance thresholds.

## Results

Exact E3 arm 002: Large surplus, [3308,3332), 24 hourly intervals, frozen fleet,
profiles, costs and individual energy-neutral endpoints. Each arm has the same
mathematical input digest. Solver controls are unchanged from E3.

| Representation | Native status | Original-unit physical/accounting audit | Max P imbalance (MW) | Iterations | Solve wall (s) | Original objective | Native/original objective discrepancy |
|---|---|---|---:|---:|---:|---:|---:|
| Baseline | AlmostSolved | Fail | 0.117107027 | 132 | 3.251 | 191.541666 | 2757.684432 |
| Normalized device cones | AlmostSolved | Pass | 2.316e-12 | 44 | 1.088 | 3.122148 | 6.041e-6 |
| Objective divided by 1e6 | AlmostSolved | Fail | 0.332566103 | 209 | 5.031 | 165023.776175 | 21345.314580 |
| Both changes | Solved | Pass | 2.068e-12 | 66 | 1.565 | 38.128885 | 54.514441 |

The baseline reproduced the original E3 objective, balance violation, native
objective gap and iteration count exactly. Original-unit generator, storage,
renewable, load-shedding, voltage, branch, cone, recurrence and endpoint audits
all pass in the normalized-cone and combined arms. The baseline and objective-only
arms fail active balance; their displayed costs are **not feasible objective
comparators**. Timings are descriptive, not isolated-machine benchmarks.

Normalized cones reduce the largest canonical RHS from 2,989,930.145 to 2,511.888
and remove the device squared-power auxiliary construction. This intervention
removes the observed balance failure in this window. It does not alone satisfy
the strict native-status requirement: cones-only still terminates AlmostSolved.

## Native objective discrepancy localized

All native objectives reconstruct from retained P, c and native x; the canonical
constant offset is zero. Splitting the objective by canonical variable blocks
shows that the discrepancy is entirely explained (up to floating-point arithmetic)
by slack in the storage absolute-value epigraph:

| Arm | Actual storage cost, 0.01 sum(abs(b)) | Native epigraph storage cost, 0.01 sum(t) | Excess |
|---|---:|---:|---:|
| Baseline | 192.131658 | 2949.816090 | 2757.684432 |
| Normalized cones | 3.127493 | 3.127499 | 0.000006 |
| Objective / 1e6 | 180.627430 | 21525.942010 | 21345.314580 |
| Both | 38.043616 | 92.558057 | 54.514441 |

For the combined arm, `t - abs(b)` sums to 5451.444065918 over devices and time;
multiplication by 0.01 gives 54.5144406591783. Individual epigraph slacks range
from 0.00223949 to 19.4454752 MW. This is not a missing objective term, constant
offset, or public-extraction error. The auxiliary variables have not been driven
tightly to the absolute values, despite native Solved termination.

Dividing the complete objective by 1e6 preserves relative economic weights but
turns the cycling coefficient from 0.01 into 1e-8. It does not reduce the ratio
between large shedding coefficients and small cycling coefficients. This experiment
shows that whole-objective rescaling by this particular factor is not an adequate
standalone numerical remedy.

| Arm | Native gap, original cost units | Absolute dual stationarity infinity norm, original objective units |
|---|---:|---:|
| Baseline | 0.00266980 | 0.237416 |
| Normalized cones | 0.000141387 | 0.000195468 |
| Objective / 1e6 | 17.783992 | 1.879980 |
| Both | 0.0000719604 | 0.0463872 |

Stationarity is reconstructed as `(P*x + c + A.T*z) * objective_divisor`.
Coordinates are representation-specific; these norms are descriptive, not a
coordinate-invariant certificate. The combined native gap is small, but its
unscaled stationarity residual and epigraph slack explain why it cannot be treated
as a rigorous original-objective error bound. No certified optimality or AC
feasibility claim is made for any SOCP arm.

## Disposition

No arm meets all predeclared successful-arm conditions. Cones-only fails native
Solved status; combined fails native/original objective consistency. Therefore
the fixed selection rule selects no variant and **no deficit diagnostic runs**.
The descriptive surplus matrix is complete, not an overall successful scaling
qualification. No AC test or production migration is authorized by this result.

The useful positive result is specific: normalized device cones eliminate the
observed power-balance inconsistency here without altering physics or economics.
The next decision is whether to extend this representation-only comparison to
the deficit window despite AlmostSolved, retaining numerical status and physical
feasibility as separate outcomes. Objective/dual accuracy remains a separate
question; do not tune acceptance thresholds to promote these records.

## Evidence and reproducibility

Raw artifacts: `results/diagnostic_002/`. Each arm retains complete native x/s/z,
canonical sparse matrices and vectors, original-unit result/audits, variable and
constraint layouts, source and environment context, verbose log and supervision.
All four input identities, before/after source contexts, archive hashes and
canonical-file hashes were independently checked after completion. Worker peaks
were 332–355 MiB. Concurrency was one additional worker, one solver thread.

- Base commit: `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`.
- Qualification: isolated uncommitted experiment, unchanged production sources.
- Summary SHA-256: `c4da2c2ce345d59fa4f100c3e34adbc8e2bb58c2417797c5da2b86a50d4f86bc`.
- Binding SHA-256: `9fe90c81822c9b36480ded6cbad717b77513bd957b2ed9d8c8a479eff0e84fd4`.
- Protocol SHA-256: `7a932e6f85ee3cf0f1a329231cb6869b1ddd27f0fd3fa0aa5688d7844e002213`.
- Runner SHA-256: `52e8b84495f6f094746fe705e950dcb88e9cabeb7f81f524ff371c00f7fea806`.
- Original smoke summary: `158c5f3c04f55a367db99514d1a6bb2c3ea337bd5df03c0655b2a7c8f06bf0a2`.

The original Case9 failed cross-arm gate remains visible in every continuation
binding. No authoritative study result has been rewritten or promoted.
