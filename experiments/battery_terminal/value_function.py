"""Fixed-terminal-SoC value-function experiment."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from cvxopf.problem import OPFBuild
from cvxopf.results import extract_results
from cvxopf.testcases import case9

from experiments.battery_terminal.analysis import (
    SoCDecomposition,
    decompose_soc,
)
from experiments.battery_terminal.devices import (
    STORAGE_CAPACITY_MWH,
    STORAGE_INITIAL_SOC_MWH,
    make_storage,
)
from experiments.battery_terminal.problem_setup import (
    OPTIMAL_STATUSES,
    build_lossy_dc,
    prepare_experiment,
)
from experiments.battery_terminal.scenario import (
    REPRESENTATIVE_WINDOWS,
    ScenarioConfig,
    ScenarioData,
)


DEFAULT_TARGETS_MWH = tuple(float(value) for value in range(0, 1001, 50))
@dataclass(frozen=True)
class TerminalValueRun:
    """One fixed-terminal solve, including an infeasible solve if encountered."""

    scenario_name: str
    target_mwh: float
    scenario: ScenarioData
    build: OPFBuild
    results: dict
    decomposition: SoCDecomposition | None


@dataclass(frozen=True)
class TerminalValueSweep:
    """Value-function table and the underlying optimization results."""

    summary: pd.DataFrame
    runs: dict[tuple[str, float], TerminalValueRun]


def _validate_targets(targets_mwh: tuple[float, ...]) -> tuple[float, ...]:
    targets = tuple(float(target) for target in targets_mwh)
    if not targets:
        raise ValueError("targets_mwh must contain at least one target")
    if not np.isfinite(targets).all():
        raise ValueError("targets_mwh must be finite")
    if len(set(targets)) != len(targets):
        raise ValueError("targets_mwh must not contain duplicates")
    if any(target < 0 or target > STORAGE_CAPACITY_MWH for target in targets):
        raise ValueError(
            "targets_mwh must lie within the storage energy bounds "
            f"[0, {STORAGE_CAPACITY_MWH}]"
        )
    return tuple(sorted(targets))


def _max_constraint_violation(build: OPFBuild) -> float:
    violations = []
    for constraint in build.prob.constraints:
        try:
            value = np.asarray(constraint.violation(), dtype=float)
        except (TypeError, ValueError):
            continue
        if value.size and np.isfinite(value).any():
            violations.append(float(np.nanmax(value)))
    return max(violations, default=np.nan)


def _summary_row(run: TerminalValueRun) -> dict:
    row = {
        "scenario": run.scenario_name,
        "target_mwh": run.target_mwh,
        "status": run.results["status"],
        "objective": run.results["objective"],
        "terminal_soc_mwh": np.nan,
        "soc_min_mwh": np.nan,
        "soc_max_mwh": np.nan,
        "battery_min_mw": np.nan,
        "battery_max_mw": np.nan,
        "generation_mwh": np.nan,
        "curtailment_mwh": np.nan,
        "max_branch_utilization": np.nan,
        "max_constraint_violation": np.nan,
        "charging_excursions": np.nan,
        "discharging_excursions": np.nan,
        "classified_steps": np.nan,
        "final_excursion_steps": np.nan,
    }
    if run.decomposition is None:
        return row

    results = run.results
    soc = np.asarray(results["soc"], dtype=float)[:, 0]
    battery = np.asarray(results["b"], dtype=float)[:, 0]
    generation = np.asarray(results["Pg"], dtype=float).sum(axis=1)
    curtailment = (
        run.scenario.df_nd.to_numpy()
        - np.asarray(results["p_nd"], dtype=float)
    ).sum(axis=1)
    flows = np.asarray(results["p_flows"], dtype=float)
    ratings = case9()["branch"][:, 5]
    excursions = run.decomposition.excursions
    row.update(
        {
            "terminal_soc_mwh": soc[-1],
            "soc_min_mwh": np.min(soc),
            "soc_max_mwh": np.max(soc),
            "battery_min_mw": np.min(battery),
            "battery_max_mw": np.max(battery),
            "generation_mwh": np.sum(generation),
            "curtailment_mwh": np.sum(curtailment),
            "max_branch_utilization": np.max(np.abs(flows) / ratings),
            "max_constraint_violation": _max_constraint_violation(run.build),
            "charging_excursions": sum(
                excursion.kind == "charging" for excursion in excursions
            ),
            "discharging_excursions": sum(
                excursion.kind == "discharging" for excursion in excursions
            ),
            "classified_steps": run.decomposition.classified_steps,
            "final_excursion_steps": run.decomposition.final_excursion_steps,
        }
    )
    return row


def _add_secant_columns(summary: pd.DataFrame) -> pd.DataFrame:
    """Add local value slopes without bridging infeasible targets."""
    result = summary.copy()
    result["left_secant_cost_per_mwh"] = np.nan
    result["secant_slope_change"] = np.nan
    for scenario_name in result.index.get_level_values("scenario").unique():
        block = result.loc[scenario_name]
        targets = block.index.to_numpy(dtype=float)
        values = block["objective"].to_numpy(dtype=float)
        feasible = block["status"].isin(OPTIMAL_STATUSES).to_numpy()
        slopes = np.full(len(block), np.nan)
        adjacent = feasible[1:] & feasible[:-1]
        slopes[1:][adjacent] = (
            np.diff(values)[adjacent] / np.diff(targets)[adjacent]
        )
        changes = np.full(len(block), np.nan)
        valid_changes = np.isfinite(slopes[1:]) & np.isfinite(slopes[:-1])
        changes[1:][valid_changes] = np.diff(slopes)[valid_changes]
        result.loc[
            (scenario_name, slice(None)), "left_secant_cost_per_mwh"
        ] = slopes
        result.loc[
            (scenario_name, slice(None)), "secant_slope_change"
        ] = changes
    return result


def run_terminal_value_sweep(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
    scenario_names: tuple[str, ...] = tuple(REPRESENTATIVE_WINDOWS),
    targets_mwh: tuple[float, ...] = DEFAULT_TARGETS_MWH,
) -> TerminalValueSweep:
    """Evaluate the lossy-DC fixed-terminal value function ``V(q_T)``."""
    unknown_scenarios = set(scenario_names) - set(REPRESENTATIVE_WINDOWS)
    if unknown_scenarios:
        raise ValueError(f"Unknown scenarios: {sorted(unknown_scenarios)}")
    targets = _validate_targets(targets_mwh)

    prepared = prepare_experiment(source, scenario_config)

    runs = {}
    rows = []
    for scenario_name in scenario_names:
        scenario = prepared.scenarios[scenario_name]
        for target_mwh in targets:
            build = build_lossy_dc(
                prepared,
                scenario,
                make_storage(
                    terminal_soc=target_mwh,
                    terminal_constraint="equality",
                ),
            )
            build.solve()
            results = extract_results(build)
            decomposition = None
            if results["status"] in OPTIMAL_STATUSES:
                decomposition = decompose_soc(
                    results["soc"][:, 0],
                    initial_soc=STORAGE_INITIAL_SOC_MWH,
                    capacity=STORAGE_CAPACITY_MWH,
                )
            run = TerminalValueRun(
                scenario_name=scenario_name,
                target_mwh=target_mwh,
                scenario=scenario,
                build=build,
                results=results,
                decomposition=decomposition,
            )
            runs[(scenario_name, target_mwh)] = run
            rows.append(_summary_row(run))

    summary = pd.DataFrame(rows).set_index(["scenario", "target_mwh"])
    return TerminalValueSweep(
        summary=_add_secant_columns(summary),
        runs=runs,
    )
