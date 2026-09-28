# DNLP/IPOPT convergence changes with automatic sparse dispatch in CVXPY 1.9.3

## Summary

CVXPY 1.9.3's automatic conversion of low-density dense constants to sparse
operations can change IPOPT's termination. In the standalone synthetic example
below, default dispatch produces a step-computation error; setting
`cvxpy.settings.SPARSE_DENSITY_THRESHOLD = 0.0` produces successful termination.
The model, starting point, dependencies, and solver options are otherwise
identical.

The example demonstrates convergence sensitivity, not an incorrect derivative.
It has a deliberately degenerate constraint at the exact optimum. Its failure
mode also differs from the motivating application's iteration-limit failure;
a shared underlying mechanism has not been established.

## Standalone example

Save the following as `toy.py`. It requires no CVXOPF installation, grid data,
input files, or application framework.

```python
import argparse

import cvxpy as cp
import numpy as np

parser = argparse.ArgumentParser()
parser.add_argument("--dense-route", action="store_true")
args = parser.parse_args()
if args.dense_route:
    cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0

n = 24
x, z = cp.Variable(n), cp.Variable(n)
x.value = np.random.default_rng(0).uniform(0, 1, n)
z.value = np.full(n, 0.1)
A = np.eye(n)  # Dense array with density 1/24 < 0.05.
problem = cp.Problem(
    cp.Minimize(cp.sum_squares(x - 2) + cp.sum_squares(z)),
    [cp.power(1 - A @ x, 3) >= z, x >= 0, z >= 0],
)
problem.solve(solver=cp.IPOPT, nlp=True, print_level=5, sb="yes", max_iter=3000)
print(problem.status, problem.value)
```

With Python 3.11 and the native IPOPT library/development headers and
`pkg-config` installed, create an environment and run each condition in a
fresh process:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install cvxpy==1.9.3 sparsediffpy==0.6.1 cyipopt==1.7.0 numpy==2.4.6 scipy==1.17.1
.venv/bin/python toy.py
.venv/bin/python toy.py --dense-route
```

The default-dispatch solve is expected to raise CVXPY `SolverError` on the
tested stack. The threshold-zero solve prints `optimal`. The Python package
pins do not pin the native IPOPT/MUMPS libraries; the tested native versions
are listed below.

## Verified toy results

Both conditions were run twice in fresh processes outside the repository, in
an environment without CVXOPF installed. Each repeated result was identical.
Native termination and final iterates were captured before CVXPY handled the
solver failure; original constraint violations were reconstructed from those
iterates.

| Quantity | Default dispatch | Threshold zero |
|---|---:|---:|
| IPOPT termination | Error in step computation (`-3`) | Solve succeeded (`0`) |
| CVXPY outcome | `SolverError` | `optimal` |
| Iterations | 79 | 36 |
| Native objective | 23.9951805503159 | 23.9641629586798 |
| Maximum original constraint violation | 3.14802e-12 | 4.17848e-10 |
| Unscaled dual infeasibility | 3.99833e7 | 7.26220e-4 |
| IPOPT reported scaled overall NLP error | 8.75135e1 | 2.62765e-8 |
| Stored Jacobian entries | 96 | 648 |
| Numerical Jacobian nonzeros at the start | 96 | 96 |

The complete starts, native bounds, and numerical options are identical.
Objective, constraint values, gradient, normalized Jacobian, and the lower
Lagrangian Hessian with all-one multipliers agree exactly at the start.
Independent analytical checks of the initial constraints and derivatives pass
at absolute/relative tolerance 1e-12. This is not a global derivative proof.

### Interpretation of the toy

The exact optimum is x=1, z=0, with objective 24. Feasibility implies x<=1,
so every term (x_i-2)² is at least one. At that optimum the cubic constraint
is flat, and the active constraint gradients fail the usual constraint
qualification. This is a numerically difficult formulation of a simple problem.

Small cubic residuals do not imply equally small errors in x. The default and
control final iterates violate the implied bound x<=1 by approximately
1.80556e-4 and 9.41577e-4, respectively. That explains objectives below 24.
The control has successful **solver termination**, not superior primal accuracy
or an exact optimum. This toy was selected to exhibit a termination difference;
it does not estimate failure frequency or prove that sparse dispatch is
generally worse.

## Controlled application comparison

The motivating application is [CVXOPF](https://github.com/cvxgrp/cvxopf), an
optimal power flow modeling package built on CVXPY. A study comparing time and
spatial vectorization exposed a convergence change in a previously successful
three-hour, 118-bus AC problem.

That diagnostic holds the earlier application model construction, inputs, and
complete starting point fixed. It first changes the compatible dependency pair,
then retains the new pair and disables only automatic sparse dispatch:

| CVXPY / sparsediffpy | Dispatch | IPOPT outcome | Iterations | Stored Jacobian entries |
|---|---|---|---:|---:|
| 1.9.2 / 0.3.0 | Historical behavior | Successful termination | 70 | 412,439 |
| 1.9.3 / 0.6.1 | Default | Maximum iterations exceeded | 3,000 | 36,167 |
| 1.9.3 / 0.6.1 | Threshold zero | Successful termination | 70 | 412,439 |

The failed application attempt's unscaled constraint violation is
1.3264780494637305e-3 and dual infeasibility is 3.8946565871375519e11.
The threshold-zero solution exactly matches the retained old-stack solution.
The native model has 9,124 variables and 10,601 constraints. Complete starts,
bounds, ordered canonical expression fingerprints, and tested initial-point
oracle values match across the application comparisons. The latter include
three Lagrangian Hessians with zero, all-one, and seeded random multipliers.

The synthetic example removes the application dependency but is **not** an
algebraic reduction of this model. It reproduces the broader termination
sensitivity, not the specific 3,000-versus-70 iteration result.

## Relevant conversion change

In CVXPY 1.9.3,
`cvxpy/reductions/solvers/nlp_solvers/diff_engine/converters.py` automatically
routes dense constant left-multiplication operands with density below
`SPARSE_DENSITY_THRESHOLD` (default 0.05) to the sparse CSR binding. Threshold
zero disables this automatic conversion, not explicitly sparse operands.
The installed `ipopt_nlpif.py` and `nlp_solver.py` are byte-identical across the
two tested CVXPY versions.

Changed structural zeros could affect factorization ordering or pivoting and
thereby the nonlinear trajectory. Those internals have not been measured;
this is a hypothesis, not a diagnosis of IPOPT/MUMPS. Matching derivatives at
the tested points does not rule out a later-iterate derivative issue.

## Environment

- Python 3.11.15, macOS ARM64.
- CVXPY 1.9.3, sparsediffpy 0.6.1, cyipopt 1.7.0.
- IPOPT 3.14.19 with MUMPS 5.6.2; NumPy 2.4.6; SciPy 1.17.1.
- Both toy conditions use the same environment/native libraries.
- Effective IPOPT options: `mu_strategy=adaptive`, `tol=1e-7`,
  `bound_relax_factor=0`, `hessian_approximation=exact`, `derivative_test=none`,
  `least_square_init_duals=no`, `max_iter=3000`, `print_level=5`, `sb=yes`.
- Native-library changes may change the observed termination. Threshold zero
  is process-global and is not proposed as a general package default.

## Questions

1. Is there a supported per-problem or per-solve control for automatic
   dense-constant-to-sparse dispatch, retaining explicitly sparse operands?
2. What evidence would best distinguish sparse-factorization sensitivity
   from a later-iterate derivative issue?
