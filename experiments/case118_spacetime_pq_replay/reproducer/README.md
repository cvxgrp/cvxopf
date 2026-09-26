# Synthetic automatic sparse-dispatch example

`toy.py` is self-contained: no CVXOPF, grid data, input file, or experiment
framework. `verify_toy.py` is optional measurement code that captures native
termination, starts, bounds, and residuals before CVXPY handles a solver error.

## What this demonstrates

On the tested native stack, CVXPY 1.9.3 / sparsediffpy 0.6.1 with default
automatic sparse dispatch produces an IPOPT step-computation error after 79
iterations. Setting `cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0` produces
successful termination after 36 iterations, with the same model, initialization,
and solver options.

**This is a synthetic convergence-sensitivity example, not a mathematical
reduction of the application model.** The application diagnostic reached the
3,000-iteration limit; this toy fails earlier with a different native status.
It does not establish that the two failures have the same underlying mechanism.
No smaller iteration budget was used to manufacture an iteration-limit result.

## Problem

For 24-vectors x and z, minimize

    ||x - 2||² + ||z||²

subject to

    z <= (1 - A x)³,  x >= 0,  z >= 0,

where the cube is elementwise and A is a dense NumPy identity matrix. Its
density is 1/24, below the automatic conversion threshold of 0.05.

The exact optimum is x=1, z=0, objective 24: feasibility implies x<=1, and
each (x_i-2)² is at least one. The cubic constraint is flat there, and the
active constraint gradients fail the usual constraint qualification. This
is deliberately a numerically difficult formulation of a simple problem.
This formulation is used to expose numerical sensitivity, not recommended as
a way to model this optimization task in practical code.

Successful termination is **not** an assertion of exact primal/dual accuracy.
The control returns an objective slightly below 24 because a small cubic
constraint residual permits a larger x>1 violation. Both the original cubic
residual and this implied linear-bound violation are reported by the verifier.
The example demonstrates sensitivity to representation; it does not establish
incorrect derivatives, a universal regression, or a generally superior setting.

## Environment and commands

Tested: Python 3.11.15, macOS ARM64, cyipopt 1.7.0 linked to IPOPT 3.14.19
and MUMPS 5.6.2. Python package versions are pinned in `requirements.txt`.
The IPOPT/MUMPS versions are native prerequisites, **not** installed or pinned
by that requirements file. Different native builds may change the outcome.

With IPOPT development headers/library and pkg-config already installed:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python toy.py
.venv/bin/python toy.py --dense-route
```

Run each command as a fresh process. The first command is expected to exit
nonzero with CVXPY `SolverError`; the second prints `status=optimal`.
No project installation is required. Existing `OMP_NUM_THREADS`,
`OPENBLAS_NUM_THREADS`, `MKL_NUM_THREADS`, `VECLIB_MAXIMUM_THREADS`, and
`BLIS_NUM_THREADS` were unset in the tested environment.

To retain machine-readable measurements as well as IPOPT's iteration log:

```sh
.venv/bin/python verify_toy.py --output default.json > default.log 2>&1
.venv/bin/python verify_toy.py --dense-route --output dense.json > dense.log 2>&1
```

The verifier refuses to overwrite its JSON output. It catches the expected
CVXPY solver error so it can save the native failed iterate; its process exit
code alone is therefore not a success classification. Read `native_status`
and `cvxpy_status`. Both runs use the same default CVXPY IPOPT options plus
`max_iter=3000`, `print_level=5`, and `sb=yes`. All observed effective options
are retained in the JSON. No warm-start solution, retries, helpers, or tuning
is used. The start is generated from NumPy RNG seed 0, not a solved point.

The measurement wrapper leaves the solve unchanged. It records starts/bounds,
normalizes the initial Jacobian and one Lagrangian Hessian for comparison,
checks initial derivatives against analytical formulas, and independently
reconstructs the original constraint residual and objective from the final
native x,z vector. Initial-point agreement is not a global derivative proof.

Verified results are in `VERIFIED_RESULTS.json`: two fresh-process repetitions
per condition, identical starts/bounds/options and initial oracle values, and
identical outcomes across repetitions. The bundle was also run outside the
repository using an environment in which CVXOPF was not importable. The default
native objective was 23.9951805503159, with cubic/nonnegativity violation
3.14802e-12; the control objective was 23.9641629586798, with violation
4.17848e-10. Their implied x<=1 violations were 1.80556e-4 and 9.41577e-4.
Raw logs remain in the ignored experiment results; their hashes are retained
in the compact results file.

## Scope of minimization

The exploratory work first extracted the archived application formulation and
verified its complete 9,124-coordinate start and native bounds without solving.
Following the request for a fully synthetic example, small polynomial,
trigonometric, and bilinear models were screened. Most converged in both modes.
The present example was selected for an actual failure/control contrast, not
as a representative performance sample. The final construction uses two
24-vectors and no intermediate linear-copy variable. Nearby dimensions/starts
do not always retain the same outcome; this is not a claim of absolute
minimality or broad failure frequency.

Original experiment evidence and the local editorial draft remain unchanged.
Exploratory inputs, trial logs, and the unused application extraction remain
under the experiment's ignored `results/toy_dispatch_screen/` directory.
