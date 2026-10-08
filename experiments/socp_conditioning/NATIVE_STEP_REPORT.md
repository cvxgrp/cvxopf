# CLARABEL stalled step diagnosis

The fixed-load 24-hour surplus case stalls after a catastrophic loss of
accuracy in its internal Newton linear-system solves. The tiny allowable step
is a consequence of the resulting direction, not merely the minimum-step
termination threshold. This identifies a numerical failure mechanism in this
CLARABEL run; it does not establish the same cause for MOSEK or COPT.

## Exact reproduction

An isolated build of CLARABEL 0.11.1, source commit
`25540f559592068d0c8a80e46ded1b21760212a1`, replayed the exact matrices and
settings from the preceding minimum-step diagnostic. A control replay and an
instrumented replay both reproduced every retained primal, slack, and dual
coordinate bit for bit. Both ended at iteration 31 with `InsufficientProgress`.

The problem retained normalized device cones, joint scaling, fixed load, exact
fixed-coordinate substitution, and `min_terminate_step_length=1e-8`. No cost,
constraint, solver tolerance, or acceptance gate changed during this test.
The installed solver and main checkout were untouched. The two fresh workers
ran serially, with one solver thread and limits of 180 seconds and 4096 MiB.
Observed combined worker/native RSS peaked at 503.6 MiB. Native solve times
were 0.80 and 0.86 seconds; instrumented time is not a performance comparison.

## What fails before termination

At the iterate printed as iteration 30, the next Newton update requires three
reduced KKT solves. The tracing records the infinity norm of `rhs - K*x` against
the solver's unregularized internal KKT matrix, after refinement. These are
linear-system residuals, not the optimization problem's feasibility residuals.

| Linear-system solve | RHS infinity norm | Final residual infinity norm | Requested residual tolerance |
| --- | ---: | ---: | ---: |
| Constant RHS | 9.82e3 | 1.06e25 | 9.83e-10 |
| Affine predictor | 1.31e4 | 3.56e9 | 1.31e-9 |
| Combined corrector | 1.28e27 | 4.27e34 | 1.28e14 |

Refinement attempts make these residuals worse: respectively 1.97e41,
2.25e24, and 1.74e51, so the solver retains the preceding directions.
The source's refinement routine treats a finite result as successful even
when it does not meet its requested tolerance. It can therefore continue to
step-length selection with these inaccurate directions.

The deterioration is abrupt. One iteration earlier, the constant-RHS residual
was 0.00428 (already above tolerance), while predictor and corrector residuals
were 3.74e-10 and 1.15e-9 and met their respective tolerances.

This proves that the final directions do not accurately solve the intended
internal linear systems. It does not by itself distinguish near-boundary KKT
ill-conditioning, regularization, or numerical factorization behavior, and is
not a measured KKT condition number or forward-error bound.

## Which cones restrict the step

The final limiting cones map through the retained canonical row and fixed-variable
substitution maps to global interval 3322, local interval 14 of `[3308,3332)`:

- The voltage-product SOC for buses 85 and 86 first restricts the combined
  step to about 1.20e-38.
- The receiving-end apparent-power SOC on branch 86 to 87, rated 141 MVA,
  further restricts it to 6.60e-39.

The branch cone's current dual vector is order one, but its combined dual
direction reaches 4.31e33. Its current dual cone margin is about 3.43e-12.
Recalculating the cone-boundary intersection at 90-digit precision from the
exact retained binary64 vectors gives 6.601407e-39, consistent with the native
6.601451e-39. The tiny step is therefore not explained by rounding an otherwise
useful boundary intersection to zero. The source applies its small-step guard
and reports a zero step in the final iteration.

These cones identify where the bad direction is blocked. They are not evidence
that those physical constraints should be removed or that they caused the
factorization failure.

## Scientific status and next diagnostic

The retained last iterate still passes all independent physical residual checks.
Its absolute primal-dual objective gap is 2.926e-7 and relative gap 9.364e-8;
neither meets the requested 1e-10 criterion. Native success and overall
acceptance remain false. No result is promoted.

The next focused investigation should capture the failing KKT matrix and RHS,
then check an independent sparse factorization and its residual. That would
separate an unreliable factorization/regularization path from difficulty inherent
in the late-iteration matrix. A controlled linear-solver or regularization test
would leave the optimization problem unchanged; neither was run here.

## Retained evidence

All paths are relative to this experiment directory.

- `native_step_probe.py` implements the two serial replays through the existing
  `OPFBuild.solve()` boundary; `native_step_analysis.py` reconstructs the evidence
  without any optimizer calls.
- `results/native_step_probe_002/summary.json` SHA-256:
  `09540d796e3d3ebd7aac1f3b4d874ed80aacb795624790c129cee20d3b7c1fee`.
- `results/native_step_probe_002/engine.json` retains the exact native source
  patch, entrypoint, Cargo lockfile, source commit, build command, and binary hash.
- `results/native_step_probe_002/instrumented/worker.log` SHA-256:
  `e656a173803b2e8be82dd76ff17c5c2cc1d10063823a60e5a45b5b847d9460b8`.
- `results/native_step_probe_002/trace-analysis.json` retains every refinement
  residual, step restriction, physical row mapping, and high-precision check.
- `results/native_step_probe_001` preserves an adapter preflight failure:
  the installed wheel lacked a JSON-export method. No optimizer ran in that
  attempt. The corrected wrapper serializes the exact supplied matrices directly.

The native source logic is in
`src/solver/core/kktsolvers/direct/quasidef/directldlkktsolver.rs`,
`src/solver/implementations/default/kktsystem.rs`, and
`src/solver/core/cones/socone.rs` at the source commit above. Native instrumentation
and toolchain are isolated under `/tmp`; the complete retained patch and lockfile
allow reconstruction without replacing the installed Python solver.
