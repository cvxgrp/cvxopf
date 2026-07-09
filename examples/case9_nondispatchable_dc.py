"""
Case 9 with nondispatchable generator - DC single-step example.

Demonstrates:
- Single nondispatchable unit
- Lossy DC formulation (no reactive power)
- Result extraction and validation
"""

import numpy as np

from cvxopf import build_opf, extract_results, NondispatchableUnit
from cvxopf.testcases import case9

def main():
    print("Case 9 with Nondispatchable Generator - DC Example")
    print("=" * 60)

    # Define nondispatchable unit (e.g., solar farm)
    # Bus 5, 80 MW available, 100 MVA inverter
    nd_unit = NondispatchableUnit(
        bus=5,
        p_available=80.0,  # MW
        apparent_power_rating=100.0  # MVA (not used in DC constraints)
    )

    print(f"\nNondispatchable unit: {nd_unit}")

    # Build and solve the DC OPF
    print(f"\nBuilding lossy DC-OPF with nondispatchable generator...")
    build = build_opf(
        case=case9(),
        formulation="lossy_dc",
        nondispatchable=[nd_unit]
    )

    print("Solving...")
    build.solve(verbose=False)

    # Extract results
    results = extract_results(build)

    print(f"\nSolve status: {results['status']}")
    print(f"Total objective: {results['objective']:.2f} $")

    # Display results
    print(f"\nNondispatchable generator results:")
    print("-" * 60)
    print(f"P_nd: {results['p_nd'][0]:.2f} MW")
    print(f"Curtailment: {results['curtailment'][0]:.2f} MW")
    print(f"Available: {nd_unit.p_available:.2f} MW")
    print(f"Utilization: {results['p_nd'][0] / nd_unit.p_available * 100:.1f}%")

    # Show conventional generation
    print(f"\nConventional generation (Pg):")
    for i, pg_val in enumerate(results['Pg']):
        print(f"  Generator {i+1}: {pg_val:.2f} MW")

    # Verify results
    print(f"\nVerification:")
    print("-" * 60)
    
    # Check that p_nd <= available power
    p_nd_ok = results['p_nd'][0] <= nd_unit.p_available + 1e-3
    print(f"p_nd <= available power: {p_nd_ok}")
    
    # Check that curtailment = available - p_nd
    expected_curtailment = nd_unit.p_available - results['p_nd'][0]
    curtailment_ok = abs(results['curtailment'][0] - expected_curtailment) < 1e-6
    print(f"curtailment = available - p_nd: {curtailment_ok}")
    
    # Check that p_nd is non-negative
    p_nd_nonneg = results['p_nd'][0] >= -1e-3
    print(f"p_nd >= 0: {p_nd_nonneg}")
    
    # Check that q_nd is not in DC results
    q_nd_absent = "q_nd" not in results
    print(f"q_nd absent from DC results: {q_nd_absent}")
    
    # Compare with case without nondispatchable
    print(f"\nImpact analysis:")
    print("-" * 60)
    
    # Solve without nondispatchable
    build_no_nd = build_opf(case9(), formulation="lossy_dc")
    build_no_nd.solve(verbose=False)
    results_no_nd = extract_results(build_no_nd)
    
    total_pg_with_nd = np.sum(results['Pg'])
    total_pg_without_nd = np.sum(results_no_nd['Pg'])
    reduction = total_pg_without_nd - total_pg_with_nd
    
    print(f"Total conventional generation with ND: {total_pg_with_nd:.2f} MW")
    print(f"Total conventional generation without ND: {total_pg_without_nd:.2f} MW")
    print(f"Reduction due to ND injection: {reduction:.2f} MW")
    print(f"ND injection: {results['p_nd'][0]:.2f} MW")
    print(f"Accounting matches: {abs(reduction - results['p_nd'][0]) < 1.0}")

    print(f"\nExample completed successfully!")


if __name__ == "__main__":
    main()