"""Nested-horizon terminal-policy locality study."""

from __future__ import annotations

from dataclasses import dataclass, replace

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
    build_lossy_dc,
    prepare_experiment,
)
from experiments.battery_terminal.runner import POLICIES, PolicySpec
from experiments.battery_terminal.scenario import (
    REPRESENTATIVE_WINDOWS,
    ScenarioConfig,
    ScenarioData,
)


HORIZONS = (12, 24, 48, 72, 96)
HORIZON_POLICIES = {
    "none": POLICIES["none"],
    "equality": POLICIES["equality"],
    "quadratic": POLICIES["quadratic"],
}


@dataclass(frozen=True)
class HorizonRun:
    """One solved horizon and terminal policy."""

    scenario_name: str
    horizon_steps: int
    policy_name: str
    build: OPFBuild
    results: dict
    decomposition: SoCDecomposition | None


@dataclass(frozen=True)
class HorizonStudy:
    """Per-run results, pairwise locality metrics, and complete solves."""

    summary: pd.DataFrame
    locality: pd.DataFrame
    runs: dict[tuple[str, int, str], HorizonRun]


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


def run_horizon_study(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
    scenario_names: tuple[str, ...] = tuple(REPRESENTATIVE_WINDOWS),
    horizons: tuple[int, ...] = HORIZONS,
    policies: dict[str, PolicySpec] = HORIZON_POLICIES,
) -> HorizonStudy:
    """Compare nested common-endpoint horizons under selected policies."""
    unknown_scenarios = set(scenario_names) - set(REPRESENTATIVE_WINDOWS)
    if unknown_scenarios:
        raise ValueError(f"Unknown scenarios: {sorted(unknown_scenarios)}")
    horizon_values = tuple(sorted(int(value) for value in horizons))
    if (
        not horizon_values
        or len(set(horizon_values)) != len(horizon_values)
        or any(value <= 0 or value > 96 for value in horizon_values)
    ):
        raise ValueError("Horizons must be unique integers from 1 through 96")
    if "none" not in policies:
        raise ValueError("Horizon policies must include a 'none' reference")

    prepared = prepare_experiment(source, scenario_config)
    runs = {}
    rows = []
    for scenario_name in scenario_names:
        full_scenario = prepared.scenarios[scenario_name]
        for horizon_steps in horizon_values:
            scenario = _suffix(full_scenario, horizon_steps)
            for policy_name, policy in policies.items():
                build = build_lossy_dc(prepared, scenario, _storage(policy))
                build.solve()
                results = extract_results(build)
                soc = None
                decomposition = None
                terminal_cost = np.nan
                if results["status"] in OPTIMAL_STATUSES:
                    soc = np.asarray(results["soc"], dtype=float)[:, 0]
                    decomposition = decompose_soc(
                        soc,
                        initial_soc=STORAGE_INITIAL_SOC_MWH,
                        capacity=STORAGE_CAPACITY_MWH,
                    )
                    terminal_cost = float(
                        results.get("storage_terminal_cost", 0.0)
                    )
                run = HorizonRun(
                    scenario_name=scenario_name,
                    horizon_steps=horizon_steps,
                    policy_name=policy_name,
                    build=build,
                    results=results,
                    decomposition=decomposition,
                )
                runs[(scenario_name, horizon_steps, policy_name)] = run
                rows.append(
                    {
                        "scenario": scenario_name,
                        "horizon_steps": horizon_steps,
                        "policy": policy_name,
                        "status": results["status"],
                        "objective": results["objective"],
                        "operating_objective": (
                            results["objective"] - terminal_cost
                        ),
                        "terminal_cost": terminal_cost,
                        "terminal_soc_mwh": (
                            np.nan if soc is None else soc[-1]
                        ),
                        "final_excursion_steps": (
                            np.nan
                            if decomposition is None
                            else decomposition.final_excursion_steps
                        ),
                        "final_excursion_fraction": (
                            np.nan
                            if decomposition is None
                            else (
                                decomposition.final_excursion_steps
                                / horizon_steps
                            )
                        ),
                        "classified_fraction": (
                            np.nan
                            if decomposition is None
                            else decomposition.classified_steps / horizon_steps
                        ),
                    }
                )

    locality_rows = []
    for scenario_name in scenario_names:
        for horizon_steps in horizon_values:
            reference = runs[(scenario_name, horizon_steps, "none")]
            for policy_name in policies:
                if policy_name == "none":
                    continue
                candidate = runs[(scenario_name, horizon_steps, policy_name)]
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
                        "scenario": scenario_name,
                        "horizon_steps": horizon_steps,
                        "policy": policy_name,
                        "reference_status": reference.results["status"],
                        "candidate_status": candidate.results["status"],
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

    return HorizonStudy(
        summary=pd.DataFrame(rows).set_index(
            ["scenario", "horizon_steps", "policy"]
        ),
        locality=pd.DataFrame(locality_rows).set_index(
            ["scenario", "horizon_steps", "policy"]
        ),
        runs=runs,
    )
