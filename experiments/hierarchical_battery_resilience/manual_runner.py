"""Auditable pre-public-API reference runner for the M17 experiment.

This module deliberately orchestrates the existing public OPF builders. It is
an executable experiment specification, not a reusable controller API.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Literal, Mapping
import warnings

import numpy as np

from cvxopf import (
    OPFBuild,
    StorageUnitIdeal,
    build_opf_multistep,
    extract_results,
)

from experiments.hierarchical_battery_resilience.scenario import (
    FrozenScenario,
    load_frozen_scenario,
)


OuterPolicy = Literal["frozen", "replan_every_step"]
InnerPolicy = Literal["hard_equality", "quadratic_soft"]
AttemptKind = Literal["controlling", "diagnostic"]


@dataclass(frozen=True)
class EndpointCase:
    """One half-open outer-plan interval used for endpoint realization."""

    name: str
    start: int
    stop: int


FROZEN_ENDPOINT_CASES = (
    EndpointCase("crosses_saturation_boundary_32_50", 32, 50),
    EndpointCase("within_regime_60_78", 60, 78),
)


@dataclass(frozen=True)
class SolveAudit:
    """One solve's raw outcome and independently reconstructed diagnostics."""

    status: str | None
    outcome: str
    accepted_primal: bool
    missing_or_nonfinite_fields: tuple[str, ...]
    identity_error: str | None
    residuals: Mapping[str, float]
    exception: str | None
    wall_time_seconds: float
    solver_num_iters: int | str | None
    solver_setup_time_seconds: float | None
    solver_solve_time_seconds: float | None


@dataclass(frozen=True)
class OuterPlanRecord:
    """One retained lossy-DC plan with local and global boundary metadata."""

    outer_plan_id: str
    created_iteration: int
    global_interval_start: int
    global_interval_stop: int
    local_boundary_indices: np.ndarray
    global_boundary_indices: np.ndarray
    storage_device_ids: tuple[str, ...]
    boundary_soc_mwh: np.ndarray | None
    build: OPFBuild
    results: dict
    audit: SolveAudit


@dataclass(frozen=True)
class ACAttemptRecord:
    """One controlling or diagnostic AC window solve."""

    attempt_id: str
    attempt_kind: AttemptKind
    iteration: int
    interval_start: int
    interval_stop: int
    effective_window_steps: int
    outer_plan_id: str
    outer_local_boundary: int
    outer_global_boundary: int
    terminal_policy: str | None
    storage_device_ids: tuple[str, ...]
    initial_soc_mwh: Mapping[str, float]
    target_soc_mwh: Mapping[str, float] | None
    terminal_deviation_mwh: Mapping[str, float] | None
    window_diagnosis: str
    build: OPFBuild
    results: dict
    audit: SolveAudit


@dataclass(frozen=True)
class EndpointRealizationRecord:
    """One endpoint-conditioned AC realization linked to its outer plan."""

    case: EndpointCase
    outer_plan: OuterPlanRecord
    attempt: ACAttemptRecord
    diagnostic_attempt: ACAttemptRecord | None


@dataclass(frozen=True)
class EndpointStudyRecord:
    """The retained outer plan and every attempted endpoint realization."""

    outer_plan: OuterPlanRecord
    realizations: tuple[EndpointRealizationRecord, ...]
    completed: bool
    termination_reason: str | None


@dataclass(frozen=True)
class ExecutedIntervalRecord:
    """Physical/economic accounting for one accepted first AC interval."""

    iteration: int
    controlling_attempt_id: str
    generation_cost: float
    storage_cycling_cost: float
    renewable_curtailment_mwh: float
    active_loss_mwh: float
    active_loss_crosscheck_mw_abs: float
    state_transition_residual_mwh_abs: float
    voltage_violation_pu: float
    thermal_residual_mva: float
    normalized_squared_thermal_residual: float


@dataclass(frozen=True)
class SequentialRunRecord:
    """Complete or explicitly terminated sequential reference trajectory."""

    outer_policy: OuterPolicy
    inner_policy: InnerPolicy
    outer_plans: Mapping[str, OuterPlanRecord]
    ac_attempts: tuple[ACAttemptRecord, ...]
    executed_intervals: tuple[ExecutedIntervalRecord, ...]
    realized_soc_mwh: np.ndarray
    executed_b_mw: np.ndarray
    trajectory_summary: Mapping[str, float]
    completed_intervals: int
    completion_fraction: float
    completed: bool
    termination_iteration: int | None
    termination_reason: str | None


def _storage_id_order(storage: tuple[StorageUnitIdeal, ...]) -> tuple[str, ...]:
    ids = tuple(unit.device_id for unit in storage)
    if any(
        device_id is None
        or not isinstance(device_id, str)
        or not device_id.strip()
        for device_id in ids
    ):
        raise ValueError(
            "M17 requires explicit nonempty storage device_id values"
        )
    if len(set(ids)) != len(ids):
        raise ValueError("M17 requires unique storage device_id values")
    return tuple(str(device_id) for device_id in ids)


def _aligned_values(
    values: Mapping[str, float], storage_ids: tuple[str, ...], label: str
) -> np.ndarray:
    if set(values) != set(storage_ids):
        raise ValueError(
            f"{label} storage IDs do not match the frozen fleet: "
            f"expected {sorted(storage_ids)}, got {sorted(values)}"
        )
    return np.array([values[device_id] for device_id in storage_ids], dtype=float)


def _outer_storage(
    scenario: FrozenScenario, realized_soc: Mapping[str, float]
) -> tuple[StorageUnitIdeal, ...]:
    ids = _storage_id_order(scenario.storage)
    initial = _aligned_values(realized_soc, ids, "realized SoC")
    return tuple(
        replace(unit, initial_soc=float(initial[index]))
        for index, unit in enumerate(scenario.storage)
    )


def _inner_storage(
    scenario: FrozenScenario,
    realized_soc: Mapping[str, float],
    target_soc: Mapping[str, float] | None,
    policy: InnerPolicy | None,
) -> tuple[StorageUnitIdeal, ...]:
    ids = _storage_id_order(scenario.storage)
    initial = _aligned_values(realized_soc, ids, "realized SoC")
    target = (
        None
        if target_soc is None
        else _aligned_values(target_soc, ids, "terminal target")
    )
    if (policy is None) != (target is None):
        raise ValueError("Diagnostic storage must omit both target and policy")

    units = []
    for index, unit in enumerate(scenario.storage):
        common = dict(
            initial_soc=float(initial[index]),
            terminal_soc=None if target is None else float(target[index]),
            terminal_constraint=None,
            terminal_cost=None,
            terminal_weight=None,
        )
        if policy == "hard_equality":
            common["terminal_constraint"] = "equality"
        elif policy == "quadratic_soft":
            common["terminal_cost"] = "quadratic"
            common["terminal_weight"] = scenario.control.quadratic_soft_weight
        elif policy is not None:
            raise ValueError(f"Unknown inner terminal policy {policy!r}")
        units.append(replace(unit, **common))
    return tuple(units)


def _build_window(
    scenario: FrozenScenario,
    formulation: Literal["ac", "lossy_dc"],
    start: int,
    stop: int,
    storage: tuple[StorageUnitIdeal, ...],
) -> OPFBuild:
    if not 0 <= start < stop <= scenario.control.horizon_steps:
        raise ValueError(f"Invalid half-open interval [{start}, {stop})")
    load_p = scenario.df_load_p.iloc[start:stop]
    load_q = scenario.df_load_q.iloc[start:stop]
    nd = scenario.df_nd.iloc[start:stop]
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=".*reactive load input metadata.*",
            category=UserWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="Storage apparent_power_rating is applied.*",
            category=UserWarning,
        )
        return build_opf_multistep(
            deepcopy(scenario.case),
            T=stop - start,
            formulation=formulation,
            options=replace(scenario.options),
            generators=list(scenario.generators),
            loads=list(scenario.loads),
            df_load_p=load_p,
            df_load_q=load_q,
            nondispatchable=list(scenario.nondispatchable),
            df_nd=nd,
            storage=list(storage),
            hvdc=list(scenario.hvdc),
            delta=scenario.control.delta_hours,
        )


def _solve_and_extract(
    build: OPFBuild, solve_kwargs: Mapping[str, object] | None
) -> tuple[dict, str | None, float]:
    exception = None
    started = perf_counter()
    try:
        build.solve(**dict(solve_kwargs or {}))
    except Exception as exc:  # solver failures are experimental outcomes
        exception = f"{type(exc).__name__}: {exc}"
    elapsed = perf_counter() - started
    return extract_results(build), exception, elapsed


def _as_2d(values: object) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array.reshape(1, -1) if array.ndim == 1 else array


def _finite_fields(results: dict, fields: tuple[str, ...]) -> tuple[str, ...]:
    missing = []
    for field in fields:
        value = results.get(field)
        if value is None:
            missing.append(field)
            continue
        try:
            finite = np.isfinite(np.asarray(value, dtype=float)).all()
        except (TypeError, ValueError):
            finite = False
        if not finite:
            missing.append(field)
    return tuple(missing)


def _identity_error(
    scenario: FrozenScenario, build: OPFBuild, results: dict
) -> str | None:
    expected = _storage_id_order(scenario.storage)
    build_ids = tuple(str(value) for value in build.data["storage_device_ids"])
    result_ids = tuple(str(value) for value in results["storage_device_ids"])
    explicit = np.asarray(
        results["storage_device_id_is_explicit"], dtype=bool
    )
    if not explicit.all():
        return "one or more storage IDs are build-local rather than explicit"
    if set(build_ids) != set(expected) or set(result_ids) != set(expected):
        return "storage fleet differs from the frozen scenario"
    if build_ids != result_ids:
        return "build and result storage identity order differs"
    if build_ids != expected:
        return "storage result order differs from the frozen fleet order"
    return None


def _device_injections(
    scenario: FrozenScenario, results: dict, *, reactive: bool
) -> tuple[np.ndarray, np.ndarray | None]:
    nb = scenario.case["bus"].shape[0]
    bus_ids = [int(value) for value in scenario.case["bus"][:, 0]]
    bus_index = {bus_id: index for index, bus_id in enumerate(bus_ids)}
    p = np.zeros((_as_2d(results["Pg"]).shape[0], nb))
    q = np.zeros_like(p) if reactive else None

    for column, unit in enumerate(scenario.generators):
        p[:, bus_index[unit.bus]] += _as_2d(results["Pg"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(results["Qg"])[:, column]
    for column, unit in enumerate(scenario.storage):
        p[:, bus_index[unit.bus]] += _as_2d(results["b"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(results["b_q"])[:, column]
    for column, unit in enumerate(scenario.nondispatchable):
        p[:, bus_index[unit.bus]] += _as_2d(results["p_nd"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(results["q_nd"])[:, column]
    for column, unit in enumerate(scenario.loads):
        p[:, bus_index[unit.bus]] -= _as_2d(
            results["p_load_served"]
        )[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] -= _as_2d(
                results["q_load_served"]
            )[:, column]
    for column, link in enumerate(scenario.hvdc):
        p[:, bus_index[link.from_bus]] += _as_2d(
            results["p_hvdc_in"]
        )[:, column]
        p[:, bus_index[link.to_bus]] += _as_2d(
            results["p_hvdc_out"]
        )[:, column]
    return p, q


def _soc_residual(
    scenario: FrozenScenario, build: OPFBuild, results: dict
) -> float:
    soc = _as_2d(results["soc"])
    b = _as_2d(results["b"])
    initial = np.asarray(build.data["storage_initial_soc"], dtype=float)
    previous = np.vstack([initial, soc[:-1]])
    residual = soc - previous + scenario.control.delta_hours * b
    return float(np.max(np.abs(residual)))


def _terminal_deviation(
    storage_ids: tuple[str, ...],
    results: dict,
    target_soc: Mapping[str, float] | None,
) -> tuple[dict[str, float] | None, float | None]:
    if target_soc is None:
        return None, None
    terminal = _as_2d(results["soc"])[-1]
    target = _aligned_values(target_soc, storage_ids, "terminal target")
    values = terminal - target
    return (
        {
            device_id: float(values[index])
            for index, device_id in enumerate(storage_ids)
        },
        float(np.max(np.abs(values))),
    )


def _dc_incidence(case: dict) -> np.ndarray:
    bus_ids = [int(value) for value in case["bus"][:, 0]]
    bus_index = {bus_id: index for index, bus_id in enumerate(bus_ids)}
    branch = case["branch"]
    incidence = np.zeros((len(bus_ids), len(branch)))
    for row, values in enumerate(branch):
        incidence[bus_index[int(values[0])], row] = -1.0
        incidence[bus_index[int(values[1])], row] = 1.0
    return incidence


def _ac_residuals(
    scenario: FrozenScenario,
    build: OPFBuild,
    results: dict,
    target_soc: Mapping[str, float] | None,
    policy: InnerPolicy | None,
) -> tuple[dict[str, float], dict[str, float] | None]:
    base_mva = float(scenario.case["baseMVA"])
    p_device, q_device = _device_injections(scenario, results, reactive=True)
    if q_device is None:
        raise RuntimeError("AC diagnostics require reactive device injection")
    p_net = _as_2d(results["p_net"])
    q_net = _as_2d(results["q_net"])
    deviations, terminal_residual = _terminal_deviation(
        _storage_id_order(scenario.storage), results, target_soc
    )
    residuals = {
        "soc_recurrence_mwh_abs": _soc_residual(scenario, build, results),
        "ac_active_balance_pu_abs": float(
            np.max(np.abs((p_device - p_net) / base_mva))
        ),
        "ac_reactive_balance_pu_abs": float(
            np.max(np.abs((q_device - q_net) / base_mva))
        ),
    }

    network_limits = _ac_network_limit_diagnostics(scenario, results)
    residuals["voltage_bound_pu_abs"] = network_limits[0]
    residuals["branch_mva_abs"] = network_limits[1]
    residuals["branch_normalized_squared_residual"] = network_limits[2]

    if policy == "hard_equality" and terminal_residual is not None:
        residuals["terminal_soc_mwh_abs"] = terminal_residual
    elif policy == "quadratic_soft" and deviations is not None:
        reported = float(results["storage_terminal_cost"])
        expected = scenario.control.quadratic_soft_weight * sum(
            value**2 for value in deviations.values()
        )
        residuals["soft_terminal_cost_abs"] = abs(reported - expected)
    return residuals, deviations


def _ac_network_limit_diagnostics(
    scenario: FrozenScenario,
    results: dict,
    *,
    first_interval_only: bool = False,
) -> tuple[float, float, float]:
    """Return voltage and both thermal violations from one AC result."""
    vm = _as_2d(results["Vm"])
    s_from = _as_2d(results["branch_s_from"])
    s_to = _as_2d(results["branch_s_to"])
    if first_interval_only:
        vm = vm[:1]
        s_from = s_from[:1]
        s_to = s_to[:1]

    vmax = np.asarray(scenario.case["bus"][:, 11], dtype=float)
    vmin = np.asarray(scenario.case["bus"][:, 12], dtype=float)
    voltage = float(
        np.max(np.maximum.reduce([vm - vmax, vmin - vm, np.zeros_like(vm)]))
    )

    branch = scenario.case["branch"]
    constrained = (
        (branch[:, 10] == 1)
        & np.isfinite(branch[:, 5])
        & (branch[:, 5] > 0)
    )
    if not np.any(constrained):
        return voltage, 0.0, 0.0
    ratings = branch[constrained, 5]
    apparent = np.concatenate(
        [s_from[:, constrained], s_to[:, constrained]], axis=1
    )
    both_ratings = np.concatenate([ratings, ratings])
    thermal = float(np.max(np.maximum(apparent - both_ratings, 0.0)))
    normalized = float(
        np.max(
            np.maximum(
                (apparent**2 - both_ratings**2) / both_ratings**2,
                0.0,
            )
        )
    )
    return voltage, thermal, normalized


def _dc_residuals(
    scenario: FrozenScenario,
    build: OPFBuild,
    results: dict,
    target_soc: Mapping[str, float],
) -> dict[str, float]:
    p_device, _ = _device_injections(scenario, results, reactive=False)
    p_net = _as_2d(results["p_net"])
    p_flows = _as_2d(results["p_flows"])
    incidence = _dc_incidence(scenario.case)
    _, terminal_residual = _terminal_deviation(
        _storage_id_order(scenario.storage), results, target_soc
    )
    if terminal_residual is None:
        raise RuntimeError("Outer-plan diagnostics require a terminal target")
    return {
        "soc_recurrence_mwh_abs": _soc_residual(scenario, build, results),
        "dc_injection_reporting_mw_abs": float(
            np.max(np.abs(p_device - p_net))
        ),
        "dc_nodal_balance_pu_abs": float(
            np.max(
                np.abs(
                    (p_flows @ incidence.T + p_net)
                    / float(scenario.case["baseMVA"])
                )
            )
        ),
        "terminal_soc_mwh_abs": terminal_residual,
    }


def _residuals_accepted(
    scenario: FrozenScenario, residuals: Mapping[str, float]
) -> bool:
    tolerances = scenario.control.acceptance_tolerances
    return all(
        np.isfinite(value) and value <= tolerances[name]
        for name, value in residuals.items()
    )


def _classify_audit(
    scenario: FrozenScenario,
    build: OPFBuild,
    results: dict,
    exception: str | None,
    elapsed: float,
    required_fields: tuple[str, ...],
    residuals: Mapping[str, float],
    identity_error: str | None,
    *,
    soft: bool,
) -> SolveAudit:
    status = results.get("status")
    missing = _finite_fields(results, required_fields)
    accepted = (
        exception is None
        and status in scenario.control.accepted_statuses
        and not missing
        and identity_error is None
        and _residuals_accepted(scenario, residuals)
    )
    if exception is not None:
        outcome = "solver_failure"
    elif status in {"infeasible", "infeasible_inaccurate"}:
        outcome = "solver_certified_infeasible"
    elif accepted:
        outcome = "accepted_soft" if soft else "accepted"
    else:
        outcome = "unusable_primal"
    solver_stats = build.prob.solver_stats
    return SolveAudit(
        status=status,
        outcome=outcome,
        accepted_primal=accepted,
        missing_or_nonfinite_fields=missing,
        identity_error=identity_error,
        residuals=dict(residuals),
        exception=exception,
        wall_time_seconds=elapsed,
        solver_num_iters=getattr(solver_stats, "num_iters", None),
        solver_setup_time_seconds=getattr(solver_stats, "setup_time", None),
        solver_solve_time_seconds=getattr(solver_stats, "solve_time", None),
    )


OUTER_REQUIRED_FIELDS = (
    "objective", "b", "soc", "Pg", "p_net", "p_flows", "p_nd",
    "curtailment", "p_load", "q_load", "p_load_served",
)
AC_REQUIRED_FIELDS = (
    "objective", "b", "b_q", "soc", "Pg", "Qg", "Vm", "Va_deg",
    "p_net", "q_net", "branch_p_from", "branch_q_from", "branch_p_to",
    "branch_q_to", "branch_s_from", "branch_s_to", "p_nd", "q_nd",
    "curtailment", "p_load", "q_load", "p_load_served", "q_load_served",
)


def _solve_outer_plan(
    scenario: FrozenScenario,
    created_iteration: int,
    realized_soc: Mapping[str, float],
    solve_kwargs: Mapping[str, object] | None,
) -> OuterPlanRecord:
    storage = _outer_storage(scenario, realized_soc)
    build = _build_window(
        scenario,
        "lossy_dc",
        created_iteration,
        scenario.control.horizon_steps,
        storage,
    )
    results, exception, elapsed = _solve_and_extract(build, solve_kwargs)
    target = {
        unit.device_id: float(unit.terminal_soc)
        for unit in storage
        if unit.device_id is not None and unit.terminal_soc is not None
    }
    missing = _finite_fields(results, OUTER_REQUIRED_FIELDS)
    residuals = (
        {}
        if missing
        else _dc_residuals(scenario, build, results, target)
    )
    audit = _classify_audit(
        scenario,
        build,
        results,
        exception,
        elapsed,
        OUTER_REQUIRED_FIELDS,
        residuals,
        _identity_error(scenario, build, results),
        soft=False,
    )
    ids = _storage_id_order(scenario.storage)
    boundary_soc = None
    if audit.accepted_primal:
        boundary_soc = np.vstack(
            [
                np.asarray(build.data["storage_initial_soc"], dtype=float),
                _as_2d(results["soc"]),
            ]
        )
    local = np.arange(scenario.control.horizon_steps - created_iteration + 1)
    return OuterPlanRecord(
        outer_plan_id=f"outer-{created_iteration:03d}",
        created_iteration=created_iteration,
        global_interval_start=created_iteration,
        global_interval_stop=scenario.control.horizon_steps,
        local_boundary_indices=local,
        global_boundary_indices=created_iteration + local,
        storage_device_ids=ids,
        boundary_soc_mwh=boundary_soc,
        build=build,
        results=results,
        audit=audit,
    )


def _solve_ac_attempt(
    scenario: FrozenScenario,
    *,
    attempt_id: str,
    attempt_kind: AttemptKind,
    iteration: int,
    stop: int,
    outer_plan: OuterPlanRecord,
    outer_local_boundary: int,
    realized_soc: Mapping[str, float],
    target_soc: Mapping[str, float] | None,
    policy: InnerPolicy | None,
    solve_kwargs: Mapping[str, object] | None,
) -> ACAttemptRecord:
    storage = _inner_storage(scenario, realized_soc, target_soc, policy)
    build = _build_window(scenario, "ac", iteration, stop, storage)
    results, exception, elapsed = _solve_and_extract(build, solve_kwargs)
    required_fields = AC_REQUIRED_FIELDS + (
        ("storage_terminal_cost",) if policy == "quadratic_soft" else ()
    )
    missing = _finite_fields(results, required_fields)
    if missing:
        residuals: dict[str, float] = {}
        deviations = None
    else:
        residuals, deviations = _ac_residuals(
            scenario, build, results, target_soc, policy
        )
    audit = _classify_audit(
        scenario,
        build,
        results,
        exception,
        elapsed,
        required_fields,
        residuals,
        _identity_error(scenario, build, results),
        soft=policy == "quadratic_soft",
    )
    if audit.accepted_primal and policy == "hard_equality":
        diagnosis = "hard_target_met"
    elif audit.accepted_primal and policy == "quadratic_soft":
        maximum = max(abs(value) for value in (deviations or {}).values())
        if maximum <= scenario.control.acceptance_tolerances[
            "terminal_soc_mwh_abs"
        ]:
            diagnosis = "soft_target_met"
        else:
            diagnosis = "soft_target_deviated"
    elif attempt_kind == "diagnostic" and audit.accepted_primal:
        diagnosis = "target_conditioned_failure"
    else:
        diagnosis = "unresolved_failure"
    return ACAttemptRecord(
        attempt_id=attempt_id,
        attempt_kind=attempt_kind,
        iteration=iteration,
        interval_start=iteration,
        interval_stop=stop,
        effective_window_steps=stop - iteration,
        outer_plan_id=outer_plan.outer_plan_id,
        outer_local_boundary=outer_local_boundary,
        outer_global_boundary=stop,
        terminal_policy=policy,
        storage_device_ids=_storage_id_order(scenario.storage),
        initial_soc_mwh=dict(realized_soc),
        target_soc_mwh=None if target_soc is None else dict(target_soc),
        terminal_deviation_mwh=deviations,
        window_diagnosis=diagnosis,
        build=build,
        results=results,
        audit=audit,
    )


def _diagnose_failed_window(
    scenario: FrozenScenario,
    controlling: ACAttemptRecord,
    outer_plan: OuterPlanRecord,
    solve_kwargs: Mapping[str, object] | None,
) -> ACAttemptRecord:
    diagnostic = _solve_ac_attempt(
        scenario,
        attempt_id=f"{controlling.attempt_id}-diagnostic",
        attempt_kind="diagnostic",
        iteration=controlling.iteration,
        stop=controlling.interval_stop,
        outer_plan=outer_plan,
        outer_local_boundary=controlling.outer_local_boundary,
        realized_soc=controlling.initial_soc_mwh,
        target_soc=None,
        policy=None,
        solve_kwargs=solve_kwargs,
    )
    if diagnostic.audit.accepted_primal:
        diagnosis = "target_conditioned_failure"
    elif diagnostic.audit.outcome == "solver_certified_infeasible":
        diagnosis = "target_independent_infeasibility"
    else:
        diagnosis = "unresolved_failure"
    return replace(diagnostic, window_diagnosis=diagnosis)


def _outer_target(
    outer_plan: OuterPlanRecord,
    local_boundary: int,
) -> dict[str, float]:
    if outer_plan.boundary_soc_mwh is None:
        raise ValueError("Cannot select a signpost from an unaccepted outer plan")
    if not 0 <= local_boundary < len(outer_plan.boundary_soc_mwh):
        raise IndexError(f"Outer local boundary {local_boundary} is unavailable")
    values = outer_plan.boundary_soc_mwh[local_boundary]
    return dict(zip(outer_plan.storage_device_ids, values, strict=True))


def _initial_soc(scenario: FrozenScenario) -> dict[str, float]:
    return {
        str(unit.device_id): float(unit.initial_soc)
        for unit in scenario.storage
    }


def _executed_interval_record(
    scenario: FrozenScenario,
    attempt: ACAttemptRecord,
) -> ExecutedIntervalRecord:
    """Account for the accepted first interval, never the overlapping tail."""
    delta = scenario.control.delta_hours
    pg = _as_2d(attempt.results["Pg"])[0]
    generation_rate = 0.0
    for index, unit in enumerate(scenario.generators):
        if unit.cost_type != "polynomial" or unit.cost_coeffs is None:
            raise RuntimeError(
                "The frozen M17 realized-cost audit expects polynomial "
                "generator costs"
            )
        generation_rate += sum(
            float(coefficient) * float(pg[index]) ** power
            for power, coefficient in enumerate(unit.cost_coeffs)
        )

    b = _as_2d(attempt.results["b"])[0]
    storage_rate = sum(
        unit.aging_weight * abs(float(b[index]))
        for index, unit in enumerate(scenario.storage)
    )
    curtailment = float(np.sum(_as_2d(attempt.results["curtailment"])[0]))
    branch_loss = float(
        np.sum(_as_2d(attempt.results["branch_p_from"])[0])
        + np.sum(_as_2d(attempt.results["branch_p_to"])[0])
    )
    injection_loss = float(np.sum(_as_2d(attempt.results["p_net"])[0]))
    storage_ids = _storage_id_order(scenario.storage)
    initial = _aligned_values(
        attempt.initial_soc_mwh, storage_ids, "attempt initial SoC"
    )
    first_soc = _as_2d(attempt.results["soc"])[0]
    state_residual = float(
        np.max(np.abs(first_soc - initial + delta * b))
    )
    voltage, thermal, normalized_thermal = _ac_network_limit_diagnostics(
        scenario, attempt.results, first_interval_only=True
    )
    return ExecutedIntervalRecord(
        iteration=attempt.iteration,
        controlling_attempt_id=attempt.attempt_id,
        generation_cost=delta * generation_rate,
        storage_cycling_cost=delta * storage_rate,
        renewable_curtailment_mwh=delta * curtailment,
        active_loss_mwh=delta * branch_loss,
        active_loss_crosscheck_mw_abs=abs(branch_loss - injection_loss),
        state_transition_residual_mwh_abs=state_residual,
        voltage_violation_pu=voltage,
        thermal_residual_mva=thermal,
        normalized_squared_thermal_residual=normalized_thermal,
    )


def _trajectory_summary(
    intervals: list[ExecutedIntervalRecord],
    outer_plans: Mapping[str, OuterPlanRecord],
    attempts: list[ACAttemptRecord],
) -> dict[str, float]:
    names = (
        "generation_cost",
        "storage_cycling_cost",
        "renewable_curtailment_mwh",
        "active_loss_mwh",
    )
    totals = {
        name: float(sum(getattr(interval, name) for interval in intervals))
        for name in names
    }
    totals.update(
        {
            "maximum_voltage_violation_pu": max(
                (interval.voltage_violation_pu for interval in intervals),
                default=0.0,
            ),
            "maximum_thermal_residual_mva": max(
                (interval.thermal_residual_mva for interval in intervals),
                default=0.0,
            ),
            "maximum_normalized_squared_thermal_residual": max(
                (
                    interval.normalized_squared_thermal_residual
                    for interval in intervals
                ),
                default=0.0,
            ),
            "cumulative_absolute_signpost_deviation_mwh": float(
                sum(
                    sum(
                        abs(value)
                        for value in attempt.terminal_deviation_mwh.values()
                    )
                    for attempt in attempts
                    if attempt.attempt_kind == "controlling"
                    and attempt.audit.accepted_primal
                    and attempt.terminal_deviation_mwh is not None
                )
            ),
            "runtime_seconds": float(
                sum(
                    plan.audit.wall_time_seconds
                    for plan in outer_plans.values()
                )
                + sum(
                    attempt.audit.wall_time_seconds for attempt in attempts
                )
            ),
        }
    )
    return totals


def run_endpoint_realization(
    cases: tuple[EndpointCase, ...] = FROZEN_ENDPOINT_CASES,
    *,
    outer_solve_kwargs: Mapping[str, object] | None = None,
    ac_solve_kwargs: Mapping[str, object] | None = None,
) -> EndpointStudyRecord:
    """Run endpoint-conditioned AC cases against one frozen full DC plan."""
    if not cases:
        raise ValueError("Endpoint realization requires at least one case")
    names = tuple(case.name for case in cases)
    if any(not name.strip() for name in names) or len(set(names)) != len(names):
        raise ValueError("Endpoint case names must be nonempty and unique")
    scenario = load_frozen_scenario()
    outer = _solve_outer_plan(
        scenario, 0, _initial_soc(scenario), outer_solve_kwargs
    )
    if not outer.audit.accepted_primal:
        return EndpointStudyRecord(
            outer_plan=outer,
            realizations=(),
            completed=False,
            termination_reason=f"outer_{outer.audit.outcome}",
        )
    records = []
    for case in cases:
        if not case.name or not 0 <= case.start < case.stop <= scenario.control.horizon_steps:
            raise ValueError(f"Invalid endpoint case {case}")
        initial = _outer_target(outer, case.start)
        target = _outer_target(outer, case.stop)
        attempt = _solve_ac_attempt(
            scenario,
            attempt_id=f"endpoint-{case.name}",
            attempt_kind="controlling",
            iteration=case.start,
            stop=case.stop,
            outer_plan=outer,
            outer_local_boundary=case.stop,
            realized_soc=initial,
            target_soc=target,
            policy="hard_equality",
            solve_kwargs=ac_solve_kwargs,
        )
        diagnostic = None
        if not attempt.audit.accepted_primal:
            diagnostic = _diagnose_failed_window(
                scenario, attempt, outer, ac_solve_kwargs
            )
            attempt = replace(
                attempt,
                window_diagnosis=diagnostic.window_diagnosis,
            )
        records.append(
            EndpointRealizationRecord(
                case=case,
                outer_plan=outer,
                attempt=attempt,
                diagnostic_attempt=diagnostic,
            )
        )
    completed = all(
        record.attempt.audit.accepted_primal for record in records
    )
    return EndpointStudyRecord(
        outer_plan=outer,
        realizations=tuple(records),
        completed=completed,
        termination_reason=(
            None if completed else "one_or_more_endpoint_attempts_failed"
        ),
    )


def run_sequential_execution(
    outer_policy: OuterPolicy,
    inner_policy: InnerPolicy,
    *,
    outer_solve_kwargs: Mapping[str, object] | None = None,
    ac_solve_kwargs: Mapping[str, object] | None = None,
) -> SequentialRunRecord:
    """Run frozen-plan or stepwise-replanned manual first-action execution."""
    scenario = load_frozen_scenario()
    if outer_policy not in scenario.control.outer_policies:
        raise ValueError(f"Outer policy is not frozen: {outer_policy!r}")
    if inner_policy not in scenario.control.inner_terminal_policies:
        raise ValueError(f"Inner policy is not frozen: {inner_policy!r}")

    storage_ids = _storage_id_order(scenario.storage)
    realized = _initial_soc(scenario)
    realized_history = [_aligned_values(realized, storage_ids, "initial SoC")]
    executed: list[np.ndarray] = []
    executed_intervals: list[ExecutedIntervalRecord] = []
    outer_plans: dict[str, OuterPlanRecord] = {}
    attempts: list[ACAttemptRecord] = []
    frozen_outer = None
    termination_iteration = None
    termination_reason = None

    for iteration in range(scenario.control.horizon_steps):
        if outer_policy == "frozen":
            if frozen_outer is None:
                frozen_outer = _solve_outer_plan(
                    scenario, 0, realized, outer_solve_kwargs
                )
                outer_plans[frozen_outer.outer_plan_id] = frozen_outer
            outer = frozen_outer
            local_start = iteration
        else:
            outer = _solve_outer_plan(
                scenario, iteration, realized, outer_solve_kwargs
            )
            outer_plans[outer.outer_plan_id] = outer
            local_start = 0

        if not outer.audit.accepted_primal:
            termination_iteration = iteration
            termination_reason = f"outer_{outer.audit.outcome}"
            break

        window = min(
            scenario.control.nominal_ac_window_steps,
            scenario.control.horizon_steps - iteration,
        )
        stop = iteration + window
        local_boundary = local_start + window
        target = _outer_target(outer, local_boundary)
        controlling = _solve_ac_attempt(
            scenario,
            attempt_id=f"ac-{iteration:03d}-{inner_policy}",
            attempt_kind="controlling",
            iteration=iteration,
            stop=stop,
            outer_plan=outer,
            outer_local_boundary=local_boundary,
            realized_soc=realized,
            target_soc=target,
            policy=inner_policy,
            solve_kwargs=ac_solve_kwargs,
        )
        attempts.append(controlling)
        if not controlling.audit.accepted_primal:
            diagnostic = _diagnose_failed_window(
                scenario, controlling, outer, ac_solve_kwargs
            )
            attempts.append(diagnostic)
            attempts[-2] = replace(
                controlling,
                window_diagnosis=diagnostic.window_diagnosis,
            )
            termination_iteration = iteration
            termination_reason = controlling.audit.outcome
            break

        first_b = _as_2d(controlling.results["b"])[0]
        first_soc = _as_2d(controlling.results["soc"])[0]
        reconstructed = (
            _aligned_values(realized, storage_ids, "realized SoC")
            - scenario.control.delta_hours * first_b
        )
        tolerance = scenario.control.acceptance_tolerances[
            "soc_recurrence_mwh_abs"
        ]
        if np.max(np.abs(reconstructed - first_soc)) > tolerance:
            raise RuntimeError(
                "Accepted AC first action disagrees with first post-step SoC"
            )
        executed.append(first_b.copy())
        executed_intervals.append(
            _executed_interval_record(scenario, controlling)
        )
        realized_history.append(reconstructed.copy())
        realized = dict(zip(storage_ids, reconstructed, strict=True))

    completed = len(executed) == scenario.control.horizon_steps
    return SequentialRunRecord(
        outer_policy=outer_policy,
        inner_policy=inner_policy,
        outer_plans=outer_plans,
        ac_attempts=tuple(attempts),
        executed_intervals=tuple(executed_intervals),
        realized_soc_mwh=np.asarray(realized_history),
        executed_b_mw=(
            np.asarray(executed)
            if executed
            else np.empty((0, len(storage_ids)))
        ),
        trajectory_summary=_trajectory_summary(
            executed_intervals, outer_plans, attempts
        ),
        completed_intervals=len(executed),
        completion_fraction=len(executed) / scenario.control.horizon_steps,
        completed=completed,
        termination_iteration=None if completed else termination_iteration,
        termination_reason=None if completed else termination_reason,
    )


def run_all_studies(
    endpoint_cases: tuple[EndpointCase, ...] = FROZEN_ENDPOINT_CASES,
    *,
    outer_solve_kwargs: Mapping[str, object] | None = None,
    ac_solve_kwargs: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Run the three predeclared studies without defining a public controller."""
    endpoint = run_endpoint_realization(
        endpoint_cases,
        outer_solve_kwargs=outer_solve_kwargs,
        ac_solve_kwargs=ac_solve_kwargs,
    )
    sequential = {
        (outer_policy, inner_policy): run_sequential_execution(
            outer_policy,
            inner_policy,
            outer_solve_kwargs=outer_solve_kwargs,
            ac_solve_kwargs=ac_solve_kwargs,
        )
        for outer_policy in ("frozen", "replan_every_step")
        for inner_policy in ("hard_equality", "quadratic_soft")
    }
    return {"endpoint_realization": endpoint, "sequential": sequential}
