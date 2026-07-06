"""
Sparse vs dense P/Q variable comparison for AC-OPF on case57.

Demonstrates OPFOptions(sparse_pq=True/False), reports CVXPY variable
counts, solve times, and confirms numerical equivalence of results.

Usage:
    uv run examples/case57_sparse_vs_dense_ac.py
"""

import time
import numpy as np

from cvxopf.testcases import case57
from cvxopf.problem import build_opf, OPFOptions
from cvxopf.results import extract_results


def main():
    print(f"{'':>8}  {'P/Q vars':>10}  {'status':>8}  {'obj ($/hr)':>12}  "
          f"{'time (s)':>9}")
    print("-" * 55)

    results = {}
    for sparse in [True, False]:
        label  = "sparse" if sparse else "dense "

        build  = build_opf(case57(), formulation="ac",
                           options=OPFOptions(sparse_pq=sparse))

        n_pq   = sum(
            v.size for v in build.prob.variables()
            if v.name() in ("P_vec", "Q_vec", "P", "Q")
        )

        t0     = time.perf_counter()
        build.solve()
        dt     = time.perf_counter() - t0

        r      = extract_results(build)
        results[label.strip()] = r

        print(f"[{label}]  {n_pq:>10d}  {r['status']:>8}  "
              f"{r['objective']:>12.4f}  {dt:>9.3f}s")

    print()
    obj_diff = abs(results["sparse"]["objective"] - results["dense"]["objective"])
    pg_diff  = np.max(np.abs(results["sparse"]["Pg"] - results["dense"]["Pg"]))
    vm_diff  = np.max(np.abs(results["sparse"]["Vm"] - results["dense"]["Vm"]))
    print(f"Objective difference:  {obj_diff:.2e} $/hr")
    print(f"Max Pg difference:     {pg_diff:.2e} MW")
    print(f"Max Vm difference:     {vm_diff:.2e} p.u.")

    ppc = case57()
    nb  = ppc["bus"].shape[0]
    nnz = len(build_opf(ppc, formulation="ac",
                        options=OPFOptions(sparse_pq=True)).data["rows"])
    print()
    print(f"case57:  nb={nb},  nnz={nnz},  "
          f"dense P+Q vars={2*nb*nb},  sparse P+Q vars={2*nnz}  "
          f"({100*(1 - nnz/(nb*nb)):.0f}% reduction)")


if __name__ == "__main__":
    main()
