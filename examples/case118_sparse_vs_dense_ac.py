"""
Sparse vs dense P/Q variable comparison for AC-OPF on case118.

Demonstrates OPFOptions(sparse_pq=True/False), reports CVXPY variable
counts, construction times, solve times, and IPOPT internal times.

NOTE: The sparse formulation currently uses a scalar constraint loop as a
workaround for https://github.com/cvxpy/cvxpy/issues/3442, which causes
numpy array indexing of CVXPY variables to segfault inside CVXPY's DNLP
Hessian analyser. This makes problem *construction* slower than the dense
path for large cases. IPOPT internal solve time is already faster sparse.
Once issue #3442 is resolved and the vectorised path is enabled, both
construction and solve time should favour the sparse formulation.

Usage:
    uv run examples/case118_sparse_vs_dense_ac.py
"""

import time
import numpy as np

from cvxopf.testcases import case118
from cvxopf.problem import build_opf, OPFOptions
from cvxopf.results import extract_results


def main():
    ppc = case118()
    nb  = ppc["bus"].shape[0]

    header = (f"{'':>8}  {'P/Q vars':>10}  {'status':>8}  {'obj ($/hr)':>14}"
              f"  {'build (s)':>10}  {'canonicalize+solve (s)':>10} ")
    # print(header)
    # print("-" * len(header))

    results = {}
    print_statements = []
    for sparse in [True, False]:
        label = "sparse" if sparse else "dense "

        print('### IPOPT output, ' + label + ' ###')

        t_build_0 = time.perf_counter()
        build     = build_opf(ppc, formulation="ac",
                              options=OPFOptions(sparse_pq=sparse))
        t_build   = time.perf_counter() - t_build_0

        n_pq = sum(
            v.size for v in build.prob.variables()
            if v.name() in ("P_vec", "Q_vec", "P", "Q")
        )

        t_solve_0 = time.perf_counter()
        build.solve()
        t_total   = time.perf_counter() - t_solve_0

        t_ipopt   = build.prob.solver_stats.solve_time

        r = extract_results(build)
        results[label.strip()] = r

        print_statements.append(f"[{label}]  {n_pq:>10d}  {r['status']:>8}  "
              f"{r['objective']:>14.4f}  "
              f"{t_build:>10.3f}s  "
              f"{t_total:>10.3f}s")

    print(header)
    print("-" * len(header))
    for ps in print_statements:
        print(ps)
    print()
    obj_diff = abs(results["sparse"]["objective"] - results["dense"]["objective"])
    pg_diff  = np.max(np.abs(results["sparse"]["Pg"] - results["dense"]["Pg"]))
    vm_diff  = np.max(np.abs(results["sparse"]["Vm"] - results["dense"]["Vm"]))
    print(f"Objective difference:  {obj_diff:.2e} $/hr")
    print(f"Max Pg difference:     {pg_diff:.2e} MW")
    print(f"Max Vm difference:     {vm_diff:.2e} p.u.")

    nnz = len(build_opf(ppc, formulation="ac",
                        options=OPFOptions(sparse_pq=True)).data["rows"])
    print()
    print(f"case118:  nb={nb},  nnz={nnz},  "
          f"dense P+Q vars={2*nb*nb},  sparse P+Q vars={2*nnz}  "
          f"({100*(1 - nnz/(nb*nb)):.0f}% reduction)")
    print()
    print("NOTE: sparse construction is slower due to scalar constraint loop")
    print("      (workaround for https://github.com/cvxpy/cvxpy/issues/3442).")
    print("      IPOPT internal solve time is reported above in the solver output.")
    print("      Vectorised construction will be enabled once #3442 is patched.")
    print("      IPOPT internal solver time is visible in the solver output above")
    print("      and is faster for the sparse problem instance.")


if __name__ == "__main__":
    main()