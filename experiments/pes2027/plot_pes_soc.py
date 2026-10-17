"""Non-solving normalized joint-dispatch battery trajectories for the PES paper."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from experiments.case118_tracy_2021.e3_results import HERE, load_scenarios, sha


def normalized_soc(boundaries, capacities):
    """Return per-device fractions and capacity-weighted fleet energy fraction.

    Boundaries include the initial state. Keep raw numerical-scale excursions;
    display normalization must not clip or repair the retained trajectories.
    """
    energy = np.asarray(boundaries, dtype=float)
    capacity = np.asarray(capacities, dtype=float)
    if (
        energy.ndim != 2
        or energy.shape[0] < 2
        or capacity.shape != (energy.shape[1],)
        or not capacity.size
        or not np.isfinite(energy).all()
        or not np.isfinite(capacity).all()
        or np.any(capacity <= 0)
    ):
        raise ValueError("Invalid boundary energies or battery capacities")
    return energy / capacity, energy.sum(axis=1) / capacity.sum()


def collect():
    """Reuse the finalized E3 reader's hashes, identity and boundary checks."""
    data = load_scenarios()
    table = data["tables"]["batteries"]
    cases = []
    for name, records in data["cases"].items():
        arms = []
        for method in ("ac", "singlenode_dc"):
            record = records[method]
            ids = record["input_identities"]["storage"]
            if ids != table.device_id.tolist():
                raise ValueError("Retained battery order differs from capacity table")
            run = next(
                r
                for r in data["runs"]
                if r["binding"]["context"]["cohort"] == record["cohort"]
            )
            path = run["directory"] / record["archive"]
            normalized_soc(record["boundary_soc_mwh"], table.capacity_mwh)
            arms.append(
                dict(
                    method=method,
                    classification=record["classification"],
                    source_path=str(path.relative_to(HERE)),
                    source_sha256=sha(path),
                    execution_commit=record["execution_context"]["commit"],
                    boundary_soc_mwh=record["boundary_soc_mwh"].tolist(),
                )
            )
        cases.append(
            dict(
                scenario=name,
                start=records["ac"]["arm"]["start"],
                stop=records["ac"]["arm"]["stop"],
                requested_load_mw=records["ac"]["result"]["p_load"]
                .sum(axis=1)
                .tolist(),
                available_renewables_mw=records["ac"]["renewable_available_mw"]
                .sum(axis=1)
                .tolist(),
                arms=arms,
            )
        )
    return dict(
        storage_ids=table.device_id.tolist(),
        capacity_mwh=table.capacity_mwh.tolist(),
        capacity_table_sha256=sha(HERE / "stage_a/batteries.csv"),
        cases=cases,
        scope="Retained joint solutions; normalized for display, not new acceptance.",
        optimizer_calls=0,
    )


def render(case, capacities, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import PercentFormatter

    with plt.rc_context(
        {
            "font.size": 8,
            "axes.titlesize": 9,
            "pdf.fonttype": 42,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    ):
        fig = plt.figure(figsize=(3.5, 4.0))
        grid = fig.add_gridspec(
            3,
            1,
            left=0.19,
            right=0.97,
            bottom=0.11,
            top=0.80,
            hspace=0.50,
        )
        axes = [fig.add_subplot(grid[1, 0]), fig.add_subplot(grid[2, 0])]
        context = fig.add_subplot(grid[0, 0])
        for index, (ax, arm) in enumerate(zip(axes, case["arms"])):
            expected = ("ac", "singlenode_dc")[index]
            if arm["method"] != expected:
                raise ValueError("Expected full AC above copper plate")
            individual, fleet = normalized_soc(arm["boundary_soc_mwh"], capacities)
            hours = np.arange(individual.shape[0])
            if hours[-1] != case["stop"] - case["start"]:
                raise ValueError("Boundary coverage differs from retained horizon")
            ax.plot(hours, individual, color=".65", alpha=0.6, lw=0.65)
            ax.plot(hours, fleet, color="black", lw=1.8, zorder=5)
            ax.set_title(("(b) full AC", "(c) copper plate")[index], pad=5)
            ax.set_ylabel("SoC / capacity")
            ax.set_ylim(-0.02, 1.02)
            ax.set_xlim(0, hours[-1])
            ax.set_xticks(np.arange(0, hours[-1] + 1, 6))
            ax.set_yticks([0, 0.5, 1])
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
            ax.grid(axis="y", color=".9", lw=0.5)
        axes[-1].set_xlabel("Hours from start")
        axes[0].tick_params(axis="x", labelbottom=False)
        horizon = case["stop"] - case["start"]
        edges = np.arange(horizon + 1)
        for key, color, label in (
            ("requested_load_mw", "#0072B2", "Demand"),
            ("available_renewables_mw", "#228833", "Nondispatchable availability"),
        ):
            values = np.asarray(case[key], dtype=float)
            if values.shape != (horizon,) or not np.isfinite(values).all():
                raise ValueError("Input trace does not match the retained horizon")
            context.stairs(
                values / 1000, edges, baseline=None, color=color, lw=1.3, label=label
            )
        context.set_title("(a) Operating conditions", pad=5)
        context.set_ylabel("Power (GW)")
        context.set_xlim(0, horizon)
        context.set_xticks(np.arange(0, horizon + 1, 6))
        context.set_ylim(bottom=0)
        context.grid(axis="y", color=".9", lw=0.5)
        context.tick_params(axis="x", labelbottom=False)
        fig.legend(
            handles=[
                Line2D([], [], color="#0072B2", lw=1.3, label="Demand"),
                Line2D(
                    [],
                    [],
                    color="#228833",
                    lw=1.3,
                    label="Nondispatchable availability",
                ),
                Line2D([], [], color="black", lw=1.8, label="Fleet total"),
                Line2D([], [], color=".65", lw=0.65, label="Individual batteries"),
            ],
            loc="upper center",
            bbox_to_anchor=(0.54, 0.99),
            frameon=False,
            ncol=2,
            fontsize=7,
            columnspacing=0.8,
            labelspacing=0.25,
        )
        for suffix in ("png", "pdf"):
            fig.savefig(output.with_suffix("." + suffix), dpi=190, facecolor="white")
        plt.close(fig)
        return fig


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    if args.render_only:
        bundle = json.loads((args.output / "soc_data.json").read_text())
    else:
        if args.output.exists():
            raise ValueError("Choose a fresh SoC review directory")
        bundle = collect()
        args.output.mkdir(parents=True)
        (args.output / "soc_data.json").write_text(
            json.dumps(bundle, indent=2, allow_nan=False) + "\n"
        )
    for number, case in enumerate(bundle["cases"]):
        render(case, bundle["capacity_mwh"], args.output / f"soc_case_{number}")
        print(f"soc_case_{number}: {case['scenario']}")
    (args.output / "manifest.json").write_text(
        json.dumps(
            dict(
                plot_source_sha256=sha(Path(__file__)),
                files={
                    p.name: sha(p)
                    for p in args.output.iterdir()
                    if p.suffix in ("png", "pdf") or p.name == "soc_data.json"
                },
                optimizer_calls=0,
            ),
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
