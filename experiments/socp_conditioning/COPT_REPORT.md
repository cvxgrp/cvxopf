# Default-tolerance COPT comparison

2026-10-02. COPT 8.0.7 recognizes the owner's installed local trial license.
Same surplus input, normalized device cones, original paired inequalities and
unscaled objective as the MOSEK and normalized-cone CLARABEL checks. One thread,
default numerical tolerances, external 180-second/4-GiB limits; CVXPY's automatic
infeasible-or-unbounded retry disabled. Temporary package overlay only.

## Result

| Measure | CLARABEL | MOSEK | COPT |
|---|---:|---:|---:|
| Native status | AlmostSolved | Optimal | Optimal |
| Native solve seconds | 1.091 | 0.699 | 1.869 |
| Iterations | 44 | 30 | 43 |
| Physical objective | 3.122147605 | 23.492277916 | 3.116113562 |
| Canonical/physical discrepancy | 0.000006041 | 0.284939150 | 0.000267928 |
| Physical audit | Pass | Reject | Reject |
| Overall diagnostic acceptance | Reject | Reject | Reject |

COPT's physical rejection is reactive nodal balance: 0.000234827 MVAr versus
0.0001 MVAr. Active balance is 0.000072498 MW and passes. The canonical/physical
objective discrepancy also exceeds 1e-4 + 1e-8*abs(objective). No tolerance was
changed to accept the result. These are small absolute physical violations,
not evidence of a gross model infeasibility.

COPT's returned costs are generator 0.0000800231, storage 3.1251124112 and
shedding -0.0090788726. The negative shedding cost is numerical bound error;
the lower reported total must not be interpreted as a demonstrated better
feasible optimum. Storage cost is close to CLARABEL's 3.1274933948 and far
closer than MOSEK's 22.7440941910.

The native log retains primal/dual objectives 3.11638149 / 3.11638166 and
absolute/relative gaps 1.73e-7 / 5.55e-8. Reported primal infeasibility is
3.46e-7 absolute / 2.75e-10 relative; dual infeasibility is 3.77e-5 absolute /
5.77e-12 relative. Those native gaps alone are not verified objective-error
certificates. Full native returned primal and linear-dual arrays are archived.

Effective COPT FeasTol and DualTol are both 1e-6; BarIterLimit is 500. This is
a default-COPT-tolerance check, **not an equal-tolerance solver benchmark**:
the retained CLARABEL comparator uses the earlier declared 1e-10 tolerances.
Solver-specific canonicalization also differs. Native optimal status does not
override the unchanged original-unit audit or cost-accounting gate.

Canonicalization plus solve took 2.222 seconds; the supervised fresh worker
took 5.240 seconds, with sampled peak RSS 472.84 MiB. One case is not a broad
performance characterization. No additional solves or tuning were performed.

## Evidence and publication repair

`results/copt_001` retains the first completed solve's task/log/supervision.
Its JSON publication failed because CVXPY's COPT adapter puts a live native
Model in extra_stats, which the relaxation audit retains. The identical solve
was repeated once after omitting that nonserializable handle with an explicit
reference to the separately retained numerical evidence. `results/copt_002`
completed publication and both attempts have byte-identical task files.
Solver-free regression coverage checks this export and acceptance logic.

- Input SHA-256: `0cf7b0a6eb7c093ecd00f667a3f3cbec5e0701715449f0c701ee09bdaeca598f`.
- Arm SHA-256: `64bc4e998368766d8a13560a22e978934a58910936e69bc30ede487d832a770b`.
- Task SHA-256: `5dc34573ebfa01dce2888bda8ace95beff364d915e35500d298b5a807f71a7af`.
- Supervision SHA-256: `3591e2b90b8953a444c08f665847fbfb88f57056f45bea94ab831ec6ccc109d9`.

Registered artifact hashes and exact before/after context equality were checked.
The isolated uncommitted experiment records the base commit and all relevant
source hashes, including the reused supervisor. Production sources, main checkout,
live environment and ongoing study were not changed. No promotion or commits.
