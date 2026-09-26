"""Evaluate the frozen time-only model's nonlinear oracles without native IPOPT."""

import argparse
import hashlib
import json
from pathlib import Path
import sys

import prepare_historical as prep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("a", "b"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--directional", action="store_true")
    parser.add_argument("--dense-route", action="store_true",
                        help="Diagnostic only: disable new density-based sparse dispatch")
    args = parser.parse_args()
    output = args.output.resolve()
    sys.path[:0] = [str(prep.SOURCE / "src"), str(prep.SOURCE)]
    import numpy as np
    from scipy import sparse
    from experiments.case118_vectorization_replay import worker  # noqa: F401
    import cyipopt
    import cvxpy as cp
    from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles
    import solve_historical

    if args.dense_route:
        if args.arm != "b":
            raise ValueError("Density-dispatch control applies only to the new stack")
        cp.settings.SPARSE_DENSITY_THRESHOLD = 0.0

    structure = {}
    original_init = Oracles.__init__

    def fingerprint_problem(problem):
        """Label columns by canonical offset; hash complete expression trees.

        Constraint blocks preserve native order and Fortran scalar expansion.
        Full leaf values and atom metadata are included, not truncated display text.
        """
        variables = {v.id: i for i, v in enumerate(problem.variables())}
        memo = {}

        def array_record(value):
            if sparse.issparse(value):
                value = value.tocsr(copy=True)
                value.sum_duplicates()
                value.sort_indices()
                return dict(shape=value.shape, sparse=True,
                            indices=value.indices.tolist(), indptr=value.indptr.tolist(),
                            data=value.data.tolist())
            value = np.asarray(value)
            return dict(shape=value.shape, values=value.tolist())

        def metadata(value):
            if value is None or isinstance(value, (str, int, float, bool)):
                return value
            if isinstance(value, slice):
                return dict(slice=[value.start, value.stop, value.step])
            if isinstance(value, np.generic):
                return value.item()
            if isinstance(value, (tuple, list)):
                return [metadata(item) for item in value]
            if isinstance(value, np.ndarray) or sparse.issparse(value):
                return array_record(value)
            raise TypeError(f"Unrecognized atom metadata: {type(value)}")

        def node(expr):
            if id(expr) in memo:
                return memo[id(expr)]
            record = dict(type=type(expr).__module__ + "." + type(expr).__name__, shape=expr.shape)
            if isinstance(expr, cp.Variable):
                record["canonical_variable"] = variables[expr.id]
            elif isinstance(expr, (cp.Constant, cp.Parameter)):
                record["value"] = array_record(expr.value)
            else:
                record["metadata"] = metadata(expr.get_data())
                record["children"] = [node(child) for child in expr.args]
            encoded = json.dumps(record, sort_keys=True,
                                 default=lambda value: value.item()).encode()
            memo[id(expr)] = hashlib.sha256(encoded).hexdigest()
            return memo[id(expr)]

        offset = 0
        blocks = []
        for constraint in problem.constraints:
            blocks.append(dict(start=offset, stop=offset + constraint.size,
                               shape=constraint.shape, kind=type(constraint).__name__,
                               expression=node(constraint.expr)))
            offset += constraint.size
        return dict(objective=node(problem.objective.expr), constraints=blocks,
                    variable_shapes=[v.shape for v in problem.variables()],
                    scalar_row_order="Fortran order within each constraint block")

    def inspect_init(self, problem, *positional, **keywords):
        structure.update(fingerprint_problem(problem))
        original_init(self, problem, *positional, **keywords)

    Oracles.__init__ = inspect_init

    class OracleComplete(BaseException):
        pass

    class CaptureProblem:
        def __init__(self, **kwargs):
            self.oracle = kwargs["problem_obj"]
            self.n, self.m = kwargs["n"], kwargs["m"]

        def add_option(self, name, value):
            pass

        def solve(self, x):
            oracle = self.oracle
            x = np.asarray(x).copy()
            objective = float(oracle.objective(x))
            gradient = np.asarray(oracle.gradient(x)).copy()
            constraints = np.asarray(oracle.constraints(x)).copy()
            jr, jc = [np.asarray(v).copy() for v in oracle.jacobianstructure()]
            jv = np.asarray(oracle.jacobian(x)).copy()
            hr, hc = [np.asarray(v).copy() for v in oracle.hessianstructure()]
            rng = np.random.default_rng(6047)
            multipliers = dict(objective=np.zeros(self.m), ones=np.ones(self.m),
                               random=rng.standard_normal(self.m))
            arrays = dict(x=x, objective=objective, gradient=gradient, constraints=constraints,
                          jac_rows=jr, jac_cols=jc, jac_values=jv, hess_rows=hr, hess_cols=hc)
            for name, dual in multipliers.items():
                arrays[f"dual_{name}"] = dual
                arrays[f"hess_{name}"] = np.asarray(oracle.hessian(x, dual, 1.0)).copy()
            np.savez_compressed(output / "oracles.npz", **arrays)
            (output / "structure.json").write_text(json.dumps(
                structure, indent=2, default=lambda value: value.item()) + "\n")
            checks = []
            if args.directional:
                jac = sparse.coo_array((jv, (jr, jc)), shape=(self.m, self.n)).tocsr()
                dual = multipliers["random"]
                lower = sparse.coo_array((arrays["hess_random"], (hr, hc)),
                                         shape=(self.n, self.n)).tocsr()
                hessian = lower + lower.T - sparse.diags_array(lower.diagonal())

                def lag_gradient(point):
                    oracle.objective(point)
                    grad = np.asarray(oracle.gradient(point)).copy()
                    oracle.constraints(point)
                    vals = np.asarray(oracle.jacobian(point)).copy()
                    return grad + sparse.coo_array((vals, (jr, jc)), shape=jac.shape).T @ dual

                for direction_index in range(3):
                    direction = rng.standard_normal(self.n)
                    direction /= np.linalg.norm(direction)
                    for step in (1e-4, 1e-5, 1e-6):
                        plus, minus = x + step * direction, x - step * direction
                        f_fd = (oracle.objective(plus) - oracle.objective(minus)) / (2 * step)
                        c_fd = (oracle.constraints(plus).copy() - oracle.constraints(minus).copy()) / (2 * step)
                        h_fd = (lag_gradient(plus) - lag_gradient(minus)) / (2 * step)
                        expected_c, expected_h = jac @ direction, hessian @ direction
                        checks.append(dict(direction=direction_index, step=step,
                            objective_abs_error=abs(f_fd - gradient @ direction),
                            jacobian_max_abs_error=float(np.max(np.abs(c_fd - expected_c))),
                            hessian_max_abs_error=float(np.max(np.abs(h_fd - expected_h))),
                            jacobian_relative_inf=float(np.max(np.abs(c_fd - expected_c)) / max(1., np.max(np.abs(expected_c)))),
                            hessian_relative_inf=float(np.max(np.abs(h_fd - expected_h)) / max(1., np.max(np.abs(expected_h))))))
            (output / "oracle_summary.json").write_text(json.dumps(dict(
                arm=args.arm, n=self.n, m=self.m, objective=objective,
                jacobian_stored_entries=len(jv), hessian_stored_entries=len(hr),
                directional_checks=checks, native_optimization_run=False,
                dense_route_control=args.dense_route,
                harness_sha256=prep.digest(Path(__file__)),
            ), indent=2) + "\n")
            raise OracleComplete()

    cyipopt.Problem = CaptureProblem
    sys.argv = ["solve_historical.py", "--arm", args.arm, "--output", str(output),
                "--binding", str(prep.BASE / "solve-binding.json")]
    try:
        solve_historical.main()
    except OracleComplete:
        print((output / "oracle_summary.json").read_text(), flush=True)
    else:
        raise RuntimeError("Oracle capture was not reached")


if __name__ == "__main__":
    main()
