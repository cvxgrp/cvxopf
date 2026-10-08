# Native cone scaling failure results

The observational replay identifies a specific finite-precision failure:
CLARABEL rejects a strictly interior dual SOC vector because its rounded norm
equals its leading coordinate. This explains the immediate `NumericalError`
in the SuperLU experiment. It does not establish that correcting the arithmetic
will complete convergence or resolve the remaining constant-RHS solve errors.

## Experiment and controls

Executed 2026-10-06 under the [declared protocol](CONE_INTERIORITY_PROTOCOL.md).
One instrumented native SuperLU replay used the unchanged Case118 Tracy surplus
window [3308,3332), normalized device cones, joint input scaling, fixed loads,
and exact fixed-coordinate substitution. CLARABEL 0.11.1, SuperLU settings,
regularization, refinement, and full/reduced 1e-10 tolerances were unchanged.

The replay exactly matched the preceding SuperLU arm's complete returned x/s/z,
native status, iteration, gap and residual values. Instrumentation only copied
internal vectors and printed diagnostic values; no numerical operation or
acceptance gate was replaced. There was one optimization call and no retry.
Physical row mapping and high-precision arithmetic used no further solves.

| Retained measure | Result |
| --- | ---: |
| Native status | `NumericalError` |
| Iteration | 32 |
| Absolute gap | 2.10649e-9 |
| Relative gap | 6.74077e-10 |
| Independent physical checks | Pass |
| Original objective reconstruction error | 7.20530e-11 |
| Full acceptance | No |
| Native solve time | 24.66 s |
| Worker-local wall time | 26.84 s |
| Supervised wall time | 28.58 s |
| Combined sampled worker, native and bridge peak RSS | 889.0 MiB |

The single worker stayed within 180 seconds and 4096 MiB. Process monitoring
worked; macOS reported no recorded thermal/performance warning. These are
diagnostic timing observations, not a production performance comparison.

## Failed cone and arithmetic

Native rows [27844,27848) map through the frozen fixed-coordinate substitution
to original canonical rows [29665,29669). No native row elimination occurred.
The cone is the voltage-product SOC for buses **77 and 78**, pair index 125,
local interval 7, global interval **3315**. This is not an apparent-power
rating failure or evidence that the physical relaxation is infeasible.

The native input-interiority check fails before constructing the scaling vector
w. Its dual vector is:

```text
[ 2.334893449993115,
 -2.33471439015205,
 -0.02762315770132989,
 -0.008549876975618733 ]
```

CLARABEL evaluates the SOC determinant using
`(z[0] - norm(z[1:])) * (z[0] + norm(z[1:]))`. Its overflow-safe norm
returns exactly `2.334893449993115`, equal to `z[0]`, giving determinant zero.
The offline reconstruction reproduces that native norm exactly.

At 90-digit precision, using the **exact binary64 inputs** rather than their
decimal approximations, both incoming vectors are strictly interior:

| Quantity | Primal s | Dual z |
| --- | ---: | ---: |
| Margin before the preceding step | 8.10566e-14 | 9.90744e-15 |
| Margin after exact arithmetic step | 1.02535e-14 | 4.04221e-16 |
| Margin of actual rounded post-step vector | 7.45634e-15 | 2.37573e-17 |
| Determinant of actual rounded vector | 3.09020e-13 | 1.10942e-16 |
| Native computed determinant | 2.94477e-13 | 0 |

The dual margin is only **0.05350 ULP** of its leading coordinate, or
1.01749e-17 relative to that coordinate. Even a correctly rounded norm cannot
preserve such a margin when it is subtracted from the leading value. An
overflow-safe norm is not sufficient for this near-boundary determinant.

## Preceding step

The retained s/z/ds/dz and applied step reproduce the actual failed vectors
exactly with the native binary64 update order. No homogeneous rescaling
occurred between them. The applied step was **0.8936666889361046**; the
high-precision dual boundary along that direction was **0.9316789295816977**
(primal boundary 1.023084609530641).

Thus the step was interior in exact arithmetic, and even the rounded update
remained interior. Rounding substantially reduced its remaining dual margin,
then norm-then-subtract erased that margin. This observation distinguishes the
current failure from a genuinely exterior vector or a failed scaling-vector w.
It does not establish that every other cone remains safely separated from its
boundary: native scaling stops at the first failed check.

## Interpretation and next decision

The result supports testing a narrowly scoped, compensated or higher-precision
SOC determinant calculation, first on these saved vectors and boundary/exterior
regressions. A subsequent full native SuperLU replay could test whether that
arithmetic correction gets past iteration 32 and satisfies the unchanged gate.
Simply computing squares and subtracting them naively is not a demonstrated
remedy; that expression can also suffer cancellation.

Do not clip a nonpositive determinant to an arbitrary positive floor or loosen
the gap tolerance to declare success. Genuine boundary/exterior inputs must
remain distinguishable. Accurate arithmetic may expose a later limitation,
including the previously observed inaccurate constant-RHS solve. No arithmetic
remedy, production adoption or additional optimizer run is implemented here.

## Evidence and reproduction

Runner: [cone_interiority.py](cone_interiority.py). Offline reconstruction:
[cone_interiority_analysis.py](cone_interiority_analysis.py). Regression tests:
`tests/test_socp_conditioning_cone_interiority.py`.

From the isolated worktree, use the established single-thread environment and
`uv run --offline --no-sync --with coptpy==8.0.7 --with mosek==11.2.5 python -B`:

```text
-m experiments.socp_conditioning.cone_interiority
  --output experiments/socp_conditioning/results/cone_interiority_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3

-m experiments.socp_conditioning.cone_interiority_analysis
  --root experiments/socp_conditioning/results/cone_interiority_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3
```

Historical outputs are immutable; reproduction requires a fresh output root.
The engine record binds the temporary native source patch, entrypoint, lockfile,
compiler, source commit and compiled binary. The context binds Python source,
protocol, dependencies and platform separately from post-run analysis.

| Artifact | SHA-256 |
| --- | --- |
| `results/cone_interiority_001/summary.json` | `6518a9317525bc06737527295c3d65940b3d0f9cbeeb30fcc0cab6957cf6dd20` |
| `results/cone_interiority_001/analysis.json` | `01b4cc121b8ad91e524d584f75212de8006d2f9140d25829598a53e255f446cf` |
| Instrumented `arm.json.gz` | `2c3509ff4da97edc65a7a9c4061463120554fec676ca6d6851bc94250ff1652f` |
| Native input | `b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184` |
| Native engine record | `25238d487b3f8ccc25ccb392086cd06785e8833ae3a1084b9f0895fcba560092` |

Verification: 151 conditioning tests, 122 native library tests, Ruff lint/format,
artifact checks, and whitespace checks pass. Tests include the actual captured
dual vector, exact replay rejection, malformed evidence, and step reconstruction.
Known warnings concern mixed OpenMP imports, a zero-norm synthetic cone test,
and an upstream Rust lifetime annotation. No worker or bridge remains running.

Production sources, installed solvers and the main checkout remain unchanged.
The protocol, runner, offline analysis, tests and report are unstaged in the
authorized isolated worktree; raw evidence remains ignored. No commit was made.
