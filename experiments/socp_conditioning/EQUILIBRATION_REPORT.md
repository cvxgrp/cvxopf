# Equilibration-only comparison

2026-10-02. Three predeclared fresh serial workers, one solver thread each,
on the exact Case118 surplus model. Normalized capability cones, original paired
box inequalities and unscaled objective throughout. No fixed-box intervention,
tolerance change, retry or production modification. All canonical A/P/b/c arrays
(including sparse structure) and cone dimensions compare exactly equal across
the three arms. Physical input identities and historical references also match.

| Setting | Status | Iterations | Solve wall | Physical audit | Native gap | Epigraph excess cost | Max original-unit stationarity |
|---|---|---:|---:|---|---:|---:|---:|
| Enabled, 1e-4 to 1e4 | AlmostSolved | 44 | 1.117 s | Pass | 1.414e-4 | 6.041e-6 | 1.955e-4 |
| Disabled | DualInfeasible | 1 | 0.095 s | No primal | — | — | — |
| Enabled, 1e-2 to 1e2 | AlmostSolved | 38 | 0.930 s | Pass | 9.028e-7 | 4.228e-8 | 7.932e-2 |

No arm meets the frozen full acceptance predicate. Default equilibration exactly
reproduces the previous cones-only objective, iterations, gap and residuals.
Narrower limits improve storage epigraph consistency and the reported gap, but
the maximum stationarity residual worsens about 406-fold, concentrated in voltage
products. Its native primal/dual residuals (8.63e-10 / 1.73e-9) also fail the
requested 1e-10 tolerance. This is not an all-around improvement.

Original-expression objectives are 3.122147605 and 3.124814383 for default and
narrow respectively. Their components require care: default storage cost is
3.127493395 with shedding cost -0.005348351; narrow storage cost is 3.124995169
with shedding cost -0.000180803. Tiny negative shedding fractions multiplied by
large penalties affect this comparison. Retain them rather than clipping or
interpreting the lower total as certified economic superiority.

## Disabled equilibration: retain, do not certify

Clarabel returns DualInfeasible after one iteration. This is a candidate numerical
recession-ray outcome, not a usable primal or a verified unboundedness certificate.
The physical problem is unchanged and its objective is bounded below by the device
bounds and nonnegative penalties (with bounded generator costs).

For the returned ray, independent reconstruction gives `c.T*x=-26960.26`,
`||P*x||_inf=5.09e-6`, and `||A*x+s||_inf=3.65e-5`. The implied `-A*x` violates
the nonnegative cone by 3.50e-5 and SOC membership by 2.25e-5. These are unnormalized
ray measurements, not comparison against a newly declared tolerance. No exact or
validated certificate has been established; do not claim mathematical unboundedness.

## Execution and provenance qualification

Outputs are in `results/equilibration_001/` (default/off) and
`results/equilibration_001_remaining/` (narrow only). The no-primal result exposed
a parent-summary bug: it attempted to read a physical audit absent for infeasible
outcomes. Both completed arms and their hashes were already durable. The parent
stopped; its incomplete root summary remains unchanged. A narrow reporting-only
fix allowed the single previously unstarted arm to execute. No solve was repeated.
The continuation binding records the prior binding/summary hashes and changed
runner context; all other recorded sources match. A subsequent small refactor
and regression test cover the no-primal rejection branch without new execution.

`equilibration_analysis.py` checks arm/canonical/log hashes, exact canonical-model
identity, source-context consistency, and retained optimality evidence. Its
combined artifact is `results/equilibration_analysis_001.json`, SHA-256
`dbf5cc20319cf511f8a93db9e425c7086dc95c354cee6f54d8c3dfa6726a3459`.
All original numerical artifacts remain immutable. No main-checkout changes.

53 focused solver-free tests passed; Ruff and whitespace checks pass. Timings
are descriptive on a shared machine. The experiment establishes equilibration
sensitivity but does not identify a setting that resolves the numerical issue.
No additional sweep, production migration or scientific promotion is authorized.
