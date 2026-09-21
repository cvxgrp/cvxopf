# /// script
# requires-python = ">=3.11"
# dependencies = ["marimo==0.24.2", "numpy>=2.0", "pandas>=2.2", "plotly>=6.0"]
# ///

import marimo

__generated_with = "0.24.2"
app = marimo.App(width="medium")


@app.cell(hide_code=True)
def imports():
    import importlib.util
    from pathlib import Path
    import json
    import marimo as mo
    import numpy as np
    import pandas as pd
    import plotly.graph_objects as go

    return Path, go, importlib, json, mo, np, pd


@app.cell(hide_code=True)
def load_results(Path, importlib, json, mo):
    repository = Path(mo.notebook_location()).parent
    analysis_path = repository / "experiments/m14_time_vectorization/analyze_tracy_week.py"
    module_spec = importlib.util.spec_from_file_location("tracy_week_analysis", analysis_path)
    analysis = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(analysis)
    bundle_path = repository / "outputs/tracy_week_comparison/comparison.json"
    comparison_record = json.loads((repository / "experiments/m14_time_vectorization/TRACY_WEEK_COMPARISON_RESULTS.json").read_text())
    assert analysis.sha(bundle_path) == comparison_record["bundle_sha256"], "Comparison bundle differs from its summary record."
    bundle = json.loads(bundle_path.read_text())
    labels = analysis.LABELS
    keys_by_label = {label: key for key, label in labels.items()}
    return analysis, bundle, bundle_path, keys_by_label, labels


@app.cell(hide_code=True)
def introduction(mo):
    mo.md("""
    # One Tracy week, five solutions

    **December 22–28, 2021 · Case9 scale · fixed UTC−08:00 · 168 hourly intervals.**
    Compare single-node DC and lossy DC in both representations with vectorized AC.
    Stepwise AC is the sixth mode: it timed out at both **3 and 30 minutes**.

    All solutions share load, renewable availability, generator costs and limits,
    and a **150 MW / 1,000 MWh battery starting and finishing at 500 MWh**.
    Representation pairs model the same problem. Across network types, the physics
    differ: single-node omits the network; lossy DC enforces network-flow limits
    with a quadratic loss penalty but no real-power loss withdrawal; AC includes
    voltage, reactive support, two-terminal ratings and electrical losses.

    This notebook only reads saved results. Legend clicks toggle curves, drag to
    zoom, and hover for values. The controls below drive the time-series sections.
    """)
    return


@app.cell(hide_code=True)
def overview(bundle, mo, pd):
    overview_table = pd.DataFrame(bundle["summary"]).rename(columns={
        "mode":"Mode", "status":"Status", "objective":"Objective", "generation_cost":"Generation cost",
        "cycling_cost":"Cycling cost", "dc_loss_penalty":"DC loss penalty", "generation_mwh":"Generation (MWh)",
        "curtailed_mwh":"Curtailed (MWh)", "battery_throughput_mwh":"Battery throughput (MWh)",
        "ac_real_losses_mwh":"AC real losses (MWh)", "total_seconds":"Total (s)",
        "peak_rss_mib":"Peak RSS (MiB)", "wall_limit_seconds":"Timeout bound (s)"})
    mo.vstack([
        mo.md("## Full-week outcomes\nCosts use the model’s objective units. Missing physical quantities and failed-run results are shown as missing."),
        mo.ui.table(overview_table, selection=None, show_column_summaries=False, show_data_types=False, format_mapping={column: "{:,.3f}" for column in overview_table.select_dtypes("number").columns}),
        mo.md("**Timing context:** " + bundle["timing_note"] +
              " A timeout bound is not a completed solve time. Reactive allocation is unpriced; differences alone are not a correctness failure.")
    ])
    return


@app.cell(hide_code=True)
def timing_view(bundle, go, labels, mo):
    def make_timing_chart(data):
        chart = go.Figure()
        phases = {"construction":"Construction", "initialization":"Initialization", "canonicalization":"Canonicalization",
                  "solver":"Solver", "interface_overhead":"Interface overhead", "extraction":"Extraction"}
        for phase, label in phases.items():
            values = []
            for solution in data["solutions"].values():
                timing = solution["seconds"]
                if phase == "solver":
                    value = timing.get("solver", timing.get("solver_reported"))
                elif phase == "interface_overhead" and "solve_wall_after_canonicalization" in timing:
                    value = timing["solve_wall_after_canonicalization"]-timing["solver_reported"]
                else:
                    value = timing.get(phase, 0.)
                values.append(value)
            chart.add_trace(go.Bar(x=list(labels.values()), y=values, name=label))
        chart.update_layout(barmode="stack", height=400, template="plotly_white", yaxis_title="Seconds", legend=dict(orientation="h",y=1.15))
        return chart

    mo.accordion({"Timing breakdown · successful solves only": mo.ui.plotly(make_timing_chart(bundle)),
                  "AC stepwise attempts · no returned solutions": mo.json(bundle["failed_mode"])})
    return


@app.cell(hide_code=True)
def controls(keys_by_label, labels, mo):
    mode_picker = mo.ui.multiselect(options=list(keys_by_label), value=list(keys_by_label), label="Show solutions")
    baseline_picker = mo.ui.dropdown(options=list(keys_by_label), value=labels["lossy_dc_vectorized"], label="Difference baseline", allow_select_none=False)
    hour_picker = mo.ui.range_slider(start=0, stop=168, step=1, value=(0,168), label="Hours to inspect [start, end)", show_value=True)
    mo.vstack([mo.md("## Inspect trajectories"), mo.hstack([mode_picker,baseline_picker],justify="start",wrap=True),hour_picker])
    return baseline_picker, hour_picker, mode_picker


@app.cell(hide_code=True)
def selection(baseline_picker, hour_picker, keys_by_label, mo, mode_picker):
    selected = [keys_by_label[label] for label in mode_picker.value]
    baseline = keys_by_label[baseline_picker.value]
    window = tuple(hour_picker.value)
    mo.stop(not selected, mo.md("Choose at least one solution."))
    mo.stop(window[1] <= window[0], mo.md("Choose an interval at least one hour long."))
    return baseline, selected, window


@app.cell(hide_code=True)
def inputs_view(analysis, bundle, mo, np, window):
    net_input = np.asarray(bundle["inputs"]["load_mw"])-np.asarray(bundle["inputs"]["renewable_availability_mw"]).sum(axis=1)
    inspected_net = net_input[window[0]:window[1]]
    mo.vstack([
        mo.md(f"### Shared electrical inputs · hours {window[0]}–{window[1]}\n"
              f"**Net energy:** {inspected_net.sum():,.1f} MWh · **Peak net load:** {inspected_net.max():,.1f} MW · "
              f"**Minimum net load:** {inspected_net.min():,.1f} MW · **Positive-net hours:** {(inspected_net>0).sum()} / {len(inspected_net)}"),
        mo.ui.plotly(analysis.input_figure(bundle,window))])
    return


@app.cell(hide_code=True)
def battery_view(analysis, baseline, bundle, mo, selected, window):
    mo.vstack([
        mo.md("### Battery\nPositive power is discharge. Power uses interval starts; SoC includes all 169 boundaries, with both endpoints fixed at 500 MWh."),
        mo.ui.plotly(analysis.comparison_figure(bundle,selected,baseline,"Battery power","Total",window)),
        mo.ui.plotly(analysis.comparison_figure(bundle,selected,baseline,"State of charge","Total",window))])
    return


@app.cell(hide_code=True)
def dispatch_controls(bundle, mo):
    generation_picker = mo.ui.dropdown(options=["Total"]+bundle["inputs"]["generator_ids"], value="Total", label="Generation channel", allow_select_none=False)
    renewable_picker = mo.ui.dropdown(options=["Total","DG solar","Utility solar","Utility wind"]+bundle["inputs"]["renewable_ids"],value="Total",label="Renewable channel",allow_select_none=False)
    mo.vstack([mo.md("### Dispatch and renewables"),mo.hstack([generation_picker,renewable_picker],justify="start",wrap=True)])
    return generation_picker, renewable_picker


@app.cell(hide_code=True)
def dispatch_view(
    analysis,
    baseline,
    bundle,
    generation_picker,
    mo,
    renewable_picker,
    selected,
    window,
):
    mo.vstack([
        mo.ui.plotly(analysis.comparison_figure(bundle,selected,baseline,"Generation",generation_picker.value,window)),
        mo.ui.plotly(analysis.comparison_figure(bundle,selected,baseline,"Curtailment",renewable_picker.value,window)),
        mo.accordion({"Renewable output":mo.ui.plotly(analysis.comparison_figure(bundle,selected,baseline,"Renewable output",renewable_picker.value,window)),
                      "Supply minus load":mo.vstack([mo.md("Generation + renewable output + battery discharge − load. This is AC real loss demand; DC balances sum to zero."),
                            mo.ui.plotly(analysis.comparison_figure(bundle,selected,baseline,"Supply minus load","Total",window))])})])
    return


@app.cell(hide_code=True)
def difference_controls(mo):
    difference_picker = mo.ui.dropdown(options=["Battery power","State of charge","Generation","Curtailment","Renewable output","Supply minus load"],value="Battery power",label="Difference statistics",allow_select_none=False)
    difference_picker
    return (difference_picker,)


@app.cell(hide_code=True)
def differences(
    analysis,
    baseline,
    bundle,
    difference_picker,
    generation_picker,
    mo,
    renewable_picker,
    selected,
    window,
):
    difference_channel = generation_picker.value if difference_picker.value=="Generation" else (renewable_picker.value if difference_picker.value in ("Curtailment","Renewable output") else "Total")
    delta_table = analysis.difference_table(bundle,selected,baseline,difference_picker.value,difference_channel,window)
    mo.vstack([mo.md("Signed differences are **selected − baseline**, over the chosen interval. Energy differences integrate power for one-hour intervals; SoC differences are not integrated."),
               mo.ui.table(delta_table,selection=None,show_column_summaries=False, show_data_types=False, format_mapping={column: "{:,.6g}" for column in delta_table.select_dtypes("number").columns})])
    return


@app.cell(hide_code=True)
def network_controls(mo):
    network_picker = mo.ui.dropdown(options=["Branch real power","Voltage magnitude","Generator reactive power","Renewable reactive power","Battery reactive power"],value="Branch real power",label="Network / AC quantity",allow_select_none=False)
    mo.vstack([mo.md("## Network and reactive support\nBranch real power is positive from the named from-bus to to-bus. AC uses the from-terminal power; DC has no separate terminal losses. Voltage and reactive series exist only for AC. Single-node has no branches."),network_picker])
    return (network_picker,)


@app.cell(hide_code=True)
def network_channels(bundle, mo, network_picker):
    channels_by_quantity = {"Branch real power":bundle["inputs"]["branch_ids"],"Voltage magnitude":[f"Bus {i}" for i in range(1,10)],
                            "Generator reactive power":bundle["inputs"]["generator_ids"],"Renewable reactive power":bundle["inputs"]["renewable_ids"],"Battery reactive power":["Battery · bus 7"]}
    network_channels = channels_by_quantity[network_picker.value]
    network_channel_picker = mo.ui.dropdown(options=network_channels,value=network_channels[0],label="Channel",allow_select_none=False)
    network_channel_picker
    return network_channel_picker, network_channels


@app.cell(hide_code=True)
def network_view(
    analysis,
    bundle,
    mo,
    network_channel_picker,
    network_channels,
    network_picker,
    window,
):
    mo.ui.plotly(analysis.network_figure(bundle,network_picker.value,network_channels.index(network_channel_picker.value),window))
    return


@app.cell(hide_code=True)
def provenance(bundle, bundle_path, mo):
    mo.accordion({"Verification and provenance":mo.vstack([
        mo.md(f"Bundle: `{bundle_path}`\n\nAll five saved solutions were checked in their corresponding current model graphs. Cross-model objective differences are economic/model comparisons, not formulation-equivalence tests. "
              "Stepwise AC has no trajectory to plot. No solver runs are triggered by widget changes."),
        mo.json(bundle["checks"]),mo.json(bundle["provenance"])]),
        "Fleet and input settings":mo.json({k:v for k,v in bundle["inputs"].items() if k in ("start","end","delta_hours","generators","storage","renewable_units")})})
    return


if __name__ == "__main__":
    app.run()
