"""Soft terminal-weight calibration study."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cvxopf.problem import OPFBuild
from cvxopf.results import extract_results

from experiments.battery_terminal.analysis import decompose_soc
from experiments.battery_terminal.devices import (
    STORAGE_CAPACITY_MWH,
    STORAGE_INITIAL_SOC_MWH,
    make_storage,
)
from experiments.battery_terminal.problem_setup import (
    build_lossy_dc,
    prepare_experiment,
)
from experiments.battery_terminal.scenario import (
    REPRESENTATIVE_WINDOWS,
    ScenarioConfig,
)


LINEAR_WEIGHTS = (0.01, 1.0, 5.0, 10.0, 12.5, 15.0, 17.5, 20.0, 25.0)
QUADRATIC_WEIGHTS = (0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 1.0)
WEIGHT_GRIDS = {
    "linear": LINEAR_WEIGHTS,
    "quadratic": QUADRATIC_WEIGHTS,
}


@dataclass(frozen=True)
class SoftWeightRun:
    """One solved soft terminal-cost configuration."""

    scenario_name: str
    cost_kind: str
    weight: float
    build: OPFBuild
    results: dict


@dataclass(frozen=True)
class SoftWeightSweep:
    """Soft-weight response table and complete solved runs."""

    summary: pd.DataFrame
    runs: dict[tuple[str, str, float], SoftWeightRun]


def _validate_grid(cost_kind: str, weights) -> tuple[float, ...]:
    if cost_kind not in WEIGHT_GRIDS:
        raise ValueError(f"Unknown terminal cost kind: {cost_kind!r}")
    values = tuple(float(weight) for weight in weights)
    if not values or not np.isfinite(values).all():
        raise ValueError("Weight grids must contain finite values")
    if any(weight <= 0 for weight in values):
        raise ValueError("Terminal weights must be positive")
    if len(set(values)) != len(values):
        raise ValueError("Weight grids must not contain duplicates")
    return tuple(sorted(values))


def run_soft_weight_sweep(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
    scenario_names: tuple[str, ...] = tuple(REPRESENTATIVE_WINDOWS),
    weight_grids: dict[str, tuple[float, ...]] = WEIGHT_GRIDS,
) -> SoftWeightSweep:
    """Solve two-sided linear and quadratic terminal-weight paths."""
    unknown_scenarios = set(scenario_names) - set(REPRESENTATIVE_WINDOWS)
    if unknown_scenarios:
        raise ValueError(f"Unknown scenarios: {sorted(unknown_scenarios)}")
    grids = {
        cost_kind: _validate_grid(cost_kind, weights)
        for cost_kind, weights in weight_grids.items()
    }
    prepared = prepare_experiment(source, scenario_config)

    runs = {}
    rows = []
    for scenario_name in scenario_names:
        scenario = prepared.scenarios[scenario_name]
        for cost_kind, weights in grids.items():
            for weight in weights:
                build = build_lossy_dc(
                    prepared,
                    scenario,
                    make_storage(
                        terminal_soc=STORAGE_INITIAL_SOC_MWH,
                        terminal_cost=cost_kind,
                        terminal_weight=weight,
                    ),
                )
                build.solve()
                results = extract_results(build)
                soc = np.asarray(results["soc"], dtype=float)[:, 0]
                decomposition = decompose_soc(
                    soc,
                    initial_soc=STORAGE_INITIAL_SOC_MWH,
                    capacity=STORAGE_CAPACITY_MWH,
                )
                terminal_cost = float(results["storage_terminal_cost"])
                run = SoftWeightRun(
                    scenario_name=scenario_name,
                    cost_kind=cost_kind,
                    weight=weight,
                    build=build,
                    results=results,
                )
                runs[(scenario_name, cost_kind, weight)] = run
                rows.append(
                    {
                        "scenario": scenario_name,
                        "cost_kind": cost_kind,
                        "weight": weight,
                        "status": results["status"],
                        "objective": results["objective"],
                        "operating_objective": (
                            results["objective"] - terminal_cost
                        ),
                        "terminal_cost": terminal_cost,
                        "terminal_soc_mwh": soc[-1],
                        "absolute_deviation_mwh": abs(
                            soc[-1] - STORAGE_INITIAL_SOC_MWH
                        ),
                        "final_excursion_steps": (
                            decomposition.final_excursion_steps
                        ),
                    }
                )

    summary = pd.DataFrame(rows).set_index(
        ["scenario", "cost_kind", "weight"]
    )
    return SoftWeightSweep(summary=summary, runs=runs)
