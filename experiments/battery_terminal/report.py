# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo>=0.23.13",
#     "matplotlib>=3.10",
#     "numpy>=2.0",
#     "pandas>=2.2",
# ]
# ///

"""Executable report for the battery terminal-policy experiment."""

import marimo

__generated_with = "0.23.13"
app = marimo.App(width="medium")


@app.cell
def _():
    from pathlib import Path

    import marimo as mo
    import matplotlib.pyplot as plt
    import pandas as pd

    return Path, mo, pd, plt


@app.cell
def _(pd):
    from numbers import Integral, Real

    def format_report_table(frame):
        """Format a result table for compact scientific presentation."""

        def format_cell(value):
            if pd.isna(value):
                return ""
            if isinstance(value, Integral):
                return f"{value:,}"
            if isinstance(value, Real):
                magnitude = abs(float(value))
                if magnitude == 0:
                    return "0"
                if magnitude >= 1000:
                    return f"{value:,.1f}"
                if magnitude < 0.001:
                    return f"{value:.2e}"
                return f"{value:.4g}"
            return str(value)

        return frame.map(format_cell)

    return (format_report_table,)


@app.cell
def _(mo):
    mo.md(r"""
    # Battery terminal-policy experiment

    **Executable final report**

    This study examines hard and soft terminal state-of-charge policies,
    their convex value-function interpretation, their localization after
    storage saturation, and their behavior under AC realization and time
    discretization.

    The package's `lossy_dc` formulation is **loss-penalized DC**:

    $$J_{\mathrm{DC}} = J_{\mathrm{generation}} + \sum_{t,e} r_e p_{t,e}^2 + J_{\mathrm{storage}}.$$

    The $r_ep_{t,e}^2$ term is an objective penalty. It is not withdrawn
    from nodal energy balance. AC results separately report physical
    active-power loss from the nonlinear network equations.

    ## Report map and principal conclusions

    1. **Terminal equality value functions.** The sampled
       endpoint-conditioned operating values exhibit the convexity predicted
       by the model; their slopes expose the marginal operating cost of
       retaining terminal energy and changes in the active constraint set.
    2. **Exact versus smooth penalties.** Linear terminal penalties recover
       hard targets above a marginal-value threshold, while quadratic
       penalties produce a smooth and interpretable energy-cost tradeoff.
    3. **Saturation boundaries and terminal locality.** A terminal policy
       changes only the final undecoupled storage excursion after the last
       common empty- or full-SoC state, so its relative influence decreases
       as completed history is added to the horizon.
    4. **Endpoint-conditioned optimal substructure.** Fixing the battery
       states at subsection boundaries reproduces the restricted convex
       optimum, and splitting at an internal saturation state gives objective
       additivity to numerical precision.
    5. **Short-horizon AC realization.** Short AC problems can realize
       energy-state boundaries supplied by the long DC solution without
       reproducing its dispatch trajectory. They also preserve the principal
       terminal-locality and saturation geometry; physical loss, voltage
       constraints, reactive allocation, and local optimality explain the
       remaining DC–AC differences.
    6. **Time-resolution invariance.** With stage-cost rates integrated by
       the interval duration, no-policy, hard-equality, and soft-quadratic
       solutions are invariant at common physical times across 1-hour,
       30-minute, and 15-minute grids.

    ## Implication for hierarchical DC–AC control

    Section 5 materially strengthens the project's hierarchical-solve thesis.
    Its endpoint-conditioned AC results validate the central abstraction
    behind Milestone 17: a long-horizon convex layer can communicate battery
    energy states to a short-horizon AC layer without requiring the AC layer
    to reproduce the DC dispatch trajectory. The communicated object is the
    intertemporal energy state; the AC layer remains responsible for a
    network-feasible realization within its shorter window. Section 4
    supplies the complementary convex optimal-substructure result that makes
    those energy-state boundaries meaningful.

    This is strong open-loop evidence, not yet a closed-loop validation of
    indexing, realized-state feedback, replanning, or fallback behavior. A
    companion experiment is therefore planned in
    `experiments/hierarchical_battery_resilience`. Before M17 implementation,
    it will manually orchestrate the existing long-horizon `lossy_dc` and
    short-window AC builders; after implementation, the same frozen protocol
    will serve as an equivalence test for the public M17 API.
    """)
    return


@app.cell
def _(Path, pd):
    report_dir = Path(__file__).resolve().parent
    results_dir = report_dir / "results"
    table_files = {
        "scenario_inputs": "scenario_inputs.csv",
        "trajectories": "policy_trajectories.csv",
        "value": "terminal_value_sweep.csv",
        "weights": "soft_weight_sweep.csv",
        "horizon": "horizon_study.csv",
        "ac": "ac_study.csv",
        "subset": "subset_study.csv",
        "subset_comparison": "subset_comparison.csv",
        "subset_additivity": "subset_additivity.csv",
        "subset_trajectories": "subset_trajectories.csv",
        "resolution": "resolution_study.csv",
        "energy_validation": "resolution_energy_validation.csv",
    }
    missing_files = [
        filename
        for filename in table_files.values()
        if not (results_dir / filename).exists()
    ]
    if missing_files:
        raise FileNotFoundError(
            "Missing reproduced result tables: "
            f"{missing_files}. Run `uv run python -m "
            "experiments.battery_terminal.reproduce` first."
        )
    tables = {
        name: pd.read_csv(results_dir / filename)
        for name, filename in table_files.items()
    }
    return results_dir, tables


@app.cell
def _(mo, results_dir):
    mo.callout(
        mo.md(
            f"""
            **Reproducibility.** This notebook reads generated tables from
            `{results_dir}`. Recreate them from the repository root with:

            ```bash
            uv run python -m experiments.battery_terminal.reproduce
            ```

            Source data and generated tables remain Git-ignored. The results
            directory also contains `metadata.json`, recording the source hash,
            package versions, solvers, and every experiment grid.
            """
        ),
        kind="info",
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Data preparation

    The local source record contains hourly Tracy-node trajectories for load,
    utility solar, wind, and distributed solar, interpreted here in MW.
    Timestamps are retained on their fixed Pacific-standard-time offset. The
    preparation code rejects duplicate, nonmonotone, missing, nonfinite, or
    negative observations; it does not impute gaps. Every study window must be
    contiguous, hourly, and complete in all four channels.

    All source channels receive the same fixed normalization,

    $$s = \frac{315\ \mathrm{MW}}{1{,}138.8\ \mathrm{MW}} \approx 0.2766,$$

    where 315 MW is the case9 base load and 1,138.8 MW is the mean of the
    43,787 observed Tracy load values. Applying one common factor preserves
    renewable-to-load ratios and seasonal magnitude differences. The scenario
    generator can subsequently scale or shift individual aggregate channels,
    but the results in this report use unity secondary scale factors, zero
    load shift, and zero spatial noise.

    The study evaluated complete 96-hour candidates and retained three
    midnight-aligned windows representing renewable surplus, approximate
    energy balance with a high peak deficit, and sustained energy deficit.
    These are the low, moderate, and high windows used below. The labels refer
    to energy conditions over the full window; in particular, the moderate
    window has a higher instantaneous net-load peak than the high window.

    Normalized aggregate trajectories are mapped to the imagined nine-bus
    network using fixed fractions:

    - active load is assigned to buses 5, 7, and 9 in the case9 base-load
      proportions $90:100:125$;
    - reactive load preserves each bus's original $Q_d/P_d$ ratio;
    - utility solar is assigned 20% to bus 1 and 80% to bus 2;
    - wind is assigned 20% to bus 2 and 80% to bus 3; and
    - distributed solar follows the load fractions at buses 5, 7, and 9.

    These allocations preserve each aggregate source trajectory exactly.
    Renewable inverter ratings are fixed at 110% of the sitewise maximum
    availability computed jointly across all three windows. Thus comparisons
    change operating trajectories without silently changing renewable
    equipment ratings. The local source file and generated tables remain
    Git-ignored; `metadata.json` records the source hash and experiment
    configuration needed to identify a reproduced run.
    """)
    return


@app.cell
def _(mo, tables):
    scenario_input_data = tables["scenario_inputs"].copy()
    scenario_viewer_selector = mo.ui.dropdown(
        options=scenario_input_data["scenario"].drop_duplicates().tolist(),
        value="high",
        label="Representative window",
    )
    scenario_viewer_text = mo.md(r"""
    ## Prepared scenario inputs

    Select a representative 96-hour window to inspect the exogenous active
    load and total renewable availability supplied to the OPF models.
    Renewable availability is a physical upper bound, not necessarily the
    dispatched renewable power; the optimizer may curtail it.
    """)
    mo.vstack([scenario_viewer_text, scenario_viewer_selector])
    return scenario_input_data, scenario_viewer_selector


@app.cell
def _(plt, scenario_input_data, scenario_viewer_selector):
    scenario_plot_inputs = scenario_input_data[
        scenario_input_data["scenario"] == scenario_viewer_selector.value
    ].sort_values("step")
    if len(scenario_plot_inputs) != 96:
        raise ValueError("Selected scenario input trace must contain 96 steps")

    scenario_fig, scenario_ax = plt.subplots(figsize=(10.0, 4.6))
    scenario_ax.stairs(
        scenario_plot_inputs["load_mw"],
        range(97),
        label="Total active load",
        color="black",
    )
    scenario_ax.stairs(
        scenario_plot_inputs["renewable_available_mw"],
        range(97),
        label="Total renewable availability",
        color="tab:green",
    )
    scenario_ax.set(
        xlabel="Dispatch interval",
        ylabel="Power (MW)",
        title=f"{scenario_viewer_selector.value} 96-hour input window",
    )
    scenario_ax.grid(alpha=0.2)
    scenario_ax.legend()
    scenario_fig.tight_layout()
    scenario_fig
    return


@app.cell
def _(mo, scenario_input_data, scenario_viewer_selector):
    scenario_summary_inputs = scenario_input_data[
        scenario_input_data["scenario"] == scenario_viewer_selector.value
    ]
    load_energy = scenario_summary_inputs["load_mw"].sum()
    renewable_energy = scenario_summary_inputs[
        "renewable_available_mw"
    ].sum()
    balance = renewable_energy - load_energy
    mo.md(
        f"""
        Over the selected window, total load energy is
        **{load_energy:,.1f} MWh**, available renewable energy is
        **{renewable_energy:,.1f} MWh**, and renewable-minus-load energy is
        **{balance:,.1f} MWh**. Positive balance indicates aggregate renewable
        surplus over the full window; it does not guarantee feasibility at
        every hour or location.
        """
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Network and controller specification

    Every prepared input window is applied to the same imagined case9 network
    centered on Tracy, California. The fixed device fleet contains 350 MW of
    dispatchable generation and one ideal battery at bus 7 rated
    150 MVA / 1,000 MWh and initialized at 500 MWh. The spatial allocations
    and jointly sized renewable inverters defined above are unchanged across
    all scenario and terminal-policy comparisons.

    The primary terminal target is 500 MWh. The nominal soft weights are
    25 objective units/MWh for linear deviation and 0.05 objective
    units/MWh² for quadratic deviation.
    """)
    return


@app.cell
def _(tables):
    value_data = tables["value"].copy()
    weight_data = tables["weights"].copy()
    trajectory_data = tables["trajectories"].copy()
    horizon_data = tables["horizon"].copy()
    ac_data = tables["ac"].copy()
    subset_data = tables["subset"].copy()
    subset_trajectory_data = tables["subset_trajectories"].copy()
    subset_comparison_data = tables["subset_comparison"].copy()
    subset_additivity_data = tables["subset_additivity"].copy()
    resolution_data = tables["resolution"].copy()
    energy_validation_data = tables["energy_validation"].copy()
    return (
        ac_data,
        energy_validation_data,
        horizon_data,
        resolution_data,
        subset_additivity_data,
        subset_comparison_data,
        subset_data,
        subset_trajectory_data,
        trajectory_data,
        value_data,
        weight_data,
    )


@app.cell
def _(mo):
    mo.md(r"""
    ## 1. Terminal equality value functions

    Define the endpoint-conditioned operating value

    $$V(q_T) = \min\{J_{\mathrm{stage}}:\ q_T\ \text{is fixed}\}.$$

    In other words, $V(q_T)$ is the minimum operating cost over the horizon
    when the battery is required to finish with exactly $q_T$ MWh.

    Because terminal SoC enters as an affine right-hand side in the convex
    DC problem, $V$ is convex. All targets from 0 to 1,000 MWh were
    feasible in all three representative windows.
    """)
    return


@app.cell
def _(plt, value_data):
    value_fig, value_ax = plt.subplots(figsize=(8.5, 4.8))
    for value_scenario, value_group in value_data.groupby("scenario"):
        ordered_value = value_group.sort_values("target_mwh")
        baseline_value = ordered_value["objective"].iloc[0]
        value_ax.plot(
            ordered_value["target_mwh"],
            ordered_value["objective"] - baseline_value,
            marker="o",
            markersize=3,
            label=value_scenario,
        )
    value_ax.set(
        xlabel="Terminal SoC target (MWh)",
        ylabel=r"$V(q_T)-V(0)$ (objective units)",
        title="Fixed-terminal operating value",
    )
    value_ax.grid(alpha=0.25)
    value_ax.legend(title="Window")
    value_fig.tight_layout()
    value_fig
    return


@app.cell
def _(mo):
    mo.md(r"""
    The sampled secant slopes are nondecreasing in every window. The low
    window has a sharp active-set transition near 450.5 MWh: below that
    point, additional terminal energy replaces renewable curtailment;
    above it, the battery has already saturated at 1,000 MWh and additional
    dispatchable generation is required later.

    The moderate and high windows assign substantially larger marginal
    operating costs to retaining energy at the terminal boundary because
    doing so forgoes discharge during sustained scarcity.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 2. Exact versus smooth penalties

    The soft terminal-weight response figure shows the optimized terminal SoC
    as the penalty weight changes. Each point is a separate optimization, and
    the dashed line is the 500 MWh target. The two panels therefore show how
    strongly each penalty family enforces the same desired endpoint, not the
    evolution of one controller through time.
    """)
    return


@app.cell
def _(plt, weight_data):
    weight_fig, weight_axes = plt.subplots(
        1, 2, figsize=(10.5, 4.2), sharey=True
    )
    for weight_axis, weight_kind in zip(
        weight_axes, ("linear", "quadratic"), strict=True
    ):
        kind_data = weight_data[weight_data["cost_kind"] == weight_kind]
        for weight_scenario, weight_group in kind_data.groupby("scenario"):
            ordered_weight = weight_group.sort_values("weight")
            weight_axis.plot(
                ordered_weight["weight"],
                ordered_weight["terminal_soc_mwh"],
                marker="o",
                label=weight_scenario,
            )
        weight_axis.set_xscale("log")
        weight_axis.axhline(500.0, color="black", linestyle="--", linewidth=1)
        weight_axis.set(
            xlabel="Terminal weight",
            title=f"{weight_kind.capitalize()} penalty",
        )
        weight_axis.grid(alpha=0.25)
    weight_axes[0].set_ylabel("Terminal SoC (MWh)")
    weight_axes[1].legend(title="Window")
    weight_fig.suptitle("Soft terminal-weight response")
    weight_fig.tight_layout()
    weight_fig
    return


@app.cell
def _(mo):
    mo.md(r"""
    The linear curves move in discrete active-set regimes and then meet the
    target exactly: linear deviation is an exact penalty once its weight
    exceeds the relevant left marginal value of $V$. The tested thresholds
    were bracketed by 1–5 for the low window, 15–17.5 for high, and 17.5–20
    for moderate.

    The quadratic curves instead approach the target smoothly but do not
    attain it at finite weight when $V$ has positive marginal cost at the
    target. Their different response rates across windows reflect the
    different slopes of the corresponding operating value functions. The
    first-order balance is

    $$V'(q_T) + 2w(q_T-q_{\mathrm{target}})=0.$$
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 3. Saturation boundaries and terminal locality

    With storage as the only intertemporal device, fixing a state
    $q_\tau\in\{0,E\}$ separates past and future:

    $$V_{0,T}(q_0) = V_{0,\tau}(q_0,q_\tau) + V_{\tau,T}(q_\tau;\text{terminal policy}).$$

    The high-window trajectory figure compares no terminal policy, the
    nominal quadratic penalty, and hard equality over the same 96-hour
    horizon. The upper panel shows post-step SoC; the lower panel shows
    battery injection, with positive power denoting discharge. Agreement
    among curves identifies history that is unaffected by the terminal
    policy, while their final separation identifies the terminally coupled
    suffix.
    """)
    return


@app.cell
def _(plt, trajectory_data):
    shown_policies = ("none", "quadratic", "equality")
    high_trajectories = trajectory_data[
        (trajectory_data["scenario"] == "high")
        & trajectory_data["policy"].isin(shown_policies)
    ]
    trajectory_fig, trajectory_axes = plt.subplots(
        2, 1, figsize=(9.5, 6.3), sharex=True
    )
    for trajectory_policy in shown_policies:
        policy_trajectory = high_trajectories[
            high_trajectories["policy"] == trajectory_policy
        ].sort_values("step")
        trajectory_axes[0].plot(
            policy_trajectory["step"] + 1,
            policy_trajectory["soc_mwh"],
            label=trajectory_policy,
        )
        trajectory_axes[1].plot(
            policy_trajectory["step"],
            policy_trajectory["battery_mw"],
            label=trajectory_policy,
        )
    trajectory_axes[0].axhline(0, color="black", linewidth=0.8)
    trajectory_axes[0].axhline(1000, color="black", linewidth=0.8)
    trajectory_axes[0].set_ylabel("Post-step SoC (MWh)")
    trajectory_axes[1].axhline(0, color="black", linewidth=0.8)
    trajectory_axes[1].set(
        xlabel="Dispatch step",
        ylabel="Battery injection (MW)",
    )
    trajectory_axes[0].legend(title="Policy", ncols=3)
    trajectory_fig.suptitle("High-window terminal-policy trajectories")
    trajectory_fig.tight_layout()
    trajectory_fig
    return


@app.cell
def _(mo):
    mo.md(r"""
    The three high-window trajectories coincide through empty-SoC state 82
    and differ only over the final 14 dispatch steps. The common empty state
    fixes the energy passed from the shared prefix into the final excursion,
    after which the terminal policy changes charging and discharging choices.

    Pointwise agreement before a later common boundary can fail when an
    earlier endpoint-conditioned block has multiple optima. Objective
    additivity and fixed-endpoint optimality, rather than selection of one
    pointwise trajectory, are the invariant statements.

    The next figure repeats the analysis over nested horizons. It reports the
    final policy-sensitive excursion as a percentage of total horizon length
    for the equality and quadratic policies.
    """)
    return


@app.cell
def _(horizon_data, plt):
    horizon_fig, horizon_ax = plt.subplots(figsize=(8.5, 4.6))
    active_horizon = horizon_data[
        horizon_data["policy"].isin(("equality", "quadratic"))
    ]
    for horizon_key, horizon_group in active_horizon.groupby(
        ["scenario", "policy"]
    ):
        horizon_scenario, horizon_policy = horizon_key
        ordered_horizon = horizon_group.sort_values("horizon_steps")
        horizon_ax.plot(
            ordered_horizon["horizon_steps"],
            100 * ordered_horizon["final_excursion_fraction"],
            marker="o",
            label=f"{horizon_scenario}: {horizon_policy}",
        )
    horizon_ax.set(
        xlabel="Horizon length (hours)",
        ylabel="Final excursion (% of horizon)",
        title="Terminal excursion fraction under nested horizons",
    )
    horizon_ax.grid(alpha=0.25)
    horizon_ax.legend(fontsize=8, ncols=2)
    horizon_fig.tight_layout()
    horizon_fig
    return


@app.cell
def _(mo):
    mo.md(r"""
    Within each feasible scenario-policy pair, the final excursion duration is
    constant as earlier history is added: 6 steps in the low and moderate
    windows and 14 steps in the high window once the horizon is at least
    24 hours. Its fraction therefore decays with horizon length; for the high
    window it falls from 58.3% at 24 hours to 14.6% at 96 hours. The missing
    moderate 24-hour points correspond to infeasible problems, not omitted
    successful solves.

    This is the sense in which terminal-policy importance decreases with
    horizon length: the policy still controls the same final coupled
    subproblem, but that subproblem occupies a smaller fraction of the full
    plan.
    """)
    return


@app.cell
def _(mo, trajectory_data):
    available_windows = trajectory_data["scenario"].drop_duplicates().tolist()
    available_policies = trajectory_data["policy"].drop_duplicates().tolist()
    window_selector = mo.ui.dropdown(
        options=available_windows,
        value="high",
        label="96-hour window",
    )
    policy_selector = mo.ui.dropdown(
        options=available_policies,
        value="equality",
        label="Terminal policy",
    )
    trace_controls_text = mo.md(r"""
    ### 96-hour outer-layer trace explorer

    Select any of the three representative windows and any terminal policy
    from the complete loss-penalized DC sweep. Every selection represents a
    full 96-hour solve. The figure reports post-step battery SoC, battery
    injection, and aggregate dispatchable-generator power.
    """)
    mo.vstack(
        [
            trace_controls_text,
            mo.hstack([window_selector, policy_selector]),
        ]
    )
    return policy_selector, window_selector


@app.cell
def _(
    plt,
    policy_selector,
    trajectory_data,
    window_selector,
):
    selected_trace = trajectory_data[
        (trajectory_data["scenario"] == window_selector.value)
        & (trajectory_data["policy"] == policy_selector.value)
    ].sort_values("step")
    if len(selected_trace) != 96:
        raise ValueError("Selected outer-layer trace must contain 96 steps")

    outer_trace_fig, outer_trace_axes = plt.subplots(
        3,
        1,
        figsize=(10.0, 7.2),
        sharex=True,
    )
    outer_state_axis = [0, *(selected_trace["step"] + 1).tolist()]
    outer_state_values = [
        float(selected_trace["initial_soc_mwh"].iloc[0]),
        *selected_trace["soc_mwh"].tolist(),
    ]
    outer_trace_axes[0].plot(
        outer_state_axis,
        outer_state_values,
        color="tab:blue",
    )
    outer_trace_axes[1].stairs(
        selected_trace["battery_mw"],
        range(97),
        color="tab:orange",
    )
    outer_trace_axes[2].stairs(
        selected_trace["generation_mw"],
        range(97),
        color="tab:green",
    )
    outer_trace_axes[0].set_ylabel("SoC (MWh)")
    outer_trace_axes[1].set_ylabel("Battery (MW)")
    outer_trace_axes[2].set(
        xlabel="Dispatch interval",
        ylabel="Dispatchable\npower (MW)",
    )
    outer_trace_axes[0].axhline(0, color="black", linewidth=0.7)
    outer_trace_axes[0].axhline(1000, color="black", linewidth=0.7)
    outer_trace_axes[1].axhline(0, color="black", linewidth=0.7)
    for outer_axis in outer_trace_axes:
        outer_axis.grid(alpha=0.2)
    outer_trace_fig.suptitle(
        f"{window_selector.value} window — "
        f"{policy_selector.value.replace('_', ' ')} policy"
    )
    outer_trace_fig.tight_layout()
    outer_trace_fig
    return


@app.cell
def _(mo, policy_selector, window_selector):
    mo.md(
        f"""
        The selected **{window_selector.value}** trace under the
        **{policy_selector.value.replace("_", " ")}** policy is one complete
        outer-layer plan. Positive battery power denotes discharge and
        negative power denotes charge. Dispatchable power is summed over all
        three generators; battery power and SoC are both individual and
        network-aggregate quantities because this experiment has one storage
        device.

        Comparing the panels shows when generator dispatch is displaced by
        battery discharge, when charging increases contemporaneous supply
        requirements, and how those power decisions accumulate into the
        battery energy trajectory.

        A separate
        [greedy-controller comparison](greedy_controller_comparison.py)
        compares this optimal no-terminal-policy trajectory with causal
        dispatchable-priority and battery-priority baselines. That notebook
        keeps the behavioral benchmark distinct from the terminal-policy study
        and ends with overlaid controller traces.
        """
    )
    return


@app.cell
def _(mo, subset_additivity_data, subset_comparison_data):
    subset_gap = float(
        subset_additivity_data.loc[
            subset_additivity_data["component"] == "whole_crossing_window",
            "objective",
        ].iloc[0]
        - subset_additivity_data.loc[
            subset_additivity_data["component"] == "sum_of_halves",
            "objective",
        ].iloc[0]
    )
    crossing_objective_gap = float(
        subset_comparison_data.loc[
            subset_comparison_data["case"] == "crosses_boundary",
            "dc_objective_gap",
        ].iloc[0]
    )
    no_boundary_objective_gap = float(
        subset_comparison_data.loc[
            subset_comparison_data["case"] == "no_boundary",
            "dc_objective_gap",
        ].iloc[0]
    )
    mo.md(
        rf"""
        ## 4. Endpoint-conditioned optimal substructure

        Two equal 18-step subsections were extracted from the 96-hour high
        equality solution:

        - states $[32,50]$, crossing the full-SoC state 41;
        - states $[60,78]$, with no internal saturation state.

        After inheriting both endpoint SoCs, independently solved DC objectives
        differ from the restricted long-solution objectives by only
        {crossing_objective_gap:.2e} and
        {no_boundary_objective_gap:.2e}.

        Splitting the crossing case at state 41 gives

        $$V_{{32,50}} = V_{{32,41}} + V_{{41,50}}$$

        to an absolute numerical difference of {subset_gap:.2e}. This
        numerically verifies additivity across the decoupling boundary.
        """
    )
    return


@app.cell
def _(format_report_table, mo, subset_comparison_data):
    subset_display = subset_comparison_data[
        [
            "case",
            "dc_soc_max_abs_difference_from_long_mwh",
            "dc_battery_max_abs_difference_from_long_mw",
            "dc_objective_gap",
        ]
    ].copy()
    mo.ui.table(
        format_report_table(subset_display),
        label="Endpoint-fixed DC reconstruction",
        selection=None,
    )
    return


@app.cell
def _(
    ac_data,
    format_report_table,
    mo,
    plt,
    subset_comparison_data,
    subset_data,
    subset_trajectory_data,
):
    subset_ac = subset_data.loc[
        subset_data["formulation"] == "ac",
        [
            "case",
            "status",
            "initial_soc_mwh",
            "terminal_soc_mwh",
            "soc_max_mwh",
            "physical_network_loss_mwh",
            "max_constraint_violation",
        ],
    ].merge(
        subset_comparison_data[
            [
                "case",
                "ac_dc_soc_rmse_mwh",
                "ac_dc_battery_rmse_mw",
                "ac_dc_generation_rmse_mw",
            ]
        ],
        on="case",
        validate="one_to_one",
    )
    ac_display = ac_data[
        [
            "horizon_steps",
            "policy",
            "status",
            "terminal_soc_mwh",
            "physical_network_loss_mwh",
            "voltage_min_pu",
            "voltage_max_pu",
            "renewable_q_min_mvar",
            "renewable_q_max_mvar",
            "max_constraint_violation",
        ]
    ].copy()
    ac_handoff_text = mo.md(
        r"""
        ## 5. Short-horizon AC realization

        Section 4 establishes endpoint-conditioned optimal substructure in
        the convex DC model. The first AC test asks a different question:
        can a short nonlinear-network problem realize energy boundaries
        supplied by the long DC solution without being required to follow
        the DC dispatch?

        Each 18-step AC subsection inherited its initial and terminal SoCs
        from the 96-hour DC solution; no intermediate DC power trajectory was
        imposed. The boundary-crossing AC solution reaches full SoC at the
        same internal state as DC, while the no-boundary AC solution remains
        strictly inside the energy limits. The nonzero RMSE values below are
        DC–AC trajectory differences, not reconstruction errors: the AC layer
        is solving a different network model subject to the communicated
        energy boundaries.

        This is the result that supports the M17 handoff abstraction. It shows
        that battery energy states can serve as the inter-layer contract while
        short-horizon AC dispatch remains free to satisfy its own physical
        network equations.
        """
    )
    subset_ac_table = mo.ui.table(
        format_report_table(subset_ac),
        label="AC realization of DC-derived energy boundaries",
        selection=None,
    )
    battery_ids = subset_trajectory_data[
        ["battery_index", "battery_bus"]
    ].drop_duplicates()
    if len(battery_ids) != 1:
        raise ValueError(
            "Section 5 trace layout expects the report's one-battery system"
        )
    battery_bus = int(battery_ids["battery_bus"].iloc[0])
    trace_cases = ("crosses_boundary", "no_boundary")
    trace_formulations = (
        ("lossy_dc", "loss-penalized DC"),
        ("ac", "AC"),
    )
    trace_fig, trace_axes = plt.subplots(
        2,
        2,
        figsize=(10.5, 6.2),
        sharex="col",
        sharey="row",
    )
    for trace_column, trace_case in enumerate(trace_cases):
        for trace_formulation, trace_label in trace_formulations:
            trace = subset_trajectory_data[
                (subset_trajectory_data["case"] == trace_case)
                & (
                    subset_trajectory_data["formulation"]
                    == trace_formulation
                )
            ].sort_values("local_step")
            state_axis = [0, *trace["post_step_state"].tolist()]
            state_values = [
                float(trace["initial_soc_mwh"].iloc[0]),
                *trace["soc_mwh"].tolist(),
            ]
            trace_axes[0, trace_column].plot(
                state_axis,
                state_values,
                marker="o",
                markersize=3,
                label=trace_label,
            )
            trace_axes[1, trace_column].stairs(
                trace["battery_mw"],
                range(len(trace) + 1),
                label=trace_label,
            )
        trace_axes[0, trace_column].set_title(
            trace_case.replace("_", " ")
        )
        trace_axes[0, trace_column].axhline(
            0,
            color="black",
            linewidth=0.7,
        )
        trace_axes[0, trace_column].axhline(
            1000,
            color="black",
            linewidth=0.7,
        )
        trace_axes[1, trace_column].axhline(
            0,
            color="black",
            linewidth=0.7,
        )
        trace_axes[1, trace_column].set_xlabel(
            "Local state / dispatch interval"
        )
        for trace_row in range(2):
            trace_axes[trace_row, trace_column].grid(alpha=0.2)
    trace_axes[0, 0].set_ylabel("SoC (MWh)")
    trace_axes[1, 0].set_ylabel("Battery injection (MW)")
    trace_axes[0, 0].legend()
    trace_fig.suptitle(
        f"Bus {battery_bus} battery: individual and network-aggregate traces"
    )
    trace_fig.tight_layout()
    trace_explanation = mo.md(
        rf"""
        The upper panels include the inherited initial state and every
        post-step SoC; the lower panels show interval power, with positive
        injection denoting discharge and negative injection denoting charge.
        This experiment contains one battery, at bus {battery_bus}, so its
        individual trace is also the network-wide aggregate trace. A separate
        aggregate curve would be identical and is intentionally not
        duplicated.

        Both formulations satisfy the same endpoint energy states while
        choosing visibly different interior power schedules. In the crossing
        window they also agree on the physically important full-SoC
        saturation event. This makes the hierarchical point directly visible:
        endpoint energy states coordinate the layers without fixing the AC
        dispatch trajectory.
        """
    )
    staged_ac_text = mo.md(
        r"""
        ### Staged AC terminal-policy study

        All staged 12- and 24-hour cold-start IPOPT solves returned usable
        local solutions. At 24 hours, AC reproduces the DC locality geometry
        exactly: the active policies share empty state 10 with the no-policy
        trajectory, diverge at state 11, and affect a 14-step suffix.

        AC physical loss is approximately 40 MWh over 12 hours and 94–96 MWh
        over 24 hours. These quantities are not comparable as energy to the DC
        $r p^2$ objective penalty.
        """
    )
    ac_section_table = mo.ui.table(
        format_report_table(ac_display),
        selection=None,
    )
    mo.vstack(
        [
            ac_handoff_text,
            subset_ac_table,
            trace_fig,
            trace_explanation,
            staged_ac_text,
            ac_section_table,
        ]
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    The AC solutions reach the 1.10 p.u. voltage ceiling and at least one
    renewable apparent-power boundary. Some terminal-policy solutions
    select several hundred MVAr of renewable reactive dispatch. The
    separate `reactive_support_tiebreaker` experiment is designed to
    distinguish necessary support from unpriced reactive nonuniqueness.

    AC branch thermal limits remain unavailable, and IPOPT solutions are
    local rather than globally certified.
    """)
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## 6. Time-resolution invariance

    The final figure represents the same 24-hour high-window power trajectory
    at 1-hour, 30-minute, and 15-minute resolution. It plots optimized
    terminal SoC against step duration for no terminal policy, hard equality,
    and the nominal quadratic penalty. The horizontal direction therefore
    changes numerical discretization, not the physical horizon or source
    energy.
    """)
    return


@app.cell
def _(plt, resolution_data):
    resolution_fig, resolution_ax = plt.subplots(figsize=(8.5, 4.7))
    for resolution_policy, resolution_group in resolution_data.groupby(
        "policy"
    ):
        ordered_resolution = resolution_group.sort_values(
            "delta_hours", ascending=False
        )
        resolution_ax.plot(
            ordered_resolution["delta_hours"],
            ordered_resolution["terminal_soc_mwh"],
            marker="o",
            label=resolution_policy,
        )
    resolution_ax.set(
        xlabel=r"Step duration $\Delta$ (hours)",
        ylabel="Terminal SoC (MWh)",
        title="Terminal policy under time-grid refinement",
    )
    resolution_ax.invert_xaxis()
    resolution_ax.grid(alpha=0.25)
    resolution_ax.legend(title="Policy")
    resolution_fig.tight_layout()
    resolution_fig
    return


@app.cell
def _(energy_validation_data, mo):
    maximum_energy_error = float(
        energy_validation_data["maximum_channel_energy_error"].max()
    )
    mo.md(
        rf"""
        Zero-order hold preserves every source and load channel to a maximum
        energy error of {maximum_energy_error:.2e} MWh. No-policy, equality,
        and soft-quadratic dispatch remain invariant at common hourly
        boundaries under the time-integrated objective:

        | Step duration | Terminal SoC |
        |---:|---:|
        | 1 hour | 350.1 MWh |
        | 30 minutes | 350.1 MWh |
        | 15 minutes | 350.1 MWh |

        The implemented objective is

        $$\Delta\sum_t J_t + w(q_T-q_\mathrm{{target}})^2.$$

        Across the three grids, the quadratic-case objective agrees to about
        1e-5 objective units, terminal SoC agrees within 3e-7 MWh, and the
        maximum common-boundary SoC difference is about 2e-5 MWh. The
        corrected discretization therefore preserves the intended tradeoff
        between operating value and the once-per-horizon terminal penalty.
        """
    )
    return


@app.cell
def _(mo):
    mo.md(r"""
    ## Conclusions

    1. The terminal equality value $V(q_T)$ is empirically convex and
       exposes interpretable marginal values and active-set transitions.
    2. Linear penalties become exact above a marginal-value threshold;
       quadratic penalties produce a smooth terminal-energy tradeoff.
    3. Terminal influence localizes to the final undecoupled excursion
       after a common saturation state.
    4. Endpoint-fixed DC subsections reproduce the long-horizon optimum,
       and crossing a saturation state gives numerical objective
       additivity.
    5. Short AC realizations satisfy DC-derived energy boundaries and preserve
       the principal SoC geometry without reproducing DC dispatch; physical
       loss, voltage activity, reactive allocation, and local optimality
       explain the trajectory differences.
    6. Integrating stage-cost rates by $\Delta$ removes the prior numerical
       time-resolution dependence of soft terminal weights.

    ## Open decisions and limitations

    - Complete the separate reactive-support tie-breaker experiment.
    - AC branch thermal limits and load shedding remain future package
      capabilities.
    - Storage siting, sizing, forecast error, and receding-horizon control
      are follow-on studies rather than acceptance requirements here.

    The experiment demonstrates the intended storage terminal-policy behavior
    and verifies the adopted package-level objective-time convention.
    """)
    return


if __name__ == "__main__":
    app.run()
