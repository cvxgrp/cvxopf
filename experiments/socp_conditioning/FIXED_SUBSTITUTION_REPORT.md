# Exact fixed coordinate substitution with joint scaling

## Result

Substitution preserves the physical problem and reduces its dimensions, but
does not resolve the two remaining surplus convergence failures. The deficit
case remains native `Solved`; both surplus cases remain `AlmostSolved`.
All three independent physical audits pass. No additional solve was retried.

Exactly three fresh serial CLARABEL workers tested the previously declared
24-hour surplus/shedding, surplus/fixed-load and deficit/shedding cases.
Normalized device cones, the joint scaling rule, costs, inputs, acceptance
gates and solver controls were unchanged. Full convergence requires the
existing 1e-10 gap and feasibility tolerances.

## Comparison with fixed bounds represented as equalities

| Case | Relative gap with equalities | Relative gap with substitution | New status | Iterations | Native time |
|---|---:|---:|---|---:|---:|
| Surplus with shedding | 4.573e-7 | 4.132e-8 | AlmostSolved | 34 | 0.782 s |
| Surplus with fixed load | 4.453e-8 | 9.364e-8 | AlmostSolved | 31 | 0.712 s |
| Deficit with shedding | 7.238e-11 | 6.338e-11 | Solved | 40 | 0.900 s |

Substitution improves the first gap about elevenfold, worsens the second about
twofold, and modestly improves the third. For perspective, joint scaling alone
with the original paired bounds achieved relative gaps of 4.230e-9, 9.622e-8
and 3.792e-10 respectively. Neither cleanup nor substitution dominates all
cases. The surplus problems remain gap-limited, despite accurate physical
solutions and small stationarity residuals.

The new absolute gaps are 1.291e-7, 2.926e-7 and 9.765e-5. The deficit objective
is approximately 1,540,766.73, so its relative gap satisfies the native criterion.
Surplus objectives are approximately 3.125. Relative-gap comparisons across
these operating conditions should not be confused with equal absolute errors.

Objective changes relative to the equality arms are -1.399e-7, +6.338e-9 and
-2.989e-6 cost units. The maximum physical residual/tolerance ratio across all
arms is 0.001816, under 0.182% of its allowed tolerance. Original-coordinate
stationarity infinity norms are 2.421e-8, 8.156e-12 and 2.386e-5.

Substitution and its pre-solve identity checks cost about 0.030 s per arm;
scaling costs another 0.191–0.193 s. Peak supervised RSS ranges from 500.3 to
534.5 MiB. These single runs do not demonstrate a speed or memory improvement.

## Exact problem transformation

Only the exact unary Pg/p_nd fixing equalities from the previous cleanup were
eliminated. Near-fixed bounds, reactive variables, storage boundaries, and
constant cone/inequality rows were retained. The transformations are:

| Case | Original canonical rows and columns | Reduced rows and columns | Eliminated coordinates |
|---|---:|---:|---:|
| Surplus with shedding | 83,385 × 25,179 | 81,564 × 23,358 | 1,821 |
| Surplus with fixed load | 78,633 × 22,803 | 76,812 × 20,982 | 1,821 |
| Deficit with shedding | 82,769 × 25,179 | 80,332 × 22,742 | 2,437 |

The original canonical matrices match the retained equality-arm artifacts
exactly. Tests cover nonzero fixed values, quadratic cross-terms, RHS changes,
constant objective offsets and full primal/dual restoration. All eliminated
values in these particular three problems are zero, so their objective offsets
are zero. The unchanged joint rule computes new scale factors on each reduced
problem; no factors were selected after observing solver outcomes.

The offline analyzer independently verifies coefficient block identities,
variable/row partitions, fixed equality definitions, scaling factors and
transformed arrays, saved x/s/z maps, original objective and gap identities,
and the complete immutable artifact chain. Removed equality multipliers are
reconstructed from stationarity and explicitly labeled, not presented as
native solver output. Public physical results use the restored full primal.

## Evidence and disposition

Raw evidence is in `results/fixed_substitution_001/`, including each original,
reduced and transformed canonical problem, scales, native and reconstructed
vectors, logs, physical audits and resource supervision. All three workers and
the parent completed normally. The independently reconstructed
`verified_analysis.json` has SHA-256
`ae910141f52da2e5abd6c282aaa01e995820c072033f0370c020bb5bbb890ea2`.
Execution context binds the unchanged production sources and experiment-local
runner; analysis provenance is retained separately. No production changes,
commits, or additional solver trials were made.

This test narrows the diagnosis: fixed-coordinate retention is not the sole
cause of the surplus convergence ceiling. Substitution is mathematically
valid and dimension-reducing, but is not a demonstrated universal convergence
improvement here. The results remain SOCP relaxation results, not recovered
AC feasibility certificates or qualification of other solvers.
