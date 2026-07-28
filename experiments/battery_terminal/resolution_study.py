"""Time-resolution sensitivity under the package's current objective."""

from __future__ import annotations

from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from cvxopf.problem import OPFBuild
from cvxopf.results import extract_results
from cvxopf.testcases import case9

from experiments.battery_terminal.devices import (
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
    ScenarioConfig,
    ScenarioData,
)


RESOLUTIONS_HOURS = (1.0, 0.5, 0.25)
RESOLUTION_POLICIES = {
    "none": POLICIES["none"],
    "equality": POLICIES["equality"],
    "quadratic": POLICIES["quadratic"],
}


@dataclass(frozen=True)
class ResolutionRun:
    """One resolution and policy solve."""

    delta_hours: float
    policy_name: str
    scenario: ScenarioData
    build: OPFBuild
    results: dict


@dataclass(frozen=True)
class ResolutionStudy:
    """Resolution results, hourly comparisons, and retained solves."""

    summary: pd.DataFrame
    comparison: pd.DataFrame
    energy_validation: pd.DataFrame
    runs: dict[tuple[float, str], ResolutionRun]


def refine_frame_zero_order_hold(
    frame: pd.DataFrame,
    delta_hours: float,
) -> pd.DataFrame:
    """Repeat hourly-average rows on an exact subhourly fixed-offset grid."""
    reciprocal = 1.0 / float(delta_hours)
    factor = int(round(reciprocal))
    if (
        not np.isfinite(delta_hours)
        or delta_hours <= 0
        or not np.isclose(reciprocal, factor)
    ):
        raise ValueError("delta_hours must evenly subdivide one hour")
    values = np.repeat(frame.to_numpy(), factor, axis=0)
    index = pd.date_range(
        start=frame.index[0],
        periods=len(values),
        freq=pd.Timedelta(hours=delta_hours),
    )
    return pd.DataFrame(values, index=index, columns=frame.columns)


def refine_scenario_zero_order_hold(
    scenario: ScenarioData,
    delta_hours: float,
) -> ScenarioData:
    """Refine every scenario channel with the same zero-order hold."""
    return replace(
        scenario,
        df_P=refine_frame_zero_order_hold(scenario.df_P, delta_hours),
        df_Q=refine_frame_zero_order_hold(scenario.df_Q, delta_hours),
        df_nd=refine_frame_zero_order_hold(scenario.df_nd, delta_hours),
    )


def aggregate_power_to_hourly(
    values: np.ndarray,
    delta_hours: float,
) -> np.ndarray:
    """Average refined power samples over each original hour."""
    factor = int(round(1.0 / delta_hours))
    array = np.asarray(values, dtype=float)
    if len(array) % factor:
        raise ValueError("Power trajectory does not span complete hours")
    return array.reshape(len(array) // factor, factor, *array.shape[1:]).mean(
        axis=1
    )


def sample_soc_at_hourly_boundaries(
    values: np.ndarray,
    delta_hours: float,
) -> np.ndarray:
    """Select post-step SoC at common hourly boundaries."""
    factor = int(round(1.0 / delta_hours))
    array = np.asarray(values, dtype=float)
    if len(array) % factor:
        raise ValueError("SoC trajectory does not span complete hours")
    return array[factor - 1 :: factor]


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


def _summary_row(run: ResolutionRun) -> dict:
    row = {
        "delta_hours": run.delta_hours,
        "policy": run.policy_name,
        "steps": len(run.scenario.df_P),
        "status": run.results["status"],
        "objective": run.results["objective"],
        "operating_objective": np.nan,
        "terminal_cost": np.nan,
        "terminal_soc_mwh": np.nan,
        "terminal_deviation_mwh": np.nan,
        "soc_min_mwh": np.nan,
        "soc_max_mwh": np.nan,
        "battery_min_mw": np.nan,
        "battery_max_mw": np.nan,
        "generation_energy_mwh": np.nan,
        "curtailment_energy_mwh": np.nan,
        "charge_throughput_mwh": np.nan,
        "discharge_throughput_mwh": np.nan,
        "max_branch_utilization": np.nan,
        "max_constraint_violation": np.nan,
    }
    if run.results["status"] not in OPTIMAL_STATUSES:
        return row

    results = run.results
    delta = run.delta_hours
    soc = np.asarray(results["soc"], dtype=float)[:, 0]
    battery = np.asarray(results["b"], dtype=float)[:, 0]
    generation = np.asarray(results["Pg"], dtype=float)
    renewable = np.asarray(results["p_nd"], dtype=float)
    curtailment = run.scenario.df_nd.to_numpy() - renewable
    flows = np.asarray(results["p_flows"], dtype=float)
    ratings = case9()["branch"][:, 5]
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
            "battery_min_mw": np.min(battery),
            "battery_max_mw": np.max(battery),
            "generation_energy_mwh": delta * np.sum(generation),
            "curtailment_energy_mwh": delta * np.sum(curtailment),
            "charge_throughput_mwh": (
                delta * np.sum(np.maximum(-battery, 0.0))
            ),
            "discharge_throughput_mwh": (
                delta * np.sum(np.maximum(battery, 0.0))
            ),
            "max_branch_utilization": np.max(
                np.abs(flows) / ratings
            ),
            "max_constraint_violation": _max_constraint_violation(run.build),
        }
    )
    return row


def run_resolution_study(
    source: pd.DataFrame,
    *,
    scenario_config: ScenarioConfig = ScenarioConfig(),
    resolutions_hours: tuple[float, ...] = RESOLUTIONS_HOURS,
    policies: dict[str, PolicySpec] = RESOLUTION_POLICIES,
) -> ResolutionStudy:
    """Run the nine-case current-objective resolution experiment."""
    if tuple(resolutions_hours) != RESOLUTIONS_HOURS:
        raise ValueError(
            f"Resolution grid must be {RESOLUTIONS_HOURS}"
        )
    if tuple(policies) != tuple(RESOLUTION_POLICIES):
        raise ValueError(
            "Resolution policies must preserve the approved order "
            f"{tuple(RESOLUTION_POLICIES)}"
        )

    prepared = prepare_experiment(source, scenario_config)
    full = prepared.scenarios["high"]
    hourly = replace(
        full,
        df_P=full.df_P.iloc[-24:].copy(),
        df_Q=full.df_Q.iloc[-24:].copy(),
        df_nd=full.df_nd.iloc[-24:].copy(),
    )
    scenarios = {
        delta: refine_scenario_zero_order_hold(hourly, delta)
        for delta in resolutions_hours
    }

    energy_rows = []
    for delta, scenario in scenarios.items():
        for channel_name, frame in (
            ("active_load", scenario.df_P),
            ("reactive_load", scenario.df_Q),
            ("nondispatchable", scenario.df_nd),
        ):
            refined_energy = delta * frame.sum(axis=0)
            hourly_frame = getattr(
                hourly,
                {
                    "active_load": "df_P",
                    "reactive_load": "df_Q",
                    "nondispatchable": "df_nd",
                }[channel_name],
            )
            reference_energy = hourly_frame.sum(axis=0)
            energy_rows.append(
                {
                    "delta_hours": delta,
                    "channel": channel_name,
                    "maximum_channel_energy_error": np.max(
                        np.abs(refined_energy - reference_energy)
                    ),
                }
            )

    runs = {}
    rows = []
    for delta, scenario in scenarios.items():
        for policy_name, policy in policies.items():
            build = build_lossy_dc(
                prepared,
                scenario,
                _storage(policy),
                delta=delta,
            )
            build.solve()
            results = extract_results(build)
            run = ResolutionRun(
                delta_hours=delta,
                policy_name=policy_name,
                scenario=scenario,
                build=build,
                results=results,
            )
            runs[(delta, policy_name)] = run
            rows.append(_summary_row(run))

    comparison_rows = []
    for policy_name in policies:
        reference = runs[(1.0, policy_name)].results
        for delta in resolutions_hours:
            candidate = runs[(delta, policy_name)].results
            reference_soc = np.asarray(reference["soc"])[:, 0]
            candidate_soc = sample_soc_at_hourly_boundaries(
                np.asarray(candidate["soc"])[:, 0],
                delta,
            )
            comparison_rows.append(
                {
                    "policy": policy_name,
                    "delta_hours": delta,
                    "soc_hourly_max_abs_difference_mwh": np.max(
                        np.abs(candidate_soc - reference_soc)
                    ),
                    "battery_hourly_max_abs_difference_mw": np.max(
                        np.abs(
                            aggregate_power_to_hourly(
                                np.asarray(candidate["b"])[:, 0],
                                delta,
                            )
                            - np.asarray(reference["b"])[:, 0]
                        )
                    ),
                    "generation_hourly_max_abs_difference_mw": np.max(
                        np.abs(
                            aggregate_power_to_hourly(
                                np.asarray(candidate["Pg"]).sum(axis=1),
                                delta,
                            )
                            - np.asarray(reference["Pg"]).sum(axis=1)
                        )
                    ),
                    "terminal_soc_difference_mwh": (
                        candidate_soc[-1] - reference_soc[-1]
                    ),
                }
            )

    return ResolutionStudy(
        summary=pd.DataFrame(rows).set_index(["delta_hours", "policy"]),
        comparison=pd.DataFrame(comparison_rows).set_index(
            ["policy", "delta_hours"]
        ),
        energy_validation=pd.DataFrame(energy_rows).set_index(
            ["delta_hours", "channel"]
        ),
        runs=runs,
    )
