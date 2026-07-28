"""Reproduce the tabular results for the battery terminal experiment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
import platform

import pandas as pd

from experiments.battery_terminal.ac_study import (
    AC_HORIZONS,
    run_ac_study,
)
from experiments.battery_terminal.runner import run_lossy_dc_sweep
from experiments.battery_terminal.resolution_study import (
    RESOLUTIONS_HOURS,
    run_resolution_study,
)
from experiments.battery_terminal.scenario import read_source_data
from experiments.battery_terminal.horizon_study import (
    HORIZONS,
    run_horizon_study,
)
from experiments.battery_terminal.followup_studies import (
    ADEQUACY_INITIAL_SOC_MWH,
    ADEQUACY_LOOKBACK_HOURS,
    ADEQUACY_PREFIX_HOURS,
    LOW_BREAKPOINT_TARGETS_MWH,
    run_low_breakpoint_refinement,
    run_moderate_adequacy_diagnostic,
)
from experiments.battery_terminal.soft_weights import (
    WEIGHT_GRIDS,
    run_soft_weight_sweep,
)
from experiments.battery_terminal.subset_study import (
    SUBSET_CASES,
    run_subset_study,
)
from experiments.battery_terminal.value_function import (
    DEFAULT_TARGETS_MWH,
    run_terminal_value_sweep,
)


DEFAULT_SOURCE = Path(
    "experiments/battery_terminal/data/9q9wtp_gen_and_load.csv"
)
DEFAULT_OUTPUT = Path("experiments/battery_terminal/results")


def _policy_trajectory_table(policy_sweep) -> pd.DataFrame:
    """Flatten retained policy trajectories for plotting and reporting."""
    frames = []
    for (scenario_name, policy_name), run in policy_sweep.runs.items():
        results = run.results
        curtailment = (
            run.scenario.df_nd.to_numpy() - results["p_nd"]
        ).sum(axis=1)
        frames.append(
            pd.DataFrame(
                {
                    "scenario": scenario_name,
                    "policy": policy_name,
                    "time": run.scenario.df_P.index.astype(str),
                    "step": range(len(run.scenario.df_P)),
                    "soc_mwh": results["soc"][:, 0],
                    "battery_mw": results["b"][:, 0],
                    "generation_mw": results["Pg"].sum(axis=1),
                    "curtailment_mw": curtailment,
                }
            )
        )
    return pd.concat(frames, ignore_index=True)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _metadata(source_path: Path, solver_names: set[str]) -> dict:
    return {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "source_path": str(source_path),
        "source_sha256": _sha256(source_path),
        "python": platform.python_version(),
        "packages": {
            package: version(package)
            for package in ("cvxopf", "cvxpy", "numpy", "pandas")
        },
        "terminal_value_targets_mwh": list(DEFAULT_TARGETS_MWH),
        "soft_weight_grids": {
            name: list(values) for name, values in WEIGHT_GRIDS.items()
        },
        "horizon_steps": list(HORIZONS),
        "adequacy_initial_soc_mwh": list(ADEQUACY_INITIAL_SOC_MWH),
        "adequacy_lookback_hours": list(ADEQUACY_LOOKBACK_HOURS),
        "adequacy_prefix_hours": list(ADEQUACY_PREFIX_HOURS),
        "low_breakpoint_targets_mwh": list(LOW_BREAKPOINT_TARGETS_MWH),
        "ac_horizon_steps": list(AC_HORIZONS),
        "subset_cases": {
            name: list(bounds) for name, bounds in SUBSET_CASES.items()
        },
        "resolution_hours": list(RESOLUTIONS_HOURS),
        "formulation": "lossy_dc",
        "solvers": sorted(solver_names),
        "time_step_hours": 1.0,
    }


def reproduce(source_path: Path, output_path: Path) -> None:
    """Run the approved policy and terminal-value sweeps."""
    source_path = source_path.resolve()
    output_path.mkdir(parents=True, exist_ok=True)
    source = read_source_data(source_path)

    policy_sweep = run_lossy_dc_sweep(source)
    policy_sweep.summary.to_csv(output_path / "policy_sweep.csv")
    _policy_trajectory_table(policy_sweep).to_csv(
        output_path / "policy_trajectories.csv",
        index=False,
    )
    solver_names = {
        run.build.prob.solver_stats.solver_name
        for run in policy_sweep.runs.values()
    }
    del policy_sweep

    value_sweep = run_terminal_value_sweep(source)
    value_sweep.summary.to_csv(output_path / "terminal_value_sweep.csv")
    solver_names.update(
        run.build.prob.solver_stats.solver_name
        for run in value_sweep.runs.values()
    )
    del value_sweep

    weight_sweep = run_soft_weight_sweep(source)
    weight_sweep.summary.to_csv(output_path / "soft_weight_sweep.csv")
    solver_names.update(
        run.build.prob.solver_stats.solver_name
        for run in weight_sweep.runs.values()
    )
    del weight_sweep

    horizon_study = run_horizon_study(source)
    horizon_study.summary.to_csv(output_path / "horizon_study.csv")
    horizon_study.locality.to_csv(output_path / "horizon_locality.csv")
    solver_names.update(
        run.build.prob.solver_stats.solver_name
        for run in horizon_study.runs.values()
    )
    del horizon_study

    adequacy = run_moderate_adequacy_diagnostic(source)
    adequacy.initial_soc.to_csv(
        output_path / "moderate_24_initial_soc.csv"
    )
    adequacy.lookback.to_csv(output_path / "moderate_lookback.csv")
    adequacy.prefix_capacity.to_csv(
        output_path / "moderate_prefix_capacity.csv"
    )

    breakpoint = run_low_breakpoint_refinement(source)
    breakpoint.summary.to_csv(output_path / "low_breakpoint.csv")
    solver_names.update(
        run.build.prob.solver_stats.solver_name
        for run in breakpoint.runs.values()
    )
    del breakpoint

    ac_study = run_ac_study(source)
    ac_study.summary.to_csv(output_path / "ac_study.csv")
    ac_study.locality.to_csv(output_path / "ac_locality.csv")
    solver_names.update(
        run.build.prob.solver_stats.solver_name
        for run in ac_study.runs.values()
    )
    del ac_study

    subset_study = run_subset_study(source)
    subset_study.summary.to_csv(output_path / "subset_study.csv")
    subset_study.comparison.to_csv(output_path / "subset_comparison.csv")
    subset_study.additivity.to_csv(output_path / "subset_additivity.csv")
    solver_names.update(
        run.build.prob.solver_stats.solver_name
        for run in subset_study.runs.values()
    )
    del subset_study

    resolution_study = run_resolution_study(source)
    resolution_study.summary.to_csv(output_path / "resolution_study.csv")
    resolution_study.comparison.to_csv(
        output_path / "resolution_comparison.csv"
    )
    resolution_study.energy_validation.to_csv(
        output_path / "resolution_energy_validation.csv"
    )
    solver_names.update(
        run.build.prob.solver_stats.solver_name
        for run in resolution_study.runs.values()
    )

    metadata = _metadata(source_path, solver_names)
    (output_path / "metadata.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    reproduce(args.source, args.output)


if __name__ == "__main__":
    main()
