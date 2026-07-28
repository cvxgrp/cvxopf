"""Generate the README intertemporal-storage controller comparison."""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from experiments.battery_terminal.greedy_controllers import (
    GreedyConfig,
    naive_control_battery_priority,
    naive_control_dispatchable_priority,
)


EXPERIMENT_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EXPERIMENT_DIR / "results"
OUTPUT_PATH = EXPERIMENT_DIR / "readme_intertemporal_storage.png"
SCENARIO = "high"
TERMINAL_POLICY = "equality"
TERMINAL_TARGET_MWH = 500.0


def main() -> None:
    """Write the curated high-stress, terminal-equality comparison figure."""
    scenario_inputs = pd.read_csv(RESULTS_DIR / "scenario_inputs.csv")
    optimal_trajectories = pd.read_csv(
        RESULTS_DIR / "policy_trajectories.csv"
    )

    inputs = scenario_inputs[
        scenario_inputs["scenario"] == SCENARIO
    ].sort_values("step")
    optimal = optimal_trajectories[
        (optimal_trajectories["scenario"] == SCENARIO)
        & (optimal_trajectories["policy"] == TERMINAL_POLICY)
    ].sort_values("step")
    if len(inputs) != 96 or len(optimal) != 96:
        raise ValueError("Expected one complete 96-hour high-stress window")

    config = GreedyConfig()
    greedy = {
        "dispatchable priority": naive_control_dispatchable_priority(
            inputs["load_mw"].to_numpy(),
            inputs["renewable_available_mw"].to_numpy(),
            config,
        ),
        "battery priority": naive_control_battery_priority(
            inputs["load_mw"].to_numpy(),
            inputs["renewable_available_mw"].to_numpy(),
            config,
        ),
    }
    controllers = {
        "optimal, terminal equality": {
            "soc": [
                float(optimal["initial_soc_mwh"].iloc[0]),
                *optimal["soc_mwh"].tolist(),
            ],
            "battery": optimal["battery_mw"].to_numpy(),
            "dispatchable": optimal["generation_mw"].to_numpy(),
            "shedding": 0.0 * optimal["battery_mw"].to_numpy(),
        },
        **{
            name: {
                "soc": result.soc_mwh,
                "battery": result.battery_mw,
                "dispatchable": result.dispatchable_mw,
                "shedding": result.load_shedding_mw,
            }
            for name, result in greedy.items()
        },
    }
    styles = {
        "optimal, terminal equality": ("black", "-"),
        "dispatchable priority": ("tab:blue", "--"),
        "battery priority": ("tab:orange", ":"),
    }

    figure, axes = plt.subplots(4, 1, figsize=(10.5, 8.5), sharex=True)
    for name, values in controllers.items():
        color, linestyle = styles[name]
        axes[0].plot(
            range(97),
            values["soc"],
            color=color,
            linestyle=linestyle,
            linewidth=1.6,
            label=name,
        )
        for axis, key in zip(
            axes[1:],
            ("battery", "dispatchable", "shedding"),
            strict=True,
        ):
            axis.stairs(
                values[key],
                range(97),
                color=color,
                linestyle=linestyle,
                linewidth=1.3,
                label=name,
            )

    axes[0].axhline(
        TERMINAL_TARGET_MWH,
        color="tab:red",
        linestyle="-.",
        linewidth=1.0,
        label="terminal target",
    )
    axes[0].set_ylabel("SoC\n(MWh)")
    axes[1].set_ylabel("Battery\n(MW)")
    axes[2].set_ylabel("Dispatchable\npower (MW)")
    axes[3].set(xlabel="Dispatch interval", ylabel="Load shedding\n(MW)")
    axes[1].axhline(0.0, color="gray", linewidth=0.6)
    for axis in axes:
        axis.grid(alpha=0.2)
    axes[0].legend(ncols=2, fontsize=8)
    figure.suptitle(
        "High-stress 96-hour window: intertemporal and greedy control"
    )
    figure.tight_layout()
    figure.savefig(OUTPUT_PATH, dpi=180, bbox_inches="tight")
    plt.close(figure)
    print(OUTPUT_PATH)


if __name__ == "__main__":
    main()
