"""Synthetic sparse-dispatch sensitivity example; no grid data or CVXOPF.

Run in separate processes: python toy.py; python toy.py --dense-route
The default route is expected to raise SolverError on the documented stack.
This is an intentionally degenerate constrained problem, not a derivative test.
"""

import argparse

import cvxpy as cp
import numpy as np


def build_model():
    n = 24
    x, z = cp.Variable(n), cp.Variable(n)
    x.value = np.random.default_rng(0).uniform(0, 1, n)
    z.value = np.full(n, 0.1)
    A = np.eye(n)  # Dense array; density 1/24 < automatic threshold 0.05.
    return cp.Problem(
        cp.Minimize(cp.sum_squares(x - 2) + cp.sum_squares(z)),
        [cp.power(1 - A @ x, 3) >= z, x >= 0, z >= 0],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-route", action="store_true")
    args = parser.parse_args()
    if args.dense_route:
        cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0
    problem = build_model()
    problem.solve(solver=cp.IPOPT, nlp=True, print_level=5, sb="yes", max_iter=3000)
    print(f"status={problem.status}; objective={problem.value}")


if __name__ == "__main__":
    main()
