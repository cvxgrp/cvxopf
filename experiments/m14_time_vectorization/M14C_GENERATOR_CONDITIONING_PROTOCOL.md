# M14c Case118 generator-conditioning diagnostic

This post-hoc, non-promotional diagnostic selects one simple synthetic
generator-cost amendment for the frozen Case118 numerical study. It does not
claim that the added term represents reviewed generator economics, establish
global uniqueness, or authorize the annual solve.

## Reproducible execution

The exact tracked runner is
`experiments/m14_time_vectorization/m14c_generator_conditioning.py`. The
retained diagnostic used that runner byte-for-byte with SHA-256
`9a708ca42044fcbded0ba905d024a7388ac6829dee10347abc75203e2f315b7b`.
Raw worker outputs remain ignored. From a clean checkout containing this
tracked runner and the frozen conditioned fixture, run:

```bash
CVXOPF_CONDITION_OUTPUT=experiments/m14_time_vectorization/results/m14c_case118_quadratic_all_c2_1e4 \
CVXOPF_CONDITION_SCOPE=all \
CVXOPF_GENERATOR_C2=1e-4 \
uv run python -m experiments.m14_time_vectorization.m14c_generator_conditioning
```

The runner starts a fresh process for each 24- and 168-hour arm, compares
`stepwise + CPP` with `vectorized + SCIPY`, uses the same tight CLARABEL
tolerances in both arms, and retains solver evidence, complete public results,
objective accounting, scientific audits, and full bounds audits.

## Selected amendment

For every one of the 54 dispatchable generators, preserve the inherited
constant and linear coefficients and set the quadratic coefficient to
`c2 = 1e-4`, using coefficient order `(c0, c1, c2)`. The imported fleet has no
quadratic production costs: 19 units have linear costs and 35 have zero
production cost.

This is a deliberate economic perturbation used to condition dispatchable
generation and reduce representation sensitivity. It does not identify all
storage or branch-flow coordinates. The largest unit has `Pmax = 1182 MW`, so
the largest added marginal term over the declared generator boxes is
`2 * c2 * Pmax = 0.2364` cost/MWh. Relative to the unconditioned stepwise
objective, the selected term changes the 24-hour objective by about `0.249%`
and the 168-hour objective by about `0.243%`.

## Predeclared selection evidence

The reviewed local sensitivity set was intentionally small:

| Rule | 24 h objective gap | 168 h objective gap | Interpretation |
|---|---:|---:|---|
| no added curvature | 0.0198885 | 0.2000514 | weakly identified baseline |
| bus 69 only, `c2=1e-4` | 0.0005054 | 0.0565013 | improved but horizon-sensitive |
| bus 69 only, `c2=1e-3` | 0.0026348 | 0.0289658 | nonmonotone; stronger local term was not robust |
| all generators, `c2=1e-4` | 0.00007818 | 0.00000792 | selected simple fleet-wide rule |

Selection required one uniform, easily testable rule that substantially
reduced the representation objective gap at both horizons, retained native
CLARABEL `Solved` status, passed every frozen scientific and bounds audit, and
kept both objective differences within the conservative scale of the combined
reported solver gaps. The rule was not selected by minimizing the synthetic
economic perturbation or by asserting a unique complete trajectory.

## Authority boundary

The tracked compact record is `M14C_GENERATOR_CONDITIONING.json`; the ignored
complete root is identified there by hash. This diagnostic supersedes the
unconditioned prefix evidence only as a scenario-definition amendment. Both
`prefix_ladder_executed` and `annual_execution_authorized` remain false until
the conditioned 24/168/720 ladder and its corresponding comparison evidence
are executed and reviewed.
