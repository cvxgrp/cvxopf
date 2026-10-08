# CLARABEL minimum step ablation

Reducing `min_terminate_step_length` from 1e-4 to 1e-8 did not improve the
fixed-load surplus solve. The returned primal, slack, and dual vectors are
bitwise identical to the preceding run, and termination remains
`InsufficientProgress` at iteration 31. This particular cutoff reduction is
not a remedy. Final report completed 2026-10-06 from retained evidence.

## Question and controlled intervention

The question was whether CLARABEL was discarding a useful small step before
reaching the requested accuracy. One fresh serial worker ran the Case118 Tracy
24-hour surplus window [3308,3332), with normalized device cones, exact fixed
coordinate substitution, joint objective and constraint scaling, and fixed
load service. Only the minimum-step parameter changed.

CLARABEL 0.11.1 retained its native QDLDL path, one thread, 5,000-iteration cap,
and full and reduced gap/feasibility tolerances of 1e-10. The supervisor limits
were 180 seconds and 4096 MiB. Costs, physical constraints, and acceptance gates
did not change. The comparator is `termination_probe_001/clarabel`.

## Results

| Measure | Previous cutoff 1e-4 | New cutoff 1e-8 |
| --- | ---: | ---: |
| Native status | InsufficientProgress | InsufficientProgress |
| Iterations | 31 | 31 |
| Absolute gap | 2.92617365e-7 | 2.92617365e-7 |
| Relative gap | 9.36376022e-8 | 9.36376022e-8 |
| Native primal residual | 5.80305146e-14 | 5.80305146e-14 |
| Native dual residual | 2.52975914e-15 | 2.52975914e-15 |
| Reconstructed physical objective | 3.12499876304 | 3.12499876304 |
| Native solve seconds | 0.6991 | 0.7025 |
| Independent physical residual checks | Pass | Pass |
| Overall acceptance | Reject | Reject |

The callback history matches exactly apart from elapsed times. The new worker
finished in 3.652 seconds, with sampled peak RSS 466.47 MiB. These are
descriptive timings, not a speed comparison. CVXPY's solver exception is
retained alongside the native rejected iterate; the record does not label
that iterate optimal merely because its physical residuals pass.

## Interpretation and disposition

The tested cutoff was not the limiting fix. This test alone does not establish
why the step was too small; it motivated the separately authorized
[native step tracing](NATIVE_STEP_REPORT.md), which subsequently localized
catastrophic linear-solve errors upstream of the step-length calculation.
No acceptance tolerance was relaxed and no result was promoted.

## Evidence and verification

Runner and comparison: [minimum_step.py](minimum_step.py). The comparison checks
identical physical inputs and canonical problem, the single allowed option
change, exact callback history excluding time, and exact final x/s/z vectors.

- [Root summary](results/minimum_step_001/summary.json), SHA-256
  `eea6b058ada32bf26dded4c17faa404fe9638c870f18a04697d6ef01bf23c6d2`.
- [Matched comparison](results/minimum_step_001/comparison.json), SHA-256
  `37fbd1dbfd21144f863806592b40120e021668395d0467a1b0b8b7f3def1f540`.
- New arm SHA-256:
  `9dc63831645db3a65eeb0fa77faa1b2dc3bd4cd9fb1ea3eb21b99c897b9972ff`.
- Comparator arm SHA-256:
  `ae37e20f1f97a43eee0ebaa62ee63e5e551673976221016c0074a428aa412677`.

The retained context binds the uncommitted isolated experiment sources,
production base `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`, dependencies,
platform, and source hashes. Production code and installed solver were unchanged.
The latest conditioning regression suite includes the minimum-step tests and
passed all 134 tests; that later verification is not a rerun of this experiment.
