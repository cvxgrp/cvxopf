"""
Case 9 with nondispatchable generator - AC multistep example.

Demonstrates:
- Single nondispatchable unit with time-varying availability
- AC formulation with reactive power support
- Result extraction and validation
"""

import numpy as np
import pandas as pd

from cvxopf import build_opf_multistep, extract_results, NondispatchableUnit
from cvxopf.testcases import case9

def main():
    print("Case 9 with Nondispatchable Generator - AC Multistep Example")
    print("=" * 60)

    # Define time series parameters
    T = 3  # 3 time steps
    
    # Create load time series (flat for simplicity)
    # Use base case load values scaled by 1.5x for a reasonable load level
    ppc = case9()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    scale_factor = 1.5  # Scale up load by 50% to make nondispatchable impact visible
    df_P = pd.DataFrame(np.tile(Pd_base * scale_factor, (T, 1)))
    df_Q = pd.DataFrame(np.tile(Qd_base * scale_factor, (T, 1)))

    # Define nondispatchable unit (e.g., wind farm)
    # Bus 5, 100 MVA inverter, ramping down from 100% to 50% of capacity
    nd_unit = NondispatchableUnit(
        bus=5,
        p_available=80.0,  # MW - this will be overridden by df_nd
        apparent_power_rating=100.0  # MVA
    )

    # Create time-varying availability profile (ramping down)
    # Column name must match the bus ID
    df_nd = pd.DataFrame({
        nd_unit.bus: [100.0, 75.0, 50.0]  # MW available at each time step
    })

    print(f"\nNondispatchable unit: {nd_unit}")
    print(f"Availability profile (MW):\n{df_nd}")

    # Build and solve the multistep OPF
    print(f"\nBuilding {T}-step AC-OPF with nondispatchable generator...")
    build = build_opf_multistep(
        case=case9(),
        df_P=df_P,
        df_Q=df_Q,
        T=T,
        formulation="ac",
        nondispatchable=[nd_unit],
        df_nd=df_nd
    )

    print("Solving...")
    build.solve(verbose=False)

    # Extract results
    results = extract_results(build)

    print(f"\nSolve status: {results['status']}")
    print(f"Total objective: {results['objective']:.2f} $")

    # Display results by time step
    print("\nResults by time step:")
    print("-" * 60)
    
    for t in range(T):
        print(f"\nTime step {t+1}:")
        print(f"  P_nd: {results['p_nd'][t, 0]:.2f} MW")
        print(f"  Q_nd: {results['q_nd'][t, 0]:.2f} MVAr")
        print(f"  Curtailment: {results['curtailment'][t, 0]:.2f} MW")
        print(f"  Available: {df_nd.iloc[t, 0]:.2f} MW")
        
        # Verify apparent power constraint
        p_nd = results['p_nd'][t, 0]
        q_nd = results['q_nd'][t, 0]
        apparent_power = np.sqrt(p_nd**2 + q_nd**2)
        print(f"  Apparent power: {apparent_power:.2f} MVA")
        print(f"  Apparent power limit: {nd_unit.apparent_power_rating:.2f} MVA")
        print(f"  Constraint satisfied: {apparent_power <= nd_unit.apparent_power_rating + 1e-4}")
        
        # Show conventional generation
        print(f"  Conventional generation (Pg): {results['Pg'][t, :]}")

    # Verify that p_nd respects the available power bounds
    print("\nVerification:")
    print("-" * 60)
    
    # Check that p_nd <= available power at each step
    p_nd_ok = np.all(results['p_nd'] <= df_nd.values + 1e-3)
    print(f"p_nd <= available power: {p_nd_ok}")
    
    # Check that curtailment = available - p_nd
    expected_curtailment = df_nd.values - results['p_nd']
    curtailment_ok = np.allclose(results['curtailment'], expected_curtailment, atol=1e-6)
    print(f"curtailment = available - p_nd: {curtailment_ok}")
    
    # Check that p_nd is non-negative
    p_nd_nonneg = np.all(results['p_nd'] >= -1e-3)
    print(f"p_nd >= 0: {p_nd_nonneg}")
    
    # Check apparent power constraints
    apparent_power = np.sqrt(results['p_nd']**2 + results['q_nd']**2)
    apr_ok = np.all(apparent_power <= nd_unit.apparent_power_rating + 1e-4)
    print(f"apparent power constraints: {apr_ok}")

    print("\nExample completed successfully!")


if __name__ == "__main__":
    main()