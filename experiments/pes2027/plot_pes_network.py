"""Render the paper's configured Case118 network from retained Stage A tables.

This is a non-solving presentation of the existing topology, allocation and
ratings, not a new input preparation or a rewrite of historical figures.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import networkx as nx
import pandas as pd

from experiments.case118_annual_hierarchy.pglib_case import load_pglib_case118
from experiments.case118_tracy_2021.prepare import HERE, digest


def collect():
    """Read identity-checked siting tables and the pinned PGLib topology."""
    stage = HERE / "stage_a"
    manifest = json.loads((stage / "manifest.json").read_text())
    names = ("buses.csv", "batteries.csv", "renewables.csv")
    for name in names:
        if digest(stage / name) != manifest["review_artifacts"][name]:
            raise ValueError(f"Retained network table changed: {name}")
    return (
        load_pglib_case118(),
        *(pd.read_csv(stage / name) for name in names),
        {name: digest(stage / name) for name in (*names, "manifest.json")},
    )


def render(case, buses, batteries, nondispatchable, output):
    """Draw the configured network with the historical layout and marker scales.

    Parallel circuits share an edge in this topological view. Distributed solar
    is omitted; utility squares sum channel peaks and battery circles use MW
    ratings, with separate area scales, as in the original Stage A rendering.
    Load-node areas use Case118 source MW demand, whose shares determine the
    configured demand allocation. Non-load buses retain a small fixed marker.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    graph = nx.Graph()
    graph.add_nodes_from(buses.bus)
    graph.add_edges_from((int(r[0]), int(r[1])) for r in case["branch"] if r[10] > 0)
    positions = nx.spring_layout(graph, seed=42)
    fig, ax = plt.subplots(figsize=(7.5, 7), layout="constrained")
    nx.draw_networkx_edges(graph, positions, ax=ax, alpha=0.25)
    nx.draw_networkx_nodes(
        graph,
        positions,
        nodelist=buses.loc[buses.source_p_mw <= 0, "bus"].tolist(),
        ax=ax,
        node_size=22,
        node_color="lightgray",
    )
    loads = buses.loc[buses.source_p_mw > 0]
    nx.draw_networkx_nodes(
        graph,
        positions,
        nodelist=loads.bus.tolist(),
        node_size=loads.source_p_mw.to_numpy() * 0.805,
        node_color="lightgray",
        label="Load: source demand (MW)",
        ax=ax,
    )
    nx.draw_networkx_labels(graph, positions, ax=ax, font_size=5)
    utility = (
        nondispatchable.loc[nondispatchable.channel != "dist_solar"]
        .groupby("bus")
        .peak_available_mw.sum()
    )
    nx.draw_networkx_nodes(
        graph,
        positions,
        nodelist=utility.index.tolist(),
        node_size=utility.to_numpy() * 0.12,
        node_shape="s",
        node_color="#E69F00",
        alpha=0.55,
        label="Utility solar/wind: sum of channel peaks",
        ax=ax,
    )
    nx.draw_networkx_nodes(
        graph,
        positions,
        nodelist=batteries.bus.tolist(),
        node_size=batteries.active_power_limit_mw.to_numpy() * 0.4,
        node_color="#0072B2",
        alpha=0.7,
        label="Battery: active MW rating",
        ax=ax,
    )
    ax.set_title("Device siting and relative sizes (separate marker scales)")
    ax.legend(fontsize=8)
    ax.set_axis_off()
    for suffix in ("png", "pdf"):
        fig.savefig(output.with_suffix("." + suffix), dpi=160)
    plt.close(fig)
    return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("Choose a fresh network figure directory")
    case, buses, batteries, nondispatchable, sources = collect()
    args.output.mkdir(parents=True)
    render(case, buses, batteries, nondispatchable, args.output / "configured_network")
    (args.output / "manifest.json").write_text(
        json.dumps(
            dict(
                plot_source_sha256=digest(Path(__file__)),
                source_hashes=sources,
                files={p.name: digest(p) for p in args.output.iterdir()},
                layout_seed=42,
                optimizer_calls=0,
                scope="Retained configured topology and asset siting; display only.",
            ),
            indent=2,
        )
        + "\n"
    )
    print(args.output)


if __name__ == "__main__":
    main()
