# examples/case9_storage_dc.py
"""
Lossy DC OPF with a single battery storage unit on case9, T=3 time steps.

Demonstrates StorageUnitIdeal with the lossy DC formulation:
  - Real power bound only: |b_t| <= S_max  (apparent power rating applied
    as real power limit; no reactive power in DC formulation)
  - State-of-charge dynamics across three time steps
  - L1 aging penalty on real power cycling

Load profile: 80% / 100% / 120% of base case load across three steps.
Storage unit: 50 MW rating, 100 MWh capacity, initially half-charged.

Note: a UserWarning is emitted at build time because the apparent power
rating is applied as a real power bound in the DC formulation.

Usage:
    uv run examples/case9_storage_dc.py

Reference:
    Nnorom et al., "Aging-Aware Battery Control via Convex Optimization,"
    Optimization and Engineering, 27:1303-1326, 2026.
"""

import warnings

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

    # Storage unit on bus 5 (external bus ID in case9)
    unit = StorageUnitIdeal(
        bus=5,
        apparent_power_rating=50.0,   # applied as real power limit in DC
        capacity=100.0,               # Q     = 100 MWh
        initial_soc=50.0,             # q_0   = 50 MWh (half charged)
        aging_weight=1e-2,            # lambda = 0.01 $/MW
    )

    print("Building DC OPF with storage...")
    print("(UserWarning expected: apparent power rating used as real power "
          "limit in DC formulation)")
    print()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        build = build_opf_multistep(
            ppc, df_P, df_Q, T=T,
            formulation="lossy_dc",
            storage=[unit],
            delta=1.0,    # hourly time steps
        )
        for w in caught:
            print(f"Warning: {w.message}")
    print()

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
          f"{'b (MW)':>8}  {'soc (MWh)':>10}")
    print("-" * 72)
    for t in range(T):
        Pg_t  = r["Pg"][t]       # (ng,) MW
        b_t   = r["b"][t, 0]    # scalar MW
        soc_t = r["soc"][t, 0]  # scalar MWh
        print(f"{t:>4}  {scales[t]:>10.0%}  "
              f"{np.array2string(np.round(Pg_t, 2), separator=', '):>30}  "
              f"{b_t:>8.3f}  {soc_t:>10.3f}")

    print()
    print("Storage unit summary:")
    print(f"  Bus:                    {unit.bus}")
    print(f"  Apparent power rating:  {unit.apparent_power_rating} MVA "
          f"(applied as real power limit in DC)")
    print(f"  Capacity:               {unit.capacity} MWh")
    print(f"  Initial SoC:            {unit.initial_soc} MWh")
    print(f"  Final SoC:              {r['soc'][-1, 0]:.3f} MWh")
    print(f"  Aging weight:           {unit.aging_weight} $/MW")
    print(f"  Total |b| throughput:   {np.sum(np.abs(r['b'])):.3f} MWh")

    print()
    print("b_q absent from results (DC has no reactive power):",
          "b_q" not in r)

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
    print("Real power bound verification (|b_t| <= S_max):")
    for t in range(T):
        ok = abs(r["b"][t, 0]) <= unit.apparent_power_rating + 1e-4
        print(f"  t={t}: b={r['b'][t,0]:.3f} MW  "
              f"S_max={unit.apparent_power_rating} MW  "
              f"{'OK' if ok else 'VIOLATED'}")


if __name__ == "__main__":
    main()
