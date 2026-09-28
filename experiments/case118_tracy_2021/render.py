"""Render Stage A input-only comparisons; no OPF results implied."""

import argparse
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import networkx as nx
import pandas as pd

from experiments.case118_annual_hierarchy.pglib_case import load_pglib_case118
from experiments.case118_annual_hierarchy.s5_closeout import calendar_matrix
from .prepare import HERE, TOY, digest, write_json


def render(output: Path, review: Path) -> None:
    manifest = json.loads((review / "manifest.json").read_text())
    for name, expected in manifest["raw_artifacts"].items():
        if digest(output / name) != expected:
            raise ValueError(f"prepared input changed: {name}")
    for name, expected in manifest["toy_artifacts"].items():
        if digest(TOY / name) != expected:
            raise ValueError(f"toy input changed: {name}")
    for name, expected in manifest["review_artifacts"].items():
        if digest(review / name) != expected:
            raise ValueError(f"review input changed: {name}")
    tracy = pd.read_csv(output / "aggregate_inputs.csv", index_col=0, parse_dates=True)
    toy = pd.read_csv(TOY / "aggregate_inputs.csv", index_col=0, parse_dates=True)
    signals = dict(
        load_mw="Gross load",
        available_nd_mw="Available renewables",
        net_load_mw="Available net load",
        solar_mw="Utility solar",
        wind_mw="Wind",
        dist_solar_mw="Distributed solar",
    )
    fig, axes = plt.subplots(6, 2, figsize=(14, 13), layout="constrained")
    for row, (key, label) in enumerate(signals.items()):
        peak = max(
            float(tracy[key].abs().max()),
            float(toy[key].abs().max()) if key in toy else 0,
        )
        signed = key == "net_load_mw"
        for col, (frame, calendar) in enumerate(
            ((toy, "2025 UTC"), (tracy, "2021 UTC−08:00"))
        ):
            ax = axes[row, col]
            ax.set_title(f"{label} · {calendar}", fontsize=10)
            if key not in frame:
                ax.text(0.5, 0.5, "Not modeled", ha="center", transform=ax.transAxes)
                ax.set_axis_off()
                continue
            im = ax.imshow(
                calendar_matrix(frame[key]),
                origin="lower",
                aspect="auto",
                extent=(0.5, 365.5, -0.5, 23.5),
                vmin=-peak if signed else 0,
                vmax=peak,
                cmap="RdBu_r" if signed else "viridis",
            )
            ax.set_ylabel("Hour of day")
            ax.set_yticks([0, 12, 23])
            if row == 5:
                ax.set_xlabel("Chronological day of year")
            fig.colorbar(im, ax=ax, label="MW", fraction=0.025)
    fig.savefig(review / "input_heatmaps.png", dpi=140)
    plt.close(fig)
    colors = ["#000000", "#E69F00", "#0072B2", "#009E73", "#CC79A7"]
    # Separate annual and short-window plots avoid obscuring hourly behavior.
    fig, axes = plt.subplots(3, 1, figsize=(13, 10), layout="constrained")
    views = [
        tracy,
        tracy.loc["2021-02-16":"2021-02-22"],
        tracy.loc["2021-12-18":"2021-12-21"],
    ]
    for ax, frame, title in zip(
        axes,
        views,
        ("Annual hourly inputs", "February shortfall event", "M17 comparison window"),
        strict=True,
    ):
        for color, key in zip(
            colors,
            ("load_mw", "solar_mw", "wind_mw", "dist_solar_mw", "net_load_mw"),
            strict=True,
        ):
            ax.plot(
                frame.index, frame[key], color=color, label=signals[key], linewidth=0.7
            )
        ax.axhline(5000, color="gray", linestyle="--", label="Dispatchable capacity")
        locator = mdates.AutoDateLocator(tz=frame.index.tz)
        ax.xaxis.set_major_locator(locator)
        ax.xaxis.set_major_formatter(
            mdates.ConciseDateFormatter(locator, tz=frame.index.tz)
        )
        ax.set(title=title, ylabel="MW", xlabel="2021 fixed UTC−08:00")
    axes[0].legend(ncol=3, fontsize=8)
    fig.savefig(review / "input_timeseries.png", dpi=140)
    plt.close(fig)
    bus = pd.read_csv(review / "buses.csv")
    battery = pd.read_csv(review / "batteries.csv")
    renewable = pd.read_csv(review / "renewables.csv")
    case = load_pglib_case118()
    graph = nx.Graph()
    graph.add_nodes_from(bus.bus)
    graph.add_edges_from((int(r[0]), int(r[1])) for r in case["branch"] if r[10] > 0)
    positions = nx.spring_layout(graph, seed=42)
    fig, axes = plt.subplots(1, 2, figsize=(15, 7), layout="constrained")
    for ax in axes:
        nx.draw_networkx_edges(graph, positions, ax=ax, alpha=0.25)
        nx.draw_networkx_nodes(
            graph, positions, ax=ax, node_size=22, node_color="lightgray"
        )
        nx.draw_networkx_labels(graph, positions, ax=ax, font_size=5)
        ax.set_axis_off()
    for category, color in zip(
        ("load_only", "load_generation", "generation_no_load", "neither"),
        colors[1:],
        strict=True,
    ):
        rows = bus.loc[bus.category == category]
        nx.draw_networkx_nodes(
            graph,
            positions,
            nodelist=rows.bus.tolist(),
            node_size=45,
            node_color=color,
            label=category.replace("_", " "),
            ax=axes[0],
        )
    axes[0].set_title("Original bus roles (topological layout, not geography)")
    axes[0].legend(fontsize=8)
    utility = (
        renewable.loc[renewable.channel != "dist_solar"]
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
        label="Utility renewable: sum of channel peaks",
        ax=axes[1],
    )
    nx.draw_networkx_nodes(
        graph,
        positions,
        nodelist=battery.bus.tolist(),
        node_size=battery.active_power_limit_mw.to_numpy() * 0.4,
        node_color="#0072B2",
        alpha=0.7,
        label="Battery: active MW rating",
        ax=axes[1],
    )
    axes[1].set_title("Device siting and relative sizes (separate marker scales)")
    axes[1].legend(fontsize=8)
    fig.savefig(review / "network_roles.png", dpi=160)
    plt.close(fig)
    write_json(
        review / "figure_provenance.json",
        dict(
            renderer_sha256=digest(Path(__file__)),
            input_manifest_sha256=digest(review / "manifest.json"),
            figures={
                name: digest(review / name)
                for name in (
                    "input_heatmaps.png",
                    "input_timeseries.png",
                    "network_roles.png",
                )
            },
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=HERE / "results/stage_a_active_inputs"
    )
    parser.add_argument("--review-output", type=Path, default=HERE / "stage_a")
    args = parser.parse_args()
    render(args.output, args.review_output)
