# Shared compensated SOC determinant results

Consistent compensated determinant evaluation improves the final gap by **2.26×**
over the prior SuperLU result, but does not achieve native `Solved`. It removes
the demonstrated false-zero step and permits another iterate. The next failure
is a genuinely exterior rounded dual vector: an exact-arithmetic interior step
crosses the cone boundary when its updated coordinates are rounded to binary64.
This is a further localized numerical improvement, not a completed remedy.

## Intervention and controls

Executed 2026-10-06 under [SHARED_CONE_PROTOCOL.md](SHARED_CONE_PROTOCOL.md).
The temporary native CLARABEL build routes shared signed SOC determinants
through power-of-two normalization and FMA/TwoSum compensation. This reaches
scaling, inverse Jordan operations, corrector offsets, and step-length
coefficients. Shifted residuals evaluate the same rounded shifted coordinates
with that shared primitive. The prior square-root-only fallback is disabled.

Mixed bilinear products, discriminants, root selection, step application,
regularization, refinement, tolerances, and the SuperLU KKT path are unchanged.
No positive determinant floor, clipping, or backtracking is introduced. This
tests equivalent arithmetic, not a changed feasible set or objective.

One fresh worker solved the same 24-hour Case118 Tracy surplus window
[3308,3332), with normalized device cones, joint input scaling, fixed loads,
and exact fixed-coordinate substitution. Native input SHA remained
`b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184`.
Shared arithmetic changes rounding throughout the run: the first recorded
numerical difference is iteration 1. An identical early trajectory is neither
claimed nor required. There was one optimization call, no retry or added arm.

## Arithmetic qualification

All 12 native fixture cases pass comparison with 90-digit arithmetic on exact
binary64 inputs. They cover interior, exterior, boundary, zero, negative-head,
signed-direction, adjacent-coordinate, shifted-coordinate, and power-of-two
±400 cases. Negative determinants remain negative.

The previously false-zero allowable affine step becomes
**0.5150369076889154**, versus high-precision boundary
**0.5150369076886725**. The inverse Jordan operation returns finite values on
the captured near-boundary input. These tests do not qualify all possible
floating-point magnitudes or establish accuracy of every downstream operation.

## Optimization result

| Measure | Prior SuperLU / square-root-only trial | Shared compensation |
| --- | ---: | ---: |
| Native status | NumericalError | NumericalError |
| Absolute gap | 2.10649e-9 | 9.33882e-10 |
| Relative gap | 6.74077e-10 | 2.98842e-10 |
| Native primal residual | — | 8.13303e-14 |
| Native dual residual | — | 1.14964e-15 |
| Independent physical residual checks | Pass | Pass |
| Objective reconstruction error | 7.20530e-11 | 3.19496e-11 |
| Full acceptance | No | No |

The new native iteration counter is 33 and a new state was actually applied,
unlike the square-root-only trial. The relative gap remains about **3×** its
1e-10 target. Native time was **26.27 s**, worker-local time **28.40 s**, and
supervised wall time **30.60 s**. Combined sampled worker/native/bridge RSS
peaked at **889.3 MiB**, below the declared 4096-MiB limit; the wall limit was
180 seconds. No bridge errors were recorded.

## The next failure: rounding the state update

The failing voltage-product cone maps to buses **78–79**, local interval 9,
global interval **3317**. Native rows [29288,29292) map to original canonical
rows [31109,31113). This is not the prior bus-77/78 cone.

The retained previous vector, direction and applied step reproduce the failed
rounded coordinates exactly. High-precision reconstruction separates step
selection from coordinate rounding:

| Dual-cone quantity | Value |
| --- | ---: |
| Previous exact-input margin | +1.50031e-16 |
| High-precision boundary step | 0.19581866683009586 |
| Native boundary step | 0.19581866683052995 |
| Applied step (0.99 × native boundary) | 0.19386048016222465 |
| Margin after exact-arithmetic update | +1.50031e-18 |
| Margin after actual binary64 update | −4.01833e-17 |
| Determinant after actual binary64 update | −1.06321e-16 |
| Leading-coordinate floating-point spacing | 2.22045e-16 |

The step is below the exact boundary; its tiny intended interior margin is
lost during coordinate rounding. The compensated determinant correctly rejects
the resulting exterior vector. The primal side remains interior. Consequently
another determinant correction or positive floor would misdiagnose this stop.

Linear accuracy also remains unresolved: the last combined solve retains
residual **5.18776e-9** against tolerance **1.11224e-9**. The immediate stop is
localized to the rounded cone crossing, but this does not establish that
eliminating it alone would produce a fully converged optimum.

## Interpretation and next test candidate

The shared primitive is more coherent than patching only the scaling check.
It fixes the specific cancellation defects and gives measurable progress on
the unchanged problem, while exposing a different finite-precision limitation.

A focused next candidate is **rounded-update interiority checking with bounded
step backtracking**: evaluate the actual proposed binary64 cone coordinates
before accepting an update, and shorten the step when rounding makes them
noninterior. Retain the original objective, tolerance and acceptance gates;
do not project coordinates or label an exterior point interior. The check
would need to cover all relevant cones and homogeneous scalar domains and
retain a finite unsuccessful termination when no usable step exists. This
report does not implement or authorize that next test, or guarantee convergence.

## Evidence, reproduction and verification

Runner: [shared_cone.py](shared_cone.py). Offline analysis:
[shared_cone_analysis.py](shared_cone_analysis.py). Regression tests:
`tests/test_socp_conditioning_shared_cone.py`. The retained native source patch,
entrypoint, lockfile and binary identity are bound by `engine.json`.

In the existing isolated, single-thread environment, using
`uv run --offline --no-sync --with coptpy==8.0.7 --with mosek==11.2.5 python -B`:

```text
-m experiments.socp_conditioning.shared_cone
  --output experiments/socp_conditioning/results/shared_cone_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3

-m experiments.socp_conditioning.shared_cone_analysis
  --root experiments/socp_conditioning/results/shared_cone_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3
```

Use a fresh output root to reproduce; existing outputs are immutable. Offline
analysis verifies retained artifact hashes and reconstructs the step without
an optimizer call. The runner and frozen protocol were not edited after execution.

| Artifact under `results/shared_cone_001/` | SHA-256 |
| --- | --- |
| `summary.json` | `05d4a558fff8dde26ee3465bd4baa43e68be75111e50a3dc7c482e75f2b343e5` |
| `analysis.json` | `8af951839d69cfcf455fa4213a769d03b8c18ad993c1f711d7298523df8539aa` |
| `shared/arm.json.gz` | `f60bb72188f0c0b4e45736563fff236b0294f4a2154f6a4b872d69aa8ed56045` |
| `native-fixtures.json` | `0c06e5431af20c1acbfa0e74f0d2c2503390a7d92a65c7c6bfc422db1137f3de` |
| `engine.json` | `f47dc8a73e23fa43c4c3a982487b2a8345bbfffc18be46b7677d9aa34929ff9d` |

Verification: 158 conditioning tests and 125 serial native library tests pass.
Known test warnings concern mixed OpenMP imports, a boundary norm division,
and an upstream Rust lifetime annotation. Production sources, installed solvers,
lockfiles and the clean main checkout are unchanged. The isolated experiment
remains unstaged/uncommitted and nonpromotional; no workers remain active.
