"""
Single time-step AC-OPF for the IEEE 14-bus test case.

Run from the repository root:
    python examples/case14_single_step.py
"""

from cvxopf.testcases import case14
from cvxopf.problem import build_acopf, OPFOptions
from cvxopf.results import extract_results


def main():
    print("=" * 60)
    print("cvxopf — case14 single-step AC-OPF")
    print("=" * 60)

    # --- build ---
    options = OPFOptions(init_flat=True)
    build   = build_acopf(case14(), options=options)

    print(f"\nVariables  : {build.prob.variables().__len__()}")
    print(f"Constraints: {len(build.prob.constraints)}")

    # --- solve ---
    print("\nSolving with IPOPT ...")
    build.solve()

    # --- extract ---
    results = extract_results(build)

    print(f"\nStatus    : {results['status']}")
    print(f"Objective : {results['objective']:.4f} $/hr")

    print("\nGenerator dispatch:")
    print(f"  {'Gen':>4}  {'Pg (MW)':>10}  {'Qg (MVAr)':>10}")
    print(f"  {'-'*4}  {'-'*10}  {'-'*10}")
    for k in range(build.data["ng"]):
        print(f"  {k:>4}  {results['Pg'][k]:>10.4f}  {results['Qg'][k]:>10.4f}")

    print("\nBus results:")
    print(f"  {'Bus':>4}  {'Vm (pu)':>10}  {'Va (deg)':>10}")
    print(f"  {'-'*4}  {'-'*10}  {'-'*10}")
    for i in range(build.data["nb"]):
        print(f"  {i:>4}  {results['Vm'][i]:>10.6f}  {results['Va_deg'][i]:>10.4f}")


if __name__ == "__main__":
    main()
