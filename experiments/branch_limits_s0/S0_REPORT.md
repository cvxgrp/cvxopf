# M4 Stage 0 characterization report

## Outcome

Stage 0 validates the proposed AC branch-terminal physics but rejects direct
use of the nonlinear terminal expressions inside the thermal inequalities.
The approved production structure is:

1. construct each scalar-indexed voltage expression once;
2. tie four network-owned lifted terminal-flow variables to those expressions;
3. publish and thermally constrain the identical lifted variables.

This is an equivalent representation of the same branch equations. The lift
is justified by a large, reproducible DNLP structural advantage rather than by
a change in physical modeling.

## Branch-equation verification

Direct real-valued CVXPY expressions were compared with independent complex
current arithmetic,

```math
I_f=Y_{ff}V_f+Y_{ft}V_t,\qquad
I_t=Y_{tf}V_f+Y_{tt}V_t,
```

```math
S_f=V_f\overline{I_f},\qquad
S_t=V_t\overline{I_t}.
```

The largest observed discrepancy was below `4.5e-14` p.u. The synthetic
fixture also verified:

- unequal parallel circuits;
- non-unity tap and phase shift;
- analytically reversed terminal orientation;
- exact zero coefficients for an inactive zero-impedance row; and
- `(0,)` direct and published expression shapes for an empty branch table,
  with no lifted variables or defining equalities. This remains a defensive
  private-helper result; post-review hardening rejects public branchless AC
  builds because the current DNLP/IPOPT path cannot solve them safely.

The direct and lifted representations use the same physical terminal-power
functions. Let

$$
\widehat P_{f,e}(\theta,v),\quad
\widehat Q_{f,e}(\theta,v),\quad
\widehat P_{t,e}(\theta,v),\quad
\widehat Q_{t,e}(\theta,v)
$$

denote the real-valued expressions obtained from
$S_f=V_f\overline{I_f}$ and $S_t=V_t\overline{I_t}$. The direct formulation
places these composed nonlinear expressions inside the thermal inequalities,
for example

$$
\left(\frac{\widehat P_{f,e}(\theta,v)}{S_{\max,e}}\right)^2
+
\left(\frac{\widehat Q_{f,e}(\theta,v)}{S_{\max,e}}\right)^2
\leq 1.
$$

The lifted formulation instead introduces auxiliary variables
$p_{f,e},q_{f,e},p_{t,e},q_{t,e}$, imposes the defining equalities

$$
p_{f,e}=\widehat P_{f,e}(\theta,v),\qquad
q_{f,e}=\widehat Q_{f,e}(\theta,v),
$$

with the analogous two equalities at the to terminal, and applies the same
normalized inequality to the lifted variables:

$$
\left(\frac{p_{f,e}}{S_{\max,e}}\right)^2
+
\left(\frac{q_{f,e}}{S_{\max,e}}\right)^2
\leq 1.
$$

The two formulations therefore define the same feasible set after eliminating
the auxiliary variables. Their difference is computational: lifting separates
the nonlinear voltage equations from the quadratic thermal inequalities,
giving CVXPY DNLP and IPOPT a substantially simpler derivative-expression
structure. The published branch-flow quantities are the lifted variables tied
to the authoritative direct equations, so reporting and enforcement refer to
the same modeled values.

## Direct versus lifted DNLP structure

Wall times are individual local measurements in seconds. Case57 sparse
results include three alternating repetitions.

| Case and representation | Direct enforced | Lifted enforced | Ratio |
|---|---:|---:|---:|
| case9, sparse | 0.171 | 0.054 | 3.2 |
| case57, sparse, median | 18.62 | 0.685 | 27.2 |
| case9, dense | 0.153 | 0.038 | 4.0 |
| case57, dense | 79.13 | 0.719 | 110.1 |
| case9, sparse, `T=3` | 0.571 | 0.199 | 2.9 |

The direct and lifted objectives agree to solver precision in every paired
comparison. All case57 `rateA` values are `9900 MVA` and nonbinding, so the
timing difference is not caused by a changed active set or congested optimum.
It arises from placing the composed nonlinear voltage expressions directly
inside the squared inequalities.

The lift adds four variables and four equality rows per branch and time step.
For sparse case57 this changes:

| Metric | Direct | Lifted |
|---|---:|---:|
| Scalar variables | 668 | 988 |
| Scalar equalities | 655 | 975 |
| Scalar inequalities | 188 | 188 |

Despite the larger algebraic model, the lifted derivative structure is much
more tractable for the current CVXPY DNLP/IPOPT path.

## Reporting overhead and all-case behavior

The first reporting-only measurement built unused direct expressions but
solved the unchanged problem. A follow-up therefore measured the actual
production candidate: direct expressions tied to lifted variables by four
defining equalities, with no thermal inequalities. Representative
single-step solve wall times were:

| Case | Pre-M4 baseline | Unused direct reporting | Lifted reporting | Lifted enforced |
|---|---:|---:|---:|---:|
| case9 | 0.030 | 0.037 | 0.049 | 0.076 |
| case57 | 0.295 | 0.289 | 0.509 | 0.711 |
| case118 | 1.734 | 1.759 | 2.547 | 3.268 |

The lifted reporting structure solved every bundled case in the audit, as did
the lifted enforced structure: case9, case9 PWL, case9 dcline, case14, case30,
case30 PWL, case39, case57, and case118. Lifted reporting also preserved every
baseline objective within solver tolerance: the largest absolute difference
was approximately `2.0e-6`, on case14, corresponding to a relative difference
of approximately `2.4e-10`. The compatibility escape hatch therefore changes
the algebraic representation and performance, but not the intended
mathematical problem.

The lift adds exactly four scalar variables and four scalar equalities per
branch in a single-step problem. On case118, that is 744 added variables and
equalities and about 0.8 seconds of local solve overhead relative to the
pre-M4 baseline.

The `T=3` case9 lifted-reporting model also solved successfully in `0.147 s`,
with 108 added variables and equalities relative to the existing multistep
model. These results characterize the documented
`enforce_branch_limits=False` escape hatch rather than treating unused
expressions as a proxy for it.

## Sparsity tolerance

For case57, `sparsity_tol` values from `0` through `0.1` retain all 213
selected `Ybus` entries and provide no measurable construction benefit. At
`1.0`, eight entries are removed and the approximate nodal problem becomes
infeasible.

The experiment therefore provides no evidence for relaxing the approved rule:
branch-limit enforcement requires `sparsity_tol == 0`, and terminal flows
always retain exact branch coefficients.

## Branch status

Every audited bundled branch row uses status `1`. This supports the approved
strict `{0,1}` shared validation. Production tests must still cover valid
inactive rows and invalid negative or greater-than-one values across all three
formulations.

## Measurement limitations

The current CVXPY DNLP/IPOPT interface does not populate:

- `Problem.compilation_time`;
- solver setup or solve time in `SolverStats`;
- IPOPT iteration counts;
- Jacobian or Hessian nonzero counts; or
- detailed solver memory.

The experiment therefore reports Python construction wall time, end-to-end
solve wall time, CVXPY scalar variable/constraint counts, and limited
`tracemalloc` observations. No unsupported inference is made for unavailable
metrics.

## Reproduction

From the repository root:

```bash
uv run python experiments/branch_limits_s0/s0_characterization.py
```

The complete machine-readable output is
`experiments/branch_limits_s0/results/s0_results.json`. Timing values should be
treated as local measurements; the order-of-magnitude structural comparison,
equation residuals, objectives, statuses, and model sizes are the durable
results.
