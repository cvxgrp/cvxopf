# Normalized-cone objective-divisor sweep

2026-10-02. Owner-approved descriptive follow-up, run serially in four fresh
workers against the same Case118 Large surplus 24-hour inputs. All arms use
normalized device cones. No production source, live-study record, physical gate,
solver setting or relative cost was changed. Earlier smoke and surplus outcomes
remain immutable. Raw outputs: `results/sweep_001/`.

## Observations

| Objective divisor | Native status | Physical/accounting audit | Actual objective | Native/actual objective discrepancy | Original-unit dual stationarity infinity norm | Iterations | Solve wall (s) |
|---|---|---|---:|---:|---:|---:|---:|
| 1 | AlmostSolved | Pass | 3.122147605 | 6.041e-6 | 1.955e-4 | 44 | 1.090 |
| 10 | AlmostSolved | Pass | 3.252410385 | 1.204e-5 | 5.017e-2 | 250 | 5.363 |
| 100 | AlmostSolved | Pass | 35.492959723 | 1.759e-3 | 6.657e-3 | 95 | 2.173 |
| 1000 | Solved | Pass | 41.558465283 | 39.443708068 | 1.066e-2 | 87 | 2.002 |

Maximum original-unit P-balance errors are respectively 2.316e-12, 1.423e-11,
5.315e-12 and 9.042e-9 MW, all comfortably within the unchanged 1e-4 MW gate.
The divisor-1 replay reproduces the previous cones-only objective, residual,
native gap and iteration count exactly. The mathematical input digests are
identical across all four arms. Worker peaks are 332–341 MiB; total supervised
sweep elapsed time was approximately 21.3 seconds. Timings are descriptive:
another study was executing concurrently on the machine.

The native/original objective discrepancy in each arm is entirely accounted for
by storage absolute-value epigraph slack, using retained native variables and
the original 0.01 cycling coefficient. In the divisor-1000 arm, the excess is
39.44370806757323 cost units despite native Solved termination. Original-unit
native primal–dual gaps are 1.414e-4, 1.124e-8, 1.080e-4 and 6.115e-8; these are
not rigorous objective-error certificates when dual feasibility is approximate.
Dual stationarity is reconstructed from `(P*x + c + A.T*z)*divisor`.

## Interpretation and boundary

No tested divisor improves the combined physical/objective evidence over divisor
1, and none passes every declared successful-arm condition. A smaller divisor
does not monotonically improve convergence or economic accuracy. The best observed
physical-audit-passing objective remains the unscaled normalized-cone point; this
is not a proof of its global optimality. Its tiny negative reported shedding cost
(-0.00535) reflects fraction-bound error permitted by the unchanged physical
tolerance, and remains visible rather than clipped or silently repaired.

The clear gain remains the normalized device-cone representation. Dividing the
whole objective shifts absolute numerical scales but preserves the large ratio
between shedding and cycling coefficients. This sweep does not establish a useful
rescaling factor or authorize changing solver tolerances. No deficit, AC, further
factor, retry, production migration or promotion follows automatically.

## Evidence

All arm/archive/supervision hashes, source contexts and physical-input identities
were checked after completion. Native canonical objective and dual residuals were
independently recalculated from retained sparse matrices and full x/s/z.

- Base commit: `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`; isolated experiment changes uncommitted.
- Summary SHA-256: `12743cbac9dae3abb6d454aa0aa8efdce2229cf31a10c2e8b860f0b6a9eede3a`.
- Binding SHA-256: `8b21f6509ee439fc26f72bdce9c438dbbdaf724e69bc0e050fea4223060e27fa`.
- Protocol SHA-256: `3f865a18ae5471f060ac1167e24d7e4bd48f2e89d49fbdbda856f3ee9fbd8eb6`.
- Runner SHA-256: `8084f62575ae63fddf5bc64b8570598e0dbc0b8c9d3d94f24a67715a5c14b4f5`.
