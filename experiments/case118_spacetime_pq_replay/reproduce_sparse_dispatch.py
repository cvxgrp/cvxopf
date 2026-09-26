"""Standalone sparsity demonstration, not a reproduction of OPF nonconvergence.

Run with CVXPY 1.9.2/sparsediffpy 0.3.0 and 1.9.3/0.6.1. No solver is called.
On 1.9.3, --dense-route disables the new density-based dispatch for comparison.
"""

import argparse
from importlib.metadata import version
import json

import cvxpy as cp
import numpy as np
from scipy import sparse
from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Bounds, Oracles


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-route", action="store_true")
    args = parser.parse_args()
    if args.dense_route:
        if not hasattr(cp.settings, "SPARSE_DENSITY_THRESHOLD"):
            raise ValueError("This control is for CVXPY 1.9.3")
        cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0
    x = cp.Variable((24, 3), name="x")
    x.value = np.arange(72, dtype=float).reshape((24, 3), order="F") / 72
    problem = cp.Problem(cp.Minimize(cp.sum(cp.square(x))), [np.eye(24) @ x == 1])
    bounds = Bounds(problem)
    oracle = Oracles(bounds.new_problem, verbose=False)
    objective = oracle.objective(bounds.x0)
    gradient = np.asarray(oracle.gradient(bounds.x0)).copy()
    constraints = np.asarray(oracle.constraints(bounds.x0)).copy()
    rows, cols = oracle.jacobianstructure()
    values = np.asarray(oracle.jacobian(bounds.x0)).copy()
    jacobian = sparse.coo_array((values, (rows, cols)), shape=(72, 72)).tocsr()
    np.testing.assert_array_equal(gradient, 2 * bounds.x0)
    np.testing.assert_array_equal(constraints, bounds.x0 - 1)
    np.testing.assert_array_equal(jacobian.toarray(), np.eye(72))
    print(json.dumps(dict(
        cvxpy=cp.__version__, sparsediffpy=version("sparsediffpy"),
        dense_route_control=args.dense_route, objective=float(objective),
        jacobian_stored_entries=len(values), jacobian_exact_zeros=int(np.count_nonzero(values == 0)),
        numerical_jacobian_is_identity=True, native_solve_run=False,
    ), indent=2))


if __name__ == "__main__":
    main()
