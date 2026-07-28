"""
Lossy DC OPF for the IEEE 14-bus test case.

Demonstrates:
1. Building and solving the lossy DC OPF for case14 at base load
2. Printing branch flows and identifying lines at or near capacity
3. Sweeping load scale factor alpha to find alpha_max — the largest
   value for which the problem remains feasible
4. Printing a summary table of results at alpha=4.6

Note on problem setup
---------------------
To reproduce the alpha_max ~ 4.66 result from the reference:

    Convex Optimization with Smart Grid Examples,
    https://doi.org/10.2172/3018252

two modifications are made to the MATPOWER case14 data:

1. Generator Pmax values are doubled (the MATPOWER case14 Pmax values
   are tight; the reference notebook uses 2x Pmax).
2. All branch flow limits are set to 175 MW (the MATPOWER case14 has
   rateA=9900 MW which is effectively unlimited; the reference notebook
   uses 175 MW for all branches).

Run from the repository root:
    python examples/case14_lossy_dc.py
"""

import warnings

import numpy as np

from cvxopf.testcases import case14
from cvxopf.problem import build_opf
from cvxopf.results import extract_results


def _setup_case():
    """
    Load case14 and apply the two modifications needed to match the
    reference problem setup.
    """
    ppc = case14()

    # 1. Double Pmax
    ppc["gen"]              = ppc["gen"].copy()
    ppc["gen"][:, 8]       *= 2.0   # PMAX column

    # 2. Set all branch limits to 175 MW
    ppc["branch"]           = ppc["branch"].copy()
    ppc["branch"][:, 5]    = 175.0  # rateA column

    return ppc


def main():
    ppc     = _setup_case()
    baseMVA = ppc["baseMVA"]
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    nb      = ppc["bus"].shape[0]
    nl      = ppc["branch"].shape[0]

    print("=" * 60)
    print("cvxopf — case14 lossy DC OPF")
    print("Reference: Convex Optimization with Smart Grid Examples")
    print("           https://doi.org/10.2172/3018252")
    print("=" * 60)
    print(f"\nBuses          : {nb}")
    print(f"Branches       : {nl}")
    print(f"Base load      : {Pd_base.sum():.1f} MW")
    print(f"Total Pmax     : {ppc['gen'][:, 8].sum():.1f} MW (2x MATPOWER)")
    print("Branch limit   : 175 MW (all branches, per reference)")

    # ------------------------------------------------------------------
    # 1. Solve at base load (alpha=1.0)
    # ------------------------------------------------------------------
    print("\n" + "-" * 60)
    print("Base load solution (alpha = 1.0)")
    print("-" * 60)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build_base = build_opf(ppc, formulation="lossy_dc")
    build_base.solve()
    r_base = extract_results(build_base)

    print(f"Status    : {r_base['status']}")
    print(f"Objective : {r_base['objective']:.4f} $/hr")

    print("\nGenerator dispatch:")
    print(f"  {'Gen':>4}  {'Pg (MW)':>10}  {'Pmax (MW)':>10}")
    print(f"  {'-'*4}  {'-'*10}  {'-'*10}")
    Pgmax = build_base.data["Pgmax"] * baseMVA
    for k in range(build_base.data["ng"]):
        print(f"  {k:>4}  {r_base['Pg'][k]:>10.3f}  {Pgmax[k]:>10.3f}")

    print("\nBranch flows (MW):")
    f_max_MW = build_base.data["f_max"] * baseMVA
    print(f"  {'Branch':>6}  {'Flow (MW)':>10}  {'Limit (MW)':>10}  {'At limit':>8}")
    print(f"  {'-'*6}  {'-'*10}  {'-'*10}  {'-'*8}")
    n_constrained = 0
    for e in range(nl):
        flow     = r_base["p_flows"][e]
        limit    = f_max_MW[e]
        at_limit = abs(flow) >= 0.99 * limit
        if at_limit:
            n_constrained += 1
        marker = " <--" if at_limit else ""
        print(f"  {e:>6}  {flow:>10.3f}  {limit:>10.1f}{marker}")
    print(f"\n  {n_constrained} branch(es) at capacity")

    # ------------------------------------------------------------------
    # 2. Alpha sweep to find alpha_max
    # ------------------------------------------------------------------
    print("\n" + "-" * 60)
    print("Load scaling sweep to find alpha_max")
    print("-" * 60)

    alpha_max    = None
    alpha_values = np.round(np.arange(1.0, 5.5, 0.1), 2)

    for alpha in alpha_values:
        ppc_alpha               = {**ppc, "bus": ppc["bus"].copy()}
        ppc_alpha["bus"][:, 2] = Pd_base * alpha
        ppc_alpha["bus"][:, 3] = Qd_base * alpha

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build_a = build_opf(ppc_alpha, formulation="lossy_dc")
        build_a.solve()
        r_a = extract_results(build_a)

        if r_a["status"] == "optimal":
            alpha_max = alpha
        else:
            print(f"  Infeasible at alpha = {alpha:.2f}  "
                  f"(status: {r_a['status']})")
            break

    print(f"\n  alpha_max = {alpha_max:.2f}  (reference: ~4.66)")
    print("  Note: step size 0.1 brackets the reference value")

    # ------------------------------------------------------------------
    # 3. Solution at alpha=4.6
    # ------------------------------------------------------------------
    print("\n" + "-" * 60)
    print("Solution at alpha = 4.6")
    print("-" * 60)

    alpha_demo              = 4.6
    ppc_46                  = {**ppc, "bus": ppc["bus"].copy()}
    ppc_46["bus"][:, 2]    = Pd_base * alpha_demo
    ppc_46["bus"][:, 3]    = Qd_base * alpha_demo

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build_46 = build_opf(ppc_46, formulation="lossy_dc")
    build_46.solve()
    r_46 = extract_results(build_46)

    print(f"Status    : {r_46['status']}")
    print(f"Objective : {r_46['objective']:.4f} $/hr")
    print(f"Total load: {Pd_base.sum() * alpha_demo:.1f} MW")
    print(f"Total Pg  : {r_46['Pg'].sum():.1f} MW")

    print("\nGenerator dispatch:")
    print(f"  {'Gen':>4}  {'Pg (MW)':>10}  {'Pmax (MW)':>10}  {'At max':>6}")
    print(f"  {'-'*4}  {'-'*10}  {'-'*10}  {'-'*6}")
    Pgmax_46 = build_46.data["Pgmax"] * baseMVA
    for k in range(build_46.data["ng"]):
        at_max = r_46["Pg"][k] >= 0.99 * Pgmax_46[k]
        marker = " <--" if at_max else ""
        print(f"  {k:>4}  {r_46['Pg'][k]:>10.3f}  "
              f"{Pgmax_46[k]:>10.3f}{marker}")

    print("\nConstrained branches (|flow| >= 99% of limit):")
    f_max_46   = build_46.data["f_max"] * baseMVA
    branch     = ppc["branch"]
    any_found  = False
    for e in range(nl):
        flow  = r_46["p_flows"][e]
        limit = f_max_46[e]
        if abs(flow) >= 0.99 * limit:
            f_bus = int(branch[e, 0])
            t_bus = int(branch[e, 1])
            print(f"  Branch {e:>2} (bus {f_bus:>2} -> "
                  f"{t_bus:>2}): "
                  f"{flow:>8.3f} MW  /  {limit:.1f} MW limit")
            any_found = True
    if not any_found:
        print("  None")


if __name__ == "__main__":
    main()