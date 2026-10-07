# Joint objective and constraint scaling

The four predeclared CLARABEL solves completed on 2026-10-05. Joint scaling
of P, A, b and c improved original-unit optimality evidence substantially on
the same 24-hour Case118 Tracy surplus data. Unlike the preceding A-only
experiment, it did not produce a large objective deterioration. All four
physical audits passed, but every arm still returned `AlmostSolved`, so none
passed the unchanged complete diagnostic acceptance gate.

## Method

The matched arms retain normalized capability cones, paired bounds, original
device costs, solver settings and physical acceptance tolerances. One pair
allows optional shedding; the other fixes load service. Each arm runs once
in a fresh supervised single-thread worker, serially, with no tuning or retries.

Five simultaneous square-root norm-balancing passes consider the symmetric
data layout [P, A.T, c; A, 0, b; c.T, b.T, 0]. Its final coordinate stays at
one: there is no global objective rescaling. This is a data layout, not the
barrier-dependent KKT matrix. Each SOC block receives a common positive row
scale; cumulative scales are clipped to [1e-6, 1e6].

The transformation is Ahat=R*A*D, bhat=R*b, Phat=D*P*D, chat=D*c.
Mapping back uses x=D*xhat, s=shat/R, z=R*zhat. This preserves the mathematical
problem. Offline reconstruction verified artifact hashes, transformed arrays,
all solution mappings, and exact reproduction of the declared scaling rule.

## Matched results

| Load policy and arm | Estimated condition number of A | Iterations | Native solve seconds | Original objective | Original stationarity infinity norm |
|---|---:|---:|---:|---:|---:|
| Shedding baseline | 908,324 | 44 | 0.941 | 3.122147605 | 1.955e-4 |
| Shedding joint scaling | 28.94 | 33 | 0.768 | 3.124998767 | 4.014e-5 |
| Fixed-load baseline | 908,324 | 36 | 0.749 | 3.125903472 | 5.729e-5 |
| Fixed-load joint scaling | 15.01 | 30 | 0.667 | 3.124998763 | 2.970e-11 |

Spectral values are estimates, not certified bounds. The two scaled physical
objectives differ by only 4.57e-9. The baseline shedding objective includes
-0.00534835 of numerical shedding cost; its apparently lower objective is not
evidence of a better feasible solution. Joint scaling reduces that cost to
positive 8.63e-10. Nearly all remaining objective is storage cycling cost.

| Arm | Absolute native primal dual gap | Original complementarity s dot z | Native versus physical objective discrepancy |
|---|---:|---:|---:|
| Shedding baseline | 1.414e-4 | 2.328e-4 | 6.041e-6 |
| Shedding joint scaling | 1.322e-8 | 1.478e-8 | 3.909e-10 |
| Fixed-load baseline | 8.448e-6 | 1.320e-5 | 3.616e-7 |
| Fixed-load joint scaling | 3.007e-7 | 3.439e-7 | 1.031e-8 |

Original-unit stationarity improves about 4.9-fold with shedding and
1.9 million-fold without it. Improvement is not uniform: the shedding
canonical primal residual rises from 1.57e-8 to 6.04e-8, while the fixed-load
residual falls from 1.47e-6 to 1.95e-9. Physical audits still pass in all arms.
Approximate native gaps are not rigorous objective-error certificates.

## Timing and interpretation

Native solve time decreases by 18.4% and 10.9%, respectively, but external
scaling takes 0.174 and 0.171 seconds. Scaling plus solver-interface time is
0.966 versus 0.964 seconds with shedding and 0.860 versus 0.771 seconds for
fixed load. These single observations support an accuracy improvement, not
an end-to-end speedup. Peak externally sampled RSS stays below 493 MiB.

The earlier A-only rule achieved a lower condition estimate near 1.84 but
worsened convergence. Joint scaling leaves A less perfectly balanced while
keeping the largest nonzero P coefficient near one and reducing the c/b
coefficient spreads. This supports considering objective and right-hand-side
data together, rather than optimizing the condition number of A alone. It
does not establish which change caused the improvement or resolve the
remaining `AlmostSolved` termination. No acceptance threshold was relaxed.

## Evidence and verification

- Runner: `joint_scaling.py`; frozen definition: `PROTOCOL.md`.
- Immutable solve artifacts: `results/joint_scaling_001/`, including source
  and transformed matrices, scales, native and mapped solutions, public
  results, settings, audits, logs, provenance and supervision.
- Root summary SHA-256:
  `4324d75e8fdd6656e71ab6cbf8e77030629b271a4fa6171eac8b010a2a5d118d`.
- Independent analysis: `results/joint_scaling_analysis_001.json`, SHA-256
  `dfbac36b5a60d78c173c3cabf5cb9846d71bb66cc6ec073d917de9d630bf12be`.
- Base commit: `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`; experiment
  sources intentionally uncommitted and fingerprinted in the isolated worktree.
- All 80 focused tests passed. Changed Python files pass Ruff lint and format
  checks. The known CVXPY OpenMP import and zero-norm cone warnings remain.
- No production-source, main-checkout or live-study changes; no staging or commit.
