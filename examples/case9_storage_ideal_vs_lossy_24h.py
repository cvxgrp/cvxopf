# examples/case9_storage_ideal_vs_lossy_24h.py
"""
Ideal vs lossy battery, head to head, on case9 in every network model, T=24.

Solves the same 24-hour problem with a StorageUnitIdeal and with a
StorageUnitLossy in each formulation, from the copper-plate model up to
full AC:

    singlenode_dc  ->  lossy_dc  ->  socp  ->  ac

Load follows one sinusoidal period, as in case9_storage_dc_24h.py:

    scale(t) = 1.0 + 0.3 * sin(2*pi*t/24)

Both units share bus, rating, capacity, initial SoC, and aging weight.
The lossy unit adds:

    charge_efficiency        = 0.95
    discharge_efficiency     = 0.95
    self_discharge_per_month = 0.02   (2% per 730-hour month)

In AC/SOCP the rating is an apparent-power circle on net power and reactive
power; in the DC models it is a real-power bound. Neither unit has a
terminal SoC policy, so both may end the day empty; compare objectives
with that in mind.

Usage:
    uv run examples/case9_storage_ideal_vs_lossy_24h.py
"""

import warnings

import numpy as np
import pandas as pd

from cvxopf.testcases import case9
from cvxopf.problem import build_opf_multistep, StorageUnitIdeal
from cvxopf.storage_lossy import StorageUnitLossy
from cvxopf.results import extract_results

FORMULATIONS = ["singlenode_dc", "lossy_dc", "socp", "ac"]
T = 24


def solve(formulation, df_P, df_Q, **devices):
    """Build and solve one model, suppressing the expected DC warnings."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case9(), df_P, df_Q, T=T, formulation=formulation, **devices
        )
    build.solve()
    return extract_results(build)


def summarize(r, lossy):
    """Collect comparable battery quantities from one solved result."""
    if lossy:
        b, soc = r["ls_b"][:, 0], r["ls_soc"][:, 0]
        charged, discharged = np.sum(r["ls_p_ch"]), np.sum(r["ls_p_dis"])
        cost = r["ls_cost"]
        conversion = np.sum(r["ls_conversion_loss"])
        self_discharge = np.sum(r["ls_self_discharge"])
        split = np.max(r["ls_split"])
    else:
        b, soc = r["b"][:, 0], r["soc"][:, 0]
        charged, discharged = np.sum(np.maximum(-b, 0)), np.sum(np.maximum(b, 0))
        cost = r["storage_cost"]
        conversion = self_discharge = 0.0
        split = np.nan
    return dict(status=r["status"], objective=r["objective"], cost=cost,
                charged=charged, discharged=discharged, final=soc[-1],
                conversion=conversion, self_discharge=self_discharge,
                split=split, soc=soc)


def main():
    ppc     = case9()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()

    scales = 1.0 + 0.3 * np.sin(2 * np.pi * np.arange(T) / T)
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

    rows = {}
    for formulation in FORMULATIONS:
        print(f"Solving {formulation} ...")
        rows[formulation, "ideal"] = summarize(
            solve(formulation, df_P, df_Q, storage=[ideal]), lossy=False
        )
        rows[formulation, "lossy"] = summarize(
            solve(formulation, df_P, df_Q, lossy_storage=[lossy]), lossy=True
        )

    # ------------------------------------------------------------------
    # Summary: one ideal and one lossy row per network model
    # ------------------------------------------------------------------
    print()
    print(f"{'model':14} {'battery':7} {'status':>8} {'objective $':>12} "
          f"{'stor $':>7} {'charged':>8} {'dischgd':>8} {'conv':>6} "
          f"{'selfdis':>7} {'final':>6} {'split MW':>9}")
    print("-" * 98)
    for formulation in FORMULATIONS:
        for kind in ("ideal", "lossy"):
            s = rows[formulation, kind]
            split = "-" if np.isnan(s["split"]) else f"{s['split']:.1e}"
            print(f"{formulation:14} {kind:7} {s['status']:>8} "
                  f"{s['objective']:>12.2f} {s['cost']:>7.3f} "
                  f"{s['charged']:>8.2f} {s['discharged']:>8.2f} "
                  f"{s['conversion']:>6.2f} {s['self_discharge']:>7.4f} "
                  f"{s['final']:>6.2f} {split:>9}")
    print()
    print("Energy columns are MWh over the day; stor $ is the cycling or "
          "throughput cost;")
    print("conv and selfdis are lossy-only losses; split is max "
          "min(p_ch, p_dis).")

    print()
    print("Lossy minus ideal objective ($):")
    for formulation in FORMULATIONS:
        gap = (rows[formulation, "lossy"]["objective"]
               - rows[formulation, "ideal"]["objective"])
        print(f"  {formulation:14} {gap:>10.2f}")

    # ------------------------------------------------------------------
    # Per-step post-step SoC (MWh): ideal / lossy for every model
    # ------------------------------------------------------------------
    print()
    header = f"{'t':>3} {'scale':>6}"
    for formulation in FORMULATIONS:
        header += f"  {formulation[:10]:>10} i/l"
    print(header)
    print("-" * len(header))
    for t in range(T):
        line = f"{t:>3} {scales[t]:>6.3f}"
        for formulation in FORMULATIONS:
            line += (f"  {rows[formulation, 'ideal']['soc'][t]:>6.1f}"
                     f"  {rows[formulation, 'lossy']['soc'][t]:>6.1f}")
        print(line)
    print()
    print("Each model column shows ideal SoC then lossy SoC (MWh, post-step).")


if __name__ == "__main__":
    main()
