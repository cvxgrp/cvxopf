# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "marimo==0.24.2",
#     "matplotlib>=3.10",
#     "numpy>=2.0",
#     "pandas>=2.2",
#     "plotly==7.1.0",
# ]
# ///

import marimo

__generated_with = "0.24.2"
app = marimo.App(
    width="medium",
    app_title="Completed toy AC study · Matrix and Tensor views",
)


@app.cell
def _():
    from pathlib import Path
    import sys
    import marimo as mo
    import matplotlib.pyplot as plt

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from experiments.case118_annual_hierarchy.analysis.dashboard_support import load_final_snapshot, plot_heatmaps, WEEKDAYS

    return Path, WEEKDAYS, load_final_snapshot, mo, plot_heatmaps, plt


@app.cell
def _(mo):
    mo.md("""
    # Annual AC study explorer

    **Matrix view:** hour × day. **Tensor view:** hour × calendar week × weekday.
    Gray means unexecuted or calendar padding, never zero. No interpolation or averaging.

    This explorer reads the preserved final **toy** snapshot: 8,760 hourly actions.
    Calendar time is synthetic 2025 UTC, not the future Tracy study.
    Controls use saved data; opening this notebook does not collect archives or solve OPF.
    """)
    return


@app.cell
def _(load_final_snapshot):
    snapshot = load_final_snapshot()
    return (snapshot,)


@app.cell
def _(mo, snapshot):
    mo.md(f"""
    **Correction snapshot:** {snapshot['report']['completed']:,} / {snapshot['report']['horizon']:,}
    (**{100*snapshot['report']['completed']/snapshot['report']['horizon']:.2f}%**),
    captured {snapshot['report']['snapshot_utc']}.<br>
    **Completion/timing snapshot:** {snapshot['completion']['completed']:,} intervals
    ({snapshot['completion']['percent']:.2f}%), through {snapshot['completion']['snapshot_finished_utc']}.<br>
    Historical collectors ran serially; each retains its own capture time.
    Retained data: `{snapshot['path']}`.

    Choose signed net sums, absolute device-wise magnitudes, or opposing generator redispatch for differences between the **executed AC first action**
    and annual DC schedule. SoC compares the end of that hour, plotted on its start-hour row.
    These are controller-selected differences, **not minimum required feasibility repairs**.
    In signed mode, positive generator change means more generation; positive battery change means more discharge (or less charging); positive SoC means more stored energy. Generator net changes include loss balancing; SoC includes inherited schedule divergence.
    """)
    return


@app.cell(hide_code=True)
def _(mo):
    heatmap_mode = mo.ui.dropdown(
        options=['Signed net (AC − DC)', 'Absolute magnitude', 'Opposing generator redispatch'],
        value='Opposing generator redispatch', label='Heatmap metric')
    return (heatmap_mode,)


@app.cell(hide_code=True)
def _(Path, mo, plt, snapshot, stress_data):
    def load_operating_trajectories(data, snapshot):
        import hashlib
        import json
        import numpy as np
        import pandas as pd

        base = Path(__file__).resolve().parent / "artifacts/operating_totals"
        meta = json.loads((base / "provenance.json").read_text())
        csv_path = base / "dc_operating_totals.csv"
        if hashlib.sha256(csv_path.read_bytes()).hexdigest() != meta["csv_sha256"]:
            raise ValueError("Saved DC operating totals changed")
        for key in ("outer_sha256", "manifest_sha256", "fixture_hashes"):
            if meta[key] != snapshot["report"][key]:
                raise ValueError(f"Operating trajectory identity mismatch: {key}")
        dc = pd.read_csv(csv_path).set_index("iteration")
        frame = data["frame"].sort_index()
        np.testing.assert_array_equal(dc.index, frame.index)
        np.testing.assert_array_equal(dc.index, np.arange(snapshot["report"]["horizon"]))
        np.testing.assert_allclose(dc.dc_battery_power_mw,
            frame.aggregate_discharging_mw - frame.aggregate_charging_mw, atol=1e-8, rtol=0)
        np.testing.assert_allclose(dc.dc_soc_end_mwh,
            frame.aggregate_soc_fraction * snapshot["report"]["fleet_capacity_mwh"]
            - snapshot["report"]["delta_hours"] * dc.dc_battery_power_mw, atol=8e-4, rtol=0)
        result = dc.copy()
        result["ac_generation_mw"] = dc.dc_generation_mw + frame.generator_net_change_mw
        result["ac_battery_power_mw"] = dc.dc_battery_power_mw + frame.battery_net_change_mw
        result["ac_soc_end_mwh"] = dc.dc_soc_end_mwh + frame[
            [f"{device}.delta_soc_end_mwh" for device in snapshot["report"]["storage_ids"]]
        ].sum(axis=1)
        if not np.isfinite(result).all().all():
            raise ValueError("Nonfinite operating trajectory")
        return result


    def plot_calendar_heatmaps(frame, snapshot, panels, title):
        from datetime import timedelta

        days = snapshot["report"]["horizon"] // 24
        fig, axes = plt.subplots(len(panels), 1, figsize=(15, 3 * len(panels)),
                                 sharex=True, layout="constrained")
        for ax, (column, label, unit, cmap, low, high) in zip(axes, panels):
            matrix = frame[column].to_numpy().reshape(days, 24).T
            im = ax.imshow(matrix, origin="lower", aspect="auto", interpolation="nearest",
                           extent=(0, days, 0, 24), cmap=cmap, vmin=low, vmax=high)
            ax.set_title(label, loc="left", weight="bold")
            ax.set_ylabel("Hour (study UTC)")
            ax.set_yticks([.5, 6.5, 12.5, 18.5, 23.5], ["00", "06", "12", "18", "23"])
            fig.colorbar(im, ax=ax, pad=.01, label=unit)
        dates = [snapshot["start"] + timedelta(days=d) for d in range(days)]
        ticks = [d for d, date in enumerate(dates) if date.day == 1]
        axes[-1].set_xticks(ticks, [dates[d].strftime("%b") for d in ticks])
        axes[-1].set_xlabel("Synthetic study date · 2025 UTC")
        fig.suptitle(f"{title} · {len(frame):,} hourly observations", weight="bold")
        return fig


    def plot_operating_heatmaps(frame, snapshot, realization):
        generation_max = frame[["ac_generation_mw", "dc_generation_mw"]].to_numpy().max()
        panels = [
            (f"{realization}_generation_mw", "Gross dispatchable generation", "MW", "viridis", 0, generation_max),
            (f"{realization}_battery_power_mw", "Signed fleet battery power · positive discharge", "MW", "RdBu_r",
             -snapshot["report"]["fleet_rating_mw"], snapshot["report"]["fleet_rating_mw"]),
            (f"{realization}_soc_end_mwh", "Fleet total SoC · end of hour", "MWh", "viridis",
             0, snapshot["report"]["fleet_capacity_mwh"]),
        ]
        title = "Executed AC first actions" if realization == "ac" else "Annual DC plan"
        return plot_calendar_heatmaps(frame, snapshot, panels, title)


    def load_report_inputs(snapshot):
        import hashlib
        import json
        import numpy as np
        import pandas as pd

        base = Path(__file__).resolve().parent.parent / "s5_closeout"
        provenance = json.loads((base / "provenance.json").read_text())
        path = base / "aggregate_inputs.csv"
        if hashlib.sha256(path.read_bytes()).hexdigest() != provenance["artifacts"][path.name]:
            raise ValueError("Report input data changed")
        if provenance["fixture_hashes"] != snapshot["report"]["fixture_hashes"]:
            raise ValueError("Report and dashboard fixture mismatch")
        frame = pd.read_csv(path)
        np.testing.assert_array_equal(pd.to_datetime(frame.timestamp, utc=True),
            pd.date_range(snapshot["start"], periods=snapshot["report"]["horizon"], freq="h"))
        if not np.isfinite(frame.drop(columns="timestamp")).all().all():
            raise ValueError("Nonfinite report inputs")
        return frame


    def plot_input_heatmaps(frame, snapshot):
        signals = [
            ("load_mw", "Gross active load"),
            ("available_nd_mw", "Available wind + utility solar"),
            ("net_load_mw", "Available net load"),
            ("solar_mw", "Utility solar availability"),
            ("wind_mw", "Wind availability"),
        ]
        panels = []
        for column, label in signals:
            maximum = frame[column].abs().max()
            signed = column == "net_load_mw"
            panels.append((column, label, "MW", "RdBu_r" if signed else "viridis",
                           -maximum if signed else 0, maximum))
        return plot_calendar_heatmaps(frame, snapshot, panels, "Toy study inputs")


    operating_trajectories = load_operating_trajectories(stress_data, snapshot)
    ac_operating_figure = plot_operating_heatmaps(operating_trajectories, snapshot, "ac")
    dc_operating_figure = plot_operating_heatmaps(operating_trajectories, snapshot, "dc")
    plt.close(ac_operating_figure)
    plt.close(dc_operating_figure)
    report_input_data = load_report_inputs(snapshot)
    input_heatmap_figure = plot_input_heatmaps(report_input_data, snapshot)
    plt.close(input_heatmap_figure)
    operating_tabs = mo.ui.tabs({
        "Executed AC": ac_operating_figure,
        "DC plan": dc_operating_figure,
        "Inputs": input_heatmap_figure,
    }, value="DC plan")
    mo.vstack([
        mo.md("## Operating trajectories and inputs"),
        operating_tabs,
        mo.md("Generation sums dispatchable generators and excludes wind/solar. Battery power is the signed fleet sum: positive discharge, negative charging. SoC is total fleet energy at the end of each hour, plotted on that hour's start row. AC and DC use identical scales: generation from zero to the combined maximum, battery power ±fleet rating, and SoC from zero to fleet capacity. All 8,760 hours are shown, independently of the correlation filters. AC totals combine the accepted DC trajectory with the saved executed AC−DC differences."),
    ])
    return operating_trajectories, report_input_data


@app.cell(hide_code=True)
def weekly_imports():
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go

    return go, np, pd


@app.cell(hide_code=True)
def weekly_input_series(np, pd, report_input_data, snapshot):
    weekly_timestamps = pd.to_datetime(report_input_data.timestamp, utc=True)
    assert np.array_equal(weekly_timestamps, pd.date_range(snapshot["start"], periods=8760, freq="h"))
    assert weekly_timestamps.iloc[0].hour == 0
    assert snapshot["report"]["delta_hours"] == 1
    np.testing.assert_allclose(report_input_data.net_load_mw,
                               report_input_data.load_mw - report_input_data.available_nd_mw)
    net_load = pd.Series(report_input_data.net_load_mw.to_numpy(), index=pd.DatetimeIndex(weekly_timestamps))
    assert np.isfinite(net_load.to_numpy()).all()
    return (net_load,)


@app.cell(hide_code=True)
def weekly_metrics(net_load, np, pd):
    def weekly_metrics(net):
        blocks = np.lib.stride_tricks.sliding_window_view(net.to_numpy(), 168)[::24]
        starts = net.index[:len(net) - 167:24]
        result = pd.DataFrame({
            "start": starts,
            "end": starts + pd.Timedelta(hours=167),
            "net_energy_gwh": blocks.sum(axis=1) / 1000,
            "peak_net_load_mw": blocks.max(axis=1),
            "minimum_net_load_mw": blocks.min(axis=1),
            "positive_net_hours": (blocks > 0).sum(axis=1),
        })
        result["dates"] = result.start.dt.strftime("%b %d") + "–" + result.end.dt.strftime("%b %d, %Y")
        result["start_date"] = result.start.dt.strftime("%Y-%m-%d")
        return result

    raw_weeks = weekly_metrics(net_load)
    assert len(raw_weeks) == 359
    # Reconstruct the three measures with pandas rolling operations independently.
    rolling_endpoints = net_load.rolling(168)
    assert np.allclose(raw_weeks.net_energy_gwh, rolling_endpoints.sum().iloc[167::24] / 1000)
    assert np.allclose(raw_weeks.peak_net_load_mw, rolling_endpoints.max().iloc[167::24])
    assert np.allclose(raw_weeks.minimum_net_load_mw, rolling_endpoints.min().iloc[167::24])
    return (raw_weeks,)


@app.cell(hide_code=True)
def weekly_controls(mo, raw_weeks):
    show_candidates = mo.ui.checkbox(value=True, label="Label candidate weeks")
    week_picker = mo.ui.dropdown(options=raw_weeks.start_date.tolist(),
        value=raw_weeks.loc[raw_weeks.net_energy_gwh.idxmax(), "start_date"],
        label="Inspect week starting (2025 UTC)", allow_select_none=False, searchable=True)
    mo.vstack([
        mo.md("## Weekly input conditions · energy, peak, and minimum\n"
              "Each point is a midnight-start **168-hour window** in the toy study's **synthetic 2025 UTC** calendar: "
              "359 overlapping windows wholly inside the year. Net load = load − available wind − utility solar, "
              "before curtailment, storage dispatch, or losses. All hours are included, independently of the AC−DC correlation filters. "
              "Energy is the signed hourly sum (GWh); peak and minimum are hourly net load (MW). "
              "Hover for dates and all three metrics; the black diamond marks the inspected week. "
              "Candidate medians are selected per metric; energy balance means nearest zero."),
        mo.hstack([show_candidates, week_picker], justify="start", gap=2, wrap=True),
    ])
    return show_candidates, week_picker


@app.cell(hide_code=True)
def weekly_selection(raw_weeks, week_picker):
    weeks = raw_weeks
    metric_columns = ["net_energy_gwh", "peak_net_load_mw", "minimum_net_load_mw"]
    axis_labels = {"net_energy_gwh": "Net energy (GWh)",
                   "peak_net_load_mw": "Peak net load (MW)",
                   "minimum_net_load_mw": "Minimum net load (MW)"}
    inspected_week = weeks.loc[weeks.start_date.eq(week_picker.value)].iloc[0]
    return axis_labels, inspected_week, metric_columns, weeks


@app.cell(hide_code=True)
def weekly_candidates(pd, weeks):
    def candidate_periods(frame):
        rules = [
            ("Energy low", "net_energy_gwh", 0, "#197a62"),
            ("Energy median", "net_energy_gwh", .5, "#2666a3"),
            ("Energy high", "net_energy_gwh", 1, "#b54836"),
            ("Peak low", "peak_net_load_mw", 0, "#197a62"),
            ("Peak median", "peak_net_load_mw", .5, "#2666a3"),
            ("Peak high", "peak_net_load_mw", 1, "#b54836"),
            ("Lowest minimum", "minimum_net_load_mw", 0, "#b57816"),
            ("Highest minimum", "minimum_net_load_mw", 1, "#71589e"),
        ]
        rows = []
        for label, metric, quantile, color in rules:
            index = (frame[metric] - frame[metric].quantile(quantile)).abs().idxmin()
            rows.append({"candidate": label, "color": color, **frame.loc[index].to_dict()})
        balance = frame.loc[frame.net_energy_gwh.abs().idxmin()].to_dict()
        rows.append({"candidate": "Energy balance", "color": "#8e5a9c", **balance})
        return pd.DataFrame(rows)

    candidates = candidate_periods(weeks)
    return (candidates,)


@app.cell(hide_code=True)
def weekly_plot_helpers(go):
    hover_columns = ["dates", "net_energy_gwh", "peak_net_load_mw", "minimum_net_load_mw", "positive_net_hours"]
    hover_template = (
        "<b>%{customdata[0]}</b><br>"
        "Net energy: %{customdata[1]:,.3f} GWh<br>"
        "Peak net load: %{customdata[2]:,.2f} MW<br>"
        "Minimum net load: %{customdata[3]:,.2f} MW<br>"
        "Positive-net hours: %{customdata[4]} / 168<extra></extra>"
    )

    def scatter_view(frame, candidate_frame, chosen, axes, labels, annotate, dimension=2):
        trace_type = go.Scatter3d if dimension == 3 else go.Scatter
        positions = dict(zip(["x", "y", "z"][:dimension], [frame[column] for column in axes]))
        chart = go.Figure(trace_type(
            **positions, mode="markers", name="All 359 windows",
            marker={"size": 4 if dimension == 3 else 7, "color": "#8495a7", "opacity": .6},
            customdata=frame[hover_columns].to_numpy(), hovertemplate=hover_template,
        ))
        if annotate:
            unique_candidates = {}
            for candidate in candidate_frame.to_dict("records"):
                key = tuple(candidate[column] for column in hover_columns[1:4])
                if key in unique_candidates:
                    unique_candidates[key]["candidate"] += " / " + candidate["candidate"]
                else:
                    unique_candidates[key] = candidate.copy()
            label_positions = {"Energy median": "top left", "Peak median": "bottom right",
                               "Energy high": "top right", "Peak high": "top left",
                               "Peak low": "bottom center", "Highest minimum": "bottom right",
                               "Energy low / Energy balance": "bottom right"}
            for candidate in unique_candidates.values():
                positions = dict(zip(["x", "y", "z"][:dimension], [[candidate[column]] for column in axes]))
                chart.add_trace(trace_type(
                    **positions, mode="markers+text", name=candidate["candidate"],
                    text=[candidate["candidate"]], textposition=label_positions.get(candidate["candidate"], "top center"),
                    textfont={"size": 10, "color": candidate["color"]},
                    marker={"size": 6 if dimension == 3 else 10, "color": candidate["color"]},
                    customdata=[[candidate[column] for column in hover_columns]], hovertemplate=hover_template,
                ))
        positions = dict(zip(["x", "y", "z"][:dimension], [[chosen[column]] for column in axes]))
        chart.add_trace(trace_type(
            **positions, mode="markers", name="Inspected week",
            marker={"size": 8 if dimension == 3 else 13, "color": "#151b27", "symbol": "diamond"},
            customdata=[[chosen[column] for column in hover_columns]], hovertemplate=hover_template,
        ))
        chart.update_layout(template="plotly_white", showlegend=False,
                            margin={"l": 65, "r": 25, "t": 45, "b": 60},
                            height=600 if dimension == 3 else 440,
                            uirevision="toy-weekly", hoverlabel={"namelength": -1})
        if dimension == 3:
            chart.update_layout(scene={
                "xaxis_title": labels[axes[0]], "yaxis_title": labels[axes[1]],
                "zaxis_title": labels[axes[2]], "aspectmode": "cube",
                "camera": {"eye": {"x": 1.5, "y": 1.6, "z": 1.1}},
            })
        else:
            chart.update_xaxes(title=labels[axes[0]], zeroline=True, zerolinecolor="#bfc5cd")
            chart.update_yaxes(title=labels[axes[1]], zeroline=True, zerolinecolor="#bfc5cd")
        return chart

    return (scatter_view,)


@app.cell(hide_code=True)
def weekly_three_dimensions(
    axis_labels,
    candidates,
    inspected_week,
    metric_columns,
    mo,
    scatter_view,
    show_candidates,
    weeks,
):
    figure_3d = scatter_view(weeks, candidates, inspected_week, metric_columns, axis_labels, show_candidates.value, dimension=3)
    figure_3d.update_layout(title="Weekly energy, peak, and minimum net load")
    mo.vstack([mo.md("## Three dimensions\nDrag to rotate; scroll to zoom. The black diamond is the inspected week."), mo.as_html(figure_3d)])
    return


@app.cell(hide_code=True)
def weekly_energy_peak(
    axis_labels,
    candidates,
    inspected_week,
    mo,
    scatter_view,
    show_candidates,
    weeks,
):
    figure_energy_peak = scatter_view(weeks, candidates, inspected_week, ["net_energy_gwh", "peak_net_load_mw"], axis_labels, show_candidates.value)
    figure_energy_peak.update_layout(title="Net energy versus peak net load")
    mo.vstack([mo.md("## Pairwise projections"), mo.ui.plotly(figure_energy_peak)])
    return


@app.cell(hide_code=True)
def weekly_energy_minimum(
    axis_labels,
    candidates,
    inspected_week,
    mo,
    scatter_view,
    show_candidates,
    weeks,
):
    figure_energy_minimum = scatter_view(weeks, candidates, inspected_week, ["net_energy_gwh", "minimum_net_load_mw"], axis_labels, show_candidates.value)
    figure_energy_minimum.update_layout(title="Net energy versus minimum net load")
    mo.ui.plotly(figure_energy_minimum)
    return


@app.cell(hide_code=True)
def weekly_peak_minimum(
    axis_labels,
    candidates,
    inspected_week,
    mo,
    scatter_view,
    show_candidates,
    weeks,
):
    figure_peak_minimum = scatter_view(weeks, candidates, inspected_week, ["peak_net_load_mw", "minimum_net_load_mw"], axis_labels, show_candidates.value)
    figure_peak_minimum.update_layout(title="Peak versus minimum net load")
    mo.ui.plotly(figure_peak_minimum)
    return


@app.cell(hide_code=True)
def weekly_candidate_table(axis_labels, candidates, metric_columns, mo, weeks):
    candidate_table = candidates[["candidate", "dates", *metric_columns, "positive_net_hours"]].rename(columns={
        **axis_labels, "candidate": "Candidate", "dates": "2025 interval", "positive_net_hours": "Positive-net hours",
    })
    mo.vstack([
        mo.md("## Candidate periods\nExtrema and medians are selected separately for each metric. Earliest start breaks ties."),
        mo.ui.table(candidate_table.round(3), selection=None),
        mo.accordion({"All 359 windows": mo.ui.table(weeks[["dates", *metric_columns, "positive_net_hours"]].rename(columns=axis_labels).round(3), selection=None)}),
    ])
    return


@app.cell(hide_code=True)
def _(mo, snapshot):
    window_start_slider = mo.ui.slider(
        start=0, stop=snapshot["report"]["horizon"] - 72, step=1, value=0,
        label="72-hour window · start hour of year", show_value=True,
        include_input=True, full_width=True, debounce=True,
    )
    mo.vstack([mo.md("## Operating trajectories and inputs · 72-hour window"), window_start_slider])
    return (window_start_slider,)


@app.cell(hide_code=True)
def _(mo):
    get_time_series_tab, set_time_series_tab = mo.state("Executed AC")
    return get_time_series_tab, set_time_series_tab


@app.cell(hide_code=True)
def _(
    operating_trajectories,
    plt,
    report_input_data,
    snapshot,
    stress_data,
    window_start_slider,
):
    def plot_72_hour_series(operating, inputs, data, snapshot, start, kind):
        from datetime import timedelta
        import matplotlib.dates as mdates
        import numpy as np

        stop = start + 72
        if not 0 <= start <= len(operating) - 72:
            raise ValueError("Select a complete 72-hour window within the study")
        edges = [snapshot["start"] + timedelta(hours=i) for i in range(start, stop + 1)]
        generation_max = operating[["ac_generation_mw", "dc_generation_mw"]].to_numpy().max()
        rating, capacity = snapshot["report"]["fleet_rating_mw"], snapshot["report"]["fleet_capacity_mwh"]
        if kind == "inputs":
            frame = inputs
            signals = [("load_mw", "Gross active load"),
                       ("available_nd_mw", "Available wind + utility solar"),
                       ("net_load_mw", "Available net load"),
                       ("solar_mw", "Utility solar availability"),
                       ("wind_mw", "Wind availability")]
            panels = [(column, label, "MW", min(0, frame[column].min()), frame[column].max())
                      for column, label in signals]
            title = "Toy study inputs"
        else:
            frame = operating
            panels = [
                (f"{kind}_generation_mw", "Gross dispatchable generation", "MW", 0, generation_max),
                (f"{kind}_battery_power_mw", "Signed fleet battery power · positive discharge", "MW", -rating, rating),
                (f"{kind}_soc_end_mwh", "Fleet total SoC · end-of-hour samples", "MWh", 0, capacity),
            ]
            title = "Executed AC first actions" if kind == "ac" else "Annual DC plan"
        shards = data["frame"].sort_index()["shard"].to_numpy()
        resets = [i for i in range(start, stop) if i > 0 and shards[i] != shards[i - 1]]
        splits = [i - start for i in resets if i > start]
        fig, axes = plt.subplots(len(panels), 1, figsize=(15, 3 * len(panels)),
                                 sharex=True, layout="constrained")
        for ax, (column, label, unit, low, high) in zip(axes, panels):
            values = frame[column].iloc[start:stop].to_numpy()
            if column.endswith("soc_end_mwh"):
                # The AC controller is initialized separately at each shard boundary.
                # Never connect endpoint samples across that reset as a continuous path.
                groups = np.split(np.arange(72), splits if kind == "ac" else [])
                for group in groups:
                    ax.plot([edges[i + 1] for i in group], values[group],
                            color="#0072B2", lw=1.5, marker="o", markersize=2.5)
            else:
                ax.stairs(values, mdates.date2num(edges), baseline=None, color="#0072B2", lw=1.6)
            for i in resets:
                ax.axvline(snapshot["start"] + timedelta(hours=i), color="#777777", ls=":", lw=1)
            if low < 0:
                ax.axhline(0, color="#777777", lw=.7)
            ax.set_ylim(low, high)
            ax.set_ylabel(unit)
            ax.set_title(label, loc="left", weight="bold")
            ax.grid(alpha=.15)
        axes[-1].set_xlim(edges[0], edges[-1])
        axes[-1].xaxis.set_major_locator(mdates.HourLocator(interval=12, tz=snapshot["start"].tzinfo))
        axes[-1].xaxis.set_major_formatter(mdates.DateFormatter("%b %d\n%H:%M", tz=snapshot["start"].tzinfo))
        axes[-1].set_xlabel("Synthetic study time · UTC")
        fig.suptitle(f"{title} · 72 hours\n{edges[0]:%Y-%m-%d %H:%M} to {edges[-1]:%Y-%m-%d %H:%M} UTC", weight="bold")
        return fig


    window_start_hour = int(window_start_slider.value)
    ac_window_figure = plot_72_hour_series(operating_trajectories, report_input_data, stress_data, snapshot, window_start_hour, "ac")
    dc_window_figure = plot_72_hour_series(operating_trajectories, report_input_data, stress_data, snapshot, window_start_hour, "dc")
    inputs_window_figure = plot_72_hour_series(operating_trajectories, report_input_data, stress_data, snapshot, window_start_hour, "inputs")
    plt.close(ac_window_figure)
    plt.close(dc_window_figure)
    plt.close(inputs_window_figure)
    return ac_window_figure, dc_window_figure, inputs_window_figure


@app.cell(hide_code=True)
def _(
    ac_window_figure,
    dc_window_figure,
    get_time_series_tab,
    inputs_window_figure,
    mo,
    set_time_series_tab,
):
    time_series_tabs = mo.ui.tabs({
        "Executed AC": ac_window_figure,
        "DC plan": dc_window_figure,
        "Inputs": inputs_window_figure,
    }, value=get_time_series_tab(), on_change=set_time_series_tab)
    mo.vstack([
        time_series_tabs,
        mo.md("One slider controls all three tabs, in one-hour steps. Each window contains 72 complete hourly intervals; it never wraps the year. Power and inputs are shown as hourly steps. SoC dots are end-of-hour values at their actual boundary times. Axis ranges stay fixed as the window moves, with common scales for AC and DC. Dotted vertical lines mark shard boundaries; AC SoC lines break across controller resets. These views show the full retained trajectories and are independent of the correlation filters."),
    ])
    return


@app.cell
def _(mo):
    mo.md("""
    ## AC−DC changes
    """)
    return


@app.cell(hide_code=True)
def _(plot_heatmaps, plt, snapshot):
    def signed_calendar_data(data):
        """Net AC-minus-DC changes, aligned to the existing calendar without imputation."""
        import csv
        from datetime import datetime, timedelta
        import numpy as np

        matrix = np.full_like(data['matrix'], np.nan)
        tensor = np.full_like(data['tensor'], np.nan)
        seen = set()
        with (data['path'] / 'dispatch/intervals.csv').open() as stream:
            for row in csv.DictReader(stream):
                i = int(row['iteration'])
                if i in seen or not 0 <= i < data['report']['horizon']:
                    raise ValueError('Duplicate or invalid interval')
                seen.add(i)
                if datetime.fromisoformat(row['timestamp']) != data['start'] + timedelta(hours=i):
                    raise ValueError('Calendar timestamp mismatch')
                storage_ids = data['report']['storage_ids']
                battery = sum(float(row[f'{s}.delta_b_mw']) for s in storage_ids)
                soc = sum(float(row[f'{s}.delta_soc_end_mwh']) for s in storage_ids)
                np.testing.assert_allclose(battery, float(row['battery_net_change_mw']), atol=1e-8, rtol=0)
                values = np.array([float(row['generator_net_change_mw']), battery, soc])
                if not np.isfinite(values).all():
                    raise ValueError('Nonfinite signed correction')
                day, hour = divmod(i, 24)
                week, weekday = divmod(day + data['start'].weekday(), 7)
                matrix[:, hour, day] = values
                tensor[:, hour, week, weekday] = values
        if len(seen) != data['report']['completed']:
            raise ValueError('Signed correction count mismatch')
        return dict(data, matrix=matrix, tensor=tensor)


    def plot_signed_heatmaps(data, weekday=None):
        """Reuse calendar layout; fixed symmetric per-metric limits across all weekdays."""
        import numpy as np
        fig = plot_heatmaps(data, weekday)
        labels = [('Net generator dispatch change', 'MW'),
                  ('Net battery-power change', 'MW'),
                  ('Net end-of-hour SoC difference', 'MWh')]
        axes = [ax for ax in fig.axes if ax.images]
        for index, (ax, (title, unit)) in enumerate(zip(axes, labels)):
            image = ax.images[0]
            limit = float(np.nanmax(np.abs(data['matrix'][index])))
            limit = limit if limit > 0 else 1.0
            image.set_cmap(plt.get_cmap('seismic').with_extremes(bad='#dedede'))
            image.set_clim(-limit, limit)
            ax.set_title(title, loc='left', weight='bold')
            image.colorbar.set_label(f'Σ (AC − DC) ({unit})')
        return fig

    signed_snapshot = signed_calendar_data(snapshot)
    return plot_signed_heatmaps, signed_snapshot


@app.cell(hide_code=True)
def _(plot_heatmaps, signed_snapshot, snapshot):
    def opposing_calendar_data(absolute, signed):
        """Remove net balancing from generator L1; leave storage magnitude panels unchanged."""
        import numpy as np
        result = dict(absolute)
        for key in ('matrix', 'tensor'):
            values = absolute[key].copy()
            opposing = (values[0] - np.abs(signed[key][0])) / 2
            if np.nanmin(opposing) < -1e-8:
                raise ValueError('Net dispatch exceeds generator L1')
            values[0] = np.maximum(opposing, 0)
            result[key] = values
        return result


    def plot_opposing_heatmaps(data, weekday=None):
        fig = plot_heatmaps(data, weekday)
        ax = next(ax for ax in fig.axes if ax.images)
        ax.set_title('Opposing generator redispatch · net balancing removed', loc='left', weight='bold')
        ax.images[0].colorbar.set_label('(Σ |ΔPg| − |Σ ΔPg|) / 2 (MW)')
        return fig

    opposing_snapshot = opposing_calendar_data(snapshot, signed_snapshot)
    return opposing_snapshot, plot_opposing_heatmaps


@app.cell(hide_code=True)
def _(
    heatmap_mode,
    opposing_snapshot,
    plot_heatmaps,
    plot_opposing_heatmaps,
    plot_signed_heatmaps,
    signed_snapshot,
    snapshot,
):
    heatmap_modes = {
        'Signed net (AC − DC)': (
            signed_snapshot, plot_signed_heatmaps,
            'Seismic: blue negative, white zero, red positive. Symmetric per-metric limits match Matrix view and remain fixed across weekdays.'),
        'Absolute magnitude': (
            snapshot, plot_heatmaps,
            'Viridis: sum of absolute device changes, from zero to the observed maximum. Per-metric limits match Matrix view and remain fixed across weekdays.'),
        'Opposing generator redispatch': (
            opposing_snapshot, plot_opposing_heatmaps,
            'Generator panel: (Σ|ΔPg| − |ΣΔPg|)/2 in MW, the smaller of total upward and downward redispatch. Net balancing is removed; paired movements count once. Battery and SoC panels retain absolute magnitudes. Viridis starts at zero, with fixed per-metric scales across weekdays. This measures selected redispatch, not required repair or congestion alone.'),
    }
    heatmap_data, heatmap_renderer, heatmap_scale_note = heatmap_modes[heatmap_mode.value]
    return heatmap_data, heatmap_renderer, heatmap_scale_note


@app.cell
def _(heatmap_data, heatmap_renderer, plt):
    matrix_figure = heatmap_renderer(heatmap_data)
    plt.close(matrix_figure)
    return (matrix_figure,)


@app.cell
def _(mo):
    weekday_slider = mo.ui.slider(start=0, stop=6, step=1, value=0,
                                 label="Monday ← Day of week → Sunday", full_width=True)
    return (weekday_slider,)


@app.cell
def _(heatmap_data, heatmap_renderer, plt, weekday_slider):
    tensor_figure = heatmap_renderer(heatmap_data, int(weekday_slider.value))
    plt.close(tensor_figure)
    return (tensor_figure,)


@app.cell(hide_code=True)
def _(mo):
    get_heatmap_tab, set_heatmap_tab = mo.state("Matrix")
    return get_heatmap_tab, set_heatmap_tab


@app.cell(hide_code=True)
def _(
    WEEKDAYS,
    get_heatmap_tab,
    heatmap_mode,
    heatmap_scale_note,
    matrix_figure,
    mo,
    set_heatmap_tab,
    tensor_figure,
    weekday_slider,
):
    heatmap_tabs = mo.ui.tabs({
        "Matrix": mo.vstack([
            heatmap_mode,
            matrix_figure,
            mo.md(heatmap_scale_note),
        ]),
        "Tensor": mo.vstack([
            heatmap_mode,
            weekday_slider,
            mo.md(f"### {WEEKDAYS[int(weekday_slider.value)]}"),
            tensor_figure,
            mo.md(heatmap_scale_note + " Weeks begin Monday."),
        ]),

    }, value=get_heatmap_tab(), on_change=set_heatmap_tab)
    heatmap_tabs
    return


@app.cell(hide_code=True)
def _():
    from experiments.case118_annual_hierarchy.analysis.dashboard_stress import load_stress_data, stress_tables, plot_stress_matrix, plot_stress_pairs

    return (
        load_stress_data,
        plot_stress_matrix,
        plot_stress_pairs,
        stress_tables,
    )


@app.cell(hide_code=True)
def _(mo):
    stress_rounding = mo.ui.checkbox(value=True, label="Collapse numerical noise before ranking")
    stress_exclude_interventions = mo.ui.checkbox(value=False, label="Exclude operator-intervention intervals")
    mo.vstack([
        mo.md("## DC stress and AC adjustments\nExplore correlations and pairwise relationships using the same correction snapshot as the heatmaps. The annual DC covariates are fixed and aligned by interval; historical coverage membership is not used."),
        mo.hstack([stress_rounding, stress_exclude_interventions]),
    ])
    return stress_exclude_interventions, stress_rounding


@app.cell(hide_code=True)
def _(load_stress_data, snapshot):
    stress_data = load_stress_data(snapshot)
    return (stress_data,)


@app.cell(hide_code=True)
def _(
    stress_data,
    stress_exclude_interventions,
    stress_rounding,
    stress_tables,
):
    stress_results = stress_tables(
        stress_data,
        collapse_noise=stress_rounding.value,
        exclude_interventions=stress_exclude_interventions.value,
    )
    return (stress_results,)


@app.cell(hide_code=True)
def _(
    mo,
    plot_stress_matrix,
    plot_stress_pairs,
    plt,
    stress_data,
    stress_results,
):
    stress_matrix_figure = plot_stress_matrix(stress_results)
    stress_pairs_figure = plot_stress_pairs(stress_results)
    plt.close(stress_matrix_figure)
    plt.close(stress_pairs_figure)
    mo.vstack([
        mo.md(f"**{len(stress_results['frame']):,} / {stress_data['horizon']:,} intervals** · correction snapshot: {stress_data['snapshot_utc']}"),
        mo.ui.tabs({"Correlation matrix": stress_matrix_figure, "Pairwise relationships": stress_pairs_figure}),
        mo.md("""
    **Reading the matrix:** raw Spearman correlations versus correlations of ranks after removing
    month × hour × weekday/weekend group means. Colors share a fixed −1 to +1 scale.
    When enabled, rounding uses 0.001 MW/MWh and 0.0001 for normalized fractions; it does not change physical data or solver tolerances.
    The maximum-utilization row is blank when saturated near 100%, since ranks of tiny bound slacks are not useful stress evidence.

    **Reading the scatterplots:** every point is one completed interval in original physical units.
    Color shows renewable share, branch count, or study hour. Black squares are equal-width-bin medians
    where at least 20 observations exist; they are not fitted causal curves or confidence intervals.
    The last panel compares inherited and end-of-hour SoC divergence: a retrospective relationship,
    not an independent DC-only predictor. Adjusted coefficients are annotations, not slopes fitted to the raw points.

    All relationships are descriptive, autocorrelated, and conditional on this scenario/controller.
    They do not establish AC necessity, cost benefit, or validated predictive performance.
    The annual DC feature table is fixed; its old completion mask is ignored.
    """),
    ])
    return


@app.cell(hide_code=True)
def _(mo, plt, snapshot, stress_data, stress_results):
    def plot_signed_ramp_battery(data, selected_indices, delta_hours):
        """Backward net-load ramp versus current executed AC fleet power."""
        import numpy as np
        import pandas as pd

        frame = data["frame"].sort_index().copy()
        timestamps = pd.to_datetime(frame["timestamp"], utc=True)
        if not np.allclose(timestamps.diff().dt.total_seconds().iloc[1:] / 3600, delta_hours):
            raise ValueError("Signed ramp requires consecutive hourly source observations")
        # Differentiate the full timeline before applying any intervention filter.
        frame["signed_net_load_ramp_mw_per_hour"] = frame["net_load_mw"].diff() / delta_hours
        frame["ac_battery_power_mw"] = (
            frame["aggregate_discharging_mw"] - frame["aggregate_charging_mw"]
            + frame["battery_net_change_mw"]
        )
        frame = frame.loc[selected_indices].dropna(subset=["signed_net_load_ramp_mw_per_hour"])
        if not np.isfinite(frame[["signed_net_load_ramp_mw_per_hour", "ac_battery_power_mw"]]).all().all():
            raise ValueError("Nonfinite ramp or battery power")
        rho = frame["signed_net_load_ramp_mw_per_hour"].rank().corr(frame["ac_battery_power_mw"].rank())
        fig, ax = plt.subplots(figsize=(12, 7), layout="constrained")
        points = ax.scatter(
            frame["signed_net_load_ramp_mw_per_hour"], frame["ac_battery_power_mw"],
            c=frame["study_hour"], cmap="twilight", vmin=0, vmax=23,
            s=12, alpha=.45, edgecolors="none", rasterized=True,
        )
        ax.axhline(0, color="#555555", lw=.8, alpha=.65)
        ax.axvline(0, color="#555555", lw=.8, alpha=.65)
        ax.set_xlabel("Signed net-load ramp (MW/hour) · falling ← 0 → rising")
        ax.set_ylabel("Executed AC fleet battery power (MW) · charging < 0 < discharging")
        ax.set_title(f"Signed net-load ramp versus battery power\n{len(frame):,} hourly observations · raw Spearman ρ = {rho:+.2f}")
        ax.grid(alpha=.12)
        fig.colorbar(points, ax=ax, label="Study hour (UTC)", ticks=[0, 6, 12, 18, 23])
        return frame, fig

    signed_ramp_battery_data, signed_ramp_battery_figure = plot_signed_ramp_battery(
        stress_data, stress_results["frame"].index, snapshot["report"]["delta_hours"],
    )
    plt.close(signed_ramp_battery_figure)
    mo.vstack([
        mo.md("## Signed net-load ramp and battery power"),
        signed_ramp_battery_figure,
        mo.md("Net load = gross load − available renewables. The x-axis is (net load at t − net load at t−1) / 1 hour; the y-axis is signed executed AC power at t, summed across all batteries. AC power is reconstructed as DC discharge − DC charge + the signed AC−DC power change. The first hour is omitted because no preceding hour is available. The intervention-exclusion checkbox applies; this plot and its raw rank correlation use unrounded values. This is a descriptive contemporaneous association."),
    ])
    return


@app.cell
def _(mo, snapshot):
    mo.vstack([
        mo.md("## Completion percentage by wall time\nReuses the review task’s completion collector and intervention-band plot."),
        mo.image(src=snapshot['path'] / 'completion/completion_walltime_band.png'),
        mo.md("Calendar time includes pauses. Historical points use archive-mtime proxies; newer advances use retained UTC/monotonic anchors. The shaded intervention band is descriptive, not a causal attribution."),
        mo.accordion({"Zoom since September 14": mo.image(src=snapshot['path'] / 'assets/completion_zoom.png')}),
    ])
    return


@app.cell(hide_code=True)
def _(plt, snapshot):
    def plot_notebook_solve_times(data):
        """Render retained solve-call data with distinct green/charcoal categories."""
        import csv
        import json
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        import matplotlib.dates as mdates
        from matplotlib.lines import Line2D
        from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter

        with (data['path'] / 'solves/solve_times.csv').open() as stream:
            rows = list(csv.DictReader(stream))
        events = json.loads((data['path'] / 'events.json').read_text())
        palette = {'Primary': '#0072B2', 'Target-free': '#D55E00',
                   'Copied target-free': '#009E73', 'Perturbed starts': '#222222'}
        markers = {'Primary': 'o', 'Target-free': 's',
                   'Copied target-free': 'D', 'Perturbed starts': '*'}
        sizes = {'Primary': 10, 'Target-free': 22,
                 'Copied target-free': 24, 'Perturbed starts': 48}
        tz = ZoneInfo('America/Los_Angeles')
        end = datetime.fromisoformat(data['solves']['snapshot_finished_utc'])
        fig, ax = plt.subplots(figsize=(12, 5.6))
        for role, color in palette.items():
            for status in ('returned', 'censored'):
                group = [r for r in rows if r['role'] == role and r['status'] == status]
                ax.scatter([datetime.fromisoformat(r['utc']) for r in group],
                           [float(r['minutes']) for r in group], color=color,
                           marker=markers[role] if status == 'returned' else 'x',
                           s=sizes[role] if status == 'returned' else 30,
                           alpha=.7, linewidths=.8)
        for event in events:
            ax.axvline(datetime.fromisoformat(event['utc']), color=event['color'],
                       ls=event['linestyle'], lw=1.4)
        fig.legend(handles=[Line2D([], [], color=e['color'], ls=e['linestyle'], label=e['label'])
                            for e in events], loc='upper center', bbox_to_anchor=(.52, .98),
                   ncol=3, frameon=False, fontsize=11)
        ax.legend(handles=[Line2D([], [], color=color, marker=markers[role], ls='', label=role)
                           for role, color in palette.items()] +
                          [Line2D([], [], color='#555555', marker='x', ls='', label='Terminated (any role)')],
                  frameon=False, ncol=5,
                  loc='upper center', bbox_to_anchor=(.5, 1.16), fontsize=9)
        ax.set_yscale('log')
        ax.set_ylim(.08, max(150, max(float(r['minutes']) for r in rows)*1.2))
        ax.yaxis.set_major_locator(FixedLocator([.1,.3,1,3,10,30,100]))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, pos: f'{value:g}'))
        ax.yaxis.set_minor_formatter(NullFormatter())
        ax.set_ylabel('Solve-call minutes (log scale)')
        ax.set_xlim(datetime(2026, 9, 14, 10, tzinfo=tz), end+timedelta(minutes=15))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator(tz=tz, minticks=5, maxticks=8))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%b %d\n%H:%M', tz=tz))
        ax.set_xlabel('Wall-clock time (PDT)')
        ax.grid(axis='y', alpha=.2)
        fig.text(.09,.025, f'Snapshot {end.astimezone(tz):%b %d, %H:%M PDT}. Role markers: returned calls, including failures. ×: terminated/censored calls; color identifies role.\n'
                 'Canonicalization included, construction excluded. Legacy wall times use phase-file mtime proxies. *Fan: approximate.',
                 fontsize=9, color='#555555', va='bottom')
        fig.subplots_adjust(left=.09, right=.98, top=.76, bottom=.22)
        return fig

    solve_times_figure = plot_notebook_solve_times(snapshot)
    plt.close(solve_times_figure)
    return (solve_times_figure,)


@app.cell
def _(mo, snapshot, solve_times_figure):
    mo.vstack([
        mo.md("## Timing and throughput\nPreserved from the final toy snapshot; promoted collectors and `render_saved_timing.py` retain the timing definitions; Solve times uses the same retained CSV with a notebook-specific high-contrast palette."),
        mo.ui.tabs({
            "Solve times": solve_times_figure,
            "Rolling P95": mo.image(src=snapshot['path'] / 'assets/period_p95.png'),
            "Time / log-time": mo.image(src=snapshot['path'] / 'assets/post_reboot_distribution.png'),
            "Two-worker throughput": mo.image(src=snapshot['path'] / 'assets/parallel_throughput.png'),
        }),
        mo.md("""
        **Solve calls:** canonicalization included, construction excluded; terminated/censored calls use ×; returned calls use role-specific circles, squares, diamonds, or stars.
        **Period latency:** first contender launch to final reap for speculative execution; overlapping work counts once.
        Special interrupted/retried or operator-inserted periods are excluded from the ordinary latency distribution,
        but accepted physical intervals still count toward completion.
        **P95:** trailing two-hour window, at least 20 completed periods, every five minutes.
        **Throughput:** completions per complete five-minute bin × 12, summed across shards; zero bins included.
        Unfinished periods are not yet latency observations.
        """),
        mo.accordion({"Timing snapshot definitions and exclusions": mo.json({
            'periods': {k:v for k,v in snapshot['periods'].items() if k != 'input_sha256'},
            'solves': {k:v for k,v in snapshot['solves'].items() if k != 'file_references'},
        })}),
    ])
    return


if __name__ == "__main__":
    app.run()
