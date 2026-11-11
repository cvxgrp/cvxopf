"""
Ideal vs lossy battery -- interactive marimo notebook.

Run from the repository root:
    uv run --extra notebook marimo run notebooks/storage_ideal_vs_lossy.py
"""

import marimo

__generated_with = "0.23.13"
app = marimo.App(width="medium")


@app.cell
def _():
    import warnings
    from dataclasses import replace

    import marimo as mo
    import matplotlib.pyplot as plt
    import networkx as nx
    import numpy as np
    import pandas as pd

    from cvxopf import gen_from_matpower, max_generation_marginal_cost
    from cvxopf.load import loads_from_matpower
    from cvxopf.problem import build_opf_multistep, StorageUnitIdeal
    from cvxopf.results import extract_results
    from cvxopf.storage_lossy import SimultaneousOperationError, StorageUnitLossy
    from cvxopf.testcases import case9, case14, case30, case39

    return (
        SimultaneousOperationError,
        StorageUnitIdeal,
        StorageUnitLossy,
        build_opf_multistep,
        case14,
        case30,
        case39,
        case9,
        extract_results,
        gen_from_matpower,
        loads_from_matpower,
        max_generation_marginal_cost,
        mo,
        np,
        nx,
        pd,
        plt,
        replace,
        warnings,
    )


@app.cell
def _(mo):
    mo.md(r"""
    # Ideal vs lossy battery

    Solves the same 24-hour dispatch twice, once with a `StorageUnitIdeal`
    and once with a `StorageUnitLossy`.

    - Load follows one sinusoidal day
    - Both batteries start half full and must end half full
    - Both use the same bus, power rating, capacity, and aging weight
      (0.01 $/MWh of throughput)
    - Every load can be shed at a linear penalty of 100 times the largest
      generator marginal cost
    """)
    return


@app.cell
def _(case14, case30, case39, case9, mo):
    case_selector = mo.ui.dropdown(
        options={"case9": case9, "case14": case14, "case30": case30,
                 "case39": case39},
        value="case9",
        label="Network",
    )
    model_selector = mo.ui.dropdown(
        options=["singlenode_dc", "lossy_dc", "socp", "ac"],
        value="lossy_dc",
        label="Model",
    )
    rating_pct = mo.ui.slider(1, 50, step=1, value=10, debounce=True,
                              show_value=True,
                              label="Power rating (% of system load)")
    duration_h = mo.ui.slider(1, 4, step=1, value=2, debounce=True,
                              show_value=True,
                              label="Duration (hours at rated power)")
    round_trip = mo.ui.slider(0.70, 1.00, step=0.01, value=0.90, debounce=True,
                              show_value=True,
                              label="Round-trip efficiency (lossy)")
    self_discharge_pct = mo.ui.slider(0.0, 5.0, step=0.5, value=2.0,
                                      debounce=True, show_value=True,
                                      label="Self-discharge (% per month)")
    return (
        case_selector,
        duration_h,
        model_selector,
        rating_pct,
        round_trip,
        self_discharge_pct,
    )


@app.cell
def _(case_selector, mo, np):
    ppc = case_selector.value()
    Pd_base = ppc["bus"][:, 2]
    Qd_base = ppc["bus"][:, 3]
    _bus_ids = [str(int(bus)) for bus in ppc["bus"][:, 0]]
    bus_selector = mo.ui.dropdown(
        options=_bus_ids,
        value=_bus_ids[int(np.argmax(Pd_base))],
        label="Battery bus",
    )
    return Pd_base, Qd_base, bus_selector, ppc


@app.cell
def _(
    bus_selector,
    case_selector,
    duration_h,
    mo,
    model_selector,
    rating_pct,
    round_trip,
    self_discharge_pct,
):
    mo.vstack([
        mo.hstack([case_selector, model_selector, bus_selector], justify="start"),
        rating_pct,
        duration_h,
        round_trip,
        self_discharge_pct,
    ])
    return


@app.cell
def _(bus_selector, case_selector, nx, plt, ppc):
    _graph = nx.Graph()
    _graph.add_nodes_from(int(bus) for bus in ppc["bus"][:, 0])
    _graph.add_edges_from((int(f), int(t)) for f, t in ppc["branch"][:, :2])
    _pos = (nx.kamada_kawai_layout(_graph) if _graph.number_of_nodes() <= 100
            else nx.spring_layout(_graph, seed=42))

    _battery = int(bus_selector.value)
    _gen = set(int(bus) for bus in ppc["gen"][:, 0]) - {_battery}
    _other = set(_graph.nodes) - _gen - {_battery}

    network_fig, _ax = plt.subplots(figsize=(7, 5))
    nx.draw_networkx_edges(_graph, _pos, ax=_ax, edge_color="0.6")
    nx.draw_networkx_nodes(_graph, _pos, nodelist=sorted(_other), ax=_ax,
                           node_color="0.8", node_size=120, label="Bus")
    nx.draw_networkx_nodes(_graph, _pos, nodelist=sorted(_gen), ax=_ax,
                           node_color="tab:blue", node_shape="s",
                           node_size=140, label="Generator bus")
    nx.draw_networkx_nodes(_graph, _pos, nodelist=[_battery], ax=_ax,
                           node_color="tab:purple", node_shape="*",
                           node_size=500, label="Battery bus")
    nx.draw_networkx_labels(_graph, _pos, ax=_ax, font_size=6)
    _ax.set_title(f"{case_selector.selected_key}: battery at bus {_battery}")
    _ax.legend(loc="best", fontsize=8, markerscale=0.6)
    _ax.axis("off")
    network_fig.tight_layout()
    network_fig
    return


@app.cell
def _(Pd_base, T, capacity, np, plt, rating, scales, series):
    _hours = np.arange(T + 1)
    _styles = {
        "ideal": dict(color="tab:blue", linestyle="-", label="Ideal"),
        "lossy": dict(color="tab:orange", linestyle="--", label="Lossy"),
    }
    fig, _axes = plt.subplots(3, 1, figsize=(8, 7), sharex=True)
    _axes[0].stairs(scales * Pd_base.sum(), _hours, baseline=None, color="0.4")
    _axes[0].set_ylabel("System load (MW)")
    for _kind, (_b, _soc, _) in series.items():
        _axes[1].stairs(_b, _hours, **_styles[_kind])
        _axes[2].plot(_hours, np.concatenate([[capacity / 2], _soc]),
                      **_styles[_kind])
    _axes[1].axhline(0.0, color="0.7", linewidth=0.8)
    for _limit in (rating, -rating):
        _axes[1].axhline(_limit, color="0.3", linestyle="--", linewidth=1.0)
    _axes[1].set_ylim(-1.25 * rating, 1.25 * rating)
    _axes[1].set_ylabel("Battery net power (MW)\n(+ discharge)")
    _axes[2].set_ylabel("State of charge (MWh)")
    _axes[2].set_xlabel("Hour")
    _axes[2].set_xticks(range(0, T + 1, 4))
    _axes[1].legend()
    for _ax in _axes:
        _ax.grid(alpha=0.3)
    fig.tight_layout()
    fig
    return


@app.cell
def _(
    Pd_base,
    Qd_base,
    SimultaneousOperationError,
    StorageUnitIdeal,
    StorageUnitLossy,
    build_opf_multistep,
    bus_selector,
    duration_h,
    extract_results,
    gen_from_matpower,
    loads_from_matpower,
    max_generation_marginal_cost,
    mo,
    model_selector,
    np,
    pd,
    ppc,
    rating_pct,
    replace,
    round_trip,
    self_discharge_pct,
    warnings,
):
    T = 24
    scales = 1.0 + 0.3 * np.sin(2 * np.pi * np.arange(T) / T)

    shedding_cost = 100.0 * max_generation_marginal_cost(
        gen_from_matpower(ppc["gen"], ppc["gencost"])
    )
    _loads = [
        replace(load, shedding_cost_per_mwh=shedding_cost)
        for load in loads_from_matpower(ppc["bus"])
    ]
    _ids = [load.device_id for load in _loads]
    _df_load_p = pd.DataFrame(np.outer(scales, Pd_base), columns=_ids)
    _df_load_q = pd.DataFrame(np.outer(scales, Qd_base), columns=_ids)

    rating = rating_pct.value / 100 * Pd_base.sum()
    capacity = duration_h.value * rating
    _shared = dict(
        bus=int(bus_selector.value),
        apparent_power_rating=rating,
        capacity=capacity,
        initial_soc=capacity / 2,
        terminal_soc=capacity / 2,
        terminal_constraint="equality",
    )
    _eta = round_trip.value ** 0.5
    _devices = {
        "ideal": dict(storage=[StorageUnitIdeal(**_shared)]),
        "lossy": dict(lossy_storage=[StorageUnitLossy(
            **_shared,
            charge_efficiency=_eta,
            discharge_efficiency=_eta,
            self_discharge_per_month=self_discharge_pct.value / 100,
        )]),
    }

    results = {}
    split_message = None
    _failed = {}
    for _kind, _device in _devices.items():
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            _build = build_opf_multistep(
                ppc, T=T, formulation=model_selector.value,
                loads=_loads, df_load_p=_df_load_p, df_load_q=_df_load_q,
                **_device,
            )
        _build.solve()
        if _build.prob.status not in ("optimal", "optimal_inaccurate"):
            _failed[_kind] = _build.prob.status
            continue
        try:
            results[_kind] = extract_results(_build)
        except SimultaneousOperationError as _error:
            results[_kind] = _error.results
            split_message = str(_error)

    mo.stop(
        bool(_failed),
        mo.callout(mo.md(f"Solve did not reach optimality: `{_failed}`"),
                   kind="danger"),
    )
    return T, capacity, rating, results, scales, shedding_cost, split_message


@app.cell
def _(np, results):
    def battery_series(kind):
        """Net power (MW), post-step SoC (MWh), and summary metrics."""
        r = results[kind]
        if kind == "lossy":
            b, soc = r["ls_b"][:, 0], r["ls_soc"][:, 0]
            charged, discharged = np.sum(r["ls_p_ch"]), np.sum(r["ls_p_dis"])
            cost = r["ls_cost"]
            conversion_loss = np.sum(r["ls_conversion_loss"])
            self_discharge = np.sum(r["ls_self_discharge"])
        else:
            b, soc = r["b"][:, 0], r["soc"][:, 0]
            charged, discharged = np.sum(np.maximum(-b, 0)), np.sum(np.maximum(b, 0))
            cost = r["storage_cost"]
            conversion_loss = self_discharge = 0.0
        metrics = {
            "Objective ($)": r["objective"],
            "Battery cost ($)": cost,
            "Energy charged (MWh)": charged,
            "Energy discharged (MWh)": discharged,
            "Conversion loss (MWh)": conversion_loss,
            "Self-discharge (MWh)": self_discharge,
            "Energy not served (MWh)": r["energy_not_served"],
        }
        return b, soc, metrics

    series = {kind: battery_series(kind) for kind in ("ideal", "lossy")}
    return (series,)


@app.cell
def _(mo, np, results, series, shedding_cost, split_message):
    _ideal, _lossy = series["ideal"][2], series["lossy"][2]

    def _percent(name):
        if abs(_ideal[name]) < 1e-9:
            return "n/a"
        return f"{100 * (_lossy[name] - _ideal[name]) / abs(_ideal[name]):+.2f}%"

    _rows = "\n".join(
        f"| {name} | {_ideal[name]:,.2f} | {_lossy[name]:,.2f} | "
        f"{_lossy[name] - _ideal[name]:+,.2f} | {_percent(name)} |"
        for name in _ideal
    )
    _split = float(np.max(results["lossy"]["ls_split"]))
    _items = [
        mo.md(
            "| Metric | Ideal | Lossy | Lossy - ideal | Change |\n"
            "|---|---:|---:|---:|---:|\n" + _rows + "\n\n"
            f"Largest simultaneous charge and discharge (lossy): "
            f"{_split:.1e} MW. Shedding penalty: {shedding_cost:,.0f} $/MWh."
        )
    ]
    if split_message is not None:
        _items.append(mo.callout(mo.md(split_message), kind="warn"))
    mo.vstack(_items)
    return


@app.cell
def _(T, np, plt, ppc, results):
    _bus_ids = ppc["bus"][:, 0].astype(int)
    _nb = len(_bus_ids)
    _shed = {
        _kind: np.maximum(np.asarray(results[_kind]["p_load_shed"], dtype=float), 0.0)
        for _kind in ("ideal", "lossy")
    }
    # A floor of 1 MW keeps the color scale valid when nothing is shed.
    _vmax = max(_shed["ideal"].max(), _shed["lossy"].max(), 1.0)
    _fig, _axes = plt.subplots(
        1, 2, figsize=(9, 1.5 + 0.18 * _nb), sharey=True, constrained_layout=True,
    )
    for _ax, _kind in zip(_axes, ("ideal", "lossy")):
        _image = _ax.imshow(
            _shed[_kind].T, aspect="auto", cmap="plasma",
            vmin=0.0, vmax=_vmax, interpolation="nearest",
            extent=(0, T, _nb, 0),
        )
        _ax.set_title(_kind.capitalize())
        _ax.set_xlabel("Hour")
        _ax.set_xticks(range(0, T + 1, 4))
    _axes[0].set_yticks(np.arange(_nb) + 0.5, [str(bus) for bus in _bus_ids],
                        fontsize=7)
    _axes[0].set_ylabel("Bus")
    _fig.suptitle("Energy not served by bus")
    _fig.colorbar(_image, ax=_axes, label="MWh per hour")
    _fig
    return


if __name__ == "__main__":
    app.run()
