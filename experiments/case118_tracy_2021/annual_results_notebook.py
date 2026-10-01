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
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    sys.path.insert(0, str(Path(mo.notebook_location())))
    from annual_results import load_results, aggregate, calendar, summaries, LABELS

    return (
        LABELS,
        Path,
        aggregate,
        calendar,
        go,
        load_results,
        make_subplots,
        mo,
        np,
        pd,
        summaries,
    )


@app.cell(hide_code=True)
def introduction(mo):
    mo.md("""
    # Tracy 2021: annual DC comparison

    **ρ = 1/3 · λ = 0.01 · identical device fleet · 50% initial/terminal SoC.**
    Read-only exploration of accepted annual results; no optimization runs here.
    All differences are **lossy DC − copper plate**, with no separate normalization.
    Time is fixed **UTC−08:00**, not local daylight-saving time. Battery power is
    positive for discharge. Numerical-scale shedding is displayed without clipping.
    DC feasibility does not establish AC feasibility; the DC loss proxy is a cost,
    not an energy withdrawal in the balance equations.
    """)
    return


@app.cell(hide_code=True)
def data_cell(Path, load_results, mo, summaries):
    run_path = Path(mo.notebook_location()) / "results/stage_c"
    mo.stop(
        not (run_path / "analysis.json").exists(),
        mo.md(
            "**Retained annual results/analysis are unavailable.** Obtain the ignored Stage C run and Stage A input artifacts; no fallback data will be substituted."
        ),
    )
    data = load_results(run_path)
    annual_table, monthly_table = summaries(data)
    mo.md(
        f"Execution commit: `{data['binding']['context']['commit']}`. Both archives hash-verified and independently accepted. Inputs and device identities agree between formulations."
    )
    return annual_table, data, monthly_table


@app.cell(hide_code=True)
def helpers(go, make_subplots, np):
    def time_plot(title, series, units):
        fig = go.Figure()
        colors = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#332288"]
        for i, (label, x, y) in enumerate(series):
            fig.add_trace(
                go.Scatter(
                    x=x,
                    y=y,
                    name=label,
                    mode="lines",
                    line=dict(color=colors[i % len(colors)], width=1.5),
                )
            )
        fig.update_layout(
            title=title,
            template="plotly_white",
            height=390,
            hovermode="x unified",
            yaxis_title=units,
            xaxis_title="2021 · fixed UTC−08:00",
            legend=dict(orientation="h", y=-0.3),
            margin=dict(b=100),
        )
        return fig

    def heatmaps(title, grids, dates, signed=False):
        fig = make_subplots(
            rows=len(grids),
            cols=1,
            shared_xaxes=True,
            subplot_titles=[label for label, values in grids],
            vertical_spacing=0.09,
        )
        low = min(float(np.min(v)) for label, v in grids)
        high = max(float(np.max(v)) for label, v in grids)
        if signed:
            high = max(abs(low), abs(high), 1e-15)
            low = -high
        if high == low:
            high = low + 1e-15
        for row, (label, values) in enumerate(grids, 1):
            fig.add_trace(
                go.Heatmap(x=dates, y=np.arange(24), z=values, coloraxis="coloraxis"),
                row=row,
                col=1,
            )
            fig.update_yaxes(title_text="Hour", dtick=6, row=row, col=1)
        fig.update_layout(
            title=title,
            height=240 * len(grids) + 110,
            template="plotly_white",
            coloraxis=dict(
                cmin=low, cmax=high, colorscale="RdBu_r" if signed else "Viridis"
            ),
        )
        return fig

    return heatmaps, time_plot


@app.cell(hide_code=True)
def input_controls(data, mo):
    input_options = {
        "Gross load": "load_mw",
        "Utility solar": "solar_mw",
        "Wind": "wind_mw",
        "Distributed solar": "dist_solar_mw",
        "Available net load": "net_load_mw",
    }
    input_channel = mo.ui.dropdown(
        input_options, value="Available net load", label="Full-year input heatmap"
    )
    start_date = mo.ui.date(value="2021-01-01", label="Time-series start (inclusive)")
    end_date = mo.ui.date(value="2021-12-31", label="Time-series end (inclusive)")
    mo.hstack([input_channel, start_date, end_date])
    return end_date, input_channel, input_options, start_date


@app.cell(hide_code=True)
def dates_cell(data, end_date, mo, pd, start_date):
    start_time = pd.Timestamp(start_date.value)
    stop_time = pd.Timestamp(end_date.value) + pd.Timedelta(days=1)
    mo.stop(
        start_time >= stop_time
        or start_time < pd.Timestamp("2021-01-01")
        or stop_time > pd.Timestamp("2022-01-01"),
        mo.md("Choose an ordered date range within 2021."),
    )
    time_index = data["inputs"].index
    interval_mask = (time_index >= start_time) & (time_index < stop_time)
    boundary_index = pd.date_range(time_index[0], periods=8761, freq="h")
    boundary_mask = (boundary_index >= start_time) & (boundary_index <= stop_time)
    return boundary_index, boundary_mask, interval_mask, time_index


@app.cell(hide_code=True)
def inputs_plot(
    calendar,
    data,
    heatmaps,
    input_channel,
    input_options,
    interval_mask,
    mo,
    time_index,
    time_plot,
):
    input_heatmap = heatmaps(
        "Shared input · MW",
        [(input_channel.selected_key, calendar(data["inputs"][input_channel.value]))],
        time_index[::24],
        signed=input_channel.value == "net_load_mw",
    )
    input_series = [
        (label, time_index[interval_mask], data["inputs"].loc[interval_mask, key])
        for label, key in input_options.items()
    ]
    mo.vstack(
        [
            mo.ui.plotly(input_heatmap),
            mo.ui.plotly(
                time_plot("Shared inputs (input deltas = 0)", input_series, "MW")
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def output_controls(aggregate, data, mo):
    aggregates = {f: aggregate(r) for f, r in data["runs"].items()}
    output_quantity = mo.ui.dropdown(
        list(aggregates["lossy_dc"]),
        value="Dispatchable generation (MW)",
        label="Output / delta",
    )
    output_quantity
    return aggregates, output_quantity


@app.cell(hide_code=True)
def aggregate_plots(
    LABELS,
    aggregates,
    boundary_index,
    boundary_mask,
    calendar,
    data,
    heatmaps,
    interval_mask,
    mo,
    output_quantity,
    time_index,
    time_plot,
):
    quantity = output_quantity.value
    copper = aggregates["singlenode_dc"][quantity]
    network = aggregates["lossy_dc"][quantity]
    output_map = heatmaps(
        quantity,
        [(LABELS[f], calendar(aggregates[f][quantity])) for f in LABELS],
        time_index[::24],
        signed="power" in quantity,
    )
    delta_map = heatmaps(
        "Difference · " + quantity,
        [("Lossy DC − copper plate", calendar(network - copper))],
        time_index[::24],
        signed=True,
    )
    is_soc = quantity.startswith("SoC")
    plot_index = boundary_index if is_soc else time_index
    plot_mask = boundary_mask if is_soc else interval_mask
    trajectories = {
        f: data["runs"][f]["boundary_soc_mwh"].sum(axis=1)
        if is_soc
        else aggregates[f][quantity]
        for f in LABELS
    }
    overlay = [
        (LABELS[f], plot_index[plot_mask], trajectories[f][plot_mask]) for f in LABELS
    ]
    delta_series = [
        (
            "Lossy DC − copper plate",
            plot_index[plot_mask],
            (trajectories["lossy_dc"] - trajectories["singlenode_dc"])[plot_mask],
        )
    ]
    mo.vstack(
        [
            mo.md(
                "### Outputs and differences\nSoC heatmaps use the **start** of each hourly interval. SoC time series retain all selected boundaries, including the final boundary. Other outputs are interval powers."
            ),
            mo.ui.plotly(output_map),
            mo.ui.plotly(delta_map),
            mo.ui.plotly(time_plot(quantity, overlay, "MWh" if is_soc else "MW")),
            mo.ui.plotly(
                time_plot(
                    "Difference · " + quantity, delta_series, "MWh" if is_soc else "MW"
                )
            ),
        ]
    )
    return


@app.cell(hide_code=True)
def device_family(mo):
    family = mo.ui.dropdown(
        [
            "Generators",
            "Batteries: power",
            "Batteries: SoC",
            "Renewables",
            "Loads: served",
            "Loads: shed",
            "Branches (network only)",
        ],
        value="Batteries: SoC",
        label="Device-level comparison",
    )
    family
    return (family,)


@app.cell(hide_code=True)
def devices_cell(data, family, mo):
    table_key = (
        "batteries"
        if family.value.startswith("Batteries")
        else "renewables"
        if family.value == "Renewables"
        else "generators"
    )
    names = data["tables"][table_key].device_id.tolist()
    if family.value.startswith("Loads"):
        names = data["runs"]["lossy_dc"]["identities"]["loads"]
    if family.value.startswith("Branches"):
        names = [
            f"Branch {j}: {int(b[0])} → {int(b[1])}"
            for j, b in enumerate(data["branches"])
        ]
    device = mo.ui.dropdown(
        {n: i for i, n in enumerate(names)}, value=names[0], label="Device"
    )
    device
    return (device,)


@app.cell(hide_code=True)
def device_plots(
    LABELS,
    boundary_index,
    boundary_mask,
    data,
    device,
    family,
    interval_mask,
    mo,
    time_index,
    time_plot,
):
    key = {
        "Generators": "Pg",
        "Batteries: power": "b",
        "Batteries: SoC": "boundary_soc_mwh",
        "Renewables": "p_nd",
        "Loads: served": "p_load_served",
        "Loads: shed": "p_load_shed",
        "Branches (network only)": "p_flows",
    }[family.value]
    device_time = boundary_index if key == "boundary_soc_mwh" else time_index
    device_mask = boundary_mask if key == "boundary_soc_mwh" else interval_mask
    device_values = {
        f: (r[key] if key == "boundary_soc_mwh" else r["result"][key])[:, device.value]
        for f, r in data["runs"].items()
        if key != "p_flows" or f == "lossy_dc"
    }
    device_series = [
        (LABELS[f], device_time[device_mask], v[device_mask])
        for f, v in device_values.items()
    ]
    device_delta = (
        []
        if key == "p_flows"
        else [
            (
                "Lossy DC − copper plate",
                device_time[device_mask],
                (device_values["lossy_dc"] - device_values["singlenode_dc"])[
                    device_mask
                ],
            )
        ]
    )
    device_units = "MWh" if key == "boundary_soc_mwh" else "MW"
    extra = (
        mo.md(
            "Branch flows have no copper-plate counterpart; no branch difference is defined."
        )
        if key == "p_flows"
        else mo.ui.plotly(time_plot("Device difference", device_delta, device_units))
    )
    mo.vstack(
        [
            mo.ui.plotly(
                time_plot(
                    family.value + " · " + device.selected_key,
                    device_series,
                    device_units,
                )
            ),
            extra,
        ]
    )
    return


@app.cell(hide_code=True)
def tables_cell(annual_table, data, mo, monthly_table, np, pd):
    utilization = (
        np.abs(data["runs"]["lossy_dc"]["result"]["p_flows"]) / data["branches"][:, 5]
    )
    congestion = pd.DataFrame(
        {
            "From bus": data["branches"][:, 0].astype(int),
            "To bus": data["branches"][:, 1].astype(int),
            "Peak utilization": utilization.max(axis=0),
            "Hours ≥99.999%": (utilization >= 0.99999).sum(axis=0),
        }
    ).sort_values("Hours ≥99.999%", ascending=False)
    residuals = pd.DataFrame(
        {f: r["audit"]["residuals"] for f, r in data["runs"].items()}
    )
    mo.vstack(
        [
            mo.md(
                "## Annual totals and timing\nEnergy is MWh; costs are objective units. Compilation time and native solve time are diagnostic subdivisions, not necessarily an exact sum of wall time. RSS is sampled worker-PID memory, not whole-machine memory."
            ),
            mo.ui.table(annual_table, selection=None),
            mo.md("## Monthly totals and boundaries"),
            mo.ui.table(monthly_table, selection=None),
            mo.md("## Network congestion"),
            mo.ui.table(congestion, selection=None),
            mo.md("## Acceptance residuals"),
            mo.ui.table(residuals.reset_index(names="Residual"), selection=None),
            mo.md("## Retained artifact hashes"),
            mo.ui.table(
                pd.DataFrame(
                    list(data["hashes"].items()), columns=["Artifact", "SHA-256"]
                ),
                selection=None,
            ),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
