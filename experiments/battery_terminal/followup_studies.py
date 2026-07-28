"""Adequacy and active-set follow-up studies."""

from __future__ import annotations

from dataclasses import dataclass, replace

import cvxpy as cp
import numpy as np
import pandas as pd

from cvxopf.results import extract_results

from experiments.battery_terminal.devices import (
    STORAGE_INITIAL_SOC_MWH,
    make_dispatchable_generators,
    make_storage,
)
from experiments.battery_terminal.problem_setup import (
    OPTIMAL_STATUSES,
    build_lossy_dc,
    build_singlenode_dc,
    prepare_experiment,
)
from experiments.battery_terminal.scenario import (
    ScenarioConfig,
    ScenarioData,
)
from experiments.battery_terminal.value_function import (
    TerminalValueSweep,
    run_terminal_value_sweep,
)


ADEQUACY_INITIAL_SOC_MWH = (500.0, 646.0, 646.25, 646.5, 750.0, 1000.0)
ADEQUACY_LOOKBACK_HOURS = tuple(range(24, 49))
ADEQUACY_PREFIX_HOURS = (1, 2, 3)
LOW_BREAKPOINT_TARGETS_MWH = (
    449.0,
    450.0,
    450.25,
    450.5,
    450.55,
    450.75,
    451.0,
    452.0,
)


@dataclass(frozen=True)
class AdequacyDiagnostic:
    """Initial-energy and lookback feasibility tables."""

    initial_soc: pd.DataFrame
    lookback: pd.DataFrame
    prefix_capacity: pd.DataFrame


def _suffix(scenario: ScenarioData, horizon_steps: int) -> ScenarioData:
    return replace(
        scenario,
        df_P=scenario.df_P.iloc[-horizon_steps:].copy(),
        df_Q=scenario.df_Q.iloc[-horizon_steps:].copy(),
        df_nd=scenario.df_nd.iloc[-horizon_steps:].copy(),
    )


def _solve_row(build) -> dict:
    build.solve()
    row = {
        "status": build.prob.status,
        "objective": np.nan,
        "soc_min_mwh": np.nan,
        "soc_max_mwh": np.nan,
        "battery_min_mw": np.nan,
        "battery_max_mw": np.nan,
        "generation_max_mw": np.nan,
    }
    if build.prob.status not in OPTIMAL_STATUSES:
        return row
    results = extract_results(build)
    soc = np.asarray(results["soc"], dtype=float)[:, 0]
    battery = np.asarray(results["b"], dtype=float)[:, 0]
    generation = np.asarray(results["Pg"], dtype=float).sum(axis=1)
    row.update(
        {
            "objective": results["objective"],
            "soc_min_mwh": np.min(soc),
            "soc_max_mwh": np.max(soc),
            "battery_min_mw": np.min(battery),
            "battery_max_mw": np.max(battery),
            "generation_max_mw": np.max(generation),
        }
    )
    return row


def run_moderate_adequacy_diagnostic(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
    initial_soc_values: tuple[float, ...] = ADEQUACY_INITIAL_SOC_MWH,
    lookback_hours: tuple[int, ...] = ADEQUACY_LOOKBACK_HOURS,
    prefix_hours: tuple[int, ...] = ADEQUACY_PREFIX_HOURS,
) -> AdequacyDiagnostic:
    """Diagnose moderate-suffix feasibility without adding balance slacks."""
    initial_values = tuple(float(value) for value in initial_soc_values)
    horizons = tuple(int(value) for value in lookback_hours)
    prefixes = tuple(int(value) for value in prefix_hours)
    if (
        not initial_values
        or not np.isfinite(initial_values).all()
        or any(value < 0 or value > 1000 for value in initial_values)
    ):
        raise ValueError("Initial SoC values must lie in [0, 1000] MWh")
    if not horizons or any(value < 24 or value > 96 for value in horizons):
        raise ValueError("Lookback horizons must lie in [24, 96] hours")
    if not prefixes or any(value < 1 or value > 72 for value in prefixes):
        raise ValueError("Prefix horizons must lie in [1, 72] hours")

    prepared = prepare_experiment(source, scenario_config)
    full = prepared.scenarios["moderate"]
    suffix_24 = _suffix(full, 24)

    initial_rows = []
    builders = {
        "singlenode_dc": build_singlenode_dc,
        "lossy_dc": build_lossy_dc,
    }
    for formulation, builder in builders.items():
        for initial_soc_mwh in initial_values:
            storage = replace(
                make_storage(),
                initial_soc=initial_soc_mwh,
            )
            row = _solve_row(builder(prepared, suffix_24, storage))
            row.update(
                {
                    "formulation": formulation,
                    "initial_soc_mwh": initial_soc_mwh,
                }
            )
            initial_rows.append(row)

    lookback_rows = []
    for horizon_steps in horizons:
        scenario = _suffix(full, horizon_steps)
        build = build_lossy_dc(prepared, scenario, make_storage())
        row = _solve_row(build)
        entry_soc = np.nan
        if build.prob.status in OPTIMAL_STATUSES:
            if horizon_steps == 24:
                entry_soc = STORAGE_INITIAL_SOC_MWH
            else:
                results = extract_results(build)
                entry_soc = results["soc"][horizon_steps - 25, 0]
        row.update(
            {
                "horizon_steps": horizon_steps,
                "added_lookback_hours": horizon_steps - 24,
                "soc_entering_final_24h_mwh": entry_soc,
            }
        )
        lookback_rows.append(row)

    prefix_rows = []
    for prefix_steps in prefixes:
        scenario = replace(
            full,
            df_P=full.df_P.iloc[-24 - prefix_steps : -24].copy(),
            df_Q=full.df_Q.iloc[-24 - prefix_steps : -24].copy(),
            df_nd=full.df_nd.iloc[-24 - prefix_steps : -24].copy(),
        )
        build = build_lossy_dc(prepared, scenario, make_storage())
        terminal_soc = build.variables["soc"][-1][0]
        problem = cp.Problem(cp.Maximize(terminal_soc), build.prob.constraints)
        problem.solve(solver=cp.CLARABEL, verbose=False)
        prefix_rows.append(
            {
                "prefix_steps": prefix_steps,
                "status": problem.status,
                "maximum_entry_soc_mwh": terminal_soc.value,
            }
        )

    return AdequacyDiagnostic(
        initial_soc=pd.DataFrame(initial_rows).set_index(
            ["formulation", "initial_soc_mwh"]
        ),
        lookback=pd.DataFrame(lookback_rows).set_index("horizon_steps"),
        prefix_capacity=pd.DataFrame(prefix_rows).set_index("prefix_steps"),
    )


def run_low_breakpoint_refinement(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
    targets_mwh: tuple[float, ...] = LOW_BREAKPOINT_TARGETS_MWH,
) -> TerminalValueSweep:
    """Refine the low-window value function around upper-SoC saturation."""
    sweep = run_terminal_value_sweep(
        source,
        scenario_config=scenario_config,
        scenario_names=("low",),
        targets_mwh=targets_mwh,
    )
    minimum_generation = 96 * sum(
        generator.p_min_mw for generator in make_dispatchable_generators()
    )
    sweep.summary["generation_above_minimum_mwh"] = (
        sweep.summary["generation_mwh"] - minimum_generation
    )
    sweep.summary["upper_soc_margin_mwh"] = (
        1000.0 - sweep.summary["soc_max_mwh"]
    )
    return sweep
