# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo==0.24.2", "numpy>=2.0", "pandas>=2.2", "plotly>=6.0"]
# ///

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="full")


@app.cell(hide_code=True)
def imports():
    import marimo as mo
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(mo.notebook_location())))
    from e3_results import (
        AGGREGATES,
        CHANNELS,
        LABELS,
        asset_labels,
        load_scenarios,
        plot_panels,
        summary_table,
    )

    return (
        AGGREGATES,
        CHANNELS,
        LABELS,
        Path,
        asset_labels,
        load_scenarios,
        mo,
        plot_panels,
        summary_table,
        sys,
    )


@app.cell(hide_code=True)
def introduction(mo):
    mo.md("""
    # Tracy E3 · matched 24-hour trajectories

    Explore **six scenarios and four formulations**: five original prepared E3
    windows plus the E3+ forced-shedding window, with 24 retained trajectories.
    No solves, retries or artifact edits run here.
    Choose a matched case, then overlay formulations in aggregate and asset panels.
    Colors identify the formulation/solver consistently; legend clicks hide traces.

    Power is held constant over each hourly interval. Battery power is **positive
    for discharge**; SoC includes the initial and terminal boundaries (25 points).
    Times use the source's fixed **UTC−08:00**, not browser daylight-saving time.
    Tiny values are not clipped; zoom to inspect them.

    All six scenarios use the same plots and controls. Saved audit flags and run
    provenance are metadata, not filters on the displayed trajectories.
    AC convergence is not a global-optimality claim. SOCP trajectories describe
    the relaxation, not proof of an AC-realizable trajectory. Net network real
    injection is an accounting quantity; its SOCP value includes relaxation effects.
    The lossy-DC loss proxy is a cost, not a withdrawal in real-power balance.
    Missing reactive/voltage/branch quantities are **unavailable**, never padded with zeros.
    """)
    return


@app.cell(hide_code=True)
def data_cell(Path, load_scenarios, mo, summary_table):
    run_path = Path(mo.notebook_location()) / "results"
    mo.stop(
        not all((run_path / name / "progress.json").exists() for name in ("e3_prepared", "e3_plus")),
        mo.md("Retained E3, E3+ and Stage A artifacts are required. No synthetic or historical-run fallback is used."),
    )
    data = load_scenarios(run_path)
    summaries = summary_table(data)
    capacities = data["tables"]["batteries"].capacity_mwh.to_numpy()
    execution_commits = " · ".join(
        f"{run['directory'].name}: `{run['binding']['context']['commit']}`"
        for run in data["runs"]
    )
    mo.md(
        f"**6 scenarios · 24 trajectories** · {len(data['hashes'])} completion artifact hashes verified. "
        "Successful finalized supervision and saved audit classifications checked; no numerical qualification is rerun. "
        f"Execution provenance: {execution_commits}."
    )
    return capacities, data, summaries


@app.cell(hide_code=True)
def case_controls(LABELS, data, mo):
    case_options = list(data["cases"])
    chosen_case = mo.ui.dropdown(
        case_options, value=case_options[-1], label="Scenario", full_width=True
    )
    chosen_formulations = mo.ui.multiselect(
        {label: key for key, label in LABELS.items()},
        value=list(LABELS.values()),
        label="Overlay formulations / solvers",
        full_width=True,
    )
    clock = mo.ui.dropdown(
        ["Hour within window", "Global input hour", "2021 timestamp (fixed UTC−08:00)"],
        value="Hour within window",
        label="Horizontal axis",
    )
    hour_range = mo.ui.range_slider(
        start=0,
        stop=24,
        step=1,
        value=[0, 24],
        label="Window hours [start, end)",
        show_value=True,
        full_width=True,
    )
    difference = mo.ui.checkbox(
        value=False, label="Show trajectory differences instead of levels"
    )
    baseline = mo.ui.dropdown(
        {label: key for key, label in LABELS.items()},
        value=LABELS["ac"],
        label="Difference reference",
    )
    mo.vstack(
        [
            chosen_case,
            chosen_formulations,
            mo.hstack([clock, difference, baseline]),
            hour_range,
        ]
    )
    return (
        baseline,
        chosen_case,
        chosen_formulations,
        clock,
        difference,
        hour_range,
    )


@app.cell(hide_code=True)
def selection(
    baseline,
    chosen_case,
    chosen_formulations,
    clock,
    data,
    difference,
    hour_range,
    mo,
):
    mo.stop(not chosen_formulations.value, mo.md("Select at least one formulation."))
    interval = tuple(int(v) for v in hour_range.value)
    mo.stop(interval[0] >= interval[1], mo.md("Select a nonempty hourly interval."))
    case = data["cases"][chosen_case.value]
    formulations = chosen_formulations.value
    reference = baseline.value if difference.value else None
    axis_clock = clock.value
    return axis_clock, case, formulations, interval, reference


@app.cell(hide_code=True)
def case_details(case, chosen_case, data, mo):
    scenario_record = next(iter(case.values()))
    arm = scenario_record["arm"]
    scenario_binding = next(
        run["binding"] for run in data["runs"]
        if run["binding"]["context"]["cohort"] == scenario_record["cohort"]
    )
    fixture_stress = scenario_binding["study"].get("fixture", {}).get("stress", {})
    scenario_annotation = (
        f"Counterfactual demand multiplier {fixture_stress['demand_multiplier']:.6g} ×; "
        f"input-only unserved-energy lower bound {fixture_stress['modified']['minimum_ens_mwh']:,.2f} MWh. "
        if fixture_stress.get("forced_shedding") else ""
    )
    initial_total = sum(arm["initial_soc_mwh"])
    terminal_total = sum(arm["terminal_soc_mwh"])
    mo.md(
        f"### {chosen_case.value}\n\n" + scenario_annotation
        + f"Matched fleet and inputs · ρ = {arm['rho']:.4g} · throughput cost = {arm['throughput']:g} · "
        f"fleet SoC target {initial_total:,.2f} → {terminal_total:,.2f} MWh. "
        "AC uses component-first assembly and production cost coordinates; convex formulations use hourly assembly and original economic coordinates."
    )
    return


@app.cell(hide_code=True)
def overview(chosen_case, mo, summaries):
    case_summary = summaries[summaries["Case"] == chosen_case.value].drop(
        columns=["Case", "Archive"]
    )
    mo.vstack(
        [
            mo.md(
                "### Horizon totals and execution\n\nCosts are saved reconstructed physical-dispatch costs, in the study's cost units. "
                "Each objective is formulation-specific; the DC proxy cost exists only for lossy DC. "
                "Served energy is load served integrated over all 24 hours and summed across loads, irrespective of plot zoom. Saved decision/check columns are original run metadata; they do not exclude trajectories. Worker time includes preparation, not just native solve time."
            ),
            mo.ui.table(
                case_summary,
                selection=None,
                show_column_summaries=False,
                show_data_types=False,
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def sensitivity_introduction(Path, mo, sys):
    sys.path.insert(0, str(Path(mo.notebook_location()).parents[1]))
    from experiments.case118_tracy_2021.e3_sensitivity import (
        HORIZONS, PROFILES, condition_scatter, findings_markdown,
        sensitivity_heatmap, sensitivity_tables,
    )
    mo.md("""
    ## Condition-dependent model sensitivity

    **Under which operating conditions, and for which quantities of interest,
    does network-model fidelity materially change the answer?**

    Compare daily totals, hourly fleet trajectories, and individual assets separately.
    AC is the default reference, not an assumed global optimum. All six scenarios
    participate equally. This section reads retained trajectories; it runs no solves.
    Cross-scenario comparisons use full 24-hour horizons, not the trajectory zoom.
    """)
    return (
        HORIZONS,
        PROFILES,
        condition_scatter,
        findings_markdown,
        sensitivity_heatmap,
        sensitivity_tables,
    )


@app.cell(hide_code=True)
def sensitivity_conditions(data, mo, sensitivity_tables):
    ac_sensitivity = sensitivity_tables(data, reference="ac")
    mo.ui.tabs({
        "Input operating conditions": mo.vstack([
            mo.ui.table(ac_sensitivity["conditions"].iloc[:, :6], selection=None, show_column_summaries=False),
            mo.md("Resource deficit means demand exceeds available renewables plus installed conventional capacity before considering storage or network constraints. Renewable surplus means renewables alone exceed demand. Battery withdrawal is initial minus terminal energy. These are input descriptors, not feasibility tests."),
        ]),
        "AC operating context / SOCP tightness": mo.vstack([
            mo.ui.table(ac_sensitivity["conditions"].iloc[:, [0, 6, 7, 8]], selection=None, show_column_summaries=False),
            mo.md("The 99% branch-loading marker describes the AC solution, not an independent input or an acceptance gate. Branch-hours use the larger terminal MVA divided by rateA. AC absorption is physical net network real energy; SOCP rank gaps characterize relaxation freedom, not load-balance errors."),
        ]),
    })
    return (ac_sensitivity,)


@app.cell(hide_code=True)
def sensitivity_findings(ac_sensitivity, findings_markdown, mo):
    mo.md(findings_markdown(ac_sensitivity))
    return


@app.cell(hide_code=True)
def sensitivity_controls(HORIZONS, LABELS, PROFILES, data, mo):
    sensitivity_scenarios = mo.ui.multiselect(list(data["cases"]), value=list(data["cases"]), label="Scenarios to compare", full_width=True)
    sensitivity_models = mo.ui.multiselect({label: key for key, label in LABELS.items()}, value=list(LABELS.values()), label="Models to compare (reference omitted automatically)", full_width=True)
    sensitivity_reference = mo.ui.dropdown({label: key for key, label in LABELS.items()}, value=LABELS["ac"], label="Sensitivity reference")
    sensitivity_quantities = mo.ui.multiselect(list(HORIZONS), value=["Served energy", "Conventional generation", "Renewable curtailment", "Battery throughput"], label="Horizon quantities", full_width=True)
    sensitivity_scale = mo.ui.dropdown({"Difference / reference (%)": "Difference / reference (%)", "Difference / gross demand (%) — energy only": "Difference / gross demand (%)"}, value="Difference / reference (%)", label="Horizon normalization")
    sensitivity_profile = mo.ui.dropdown(list(PROFILES), value="Battery real power", label="Trajectory quantity")
    sensitivity_highlight = mo.ui.slider(start=0, stop=50, step=1, value=5, label="Exploratory horizon highlight (%) — not an acceptance gate", show_value=True)
    mo.vstack([
        mo.md("### Explore sensitivity by condition and quantity"),
        sensitivity_scenarios, sensitivity_models,
        mo.hstack([sensitivity_reference, sensitivity_scale]), sensitivity_quantities,
        mo.hstack([sensitivity_profile, sensitivity_highlight]),
    ])
    return (
        sensitivity_highlight,
        sensitivity_models,
        sensitivity_profile,
        sensitivity_quantities,
        sensitivity_reference,
        sensitivity_scale,
        sensitivity_scenarios,
    )


@app.cell(hide_code=True)
def sensitivity_selection(
    chosen_case,
    data,
    mo,
    sensitivity_models,
    sensitivity_profile,
    sensitivity_quantities,
    sensitivity_reference,
    sensitivity_scenarios,
    sensitivity_tables,
):
    sensitivity_comparisons = [model for model in sensitivity_models.value if model != sensitivity_reference.value]
    mo.stop(not sensitivity_scenarios.value or not sensitivity_comparisons, mo.md("Select scenarios and at least one model other than the reference."))
    sensitivity_all = sensitivity_tables(data, reference=sensitivity_reference.value)
    sensitivity_totals = sensitivity_all["totals"].loc[
        lambda frame: frame["Scenario"].isin(sensitivity_scenarios.value)
        & frame["Model"].isin(sensitivity_comparisons)
        & frame["Quantity"].isin(sensitivity_quantities.value)
    ]
    sensitivity_profiles = sensitivity_all["profiles"].loc[
        lambda frame: frame["Scenario"].isin(sensitivity_scenarios.value)
        & frame["Model"].isin(sensitivity_comparisons)
        & (frame["Quantity"] == sensitivity_profile.value)
    ]
    sensitivity_hours = sensitivity_all["hours"].loc[
        lambda frame: frame["Scenario"].isin(sensitivity_scenarios.value)
        & frame["Model"].isin(sensitivity_comparisons)
    ]
    sensitivity_assets = sensitivity_all["assets"].loc[
        lambda frame: (frame["Scenario"] == chosen_case.value)
        & frame["Model"].isin(sensitivity_comparisons)
        & (frame["Quantity"] == sensitivity_profile.value)
    ].sort_values("Peak / own rating (%)", ascending=False)
    return (
        sensitivity_assets,
        sensitivity_comparisons,
        sensitivity_hours,
        sensitivity_profiles,
        sensitivity_totals,
    )


@app.cell(hide_code=True)
def sensitivity_horizons(
    mo,
    sensitivity_comparisons,
    sensitivity_heatmap,
    sensitivity_highlight,
    sensitivity_quantities,
    sensitivity_scale,
    sensitivity_scenarios,
    sensitivity_totals,
):
    mo.stop(not sensitivity_quantities.value, mo.md("Select horizon quantities for the heatmap."))
    sensitivity_horizon_figure = sensitivity_heatmap(sensitivity_totals, [(name, sensitivity_scale.value) for name in sensitivity_quantities.value], sensitivity_scenarios.value, sensitivity_comparisons)
    sensitivity_highlighted = sensitivity_totals.loc[lambda frame: frame[sensitivity_scale.value].abs() >= sensitivity_highlight.value]
    mo.vstack([
        mo.md("### Horizon-total differences\n\nColor shows magnitude; cell labels retain the signed percentage (model minus reference). Percent-of-reference is undefined below a reference magnitude of 1e-6 in its units; raw differences remain in the table. Percent-of-demand is only defined for energy quantities. Network absorption is physical for AC, lifted for SOCP, and essentially absent from DC balance—not an interchangeable loss estimate."),
        mo.ui.plotly(sensitivity_horizon_figure),
        mo.accordion({
            "Exact values and both normalizations": mo.ui.table(sensitivity_totals, selection=None, show_column_summaries=False),
            "Above the exploratory display threshold": mo.ui.table(sensitivity_highlighted, selection=None, show_column_summaries=False),
        }),
    ])
    return


@app.cell(hide_code=True)
def sensitivity_trajectories(
    mo,
    sensitivity_comparisons,
    sensitivity_heatmap,
    sensitivity_profiles,
    sensitivity_scenarios,
):
    sensitivity_profile_figure = sensitivity_heatmap(sensitivity_profiles.drop(columns=["Quantity"]), [("Fleet peak / total rating", "Fleet peak / total rating (%)"), ("Asset peak / own rating", "Asset peak / own rating (%)")], sensitivity_scenarios.value, sensitivity_comparisons)
    mo.vstack([
        mo.md("### Fleet agreement versus individual-asset agreement\n\nLeft: largest hourly fleet difference divided by total installed rating. Right: largest individual-asset difference divided by that asset's own rating. For loads, ratings mean peak gross demand over the scenario; for generators and inverters they mean installed MW/MVA limits. Differences above 100% can reflect opposite charging/discharging directions, not a limit violation.\n\nThe table also integrates absolute differences before and after fleet aggregation. Their difference measures disagreement hidden by cancellation; it is not an energy-loss or ENS measure."),
        mo.ui.plotly(sensitivity_profile_figure),
        mo.ui.table(sensitivity_profiles, selection=None, show_column_summaries=False),
    ])
    return


@app.cell(hide_code=True)
def sensitivity_hourly_conditions(
    condition_scatter,
    mo,
    sensitivity_hours,
    sensitivity_profile,
):
    sensitivity_condition_figure = condition_scatter(sensitivity_hours, sensitivity_profile.value)
    mo.vstack([
        mo.md("### Hourly association with resource conditions\n\nEach point is one hour of a matched model contrast. Negative resource margin indicates instantaneous shortage before storage/network effects. Colors identify models; symbols identify scenarios. Hover reveals the global input hour and MW difference. These are associations within a small, selected set—not a causal fit or a universal breakpoint."),
        mo.ui.plotly(sensitivity_condition_figure),
    ])
    return


@app.cell(hide_code=True)
def sensitivity_assets(chosen_case, mo, sensitivity_assets):
    mo.vstack([
        mo.md(f"### Which assets change? · {chosen_case.value}\n\nThis drilldown follows the main Scenario selector above and the sensitivity trajectory quantity. Sorted by largest rating-normalized peak difference. Gross absolute-power integrals are battery throughput for battery real power; they are not net energy. Asset-level agreement is not implied by similar total throughput or served energy."),
        mo.ui.table(sensitivity_assets, selection=None, show_column_summaries=False),
        mo.md("### Interpretation limits\n\nTotal dispatch cost is formulation-specific (including the lossy-DC proxy), and forced-shedding cost is dominated by the common interruption penalty. It can hide battery-level differences. AC and SOCP retain reactive/voltage quantities; DC omits them, so this common-variable comparison cannot establish equivalence for voltage or reactive-support decisions. Model selection should be tied to the decision quantity and tolerance, not one global score. No thresholds, historical labels, or solver preparation are changed here."),
    ])
    return


@app.cell(hide_code=True)
def aggregate_controls(AGGREGATES, mo):
    aggregate_selection = mo.ui.multiselect(
        list(AGGREGATES),
        value=[
            "Generation",
            "Battery net power (+ discharge)",
            "Battery SoC (all boundaries)",
            "Load shedding",
            "Curtailment",
            "Net network real injection",
        ],
        label="Aggregate quantities · one panel per quantity",
        full_width=True,
    )
    mo.vstack([mo.md("### Aggregate trajectories"), aggregate_selection])
    return (aggregate_selection,)


@app.cell(hide_code=True)
def aggregate_plot(
    AGGREGATES,
    aggregate_selection,
    axis_clock,
    capacities,
    case,
    formulations,
    interval,
    mo,
    plot_panels,
    reference,
):
    mo.stop(
        not aggregate_selection.value, mo.md("Choose aggregate quantities to plot.")
    )
    aggregate_specs = [
        (name, *AGGREGATES[name], None) for name in aggregate_selection.value
    ]
    aggregate_figure, aggregate_missing = plot_panels(
        case, aggregate_specs, formulations, axis_clock, interval, capacities, reference
    )
    mo.vstack(
        [
            mo.ui.plotly(aggregate_figure),
            mo.md(
                "Unavailable overlays: " + "; ".join(aggregate_missing)
                if aggregate_missing
                else "All selected quantities are available for these overlays."
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def family_controls(CHANNELS, mo):
    family = mo.ui.dropdown(list(CHANNELS), value="Batteries", label="Asset family")
    mo.vstack(
        [
            mo.md(
                "### Individual assets\n\nSelect any assets and quantities. Each asset/quantity gets its own solver-overlay panel."
            ),
            family,
        ]
    )
    return (family,)


@app.cell(hide_code=True)
def asset_controls(CHANNELS, asset_labels, data, family, mo):
    asset_options = asset_labels(data, family.value)
    asset_defaults = list(asset_options)[:3]
    if family.value == "Generators":
        asset_defaults = (
            data["tables"]["generators"].query("pmax_mw > 0").device_id.tolist()[:3]
        )
    assets = mo.ui.multiselect(
        asset_options, value=asset_defaults, label="Assets", full_width=True
    )
    quantity_options = CHANNELS[family.value]
    quantities = mo.ui.multiselect(
        list(quantity_options),
        value=list(quantity_options)[:2],
        label="Asset quantities",
        full_width=True,
    )
    mo.vstack([assets, quantities])
    return asset_options, assets, quantities, quantity_options


@app.cell(hide_code=True)
def asset_plot(
    asset_options,
    assets,
    axis_clock,
    capacities,
    case,
    formulations,
    interval,
    mo,
    plot_panels,
    quantities,
    quantity_options,
    reference,
):
    mo.stop(
        not assets.value or not quantities.value,
        mo.md("Select assets and quantities to plot."),
    )
    selected_asset_names = {index: name for name, index in asset_options.items()}
    asset_specs = [
        (f"{selected_asset_names[index]} · {name}", *quantity_options[name], index)
        for index in assets.value
        for name in quantities.value
    ]
    asset_figure, asset_missing = plot_panels(
        case, asset_specs, formulations, axis_clock, interval, capacities, reference
    )
    mo.vstack(
        [
            mo.ui.plotly(asset_figure),
            mo.md(
                "Unavailable overlays: " + "; ".join(asset_missing)
                if asset_missing
                else "Assets aligned by saved input identity / retained source-row order."
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def reference_tables(data, mo, summaries):
    mo.accordion(
        {
            "All 24 horizon summaries": mo.ui.table(
                summaries, selection=None, show_column_summaries=False
            ),
            "Generators · source rows and capacity": mo.ui.table(
                data["tables"]["generators"], selection=None
            ),
            "Batteries · capacities and ratings": mo.ui.table(
                data["tables"]["batteries"], selection=None
            ),
            "Renewables · types and ratings": mo.ui.table(
                data["tables"]["renewables"], selection=None
            ),
            "Buses · load and placement": mo.ui.table(
                data["tables"]["buses"], selection=None
            ),
            "Evidence / interpretation": mo.md(
                f"Runs: {', '.join(str(run['directory']) for run in data['runs'])}. All retained completion artifacts are hash-checked on load. "
                "All finalized hard-target trajectories from the six scenarios are plotted, including the owner-approved shedding results with saved flags. No target-free diagnostic or historical E3 result is included. "
                "Power columns follow the retained Stage A/source row order; batteries, renewables and loads also have checked stable IDs. "
                "Branch terminal powers are positive into the network from either terminal; their sum is a physical AC quantity "
                "for AC, and a lifted/relaxed quantity for SOCP. DC oriented flows are not AC terminal powers. "
                "Difference plots subtract the chosen reference without renormalization. They compare implemented variables, "
                "not interchangeable feasibility certificates or identical network physics."
            ),
        }
    )
    return


if __name__ == "__main__":
    app.run()
