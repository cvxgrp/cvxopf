# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "marimo==0.24.2",
#     "numpy>=2.0",
#     "pandas>=2.2",
#     "plotly>=6.0",
# ]
# ///

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def imports():
    import marimo as mo
    import hashlib
    from pathlib import Path
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    return Path, go, hashlib, make_subplots, mo, np, pd


@app.cell(hide_code=True)
def intro(mo):
    mo.md(r"""
    # Tracy 2021: weekly net load

    Each point is a **168-hour window**, starting at midnight in fixed UTC−08:00.
    The 359 windows overlap and stay wholly within 2021.

    **Net load = load − utility solar − wind − distributed solar.** The three axes
    are signed total **net energy (GWh)**, **peak hourly net load (MW)**, and
    **minimum hourly net load (MW)**. A more negative minimum means a larger
    instantaneous available renewable surplus. These are input metrics before
    curtailment, network losses, or storage dispatch.

    The annual heatmaps show day of year horizontally and hour of day vertically.
    Rotate the 3D view, hover over any point for dates and all three metrics, or
    zoom the 2D projections. Candidate labels reproduce the earlier analysis;
    “moderate” means median, while “balance” means net energy nearest zero.
    """)
    return


@app.cell(hide_code=True)
def source(Path, hashlib, mo, np, pd):
    source_path = Path(mo.notebook_location()).parent / "experiments/battery_terminal/data/9q9wtp_gen_and_load.csv"
    expected_sha256 = "45e11f061d736741b18334aea0e9525c355c1a13068c291c1db6ed2e614b1b6f"
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert source_sha256 == expected_sha256, "Tracy source differs from the identified data."
    source_channels = ["9q9wtp_load", "9q9wtp_solar", "9q9wtp_wind", "9q9wtp_dist_solar"]
    source_frame = pd.read_csv(source_path)
    source_frame["time"] = pd.to_datetime(source_frame["time"])
    tracy_2021 = source_frame.set_index("time").loc["2021", source_channels].copy()
    expected_times = pd.date_range("2021-01-01", periods=8760, freq="h", tz="Etc/GMT+8")
    assert np.array_equal(tracy_2021.index.asi8, expected_times.asi8)
    assert np.isfinite(tracy_2021.to_numpy()).all()
    assert (tracy_2021.to_numpy() >= 0).all()
    net_load = tracy_2021[source_channels[0]] - tracy_2021[source_channels[1:]].sum(axis=1)
    return net_load, source_path, source_sha256, tracy_2021


@app.cell(hide_code=True)
def metrics(net_load, np, pd):
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
def controls(mo, raw_weeks):
    scale_picker = mo.ui.dropdown(
        options=["Raw Tracy", "Existing Case9 scale", "Planned Case118 scale"],
        value="Raw Tracy", label="Magnitude scale", allow_select_none=False,
    )
    show_candidates = mo.ui.checkbox(value=True, label="Label candidate weeks")
    week_picker = mo.ui.dropdown(
        options=raw_weeks.start_date.tolist(), value="2021-12-17",
        label="Inspect week starting", allow_select_none=False, searchable=True,
    )
    mo.hstack([scale_picker, show_candidates, week_picker], justify="start", gap=2, wrap=True)
    return scale_picker, show_candidates, week_picker


@app.cell(hide_code=True)
def scaled(mo, net_load, raw_weeks, scale_picker, week_picker):
    scale_factors = {
        "Raw Tracy": 1.0,
        "Existing Case9 scale": 315 / 1138.7624473656565,
        "Planned Case118 scale": 6000 / net_load.max(),
    }
    scale_factor = scale_factors[scale_picker.value]
    metric_columns = ["net_energy_gwh", "peak_net_load_mw", "minimum_net_load_mw"]
    weeks = raw_weeks.assign(**{column: raw_weeks[column] * scale_factor for column in metric_columns})
    axis_labels = {
        "net_energy_gwh": "Net energy (GWh)",
        "peak_net_load_mw": "Peak net load (MW)",
        "minimum_net_load_mw": "Minimum net load (MW)",
    }
    inspected_week = weeks.loc[weeks.start_date.eq(week_picker.value)].iloc[0]
    mo.md(f"**{scale_picker.value}** · common multiplier **{scale_factor:.6g}** · all rankings are unchanged. ")
    return axis_labels, inspected_week, metric_columns, scale_factor, weeks


@app.cell(hide_code=True)
def input_heatmaps(
    go,
    inspected_week,
    make_subplots,
    mo,
    net_load,
    np,
    pd,
    scale_factor,
    scale_picker,
    tracy_2021,
):
    def hour_day_matrix(series):
        # cvx-sd heatmap convention: fixed standard time, midnight anchor,
        # physical delta=3600 seconds, chronological samples filling day columns.
        delta_seconds = (series.index[1] - series.index[0]).total_seconds()
        samples_per_day = int(86400 / delta_seconds)
        assert delta_seconds == 3600 and series.index[0].hour == 0
        matrix = series.to_numpy().reshape(samples_per_day, -1, order="F")
        assert matrix.shape == (24, 365)
        # Independent calendar-index pivot checks both axis orientation and values.
        calendar = pd.DataFrame({"day": series.index.strftime("%Y-%m-%d"), "hour": series.index.hour, "value": series.to_numpy()})
        assert np.array_equal(matrix, calendar.pivot(index="hour", columns="day", values="value").to_numpy())
        return matrix

    def input_heatmap_figure(frame, net, factor, chosen, scale_name):
        signals = [
            ("Load", frame["9q9wtp_load"], False),
            ("Available utility solar", frame["9q9wtp_solar"], False),
            ("Available wind", frame["9q9wtp_wind"], False),
            ("Available distributed solar", frame["9q9wtp_dist_solar"], False),
            ("Total available renewables", frame[["9q9wtp_solar", "9q9wtp_wind", "9q9wtp_dist_solar"]].sum(axis=1), False),
            ("Net load · positive demand / negative surplus", net, True),
        ]
        day_axis = (frame.index[::24] + pd.Timedelta(hours=12)).strftime("%Y-%m-%d %H:%M").tolist()
        chart = make_subplots(rows=len(signals), cols=1, shared_xaxes=True,
                              vertical_spacing=.04, subplot_titles=[item[0] for item in signals])
        for row, (label, values, signed) in enumerate(signals, start=1):
            matrix = hour_day_matrix(values) * factor
            extent = float(np.max(np.abs(matrix)))
            yaxis_key = "yaxis" if row == 1 else f"yaxis{row}"
            bottom, top = chart.layout[yaxis_key].domain
            chart.add_trace(go.Heatmap(
                x=day_axis, y=np.arange(24), z=matrix,
                colorscale="RdBu_r" if signed else "Viridis",
                zmin=-extent if signed else 0, zmax=extent, zsmooth=False,
                colorbar={"title": {"text": "MW"}, "len": top-bottom, "y": (bottom+top)/2,
                          "thickness": 13, "x": 1.01, "yanchor": "middle", "tickfont": {"size": 10}},
                hovertemplate=f"<b>{label}</b><br>" + "%{x|%b %d, %Y} · hour %{y:02d}<br>%{z:,.2f} MW<extra></extra>",
            ), row=row, col=1)
            chart.update_yaxes(title_text="Hour (UTC−08)", tickvals=[0,6,12,18,23],
                               ticktext=["00","06","12","18","23"], range=[-.5,23.5], row=row, col=1)
            chart.add_vrect(x0=chosen.start.strftime("%Y-%m-%d"),
                            x1=(chosen.end + pd.Timedelta(hours=1)).strftime("%Y-%m-%d"),
                            line_width=1.5, line_color="#e58d26", fillcolor="rgba(0,0,0,0)", row=row, col=1)
        chart.update_xaxes(type="date", dtick="M1", tickformat="%b", range=["2021-01-01", "2022-01-01"],
                           showgrid=False)
        chart.update_xaxes(title_text="Day in 2021 · fixed UTC−08:00", row=len(signals), col=1)
        chart.update_layout(height=1480, template="plotly_white", showlegend=False,
                            margin={"l":75,"r":85,"t":70,"b":50},
                            title=f"Tracy electrical inputs · {scale_name}", uirevision="tracy-heatmaps")
        return chart

    heatmap_figure = input_heatmap_figure(tracy_2021, net_load, scale_factor, inspected_week, scale_picker.value)
    mo.vstack([
        mo.md("## Hour-by-day input heatmaps\nEach column is one day and each row one hour. Hover for the exact value. "
              "Nonnegative inputs use zero-based, per-panel color ranges; net load uses a symmetric scale centered on zero "
              "(blue surplus, red demand). The orange outline marks the inspected week. All values are MW."),
        mo.as_html(heatmap_figure),
    ])
    return


@app.cell(hide_code=True)
def candidates(pd, weeks):
    def candidate_periods(frame):
        rules = [
            ("Energy low", "net_energy_gwh", 0, "#197a62"),
            ("Energy median", "net_energy_gwh", .5, "#2666a3"),
            ("Energy high", "net_energy_gwh", 1, "#b54836"),
            ("Peak low", "peak_net_load_mw", 0, "#197a62"),
            ("Peak median", "peak_net_load_mw", .5, "#2666a3"),
            ("Peak high", "peak_net_load_mw", 1, "#b54836"),
            ("Deepest surplus", "minimum_net_load_mw", 0, "#b57816"),
            ("Shallowest minimum", "minimum_net_load_mw", 1, "#71589e"),
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
def plot_helpers(go):
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
            for candidate in candidate_frame.to_dict("records"):
                positions = dict(zip(["x", "y", "z"][:dimension], [[candidate[column]] for column in axes]))
                chart.add_trace(trace_type(
                    **positions, mode="markers+text", name=candidate["candidate"],
                    text=[candidate["candidate"]], textposition="top center",
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
                            uirevision="tracy-weekly", hoverlabel={"namelength": -1})
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
def three_dimensions(
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
def energy_peak(
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
def energy_minimum(
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
def peak_minimum(
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
def detail(
    go,
    inspected_week,
    make_subplots,
    mo,
    net_load,
    np,
    scale_factor,
    tracy_2021,
):
    inspected_inputs = tracy_2021.loc[inspected_week.start:inspected_week.end] * scale_factor
    inspected_net_load = net_load.loc[inspected_week.start:inspected_week.end] * scale_factor
    figure_detail = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.15,
        subplot_titles=["Load and net load", "Available renewables · stacked"])

    def add_week_trace(chart, values, label, color, row, stacked=False):
        chart.add_trace(go.Scatter(
            x=np.arange(len(values)), y=values.to_numpy(), name=label, mode="lines",
            line={"color": color, "width": 1 if stacked else 2},
            stackgroup="renewables" if stacked else None,
            customdata=values.index.strftime("%Y-%m-%d %H:%M %z"),
            hovertemplate="%{customdata}<br>" + label + ": %{y:,.2f} MW<extra></extra>",
        ), row=row, col=1)

    add_week_trace(figure_detail, inspected_inputs["9q9wtp_load"], "Load", "#303b4a", 1)
    add_week_trace(figure_detail, inspected_net_load, "Net load", "#2666a3", 1)
    figure_detail.data[1].update(fill="tonexty", fillcolor="rgba(38, 102, 163, 0.5)")
    add_week_trace(figure_detail, inspected_inputs["9q9wtp_dist_solar"], "DG solar", "#cb7043", 2, stacked=True)
    add_week_trace(figure_detail, inspected_inputs["9q9wtp_solar"], "Utility solar", "#e8ac32", 2, stacked=True)
    add_week_trace(figure_detail, inspected_inputs["9q9wtp_wind"], "Utility wind", "#389d9b", 2, stacked=True)
    figure_detail.add_hline(y=0, line_color="#777777", line_width=1, row=1, col=1)
    figure_detail.update_yaxes(title_text="Power (MW)")
    figure_detail.update_yaxes(rangemode="tozero", row=2, col=1)
    figure_detail.update_xaxes(range=[0,167], dtick=24)
    figure_detail.update_xaxes(title_text="Hour in window", row=2, col=1)
    figure_detail.update_layout(template="plotly_white", height=700,
        title=f"Inspected week: {inspected_week['dates']}", hovermode="x unified",
        legend={"orientation": "h", "traceorder": "normal", "x": 0, "y": -.15, "yanchor": "top"},
        margin={"l": 65, "r": 25, "t": 90, "b": 110})
    mo.vstack([
        mo.md("## Inspect a week\nChoose its start date above. Gross load and net load share the upper panel; "
              "the lower panel stacks available utility solar, utility wind, and distributed-generation (DG) solar. "
              "The stack total equals load minus net load, before curtailment. Negative net load indicates available surplus. "
              "Both panels follow the selected magnitude scale."),
        mo.hstack([
            mo.stat(value=f"{inspected_week.net_energy_gwh:,.3f} GWh", label="Net energy", bordered=True),
            mo.stat(value=f"{inspected_week.peak_net_load_mw:,.2f} MW", label="Peak net load", bordered=True),
            mo.stat(value=f"{inspected_week.minimum_net_load_mw:,.2f} MW", label="Minimum net load", bordered=True),
            mo.stat(value=f"{int(inspected_week.positive_net_hours)} / 168", label="Positive-net hours", bordered=True),
        ], justify="start", gap=1, wrap=True),
        mo.ui.plotly(figure_detail),
    ])
    return


@app.cell(hide_code=True)
def tables(axis_labels, candidates, metric_columns, mo, weeks):
    candidate_table = candidates[["candidate", "dates", *metric_columns, "positive_net_hours"]].rename(columns={
        **axis_labels, "candidate": "Candidate", "dates": "2021 interval", "positive_net_hours": "Positive-net hours",
    })
    mo.vstack([
        mo.md("## Candidate periods\nExtrema and medians are selected separately for each metric. Earliest start breaks ties."),
        mo.ui.table(candidate_table.round(3), selection=None),
        mo.accordion({"All 359 windows": mo.ui.table(weeks[["dates", *metric_columns, "positive_net_hours"]].rename(columns=axis_labels).round(3), selection=None)}),
    ])
    return


@app.cell(hide_code=True)
def provenance(mo, source_path, source_sha256):
    mo.accordion({"Definitions and data checks": mo.md(f"""
    All four input channels passed finite/nonnegative checks, and 2021 contains the
    expected **8,760 unique consecutive hours**. The three weekly metrics were
    cross-checked against pandas rolling reductions. Energy uses a one-hour timestep.

    The **minimum** is the minimum hourly net **power**, not minimum cumulative
    energy. A negative weekly total does not eliminate positive-net hours.

    Common scaling multiplies all four source channels equally. It preserves dates,
    relative shapes, and rankings. The Case9 factor is `315 / 1138.7624473656565`;
    the planned Case118 factor makes the full-year peak net load 6,000 MW.
    No resource caps, network allocation, curtailment, or solved dispatch enter these plots.

    Source: `{source_path}`  
    SHA-256: `{source_sha256}`

    The source CSV is local and git-ignored. This notebook recomputes from that source;
    it does not depend on the temporary screening outputs.
    """)})
    return


if __name__ == "__main__":
    app.run()
