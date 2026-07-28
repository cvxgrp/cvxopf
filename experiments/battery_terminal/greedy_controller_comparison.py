# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo>=0.23.13",
#     "matplotlib>=3.10",
#     "numpy>=2.0",
#     "pandas>=2.2",
# ]
# ///

"""Greedy causal baselines for the battery-terminal experiment."""

import marimo

__generated_with = "0.23.13"
app = marimo.App(width="medium")


@app.cell
def _():
    from pathlib import Path

    import marimo as mo
    import matplotlib.pyplot as plt
    import pandas as pd

    from greedy_controllers import (
        GreedyConfig,
        naive_control_battery_priority,
        naive_control_dispatchable_priority,
    )

    return (
        GreedyConfig,
        Path,
        mo,
        naive_control_battery_priority,
        naive_control_dispatchable_priority,
        pd,
        plt,
    )


@app.cell
def _(mo):
    mo.md(r"""
    # Optimal and greedy battery-control comparison

    **Standalone executable report**

    This notebook compares a 96-hour loss-penalized DC optimum against two
    causal, forecast-free copper-plate baselines. The optimal reference may use
    no terminal policy, a hard terminal equality, or a quadratic terminal cost:

    - **dispatchable priority:** dispatch available dispatchable generation before
      discharging the battery;
    - **battery priority:** discharge the battery before dispatching utility
      generation.

    Both greedy policies charge from renewable surplus whenever storage power
    and energy capacity permit. Neither reserves energy for future scarcity or
    targets a terminal SoC.

    This is a behavioral comparison, not an objective-value comparison. The
    optimizer has the complete 96-hour trajectory and enforces network
    constraints, generator limits, and the DC flow penalty. The greedy
    controllers see only current aggregate load and renewable availability;
    they omit network location, generator minimums and costs, reactive power,
    and transmission constraints.
    """)
    return


@app.cell
def _(Path, pd):
    comparison_dir = Path(__file__).resolve().parent
    results_dir = comparison_dir / "results"
    scenario_path = results_dir / "scenario_inputs.csv"
    optimal_path = results_dir / "policy_trajectories.csv"
    missing_paths = [
        path for path in (scenario_path, optimal_path) if not path.exists()
    ]
    if missing_paths:
        raise FileNotFoundError(
            f"Missing reproduced tables: {missing_paths}. Run `uv run python "
            "-m experiments.battery_terminal.reproduce` first."
        )
    scenario_inputs = pd.read_csv(scenario_path)
    optimal_trajectories = pd.read_csv(optimal_path)
    return optimal_trajectories, scenario_inputs


@app.cell
def _(GreedyConfig):
    greedy_config = GreedyConfig(
        capacity_mwh=1000.0,
        max_power_mw=150.0,
        initial_soc_mwh=500.0,
        dispatchable_max_mw=350.0,
        delta_hours=1.0,
    )
    return (greedy_config,)


@app.cell
def _(
    greedy_config,
    naive_control_battery_priority,
    naive_control_dispatchable_priority,
    scenario_inputs,
):
    greedy_runs = {}
    for greedy_scenario, greedy_frame in scenario_inputs.groupby(
        "scenario",
        sort=False,
    ):
        ordered_frame = greedy_frame.sort_values("step")
        greedy_runs[(greedy_scenario, "dispatchable priority")] = (
            naive_control_dispatchable_priority(
                ordered_frame["load_mw"].to_numpy(),
                ordered_frame["renewable_available_mw"].to_numpy(),
                greedy_config,
            )
        )
        greedy_runs[(greedy_scenario, "battery priority")] = (
            naive_control_battery_priority(
                ordered_frame["load_mw"].to_numpy(),
                ordered_frame["renewable_available_mw"].to_numpy(),
                greedy_config,
            )
        )
    return (greedy_runs,)


@app.cell
def _(mo, scenario_inputs):
    comparison_selector = mo.ui.dropdown(
        options=scenario_inputs["scenario"].drop_duplicates().tolist(),
        value="high",
        label="Representative window",
    )
    terminal_policy_selector = mo.ui.dropdown(
        options={
            "None": "none",
            "Equality": "equality",
            "Quadratic": "quadratic",
        },
        value="None",
        label="Optimal terminal policy",
    )
    mo.vstack(
        [
            mo.md(r"""
            ## Select a 96-hour comparison

            The same prepared load and renewable-availability trajectories
            drive all three controllers. The terminal-policy control changes
            only the optimal reference. Equality and quadratic cases use a
            fixed 500 MWh target, 50% of aggregate battery capacity; the two
            greedy controllers remain causal and terminal-blind.
            """),
            mo.hstack(
                [comparison_selector, terminal_policy_selector],
                justify="start",
                gap=2,
            ),
        ]
    )
    return comparison_selector, terminal_policy_selector


@app.cell(hide_code=True)
def _(comparison_selector, mo, plt, scenario_inputs):
    selected_inputs = scenario_inputs[
        scenario_inputs["scenario"] == comparison_selector.value
    ].sort_values("step")
    input_fig, input_axes = plt.subplots(
        2,
        1,
        figsize=(11.0, 6.0),
        sharex=True,
    )
    input_axes[0].plot(
        selected_inputs["step"],
        selected_inputs["load_mw"],
        color="black",
        label="load",
    )
    input_axes[0].plot(
        selected_inputs["step"],
        selected_inputs["renewable_available_mw"],
        color="tab:green",
        label="renewable availability",
    )
    input_axes[1].plot(
        selected_inputs["step"],
        selected_inputs["net_load_mw"],
        color="tab:purple",
        label="net load",
    )
    input_axes[1].axhline(0, color="gray", linewidth=0.7)
    input_axes[0].set_ylabel("Power (MW)")
    input_axes[1].set(
        xlabel="Dispatch interval",
        ylabel="Net load (MW)",
    )
    for input_axis in input_axes:
        input_axis.grid(alpha=0.2)
        input_axis.legend()
    input_fig.suptitle(
        f"{comparison_selector.value} window: common controller inputs"
    )
    input_fig.tight_layout()
    mo.vstack(
        [
            mo.md(r"""
            ## Input trajectories

            Load and renewable availability are exogenous and identical for
            every controller. Net load is load minus available renewable
            power; negative net load marks intervals with renewable surplus.
            """),
            input_fig,
        ]
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Common physical limits and balance

    The greedy models use the experiment's actual aggregate limits:

    - battery energy capacity: 1,000 MWh;
    - battery power limit: 150 MW;
    - initial SoC: 500 MWh;
    - aggregate dispatchable maximum: 350 MW; and
    - time step: 1 hour.

    With positive battery power denoting discharge, both enforce

    $$l_t-R_t=u_t+b_t+s_t-c_t,\qquad q_{t+1}=q_t-b_t.$$

    Here $u_t$ is aggregate dispatchable power, $s_t$ is load shedding, and
    $c_t$ is renewable curtailment. Curtailment is recorded as a metric of
    interest but carries zero cost. Load shedding is an explicit greedy
    diagnostic; the compared OPF solves are feasible and contain no shedding
    variable.
    """)
    return


@app.cell
def _(
    comparison_selector,
    greedy_runs,
    optimal_trajectories,
    pd,
    terminal_policy_selector,
):
    selected_scenario = comparison_selector.value
    selected_terminal_policy = terminal_policy_selector.value
    terminal_policy_labels = {
        "none": "no terminal policy",
        "equality": "terminal equality",
        "quadratic": "quadratic terminal cost",
    }
    optimal_controller_name = (
        f"optimal, {terminal_policy_labels[selected_terminal_policy]}"
    )
    optimal_selected = optimal_trajectories[
        (optimal_trajectories["scenario"] == selected_scenario)
        & (optimal_trajectories["policy"] == selected_terminal_policy)
    ].sort_values("step")
    selected_controllers = {
        optimal_controller_name: {
            "soc": [
                float(optimal_selected["initial_soc_mwh"].iloc[0]),
                *optimal_selected["soc_mwh"].tolist(),
            ],
            "battery": optimal_selected["battery_mw"].to_numpy(),
            "dispatchable": optimal_selected["generation_mw"].to_numpy(),
            "curtailment": optimal_selected["curtailment_mw"].to_numpy(),
            "shedding": 0.0 * optimal_selected["battery_mw"].to_numpy(),
        }
    }
    for greedy_name in ("dispatchable priority", "battery priority"):
        greedy_result = greedy_runs[(selected_scenario, greedy_name)]
        selected_controllers[greedy_name] = {
            "soc": greedy_result.soc_mwh,
            "battery": greedy_result.battery_mw,
            "dispatchable": greedy_result.dispatchable_mw,
            "curtailment": greedy_result.curtailment_mw,
            "shedding": greedy_result.load_shedding_mw,
        }

    comparison_rows = []
    for controller_name, controller_values in selected_controllers.items():
        battery_power = controller_values["battery"]
        comparison_rows.append(
            {
                "controller": controller_name,
                "terminal_soc_mwh": controller_values["soc"][-1],
                "dispatchable_energy_mwh": controller_values[
                    "dispatchable"
                ].sum(),
                "curtailment_mwh": controller_values["curtailment"].sum(),
                "load_shedding_mwh": controller_values["shedding"].sum(),
                "charge_throughput_mwh": (
                    -battery_power[battery_power < 0]
                ).sum(),
                "discharge_throughput_mwh": battery_power[
                    battery_power > 0
                ].sum(),
            }
        )
    comparison_summary = pd.DataFrame(comparison_rows)
    return (
        comparison_summary,
        optimal_controller_name,
        selected_controllers,
        selected_scenario,
        selected_terminal_policy,
        terminal_policy_labels,
    )


@app.cell
def _(comparison_summary, mo):
    summary_display = comparison_summary.copy()
    numeric_columns = summary_display.columns.difference(["controller"])
    summary_display[numeric_columns] = summary_display[numeric_columns].map(
        lambda value: f"{value:,.1f}"
    )
    mo.ui.table(
        summary_display,
        label="96-hour controller outcomes",
        selection=None,
    )
    return


@app.cell
def _(mo, selected_scenario, selected_terminal_policy, terminal_policy_labels):
    mo.md(f"""
    The table separates three effects for the **{selected_scenario}**
    window under the optimal **{terminal_policy_labels[selected_terminal_policy]}**
    case: when stored energy is used, how much dispatchable energy is
    consumed, and whether myopic decisions create curtailment or unserved
    energy. Differences in the optimal controller also include network
    feasibility and generator-model effects.

    Renewable curtailment is a **metric of interest**: it is tracked and
    reported, but it has zero objective weight. This is important here.
    Assigning an artificial curtailment cost would change the optimizer's
    timing and storage choices, obscuring the comparison between terminal
    policies and greedy control. Curtailment remains physically informative
    without being treated as an independently undesirable quantity in the
    optimization.

    ## Overlaid controller traces

    The final figure overlays all three controllers. SoC is plotted at
    state boundaries, including the inherited initial state; the four
    power quantities are interval values. The overlays make timing
    differences visible even when aggregate energy totals are similar.
    """)
    return


@app.cell
def _(
    optimal_controller_name,
    plt,
    selected_controllers,
    selected_scenario,
    selected_terminal_policy,
):
    overlay_fig, overlay_axes = plt.subplots(
        5,
        1,
        figsize=(11.0, 11.5),
        sharex=True,
    )
    controller_styles = {
        optimal_controller_name: ("black", "-"),
        "dispatchable priority": ("tab:blue", "--"),
        "battery priority": ("tab:orange", ":"),
    }
    for overlay_name, overlay_values in selected_controllers.items():
        overlay_color, overlay_style = controller_styles[overlay_name]
        overlay_axes[0].plot(
            range(97),
            overlay_values["soc"],
            color=overlay_color,
            linestyle=overlay_style,
            label=overlay_name,
        )
        for overlay_axis, overlay_key in zip(
            overlay_axes[1:],
            ("battery", "dispatchable", "curtailment", "shedding"),
            strict=True,
        ):
            overlay_axis.stairs(
                overlay_values[overlay_key],
                range(97),
                color=overlay_color,
                linestyle=overlay_style,
                label=overlay_name,
            )
    overlay_axes[0].set_ylabel("SoC (MWh)")
    overlay_axes[1].set_ylabel("Battery (MW)")
    overlay_axes[2].set_ylabel("Dispatchable\npower (MW)")
    overlay_axes[3].set_ylabel("Curtailment\n(MW)")
    overlay_axes[4].set(
        xlabel="Dispatch interval",
        ylabel="Load shedding\n(MW)",
    )
    overlay_axes[0].axhline(0, color="gray", linewidth=0.6)
    overlay_axes[0].axhline(1000, color="gray", linewidth=0.6)
    if selected_terminal_policy != "none":
        overlay_axes[0].axhline(
            500,
            color="tab:red",
            linestyle="--",
            linewidth=0.9,
            label="terminal target",
        )
    overlay_axes[1].axhline(0, color="gray", linewidth=0.6)
    for overlay_axis in overlay_axes:
        overlay_axis.grid(alpha=0.2)
    overlay_axes[0].legend(ncols=2, fontsize=8)
    overlay_fig.suptitle(
        f"{selected_scenario} window: optimal and greedy control"
    )
    overlay_fig.tight_layout()
    overlay_fig
    return


if __name__ == "__main__":
    app.run()
