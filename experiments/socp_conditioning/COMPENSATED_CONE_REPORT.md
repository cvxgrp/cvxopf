# Compensated SOC determinant results

The compensated calculation corrects the captured false-zero determinant and
allows native CLARABEL to finish cone scaling and factor the next KKT matrix.
The optimization still stops before another state update because other SOC
operations continue using the original cancellation-prone determinant. The
final solution and gap are unchanged. This is a successful arithmetic test,
not a successful optimization or a qualified production remedy.

## Declared intervention

Executed 2026-10-06 under [COMPENSATED_CONE_PROTOCOL.md](COMPENSATED_CONE_PROTOCOL.md).
Only `_sqrt_soc_residual` received an opt-in fallback when its original finite
determinant was nonpositive and its leading coordinate positive. The fallback
uses power-of-two normalization, FMA product residuals, and TwoSum compensated
addition. Positive original computations take the unchanged path. No positive
floor, tolerance relaxation, different optimization model or new solver was used.

One fresh SuperLU-path solve used the frozen 24-hour Case118 Tracy surplus
window [3308,3332), normalized device cones, joint input scaling, fixed loads,
and fixed-coordinate substitution. The native input hash matched the earlier
trial exactly. Every recorded iteration field except elapsed time matched
the reference through iteration 32, before the first fallback.

## Arithmetic gate

Ten native fixture records were checked against 90-digit arithmetic on exact
binary64 inputs. They covered the captured vector, adjacent leading-coordinate
values, an exact boundary, an exterior point, an ordinary interior point,
zero and negative-leading-coordinate inputs, and power-of-two rescalings by
2^-400 and 2^400. The signed determinant helper and separate positive-head
interiority interpretation were checked; this does not claim a new policy for
the unchanged positive-result branch of the native square-root routine.

The captured determinant is **1.10941666464733335e-16**, not zero. Compensated
arithmetic reproduced it with relative error **6.44e-17**, recovering square
root **1.0532885001970416e-8**. Boundary and exterior cases remained zero or
negative as appropriate; there was no sign clipping.

During the full replay a second false-zero determinant was encountered and
corrected: **1.23029518530365269e-15**, with relative error **3.29e-17**.
Both corrections occurred at iteration 32. All cone scaling then completed;
there was no `CONE_FAILURE` event.

## Optimization result

| Measure | Prior SuperLU replay | Compensated square-root fallback |
| --- | ---: | ---: |
| Native termination | NumericalError | NumericalError |
| Native iteration counter | 32 | 33 |
| Absolute gap | 2.10649e-9 | 2.10649e-9 |
| Relative gap | 6.74077e-10 | 6.74077e-10 |
| Last applied step | 0.893667 | No new step applied |
| Final x/s/z | Reference | Exactly unchanged |
| Independent physical checks | Pass | Pass |
| Objective reconstruction error | 7.20530e-11 | 7.20530e-11 |
| Full acceptance | No | No |

The new run took **25.48 s native**, **27.85 s worker-local**, and **29.60 s
supervised wall time**, with **887.9 MiB** combined sampled worker/native/bridge
RSS. It stayed within the declared 180-second and 4096-MiB limits. The iteration
counter advanced to 33 because another Newton calculation began, not because
another solution iterate was accepted. No retry or second changed arm ran.

## Why the next calculation stops

After the two corrected scaling checks:

1. The new SuperLU factorization succeeds. The constant solve retains residual
   3.95703125 against tolerance 9.82547e-10; the affine solve retains
   2.64845e-9 against 1.31221e-9. These remain unresolved accuracy limitations.
2. The affine step-length routine reads the unchanged `_soc_residual(x)`.
   On the same bus-77/78 cone vector it obtains zero, takes its boundary-case
   branch, and reports allowable step zero.
3. High-precision reconstruction of that vector and the newly retained
   direction gives a positive boundary step of **0.5150369076886725**.
   The zero step is therefore another manifestation of the same cancellation.
4. The combined-step path calls `Δs_from_Δz_offset`, which also reads the old
   determinant and divides by it. The native trace then shows a nonfinite
   corrector RHS, rejected by the bridge, followed by `NumericalError`.

The division-by-zero source path and nonfinite RHS agree, but individual
corrector intermediates were not instrumented, so this is not an assertion
that every nonfinite component has been individually localized. The false
zero in step-length evaluation is directly demonstrated by retained vectors.

## Decision

Compensated arithmetic works on the identified inputs. The narrow square-root
patch is insufficient because SOC scaling, step selection, and corrector
construction do not yet share the corrected calculation. The next coherent
test is to apply the same signed compensated determinant through the shared
SOC primitive and explicitly inspect its shifted-vector and inverse-operation
callers. Preserve negative determinants for direction vectors; do not turn
the shared determinant into an interiority-only check.

That consistency change has not been implemented or run here. It might remove
these repeated false zeros but could still expose inaccurate linear solves or
genuinely exterior rounded iterates. The full solver and physical/accounting
gates remain mandatory. The current result remains nonpromotional and rejected.

## Evidence and verification

Runner: [compensated_cone.py](compensated_cone.py). Offline reconstruction:
[compensated_cone_analysis.py](compensated_cone_analysis.py). Tests:
`tests/test_socp_conditioning_compensated_cone.py` and native
`compensated_cone_tests` in the saved source patch.

Use the existing isolated single-thread environment with
`uv run --offline --no-sync --with coptpy==8.0.7 --with mosek==11.2.5 python -B`:

```text
-m experiments.socp_conditioning.compensated_cone
  --output experiments/socp_conditioning/results/compensated_cone_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3

-m experiments.socp_conditioning.compensated_cone_analysis
  --root experiments/socp_conditioning/results/compensated_cone_001
```

Reproduction requires a fresh output root; historical outputs are immutable.
The native source patch, entrypoint, source commit, lockfile and binary are
bound by `engine.json`; `native-fixtures.json` retains the actual Rust test
output and high-precision checks.

| Artifact under `results/compensated_cone_001/` | SHA-256 |
| --- | --- |
| `summary.json` | `d68795811b88ce85e80fe75d626dd666e5f142ace809145b206455fb4ba94184` |
| `analysis.json` | `85c66a8d61ad8a8e385c45cee201d88f038c9c33e874698ff65c90d39e0a2089` |
| `compensated/arm.json.gz` | `4a7b8896fd68ef1f8bae7beaf561067cd8281e55c58efe0cdd2f99ac10bab15c` |
| `native-fixtures.json` | `68da6f6bf143d4a7b6f692d2213ec578d7230954b1e4fcd63f40d8e5c6b1d739` |
| `engine.json` | `390708c3873140cfab6112477862464c092568c8d67335f134c2810e694d8a9d` |

A Rust generic-type annotation was corrected before compilation and numerical
execution. After execution, one blank line was added to the Python runner for
Ruff formatting. The exact executed runner is preserved as `execution-runner.py`,
SHA `fff86a2e3b33e7a75ec4a7b739c6e729526e91df5b3e44b8a7d4fe396490fc08`,
matching the execution binding. Raw execution records were not rewritten.

Verification: 155 conditioning tests, 124 native library tests, Ruff lint and
format checks, artifact hashes and whitespace checks pass. No production source,
installed solver, main checkout or lockfile changed. Experimental work remains
unstaged and uncommitted in the authorized isolated tree.
