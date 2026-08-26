"""Generate presentation figures from retained cvxopf experiment artifacts."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
BATTERY = ROOT / "experiments/battery_terminal/results"
M17 = (
    ROOT
    / "experiments/hierarchical_battery_resilience/results/s3_authoritative_0cd65b1"
)
M17B = (
    ROOT
    / "experiments/hierarchical_battery_resilience/results/s3b_causal_recovery"
)
CASE118 = ROOT / "experiments/case118_annual_hierarchy"

# Okabe-Ito colorblind-friendly palette. Important comparisons also use
# distinct line styles so that color is never the only visual encoding.
BLUE = "#0072B2"
SKY = "#56B4E9"
ORANGE = "#E69F00"
GREEN = "#009E73"
VERMILLION = "#D55E00"
GRAY = "#6F6F6F"


def finish(fig: plt.Figure, name: str) -> None:
    fig.tight_layout()
    fig.savefig(HERE / name, bbox_inches="tight")
    plt.close(fig)


def terminal_value() -> None:
    data = pd.read_csv(BATTERY / "terminal_value_sweep.csv")
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.0))
    for scenario, group in data.groupby("scenario", sort=False):
        group = group[group["status"].isin(("optimal", "optimal_inaccurate"))]
        operating = group["objective"] - group["objective"].min()
        axes[0].plot(group["target_mwh"], operating, marker="o", ms=3, label=scenario)
    axes[0].set(
        xlabel="Required terminal SoC (MWh)", ylabel="Incremental operating cost"
    )
    axes[0].set_title("Terminal energy has a convex operating value", fontweight="bold")
    axes[0].grid(alpha=0.25)
    axes[0].legend(frameon=False)

    soft = pd.read_csv(BATTERY / "soft_weight_sweep.csv")
    high = soft[soft["scenario"] == "high"]
    for kind, group in high.groupby("cost_kind", sort=False):
        axes[1].semilogx(
            group["weight"], group["terminal_soc_mwh"], marker="o", ms=4, label=kind
        )
    axes[1].axhline(500, color=ORANGE, linestyle="--", label="500 MWh target")
    axes[1].set(xlabel="Terminal penalty weight", ylabel="Optimized terminal SoC (MWh)")
    axes[1].set_title(
        "Linear penalties become exact; quadratic penalties approach", fontweight="bold"
    )
    axes[1].grid(alpha=0.25)
    axes[1].legend(frameon=False)
    finish(fig, "terminal_policy_results.pdf")


def locality_and_handoff() -> None:
    horizon = pd.read_csv(BATTERY / "horizon_study.csv")
    horizon = horizon[horizon["policy"].isin(("equality", "quadratic"))]
    fig, axes = plt.subplots(1, 2, figsize=(10.8, 4.0))
    styles = {"equality": "-", "quadratic": "--"}
    for (scenario, policy), group in horizon.groupby(
        ["scenario", "policy"], sort=False
    ):
        axes[0].plot(
            group["horizon_steps"],
            100 * group["final_excursion_fraction"],
            linestyle=styles[policy],
            marker="o",
            ms=3,
            label=f"{scenario}, {policy}",
        )
    axes[0].set(
        xlabel="Horizon (hours)", ylabel="Policy-sensitive suffix (% of horizon)"
    )
    axes[0].set_title(
        "Terminal influence becomes local in long horizons", fontweight="bold"
    )
    axes[0].grid(alpha=0.25)
    axes[0].legend(frameon=False, fontsize=8)

    traces = pd.read_csv(BATTERY / "subset_trajectories.csv")
    traces = traces[
        (traces["case"] == "crosses_boundary") & (traces["battery_index"] == 0)
    ]
    for formulation, group in traces.groupby("formulation", sort=False):
        axes[1].plot(
            group["global_post_step_state"],
            group["soc_mwh"],
            marker="o",
            label=formulation,
        )
    axes[1].axhline(1000, color=ORANGE, linestyle="--", label="battery capacity")
    axes[1].set(xlabel="Global boundary", ylabel="Battery SoC (MWh)")
    axes[1].set_title(
        "Short AC realization preserves the DC energy boundary", fontweight="bold"
    )
    axes[1].grid(alpha=0.25)
    axes[1].legend(frameon=False)
    finish(fig, "locality_and_handoff.pdf")


def formulation_results() -> None:
    fig, axes = plt.subplots(
        1, 2, figsize=(10.8, 4.0), gridspec_kw={"width_ratios": [1.15, 1]}
    )
    case_labels = ["case9", "case57"]
    sparse_ratios = [3.2, 27.2]
    dense_ratios = [4.0, 110.1]
    case_x = np.arange(len(case_labels))
    width = 0.34
    sparse_bars = axes[0].bar(
        case_x - width / 2,
        sparse_ratios,
        width,
        label="sparse P/Q",
        color=BLUE,
    )
    dense_bars = axes[0].bar(
        case_x + width / 2,
        dense_ratios,
        width,
        label="dense P/Q",
        color=ORANGE,
    )
    axes[0].bar_label(sparse_bars, fmt="%.1f×", padding=3)
    axes[0].bar_label(dense_bars, fmt="%.1f×", padding=3)
    axes[0].set_xticks(case_x, case_labels)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("Direct / lifted solve time")
    axes[0].set_title(
        "Equivalent lifted branch limits solve much faster", fontweight="bold"
    )
    axes[0].grid(axis="y", alpha=0.25)
    axes[0].legend(frameon=False)

    metrics = ["variables", "equalities", "inequalities"]
    direct = [668, 655, 188]
    lifted = [988, 975, 188]
    x = np.arange(3)
    comparison_width = 0.36
    axes[1].bar(
        x - comparison_width / 2,
        direct,
        comparison_width,
        label="direct",
        color=GRAY,
    )
    axes[1].bar(
        x + comparison_width / 2,
        lifted,
        comparison_width,
        label="lifted",
        color=BLUE,
    )
    axes[1].set_xticks(x, metrics)
    axes[1].set_ylabel("Scalar model entries (case57 sparse)")
    axes[1].set_title(
        "The faster representation is algebraically larger", fontweight="bold"
    )
    axes[1].legend(frameon=False)
    axes[1].grid(axis="y", alpha=0.25)
    finish(fig, "formulation_results.pdf")


def _load_gzip_json(path: Path) -> dict[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def m17_soc_trajectories() -> None:
    frozen_hard = _load_gzip_json(M17 / "frozen__hard_equality.json.gz")
    frozen_soft = _load_gzip_json(M17 / "frozen__quadratic_soft.json.gz")
    replanned_soft = _load_gzip_json(
        M17 / "replan_every_step__quadratic_soft.json.gz"
    )
    replanned_hard = _load_gzip_json(M17B / "causal_recovery.json.gz")

    outer_plan = next(iter(frozen_hard["outer_plans"].values()))
    trajectories = (
        (
            "initial DC energy plan",
            outer_plan["boundary_soc_mwh"],
            "black",
            "--",
            2.2,
        ),
        ("frozen / hard", frozen_hard["realized_soc_mwh"], BLUE, "-", 1.8),
        ("frozen / soft", frozen_soft["realized_soc_mwh"], SKY, "-.", 1.8),
        (
            "replanned / hard + recovery",
            replanned_hard["realized_soc_mwh"],
            GREEN,
            ":",
            1.6,
        ),
        (
            "replanned / soft",
            replanned_soft["realized_soc_mwh"],
            VERMILLION,
            "-",
            1.8,
        ),
    )

    fig, ax = plt.subplots(figsize=(10.4, 4.2))
    for label, raw_values, color, linestyle, linewidth in trajectories:
        values = np.asarray(raw_values, dtype=float)[:, 0]
        boundaries = np.arange(values.size)
        ax.plot(
            boundaries,
            values,
            color=color,
            linestyle=linestyle,
            linewidth=linewidth,
            label=label,
        )

    soft_values = np.asarray(replanned_soft["realized_soc_mwh"], dtype=float)[:, 0]
    ax.scatter(
        [soft_values.size - 1],
        [soft_values[-1]],
        marker="x",
        s=55,
        linewidth=2,
        color=VERMILLION,
        zorder=5,
    )
    ax.annotate(
        "mixed-policy baseline stopped\nbefore the final soft solve",
        xy=(soft_values.size - 1, soft_values[-1]),
        xytext=(-7, -35),
        textcoords="offset points",
        ha="right",
        fontsize=8.5,
        color=VERMILLION,
        arrowprops={"arrowstyle": "-", "color": VERMILLION, "linewidth": 0.8},
    )
    ax.set(
        xlim=(0, 96),
        ylim=(0, 1025),
        xlabel="Global boundary (hour)",
        ylabel="Battery SoC (MWh)",
    )
    ax.set_title(
        "AC realization follows the energy plan while soft targets preserve flexibility",
        fontweight="bold",
    )
    ax.grid(alpha=0.2)
    ax.legend(frameon=False, ncols=3, loc="upper center", fontsize=8.5)
    finish(fig, "m17_soc_trajectories.pdf")


def recycling() -> None:
    data = json.loads((CASE118 / "RECYCLE_COMPARISON_RESULTS.json").read_text())
    fig, ax = plt.subplots(figsize=(10.2, 4.0))
    names = {
        "never": "never recycle",
        "recycle_32": "every 32 intervals",
        "recycle_16": "every 16 intervals",
    }
    colors = {"never": ORANGE, "recycle_32": BLUE, "recycle_16": GREEN}
    for arm in ("never", "recycle_32", "recycle_16"):
        series = data["arms"][arm]["after_release_series"]
        ax.plot(
            [p["iteration"] for p in series],
            [p["rss_mib"] / 1024 for p in series],
            marker=".",
            linewidth=1.5,
            label=names[arm],
            color=colors[arm],
        )
    for boundary in (16, 32, 48):
        ax.axvline(boundary, color="#cccccc", linewidth=0.8, zorder=0)
    ax.set(
        xlabel="Completed AC interval", ylabel="Worker RSS after model release (GiB)"
    )
    ax.set_title(
        "Fresh workers bound process-lifetime memory while preserving the trajectory",
        fontweight="bold",
    )
    ax.grid(alpha=0.2)
    ax.legend(frameon=False, ncols=3, loc="lower center")
    finish(fig, "recycling_memory.pdf")


def main() -> None:
    plt.rcParams.update(
        {"font.size": 10, "axes.spines.top": False, "axes.spines.right": False}
    )
    terminal_value()
    locality_and_handoff()
    formulation_results()
    m17_soc_trajectories()
    recycling()


if __name__ == "__main__":
    main()
