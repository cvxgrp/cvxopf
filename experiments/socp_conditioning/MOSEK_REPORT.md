# Default-tolerance MOSEK check

2026-10-02. One configuration: the same Case118 24-hour surplus input, normalized
device cones, original paired box inequalities and unscaled objective. MOSEK
11.2.5, default numerical tolerances, one thread, 180-second/4-GiB supervision.
No production changes or edits to the live environment. Temporary `uv --with`
installation retained CVXPY 1.9.3, NumPy 2.4.6 and SciPy 1.17.1.

## Result

MOSEK is **not a qualified fix** for this instance. Its native optimal status
does not pass the original-unit diagnostic gate.

| Measure | CLARABEL, default equilibration | MOSEK, default tolerances |
|---|---:|---:|
| Native status | AlmostSolved | Optimal |
| Native optimizer seconds | 1.091 | 0.699 |
| Iterations | 44 | 30 |
| Extracted physical objective | 3.122147605 | 23.492277916 |
| Canonical primal objective | 3.122153646 | 23.777217066 |
| Native absolute primal/dual gap | 0.000141387 | 0.000056002 |
| Canonical/physical cost discrepancy | 0.000006041 | 0.284939150 |
| Physical audit | Pass | Reject |
| Overall diagnostic acceptance | Reject | Reject |

MOSEK's only failing physical quantity is shedding-fraction violation:
1.89627e-8 versus the unchanged 1e-8 limit (reported in both the physical and
relaxation audit). Active balance residual is 2.18988e-6 MW, comfortably within
1e-4 MW. Terminal and recurrence residuals are zero in the returned values.
This is a small feasibility violation, not a grossly infeasible physical result;
the much larger objective disagreement remains scientifically important.

MOSEK costs: generation 0.015492204, storage cycling 22.744094191, shedding
0.732691521. CLARABEL costs: generation 0.000002561, storage 3.127493395,
shedding -0.005348351. The small negative CLARABEL shedding cost is a numerical
bound effect and prevents treating its reported objective as an exact reference.
Neither native gap is being promoted to a verified optimality certificate.

The retained default MOSEK conic primal/dual feasibility and relative-gap
tolerances are 1e-8; infeasibility tolerance is 1e-12 and near-relative factor
1000. Native reported primal/dual feasibility measures are 1.17049e-6 and
1.68785e-9. These belong to CVXPY's **dualized** MOSEK task, not directly to
the physical nodal equations. Its maximization-task dual objective is the
canonical minimization primal objective shown above (offset zero).

CVXPY dualizes and conifies the model for MOSEK; CLARABEL receives its QP-conic
representation. This is an interface/solver-path comparison, not an algorithm
comparison on identical canonical matrices. MOSEK canonicalization plus solving
took 0.986 seconds; the fresh supervised process took 3.645 seconds and peaked
at 477.1 MiB sampled RSS. No broader runtime conclusion follows from one case.

## Evidence and publication repair

The first solve finished but publication failed because MOSEK returns native
vectors as `array.array`. Its log, task and failed supervision are preserved in
`results/mosek_001`. One identical repeat after list conversion is retained in
`results/mosek_002`; no tolerances or model settings changed. The task gzip is
byte-identical across both attempts. A solver-free regression protects native
array serialization.

- Input SHA-256: `0cf7b0a6eb7c093ecd00f667a3f3cbec5e0701715449f0c701ee09bdaeca598f`
  (matches the retained CLARABEL comparator).
- Arm SHA-256: `dfd376099bf47fa325ed9b3ff144ee0782de16bc5350b71e7013467b82d953ad`.
- Task SHA-256: `072e384f61f1cdaab28be62d47826a0bf5ddc9033dcbee9cd3a4c4496b43e20f`.
- Completed supervision SHA-256:
  `67e4e37542cf8371fd78498ef638f48b36957a250eb30a6b3c4ef14a062da0b8`.

All registered artifact hashes and before/after execution contexts were checked.
The arm retains native task vectors, public results, original model values,
costs and audits. Execution was from the declared uncommitted isolated experiment
on base `dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`, with runner/source hashes.
The main checkout remains clean and untouched. No additional solves authorized.
