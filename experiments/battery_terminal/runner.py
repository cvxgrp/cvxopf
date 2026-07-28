"""Lossy-DC policy sweep for the battery terminal experiment."""

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
    build_lossy_dc,
    prepare_experiment,
)
from experiments.battery_terminal.scenario import (
    REPRESENTATIVE_WINDOWS,
    ScenarioConfig,
    ScenarioData,
)


LINEAR_TERMINAL_WEIGHT = 25.0
QUADRATIC_TERMINAL_WEIGHT = 0.05


@dataclass(frozen=True)
class PolicySpec:
    """One terminal-policy configuration in the approved sweep."""

    terminal_constraint: str | None = None
    terminal_cost: str | None = None
    terminal_weight: float | None = None


POLICIES = {
    "none": PolicySpec(),
    "equality": PolicySpec(terminal_constraint="equality"),
    "shortfall": PolicySpec(terminal_constraint="shortfall"),
    "linear": PolicySpec(
        terminal_cost="linear",
        terminal_weight=LINEAR_TERMINAL_WEIGHT,
    ),
    "quadratic": PolicySpec(
        terminal_cost="quadratic",
        terminal_weight=QUADRATIC_TERMINAL_WEIGHT,
    ),
    "shortfall_linear": PolicySpec(
        terminal_cost="shortfall_linear",
        terminal_weight=LINEAR_TERMINAL_WEIGHT,
    ),
    "shortfall_quadratic": PolicySpec(
        terminal_cost="shortfall_quadratic",
        terminal_weight=QUADRATIC_TERMINAL_WEIGHT,
    ),
}


@dataclass(frozen=True)
class SolvedRun:
    """One solved policy/scenario pair with geometry retained."""

    scenario_name: str
    policy_name: str
    scenario: ScenarioData
    build: OPFBuild
    results: dict
    decomposition: SoCDecomposition


@dataclass(frozen=True)
class SweepResult:
    """Scalar comparison table and full solved runs."""

    summary: pd.DataFrame
    runs: dict[tuple[str, str], SolvedRun]


def _make_policy_storage(policy: PolicySpec):
    terminal_active = (
        policy.terminal_constraint is not None
        or policy.terminal_cost is not None
    )
    return make_storage(
        terminal_soc=STORAGE_INITIAL_SOC_MWH if terminal_active else None,
        terminal_constraint=policy.terminal_constraint,
        terminal_cost=policy.terminal_cost,
        terminal_weight=policy.terminal_weight,
    )


def _interval_energy(
    values: np.ndarray,
    decomposition: SoCDecomposition,
    kind: str,
) -> float:
    return float(
        sum(
            np.sum(values[excursion.step_slice])
            for excursion in decomposition.excursions
            if excursion.kind == kind
        )
    )


def _run_summary(run: SolvedRun) -> dict:
    results = run.results
    scenario = run.scenario
    soc = np.asarray(results["soc"], dtype=float)[:, 0]
    battery = np.asarray(results["b"], dtype=float)[:, 0]
    pg = np.asarray(results["Pg"], dtype=float).sum(axis=1)
    curtailment = (
        scenario.df_nd.to_numpy()
        - np.asarray(results["p_nd"], dtype=float)
    ).sum(axis=1)
    flows = np.asarray(results["p_flows"], dtype=float)
    branch_ratings = case9()["branch"][:, 5]

    violations = []
    for constraint in run.build.prob.constraints:
        try:
            value = np.asarray(constraint.violation(), dtype=float)
        except (TypeError, ValueError):
            continue
        if value.size:
            violations.append(float(np.nanmax(value)))

    excursions = run.decomposition.excursions
    return {
        "scenario": run.scenario_name,
        "policy": run.policy_name,
        "status": results["status"],
        "objective": results["objective"],
        "terminal_soc_mwh": soc[-1],
        "terminal_deviation_mwh": (
            np.nan
            if "storage_terminal_deviation" not in results
            else results["storage_terminal_deviation"][0]
        ),
        "terminal_cost": results.get("storage_terminal_cost", np.nan),
        "soc_min_mwh": np.min(soc),
        "soc_max_mwh": np.max(soc),
        "battery_min_mw": np.min(battery),
        "battery_max_mw": np.max(battery),
        "generation_mwh": np.sum(pg),
        "curtailment_mwh": np.sum(curtailment),
        "max_branch_utilization": np.max(
            np.abs(flows) / branch_ratings
        ),
        "max_constraint_violation": max(violations, default=np.nan),
        "charging_excursions": sum(
            excursion.kind == "charging" for excursion in excursions
        ),
        "discharging_excursions": sum(
            excursion.kind == "discharging" for excursion in excursions
        ),
        "classified_steps": run.decomposition.classified_steps,
        "unclassified_steps": run.decomposition.unclassified_steps,
        "final_excursion_steps": run.decomposition.final_excursion_steps,
        "generation_charging_mwh": _interval_energy(
            pg, run.decomposition, "charging"
        ),
        "generation_discharging_mwh": _interval_energy(
            pg, run.decomposition, "discharging"
        ),
        "curtailment_charging_mwh": _interval_energy(
            curtailment, run.decomposition, "charging"
        ),
        "curtailment_discharging_mwh": _interval_energy(
            curtailment, run.decomposition, "discharging"
        ),
    }


def run_lossy_dc_sweep(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
    scenario_names: tuple[str, ...] = tuple(REPRESENTATIVE_WINDOWS),
    policy_names: tuple[str, ...] = tuple(POLICIES),
) -> SweepResult:
    """Solve the approved terminal policies on fixed representative windows."""
    unknown_scenarios = set(scenario_names) - set(REPRESENTATIVE_WINDOWS)
    if unknown_scenarios:
        raise ValueError(f"Unknown scenarios: {sorted(unknown_scenarios)}")
    unknown_policies = set(policy_names) - set(POLICIES)
    if unknown_policies:
        raise ValueError(f"Unknown policies: {sorted(unknown_policies)}")

    prepared = prepare_experiment(source, scenario_config)

    runs = {}
    rows = []
    for scenario_name in scenario_names:
        scenario = prepared.scenarios[scenario_name]
        for policy_name in policy_names:
            policy = POLICIES[policy_name]
            build = build_lossy_dc(
                prepared,
                scenario,
                _make_policy_storage(policy),
            )
            build.solve()
            results = extract_results(build)
            decomposition = decompose_soc(
                results["soc"][:, 0],
                initial_soc=STORAGE_INITIAL_SOC_MWH,
                capacity=STORAGE_CAPACITY_MWH,
            )
            run = SolvedRun(
                scenario_name=scenario_name,
                policy_name=policy_name,
                scenario=scenario,
                build=build,
                results=results,
                decomposition=decomposition,
            )
            key = (scenario_name, policy_name)
            runs[key] = run
            rows.append(_run_summary(run))

    summary = pd.DataFrame(rows).set_index(["scenario", "policy"])
    return SweepResult(summary=summary, runs=runs)
