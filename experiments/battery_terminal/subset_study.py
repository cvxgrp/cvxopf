"""DC optimal-substructure and DC-to-AC subsection study."""

from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter

import numpy as np
import pandas as pd

from cvxopf.problem import OPFBuild
from cvxopf.results import extract_results

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
    build_ac,
    build_lossy_dc,
    prepare_experiment,
)
from experiments.battery_terminal.scenario import (
    ScenarioConfig,
    ScenarioData,
)


SUBSET_CASES = {
    "crosses_boundary": (32, 50),
    "no_boundary": (60, 78),
}
CROSSING_BOUNDARY_STATE = 41


@dataclass(frozen=True)
class SubsetRun:
    """One independently solved subsection."""

    case_name: str
    formulation: str
    start_state: int
    end_state: int
    scenario: ScenarioData
    build: OPFBuild
    results: dict
    decomposition: SoCDecomposition | None
    wall_time_seconds: float


@dataclass(frozen=True)
class SubsetStudy:
    """Subset solves, formulation comparisons, and additivity diagnostics."""

    summary: pd.DataFrame
    comparison: pd.DataFrame
    additivity: pd.DataFrame
    trajectories: pd.DataFrame
    runs: dict[tuple[str, str], SubsetRun]
    reference_build: OPFBuild
    reference_results: dict


def _slice_scenario(
    scenario: ScenarioData,
    start_state: int,
    end_state: int,
) -> ScenarioData:
    return replace(
        scenario,
        df_P=scenario.df_P.iloc[start_state:end_state].copy(),
        df_Q=scenario.df_Q.iloc[start_state:end_state].copy(),
        df_nd=scenario.df_nd.iloc[start_state:end_state].copy(),
    )


def _endpoint_storage(initial_soc: float, terminal_soc: float):
    return replace(
        make_storage(
            terminal_soc=terminal_soc,
            terminal_constraint="equality",
        ),
        initial_soc=initial_soc,
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


def _assign_dc_restriction(
    reference: OPFBuild,
    subsection: OPFBuild,
    start_state: int,
    end_state: int,
) -> None:
    """Assign the long-solve variable restriction to a short DC build."""
    for name in ("p_flows", "Pg", "b", "soc", "p_nd"):
        source = reference.variables[name][start_state:end_state]
        target = subsection.variables[name]
        for source_variable, target_variable in zip(
            source, target, strict=True
        ):
            target_variable.value = np.array(
                source_variable.value,
                copy=True,
            )


def _solve_run(
    case_name: str,
    formulation: str,
    start_state: int,
    end_state: int,
    scenario: ScenarioData,
    build: OPFBuild,
) -> SubsetRun:
    start = perf_counter()
    build.solve()
    wall_time_seconds = perf_counter() - start
    results = extract_results(build)
    decomposition = None
    if results["status"] in OPTIMAL_STATUSES:
        decomposition = decompose_soc(
            results["soc"][:, 0],
            initial_soc=float(build.data["storage_initial_soc"][0]),
            capacity=STORAGE_CAPACITY_MWH,
        )
    return SubsetRun(
        case_name=case_name,
        formulation=formulation,
        start_state=start_state,
        end_state=end_state,
        scenario=scenario,
        build=build,
        results=results,
        decomposition=decomposition,
        wall_time_seconds=wall_time_seconds,
    )


def _run_summary(run: SubsetRun) -> dict:
    row = {
        "case": run.case_name,
        "formulation": run.formulation,
        "start_state": run.start_state,
        "end_state": run.end_state,
        "status": run.results["status"],
        "wall_time_seconds": run.wall_time_seconds,
        "objective": run.results["objective"],
        "initial_soc_mwh": np.nan,
        "terminal_soc_mwh": np.nan,
        "soc_min_mwh": np.nan,
        "soc_max_mwh": np.nan,
        "battery_p_min_mw": np.nan,
        "battery_p_max_mw": np.nan,
        "generation_mwh": np.nan,
        "curtailment_mwh": np.nan,
        "physical_network_loss_mwh": np.nan,
        "voltage_min_pu": np.nan,
        "voltage_max_pu": np.nan,
        "renewable_q_min_mvar": np.nan,
        "renewable_q_max_mvar": np.nan,
        "max_constraint_violation": np.nan,
        "internal_empty_states": "",
        "internal_full_states": "",
    }
    if run.decomposition is None:
        return row

    results = run.results
    soc = np.asarray(results["soc"], dtype=float)[:, 0]
    battery = np.asarray(results["b"], dtype=float)[:, 0]
    generation = np.asarray(results["Pg"], dtype=float)
    renewable = np.asarray(results["p_nd"], dtype=float)
    curtailment = run.scenario.df_nd.to_numpy() - renewable
    internal_events = [
        event
        for event in run.decomposition.boundary_events
        if event.first_state > 0
        and event.last_state < run.end_state - run.start_state
    ]
    row.update(
        {
            "initial_soc_mwh": run.decomposition.states[0],
            "terminal_soc_mwh": soc[-1],
            "soc_min_mwh": np.min(soc),
            "soc_max_mwh": np.max(soc),
            "battery_p_min_mw": np.min(battery),
            "battery_p_max_mw": np.max(battery),
            "generation_mwh": np.sum(generation),
            "curtailment_mwh": np.sum(curtailment),
            "max_constraint_violation": _max_constraint_violation(run.build),
            "internal_empty_states": ",".join(
                str(event.first_state)
                for event in internal_events
                if event.kind == "empty"
            ),
            "internal_full_states": ",".join(
                str(event.first_state)
                for event in internal_events
                if event.kind == "full"
            ),
        }
    )
    if run.formulation == "ac":
        load = run.scenario.df_P.to_numpy().sum(axis=1)
        physical_loss = (
            generation.sum(axis=1)
            + renewable.sum(axis=1)
            + battery
            - load
        )
        q_nd = np.asarray(results["q_nd"], dtype=float)
        row.update(
            {
                "physical_network_loss_mwh": np.sum(physical_loss),
                "voltage_min_pu": np.min(results["Vm"]),
                "voltage_max_pu": np.max(results["Vm"]),
                "renewable_q_min_mvar": np.min(q_nd),
                "renewable_q_max_mvar": np.max(q_nd),
            }
        )
    return row


def _trajectory_comparison(
    case_name: str,
    reference_results: dict,
    dc_run: SubsetRun,
    ac_run: SubsetRun,
    start_state: int,
    end_state: int,
) -> dict:
    reference_soc = np.asarray(reference_results["soc"])[
        start_state:end_state, 0
    ]
    reference_battery = np.asarray(reference_results["b"])[
        start_state:end_state, 0
    ]
    reference_generation = np.asarray(reference_results["Pg"])[
        start_state:end_state
    ].sum(axis=1)
    reference_renewable = np.asarray(reference_results["p_nd"])[
        start_state:end_state
    ].sum(axis=1)

    dc_results = dc_run.results
    ac_results = ac_run.results
    dc_soc = np.asarray(dc_results["soc"])[:, 0]
    ac_soc = np.asarray(ac_results["soc"])[:, 0]
    dc_battery = np.asarray(dc_results["b"])[:, 0]
    ac_battery = np.asarray(ac_results["b"])[:, 0]
    dc_generation = np.asarray(dc_results["Pg"]).sum(axis=1)
    ac_generation = np.asarray(ac_results["Pg"]).sum(axis=1)
    dc_renewable = np.asarray(dc_results["p_nd"]).sum(axis=1)
    ac_renewable = np.asarray(ac_results["p_nd"]).sum(axis=1)
    return {
        "case": case_name,
        "dc_soc_max_abs_difference_from_long_mwh": np.max(
            np.abs(dc_soc - reference_soc)
        ),
        "dc_battery_max_abs_difference_from_long_mw": np.max(
            np.abs(dc_battery - reference_battery)
        ),
        "dc_generation_max_abs_difference_from_long_mw": np.max(
            np.abs(dc_generation - reference_generation)
        ),
        "dc_renewable_max_abs_difference_from_long_mw": np.max(
            np.abs(dc_renewable - reference_renewable)
        ),
        "ac_dc_soc_rmse_mwh": np.sqrt(np.mean((ac_soc - dc_soc) ** 2)),
        "ac_dc_battery_rmse_mw": np.sqrt(
            np.mean((ac_battery - dc_battery) ** 2)
        ),
        "ac_dc_generation_rmse_mw": np.sqrt(
            np.mean((ac_generation - dc_generation) ** 2)
        ),
        "ac_dc_renewable_rmse_mw": np.sqrt(
            np.mean((ac_renewable - dc_renewable) ** 2)
        ),
    }


def _trajectory_table(
    runs: dict[tuple[str, str], SubsetRun],
) -> pd.DataFrame:
    """Return per-battery DC and AC trajectories in tidy form."""
    frames = []
    for (case_name, formulation), run in runs.items():
        soc = np.asarray(run.results["soc"], dtype=float)
        battery = np.asarray(run.results["b"], dtype=float)
        initial_soc = np.asarray(
            run.build.data["storage_initial_soc"],
            dtype=float,
        )
        storage_buses = np.asarray(
            run.build.data["storage_bus"],
            dtype=int,
        )
        for battery_index in range(soc.shape[1]):
            frames.append(
                pd.DataFrame(
                    {
                        "case": case_name,
                        "formulation": formulation,
                        "battery_index": battery_index,
                        "battery_bus": storage_buses[battery_index] + 1,
                        "local_step": np.arange(len(soc)),
                        "global_step": (
                            run.start_state + np.arange(len(soc))
                        ),
                        "post_step_state": np.arange(1, len(soc) + 1),
                        "global_post_step_state": (
                            run.start_state + np.arange(1, len(soc) + 1)
                        ),
                        "initial_soc_mwh": initial_soc[battery_index],
                        "soc_mwh": soc[:, battery_index],
                        "battery_mw": battery[:, battery_index],
                    }
                )
            )
    return pd.concat(frames, ignore_index=True)


def run_subset_study(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
) -> SubsetStudy:
    """Run crossing and non-crossing subsection reconstructions."""
    prepared = prepare_experiment(source, scenario_config)
    full_scenario = prepared.scenarios["high"]
    reference_build = build_lossy_dc(
        prepared,
        full_scenario,
        make_storage(
            terminal_soc=STORAGE_INITIAL_SOC_MWH,
            terminal_constraint="equality",
        ),
    )
    reference_build.solve()
    reference_results = extract_results(reference_build)
    reference_decomposition = decompose_soc(
        reference_results["soc"][:, 0],
        initial_soc=STORAGE_INITIAL_SOC_MWH,
        capacity=STORAGE_CAPACITY_MWH,
    )
    states = reference_decomposition.states

    runs = {}
    rows = []
    comparisons = []
    restricted_metrics = {}
    for case_name, (start_state, end_state) in SUBSET_CASES.items():
        scenario = _slice_scenario(
            full_scenario,
            start_state,
            end_state,
        )
        storage = _endpoint_storage(
            states[start_state],
            states[end_state],
        )
        dc_build = build_lossy_dc(prepared, scenario, storage)
        _assign_dc_restriction(
            reference_build,
            dc_build,
            start_state,
            end_state,
        )
        restricted_metrics[case_name] = {
            "restricted_objective": float(
                dc_build.prob.objective.expr.value
            ),
            "restricted_max_constraint_violation": (
                _max_constraint_violation(dc_build)
            ),
        }
        dc_run = _solve_run(
            case_name,
            "lossy_dc",
            start_state,
            end_state,
            scenario,
            dc_build,
        )
        ac_run = _solve_run(
            case_name,
            "ac",
            start_state,
            end_state,
            scenario,
            build_ac(prepared, scenario, storage),
        )
        runs[(case_name, "lossy_dc")] = dc_run
        runs[(case_name, "ac")] = ac_run
        rows.extend((_run_summary(dc_run), _run_summary(ac_run)))
        comparisons.append(
            _trajectory_comparison(
                case_name,
                reference_results,
                dc_run,
                ac_run,
                start_state,
                end_state,
            )
        )

    additivity_rows = []
    crossing_start, crossing_end = SUBSET_CASES["crosses_boundary"]
    half_objectives = []
    for half_name, half_start, half_end in (
        ("before_boundary", crossing_start, CROSSING_BOUNDARY_STATE),
        ("after_boundary", CROSSING_BOUNDARY_STATE, crossing_end),
    ):
        scenario = _slice_scenario(full_scenario, half_start, half_end)
        build = build_lossy_dc(
            prepared,
            scenario,
            _endpoint_storage(states[half_start], states[half_end]),
        )
        build.solve()
        results = extract_results(build)
        half_objectives.append(results["objective"])
        additivity_rows.append(
            {
                "component": half_name,
                "start_state": half_start,
                "end_state": half_end,
                "objective": results["objective"],
                "status": results["status"],
            }
        )

    crossing_run = runs[("crosses_boundary", "lossy_dc")]
    additivity_rows.append(
        {
            "component": "whole_crossing_window",
            "start_state": crossing_start,
            "end_state": crossing_end,
            "objective": crossing_run.results["objective"],
            "status": crossing_run.results["status"],
        }
    )
    additivity_rows.append(
        {
            "component": "sum_of_halves",
            "start_state": crossing_start,
            "end_state": crossing_end,
            "objective": sum(half_objectives),
            "status": "derived",
        }
    )

    comparison = pd.DataFrame(comparisons).set_index("case")
    for case_name, metrics in restricted_metrics.items():
        comparison.loc[
            case_name, "restricted_objective"
        ] = metrics["restricted_objective"]
        comparison.loc[
            case_name, "restricted_max_constraint_violation"
        ] = metrics["restricted_max_constraint_violation"]
        comparison.loc[
            case_name, "short_dc_objective"
        ] = runs[(case_name, "lossy_dc")].results["objective"]
        comparison.loc[
            case_name, "dc_objective_gap"
        ] = (
            runs[(case_name, "lossy_dc")].results["objective"]
            - metrics["restricted_objective"]
        )

    return SubsetStudy(
        summary=pd.DataFrame(rows).set_index(["case", "formulation"]),
        comparison=comparison,
        additivity=pd.DataFrame(additivity_rows).set_index("component"),
        trajectories=_trajectory_table(runs),
        runs=runs,
        reference_build=reference_build,
        reference_results=reference_results,
    )
