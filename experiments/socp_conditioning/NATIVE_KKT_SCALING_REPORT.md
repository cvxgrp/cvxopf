# Five pass scaling inside native CLARABEL

The native five-pass KKT intervention worsened this fixed-load surplus solve.
The disabled control exactly reproduced the prior result, but the enabled arm
stopped ten iterations earlier with a much larger returned primal-dual gap.
Neither arm was accepted. This is a negative result for the tested QDLDL
scaling rule, not evidence that all possible KKT scaling is ineffective.
Final report completed 2026-10-06.

## Question and controlled change

The [saved-matrix scaling study](KKT_SCALING_REPORT.md) improved some linear
residuals. This test asks whether the same five-pass idea improves the complete
optimization when applied at every native KKT refactorization.

The Case118 Tracy surplus window [3308,3332), fixed loads, normalized device
cones, joint input scaling, exact fixed-coordinate substitution, objective,
constraints, and acceptance gates are unchanged. Both arms receive byte-identical
native problem and settings files. CLARABEL 0.11.1 retains QDLDL, one thread,
5,000 maximum iterations, full/reduced gap and feasibility tolerances of 1e-10,
and the previously tested minimum-step value 1e-8.

The isolated Rust patch is opt-in through `CVXOPF_KKT_SCALING=5`. At every
refactor it resets D to one, applies the native static regularizer in original
coordinates, and performs five symmetric infinity-norm Ruiz passes on K + E.
Per-pass factors are clipped to [1e-8,1e8], cumulative factors to [1e-16,1e16].
QDLDL factors D(K + E)D; RHS scaling and solution mapping use D. Iterative
refinement still checks the original unregularized K and original RHS.

QDLDL's dynamic pivot thresholds are unchanged numerically but now act in
scaled coordinates. This changes their effective original-coordinate
perturbation. The trial therefore tests scaling together with its interaction
with the existing factorization and regularization, not an invariant replay
of the same regularized Newton directions.

## End to end results

| Measure | Disabled control | Five pass native scaling |
| --- | ---: | ---: |
| Native status | InsufficientProgress | InsufficientProgress |
| Reported iterations | 31 | 21 |
| Returned canonical primal objective | 3.12499877308 | 3.18985412484 |
| Returned canonical dual objective | 3.12499848046 | 2.16225597627 |
| Returned absolute gap | 2.92617e-7 | 1.02759815 |
| Returned relative gap | 9.36376e-8 | 0.475244 |
| Reconstructed physical objective | 3.12499876304 | 3.15443279032 |
| Canonical versus physical objective discrepancy | 1.00380e-8 | 0.0354213 |
| Independent physical residual checks | Pass | Pass |
| Complete acceptance | Reject | Reject |
| Native solve seconds | 0.8944 | 0.8550 |
| Supervised worker wall seconds | 5.190 | 5.184 |
| Sampled combined Python and native RSS MiB | 495.69 | 514.50 |

Timings include tracing and are descriptive, not evidence of a speedup. The
scaled arm performs less optimization progress. Both workers completed normally
within 180 seconds and 4096 MiB; normal process completion is not solver success.

The disabled x/s/z vectors match the saved wheel baseline bit for bit. This
gate passed before the enabled arm launched. The enabled arm's physical audit
passes numerically, but native success and the objective-reconstruction gate
both fail. A physically plausible rejected iterate is not an accepted optimum.

## Failure mechanism in the trace

The enabled run completed 22 scaled factorizations, including initialization.
At the last one, scaled row maxima lie between 0.63947 and approximately 1.
Nonetheless its three last original-coordinate linear residuals are:

| Linear solve | Residual after refinement | Requested tolerance |
| --- | ---: | ---: |
| Constant RHS | 1.84294 | 9.82547e-10 |
| Affine predictor | 3.14702e-3 | 1.31305e-9 |
| Combined corrector | 2.95695e-3 | 1.02420e-9 |

All miss their targets. Refinement improves each residual but stops under the
unchanged native improvement-ratio rule. Balanced rows are not sufficient for
an accurate mapped Newton solve.

At iteration 21, native primal infeasibility rises from 4.84495e-12 to
1.72882e-8, a factor of 3,568.3. It exceeds both 100 times the previous residual
and 100 times the feasibility tolerance. With embedding ratio below one,
CLARABEL's existing divergence test returns `InsufficientProgress` and restores
the preceding iterate. Thus the returned objective and gap in the table belong
to iteration 20, although the termination counter is 21. The rejected trial
iterate had gap 0.296527; substituting that value into the returned-result table
would mix two different iterates.

The control instead reaches its known tiny-step failure near iteration 30.
The traces follow different optimization paths, so their final linear residuals
are not a comparison on the same KKT matrix. Neither trace isolates dynamic
regularization as the sole cause.

## Disposition

Do not adopt this native scaling variant. It leaves the optimization problem
unchanged but degrades attained accuracy on the tested instance. Factorization,
regularization, and refinement remain unresolved numerical mechanisms. Further
variants require a separately declared test; none is launched by this report.
The result does not change the earlier favorable evidence for normalized device
cones plus joint input scaling, which is a different intervention.

## Reproducibility and verification

- Runner: [native_kkt_scaling.py](native_kkt_scaling.py).
- [Root summary](results/native_kkt_scaling_001/summary.json), SHA-256
  `7acd0b5d6d829c2f6eaa23fbad6a1c2a997efc2078d6fb18571529ea027c99f3`.
- Shared native input SHA-256:
  `b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184`.
- Control arm SHA-256:
  `15100b388a57d59e320a663df52f2860aaa2121fcd687544f4dd550ae7e48284`.
- Five-pass arm SHA-256:
  `d30fbd973a88fb1db901913619fdca2a8d5a90e4099ec6912342446708267f83`.
- `engine.json` retains the complete native patch, example entrypoint, Cargo
  lockfile, source commit `25540f559592068d0c8a80e46ded1b21760212a1`,
  binary hash, Rust version, and build command. Each supervised arm retains
  native inputs/outputs, solver trace, original-unit result/audit, and vectors.

All supervision-bound artifact hashes were rechecked. The isolated conditioning
suite passed 134 tests; the native library passed 122 tests, including congruence,
RHS preservation, solution mapping, reset-per-refactor, and invalid-row checks.
Ruff lint/format and whitespace checks passed. The production checkout stayed
clean; no installed solver, dependency lockfile, or production source changed.
