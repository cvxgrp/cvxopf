"""Reproduce the five bounded public-case observations in REPORT.md.

Prints compact JSON to stdout; never writes study artifacts or source files.
Run: uv run --extra dev python experiments/m11_socp/checkpoint.py
"""

import json
import platform
from time import perf_counter

import clarabel
import cvxpy as cp
import numpy as np
import pandas as pd

from cvxopf import build_opf_multistep, audit_socp_relaxation, recover_socp_voltage
from cvxopf.testcases import case9, case14


def main():
    records = []
    for factory, T, assembly in (
        (case9, 1, "vectorized"), (case9, 3, "vectorized"),
        (case9, 3, "stepwise"), (case14, 1, "vectorized"), (case14, 3, "vectorized"),
    ):
        case = factory()
        start = perf_counter()
        build = build_opf_multistep(
            case, pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1))),
            pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1))),
            T=T, formulation="socp", temporal_assembly=assembly,
        )
        built = perf_counter()
        canonical, _, _ = build.prob.get_problem_data(
            cp.CLARABEL, canon_backend=build.canonicalization_backend,
        )
        canonicalized = perf_counter()
        build.solve(max_iter=200, time_limit=20)
        solved = perf_counter()
        audit = audit_socp_relaxation(build)
        recovery = recover_socp_voltage(build)
        if not audit["available"] or not recovery["available"]:
            raise RuntimeError((audit["reason"], recovery["reason"]))
        records.append(dict(
            case=factory.__name__, T=T, assembly=assembly,
            build_s=built-start, canonicalize_s=canonicalized-built,
            solve_wall_s=solved-canonicalized, solver_s=build.prob.solver_stats.solve_time,
            scalar_variables=build.prob.size_metrics.num_scalar_variables,
            constraint_objects=len(build.prob.constraints), soc_sizes=canonical["dims"].soc,
            status=build.prob.status, objective_estimate=build.prob.value,
            relaxation_feasible=audit["feasible"],
            max_balance=max(audit["residuals"][k]["maximum"] for k in ("p_balance", "q_balance")),
            max_cone_violation=audit["residuals"]["edge_cones"]["maximum"],
            max_relative_gap=audit["edge_gaps"]["relative_summary"]["maximum"],
            max_cycle_rad=recovery["cycle_summary"]["maximum"],
            exact_products=recovery["exact_product_recovery"],
            recovered_ac_feasible=recovery["ac_feasible"],
            recovered_max_balance=max(recovery["ac_audit"]["residuals"][k]["maximum"] for k in ("p_balance", "q_balance")),
        ))
        if not audit["feasible"]:
            raise RuntimeError("Stop at failed independent relaxation audit")
    print(json.dumps(dict(
        platform=platform.platform(), machine=platform.machine(), python=platform.python_version(),
        cvxpy=cp.__version__, clarabel=clarabel.__version__, numpy=np.__version__,
        settings=dict(max_iter=200, time_limit=20), records=records,
    ), indent=2))


if __name__ == "__main__":
    main()
