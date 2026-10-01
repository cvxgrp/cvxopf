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
    import gzip
    import hashlib
    import json
    import re
    from pathlib import Path
    from functools import lru_cache
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    return Path, go, gzip, hashlib, json, lru_cache, make_subplots, mo, np, pd, re


@app.cell(hide_code=True)
def introduction(mo):
    mo.md("""
    # Tracy DC comparison results

    Select a segment, then compare any combination of **network formulation**,
    **generator curvature**, and **battery throughput penalty**. Selections form
    a Cartesian product (up to 12 runs per segment). Click legend entries to hide
    traces; drag to zoom; double-click to reset. All dates use **fixed UTC−08:00**.

    This notebook reads the completed **5,000-iteration-cap batch**, not the earlier
    stopped runs. It never builds or solves an optimization problem. “Off” means
    ρ = 0.001, not zero curvature. Cost values across different coefficients are
    not directly comparable evidence of better operation. DC results do not
    establish AC feasibility. No synthetic fallback is used for missing artifacts.
    """)
    return


@app.cell(hide_code=True)
def source(Path, hashlib, json, mo, np, pd, re):
    experiment = Path(mo.notebook_location())
    run_directory = experiment / "results/stage_b_maxiter5000"
    mo.stop(
        not (run_directory / "study-result.json").exists(),
        mo.md(
            f"**Results unavailable:** expected `{run_directory}`. Copy the retained run and Stage A inputs here; this notebook will not launch a run."
        ),
    )
    binding = json.loads((run_directory / "binding.json").read_text())
    run_record = json.loads((run_directory / "study-result.json").read_text())
    assert (
        run_record["classification"] == "complete" and len(run_record["accepted"]) == 72
    )
    manifest_path = experiment / "stage_a/manifest.json"
    assert (
        hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        == binding["context"]["stage_a_manifest_sha256"]
    )
    stage_a = json.loads(manifest_path.read_text())
    tables = {}
    for name in ("generators", "batteries", "renewables", "buses"):
        table_path = experiment / f"stage_a/{name}.csv"
        assert (
            hashlib.sha256(table_path.read_bytes()).hexdigest()
            == stage_a["review_artifacts"][table_path.name]
        )
        tables[name] = pd.read_csv(table_path)
    aggregate_path = experiment / "results/stage_a_active_inputs/aggregate_inputs.csv"
    assert (
        hashlib.sha256(aggregate_path.read_bytes()).hexdigest()
        == stage_a["raw_artifacts"][aggregate_path.name]
    )
    aggregate_inputs = pd.read_csv(aggregate_path, index_col=0, parse_dates=True)
    network_path = (
        experiment.parent / "case118_annual_hierarchy/source/pglib_opf_case118_ieee.m"
    )
    assert (
        hashlib.sha256(network_path.read_bytes()).hexdigest() == stage_a["pglib_sha256"]
    )
    branch_text = re.search(
        r"mpc\.branch\s*=\s*\[(.*?)\n\s*\];", network_path.read_text(), re.S
    ).group(1)
    branches = np.array(
        [
            [float(v) for v in line.split("%")[0].strip().rstrip(";").split()]
            for line in branch_text.splitlines()
            if line.split("%")[0].strip()
        ]
    )
    branches = branches[branches[:, 10] > 0]
    arms_table = pd.DataFrame(
        [dict(number=r["number"], **r["arm"]) for r in run_record["accepted"]]
    )
    mo.md(
        f"**72 accepted arms · execution commit `{binding['context']['commit'][:9]}` · CLARABEL / SCIPY**"
    )
    return (
        aggregate_inputs,
        arms_table,
        binding,
        branches,
        run_directory,
        run_record,
        tables,
    )


@app.cell(hide_code=True)
def selection(arms_table, mo):
    segment = mo.ui.dropdown(
        arms_table.window.unique().tolist(), value="Regional import", label="Segment"
    )
    network_models = mo.ui.multiselect(
        {"Single-node": "singlenode_dc", "Network (lossy DC)": "lossy_dc"},
        value=["Single-node", "Network (lossy DC)"],
        label="Network model",
    )
    generator_models = mo.ui.multiselect(
        {"On · ρ = 1/3": 1 / 3, "Off · ρ = 0.001": 0.001},
        value=["On · ρ = 1/3", "Off · ρ = 0.001"],
        label="Generator model",
    )
    battery_models = mo.ui.multiselect(
        {"Low · λ = 0.0001": 0.0001, "Medium · λ = 0.01": 0.01, "High · λ = 1": 1.0},
        value=["Medium · λ = 0.01"],
        label="Battery model",
    )
    mo.hstack(
        [segment, network_models, generator_models, battery_models], widths="equal"
    )
    return battery_models, generator_models, network_models, segment


@app.cell(hide_code=True)
def loader(gzip, hashlib, json, lru_cache, run_directory):
    @lru_cache(maxsize=12)
    def load_arm(number):
        directory = run_directory / f"arm-{number:03d}"
        completion = json.loads((directory / "completion.json").read_text())
        raw = (directory / "result.json.gz").read_bytes()
        assert hashlib.sha256(raw).hexdigest() == completion["result_sha256"]
        record = json.loads(gzip.decompress(raw))
        assert record["iteration"] == number and record["classification"] == "accepted"
        assert record["audit"]["passed"]
        return record

    return (load_arm,)


@app.cell(hide_code=True)
def chosen(
    aggregate_inputs,
    arms_table,
    battery_models,
    generator_models,
    load_arm,
    mo,
    network_models,
    pd,
    segment,
):
    selected_arms = arms_table.loc[
        (arms_table.window == segment.value)
        & arms_table.formulation.isin(network_models.value)
        & arms_table.rho.isin(generator_models.value)
        & arms_table.throughput.isin(battery_models.value)
    ]
    mo.stop(
        selected_arms.empty, mo.md("Select at least one option in each model selector.")
    )
    runs = [load_arm(int(n)) for n in selected_arms.number]
    start_hour, stop_hour = (
        int(selected_arms.iloc[0].start),
        int(selected_arms.iloc[0].stop),
    )
    input_block = aggregate_inputs.iloc[start_hour:stop_hour]
    # Strip timezone only after converting to the fixed source timezone: Plotly
    # must not convert these values to the browser's local/DST timezone.
    times = input_block.index.tz_convert("Etc/GMT+8").tz_localize(None)
    boundary_times = pd.date_range(times[0], periods=len(times) + 1, freq="h")
    mo.md(
        f"### {segment.value} · {times[0]:%b %d}–{boundary_times[-1]:%b %d, %Y} (exclusive stop) · {len(times):,} hours\n\nComparing **{len(runs)}** runs. Power values refer to hourly interval starts; SoC is plotted at boundaries, including the initial state."
    )
    return boundary_times, input_block, runs, times


@app.cell(hide_code=True)
def plotting(go, make_subplots):
    palette = [
        "#0072B2",
        "#D55E00",
        "#009E73",
        "#CC79A7",
        "#E69F00",
        "#332288",
        "#56B4E9",
        "#882255",
        "#44AA99",
        "#AA4499",
        "#999933",
        "#661100",
    ]

    def run_label(record):
        arm = record["arm"]
        network = "Network" if arm["formulation"] == "lossy_dc" else "Single-node"
        curvature = "on" if arm["rho"] > 0.1 else "off"
        battery = {0.0001: "low", 0.01: "medium", 1.0: "high"}[arm["throughput"]]
        return f"{network} · ρ {curvature} · λ {battery}"

    def run_color(record):
        arm = record["arm"]
        idx = (6 if arm["formulation"] == "lossy_dc" else 0) + (
            3 if arm["rho"] < 0.1 else 0
        )
        return palette[idx + {0.0001: 0, 0.01: 1, 1.0: 2}[arm["throughput"]]]

    def figure(titles, units):
        fig = make_subplots(
            rows=len(titles),
            cols=1,
            shared_xaxes=True,
            subplot_titles=titles,
            vertical_spacing=0.09,
        )
        fig.update_layout(
            height=250 * len(titles) + 130,
            template="plotly_white",
            hovermode="x unified",
            margin=dict(t=55, b=100),
            legend=dict(orientation="h", y=-0.15),
        )
        for row, unit in enumerate(units, 1):
            fig.update_yaxes(title_text=unit, row=row, col=1)
        fig.update_xaxes(title_text="2021 · fixed UTC−08:00", row=len(titles), col=1)
        return fig

    def line(fig, x, y, label, color, row=1, dash="solid", show=True):
        fig.add_trace(
            go.Scatter(
                x=x,
                y=y,
                name=label,
                mode="lines",
                line=dict(color=color, dash=dash, width=1.5),
                legendgroup=label,
                showlegend=show,
            ),
            row=row,
            col=1,
        )

    return figure, line, run_color, run_label


@app.cell(hide_code=True)
def input_plot(figure, input_block, line, mo, times):
    input_figure = figure(
        ["Data inputs", "Available net load before curtailment or storage"],
        ["MW", "MW"],
    )
    for column, input_label, input_color in [
        ("load_mw", "Gross load", "#222222"),
        ("solar_mw", "Utility solar", "#E69F00"),
        ("wind_mw", "Wind", "#0072B2"),
        ("dist_solar_mw", "Distributed solar", "#009E73"),
    ]:
        line(input_figure, times, input_block[column], input_label, input_color)
    line(
        input_figure,
        times,
        input_block.net_load_mw,
        "Available net load",
        "#882255",
        row=2,
    )
    mo.ui.plotly(input_figure)
    return


@app.cell(hide_code=True)
def leveling_plot(figure, line, mo, np, run_color, run_label, runs, times):
    leveling_figure = figure(
        [
            "Residual load before batteries (dotted) and generation after batteries (solid)",
            "Aggregate battery power: positive = discharge",
        ],
        ["MW", "MW"],
    )
    for level_record in runs:
        result = level_record["result"]
        level_label, level_color = run_label(level_record), run_color(level_record)
        before = np.asarray(result["p_load_served"]).sum(axis=1) - np.asarray(
            result["p_nd"]
        ).sum(axis=1)
        line(
            leveling_figure,
            times,
            before,
            level_label + " · before",
            level_color,
            dash="dot",
        )
        line(
            leveling_figure,
            times,
            np.asarray(result["Pg"]).sum(axis=1),
            level_label,
            level_color,
        )
        line(
            leveling_figure,
            times,
            np.asarray(result["b"]).sum(axis=1),
            level_label,
            level_color,
            row=2,
            show=False,
        )
    mo.vstack(
        [
            mo.md(
                "**Levelization:** residual load uses *dispatched* renewables after curtailment. This is a decomposition of each solution, not a no-battery counterfactual."
            ),
            mo.ui.plotly(leveling_figure),
        ]
    )
    return


@app.cell(hide_code=True)
def outcomes_plot(
    boundary_times, figure, line, mo, np, run_color, run_label, runs, tables, times
):
    outcome_figure = figure(
        [
            "Aggregate battery state of charge",
            "Renewable curtailment",
            "Load shedding (unclipped numerical values)",
        ],
        ["% of fleet capacity", "MW", "MW"],
    )
    for outcome_record in runs:
        outcome_color, outcome_label = (
            run_color(outcome_record),
            run_label(outcome_record),
        )
        line(
            outcome_figure,
            boundary_times,
            np.asarray(outcome_record["boundary_soc_mwh"]).sum(axis=1)
            / tables["batteries"].capacity_mwh.sum()
            * 100,
            outcome_label,
            outcome_color,
        )
        line(
            outcome_figure,
            times,
            np.asarray(outcome_record["result"]["curtailment"]).sum(axis=1),
            outcome_label,
            outcome_color,
            row=2,
            show=False,
        )
        line(
            outcome_figure,
            times,
            outcome_record["result"]["p_load_shed_total"],
            outcome_label,
            outcome_color,
            row=3,
            show=False,
        )
    mo.ui.plotly(outcome_figure)
    return


@app.cell(hide_code=True)
def detail_controls(mo):
    family = mo.ui.dropdown(
        [
            "Generators",
            "Batteries",
            "Renewables",
            "Loads",
            "Branches",
            "Nodal injections",
        ],
        value="Batteries",
        label="Device-level results",
    )
    family
    return (family,)


@app.cell(hide_code=True)
def device_controls(branches, family, mo, tables):
    catalogs = {
        "Generators": {
            f"Row {int(r.source_row)} · bus {int(r.bus)} · {r.pmax_mw:.0f} MW": int(
                r.source_row
            )
            for r in tables["generators"].itertuples()
        },
        "Batteries": {
            r.device_id: i for i, r in enumerate(tables["batteries"].itertuples())
        },
        "Renewables": {
            r.device_id: i for i, r in enumerate(tables["renewables"].itertuples())
        },
        "Loads": {
            f"load_bus_{int(b)}": i
            for i, b in enumerate(
                tables["buses"].loc[tables["buses"].load_share > 0, "bus"]
            )
        },
        "Branches": {
            f"Branch {i} · {int(r[0])} → {int(r[1])}": i for i, r in enumerate(branches)
        },
        "Nodal injections": {
            f"Bus {int(b)}": i for i, b in enumerate(tables["buses"].bus)
        },
    }
    device_options = catalogs[family.value]
    devices = mo.ui.multiselect(
        device_options,
        value=[next(iter(device_options))],
        label="Devices to overlay",
        full_width=True,
    )
    devices
    return device_options, devices


@app.cell(hide_code=True)
def device_plot(
    boundary_times,
    branches,
    device_options,
    devices,
    family,
    figure,
    line,
    mo,
    np,
    run_color,
    run_label,
    runs,
    times,
):
    specifications = {
        "Generators": (["Dispatchable generation"], ["MW"], ["Pg"]),
        "Batteries": (
            ["Battery power (+ discharge)", "Battery state of charge"],
            ["MW", "MWh"],
            ["b", "boundary_soc_mwh"],
        ),
        "Renewables": (
            ["Renewable dispatch", "Renewable availability"],
            ["MW", "MW"],
            ["p_nd", "renewable_available_mw"],
        ),
        "Loads": (
            ["Served load", "Shed fraction"],
            ["MW", "fraction"],
            ["p_load_served", "load_shed_fraction"],
        ),
        "Branches": (
            ["Signed branch flow", "Absolute branch utilization"],
            ["MW", "% of rateA"],
            ["p_flows", "utilization"],
        ),
        "Nodal injections": (["Net device injection (+ injection)"], ["MW"], ["p_net"]),
    }
    titles, units, keys = specifications[family.value]
    detail_figure = figure(titles, units)
    device_names = {v: k for k, v in device_options.items()}
    eligible_runs = [
        r
        for r in runs
        if family.value not in ("Branches", "Nodal injections")
        or r["arm"]["formulation"] == "lossy_dc"
    ]
    for detail_record in eligible_runs:
        for position, device in enumerate(devices.value):
            for row, key in enumerate(keys, 1):
                values = (
                    np.abs(np.asarray(detail_record["result"]["p_flows"]))
                    / branches[:, 5]
                    * 100
                    if key == "utilization"
                    else np.asarray(
                        detail_record[key]
                        if key in detail_record
                        else detail_record["result"][key]
                    )
                )
                line(
                    detail_figure,
                    boundary_times if key == "boundary_soc_mwh" else times,
                    values[:, device],
                    run_label(detail_record) + " · " + device_names[device],
                    run_color(detail_record),
                    row=row,
                    dash=["solid", "dash", "dot", "dashdot"][position % 4],
                    show=row == 1,
                )
    mo.vstack(
        [
            mo.md(
                "Single-node has no branch or nodal-network quantities; those panels show network runs only. Select devices above; color identifies the run and dash style distinguishes devices."
            ),
            mo.ui.plotly(detail_figure),
        ]
    )
    return


@app.cell(hide_code=True)
def summary(mo, pd, run_label, run_record, runs):
    accepted_by_number = {a["number"]: a for a in run_record["accepted"]}
    rows = []
    for summary_record in runs:
        accepted = accepted_by_number[summary_record["iteration"]]
        rows.append(
            {
                "Run": run_label(summary_record),
                **summary_record["audit"]["metrics"],
                **summary_record["audit"]["costs"],
                "objective": summary_record["result"]["objective"],
                "iterations": summary_record["solver_stats"]["num_iters"],
                "worker_seconds": accepted["supervision"]["wall_seconds"],
            }
        )
    mo.vstack(
        [
            mo.md(
                "### Selected-run totals\nEnergy and throughput are MWh; costs are objective units. Different segments have different lengths. Shedding values near 10⁻⁶ MWh or less are numerical residue, not meaningful interruption."
            ),
            mo.ui.table(pd.DataFrame(rows), selection=None),
        ]
    )
    return


if __name__ == "__main__":
    app.run()
