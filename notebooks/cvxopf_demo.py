"""
cvxopf OPF Explorer — interactive marimo notebook.

Run from the repository root:
    uv run --extra notebook marimo run notebooks/cvxopf_demo.py
"""

import marimo

__generated_with = "0.23.13"
app = marimo.App(width="medium")


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

@app.cell
def _():
    import marimo as mo
    import numpy as np
    import matplotlib.pyplot as plt
    import networkx as nx
    return mo, np, plt, nx


# ---------------------------------------------------------------------------
# Title and description
# ---------------------------------------------------------------------------

@app.cell
def _(mo):
    mo.md(
        """
        # cvxopf — Optimal Power Flow Explorer

        Solve AC-OPF or lossy DC OPF on standard MATPOWER test cases using
        [cvxopf](https://github.com/cvxgrp/cvxopf).

        **DC formulation** (convex QP, solved by CLARABEL):

        - Network with $n$ buses, $m$ branches, incidence matrix $A \\in \\mathbf{R}^{n \\times m}$
        - Branch flows $p \\in \\mathbf{R}^m$, with limits $|p_j| \\leq C_j$
        - Nodal generation $g \\in \\mathbf{R}^n$, load $l \\in \\mathbf{R}^n$
        - Flow conservation: $A p + g = l$
        - Generation cost: $G = \\sum_i (a_i g_i + b_i g_i^2)$
        - Line losses: $L = \\sum_j r_j p_j^2$
        - Objective: minimize $G + \\lambda L$

        **AC formulation** (nonconvex DNLP, solved by IPOPT):

        - Full nonlinear power flow with voltage magnitudes and angles
        - Reactive power dispatch
        - ⚠️ AC-OPF may be slow for large cases (case57, case118)

        Reference for DC formulation: *Convex Optimization with Smart Grid Examples*,
        https://doi.org/10.2172/3018252
        """
    )
    return


# ---------------------------------------------------------------------------
# Case and formulation selection
# ---------------------------------------------------------------------------

@app.cell
def _(mo):
    case_selector = mo.ui.dropdown(
        options={
            "case9  —   9 buses,   3 generators": "case9",
            "case14 —  14 buses,   5 generators": "case14",
            "case30 —  30 buses,   6 generators": "case30",
            "case39 —  39 buses,  10 generators": "case39",
            "case57 —  57 buses,   7 generators": "case57",
            "case118 — 118 buses, 54 generators": "case118",
        },
        value="case14 —  14 buses,   5 generators",
        label="Test case",
    )
    return (case_selector,)


@app.cell
def _(mo):
    formulation_selector = mo.ui.dropdown(
        options={
            "Lossy DC OPF (convex QP — fast)": "lossy_dc",
            "AC-OPF (nonconvex DNLP — slow for large cases)": "ac",
        },
        value="Lossy DC OPF (convex QP — fast)",
        label="Formulation",
    )
    return (formulation_selector,)


@app.cell
def _(mo, case_selector, formulation_selector):
    mo.hstack([case_selector, formulation_selector], justify="start", gap=2)
    return


# ---------------------------------------------------------------------------
# Load case data
# ---------------------------------------------------------------------------

@app.cell
def _(case_selector):
    import importlib
    _mod = importlib.import_module(f"cvxopf.testcases.{case_selector.value}")
    _fn  = getattr(_mod, case_selector.value)
    ppc  = _fn()
    return (ppc,)


@app.cell
def _(ppc):
    nb = ppc["bus"].shape[0]
    ng = ppc["gen"].shape[0]
    nl = ppc["branch"].shape[0]
    return nb, ng, nl


# ---------------------------------------------------------------------------
# AC warning for large cases
# ---------------------------------------------------------------------------

@app.cell
def _(mo, formulation_selector, nb):
    _warn = (
        mo.callout(
            mo.md(
                f"⚠️ **AC-OPF on {nb}-bus case may take 10+ seconds.** "
                "Consider switching to lossy DC OPF for interactive exploration."
            ),
            kind="warn",
        )
        if formulation_selector.value == "ac" and nb > 57
        else mo.md("")
    )
    _warn
    return

# ---------------------------------------------------------------------------
# Build parameter dicts (same logic as original notebook)
# ---------------------------------------------------------------------------

@app.cell
def _(ppc, np):
    def _merge(d1, d2):
        result = {}
        for key in d1.keys() | d2.keys():
            result[key] = {**d1.get(key, {}), **d2.get(key, {})}
        return result

    # generators — double Pmax to match reference notebook
    _gd1 = {
        int(_g[0]): {
            "p_min": float(np.clip(_g[9], 0, np.inf)),
            "p_max": float(_g[8]) * 2,
        }
        for _g in ppc["gen"]
        if int(_g[7]) == 1   # in-service only
    }
    _gd2 = {
        int(ppc["gen"][_ix, 0]): {
            "c0": float(_g[-1]),
            "c1": float(_g[-2]),
            "c2": float(_g[-3]),
        }
        for _ix, _g in enumerate(ppc["gencost"])
        if int(ppc["gen"][_ix, 7]) == 1
    }
    generator_dict = _merge(_gd1, _gd2)

    # loads
    load_dict = {
        int(_l[0]): {
            "l_min":   float(np.abs(_l[2]) * 0.5),
            "l_upper": float(np.abs(_l[2])),
            "cost":    1e4,
        }
        for _l in ppc["bus"]
    }

    # branch flow limits
    _fmax_default = 10 * float(np.max([np.max(_b[5:7]) for _b in ppc["branch"]]))
    flow_dict = {}
    for _ix, _b in enumerate(ppc["branch"]):
        _fx = float(np.max(_b[5:7]))
        flow_dict[_ix] = {
            "f_max":        _fx if _fx > 0 else _fmax_default,
            "f_resistance": float(_b[2]),
        }

    return generator_dict, load_dict, flow_dict


# ---------------------------------------------------------------------------
# Interactive controls
# ---------------------------------------------------------------------------

@app.cell
def _(mo):
    scale_load = mo.ui.slider(
        start=0, stop=10, step=0.01,
        value=1.0,
        label="Load scale factor (α)",
        full_width=True,
        show_value=True,
    )
    loss_weight = mo.ui.slider(
        start=0, stop=10, step=0.1,
        value=1.0,
        label="Loss weight (λ) — DC only",
        full_width=True,
        show_value=True,
    )
    return scale_load, loss_weight


@app.cell
def _(mo, generator_dict, np):
    _max = float(np.ceil(1.5 * np.max([v["p_max"] for v in generator_dict.values()])))
    gen_limits = mo.ui.array(
        [
            mo.ui.range_slider(
                start=0, stop=_max,
                label=f"gen bus {k}",
                value=[v["p_min"], v["p_max"]],
                debounce=True,
                show_value=True,
            )
            for k, v in generator_dict.items()
        ],
        label="Generator limits (MW)",
    )
    return (gen_limits,)


@app.cell
def _(mo, flow_dict, np):
    _max = float(np.ceil(1.5 * np.max([v["f_max"] for v in flow_dict.values()])))
    flow_limits = mo.ui.array(
        [
            mo.ui.slider(
                start=0, stop=_max,
                label=f"branch {ix + 1}",
                value=v["f_max"],
                debounce=True,
                show_value=True,
            )
            for ix, v in flow_dict.items()
        ],
        label="Branch flow limits (MW)",
    )
    return (flow_limits,)


@app.cell
def _(mo, scale_load, loss_weight):
    mo.vstack([scale_load, loss_weight])
    return


@app.cell
def _(mo, gen_limits, flow_limits):
    mo.ui.tabs({
        "Generator limits": gen_limits,
        "Branch flow limits": flow_limits,
    })
    return


# ---------------------------------------------------------------------------
# Build and solve OPF
# ---------------------------------------------------------------------------

@app.cell
def _(
    mo, ppc, np,
    formulation_selector, case_selector,
    generator_dict, load_dict, flow_dict,
    gen_limits, flow_limits, scale_load, loss_weight,
):
    from cvxopf.problem import build_opf, OPFOptions
    from cvxopf.results import extract_results
    from cvxopf.network import reindex_case_to_consecutive

    # Apply interactive overrides to ppc
    import copy
    _ppc = copy.deepcopy(ppc)

    # Scale load
    _ppc["bus"][:, 2] = ppc["bus"][:, 2] * scale_load.value
    _ppc["bus"][:, 3] = ppc["bus"][:, 3] * scale_load.value

    # Apply generator limit overrides
    _gen_keys = list(generator_dict.keys())
    for _k_ix, _bus_id in enumerate(_gen_keys):
        for _row_ix in range(_ppc["gen"].shape[0]):
            if int(_ppc["gen"][_row_ix, 0]) == _bus_id:
                _ppc["gen"][_row_ix, 9] = gen_limits.value[_k_ix][0]   # Pmin
                _ppc["gen"][_row_ix, 8] = gen_limits.value[_k_ix][1]   # Pmax

    # Apply branch flow limit overrides
    for _ix in range(_ppc["branch"].shape[0]):
        _ppc["branch"][_ix, 5] = flow_limits.value[_ix]   # rateA
        _ppc["branch"][_ix, 6] = flow_limits.value[_ix]   # rateB

    _formulation = formulation_selector.value
    _options = OPFOptions(loss_weight=loss_weight.value)

    with mo.status.spinner(
        title=f"Solving {case_selector.value} ({_formulation})",
        subtitle="Please wait ...",
    ) as _spinner:
        try:
            _build = build_opf(_ppc, formulation=_formulation, options=_options)
            _build.solve()
            _results = extract_results(_build)
            _spinner.update(subtitle="Done.")
        except Exception as _e:
            _results = {"status": f"error: {_e}", "objective": float("nan")}
            _build   = None

    build   = _build
    results = _results
    return build, results, extract_results, build_opf, OPFOptions, reindex_case_to_consecutive


# ---------------------------------------------------------------------------
# Solve status display
# ---------------------------------------------------------------------------

@app.cell
def _(mo, results):
    _status = results["status"]
    _obj    = results["objective"]
    _kind   = "success" if _status == "optimal" else "danger"
    mo.callout(
        mo.md(
            f"**Status:** `{_status}`  |  "
            f"**Objective:** `{_obj:.4f} $/hr`"
            if _status == "optimal"
            else f"**Status:** `{_status}`"
        ),
        kind=_kind,
    )
    return


# ---------------------------------------------------------------------------
# Node labelling helpers
# ---------------------------------------------------------------------------

@app.cell
def _(ppc, np):
    _gen_nodes  = set(ppc["gen"][:, 0].astype(int).tolist())
    _load_nodes = set(
        (np.where(ppc["bus"][:, 2] != 0)[0] + 1).tolist()
    )
    gen_only_nodes  = np.array(sorted(_gen_nodes - _load_nodes))
    load_only_nodes = np.array(sorted(_load_nodes - _gen_nodes))
    both_nodes      = np.array(sorted(_gen_nodes & _load_nodes))
    return gen_only_nodes, load_only_nodes, both_nodes


# ---------------------------------------------------------------------------
# Viz selector
# ---------------------------------------------------------------------------

@app.cell
def _(mo, nb):
    viz_selector = mo.ui.dropdown(
        options={"Mermaid flowchart": "mermaid", "NetworkX plot": "networkx"},
        value="Mermaid flowchart" if nb <= 30 else "NetworkX plot",
        label="Network visualization",
    )
    viz_selector
    return (viz_selector,)


# ---------------------------------------------------------------------------
# Mermaid network (topology)
# ---------------------------------------------------------------------------

@app.cell
def _(ppc, gen_only_nodes, load_only_nodes, both_nodes):
    _lines  = [(int(_b[0]), int(_b[1])) for _b in ppc["branch"]]
    _graph  = ["flowchart LR"]
    for _ix, _l in enumerate(_lines):
        _graph.append(f"  {_l[0]}(({_l[0]})) == {_ix+1} === {_l[1]}(({_l[1]}))")
    _graph = "\n".join(_graph)
    _graph += """
        classDef gen  fill:#008000,color:#fff;
        classDef load fill:#f9f;
        classDef both fill:#008000,color:#fff,stroke-dasharray:5 5;"""
    if len(load_only_nodes) > 0:
        _graph += "\n    class " + ",".join(str(i) for i in load_only_nodes) + " load;"
    if len(gen_only_nodes) > 0:
        _graph += "\n    class " + ",".join(str(i) for i in gen_only_nodes) + " gen;"
    if len(both_nodes) > 0:
        _graph += "\n    class " + ",".join(str(i) for i in both_nodes) + " both;"
    mermaid_topology = _graph
    return (mermaid_topology,)


# ---------------------------------------------------------------------------
# Mermaid solution (flows)
# ---------------------------------------------------------------------------

@app.cell
def _(ppc, results, gen_only_nodes, load_only_nodes, both_nodes, np):
    _lines   = [(int(_b[0]), int(_b[1])) for _b in ppc["branch"]]
    _sol     = ["flowchart LR"]
    _p_flows = results.get("p_flows")

    # constrained lines — dual value check not available via build_opf API;
    # flag lines where |flow| >= 99% of limit instead
    _f_max   = ppc["branch"][:, 5].astype(float)
    _constrained = []
    if _p_flows is not None:
        for _ix in range(len(_lines)):
            _fl  = float(_p_flows[_ix])
            _lim = float(_f_max[_ix])
            if _lim > 0 and abs(_fl) >= 0.99 * _lim:
                _constrained.append(_ix)
            _f, _t = _lines[_ix]
            if _fl >= 0:
                _sol.append(f"  {_f}(({_f})) == {_fl:.1f} ==> {_t}(({_t}))")
            else:
                _sol.append(f"  {_t}(({_t})) == {-_fl:.1f} ==> {_f}(({_f}))")
    else:
        for _ix, (_f, _t) in enumerate(_lines):
            _sol.append(f"  {_f}(({_f})) ~~~ {_t}(({_t}))")

    _sol = "\n".join(_sol)
    _sol += """
        classDef gen  fill:#008000,color:#fff;
        classDef load fill:#f9f;
        classDef both fill:#008000,color:#fff,stroke-dasharray:5 5;"""
    if len(load_only_nodes) > 0:
        _sol += "\n    class " + ",".join(str(i) for i in load_only_nodes) + " load;"
    if len(gen_only_nodes) > 0:
        _sol += "\n    class " + ",".join(str(i) for i in gen_only_nodes) + " gen;"
    if len(both_nodes) > 0:
        _sol += "\n    class " + ",".join(str(i) for i in both_nodes) + " both;"
    if len(_constrained) > 0:
        _sol += "\n linkStyle " + ",".join(str(i) for i in _constrained) + " stroke:#FF0000,color:red;"

    mermaid_solution = _sol
    return (mermaid_solution,)


# ---------------------------------------------------------------------------
# NetworkX plot (topology + flows)
# ---------------------------------------------------------------------------

@app.cell
def _(ppc, results, plt, nx, np):
    _lines   = [(int(_b[0]), int(_b[1])) for _b in ppc["branch"]]
    _p_flows = results.get("p_flows")
    _nb      = ppc["bus"].shape[0]

    _G = nx.DiGraph()
    for _i in range(_nb):
        _G.add_node(int(ppc["bus"][_i, 0]))
    for _ix, (_f, _t) in enumerate(_lines):
        _G.add_edge(_f, _t)

    _UG  = _G.to_undirected()
    _pos = nx.kamada_kawai_layout(_UG) if _nb <= 100 else nx.spring_layout(_UG, seed=42)

    _gen_nodes  = set(ppc["gen"][:, 0].astype(int).tolist())
    _load_nodes = set((np.where(ppc["bus"][:, 2] != 0)[0] + 1).tolist())

    _node_colors = []
    for _n in _G.nodes():
        if _n in _gen_nodes and _n in _load_nodes:
            _node_colors.append("#2ca02c")
        elif _n in _gen_nodes:
            _node_colors.append("#1f77b4")
        elif _n in _load_nodes:
            _node_colors.append("#ff7f0e")
        else:
            _node_colors.append("#aec7e8")

    _fig_nx, _ax_nx = plt.subplots(figsize=(10, 7))

    if _p_flows is not None:
        _f_max   = ppc["branch"][:, 5].astype(float)
        _e_colors = []
        for _ix, (_f, _t) in enumerate(_lines):
            _fl  = float(_p_flows[_ix])
            _lim = float(_f_max[_ix])
            if _lim > 0 and abs(_fl) >= 0.99 * _lim:
                _e_colors.append("red")
            elif abs(_fl) > 1e-3:
                _e_colors.append("#555555")
            else:
                _e_colors.append("#cccccc")
        nx.draw_networkx_edges(
            _G, _pos, ax=_ax_nx,
            edge_color=_e_colors,
            arrows=True, arrowsize=10,
            width=1.5, alpha=0.8,
        )
        _edge_labels = {
            (_f, _t): f"{float(_p_flows[_ix]):.0f}"
            for _ix, (_f, _t) in enumerate(_lines)
            if abs(float(_p_flows[_ix])) > 1.0
        }
        nx.draw_networkx_edge_labels(
            _G, _pos, edge_labels=_edge_labels,
            ax=_ax_nx, font_size=6,
        )
    else:
        nx.draw_networkx_edges(_G, _pos, ax=_ax_nx, alpha=0.4)

    nx.draw_networkx_nodes(
        _G, _pos, ax=_ax_nx,
        node_color=_node_colors, node_size=300, alpha=0.9,
    )
    nx.draw_networkx_labels(_G, _pos, ax=_ax_nx, font_size=7, font_color="white")

    from matplotlib.lines import Line2D
    _legend = [
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#1f77b4", label="Generator only"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#ff7f0e", label="Load only"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#2ca02c", label="Generator + load"),
        Line2D([0], [0], marker="o", color="w", markerfacecolor="#aec7e8", label="Transit bus"),
        Line2D([0], [0], color="red",    label="Branch at capacity"),
        Line2D([0], [0], color="#555555", label="Branch with flow"),
    ]
    _ax_nx.legend(handles=_legend, loc="upper left", fontsize=8)
    _ax_nx.set_title(f"Network — flows in MW (red = at capacity)")
    _ax_nx.axis("off")
    plt.tight_layout()
    networkx_fig = _fig_nx
    return (networkx_fig,)


# ---------------------------------------------------------------------------
# DC bus power / voltage bar charts
# ---------------------------------------------------------------------------

@app.cell
def _(results, ppc, build, plt, np):
    _nb      = ppc["bus"].shape[0]
    _buses   = np.arange(1, _nb + 1)
    _status  = results.get("status", "")
    _form    = build.formulation if build is not None else "unknown"

    if _form == "lossy_dc" and _status == "optimal":
        _p_gen  = build.variables["p_gen"].value * build.data["baseMVA"]
        _p_load = ppc["bus"][:, 2]
        _prices = -build.prob.constraints[0].dual_value * build.data["baseMVA"]

        _fig_dc, _axes = plt.subplots(nrows=3, sharex=True, figsize=(9, 6))

        _axes[0].stem(_buses, _p_gen, linefmt="C0-", markerfmt="C0o", basefmt="k-")
        _axes[0].set_title("Generator output (MW)")
        _axes[0].set_ylabel("MW")

        _axes[1].stem(_buses, _p_load, linefmt="C1-", markerfmt="C1o", basefmt="k-")
        _axes[1].set_title("Load served (MW)")
        _axes[1].set_ylabel("MW")

        _axes[2].scatter(_buses, _prices, color="C2", s=30)
        _axes[2].axhline(0, color="k", linewidth=0.5)
        _axes[2].set_title("Nodal prices (dual variable)")
        _axes[2].set_ylabel("$/MWh")
        _axes[2].set_xlabel("Bus number")

        plt.tight_layout()
        dc_bus_fig = _fig_dc
    else:
        _fig_empty, _ax_empty = plt.subplots(figsize=(9, 2))
        _ax_empty.text(
            0.5, 0.5,
            "DC bus chart not available\n(solve DC formulation to see this plot)",
            ha="center", va="center", transform=_ax_empty.transAxes,
        )
        _ax_empty.axis("off")
        dc_bus_fig = _fig_empty
    return (dc_bus_fig,)


# ---------------------------------------------------------------------------
# AC voltage / reactive power charts
# ---------------------------------------------------------------------------

@app.cell
def _(results, ppc, build, plt, np):
    _nb     = ppc["bus"].shape[0]
    _buses  = np.arange(1, _nb + 1)
    _status = results.get("status", "")
    _form   = build.formulation if build is not None else "unknown"

    if _form == "ac" and _status == "optimal":
        _Vm     = results["Vm"]
        _Va_deg = results["Va_deg"]
        _Pg     = results["Pg"]
        _Qg     = results["Qg"]
        _ng     = len(_Pg)
        _gen_buses = ppc["gen"][:, 0].astype(int)

        _fig_ac, _axes = plt.subplots(nrows=2, ncols=2, figsize=(12, 7))

        # Vm bar chart
        _axes[0, 0].bar(_buses, _Vm, color="C0", alpha=0.8)
        _axes[0, 0].axhline(1.0, color="k", linewidth=0.8, linestyle="--", label="1.0 p.u.")
        _axes[0, 0].set_ylim(0.85, 1.15)
        _axes[0, 0].set_title("Voltage magnitude (p.u.)")
        _axes[0, 0].set_ylabel("Vm (p.u.)")
        _axes[0, 0].legend(fontsize=8)

        # Va stem chart
        _axes[0, 1].stem(_buses, _Va_deg, linefmt="C1-", markerfmt="C1o", basefmt="k-")
        _axes[0, 1].set_title("Voltage angle (degrees)")
        _axes[0, 1].set_ylabel("Va (deg)")

        # Pg bar chart
        _axes[1, 0].bar(range(1, _ng + 1), _Pg, color="C2", alpha=0.8)
        _axes[1, 0].set_title("Generator real output (MW)")
        _axes[1, 0].set_ylabel("Pg (MW)")
        _axes[1, 0].set_xlabel("Generator index")

        # Qg bar chart
        _axes[1, 1].bar(range(1, _ng + 1), _Qg, color="C3", alpha=0.8)
        _axes[1, 1].axhline(0, color="k", linewidth=0.5)
        _axes[1, 1].set_title("Generator reactive output (MVAr)")
        _axes[1, 1].set_ylabel("Qg (MVAr)")
        _axes[1, 1].set_xlabel("Generator index")

        plt.tight_layout()
        ac_voltage_fig = _fig_ac
    else:
        _fig_empty, _ax_empty = plt.subplots(figsize=(9, 2))
        _ax_empty.text(
            0.5, 0.5,
            "AC voltage chart not available\n(solve AC formulation to see this plot)",
            ha="center", va="center", transform=_ax_empty.transAxes,
        )
        _ax_empty.axis("off")
        ac_voltage_fig = _fig_empty
    return (ac_voltage_fig,)


# ---------------------------------------------------------------------------
# Main display tabs
# ---------------------------------------------------------------------------

@app.cell
def _(
    mo, viz_selector,
    mermaid_topology, mermaid_solution, networkx_fig,
    dc_bus_fig, ac_voltage_fig,
):
    _network_tab = (
        mo.vstack([
            mo.ui.tabs({
                "Topology": mo.mermaid(mermaid_topology),
                "Flows": mo.mermaid(mermaid_solution),
            })
        ])
        if viz_selector.value == "mermaid"
        else mo.md("").callout() or mo.as_html(networkx_fig)
    )

    mo.ui.tabs({
        "Network": (
            mo.vstack([
                mo.ui.tabs({
                    "Topology": mo.mermaid(mermaid_topology),
                    "Flows":    mo.mermaid(mermaid_solution),
                })
            ])
            if viz_selector.value == "mermaid"
            else mo.as_html(networkx_fig)
        ),
        "DC — bus power & prices": mo.as_html(dc_bus_fig),
        "AC — voltages & reactive": mo.as_html(ac_voltage_fig),
    })
    return


if __name__ == "__main__":
    app.run()
