"""Plot the selected Tracy week and paired single-node dispatch trajectories.

Run with ``uv run --extra notebook python -m
experiments.m14_time_vectorization.plot_singlenode_comparison``.
The original timing record omits trajectories, so this makes two fresh-process
168-hour solves and saves their complete records in this experiment's ignored results/ directory.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
import numpy as np

from experiments.m14_time_vectorization.run_m14d_singlenode import (
    ROOT,
    prepare_inputs,
    sha,
)

OUT = ROOT / "experiments/m14_time_vectorization/results/singlenode_vectorization"
RECORD = Path(__file__).with_name("M14D_SINGLENODE_RESULTS.json")
BLUE, ORANGE = "#2666a3", "#d46a32"
RESOURCE_STYLES = (
    ("dist_solar", "DG solar", "#cb7043"),
    ("utility_solar", "Utility solar", "#e8ac32"),
    ("wind", "Utility wind", "#389d9b"),
)


def capture_pair():
    """Recover trajectories and check their relation to the published summary."""
    original = json.loads(RECORD.read_text())
    for path, expected in original["source_hashes"].items():
        if sha(ROOT / path) != expected:
            raise ValueError(f"Source changed since recorded experiment: {path}")
    rows = []
    for assembly in ("stepwise", "vectorized"):
        completed = subprocess.run(
            [sys.executable, "-m",
             "experiments.m14_time_vectorization.run_m14d_singlenode",
             "--worker", assembly, "--horizon", "168"],
            cwd=ROOT, capture_output=True, text=True, check=True, timeout=180,
        )
        row = json.loads(completed.stdout)
        recorded = next(r for r in original["runs"]
                        if r["horizon"] == 168 and r["assembly"] == assembly)
        np.testing.assert_allclose(row["objective"], recorded["objective"],
                                   rtol=1e-10, atol=1e-7)
        rows.append(row)
    trajectories = [{key: np.asarray(value) for key, value in row["trajectory"].items()}
                    for row in rows]
    comparison = next(c for c in original["comparisons"] if c["horizon"] == 168)
    differences = {}
    for key in trajectories[0]:
        maximum = float(np.max(np.abs(trajectories[1][key] - trajectories[0][key])))
        np.testing.assert_allclose(
            maximum, comparison["trajectory_differences"][key]["max_absolute"],
            rtol=1e-5, atol=1e-6,
        )
        differences[key] = maximum
    payload = {"purpose": "Fresh trajectory capture for figures; original timings unchanged",
               "original_record_sha256": sha(RECORD), "plot_script_sha256": sha(Path(__file__)),
               "source_hashes": original["source_hashes"],
               "max_absolute_differences": differences, "runs": rows}
    (OUT / "trajectory_capture.json").write_text(json.dumps(payload, indent=2) + "\n")
    print(json.dumps(differences, indent=2))
    return trajectories


def decorate(ax, unit):
    ax.set_ylabel(unit)
    ax.set_xlim(0, 168)
    ax.set_xticks(np.arange(0, 169, 24))
    ax.grid(alpha=.2)
    ax.spines[["top", "right"]].set_visible(False)


def save(fig, name):
    fig.savefig(OUT / f"{name}.png", dpi=170, facecolor="white")
    plt.close(fig)


def paired_figure(title, rows, note):
    """Each row is (label, unit, hours, labels, colors, stepwise, vectorized)."""
    fig, axes = plt.subplots(len(rows), 2, figsize=(14, 2.45 * len(rows) + 1.3),
                             squeeze=False, sharex=True)
    fig.subplots_adjust(left=.075, right=.97, bottom=.12, top=.87,
                        hspace=.55, wspace=.25)
    fig.suptitle(title, fontsize=17, y=.975)
    single_series = all(len(row[3]) == 1 for row in rows)
    fig.legend(handles=[Line2D([], [], color=BLUE if single_series else "#303b4a",
                               label="Stepwise", lw=2),
                        Line2D([], [], color=ORANGE if single_series else "#303b4a",
                               label="Vectorized", lw=2,
                               linestyle="--")],
               loc="upper center", bbox_to_anchor=(.5, .94), ncol=2, frameon=False)
    for (label, unit, hours, labels, colors, step, vector), (left, right) in zip(
            rows, axes, strict=True):
        for j, (name, color) in enumerate(zip(labels, colors, strict=True)):
            color = BLUE if single_series else color
            left.plot(hours, step[:, j], color=color, lw=1.8, label=name)
            left.plot(hours, vector[:, j], color=ORANGE if single_series else color,
                      lw=1.5, linestyle="--")
            right.plot(hours, vector[:, j] - step[:, j], color=color, lw=1.4)
        left.set_title(label, loc="left", fontsize=12)
        left.legend(loc="upper right", fontsize=8, framealpha=.8, ncol=len(labels))
        maximum = np.max(np.abs(vector - step))
        right.set_title(f"Δ = vectorized − stepwise  ·  max |Δ| = {maximum:.4g} {unit}",
                        loc="left", fontsize=10)
        right.axhline(0, color="#777777", lw=.7)
        for ax in (left, right):
            decorate(ax, unit)
    for ax in axes[-1]:
        ax.set_xlabel("Hour since Dec 22, 2021 00:00 (fixed UTC−08:00)")
    fig.text(.075, .025, note, fontsize=10, color="#454c57")
    return fig


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    step, vector = capture_pair()
    scenario, _ = prepare_inputs(168)
    hours = np.arange(168)
    availability = scenario.df_nd.to_numpy()
    load = scenario.df_P.sum(axis=1).to_numpy()
    net = load - availability.sum(axis=1)
    groups = [(prefix, label, color,
               [i for i, name in enumerate(scenario.df_nd.columns)
                if name.startswith(prefix + "_bus_")])
              for prefix, label, color in RESOURCE_STYLES]
    assert sorted(i for _, _, _, indices in groups for i in indices) == list(range(7))
    plt.rcParams.update({"font.size": 10})

    fig, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    fig.subplots_adjust(left=.085, right=.97, bottom=.11, top=.80, hspace=.32)
    fig.suptitle("Tracy inputs · December 22–28, 2021 · Case9 scale", fontsize=17, y=.965)
    fig.text(.085, .865,
             f"Net energy: {net.sum():,.1f} MWh     Peak net load: {net.max():,.1f} MW     "
             f"Minimum net load: {net.min():,.1f} MW     Positive-net hours: {(net > 0).sum()} / 168",
             fontsize=11)
    axes[0].plot(hours, load, label="Load", color="#303b4a", lw=2)
    axes[0].plot(hours, net, label="Net load", color=BLUE, lw=2)
    axes[0].fill_between(hours, load, net, color=BLUE, alpha=.5)
    axes[0].axhline(0, color="#777777", lw=.8)
    axes[0].set_title("Load and net load", loc="left")
    axes[0].legend(loc="lower left", ncol=2)
    axes[1].stackplot(hours, *[availability[:, indices].sum(axis=1)
                              for _, _, _, indices in groups],
                      labels=[label for _, label, _, _ in groups],
                      colors=[color for _, _, color, _ in groups])
    axes[1].set_title("Available renewables · DG solar at the bottom", loc="left")
    axes[1].legend(loc="upper left", ncol=3)
    for ax in axes:
        decorate(ax, "MW")
    axes[1].set_xlabel("Hour since Dec 22, 2021 00:00 (fixed UTC−08:00)")
    save(fig, "tracy_week_inputs")

    rows = [
        ("Battery power · positive = discharge", "MW", hours, ["Battery"], [BLUE],
         step["b"], vector["b"]),
        ("State of charge", "MWh", np.arange(169), ["Battery"], [BLUE],
         np.vstack([[500.], step["soc"]]), np.vstack([[500.], vector["soc"]])),
    ]
    fig = paired_figure("Battery trajectories · 168-hour horizon", rows,
                        "Power is timestamped at interval start; SoC at interval boundaries. "
                        "Both trajectories start and finish at 500 MWh.")
    fig.axes[2].axhline(500, color="#777777", lw=.8, linestyle=":")
    fig.axes[2].scatter([0, 168], [500, 500], s=30, color=ORANGE, zorder=5)
    save(fig, "battery_trajectories")

    curtailment = [availability - item["p_nd"] for item in (step, vector)]
    rows = [("Total renewable curtailment", "MW", hours, ["Total"], [BLUE],
             curtailment[0].sum(axis=1, keepdims=True),
             curtailment[1].sum(axis=1, keepdims=True))]
    palettes = [["#cb7043", "#893f20", "#eaa884"],
                ["#b77b00", "#e8ac32"], ["#186b6a", "#389d9b"]]
    for (_, label, _, indices), colors in zip(groups, palettes, strict=True):
        labels = [f"Bus {scenario.df_nd.columns[i].rsplit('_bus_', 1)[1]}" for i in indices]
        rows.append((f"{label} curtailment · individual sites", "MW", hours, labels, colors,
                     curtailment[0][:, indices], curtailment[1][:, indices]))
    save(paired_figure("Renewable curtailment trajectories · 168-hour horizon", rows,
                       "Curtailment = available power − renewable output. "
                       "Δ curtailment = −Δ renewable output for the common inputs."),
         "renewable_curtailment_trajectories")

    rows = [("Total dispatchable generation", "MW", hours, ["Total"], [BLUE],
             step["Pg"].sum(axis=1, keepdims=True), vector["Pg"].sum(axis=1, keepdims=True))]
    for i, color in enumerate([BLUE, ORANGE, "#389d9b"]):
        rows.append((f"Generator {i + 1}", "MW", hours, [f"Generator {i + 1}"], [color],
                     step["Pg"][:, i:i+1], vector["Pg"][:, i:i+1]))
    save(paired_figure("Generator dispatch trajectories · 168-hour horizon", rows,
                       "The two dispatch curves nearly coincide; the right panels show "
                       "their small signed differences on separate scales."),
         "generator_dispatch_trajectories")
    np.savez_compressed(OUT / "inputs.npz", hours=hours, load=load,
                        renewable_availability=availability,
                        renewable_ids=scenario.df_nd.columns.to_numpy(dtype=str))
    print(f"Saved four figures and supporting data to {OUT}")


if __name__ == "__main__":
    main()
