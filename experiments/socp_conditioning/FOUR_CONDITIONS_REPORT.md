# Four condition convergence with optional shedding

All eight stock CLARABEL solves returned native `Solved` and passed the original
physical and component-cost audits at relative-gap tolerance 1e-6. This covers
surplus, deficit, net-load ramp up and net-load ramp down with both QDLDL and
Faer. Optional shedding remains present in every case. No custom factorization
or native arithmetic patch was needed.

Four arms meet the complete unchanged diagnostic acceptance predicate. The
deficit and ramp-up pairs fail only its absolute 1e-4 canonical-versus-physical
objective check. Their discrepancies are approximately 0.0152 and 0.00562 cost
units, about 1e-8 of their respective physical objectives. They are not physical
feasibility failures. Historical classifications and acceptance rules remain
unchanged; a prospective scale-aware acceptance decision remains open.

## Controls and results

Executed 2026-10-06 under [the declared protocol](FOUR_CONDITIONS_PROTOCOL.md).
Each 24-hour window retains its original Tracy E3 energy-neutral endpoints,
optional shedding, costs and device limits. The only problem interventions are
normalized device capability cones, five-pass joint scaling, and substitution
of exactly fixed Pg/p_nd coordinates. Absolute-gap and feasibility tolerances
remain 1e-10; full relative-gap tolerance is 1e-6. Stock CLARABEL is version
0.11.1, one thread, SCIPY canonicalization, no warm start and minimum step 1e-8.

| Condition | Backend | Native status | Iterations | Relative gap | Native seconds | Physical audit | Complete diagnostic gate |
| --- | --- | --- | ---: | ---: | ---: | --- | --- |
| Surplus | QDLDL | Solved | 32 | 3.5211e-7 | 0.788 | Pass | Pass |
| Surplus | Faer | Solved | 32 | 3.4723e-7 | 0.761 | Pass | Pass |
| Deficit | QDLDL | Solved | 33 | 4.2968e-7 | 0.834 | Pass | Reject accounting check |
| Deficit | Faer | Solved | 33 | 4.2960e-7 | 0.788 | Pass | Reject accounting check |
| Net-load ramp up | QDLDL | Solved | 24 | 5.0542e-7 | 0.609 | Pass | Reject accounting check |
| Net-load ramp up | Faer | Solved | 24 | 5.0542e-7 | 0.575 | Pass | Reject accounting check |
| Net-load ramp down | QDLDL | Solved | 28 | 1.4930e-7 | 0.684 | Pass | Pass |
| Net-load ramp down | Faer | Solved | 28 | 1.4930e-7 | 0.658 | Pass | Pass |

Ramp up denotes surplus to deficit, [8580,8604); ramp down denotes deficit to
surplus, [2439,2463). Surplus is [3308,3332), deficit [1165,1189). Historical E3
stopped before its ramp-down arm: that input is reconstructed from the frozen
study definition and verified Tracy sources, not a prior numerical result.

## Physical and economic accuracy

The maximum native feasibility residual over all arms is 2.94e-11. Independent
original-unit reconstruction gives maximum active/reactive balance residuals
2.67e-9 MW and 2.50e-9 MVAr, SoC recurrence 4.69e-13 MWh, and terminal deviation
4.55e-13 MWh. All original bound, capability, network, load and state checks
pass. Reconstructed named component costs sum to the physical objective within
2.33e-10 cost units.

That last check is distinct from agreement with the lifted canonical objective:

| Condition | Physical objective, approximately | Absolute native gap, QDLDL / Faer | Canonical versus physical discrepancy, QDLDL / Faer |
| --- | ---: | --- | --- |
| Surplus | 3.1249989 | 1.10035e-6 / 1.08510e-6 | 3.25567e-8 / 3.21123e-8 |
| Deficit | 1540766.847 | 0.662037 / 0.661908 | 0.0152374 / 0.0152342 |
| Ramp up | 504027.630 | 0.254747 / 0.254745 | 0.00562221 / 0.00562216 |
| Ramp down | 12689.6887 | 0.00189457 / 0.00189452 | 3.64591e-5 / 3.64588e-5 |

The canonical objective is slightly higher than direct physical cost evaluation,
consistent with finite-tolerance slack in objective epigraph variables. This
test does not localize the discrepancy among individual auxiliary variables.
It is small relative to total cost, and substantially below the native gap in
each case, but exceeds the retained absolute accounting threshold in four arms.
Native gaps are numerical optimality evidence, not rigorous verified bounds.

Optional shedding is not needed in material amounts in these returned solutions:
maximum ENS is 2.24e-6 MWh in deficit, with about 0.0465 cost units of shedding
cost. Surplus ENS is about 3.4e-12 MWh. These are raw retained values, not zeros
inserted by a fixed-load projection. No negative shedding cost appears.

Backend objective differences are at most 3.32e-5 cost units. Agreement in
objective does not assert equality of weakly identified dispatch trajectories.
SOCP feasibility remains distinct from AC realizability.

## Interpretation

The tested stock backends can achieve native full success with interventions
1, 2 and 4, without removing optional shedding, on all four operating conditions.
The strict 1e-9 experiment and its failures remain valid records of a more
demanding precision target and, for surplus, a different load model.

These results support considering 1e-6 relative-gap accuracy for practical use,
with independent physical checks and an economically meaningful objective
accuracy requirement. They do not automatically qualify production settings or
relax the existing absolute accounting gate. The distinction is recorded in
[the pending acceptance decision](ACCEPTANCE_DECISION.md). No further solves
follow automatically from this result.

## Timing and provenance

Exactly eight fresh serial diagnostic workers ran. Total native solver time was
5.697 seconds; individual worker times were 1.62–1.91 seconds, and supervision
times 3.64–4.17 seconds, including publication and half-second polling. Sampled
peak worker RSS was 421–441 MiB, below the 4096-MiB limit. No 180-second limit
was reached. Single observations do not establish a robust backend speed ranking.

The isolated uncommitted experiment uses source commit
`dc1c5ace2d787fda563f99c0d3c11a48ba3e9b6a`. Sources, environment and installed
extension are bound by `binding.json`, unchanged through execution. The main
checkout, production code, installed solver and historical artifacts were not
modified. There were no numerical retries. Preflight initially exposed the
missing historical ramp-down completion; before output creation or any solve,
the loader and protocol were corrected to identify its original input-only
provenance explicitly.

Runner and offline analyzer: [four_conditions.py](four_conditions.py).
The analyzer rebuilds all eight problems without optimization, verifies exact
canonical/scaled matrices and cross-backend identity, restores retained full
primals, and reproduces public results, physical audits and objective checks.
Native status, gaps, full traces, primal/dual/slack vectors, scaling/substitution
maps, logs and resource evidence remain in immutable raw artifacts.

Reproduce with a fresh output directory and the established isolated environment:

```text
uv run --offline --no-sync --with coptpy==8.0.7 --with mosek==11.2.5 python -B
  -m experiments.socp_conditioning.four_conditions
  --output experiments/socp_conditioning/results/four_conditions_001
  --reference /Users/bmeyers/github/cvxopf/experiments/case118_tracy_2021/results/e3
```

Set `UV_CACHE_DIR=/tmp/cvxopf-uv-cache`,
`UV_PROJECT_ENVIRONMENT=/Users/bmeyers/github/cvxopf/.venv`,
`PYTHONPATH="$PWD/src:$PWD"`, `PYTHONDONTWRITEBYTECODE=1`, and
`OPENBLAS_NUM_THREADS=OMP_NUM_THREADS=MKL_NUM_THREADS=1`.
Add `--analyze` for offline reconstruction; no numerical solve is made then.

| Artifact in results/four_conditions_001 | SHA-256 |
| --- | --- |
| summary.json | ed82d8011f37a03338d131f4f6b6c8c3fd0e3125dfe36557faf79cf61720e042 |
| analysis.json | cc4c609d30cb1ce41e654592376e89dbc6a41f60f3c812abfa1419b9831d87dc |

The root summary binds all eight arm and supervision hashes; each supervision
record binds the matrices, vectors, logs and completion records. Verification:
171 conditioning tests pass, Ruff lint/format and whitespace checks pass; only
the known OpenMP import and zero-norm test warnings remain. Changes are isolated,
unstaged/uncommitted and nonpromotional.
