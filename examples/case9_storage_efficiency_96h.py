"""Compare ideal, lithium-ion-like, and deliberately inefficient storage over four days.

Hourly single-node dispatch uses Case9's loads and generator costs, with a
synthetic evening demand peak and solar-rich first/last days surrounding a
cloudy, higher-demand middle period. This is an illustrative profile, not
measured weather or a technology calibration. Network congestion and reactive
power are deliberately omitted so the comparison isolates storage behavior.

All batteries have the same 100 MW rating, 800 MWh capacity, throughput price,
and 50%-to-50% energy boundary. The lithium-ion-like illustration has 95%
efficiency each way (90.25% round trip) and 2% monthly self-discharge. The
stress case has 50% each-way efficiency (25% round trip) and an exaggerated
95% monthly self-discharge, equivalent to about 9.4% loss per day at idle.

Prints whole-horizon economics/energy accounting and daily dispatch summaries.
Material simultaneous operation is reported explicitly, not silently repaired.

Usage:
    uv run examples/case9_storage_efficiency_96h.py

Optional figures (requires the notebook extra):
    uv run --extra notebook examples/case9_storage_efficiency_96h.py --plot
    uv run --extra notebook examples/case9_storage_efficiency_96h.py --save-plot battery.png

The figure uses the same solves as the printed summaries. Charging is shown
positive and discharging negative; SoC includes the initial boundary.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from cvxopf import (
    NondispatchableUnit, SimultaneousOperationError, StorageUnitIdeal,
    StorageUnitLossy, build_opf_multistep, extract_results,
)
from cvxopf.testcases import case9


def plot_trajectories(demand, solar, trajectories, initial_soc, delta):
    """Plot retained results, without constructing or solving another model."""
    import matplotlib.pyplot as plt

    edges = np.arange(len(demand) + 1) * delta
    fig, axes = plt.subplots(5, 1, figsize=(11, 11), sharex=True,
                             layout="constrained")
    axes[0].stairs(demand, edges, label="Demand", color="#0072B2")
    axes[0].stairs(solar, edges, label="Available solar", color="#E69F00")
    axes[0].set_ylabel("Input power (MW)")
    axes[0].set_title("Four-day storage comparison — synthetic inputs, single-node dispatch")
    axes[0].legend(loc="upper right", ncols=2)
    colors = ("#0072B2", "#D55E00", "#009E73")
    for ax, color, (name, trajectory) in zip(axes[2:], colors, trajectories.items()):
        if trajectory is None:
            ax.set_title(f"{name}: no usable solution", loc="left")
            continue
        charge, discharge, soc, split_failed, status = trajectory
        label = name
        if split_failed:
            label += " — simultaneous operation detected"
        if status == "optimal_inaccurate":
            label += " — optimal_inaccurate"
        axes[1].plot(edges, np.r_[initial_soc, soc], color=color, label=label)
        ax.stairs(charge, edges, color=color, label="Charge (+)")
        ax.stairs(-discharge, edges, color=color, linestyle="--", label="Discharge (−)")
        ax.axhline(0, color="0.5", linewidth=0.6)
        ax.set_title(label, loc="left", fontsize=10)
        ax.set_ylabel("Power (MW)")
        ax.legend(loc="upper right", ncols=2)
    axes[1].set_ylabel("Stored energy (MWh)")
    axes[1].legend(loc="upper right", fontsize=8)
    axes[-1].set_xlabel("Elapsed time (hours)")
    for ax in axes:
        ax.set_xlim(edges[0], edges[-1])
        ax.set_xticks(np.arange(0, edges[-1] + 1, 12))
        ax.grid(axis="y", alpha=0.2)
        for boundary in np.arange(24, edges[-1], 24):
            ax.axvline(boundary, color="0.7", linestyle=":", linewidth=0.7)
    return fig


def main(*, plot=False, save_plot=None):
    if plot or save_plot is not None:
        import matplotlib
        if not plot:
            matplotlib.use("Agg")
        import matplotlib.pyplot as plt

    case = case9()
    T, delta = 96, 1.0
    hour = np.arange(T) % 24
    day = np.arange(T) // 24
    demand_scale = (0.65 + 0.35 * np.exp(-((hour - 19) / 4)**2))
    demand_scale *= np.array([1.0, 1.1, 1.2, 0.95])[day]
    solar = 450 * np.maximum(0, np.sin(np.pi * (hour - 6) / 12))
    solar *= np.array([1.0, 0.25, 0.15, 0.9])[day]
    load = pd.DataFrame(demand_scale[:, None] * case["bus"][:, 2])
    renewable = NondispatchableUnit(
        bus=5, apparent_power_rating=450, p_available=0, device_id="solar",
    )
    common = dict(bus=5, apparent_power_rating=100, capacity=800,
                  initial_soc=400, terminal_soc=400,
                  terminal_constraint="equality", aging_weight=0.01,
                  device_id="battery")
    fleets = {
        "Ideal": dict(storage=[StorageUnitIdeal(**common)]),
        "Li-ion-like": dict(lossy_storage=[StorageUnitLossy(
            **common, charge_efficiency=0.95, discharge_efficiency=0.95,
            self_discharge_per_month=0.02,
        )]),
        "Inefficient": dict(lossy_storage=[StorageUnitLossy(
            **common, charge_efficiency=0.5, discharge_efficiency=0.5,
            self_discharge_per_month=0.95,
        )]),
    }
    print("96-hour single-node dispatch; synthetic solar/cloudy-day sequence")
    print("100 MW / 800 MWh battery; initial and final energy: 400 MWh")
    print("Each-way efficiencies: ideal 100%, Li-ion-like 95%, inefficient 50%")
    print("Monthly self-discharge: 0%, 2%, 95% (last is a stress case)")
    summaries = []
    trajectories = {}
    for name, fleet in fleets.items():
        trajectories[name] = None
        build = build_opf_multistep(
            case, load, T=T, delta=delta, formulation="singlenode_dc",
            nondispatchable=[renewable], df_nd=pd.DataFrame({"solar": solar}),
            **fleet,
        )
        build.solve()
        print(f"\n{name}: {build.prob.status}")
        if build.prob.status not in ("optimal", "optimal_inaccurate"):
            print("No usable solution; comparison not reported for this case.")
            continue
        split_failed = False
        try:
            result = extract_results(build)
        except SimultaneousOperationError as error:
            print(f"WARNING: {error}")
            result = error.results
            split_failed = True
        if "lossy_storage" in fleet:
            charge, discharge, soc = (result[k][:, 0] for k in
                                      ("ls_p_ch", "ls_p_dis", "ls_soc"))
            conversion = result["ls_conversion_loss"][:, 0]
            leakage = result["ls_self_discharge"][:, 0]
            split = result["ls_split"].max()
        else:
            power, soc = result["b"][:, 0], result["soc"][:, 0]
            charge, discharge = np.maximum(-power, 0), np.maximum(power, 0)
            conversion, leakage, split = np.zeros(T), np.zeros(T), 0.0
        trajectories[name] = (charge, discharge, soc, split_failed, result["status"])
        balance = (soc[-1] - common["initial_soc"]
                   - delta * (charge.sum() - discharge.sum())
                   + conversion.sum() + leakage.sum())
        print(f"Energy-accounting residual: {balance:.3e} MWh; max split: {split:.3e} MW")
        print(f"{'day':>3} {'load MWh':>10} {'solar MWh':>10} {'curt MWh':>10} "
              f"{'charge MWh':>11} {'disch MWh':>10} {'end SoC':>10}")
        for d in range(4):
            s = slice(24*d, 24*(d+1))
            print(f"{d+1:3d} {delta*load.iloc[s].to_numpy().sum():10.1f} "
                  f"{delta*solar[s].sum():10.1f} {delta*result['curtailment'][s].sum():10.1f} "
                  f"{delta*charge[s].sum():11.1f} {delta*discharge[s].sum():10.1f} "
                  f"{soc[24*(d+1)-1]:10.1f}")
        summaries.append((name, result["objective"], delta*charge.sum(),
                          delta*discharge.sum(), conversion.sum(), leakage.sum(),
                          delta*result["curtailment"].sum()))
    print("\nWhole-horizon comparison (energy in MWh; objective in cost units)")
    print(f"{'battery':<13} {'objective':>12} {'charged':>10} {'discharged':>11} "
          f"{'conversion':>11} {'self-loss':>10} {'curtailed':>10}")
    for name, objective, *energies in summaries:
        print(f"{name:<13} {objective:12.2f}" + "".join(f"{v:11.1f}" for v in energies))
    print("\nCharging and discharging are grid-side energies. Losses explain their")
    print("difference when start/end SoC match. Curtailment has zero objective cost.")
    if plot or save_plot is not None:
        fig = plot_trajectories(load.sum(axis=1).to_numpy(), solar, trajectories,
                                common["initial_soc"], delta)
        if save_plot is not None:
            destination = Path(save_plot)
            destination.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(destination, dpi=160)
        if plot:
            plt.show()
        plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plot", action="store_true", help="Display the figure interactively.")
    parser.add_argument("--save-plot", type=Path, help="Save the figure without opening a window.")
    args = parser.parse_args()
    main(plot=args.plot, save_plot=args.save_plot)
