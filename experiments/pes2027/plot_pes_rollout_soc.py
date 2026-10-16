"""Compare Figure 3 joint schedules with a retained copper-plate-guided AC rollout.

Display only: reuse the paper's retained snapshots and their source registry;
reconstruct realized energy from executed first actions, never window forecasts.
No optimization or manuscript update is performed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from experiments.case118_tracy_2021.stage_d import read
from experiments.pes2027.plot_pes_results import ROOT, ROLLOUT, require
from experiments.pes2027.plot_pes_soc import normalized_soc, sha
from experiments.tracy_ac_rollout.run import no_optimization
from experiments.tracy_ac_rollout.worker import invocation, referenced


def reconstruct_boundaries(windows, initial, delta):
    """Integrate executed MW into MWh and independently check saved states."""
    state = np.asarray(initial, dtype=float)
    require(state.ndim == 1 and np.isfinite(state).all(), "Invalid initial energy")
    require(np.isfinite(delta) and delta > 0, "Invalid interval duration")
    boundaries = [state.copy()]
    for window in windows:
        np.testing.assert_allclose(window["initial_soc_mwh"], state, atol=1e-8, rtol=0)
        power = np.asarray(window["executed_interval"]["result"]["b"], dtype=float)
        require(power.shape == (1, state.size), "Expected exactly one executed action")
        require(np.isfinite(power).all(), "Nonfinite executed power")
        state = state - delta * power[0]
        np.testing.assert_allclose(
            window["post_step_soc_mwh"], state, atol=1e-8, rtol=0
        )
        boundaries.append(state.copy())
    return np.asarray(boundaries)


def collect(case_index=4, method="singlenode_dc"):
    """Read one identity-checked rollout; defaults reproduce the Figure 3 view."""
    sources = {}

    def track(path, expected=None):
        checksum = sha(path)
        require(expected is None or checksum == expected, f"Artifact changed: {path}")
        sources[str(path.relative_to(ROOT))] = checksum

    directory = ROOT / "experiments/case118_tracy_2021/results"
    metrics_path = directory / "pes_figures_review_001/figure_data.json"
    track(
        metrics_path, "0f07ff41289529b0d496f3085351b77f9c1817cf31512830fe3fffc6f5a27462"
    )
    metrics = read(metrics_path)
    joint_path = directory / "pes_soc_review_003/soc_data.json"
    track(
        joint_path, read(joint_path.parent / "manifest.json")["files"][joint_path.name]
    )
    joint = read(joint_path)
    case = joint["cases"][case_index]
    require(case["stop"] - case["start"] == 24, "Expected 24-hour scenario")
    binding_path = ROLLOUT / "binding.json"

    def retained(path):
        track(path, metrics["sources"][str(path.relative_to(ROOT))])
        return read(path)

    binding = retained(binding_path)
    source = "ac_reference" if method == "ac" else method
    rows = [
        row
        for row in binding["manifest"]["arms"]
        if row["scenario"] == case["scenario"] and row["source"] == source
    ]
    require(len(rows) == 1, "Expected one matching energy-signposted AC rollout")
    row = rows[0]
    require(
        row["plan"]["storage_device_ids"] == joint["storage_ids"],
        "Device order mismatch",
    )
    arm_dir = ROLLOUT / "arms" / row["arm_id"]
    checkpoint = retained(arm_dir / "checkpoint.json")
    outcome = retained(arm_dir / "outcome.json")
    require(
        checkpoint["complete"]
        and checkpoint["completed_intervals"] == 24
        and len(checkpoint["windows"]) == 24
        and outcome["classification"] == "complete",
        "Incomplete rollout",
    )
    windows = []
    previous = None
    for i, entry in enumerate(checkpoint["windows"]):
        path = arm_dir / entry["relative_path"]
        window = retained(path)
        require(
            entry["iteration"] == i
            and sha(path) == entry["sha256"]
            and path.stat().st_size == entry["bytes"],
            "Window identity mismatch",
        )
        action = window["executed_interval"]
        require(
            window["iteration"] == i
            and window["arm_id"] == row["arm_id"]
            and window["plan_id"] == row["plan"]["plan_id"]
            and window["storage_device_ids"] == joint["storage_ids"]
            and window["preceding_controlling_attempt_id"] == previous
            and action["audit"]["passed"],
            "Executed lifecycle mismatch",
        )
        for field in ("selected_record", "selected_supervision"):
            retained(ROLLOUT / window[field]["path"])
        selected = referenced(ROLLOUT, window["selected_record"])
        supervision = referenced(ROLLOUT, window["selected_supervision"])
        require(
            selected["classification"] == "accepted"
            and selected["hard_target"]
            and selected["independent_audit"]["passed"]
            and supervision["reaped"]
            and supervision["reason"] == "returned"
            and supervision["returncode"] == 0,
            "Unaccepted controlling solve",
        )
        require(
            invocation(selected["invocation"]).attempt_id
            == action["controlling_attempt_id"],
            "Controller identity mismatch",
        )
        np.testing.assert_array_equal(
            action["result"]["b"], np.asarray(selected["result"]["b"])[:1]
        )
        previous = action["controlling_attempt_id"]
        windows.append(window)
    require(
        previous == checkpoint["preceding_controlling_attempt_id"],
        "Final controller mismatch",
    )
    energy = reconstruct_boundaries(
        windows, row["initial_soc_mwh"], row["plan"]["delta_hours"]
    )
    np.testing.assert_allclose(
        energy[-1], checkpoint["realized_soc_mwh"], atol=1e-8, rtol=0
    )
    np.testing.assert_allclose(energy[-1], row["final_soc_mwh"], atol=1e-3, rtol=0)
    summary = next(
        r
        for r in metrics["rollouts"]
        if r["case"] == case_index and r["method"] == method
    )
    require(
        summary["independent_trajectory_audit"]["passed"], "Historical day audit failed"
    )
    return dict(
        case=case,
        capacity_mwh=joint["capacity_mwh"],
        storage_ids=joint["storage_ids"],
        rollout_boundary_soc_mwh=energy.tolist(),
        rollout_summary=summary,
        sources=sources,
        optimizer_calls=0,
        scope="Hash-checked historical trajectory audit; executed-energy reconstruction, not native replay.",
    )


def render(bundle, output):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    case = bundle["case"]
    trajectories = [a["boundary_soc_mwh"] for a in case["arms"]]
    trajectories.append(bundle["rollout_boundary_soc_mwh"])
    titles = (
        "Joint full AC",
        "Joint copper plate",
        "AC rollout: copper plate signposts",
    )
    with plt.rc_context(
        {
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
        }
    ):
        fig, axes = plt.subplots(
            3, 1, figsize=(6, 6.4), sharex=True, layout="constrained"
        )
        for ax, energy, title in zip(axes, trajectories, titles, strict=True):
            individual, fleet = normalized_soc(energy, bundle["capacity_mwh"])
            hours = np.arange(len(fleet))
            ax.plot(hours, individual, color=".65", alpha=0.6, lw=0.65)
            ax.plot(hours, fleet, color="black", lw=1.8, label="Fleet total")
            ax.set(
                title=title, ylabel="SoC / capacity", ylim=(-0.02, 1.02), xlim=(0, 24)
            )
            ax.set_yticks([0, 0.5, 1])
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=1, decimals=0))
            ax.grid(axis="y", color=".9", lw=0.5)
        axes[-1].set_xticks([0, 6, 12, 18, 24])
        axes[-1].set_xlabel("Hours from start · deficit to surplus [2439, 2463)")
        for suffix in ("png", "pdf"):
            fig.savefig(output.with_suffix("." + suffix), dpi=190, facecolor="white")
        plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.output.exists(), "Choose a fresh figure directory")
    with no_optimization():
        bundle = collect()
    args.output.mkdir(parents=True)
    (args.output / "figure_data.json").write_text(
        json.dumps(bundle, indent=2, allow_nan=False) + "\n"
    )
    render(bundle, args.output / "copper_plate_signposted_ac_soc")
    (args.output / "manifest.json").write_text(
        json.dumps(
            dict(
                plot_source_sha256=sha(Path(__file__)),
                optimizer_calls=0,
                files={p.name: sha(p) for p in args.output.iterdir()},
            ),
            indent=2,
        )
        + "\n"
    )
    print(args.output)


if __name__ == "__main__":
    main()
