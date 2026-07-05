"""
Multi time-step AC-OPF for the 9-bus test case with flat (constant) load.

Solves a T=3 step problem where the load profile is identical at every
step. The per-step solutions should therefore be identical to each other
and to the single-step solution.

Run from the repository root:
    python examples/case9_multistep_flat_load.py
"""

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf.testcases import case9
from cvxopf.problem import build_acopf, build_acopf_multistep, OPFOptions
from cvxopf.results import extract_results


def main():
    T   = 3
    ppc = case9()

    print("=" * 60)
    print(f"cvxopf — case9 multi-step AC-OPF  (T={T}, flat load)")
    print("=" * 60)

    # --- build flat load DataFrames ---
    Pd_base = ppc["bus"][:, 2].copy()   # MW
    Qd_base = ppc["bus"][:, 3].copy()   # MVAr
    df_P    = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q    = pd.DataFrame(np.tile(Qd_base, (T, 1)))

    print(f"\nLoad profile (MW) across {T} steps:")
    print(df_P.to_string(index=True))

    # --- build multi-step problem ---
    options = OPFOptions(init_flat=True)
    build   = build_acopf_multistep(ppc, df_P, df_Q, T=T, options=options)

    print(f"\nVariables  : {len(build.prob.variables())}")
    print(f"Constraints: {len(build.prob.constraints)}")

    # --- solve ---
    print("\nSolving with IPOPT ...")
    build.prob.solve(solver=cp.IPOPT, verbose=False)

    results = extract_results(build)

    print(f"\nStatus    : {results['status']}")
    print(f"Objective : {results['objective']:.4f} $/hr  (total across all steps)")

    # --- per-step results ---
    for t in range(T):
        print(f"\n--- Step {t} ---")
        print(f"  {'Gen':>4}  {'Pg (MW)':>10}  {'Qg (MVAr)':>10}")
        print(f"  {'-'*4}  {'-'*10}  {'-'*10}")
        ng = build.data["ng"]
        for k in range(ng):
            print(
                f"  {k:>4}  "
                f"{results['Pg'][t, k]:>10.4f}  "
                f"{results['Qg'][t, k]:>10.4f}"
            )

    # --- compare to single-step ---
    print("\n" + "=" * 60)
    print("Comparison: multi-step step 0  vs  single-step")
    print("=" * 60)

    build_s = build_acopf(ppc, options=options)
    build_s.prob.solve(solver=cp.IPOPT, verbose=False)
    results_s = extract_results(build_s)

    print(f"\n  {'Gen':>4}  {'Multi Pg':>12}  {'Single Pg':>12}  {'Diff':>10}")
    print(f"  {'-'*4}  {'-'*12}  {'-'*12}  {'-'*10}")
    ng = build.data["ng"]
    for k in range(ng):
        diff = results["Pg"][0, k] - results_s["Pg"][k]
        print(
            f"  {k:>4}  "
            f"{results['Pg'][0, k]:>12.4f}  "
            f"{results_s['Pg'][k]:>12.4f}  "
            f"{diff:>+10.4f}"
        )

    print(f"\n  Multi-step obj/step : {results['objective'] / T:.4f} $/hr")
    print(f"  Single-step obj     : {results_s['objective']:.4f} $/hr")


if __name__ == "__main__":
    main()
