# M19 S4 shedding-bound representation report

## Decision

Use fully explicit inequalities in production:

$$
\alpha_{t,i} \geq 0,
\qquad
\alpha_{t,i} \leq \rho_i m_{t,i}.
$$

Do not encode the static interval $[0,\rho_i]$ as CVXPY leaf bounds. The two
representations gave the same objective and shedding fractions to solver
precision, and the leaf representation showed no meaningful construction or
end-to-end solve advantage. Explicit inequalities state the complete
time-varying feasible set in one place and behave uniformly in AC and convex
formulations.

## Experiment

The full case9 AC builder was exercised with one sheddable load and one fixed
load. The four-step trajectory crossed positive, zero, and negative active
demand without changing the optimization graph. Each representation was built
and solved five times for single-step and four-step models.

Median timings were:

| Horizon | Representation | Build | First canon./setup | First solve | Updated canon./setup | Updated solve | Variables | Equalities | Explicit problem inequalities |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| single | explicit | 0.0105 | 0.0271 | 0.0140 | 0.0264 | 0.0060 | 133 | 127 | 32 |
| single | leaf + eligibility | 0.0105 | 0.0269 | 0.0145 | 0.0264 | 0.0062 | 133 | 127 | 31 |
| four-step | explicit | 0.0416 | 0.1069 | 0.1072 | 0.1172 | 0.0668 | 532 | 508 | 128 |
| four-step | leaf + eligibility | 0.0422 | 0.1072 | 0.1076 | 0.1113 | 0.0675 | 532 | 508 | 124 |

All times are seconds. Every build retained identical problem, variable,
constraint, and parameter object identities across the active-load update.
The inequality column counts entries in `problem.constraints`; bounds encoded
as CVXPY variable attributes in the leaf representation are not included.

The single-step objectives were $1,724,274.4062466722$ and
$1,724,274.4062466729$. The four-step objectives were
$3,195,773.3090266297$ and $3,195,773.3090266283$. Positive-step shedding
fractions agreed to approximately $10^{-15}$; zero and negative active-load
slices produced numerical zero in both representations.

Both convex formulations were also solved under both representations. Lossy
DC returned shedding fractions agreeing within $4\times10^{-13}$ and
objectives within $2\times10^{-6}$ objective units. Single-node DC returned
fractions agreeing within $2\times10^{-12}$ and objectives within
$8\times10^{-6}$. All four convex builds were DCP and optimal.

## Measurement limitations

The experiment times DNLP chain application separately from IPOPT execution
using the same internal operations called by `Problem.solve()`. The installed
IPOPT interface still reports `num_iters` as `"Not available"`; iteration
counts therefore remain explicitly unavailable rather than inferred. Both AC
representations report `problem.is_dcp() == False` and
`problem.is_dpp() == False`, as expected for the DNLP network model and the
current parameter-product scaling path.

The sheddable model is parameterized and graph-stable but is not DPP under the
current component scaling contract. The builder-owned `inv_base_mva`
parameter multiplies expressions that already contain load parameters. CVXPY
may therefore recanonicalize after a parameter update. M19 requires atomic,
correct graph reuse; it does not claim DPP fast-path performance.

## Scaling-contract decision

The companion `evaluate_dpp_scaling.py` experiment confirmed that treating
`baseMVA` as an immutable scalar makes the convex lossy-DC test problem DPP.
The observed updated end-to-end solve decreased from about 3.9 ms to 0.54 ms,
with the same objective and unchanged graph identities. Nevertheless, M19
retains the established component contract in which an engineering-unit device
creates `inv_base_mva` as a parameter and shared assembly binds it.

A load-only exception would make scaling ownership inconsistent across
storage, nondispatchable generation, HVDC, and loads. Moreover, the planned M17
corrective layer is AC DNLP, for which CVXPY does not provide a DPP model even
after removing this parameter product. Any DPP-oriented scaling revision is
therefore deferred to a separate cross-device review rather than folded into
the load feasible-set milestone.
