# M14c solver characterization

## Disposition

The completed annual Case118 lossy-DC solve confirms CLARABEL as the selected
solver for this frozen study. A subsequent, explicitly non-promotional matrix
tested OSQP, SCS, and HiGHS with their default configurations on the identical
conditioned vectorized/SCIPY formulation at 24, 168, 720, and 8,760 hours.
Only one of the twelve alternative arms passed the frozen scientific audit:
HiGHS at 24 hours. None produced an accepted annual result.

The result does not establish a universal solver ranking. It characterizes one
Case118 scenario, one model representation, one machine, CVXPY 1.9.2, and the
default settings exposed through that stack. No solver was tuned and no failed
arm was retried.

The characterized solver versions are CLARABEL 0.11.1, OSQP package 1.1.3
(native banner 1.0.0), SCS 3.2.11, HiGHS 1.15.1 (`04024d7`), and MOSEK
11.2.3. These versions are part of the interpretation boundary because default
solver behavior can change between releases.

## Results

| Solver | 24 h | 168 h | 720 h | 8,760 h |
|---|---:|---:|---:|---:|
| CLARABEL | accepted, 0.16 s | accepted, 1.05 s | accepted, 5.32 s | accepted, 93.95 s |
| MOSEK | accepted, 0.20 s* | accepted, 2.21 s* | rejected, 7.35 s* | failed/UNKNOWN, 177.48 s |
| OSQP | residual rejection, 1.25 s | residual rejection, 11.33 s | residual rejection, 54.43 s | 300 s limit |
| SCS | residual rejection, 3.84 s | residual rejection, 70.27 s | 300 s limit | unusable `unbounded`, 9.57 s |
| HiGHS | accepted, 11.31 s | solver failure, 20.74 s | 300 s limit | 300 s limit |

Times are solver/interface wall times when an arm completed and supervisor wall
limits otherwise. The three starred MOSEK prefix results are historical,
unconditioned, single-thread references and are not matched comparisons. The
annual MOSEK arm used the conditioned inputs, default tolerances, and 14
threads; it ended with native status `UNKNOWN` and no usable primal.

OSQP and SCS often returned nominal solver statuses but missed the frozen
physical residual gates. At 720 hours, OSQP's maximum nodal-balance residual
was 0.216 pu and its SoC recurrence residual was 0.0178 MWh. SCS classified the
accepted annual problem as unbounded. HiGHS reproduced the 24-hour objective
and audit, then reported the known-convex 168-hour QP as non-convex and exceeded
the five-minute limit at the longer horizons.

## Reproducibility

The compact machine-readable record is
`M14C_SOLVER_MATRIX_RESULTS.json`. It binds the exact tracked runner and the
ignored raw protocol, reference, supervision, arm, and log chain through
SHA-256 digests. The raw 248 KiB output remains under
`experiments/case118_annual_hierarchy/results/s4_solver_matrix_001`.

This characterization does not alter solver authority, annual authority, or
the accepted annual outer trajectory. CLARABEL remains the frozen lossy-DC
solver for the Case118 hierarchy.
