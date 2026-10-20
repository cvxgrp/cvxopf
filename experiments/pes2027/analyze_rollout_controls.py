"""Reconstruct the six joint-AC-signposted control distances without solving."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from experiments.pes2027.plot_pes_rollout_soc import collect
from experiments.pes2027.plot_pes_soc import normalized_soc, sha
from experiments.tracy_ac_rollout.run import no_optimization


def trajectory_distance(realized, reference, capacities):
    """Use equal device weights and interior boundaries only, as in Appendix A5.

    The mean is the appendix's D; multiplying by 100 expresses it in percentage
    points of individual capacity. The maximum retains localized differences
    that a mean or fleet sum could hide. No relative reduction is defined for
    this control because its donor-to-reference distance is exactly zero.
    """
    actual, actual_fleet = normalized_soc(realized, capacities)
    target, target_fleet = normalized_soc(reference, capacities)
    if actual.shape != target.shape or actual.shape[0] < 3:
        raise ValueError("Expected aligned trajectories with interior boundaries")
    difference = np.abs(actual[1:-1] - target[1:-1])
    fleet_difference = np.abs(actual_fleet[1:-1] - target_fleet[1:-1])
    return dict(
        capacity_normalized_mae=float(difference.mean()),
        individual_mae_percentage_points=float(100 * difference.mean()),
        individual_max_percentage_points=float(100 * difference.max()),
        fleet_mae_percentage_points=float(100 * fleet_difference.mean()),
        fleet_mae_mwh=float(fleet_difference.mean() * np.sum(capacities)),
        interior_boundaries=actual.shape[0] - 2,
        batteries=actual.shape[1],
    )


def analyze():
    rows = []
    sources = {}
    for case_index in range(6):
        bundle = collect(case_index, "ac")
        case = bundle["case"]
        reference = next(a for a in case["arms"] if a["method"] == "ac")
        rows.append(
            dict(
                case=case_index,
                scenario=case["scenario"],
                start=case["start"],
                stop=case["stop"],
                **trajectory_distance(
                    bundle["rollout_boundary_soc_mwh"],
                    reference["boundary_soc_mwh"],
                    bundle["capacity_mwh"],
                ),
                device_cost_difference=bundle["rollout_summary"]["deltas"][
                    "device_cost"
                ],
            )
        )
        sources.update(bundle["sources"])
    return dict(
        controls=rows,
        sources=sources,
        optimizer_calls=0,
        scope="One retained rollout pass against its joint-AC donor; not repeated-pass convergence.",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with no_optimization():
        result = analyze()
    result["analysis_sources"] = {
        f"experiments/pes2027/{name}": sha(Path(__file__).with_name(name))
        for name in (
            "analyze_rollout_controls.py",
            "plot_pes_rollout_soc.py",
            "plot_pes_soc.py",
        )
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        stream.write(json.dumps(result, indent=2, allow_nan=False) + "\n")
    for row in result["controls"]:
        print(
            f"{row['scenario']}: mean {row['individual_mae_percentage_points']:.6f} pp; "
            f"max {row['individual_max_percentage_points']:.6f} pp; "
            f"fleet mean {row['fleet_mae_mwh']:.6f} MWh"
        )


if __name__ == "__main__":
    main()
