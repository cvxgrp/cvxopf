# Cone preserving scaling smoke test

The four predeclared solves completed on 2026-10-05. Exact equivalent scaling
reduced the full constraint matrix condition estimate from about 908,324 to
1.84 in both load configurations, but worsened convergence and original-unit
optimality evidence. This particular A-only scaling is not a successful remedy.
No production changes, additional optimization solves, or live-study changes
were made.

## Matched results

All arms used the same 24-hour surplus input, normalized capability cones,
paired bounds, original costs, CLARABEL 0.11.1 settings, and physical audit.
Fixed-load arms disable the optional shedding feature as previously declared.
Every solve returned `AlmostSolved`; all four physical/accounting audits passed,
but none passed the unchanged complete diagnostic acceptance predicate.

| Load policy and representation | Condition estimate of A | Iterations | Native solve seconds | Original objective | Original stationarity infinity norm |
|---|---:|---:|---:|---:|---:|
| Shedding baseline | 908,324 | 44 | 0.935 | 3.122148 | 1.955e-4 |
| Shedding scaled | 1.843 | 229 | 4.551 | 52.228997 | 8.760e-3 |
| Fixed-load baseline | 908,324 | 36 | 0.753 | 3.125903 | 5.729e-5 |
| Fixed-load scaled | 1.843 | 231 | 4.406 | 11.228583 | 3.784e-3 |

External scaling took 0.162 and 0.163 seconds, respectively, separately from
native solve time. Timings are descriptive on a shared machine. Both baselines
reproduced the retained objectives and iteration counts exactly. Scaled native
solve time was approximately 4.9 and 5.8 times baseline, not an improvement.

## Accuracy after mapping back

Original-unit stationarity errors grew about 45-fold with shedding and 66-fold
without it. The apparent native gaps decreased, but the original-unit
complementarity and stationarity evidence worsened:

| Arm | Absolute native primal dual gap | Complementarity s dot z | Canonical versus physical objective discrepancy |
|---|---:|---:|---:|
| Shedding baseline | 1.414e-4 | 2.328e-4 | 6.041e-6 |
| Shedding scaled | 7.745e-7 | 3.360e-2 | 6.844e-4 |
| Fixed-load baseline | 8.448e-6 | 1.320e-5 | 3.616e-7 |
| Fixed-load scaled | 5.719e-7 | 3.588e-2 | 6.656e-4 |

Both scaled native signed gaps were negative. Independent reconstruction
verified the gap identity: complementarity is largely cancelled by the
stationarity and primal-error terms. Small native gaps are not certified
objective-error bounds for these approximate dual points. Scaled arms also
fail the frozen canonical/physical objective-agreement threshold.

The much larger physical objectives are mainly actual storage cycling cost,
not just epigraph slack: storage cost rises from 3.1275 to 52.2268 with shedding
and from 3.1259 to 11.2283 without it. The baseline shedding objective includes
the previously recorded -0.00535 numerical shedding cost; that small effect
does not explain the much larger deterioration. Nothing here establishes a
certified optimum for the original baseline either.

## Equivalence and interpretation

Five deterministic row/column norm passes used positive diagonal R and D,
with one shared multiplier per SOC block. The new problem uses Ahat=R*A*D,
bhat=R*b, Phat=D*P*D and chat=D*c; original vectors are recovered as x=D*xhat,
s=shat/R and z=R*zhat. Costs and feasible sets are unchanged under this bijection.
Original canonical arrays exactly matched the historical references before
solving. Independent post-run reconstruction checked the retained transformed
matrices and all primal, slack and dual mappings.

This experiment targeted A rather than the full optimization data or Newton
system. It made A very well conditioned but increased the maximum quadratic
coefficient from about 2.96 to 46,487 and the nonzero b spread from about
1.14 million to 31.7 million. These are measured changes in numerical
representation, not economic changes. They make a joint P/A/b/c scaling or
barrier/KKT investigation more relevant than further optimizing kappa(A) alone;
they do not prove which part caused the deterioration. No follow-on setting
or solve has been selected automatically.

## Evidence and verification

The isolated runner is `cone_scaling.py`; independent offline reconstruction is
`cone_scaling_analysis.py`. Immutable raw evidence remains under
`results/cone_scaling_001/`. All four original/transformed matrix archives,
scale vectors, raw/mapped solutions, public results, audits, native settings,
logs and source contexts are retained. One solve per worker was verified.
Maximum supervisor RSS was 477.203 MiB; no resource limit was crossed.

The initial narrow eigensolver did not converge for the scaled shedding
matrix. A separately retained offline calculation with a wider Krylov space
estimated its condition number as 1.84304, with an original-coordinate singular
triplet residual of 6.90e-7. It did not invoke an optimizer or change any solve
artifact. All spectral values are estimates, not certified bounds.

- Root summary SHA-256: `1d7c5be27ead27d1c22c1a31390cc2e2d2cd6a3ea20247caf412576c60cbc24d`.
- Independent analysis: `results/cone_scaling_analysis_001.json`, SHA-256
  `3473fa9994856c1eb56cb9312dd2b61866794b2ecebf8d958bc36b4ef88a6032`.
- Base execution commit: `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`;
  experiment code intentionally uncommitted in the authorized isolated worktree.
- The live checkout remains clean and unchanged. No staging or commit performed.
- All 76 focused conditioning tests passed; Ruff and whitespace checks passed.
  Tests retained the known CVXPY import and zero-norm cone warnings.
