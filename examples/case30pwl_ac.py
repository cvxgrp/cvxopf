"""
Single time-step AC-OPF for the 30-bus case with piecewise-linear costs.

Every generator in ``case30pwl`` uses a MODEL=1 (piecewise-linear) cost curve.
cvxopf builds each PWL cost as the pointwise maximum of its convex segment
lines (see ``cost.py``), which solves cleanly here.

Why this is an example and not a Pypower-oracle test: ``pypower==5.1.19``
cannot solve an all-PWL case under numpy 2.x -- ``opf_costfcn`` does
``baseMVA * polycost(gencost[ipol], ...)`` where ``ipol`` (the polynomial-cost
generators) is empty, and ``float * []`` raises ``TypeError``. So no reference
fixture can be generated for it. The PWL cost implementation is instead
validated against Pypower on a mixed PWL/polynomial case
(``tests/test_vs_pypower_reference.py``, case9_pwl), where ``ipol`` is
non-empty and Pypower solves.

Run from the repository root:
    python examples/case30pwl_ac.py
"""

from cvxopf.testcases.case30pwl import case30pwl
from cvxopf.problem import build_opf, OPFOptions
from cvxopf.results import extract_results


def main():
    print("=" * 60)
    print("cvxopf \u2014 case30pwl single-step AC-OPF (piecewise-linear costs)")
    print("=" * 60)

    # --- build ---
    options = OPFOptions(init_flat=True)
    build = build_opf(case30pwl(), formulation="ac", options=options)

    print(f"\nVariables  : {len(build.prob.variables())}")
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
    print(f"  {'-' * 4}  {'-' * 10}  {'-' * 10}")
    for k in range(build.data["ng"]):
        print(f"  {k:>4}  {results['Pg'][k]:>10.4f}  {results['Qg'][k]:>10.4f}")


if __name__ == "__main__":
    main()
