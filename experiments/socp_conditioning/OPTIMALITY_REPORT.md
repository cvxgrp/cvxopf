# Offline canonical optimality audit

2026-10-02. No new solve, model build, live-study edit, or source migration.
`optimality_audit.py` reads the immutable `sweep_001` divisor-1 and divisor-1000
arms, verifies completion/arm/canonical hashes, and works on in-memory copies.
All costs below are restored to original objective units.

## Epigraph tightening

The audit identifies all 648 storage absolute-value auxiliaries by exact sparse
structure: two nonnegative-cone inequalities with opposite unit coefficients on
the corresponding storage power, coefficient -1 on the auxiliary, zero RHS,
positive auxiliary cost and no quadratic auxiliary term. It does not assume a
generated variable name. Setting only those auxiliaries to absolute storage
power leaves every other constraint row exactly unchanged.

| Quantity | Cones, divisor 1 | Cones, divisor 1000 |
|---|---:|---:|
| Native status | AlmostSolved | Solved |
| Canonical objective before | 3.122153646 | 81.002173350 |
| Canonical objective after | 3.122147605 | 41.558465283 |
| Avoidable epigraph cost | 0.000006041 | **39.443708068** |
| Maximum equality error after | 4.55e-13 | 9.04e-11 |
| Maximum nonnegative-cone violation after | 9.68e-12 | 1.42e-17 |
| Maximum SOC radial violation after | 7.39e-10 | 0 |

Every cone block is checked from `b-Ax`, including all network and device cones.
The changed epigraph rows are feasible exactly in floating-point arithmetic;
the other small residuals were already present. These are canonical-unit errors,
not MW residuals. Both original physical/accounting audits passed. The tightened
canonical objective matches the original CVXPY expression to roundoff.
This is a numerical feasible-improvement demonstration, not an exact-arithmetic
feasibility or global-optimality certificate. No physical dispatch is improved by
this tightening alone: the scaled arm still costs 41.56 versus 3.12 for cones-only.

## Local economic accuracy versus global residual normalization

Original-unit stationarity is `(P*x+c+A.T*z)*objective_divisor`.

| Named group: maximum absolute stationarity | Divisor 1 | Divisor 1000 |
|---|---:|---:|
| Storage throughput epigraph | 1.87e-5 | **0.009999954** |
| Storage real power | 1.87e-5 | 0.00416725 |
| SoC | 2.53e-5 | 0.00773400 |
| Voltage squared | 1.35e-5 | 0.01066010 |
| Real voltage product | 3.23e-5 | 0.00483731 |
| Imaginary voltage product | 1.95e-4 | 0.00319222 |
| Dispatchable real power | 9.31e-10 | 2.79e-6 |
| Renewable real power | 1.13e-5 | 5.59e-5 |
| Load-shed fraction | 1.86e-9 | 1.82e-9 |

Group coordinates have different units; these maxima are localization evidence,
not directly comparable economic error bounds. For the storage epigraph the
comparison is unambiguous: its coefficient is 0.01 in original units. The maximum
stationarity error is 0.187% of that coefficient for divisor 1 and **99.99954%**
for divisor 1000. Some scaled-arm multiplier sums are only 4.60e-8 against the
required coefficient 0.01. Storage epigraph complementarity sums are 7.82e-6 and
0.01952 respectively; the discrepancy is not complementarity alone.

Clarabel 0.11.1 normalizes dual stationarity by
`max(1, ||c||_2 + ||x||_2 + ||z||_2)`, after undoing its internal scaling.
See the [tagged residual implementation](https://github.com/oxfordcontrol/Clarabel.rs/blob/v0.11.1/src/solver/implementations/default/info.rs)
and [2-norm implementation](https://github.com/oxfordcontrol/Clarabel.rs/blob/v0.11.1/src/algebra/vecmath.rs).
On returned vectors the denominators are 2.425e8 and 4.383e8. The scaled-arm
reconstructed normalized dual residual is 5.71031e-13, close to the retained
5.71085e-13, despite the local economic error above. Native gap 6.115e-11 in
scaled units and native primal/dual residuals satisfy the recorded 1e-10 tests.
The cones-only native relative gap 4.529e-5 satisfies the reduced 5e-5 test, not
the full requested gap tests.

Returned-vector reconstructions do not reproduce every native summary exactly:
cones-only dual is 9.685e-13 versus native 1.170e-12, and reconstructed primal
residuals also differ. Internal stopping iterates/equilibration were not archived;
the discrepancy is retained, not forced into agreement or diagnosed as a bug.
The homogeneous embedding kappa/tau guard was also not retained. Thus this
reconstructs the visible residual/gap tests, not every internal termination step.

## The large denominator is localized to exact fixed bounds

The canonical matrix independently identifies 840 generator coordinates and
981 renewable coordinates with coincident unary lower/upper bounds. Their
dual multipliers account for:

| Share of total squared dual norm | Divisor 1 | Divisor 1000 |
|---|---:|---:|
| Coincident generator bounds | 42.4566% | 46.6221% |
| Coincident renewable bounds | 51.3630% | 53.3779% |
| Combined | 93.8196% | **99.9999988%** |

Opposing fixed-bound multipliers can increase together without changing their
net stationarity contribution. This is a concrete dual-degeneracy/normalization
mechanism consistent with the observations; it does not establish why this
solver path chose those multipliers or prove that removing the pairs fixes it.

The small reported gap is also not a trustworthy economic certificate here.
The exact residual identity, reconstructed numerically, is
`primal_cost-dual_cost = x.T*rd + s.T*z - z.T*rp`.
For divisor 1000 its terms in original units are -0.03889560, +0.04054817,
and -0.00165264, cancelling to approximately -6.12e-8. Within `x.T*rd`, storage
epigraph and SoC contributions are approximately +73.187 and -73.275. Approximate
dual feasibility prevents reading the tiny net difference as an optimality bound.

## Recommended next experiment (not executed or authorized by this report)

Keep the normalized cones and unscaled objective. Replace only **exactly
coincident** opposing bounds with equality constraints (or eliminate those fixed
coordinates), preserving the mathematical feasible set, costs, and inputs.
Compare against the retained baseline using the same full physical, objective,
epigraph and local/global KKT audits. Do not collapse merely narrow boxes, remove
devices' reactive capability, retune penalties, or change the main experiment.
This is more targeted than building a second PowerModels device layer or another
objective-divisor sweep. Production changes require separate evidence/review.

## Reproduction and verification

From the isolated worktree, using its source and the existing environment without
syncing or modifying it:

```sh
UV_CACHE_DIR=/tmp/cvxopf-uv-cache \
UV_PROJECT_ENVIRONMENT=/Users/bmeyers/github/cvxopf/.venv \
OPENBLAS_NUM_THREADS=1 uv run --no-sync python -B \
  experiments/socp_conditioning/optimality_audit.py \
  --root experiments/socp_conditioning/results/sweep_001
```

The default prints the analysis; `--output NEW_PATH` refuses overwrite. Retained
final audit: `results/optimality_audit_003.json`, SHA-256
`e2fdee92f82f7af11a500059868d1bdb5a8f12bc515f8cb4238ecc750b700ddd`.
The artifact binds the analyzer hash and individual source artifacts. Earlier
001/002 files are development audit snapshots, not new numerical experiments.
Ten solver-free focused tests pass, including actual retained-artifact checks,
exact epigraph recognition/rejection, cone layout/membership, and cancelling
fixed-bound multipliers. Ruff is clean. No production file or existing numerical
artifact was changed; all new work remains uncommitted in the isolated worktree.
