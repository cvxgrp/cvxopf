# examples/case9_storage_ideal_vs_lossy_dc_24h.py
"""
Ideal vs lossy battery, head to head, on case9 lossy DC OPF with T=24.

Uses the load profile of case9_storage_dc_24h.py:

    scale(t) = 1.0 + 0.3 * sin(2*pi*t/24)

and solves the same problem twice, once with a StorageUnitIdeal and once
with a StorageUnitLossy that shares its bus, rating, capacity, initial SoC,
and aging weight. The lossy unit adds:

    charge_efficiency        = 0.95
    discharge_efficiency     = 0.95
    self_discharge_per_month = 0.02   (2% per 730-hour month)

Neither unit has a terminal SoC policy, so final SoC may differ between the
two runs; compare objectives with that in mind.

Usage:
    uv run examples/case9_storage_ideal_vs_lossy_dc_24h.py
"""

import numpy as np
import pandas as pd

from cvxopf.testcases import case9
from cvxopf.problem import build_opf_multistep, StorageUnitIdeal
from cvxopf.storage_lossy import StorageUnitLossy
from cvxopf.results import extract_results


def main():
    ppc     = case9()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    T       = 24

    t_idx  = np.arange(T)
    scales = 1.0 + 0.3 * np.sin(2 * np.pi * t_idx / T)
    df_P = pd.DataFrame(np.outer(scales, Pd_base))
    df_Q = pd.DataFrame(np.outer(scales, Qd_base))

    ideal = StorageUnitIdeal(
        bus=5,
        apparent_power_rating=60.0,
        capacity=150.0,
        initial_soc=75.0,
        aging_weight=1e-2,
    )
    lossy = StorageUnitLossy(
        bus=5,
        apparent_power_rating=60.0,
        capacity=150.0,
        initial_soc=75.0,
        charge_efficiency=0.95,
        discharge_efficiency=0.95,
        self_discharge_per_month=0.02,
        aging_weight=1e-2,
    )

    build_i = build_opf_multistep(
        ppc, df_P, df_Q, T=T, formulation="lossy_dc", storage=[ideal],
    )
    build_l = build_opf_multistep(
        ppc, df_P, df_Q, T=T, formulation="lossy_dc", lossy_storage=[lossy],
    )
    build_i.solve()
    build_l.solve()
    r_i = extract_results(build_i)
    r_l = extract_results(build_l)

    b_i, soc_i = r_i["b"][:, 0], r_i["soc"][:, 0]
    b_l, soc_l = r_l["ls_b"][:, 0], r_l["ls_soc"][:, 0]

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print()
    print(f"{'':28}  {'ideal':>12}  {'lossy':>12}")
    print("-" * 56)
    print(f"{'Status':28}  {r_i['status']:>12}  {r_l['status']:>12}")
    print(f"{'Objective ($)':28}  {r_i['objective']:>12.4f}  "
          f"{r_l['objective']:>12.4f}")
    print(f"{'Storage cost ($)':28}  {r_i['storage_cost']:>12.4f}  "
          f"{r_l['ls_cost']:>12.4f}")
    print(f"{'Net-power throughput (MWh)':28}  {np.sum(np.abs(b_i)):>12.3f}  "
          f"{np.sum(np.abs(b_l)):>12.3f}")
    print(f"{'Energy discharged (MWh)':28}  {np.sum(np.maximum(b_i, 0)):>12.3f}  "
          f"{np.sum(r_l['ls_p_dis']):>12.3f}")
    print(f"{'Energy charged (MWh)':28}  {np.sum(np.maximum(-b_i, 0)):>12.3f}  "
          f"{np.sum(r_l['ls_p_ch']):>12.3f}")
    print(f"{'Final SoC (MWh)':28}  {soc_i[-1]:>12.3f}  {soc_l[-1]:>12.3f}")
    print(f"{'Conversion loss (MWh)':28}  {0.0:>12.3f}  "
          f"{np.sum(r_l['ls_conversion_loss']):>12.3f}")
    print(f"{'Self-discharge (MWh)':28}  {0.0:>12.3f}  "
          f"{np.sum(r_l['ls_self_discharge']):>12.4f}")
    print(f"{'Max split min(ch, dis) (MW)':28}  {'-':>12}  "
          f"{np.max(r_l['ls_split']):>12.2e}")

    # ------------------------------------------------------------------
    # Per-step table
    # ------------------------------------------------------------------
    print()
    print(f"{'t':>3}  {'scale':>6}  {'b ideal':>9}  {'b lossy':>9}  "
          f"{'soc ideal':>10}  {'soc lossy':>10}")
    print("-" * 56)
    for t in range(T):
        print(f"{t:>3}  {scales[t]:>6.3f}  {b_i[t]:>9.3f}  {b_l[t]:>9.3f}  "
              f"{soc_i[t]:>10.3f}  {soc_l[t]:>10.3f}")
    print()
    print("b is net grid injection in MW (positive = discharging); "
          "soc is post-step MWh.")


if __name__ == "__main__":
    main()
