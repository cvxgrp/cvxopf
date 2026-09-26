"""Optional instrumentation for toy.py; each invocation performs one solve.

Captures native termination before CVXPY raises on a solver failure. The model
and options are the same as toy.py. Does not alter native starts or derivatives.
"""

import argparse
import hashlib
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform

import cvxpy as cp
import cyipopt
import numpy as np
from scipy import sparse

from toy import build_model


def fingerprint(array):
    return hashlib.sha256(np.asarray(array, dtype="<f8").tobytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dense-route", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    if args.dense_route:
        cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0
    problem = build_model()
    report = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "packages": {
            name: version(name)
            for name in ("cvxpy", "sparsediffpy", "cyipopt", "numpy", "scipy")
        },
        "threshold": cp.settings.SPARSE_DENSITY_THRESHOLD,
        "source_sha256": hashlib.sha256(
            Path(__file__).with_name("toy.py").read_bytes()
        ).hexdigest(),
        "thread_environment": {
            name: os.environ.get(name)
            for name in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
                "BLIS_NUM_THREADS",
            )
        },
    }
    native_class = cyipopt.Problem

    class Capture:
        def __init__(self, **kwargs):
            self.kwargs, self.options = kwargs, {}

        def add_option(self, name, value):
            self.options[name] = value

        def solve(self, x0):
            oracle = self.kwargs["problem_obj"]
            n, m = self.kwargs["n"], self.kwargs["m"]
            report.update(n=n, m=m, options=self.options, x0=x0.tolist())
            report["native_input_hashes"] = {
                key: fingerprint(self.kwargs[key]) for key in ("lb", "ub", "cl", "cu")
            }
            report["native_input_hashes"]["x0"] = fingerprint(x0)
            jr, jc = oracle.jacobianstructure()
            hr, hc = oracle.hessianstructure()
            report["jacobian_stored_entries"] = len(jr)
            # Initial-point checks only; analytical values use native x,z order.
            f = oracle.objective(x0)
            g = np.asarray(oracle.constraints(x0)).copy()
            grad = np.asarray(oracle.gradient(x0)).copy()
            jac = sparse.coo_array(
                (oracle.jacobian(x0).copy(), (jr, jc)), shape=(m, n)
            ).tocsr()
            jac.eliminate_zeros()
            jac.sort_indices()
            dual = np.ones(m)
            hess = sparse.coo_array(
                (oracle.hessian(x0, dual, 1.0).copy(), (hr, hc)), shape=(n, n)
            ).tocsr()
            hess.eliminate_zeros()
            hess.sort_indices()
            xx, zz = x0[:24], x0[24:]
            expected_jac = np.zeros((72, 48))
            index = np.arange(24)
            # CVXPY's NLP interface represents these inequalities as g >= 0.
            expected_jac[index, index] = -3 * (1 - xx) ** 2
            expected_jac[index, index + 24] = -1
            expected_jac[index + 24, index] = 1
            expected_jac[index + 48, index + 24] = 1
            np.testing.assert_allclose(
                g, np.r_[(1 - xx) ** 3 - zz, xx, zz], atol=1e-12, rtol=1e-12
            )
            np.testing.assert_allclose(
                grad, np.r_[2 * (xx - 2), 2 * zz], atol=1e-12, rtol=1e-12
            )
            np.testing.assert_allclose(
                jac.toarray(), expected_jac, atol=1e-12, rtol=1e-12
            )
            np.testing.assert_allclose(
                hess.toarray(),
                np.diag(np.r_[2 + 6 * (1 - xx), np.full(24, 2.0)]),
                atol=1e-12,
                rtol=1e-12,
            )
            report["initial_analytical_checks_passed"] = True
            report["initial_oracle_hashes"] = {
                "objective": fingerprint(f),
                "constraints": fingerprint(g),
                "gradient": fingerprint(grad),
                "jacobian": fingerprint(jac.toarray()),
                "hessian_lower_ones_dual": fingerprint(hess.toarray()),
            }
            report["jacobian_numerical_nonzeros"] = jac.nnz
            # Restore forward caches before the solve, as CVXPY's interface does.
            oracle.objective(x0)
            oracle.constraints(x0)

            def intermediate(algorithm, iteration, *unused):
                report["iterations"] = iteration
                return True

            oracle.intermediate = intermediate
            native = native_class(**self.kwargs)
            for name, value in self.options.items():
                native.add_option(name, value)
            x, info = native.solve(x0)
            report.update(
                native_status=int(info["status"]),
                native_message=info["status_msg"].decode(),
                native_objective=float(info["obj_val"]),
                solution=x.tolist(),
            )
            g = np.asarray(oracle.constraints(x))
            report["native_constraint_violation"] = float(
                max(0.0, np.max(self.kwargs["cl"] - g), np.max(g - self.kwargs["cu"]))
            )
            # Independent reconstruction of original constraints and objective.
            xx, zz = x[:24], x[24:]
            report["original_constraint_violation"] = float(
                max(0.0, np.max(zz - (1 - xx) ** 3), np.max(-xx), np.max(-zz))
            )
            report["original_objective_reconstructed"] = float(
                np.sum((xx - 2) ** 2) + np.sum(zz**2)
            )
            report["implied_x_upper_bound_violation"] = float(max(0.0, np.max(xx - 1)))
            return x, info

    cyipopt.Problem = Capture
    try:
        problem.solve(solver=cp.IPOPT, nlp=True, print_level=5, sb="yes", max_iter=3000)
    except cp.error.SolverError as error:
        report["cvxpy_error"] = str(error)
    finally:
        cyipopt.Problem = native_class
    report["cvxpy_status"] = problem.status
    report["cvxpy_objective"] = problem.value
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
