# Matched AC/SOCP containment and device evidence

## Disposition

The four predeclared pairs passed. All eight experimental solves returned
`optimal`; every AC candidate passed the independent physical/device checks
after a rank-one lift, with every device coordinate retained. Lifted objectives
match the original AC objective accounting. Every optimized SOCP point passed
the independent relaxation audit. No production source change was necessary.

This is E2 evidence ready for review, not automatic milestone closure or
authorization to restart Tracy. The [plan](E2_PLAN.md) fixes the cases,
tolerances, budgets and claim limits. E1's independent PowerModels references
remain the external network oracle; this gate addresses matching, containment,
shared devices and time accounting.

## Objective estimates and recovery

Gap is **feasible AC objective minus SOCP primal objective**, in objective
units. It is not a certified global AC optimality gap. No validated dual bound
was available through this interface.

| Pair | AC objective | SOCP estimate | Signed gap | SOCP exact product recovery | Recovered candidate AC-feasible |
| --- | ---: | ---: | ---: | --- | --- |
| Case9, single step | 5296.686204 | 5296.666083 | +0.020121 | No | No |
| Case14, single step | 8081.526258 | 8075.124700 | +6.401558 | No | No |
| Mixed, T=3 vectorized | 13757.789908 | 13757.790908 | −0.001000 | Yes | Yes |
| Mixed, T=3 stepwise | 13757.789908 | 13757.789907 | +0.000001 | Yes | Yes |

The vectorized mixed estimate is **slightly above** the feasible AC objective;
it is not rounded away or described as a lower bound. The difference is within
the predeclared objective allowance of about 0.027536. Its accounting attributes
about 0.000995 of the difference to shedding cost: approximately 9.95e-7 MWh
more ENS at 1000 objective units/MWh. Other components account for the remainder.
Both independent primal audits pass. This supports agreement at the declared
tolerance, not proof of the cause of the solver-level difference. No tolerance
was changed and no retry or solver tuning was performed.

The mixed stepwise/vectorized SOCP objectives differ by 0.00100111, also within
the declared allowance. Nonunique reactive dispatch and storage trajectories
are not required to match coordinate by coordinate.

For Case9 and Case14 the signed objective differences are only 0.000380% and
0.079212% of the AC candidate's cost. Nevertheless, their returned relaxation
points are not directly recoverable to feasible AC operation:

| Pair | Maximum normalized determinant gap | Maximum cycle error, rad | Recovered maximum P/Q imbalance, MW or MVAr |
| --- | ---: | ---: | ---: |
| Case9 | 8.91e-3 | 5.25e-3 | 9.13 |
| Case14 | 1.45e-10 | 5.87e-2 | 108.33 |
| Mixed vectorized | 2.61e-9 | 0 (no cycle) | 1.24e-6 |
| Mixed stepwise | 6.77e-11 | 0 (no cycle) | 3.30e-8 |

Case14 again demonstrates that tight edge cones alone do not establish a
globally consistent voltage vector. These are properties of the returned
points and deterministic forest candidates, not proofs that AC is infeasible
or that no other optimal SOCP point can recover. In fact, the matched AC
solutions are independently feasible in all four cases.

## Containment and independent accounting

Each pair is created from matching complete original inputs, with an inspectable
input hash. The matching guard rejects differences in cases, limits, costs,
reactive demand, load-shedding permissions, device identities, renewable
availability, storage initial/terminal policies, horizon and delta. Nonzero AC
admittance thresholds are rejected even with branch limits disabled. Unsupported
caller coupling is rejected before construction rather than excluded silently
from the audit.

AC voltages are lifted as w=|V|² and W=Vi conjugate(Vj), preserving Pg/Qg,
storage P/Q and SoC, renewable P/Q, HVDC terminal injections and shedding
fractions. The audit independently reconstructs network and device constraints,
not CVXPY constraint violations. Direct complex-voltage Ybus/branch calculations
also agree with the AC result and lifted powers. Forest recovery of the **lifted
AC** points reproduces the original voltages up to the connected-network angle
gauge and passes the AC audit in every case. This is distinct from recovering
the **optimized SOCP** points in the table above.

Maximum P/Q balance errors for the original AC points range from 7.82e-14 to
4.27e-10 MW/MVAr; optimized SOCP errors range from 1.24e-12 to 5.16e-10.
The maximum norm-cone violation across optimized points is 1.01e-10 pu².
All configured voltage, rating, generator, storage, renewable, HVDC and load
checks pass the predeclared thresholds.

Independent objective arithmetic uses original cost coefficients and engineering
unit primals. Generator, cycling, HVDC and shedding costs are integrated with
delta once; terminal penalty is added once outside that integral. Each is also
checked against the builder's named expression. The AC-to-SOCP lift retains
identical numeric objective components because it retains the complete device
primal and the input policies are matched.

## Mixed-device observations

This deliberately small two-bus test has three half-hour intervals, varying
demand and renewables, both P/Q device channels, and a complete replacement
explicit load fleet. At the peak interval, 90 MW demand exceeds supply capability,
so shedding is genuinely active—not just a schema field with zero values.

For AC, and closely for both SOCP assemblies:

- ENS is approximately **13.682 MWh**, with proportional reactive relief.
- The hard-target battery charges about 12 MW in the first interval and
  discharges about 12 MW in the second, returning to **6 MWh** at the final
  boundary. Its initial state is 6 MWh; delta is 0.5 h.
- The soft-target battery ends near **2.874123 MWh**, below its 3 MWh target.
  Its once-per-horizon penalty is approximately **0.110915**, not half that
  amount. This tests a nonzero terminal penalty alongside a hard target.
- AC generation, cycling, HVDC and shedding integrated costs are approximately
  **73.561581**, **1.587412**, **0.530000**, and **13682.000000**, respectively.
- AC network branch loss is **1.888785 MWh**; HVDC loss is **0.060000 MWh**.
  The two mechanisms are reported separately. Bus real-shunt energy is zero
  for these fixtures; this is not omission of reactive shunts from the model.

The standard-case AC/SOCP network branch losses are 3.306689/3.305730 MWh for
Case9 and 9.287192/9.177896 MWh for Case14 (one-hour intervals). SOCP losses
are derived from relaxed terminal powers, not a lossy-DC objective proxy or
necessarily an executable AC loss trajectory.

## Execution, sizes and timings

Source base: `5dd963bb813538df9b762ce16bae094fb8dfa68b`, **with uncommitted E2
implementation**. Retained per-file SHA-256 values identify the actual executed
source, including the test support and execution plan. Start/end contexts
matched for all workers and the parent. This is not described as a clean-commit
execution. Report/evidence summarization takes place after execution.

Environment: macOS 26.7 arm64, Python 3.11.15, CVXPY 1.9.3, cyipopt 1.7.0,
CLARABEL 0.11.1, NumPy 2.4.6, SciPy 1.17.1. AC uses IPOPT/DNLP; SOCP uses
CLARABEL, CPP for single/stepwise and SCIPY for time-vectorized assembly.
Solver settings are retained verbatim in the evidence and predeclared plan.

| Pair | AC/SOCP model scalar variables | Combined pair build, ms | AC/SOCP reduction-chain application, ms | AC/SOCP inclusive solve wall, ms |
| --- | ---: | ---: | ---: | ---: |
| Case9 | 132 / 33 | 13.28 | 17.72 / 9.24 | 50.20 / 11.05 |
| Case14 | 254 / 64 | 19.81 | 36.45 / 24.47 | 77.11 / 26.69 |
| Mixed vectorized | 101 / 53 | 14.46 | 6.08 / 26.33 | 25.99 / 29.06 |
| Mixed stepwise | 99 / 51 | 23.42 | 16.69 / 46.77 | 42.92 / 50.76 |

These are single observations, not a controlled speed benchmark. Model counts
exclude canonicalization-introduced coordinates; canonical SOC sizes are
retained separately. Reduction-chain timing is a subset of inclusive solve
wall, not an additive extra. CLARABEL native solve times are about 0.62, 0.97,
1.05 and 0.82 ms; native IPOPT solve time/iteration counts are unavailable
through this CVXPY interface and are not inferred from wall time.

## Reproducibility and verification

- Reusable cases/checks: `tests/socp_matched.py`.
- Permanent CI tests: `tests/test_socp_matched.py`. No private data, external
  reference generation, downloads, or experiment imports.
- Runner: `e2_compare.py`; each pair uses a fresh subprocess with a 180-second
  parent wall limit. Failed outcomes are retained and stop later pairs.
- Raw root: `results/e2/run.json`, SHA-256
  `e63725bf2d33f301b48843334f49a7b9cd3240886d569558cd060cde00ed377e`.
  Its four hash-bound pair artifacts retain full primals, audits, timing and logs.
- Compact selected evidence: `E2_EVIDENCE.json`. Raw files stay ignored.

To reproduce on a public repository clone with the package/development
environment installed (including IPOPT):

```sh
uv run --extra dev pytest tests/test_socp_matched.py -q
uv run --extra dev python -m experiments.m11_socp.e2_compare --output experiments/m11_socp/results/e2_reproduction
```

Use a new output directory; the runner refuses to replace an existing run.
Numerical outputs and timings may vary; acceptance is tolerance-based, not
byte-for-byte equality or identical nonunique dispatch.

Verify the selected evidence without solving:

```sh
uv run --extra dev python -m experiments.m11_socp.summarize_e2 --check
```

Verification: 30 new solver-free tests passed before numerical execution.
The full regression suite passed **3389 tests and six subtests**, with 122
warnings, in 347.63 seconds. Its E2 module has 35 tests and spends eight
optimization calls, reusing each solved pair for all assertions. Together with
the eight evidence calls, E2 used **16/24** calls, without a redundant targeted
numerical rerun. Ruff, configured mypy and diff whitespace checks pass.
Warnings include the existing solver/environment diagnostics and the deliberate
static HVDC fallback/non-DPP notices; no failing check was suppressed.
No production source or dependencies changed; no Tracy or larger-network
execution occurred. All changes remain for owner review and commit.
