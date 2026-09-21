"""Networked multistep dispatch with renewable power, storage, and shedding.

This three-step lossy-DC case uses identity-aligned pandas load and solar
trajectories. Solar curtailment has zero objective cost and remains a metric of
interest; load shedding has a high linear VOLL cost and is used only when the
networked device fleet cannot serve the configured interruptible demand. A
battery shifts early solar energy while retaining a 20 MWh terminal reserve.

The reported objective includes generation, storage throughput, the lossy-DC
``r*p^2`` flow penalty, and shedding cost. The lossy-DC penalty does not
withdraw physical losses from nodal balance. ENS is reported separately so a
lower dispatch total cannot conceal unserved demand.

Usage:
    uv run examples/case9_multistep_load_shedding.py
"""

import pandas as pd

from cvxopf import (
    Load,
    NondispatchableUnit,
    StorageUnitIdeal,
    build_opf_multistep,
    extract_results,
)
from cvxopf.testcases import case9


def main():
    case = case9()
    delta = 1.0
    case["gen"][:, 8] = [100.0, 75.0, 75.0]

    loads = [
        Load(5, 70.0, "load-5"),
        Load(7, 80.0, "load-7"),
        Load(
            9,
            100.0,
            "load-9",
            shedding_cost_per_mwh=5000.0,
        ),
    ]
    load_profile = pd.DataFrame(
        {
            "load-5": [70.0, 90.0, 110.0],
            "load-7": [80.0, 100.0, 120.0],
            "load-9": [100.0, 125.0, 150.0],
        }
    )
    solar = [
        NondispatchableUnit(
            bus=5,
            p_available=80.0,
            apparent_power_rating=80.0,
            device_id="solar-5",
        )
    ]
    solar_profile = pd.DataFrame({"solar-5": [80.0, 20.0, 0.0]})
    storage = [
        StorageUnitIdeal(
            bus=7,
            apparent_power_rating=40.0,
            capacity=60.0,
            initial_soc=40.0,
            aging_weight=0.01,
            terminal_soc=20.0,
            terminal_constraint="equality",
        )
    ]

    build = build_opf_multistep(
        case,
        T=3,
        formulation="lossy_dc",
        loads=loads,
        df_load_p=load_profile,
        nondispatchable=solar,
        df_nd=solar_profile,
        storage=storage,
        delta=delta,
    )
    build.solve()
    result = extract_results(build)

    print("case9 multistep load shedding")
    print(f"status: {result['status']}")
    print()
    print(
        f"{'step':>4} {'load':>8} {'served':>8} {'solar':>8} "
        f"{'battery':>9} {'SoC':>8} {'shed':>8}"
    )
    print("-" * 65)
    for step in range(3):
        solar_mw = max(0.0, float(result["p_nd"][step, 0]))
        shed_mw = max(0.0, float(result["p_load_shed"][step, 0]))
        print(
            f"{step:4d} {result['p_load'][step].sum():8.1f} "
            f"{result['p_load_served'][step].sum():8.1f} "
            f"{solar_mw:8.1f} "
            f"{result['b'][step, 0]:9.1f} "
            f"{result['soc'][step, 0]:8.1f} "
            f"{shed_mw:8.1f}"
        )
    print()
    curtailment = max(
        0.0, delta * float(result["curtailment"][:, 0].sum())
    )
    print(f"solar curtailment: {curtailment:.1f} MWh")
    print(f"energy not served: {result['energy_not_served']:.1f} MWh")
    print(f"shedding cost:     {result['load_shedding_cost']:.1f} objective units")
    print(f"final storage SoC: {result['soc'][-1, 0]:.1f} MWh")


if __name__ == "__main__":
    main()
