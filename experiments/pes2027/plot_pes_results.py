"""Non-solving PES review figures from retained joint, transfer, and rollout data.

This reconstructs executed-only rollout physics/accounting, not the full native
start/recovery audit. Historical joint/transfer classifications remain unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from experiments.case118_tracy_2021.e3_results import COLORS, sha
from experiments.case118_tracy_2021.stage_d import read
from experiments.numerical_preparation.audit import serializable
from experiments.tracy_ac_rollout import audit, model
from experiments.tracy_ac_rollout.run import no_optimization
from experiments.tracy_ac_rollout.worker import invocation, referenced

ROOT = model.ROOT
ROLLOUT = ROOT / "experiments/tracy_ac_rollout/results/rollout_001"
FIGURE_NOTES = Path(__file__).with_name("figure_notes.md")
METHODS = ("singlenode_dc", "lossy_dc", "socp", "ac")
LABELS = dict(zip(METHODS, ("Copper plate", "Network flow", "SOCP", "Full AC")))
MARKERS = dict(zip(METHODS, ("s", "D", "^", "o")))
SCENARIOS = (
    "Large surplus",
    "Large deficit",
    "Deficit, depletion",
    "Surplus → deficit",
    "Deficit → surplus",
    "Forced shedding",
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def totals(result, costs, delta):
    """Common costs exclude the DC network penalty; retain raw small values."""
    return dict(
        device_cost=float(sum(v for k, v in costs.items() if k != "dc_loss_cost")),
        cost_components={k: float(v) for k, v in costs.items()},
        ens_mwh=float(delta * np.asarray(result["p_load_shed"]).sum()),
        curtailed_mwh=float(delta * np.asarray(result["curtailment"]).sum()),
        demand_mwh=float(delta * np.asarray(result["p_load"]).sum()),
        storage_throughput_mwh=float(delta * np.abs(result["b"]).sum()),
    )


def collect():
    print("Admitting retained joint/transfer evidence once…", flush=True)
    manifest, data, prepared = model.inventory()
    binding = read(ROLLOUT / "binding.json")
    require(binding["manifest"] == manifest, "Retained rollout input inventory differs")
    sources = dict(manifest["sources"])

    def track(path):
        sources[str(path.relative_to(ROOT))] = sha(path)

    track(ROLLOUT / "binding.json")
    cases = list(data["cases"])
    require(len(cases) == 6, "Expected six scenarios")
    expected = (
        "Large surplus · energy neutral",
        "Large deficit · energy neutral",
        "Large deficit · deficit depletion",
        "Surplus to deficit · energy neutral",
        "Deficit to surplus · energy neutral",
        "Forced shedding · energy neutral",
    )
    require(
        all(
            case.startswith(label + " · hours") for case, label in zip(cases, expected)
        ),
        "Scenario order/labels changed",
    )
    joint, fixed, rollouts = [], [], []
    for index, (scenario, records) in enumerate(data["cases"].items()):
        for method in METHODS:
            rec = records[method]
            kwargs = model.original_kwargs(rec, prepared)
            joint.append(
                dict(
                    scenario=scenario,
                    case=index,
                    method=method,
                    classification=rec["classification"],
                    qualified=rec["classification"] != "accepted",
                    **totals(rec["result"], rec["audit"]["costs"], kwargs["delta"]),
                )
            )
    transfers, _ = model.retained_transfers()
    for rec in transfers:
        values = (
            totals(rec["result"], rec["named_costs"], 1.0)
            if rec["retained_compatible"]
            else {}
        )
        fixed.append(
            dict(
                scenario=rec["scenario"],
                case=cases.index(rec["scenario"]),
                method=rec["donor"],
                classification=rec["retained_classification"],
                accepted=rec["retained_compatible"],
                **values,
            )
        )

    for row in manifest["arms"]:
        directory = ROLLOUT / "arms" / row["arm_id"]
        cp, outcome = (
            read(directory / "checkpoint.json"),
            read(directory / "outcome.json"),
        )
        track(directory / "checkpoint.json")
        track(directory / "outcome.json")
        require(
            cp["complete"]
            and cp["completed_intervals"] == len(cp["windows"]) == 24
            and outcome["classification"] == "complete",
            "Incomplete rollout",
        )
        executed = []
        previous = None
        state = np.asarray(row["initial_soc_mwh"])
        for i, entry in enumerate(cp["windows"]):
            path = directory / entry["relative_path"]
            require(
                entry["iteration"] == i
                and sha(path) == entry["sha256"]
                and path.stat().st_size == entry["bytes"],
                "Window identity mismatch",
            )
            track(path)
            saved = read(path)
            action = saved["executed_interval"]
            require(
                saved["arm_id"] == row["arm_id"]
                and saved["iteration"] == i
                and saved["plan_id"] == row["plan"]["plan_id"]
                and saved["storage_device_ids"] == row["plan"]["storage_device_ids"]
                and saved["preceding_controlling_attempt_id"] == previous
                and action["audit"]["passed"],
                "Executed chain mismatch",
            )
            np.testing.assert_allclose(
                saved["initial_soc_mwh"], state, atol=1e-8, rtol=0
            )
            selected = referenced(ROLLOUT, saved["selected_record"])
            supervision = referenced(ROLLOUT, saved["selected_supervision"])
            track(ROLLOUT / saved["selected_record"]["path"])
            track(ROLLOUT / saved["selected_supervision"]["path"])
            require(
                selected["classification"] == "accepted"
                and selected["hard_target"]
                and selected["independent_audit"]["passed"]
                and supervision["reaped"]
                and supervision["reason"] == "returned"
                and supervision["returncode"] == 0,
                "Executed action lacks an accepted supervised controller",
            )
            require(
                invocation(selected["invocation"]).attempt_id
                == action["controlling_attempt_id"],
                "Controlling attempt mismatch",
            )
            for key in audit.TIME_FIELDS:
                if (
                    key != "soc"
                ):  # executed SoC is reconstructed, not the native forecast
                    np.testing.assert_array_equal(
                        action["result"][key], np.asarray(selected["result"][key])[:1]
                    )
            state = (
                state
                - np.asarray(action["result"]["b"])[0] * row["plan"]["delta_hours"]
            )
            np.testing.assert_allclose(
                state, saved["post_step_soc_mwh"], atol=1e-8, rtol=0
            )
            executed.append(action["result"])
            previous = action["controlling_attempt_id"]
        require(
            cp["preceding_controlling_attempt_id"] == previous,
            "Final controller mismatch",
        )
        np.testing.assert_allclose(state, cp["realized_soc_mwh"], atol=1e-8, rtol=0)
        reference = data["cases"][row["scenario"]]["ac"]
        original = model.original_kwargs(reference, prepared)
        trajectory = audit.trajectory(executed, original, complete=True)
        require(
            trajectory["audit"]["passed"], f"Trajectory audit failed: {row['arm_id']}"
        )
        if reference["cohort"] == "prepared_plus":
            from experiments.case118_tracy_2021 import e3_plus

            witness = e3_plus.fixture_reference()["ac"]["stress"]["modified"][
                "minimum_ens_mwh"
            ]
            require(
                trajectory["audit"]["metrics"]["energy_not_served_mwh"]
                + e3_plus.WITNESS_ALLOWANCE_MWH
                >= witness,
                "Forced-shedding witness failed",
            )
        rollouts.append(
            dict(
                case=cases.index(row["scenario"]),
                scenario=row["scenario"],
                method="ac" if row["source"] == "ac_reference" else row["source"],
                accepted=True,
                independent_trajectory_audit=trajectory["audit"],
                source_classification=row["plan"]["source_classification"],
                **totals(
                    trajectory["result"],
                    trajectory["audit"]["costs"],
                    original["delta"],
                ),
            )
        )
        print(
            f"Reconstructed {row['arm_id']}: {row['source']}; physical day audit passed",
            flush=True,
        )

    for rows in (fixed, rollouts):
        for row in rows:
            if row["accepted"]:
                ref = next(
                    x for x in joint if x["case"] == row["case"] and x["method"] == "ac"
                )
                row["deltas"] = {
                    k: row[k] - ref[k]
                    for k in ("device_cost", "ens_mwh", "curtailed_mwh")
                }
    track(Path(__file__).resolve())
    for relative in (
        "experiments/tracy_ac_rollout/audit.py",
        "experiments/tracy_ac_rollout/model.py",
        "experiments/case118_tracy_2021/e3_results.py",
        "experiments/case118_tracy_2021/stage_b.py",
        "experiments/tracy_socp_transfer_preflight/MULTIGRAPH_REPORT.md",
    ):
        track(ROOT / relative)
    return dict(
        joint=joint,
        fixed=fixed,
        rollouts=rollouts,
        scenarios=cases,
        sources=sources,
        scope="Hash-checked retained evidence and independently reconstructed executed-day physics/cost; not a full native/recovery replay.",
        numerical_comparison="Pending: matched study has no completed results at figure-draft creation.",
        optimizer_calls=0,
    )


def render(bundle, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.titlesize": 9,
            "axes.labelsize": 8,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "savefig.dpi": 190,
        }
    )
    offsets = dict(zip(METHODS, (-0.24, -0.08, 0.08, 0.24)))
    titles = ("(a) Device cost", "(b) Energy not served", "(c) Curtailment")
    keys = ("device_cost", "ens_mwh", "curtailed_mwh")

    def legend(fig, rollout=False, three=False, combined=False):
        handles = [
            Line2D(
                [],
                [],
                color=COLORS[m],
                marker=MARKERS[m],
                linestyle="none",
                markersize=4.25,
                label=(
                    "AC signposts (rollout only)"
                    if combined and m == "ac"
                    else "AC signposts"
                    if rollout and m == "ac"
                    else LABELS[m]
                ),
            )
            for m in METHODS
            if not (three and m == "ac")
        ]
        fig.legend(
            handles=handles,
            loc="upper center",
            bbox_to_anchor=(0.59, 0.985),
            ncol=len(handles),
            frameon=False,
            columnspacing=1.3,
        )

    def base(rows=1):
        fig, axes = plt.subplots(
            rows, 3, figsize=(7.2, {1: 3.0, 2: 5.4, 3: 8.2}[rows]), sharey=True
        )
        fig.subplots_adjust(
            left=0.205,
            right=0.985,
            top=0.82 if rows == 1 else 0.88 if rows == 2 else 0.875,
            bottom=0.18 if rows == 1 else 0.10 if rows == 2 else 0.06,
            wspace=0.26,
            hspace=0.36 if rows < 3 else 0.60,
        )
        for j, ax in enumerate(axes.flat):
            ax.set_ylim(5.55, -0.55)
            ax.set_yticks(range(6), SCENARIOS)
            ax.tick_params(axis="y", length=0, pad=6)
            ax.set_title(titles[j % 3], pad=8)
            for i in range(0, 6, 2):
                ax.axhspan(i - 0.47, i + 0.47, color="#f3f5f7", zorder=0)
            ax.grid(axis="x", which="major", color="#dde0e3", lw=0.65)
            ax.set_axisbelow(True)
        return fig, axes

    def tick(value, _):
        if value == 0:
            return "0"
        magnitude = abs(value)
        if magnitude >= 1e9:
            return f"{value / 1e9:g}B"
        if magnitude >= 1e6:
            return f"{value / 1e6:g}M"
        if magnitude >= 1000:
            return f"{value / 1000:g}k"
        return f"{value:g}"

    def save(fig, stem):
        for ext in ("png", "pdf"):
            fig.savefig(output / f"{stem}.{ext}", facecolor="white")
        plt.close(fig)

    def joint(axes):
        for j, ax in enumerate(axes):
            key = keys[j]
            for row in bundle["joint"]:
                method = row["method"]
                value = row[key] / (1000 if j == 2 else 1)
                # Preserve raw signed values; qualifications belong in captions.
                y = row["case"] + offsets[method]
                ax.plot([0, value], [y, y], color=COLORS[method], alpha=0.26, lw=1.1)
                ax.plot(
                    value,
                    y,
                    marker=MARKERS[method],
                    ms=3.825,
                    color=COLORS[method],
                    zorder=3,
                )
            ax.axvline(0, color="#666666", lw=0.8, zorder=1)
            if j == 0:
                ax.set_xscale("symlog", linthresh=1)
                ax.set_xticks([0, 1, 1e3, 1e6, 1e9])
                ax.set_xlabel("Cost units (symlog)")
            elif j == 1:
                ax.set_xscale("symlog", linthresh=1)
                ax.set_xticks([0, 1, 1e3, 1e6])
                ax.set_xlabel("MWh (symlog)")
            else:
                ax.set_xlabel("GWh")
            values = [r[key] / (1000 if j == 2 else 1) for r in bundle["joint"]]
            if j < 2:
                # Pad symlog in display space, avoiding an empty negative range.
                ax.set_xlim(min(-0.5, min(values) * 3), max(2, max(values) * 3))
            else:
                pad = max(1, max(abs(v) for v in values) * 0.05)
                ax.set_xlim(min(0, min(values)) - pad, max(values) + pad)
            ax.xaxis.set_major_formatter(FuncFormatter(tick))
            ax.set_yticks(range(6), SCENARIOS)
            ax.tick_params(axis="y", labelleft=(j == 0))

    fig, axes = base()
    legend(fig)
    joint(axes)
    save(fig, "01_joint_dispatch")

    # Identical axes between the two AC-implementation figures; preserve signs.
    limits = []
    for key, threshold in zip(keys, (100, 1, 1)):
        values = [
            r["deltas"][key]
            for group in ("fixed", "rollouts")
            for r in bundle[group]
            if r["accepted"]
        ]
        limits.append(
            (min(-threshold, min(values) * 1.5), max(threshold * 2, max(values) * 1.7))
        )

    def differences(axes, group, panel_offset=0):
        for j, ax in enumerate(axes):
            ax.set_title(
                f"({chr(ord('a') + panel_offset + j)}) "
                + ("Cost difference", "ENS difference", "Curtailment difference")[j],
                pad=8,
            )
            for row in bundle[group]:
                if not row["accepted"]:
                    continue
                method = row["method"]
                y = row["case"] + offsets[method]
                value = row["deltas"][keys[j]]
                ax.plot([0, value], [y, y], color=COLORS[method], alpha=0.26, lw=1.1)
                ax.plot(
                    value,
                    y,
                    marker=MARKERS[method],
                    ms=3.825,
                    color=COLORS[method],
                    zorder=3,
                )
            ax.set_xscale("symlog", linthresh=100 if j == 0 else 1)
            ax.set_xlim(limits[j])
            ax.axvline(0, color="#666666", lw=0.8, zorder=1)
            ax.set_xlabel(("Cost units" if j == 0 else "MWh") + " (symlog)")
            ax.xaxis.set_major_formatter(FuncFormatter(tick))
            if j == 0:
                # Sparse decade labels remain legible at two-column width;
                # setting ticks must not expand the matched physical limits.
                candidates = (-1e7, -1e5, -1e3, 0, 1e3, 1e5, 1e7)
                ax.set_xticks(
                    [x for x in candidates if limits[j][0] <= x <= limits[j][1]]
                )
            ax.set_yticks(range(6), SCENARIOS)
            ax.tick_params(axis="y", labelleft=(j == 0))
            if group == "fixed":
                # This retrospective certificate belongs to the existing report,
                # not to the failed solver's disposition or this plotting code.
                failed = [r for r in bundle[group] if not r["accepted"]]
                require(
                    len(failed) == 3 and all(r["case"] == 4 for r in failed),
                    "Failure pattern changed",
                )
                ax.text(
                    0.5,
                    4,
                    "n/a",
                    transform=ax.get_yaxis_transform(),
                    ha="center",
                    va="center",
                    fontsize=8,
                    color="#555555",
                    bbox=dict(facecolor="white", edgecolor="none", pad=2),
                )

    for group, stem in (
        ("fixed", "03_fixed_schedule_transfer"),
        ("rollouts", "04_ac_rollouts"),
    ):
        fig, axes = base()
        legend(fig, rollout=group == "rollouts", three=group == "fixed")
        differences(axes, group)
        save(fig, stem)

    fig, axes = base(rows=2)
    legend(fig, rollout=True, combined=True)
    differences(axes[0], "fixed")
    differences(axes[1], "rollouts", panel_offset=3)
    save(fig, "03_04_ac_implementation")

    fig, axes = base(rows=3)
    legend(fig)
    joint(axes[0])
    differences(axes[1], "fixed")
    differences(axes[2], "rollouts")
    for row, heading in enumerate(
        (
            "(a) Joint dispatch",
            "(b) Fixed-power AC transfer",
            "(c) Energy-signpost AC rollout",
        )
    ):
        fig.text(
            0.205,
            axes[row, 0].get_position().y1 + 0.055,
            heading,
            fontsize=9,
            fontweight="bold",
        )
        for j, ax in enumerate(axes[row]):
            ax.set_title(
                ("Device cost", "Energy not served", "Curtailment")[j]
                if row == 0
                else ("Cost difference", "ENS difference", "Curtailment difference")[j],
                pad=8,
            )
    save(fig, "01_03_04_results")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render-only", action="store_true")
    args = parser.parse_args()
    if args.render_only:
        bundle = read(args.output / "figure_data.json")
    else:
        require(not args.output.exists(), "Choose a fresh output directory")
        with no_optimization():
            bundle = collect()
        args.output.mkdir(parents=True)
        (args.output / "figure_data.json").write_text(
            json.dumps(serializable(bundle), indent=2, allow_nan=False) + "\n"
        )
    render(bundle, args.output)
    (args.output / "figure_notes.md").write_text(FIGURE_NOTES.read_text())
    files = [
        p
        for p in args.output.iterdir()
        if p.suffix in (".png", ".pdf")
        or p.name in ("figure_data.json", "figure_notes.md")
    ]
    (args.output / "manifest.json").write_text(
        json.dumps(
            dict(
                plot_source_sha256=sha(Path(__file__)),
                figure_notes_source_sha256=sha(FIGURE_NOTES),
                source_hashes=bundle["sources"],
                files={p.name: sha(p) for p in files},
                optimizer_calls=0,
                numerical_comparison=bundle["numerical_comparison"],
                scope=bundle["scope"],
            ),
            indent=2,
        )
        + "\n"
    )
    print(args.output)


if __name__ == "__main__":
    main()
