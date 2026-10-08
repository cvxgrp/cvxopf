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
        load_results,
        plot_panels,
        summary_table,
    )

    return (
        AGGREGATES,
        CHANNELS,
        LABELS,
        Path,
        asset_labels,
        load_results,
        mo,
        plot_panels,
        summary_table,
    )


@app.cell(hide_code=True)
def introduction(mo):
    mo.md("""
    # Tracy E3 · matched 24-hour trajectories

    Explore the **fresh prepared cohort only**: five cases, four formulations,
    20 accepted hard-target arms. No solves, retries or artifact edits run here.
    Choose a matched case, then overlay formulations in aggregate and asset panels.
    Colors identify the formulation/solver consistently; legend clicks hide traces.

    Power is held constant over each hourly interval. Battery power is **positive
    for discharge**; SoC includes the initial and terminal boundaries (25 points).
    Times use the source's fixed **UTC−08:00**, not browser daylight-saving time.
    Tiny values are not clipped; zoom to inspect them.

    AC acceptance is not a global-optimality claim. SOCP acceptance is feasibility
    of the relaxation, not proof of an AC-realizable trajectory. Net network real
    injection is an accounting quantity; its SOCP value includes relaxation effects.
    The lossy-DC loss proxy is a cost, not a withdrawal in real-power balance.
    Missing reactive/voltage/branch quantities are **unavailable**, never padded with zeros.
    """)
    return


@app.cell(hide_code=True)
def data_cell(Path, load_results, mo, summary_table):
    run_path = Path(mo.notebook_location()) / "results/e3_prepared"
    mo.stop(
        not (run_path / "progress.json").exists(),
        mo.md(
            "Retained `results/e3_prepared` and Stage A artifacts are required. No synthetic or historical-run fallback is used."
        ),
    )
    data = load_results(run_path)
    summaries = summary_table(data)
    capacities = data["tables"]["batteries"].capacity_mwh.to_numpy()
    mo.md(
        f"**20 / 20 accepted** · {len(data['hashes'])} completion artifact hashes verified · "
        f"execution commit `{data['binding']['context']['commit']}`. "
        "Saved audit decisions and successful finalized supervision checked; this viewer does not rerun numerical qualification."
    )
    return capacities, data, summaries


@app.cell(hide_code=True)
def case_controls(LABELS, data, mo):
    case_options = list(data["cases"])
    chosen_case = mo.ui.dropdown(
        case_options, value=case_options[1], label="Matched case", full_width=True
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
    return baseline, chosen_case, chosen_formulations, clock, difference, hour_range


@app.cell(hide_code=True)
def selection(
    baseline, chosen_case, chosen_formulations, clock, data, difference, hour_range, mo
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
def case_details(case, chosen_case, mo):
    arm = next(iter(case.values()))["arm"]
    initial_total = sum(arm["initial_soc_mwh"])
    terminal_total = sum(arm["terminal_soc_mwh"])
    mo.md(
        f"### {chosen_case.value}\n\n"
        f"Matched fleet and inputs · ρ = {arm['rho']:.4g} · throughput cost = {arm['throughput']:g} · "
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
                "Energy totals cover all 24 hours, irrespective of the plot zoom. Worker time includes preparation, not just native solve time."
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
            "All 20 horizon summaries": mo.ui.table(
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
                f"Run: `{data['directory']}`. All retained completion artifacts are hash-checked on load. "
                "Only supervised hard-target acceptances are plotted; no target-free diagnostic or historical E3 result is included. "
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
