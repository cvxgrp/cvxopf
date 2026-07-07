# examples/case9_storage_ac_24h.py
"""
AC-OPF with a single battery storage unit on case9, T=24 time steps.

Demonstrates StorageUnitIdeal with the AC formulation over a full 24-hour
day with sinusoidal load variation (one complete period across 24 steps):

    scale(t) = 1.0 + 0.3 * sin(2*pi*t/24)

This produces a load that peaks at step 6 (1.3x base) and troughs at
step 18 (0.7x base), representing a simplified diurnal load curve.

Storage unit: 60 MVA rating, 150 MWh capacity, initially half-charged.
Aging weight: 1e-2 $/MW (small L1 penalty to discourage unnecessary cycling).

The storage unit charges during low-load periods and discharges during
high-load periods, reducing peak generation cost. The apparent power
circle constraint allows reactive support throughout the day.

Usage:
    uv run examples/case9_storage_ac_24h.py
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
    T       = 24

    # Sinusoidal load scaling: one complete period over 24 steps
    t_idx  = np.arange(T)
    scales = 1.0 + 0.3 * np.sin(2 * np.pi * t_idx / T)

    df_P = pd.DataFrame(np.outer(scales, Pd_base))
    df_Q = pd.DataFrame(np.outer(scales, Qd_base))

    # Storage unit on bus 5 (external bus ID in case9)
    unit = StorageUnitIdeal(
        bus=5,
        apparent_power_rating=60.0,   # S_max = 60 MVA
        capacity=150.0,               # Q     = 150 MWh
        initial_soc=75.0,             # q_0   = 75 MWh (half charged)
        aging_weight=1e-2,            # lambda = 0.01 $/MW
    )

    print("Building AC OPF with storage (T=24)...")
    build = build_opf_multistep(
        ppc, df_P, df_Q, T=T,
        formulation="ac",
        storage=[unit],
        delta=1.0,    # hourly time steps
    )
    print("Solving...")
    build.solve()
    r = extract_results(build)
    print()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print(f"Status:               {r['status']}")
    print(f"Total objective:      {r['objective']:.4f} $/hr")
    print(f"Total storage cost:   {r['storage_cost']:.4f} $ (aging penalty)")
    print(f"Generation cost:      {r['objective'] - r['storage_cost']:.4f} $/hr")
    print(f"Total |b| throughput: {np.sum(np.abs(r['b'])):.3f} MWh")
    print(f"Max discharge:        {np.max(r['b']):.3f} MW "
          f"at t={np.argmax(r['b'][:, 0])}")
    print(f"Max charge:           {np.min(r['b']):.3f} MW "
          f"at t={np.argmin(r['b'][:, 0])}")
    print(f"Final SoC:            {r['soc'][-1, 0]:.3f} MWh "
          f"(initial: {unit.initial_soc} MWh)")
    print()

    # ------------------------------------------------------------------
    # Per-step table
    # ------------------------------------------------------------------
    print(f"{'t':>3}  {'scale':>6}  {'Total Pg':>9}  "
          f"{'b (MW)':>8}  {'b_q (MVAr)':>10}  {'soc (MWh)':>10}  "
          f"{'|S| (MVA)':>10}")
    print("-" * 68)
    for t in range(T):
        total_Pg = np.sum(r["Pg"][t])
        b_t      = r["b"][t, 0]
        b_q_t    = r["b_q"][t, 0]
        soc_t    = r["soc"][t, 0]
        apparent = np.sqrt(b_t**2 + b_q_t**2)
        marker   = " ◄" if abs(b_t) > 1.0 else ""
        print(f"{t:>3}  {scales[t]:>6.3f}  {total_Pg:>9.2f}  "
              f"{b_t:>8.3f}  {b_q_t:>10.3f}  {soc_t:>10.3f}  "
              f"{apparent:>10.3f}{marker}")

    print()
    print("(◄ marks steps where storage dispatches more than 1 MW real power)")

    # ------------------------------------------------------------------
    # SoC dynamics verification
    # ------------------------------------------------------------------
    print()
    print("SoC dynamics verification:")
    max_residual = 0.0
    for t in range(T):
        if t == 0:
            expected = unit.initial_soc - r["b"][0, 0] * 1.0
        else:
            expected = r["soc"][t - 1, 0] - r["b"][t, 0] * 1.0
        residual = abs(r["soc"][t, 0] - expected)
        max_residual = max(max_residual, residual)
    print(f"  Max residual across all steps: {max_residual:.2e} MWh  "
          f"({'PASS' if max_residual < 1e-3 else 'FAIL'})")

    # ------------------------------------------------------------------
    # Apparent power constraint verification
    # ------------------------------------------------------------------
    print()
    print("Apparent power constraint verification (b^2 + b_q^2 <= S_max^2):")
    max_violation = 0.0
    for t in range(T):
        apparent  = np.sqrt(r["b"][t, 0]**2 + r["b_q"][t, 0]**2)
        violation = apparent - unit.apparent_power_rating
        max_violation = max(max_violation, violation)
    print(f"  Max violation across all steps: {max_violation:.2e} MVA  "
          f"({'PASS' if max_violation < 1e-3 else 'FAIL'})")

    # ------------------------------------------------------------------
    # SoC bounds verification
    # ------------------------------------------------------------------
    print()
    print("SoC bounds verification (0 <= soc <= capacity):")
    soc_min = np.min(r["soc"])
    soc_max = np.max(r["soc"])
    print(f"  Min SoC: {soc_min:.3f} MWh  "
          f"({'PASS' if soc_min >= -1e-3 else 'FAIL'})")
    print(f"  Max SoC: {soc_max:.3f} MWh  "
          f"({'PASS' if soc_max <= unit.capacity + 1e-3 else 'FAIL'})")


if __name__ == "__main__":
    main()
