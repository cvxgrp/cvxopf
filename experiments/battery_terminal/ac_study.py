"""Staged short-horizon AC terminal-policy study."""

from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter

import numpy as np
import pandas as pd

from cvxopf.problem import OPFBuild
from cvxopf.results import extract_results

from experiments.battery_terminal.analysis import (
    SoCDecomposition,
    compare_soc_trajectories,
    decompose_soc,
)
from experiments.battery_terminal.devices import (
    STORAGE_CAPACITY_MWH,
    STORAGE_INITIAL_SOC_MWH,
    make_storage,
)
from experiments.battery_terminal.problem_setup import (
    OPTIMAL_STATUSES,
    build_ac,
    prepare_experiment,
)
from experiments.battery_terminal.runner import POLICIES, PolicySpec
from experiments.battery_terminal.scenario import (
    ScenarioConfig,
    ScenarioData,
)


AC_HORIZONS = (12, 24)
AC_POLICIES = {
    "none": POLICIES["none"],
    "quadratic": POLICIES["quadratic"],
    "equality": POLICIES["equality"],
}


@dataclass(frozen=True)
class ACStudyRun:
    """One cold-start AC policy solve."""

    horizon_steps: int
    policy_name: str
    scenario: ScenarioData
    build: OPFBuild
    results: dict
    decomposition: SoCDecomposition | None
    wall_time_seconds: float


@dataclass(frozen=True)
class ACStudy:
    """Staged AC results and retained optimization builds."""

    summary: pd.DataFrame
    locality: pd.DataFrame
    runs: dict[tuple[int, str], ACStudyRun]
    gate_passed: bool


def _suffix(scenario: ScenarioData, horizon_steps: int) -> ScenarioData:
    return replace(
        scenario,
        df_P=scenario.df_P.iloc[-horizon_steps:].copy(),
        df_Q=scenario.df_Q.iloc[-horizon_steps:].copy(),
        df_nd=scenario.df_nd.iloc[-horizon_steps:].copy(),
    )


def _storage(policy: PolicySpec):
    active = policy.terminal_constraint is not None or policy.terminal_cost
    return make_storage(
        terminal_soc=STORAGE_INITIAL_SOC_MWH if active else None,
        terminal_constraint=policy.terminal_constraint,
        terminal_cost=policy.terminal_cost,
        terminal_weight=policy.terminal_weight,
    )


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


def _summary_row(run: ACStudyRun) -> dict:
    row = {
        "horizon_steps": run.horizon_steps,
        "policy": run.policy_name,
        "status": run.results["status"],
        "wall_time_seconds": run.wall_time_seconds,
        "solver_time_seconds": run.build.prob.solver_stats.solve_time,
        "objective": run.results["objective"],
        "operating_objective": np.nan,
        "terminal_cost": np.nan,
        "terminal_soc_mwh": np.nan,
        "terminal_deviation_mwh": np.nan,
        "soc_min_mwh": np.nan,
        "soc_max_mwh": np.nan,
        "battery_p_min_mw": np.nan,
        "battery_p_max_mw": np.nan,
        "battery_q_min_mvar": np.nan,
        "battery_q_max_mvar": np.nan,
        "generator_p_min_mw": np.nan,
        "generator_p_max_mw": np.nan,
        "generator_q_min_mvar": np.nan,
        "generator_q_max_mvar": np.nan,
        "renewable_q_min_mvar": np.nan,
        "renewable_q_max_mvar": np.nan,
        "renewable_max_q_fraction": np.nan,
        "renewable_max_apparent_utilization": np.nan,
        "voltage_min_pu": np.nan,
        "voltage_max_pu": np.nan,
        "curtailment_mwh": np.nan,
        "physical_network_loss_mwh": np.nan,
        "max_constraint_violation": np.nan,
        "final_excursion_steps": np.nan,
    }
    if run.decomposition is None:
        return row

    results = run.results
    scenario = run.scenario
    soc = np.asarray(results["soc"], dtype=float)[:, 0]
    battery_p = np.asarray(results["b"], dtype=float)[:, 0]
    battery_q = np.asarray(results["b_q"], dtype=float)[:, 0]
    generator_p = np.asarray(results["Pg"], dtype=float)
    generator_q = np.asarray(results["Qg"], dtype=float)
    renewable_p = np.asarray(results["p_nd"], dtype=float)
    renewable_q = np.asarray(results["q_nd"], dtype=float)
    renewable_rating = np.asarray(
        run.build.data["nd_apparent_power_rating"],
        dtype=float,
    )
    load = scenario.df_P.to_numpy().sum(axis=1)
    curtailment = scenario.df_nd.to_numpy() - renewable_p
    physical_loss = (
        generator_p.sum(axis=1)
        + renewable_p.sum(axis=1)
        + battery_p
        - load
    )
    terminal_cost = float(results.get("storage_terminal_cost", 0.0))
    terminal_deviation = results.get("storage_terminal_deviation")
    row.update(
        {
            "operating_objective": results["objective"] - terminal_cost,
            "terminal_cost": terminal_cost,
            "terminal_soc_mwh": soc[-1],
            "terminal_deviation_mwh": (
                np.nan
                if terminal_deviation is None
                else float(terminal_deviation[0])
            ),
            "soc_min_mwh": np.min(soc),
            "soc_max_mwh": np.max(soc),
            "battery_p_min_mw": np.min(battery_p),
            "battery_p_max_mw": np.max(battery_p),
            "battery_q_min_mvar": np.min(battery_q),
            "battery_q_max_mvar": np.max(battery_q),
            "generator_p_min_mw": np.min(generator_p),
            "generator_p_max_mw": np.max(generator_p),
            "generator_q_min_mvar": np.min(generator_q),
            "generator_q_max_mvar": np.max(generator_q),
            "renewable_q_min_mvar": np.min(renewable_q),
            "renewable_q_max_mvar": np.max(renewable_q),
            "renewable_max_q_fraction": np.max(
                np.abs(renewable_q) / renewable_rating
            ),
            "renewable_max_apparent_utilization": np.max(
                np.sqrt(renewable_p**2 + renewable_q**2)
                / renewable_rating
            ),
            "voltage_min_pu": np.min(results["Vm"]),
            "voltage_max_pu": np.max(results["Vm"]),
            "curtailment_mwh": np.sum(curtailment),
            "physical_network_loss_mwh": np.sum(physical_loss),
            "max_constraint_violation": _max_constraint_violation(run.build),
            "final_excursion_steps": (
                run.decomposition.final_excursion_steps
            ),
        }
    )
    return row


def run_ac_study(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
    horizons: tuple[int, ...] = AC_HORIZONS,
    policies: dict[str, PolicySpec] = AC_POLICIES,
) -> ACStudy:
    """Run the high-window 12-hour gate and conditional 24-hour stage."""
    if tuple(horizons) != AC_HORIZONS:
        raise ValueError(f"AC study horizons must be {AC_HORIZONS}")
    if tuple(policies) != tuple(AC_POLICIES):
        raise ValueError(
            f"AC study policies must be {tuple(AC_POLICIES)} in that order"
        )

    prepared = prepare_experiment(source, scenario_config)
    full_scenario = prepared.scenarios["high"]
    runs = {}
    rows = []
    gate_passed = False
    for horizon_steps in horizons:
        if horizon_steps == 24 and not gate_passed:
            break
        scenario = _suffix(full_scenario, horizon_steps)
        horizon_statuses = []
        for policy_name, policy in policies.items():
            build = build_ac(prepared, scenario, _storage(policy))
            start = perf_counter()
            build.solve()
            wall_time_seconds = perf_counter() - start
            results = extract_results(build)
            decomposition = None
            if results["status"] in OPTIMAL_STATUSES:
                decomposition = decompose_soc(
                    results["soc"][:, 0],
                    initial_soc=STORAGE_INITIAL_SOC_MWH,
                    capacity=STORAGE_CAPACITY_MWH,
                )
            run = ACStudyRun(
                horizon_steps=horizon_steps,
                policy_name=policy_name,
                scenario=scenario,
                build=build,
                results=results,
                decomposition=decomposition,
                wall_time_seconds=wall_time_seconds,
            )
            runs[(horizon_steps, policy_name)] = run
            rows.append(_summary_row(run))
            horizon_statuses.append(results["status"])
        if horizon_steps == 12:
            gate_passed = all(
                status in OPTIMAL_STATUSES for status in horizon_statuses
            )

    locality_rows = []
    for horizon_steps in horizons:
        reference = runs.get((horizon_steps, "none"))
        if reference is None:
            continue
        for policy_name in policies:
            if policy_name == "none":
                continue
            candidate = runs[(horizon_steps, policy_name)]
            comparable = (
                reference.results["status"] in OPTIMAL_STATUSES
                and candidate.results["status"] in OPTIMAL_STATUSES
            )
            locality = None
            if comparable:
                locality = compare_soc_trajectories(
                    reference.results["soc"][:, 0],
                    candidate.results["soc"][:, 0],
                    initial_soc=STORAGE_INITIAL_SOC_MWH,
                    capacity=STORAGE_CAPACITY_MWH,
                )
            locality_rows.append(
                {
                    "horizon_steps": horizon_steps,
                    "policy": policy_name,
                    "comparable": comparable,
                    "first_divergent_state": (
                        np.nan
                        if locality is None
                        else locality.first_divergent_state
                    ),
                    "last_common_boundary_state": (
                        np.nan
                        if locality is None
                        else locality.last_common_boundary_state
                    ),
                    "divergence_precedes_last_common_boundary": (
                        np.nan
                        if locality is None
                        else (
                            locality
                            .divergence_precedes_last_common_boundary
                        )
                    ),
                    "affected_suffix_steps": (
                        horizon_steps
                        - locality.first_divergent_state
                        + 1
                        if (
                            locality is not None
                            and locality.first_divergent_state is not None
                        )
                        else 0
                    ),
                }
            )

    return ACStudy(
        summary=pd.DataFrame(rows).set_index(["horizon_steps", "policy"]),
        locality=pd.DataFrame(locality_rows).set_index(
            ["horizon_steps", "policy"]
        ),
        runs=runs,
        gate_passed=gate_passed,
    )
