# examples/case9_storage_ac.py
"""
AC-OPF with a single battery storage unit on case9, T=3 time steps.

Demonstrates StorageUnitIdeal with the AC formulation:
  - Apparent power circle constraint (b^2 + b_q^2 <= S_max^2)
  - Reactive power capability for voltage support
  - State-of-charge dynamics across three time steps
  - L1 aging penalty on real power cycling

Load profile: 80% / 100% / 120% of base case load across three steps.
Storage unit: 50 MVA rating, 100 MWh capacity, initially half-charged.

Usage:
    uv run examples/case9_storage_ac.py

Reference:
    Nnorom et al., "Aging-Aware Battery Control via Convex Optimization,"
    Optimization and Engineering, 27:1303-1326, 2026.
"""

import numpy as np
import pandas as pd

from cvxopf.testcases import case9
from cvxopf.problem import build_opf_multistep, StorageUnitIdeal
from cvxopf.results import extract_results


def main():
    ppc     = case9()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()

    # Three time steps at 80%, 100%, 120% of base load
    scales = [0.8, 1.0, 1.2]
    df_P   = pd.DataFrame(np.outer(scales, Pd_base))
    df_Q   = pd.DataFrame(np.outer(scales, Qd_base))
    T      = 3

    # Storage unit on bus 5 (0-based internal; external bus 5 in case9)
    unit = StorageUnitIdeal(
        bus=5,
        apparent_power_rating=50.0,   # S_max = 50 MVA
        capacity=100.0,               # Q     = 100 MWh
        initial_soc=50.0,             # q_0   = 50 MWh (half charged)
        aging_weight=1e-2,            # lambda = 0.01 $/MW
    )

    build = build_opf_multistep(
        ppc, df_P, df_Q, T=T,
        formulation="ac",
        storage=[unit],
        delta=1.0,    # hourly time steps
    )
    build.solve()
    r = extract_results(build)

    # ------------------------------------------------------------------
    # Print results
    # ------------------------------------------------------------------
    print(f"Status:     {r['status']}")
    print(f"Objective:  {r['objective']:.4f} $/hr (total, all steps)")
    print(f"Storage cost (aging): {r['storage_cost']:.4f} $")
    print()

    print(f"{'Step':>4}  {'Load scale':>10}  {'Pg (MW)':>30}  "
          f"{'b (MW)':>8}  {'b_q (MVAr)':>10}  {'soc (MWh)':>10}")
    print("-" * 85)
    for t in range(T):
        Pg_t   = r["Pg"][t]        # (ng,) MW
        b_t    = r["b"][t, 0]      # scalar MW
        b_q_t  = r["b_q"][t, 0]   # scalar MVAr
        soc_t  = r["soc"][t, 0]   # scalar MWh
        print(f"{t:>4}  {scales[t]:>10.0%}  "
              f"{np.array2string(np.round(Pg_t, 2), separator=', '):>30}  "
              f"{b_t:>8.3f}  {b_q_t:>10.3f}  {soc_t:>10.3f}")

    print()
    print("Storage unit summary:")
    print(f"  Bus:                    {unit.bus}")
    print(f"  Apparent power rating:  {unit.apparent_power_rating} MVA")
    print(f"  Capacity:               {unit.capacity} MWh")
    print(f"  Initial SoC:            {unit.initial_soc} MWh")
    print(f"  Final SoC:              {r['soc'][-1, 0]:.3f} MWh")
    print(f"  Aging weight:           {unit.aging_weight} $/MW")
    print(f"  Total |b| throughput:   {np.sum(np.abs(r['b'])):.3f} MWh")

    print()
    print("SoC dynamics verification:")
    for t in range(T):
        if t == 0:
            expected = unit.initial_soc - r["b"][0, 0] * 1.0
        else:
            expected = r["soc"][t - 1, 0] - r["b"][t, 0] * 1.0
        residual = abs(r["soc"][t, 0] - expected)
        print(f"  t={t}: soc={r['soc'][t,0]:.4f}  expected={expected:.4f}  "
              f"residual={residual:.2e}")

    print()
    print("Apparent power constraint verification (b^2 + b_q^2 <= S_max^2):")
    for t in range(T):
        apparent = np.sqrt(r["b"][t, 0]**2 + r["b_q"][t, 0]**2)
        print(f"  t={t}: |S|={apparent:.3f} MVA  S_max={unit.apparent_power_rating} MVA  "
              f"{'OK' if apparent <= unit.apparent_power_rating + 1e-4 else 'VIOLATED'}")


if __name__ == "__main__":
    main()
