"""Experiment-specific causal initialization runner for M17-S3b.

This module implements the frozen S3b protocol. It is an auditable reference
experiment, not the public hierarchical-controller API planned for M17-S4.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Mapping
import argparse
import json
import re

import numpy as np

from cvxopf import OPFBuild, extract_results
from experiments.hierarchical_battery_resilience.hard_replan_diagnostic import (
    _assign_complete_start,
    _atomic_write_gzip_json,
    _atomic_write_text,
    _git_output,
    _jsonable,
    _run_build_with_verified_x0,
    _sha256,
    _source_fingerprint,
    _variables_by_name,
    execution_context,
)
from experiments.hierarchical_battery_resilience.manual_runner import (
    AC_REQUIRED_FIELDS,
    ExecutedIntervalRecord,
    OuterPlanRecord,
    SolveAudit,
    _ac_residuals,
    _aligned_values,
    _as_2d,
    _build_window,
    _classify_audit,
    _executed_interval_record,
    _finite_fields,
    _identity_error,
    _initial_soc,
    _inner_storage,
    _outer_target,
    _solve_outer_plan,
    _storage_id_order,
    _trajectory_summary,
)
from experiments.hierarchical_battery_resilience.scenario import (
    FrozenScenario,
    load_frozen_scenario,
)
from experiments.hierarchical_battery_resilience.reproduce import (
    _executed_payload,
    _outer_payload,
)


EXPERIMENT_ROOT = Path(__file__).resolve().parent
REPOSITORY_ROOT = EXPERIMENT_ROOT.parents[1]
DEFAULT_OUTPUT = EXPERIMENT_ROOT / "results/s3b_causal_recovery"
STEP_NAME = re.compile(r"^(?P<base>.+)_(?P<step>\d+)$")
PERTURBATION_SCALES = (1e-4, 1e-3, 1e-2)
SOURCE_CODES = {"target_free": 1, "causal": 2}

SlotState = Literal[
    "pending",
    "executed",
    "not_needed_after_acceptance",
    "source_unavailable",
    "construction_error",
]
AttemptRole = Literal[
    "primary_controlling",
    "target_free",
    "copied_target_free",
    "perturbed_target_free",
    "perturbed_causal",
]


@dataclass(frozen=True)
class AttemptSlot:
    """One of nine pre-registered attempt positions for a window."""

    ordinal: int
    role: AttemptRole
    source_kind: str
    transformation: str
    scale: float | None
    seed: int | None


@dataclass
class CausalAttemptRecord:
    """One retained S3b attempt slot, executed or explicitly skipped."""

    attempt_id: str
    iteration: int
    local_interval_start: int
    local_interval_stop: int
    interval_start: int
    interval_stop: int
    outer_plan_id: str
    initial_soc_mwh: Mapping[str, float]
    target_soc_mwh: Mapping[str, float]
    slot: AttemptSlot
    slot_state: SlotState
    source_attempt_id: str | None
    raw_starting_values: Mapping[str, object] | None
    starting_values: Mapping[str, object] | None
    solver_executed: bool
    x0_verified: bool
    solver_x0: list[float] | None
    solver_x0_layout: tuple[Mapping[str, object], ...] | None
    solver_x0_layout_signature: str | None
    model_x0_count: int | None
    auxiliary_x0_count: int | None
    object_ids_before: Mapping[str, tuple[int, ...]] | None
    object_ids_after: Mapping[str, tuple[int, ...]] | None
    object_identity_preserved: bool | None
    results: dict | None
    audit: SolveAudit | None
    terminal_deviation_mwh: Mapping[str, float] | None
    supplied_executed_action: bool
    reason: str | None
    solution_values: dict[str, np.ndarray] | None = None


@dataclass(frozen=True)
class CausalRecoveryRun:
    """Complete or explicitly terminated S3b trajectory."""

    outer_plans: Mapping[str, OuterPlanRecord]
    attempts: tuple[CausalAttemptRecord, ...]
    executed_intervals: tuple[ExecutedIntervalRecord, ...]
    realized_soc_mwh: np.ndarray
    executed_b_mw: np.ndarray
    trajectory_summary: Mapping[str, object]
    completed_intervals: int
    completion_fraction: float
    completed: bool
    termination_iteration: int | None
    termination_reason: str | None


def perturbation_seed(iteration: int, source_kind: str, scale_index: int) -> int:
    """Return the frozen arithmetic seed for one perturbation slot."""
    if iteration < 0:
        raise ValueError("iteration must be nonnegative")
    if source_kind not in SOURCE_CODES:
        raise ValueError(f"Unknown perturbation source {source_kind!r}")
    if scale_index not in (1, 2, 3):
        raise ValueError("scale_index must be 1, 2, or 3")
    return (
        17_000_000
        + 100 * iteration
        + 10 * SOURCE_CODES[source_kind]
        + scale_index
    )


def attempt_registry(iteration: int) -> tuple[AttemptSlot, ...]:
    """Pre-register the frozen nine-slot sequence before a window executes."""
    slots = [
        AttemptSlot(
            0,
            "primary_controlling",
            "causal",
            "flat" if iteration == 0 else "shifted_preceding",
            None,
            None,
        ),
        AttemptSlot(
            1,
            "target_free",
            "causal",
            "flat" if iteration == 0 else "shifted_preceding",
            None,
            None,
        ),
        AttemptSlot(
            2,
            "copied_target_free",
            "target_free",
            "copy_target_free",
            None,
            None,
        ),
    ]
    ordinal = 3
    for source_kind, role in (
        ("target_free", "perturbed_target_free"),
        ("causal", "perturbed_causal"),
    ):
        for scale_index, scale in enumerate(PERTURBATION_SCALES, start=1):
            slots.append(
                AttemptSlot(
                    ordinal,
                    role,
                    source_kind,
                    f"perturb_{source_kind}",
                    scale,
                    perturbation_seed(iteration, source_kind, scale_index),
                )
            )
            ordinal += 1
    return tuple(slots)


def _values_by_step(
    values: Mapping[str, np.ndarray],
) -> tuple[dict[str, dict[int, np.ndarray]], dict[str, np.ndarray]]:
    stepped: dict[str, dict[int, np.ndarray]] = {}
    unsuffixed: dict[str, np.ndarray] = {}
    for name, value in values.items():
        match = STEP_NAME.fullmatch(name)
        if match is None:
            unsuffixed[name] = np.asarray(value, dtype=float).copy()
            continue
        stepped.setdefault(match.group("base"), {})[
            int(match.group("step"))
        ] = np.asarray(value, dtype=float).copy()
    return stepped, unsuffixed


def shifted_causal_start(
    preceding_values: Mapping[str, np.ndarray],
    destination: OPFBuild,
    *,
    realized_soc_mwh: Mapping[str, float],
    storage_device_ids: tuple[str, ...],
    delta_hours: float,
    soc_tolerance: float,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Shift one accepted prediction and reconcile it with realized storage."""
    destination_variables = _variables_by_name(destination)
    source_stepped, source_unsuffixed = _values_by_step(preceding_values)
    raw: dict[str, np.ndarray] = {}
    projected: dict[str, np.ndarray] = {}
    soc_steps: list[int] = []

    for name, variable in destination_variables.items():
        match = STEP_NAME.fullmatch(name)
        if match is None:
            if name not in source_unsuffixed:
                raise ValueError(f"Shift source lacks unsuffixed variable {name}")
            candidate = source_unsuffixed[name]
        else:
            base = match.group("base")
            step = int(match.group("step"))
            if base == "soc":
                soc_steps.append(step)
                continue
            if base not in source_stepped:
                raise ValueError(f"Shift source lacks step variable family {base}")
            source_steps = source_stepped[base]
            shifted_step = step + 1
            if shifted_step in source_steps:
                candidate = source_steps[shifted_step]
            elif base in {"b", "b_q"}:
                candidate = np.zeros(variable.shape)
            else:
                candidate = source_steps[max(source_steps)]
        candidate = np.asarray(candidate, dtype=float)
        if candidate.shape != variable.shape:
            raise ValueError(
                f"Shifted shape mismatch for {name}: "
                f"{candidate.shape} != {variable.shape}"
            )
        raw[name] = candidate.copy()
        projected[name] = np.asarray(variable.project(candidate), dtype=float)

    if sorted(soc_steps) != list(range(len(soc_steps))):
        raise ValueError("Destination SoC variables must use consecutive steps")
    state = _aligned_values(
        realized_soc_mwh, storage_device_ids, "realized SoC"
    )
    for step in soc_steps:
        b_name = f"b_{step}"
        if b_name not in projected:
            raise ValueError(f"Shifted start lacks {b_name}")
        state = state - delta_hours * projected[b_name]
        name = f"soc_{step}"
        variable = destination_variables[name]
        candidate = np.asarray(state, dtype=float)
        leaf_projection = np.asarray(variable.project(candidate), dtype=float)
        if np.max(np.abs(leaf_projection - candidate)) > soc_tolerance:
            raise ValueError(f"Reconstructed {name} violates destination bounds")
        raw[name] = candidate.copy()
        projected[name] = candidate.copy()

    if set(projected) != set(destination_variables):
        missing = sorted(set(destination_variables) - set(projected))
        raise ValueError(f"Shift did not initialize all variables: {missing}")
    return raw, projected


def perturb_start(
    center: Mapping[str, np.ndarray],
    destination: OPFBuild,
    *,
    scale: float,
    seed: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Apply the frozen deterministic perturbation to one causal center."""
    variables = _variables_by_name(destination)
    if set(center) != set(variables):
        raise ValueError("Perturbation center and destination namespaces differ")
    rng = np.random.default_rng(seed)
    raw: dict[str, np.ndarray] = {}
    projected: dict[str, np.ndarray] = {}
    for name in sorted(variables):
        variable = variables[name]
        value = np.asarray(center[name], dtype=float)
        if value.shape != variable.shape:
            raise ValueError(f"Perturbation shape mismatch for {name}")
        flat = value.flatten(order="F")
        change = scale * np.maximum(1.0, np.abs(flat)) * rng.standard_normal(
            flat.size
        )
        candidate = (flat + change).reshape(value.shape, order="F")
        raw[name] = candidate.copy()
        projected[name] = np.asarray(variable.project(candidate), dtype=float)
    return raw, projected


def _build_ac(
    scenario: FrozenScenario,
    iteration: int,
    stop: int,
    realized_soc: Mapping[str, float],
    target_soc: Mapping[str, float] | None,
) -> OPFBuild:
    policy = None if target_soc is None else "hard_equality"
    storage = _inner_storage(scenario, realized_soc, target_soc, policy)
    return _build_window(scenario, "ac", iteration, stop, storage)


def _solution_values(build: OPFBuild) -> dict[str, np.ndarray]:
    return {
        name: np.asarray(variable.value, dtype=float).copy()
        for name, variable in _variables_by_name(build).items()
    }


def _attempt_from_build(
    scenario: FrozenScenario,
    *,
    iteration: int,
    stop: int,
    outer_plan: OuterPlanRecord,
    target_soc: Mapping[str, float],
    slot: AttemptSlot,
    source_attempt_id: str | None,
    build: OPFBuild,
    raw_start: Mapping[str, np.ndarray] | None,
    assigned_start: Mapping[str, np.ndarray] | None,
    target_free: bool,
    intercept_before_ipopt: bool = False,
) -> CausalAttemptRecord:
    if assigned_start is not None:
        _assign_complete_start(build, assigned_start)
    try:
        x0_run = _run_build_with_verified_x0(
            build, intercept_before_ipopt=intercept_before_ipopt
        )
    except Exception as exc:
        record = _empty_attempt(
            iteration,
            stop,
            outer_plan,
            dict(
                zip(
                    _storage_id_order(scenario.storage),
                    np.asarray(
                        build.data["storage_initial_soc"], dtype=float
                    ),
                    strict=True,
                )
            ),
            target_soc,
            slot,
            "construction_error",
            f"x0_construction_error:{type(exc).__name__}: {exc}",
            source_attempt_id,
        )
        if raw_start is not None:
            record.raw_starting_values = {
                name: np.asarray(value).tolist()
                for name, value in raw_start.items()
            }
        return record
    results = extract_results(build)
    missing = _finite_fields(results, AC_REQUIRED_FIELDS)
    audit_target = None if target_free else target_soc
    if missing:
        residuals: dict[str, float] = {}
        deviations = None
    else:
        residuals, deviations = _ac_residuals(
            scenario,
            build,
            results,
            audit_target,
            None if target_free else "hard_equality",
        )
    audit = _classify_audit(
        scenario,
        build,
        results,
        x0_run.exception,
        x0_run.elapsed_seconds,
        AC_REQUIRED_FIELDS,
        residuals,
        _identity_error(scenario, build, results),
        soft=False,
    )
    return CausalAttemptRecord(
        attempt_id=f"s3b-{iteration:03d}-{slot.ordinal:02d}-{slot.role}",
        iteration=iteration,
        local_interval_start=0,
        local_interval_stop=stop - iteration,
        interval_start=iteration,
        interval_stop=stop,
        outer_plan_id=outer_plan.outer_plan_id,
        initial_soc_mwh=dict(
            zip(
                _storage_id_order(scenario.storage),
                np.asarray(build.data["storage_initial_soc"], dtype=float),
                strict=True,
            )
        ),
        target_soc_mwh=dict(target_soc),
        slot=slot,
        slot_state="executed",
        source_attempt_id=source_attempt_id,
        raw_starting_values=(
            None
            if raw_start is None
            else {name: value.tolist() for name, value in raw_start.items()}
        ),
        starting_values={
            name: value.tolist()
            for name, value in x0_run.starting_values.items()
        },
        solver_executed=x0_run.solver_executed,
        x0_verified=x0_run.x0_verified,
        solver_x0=(
            None if x0_run.solver_x0 is None else x0_run.solver_x0.tolist()
        ),
        solver_x0_layout=x0_run.solver_x0_layout,
        solver_x0_layout_signature=x0_run.solver_x0_layout_signature,
        model_x0_count=x0_run.model_x0_count,
        auxiliary_x0_count=x0_run.auxiliary_x0_count,
        object_ids_before=x0_run.object_ids_before,
        object_ids_after=x0_run.object_ids_after,
        object_identity_preserved=(
            x0_run.object_ids_before == x0_run.object_ids_after
        ),
        results=results,
        audit=audit,
        terminal_deviation_mwh=deviations,
        supplied_executed_action=False,
        reason=None,
        solution_values=(
            _solution_values(build) if audit.accepted_primal else None
        ),
    )


def _empty_attempt(
    iteration: int,
    stop: int,
    outer_plan: OuterPlanRecord,
    initial_soc_mwh: Mapping[str, float],
    target_soc: Mapping[str, float],
    slot: AttemptSlot,
    state: SlotState,
    reason: str,
    source_attempt_id: str | None = None,
) -> CausalAttemptRecord:
    return CausalAttemptRecord(
        attempt_id=f"s3b-{iteration:03d}-{slot.ordinal:02d}-{slot.role}",
        iteration=iteration,
        local_interval_start=0,
        local_interval_stop=stop - iteration,
        interval_start=iteration,
        interval_stop=stop,
        outer_plan_id=outer_plan.outer_plan_id,
        initial_soc_mwh=dict(initial_soc_mwh),
        target_soc_mwh=dict(target_soc),
        slot=slot,
        slot_state=state,
        source_attempt_id=source_attempt_id,
        raw_starting_values=None,
        starting_values=None,
        solver_executed=False,
        x0_verified=False,
        solver_x0=None,
        solver_x0_layout=None,
        solver_x0_layout_signature=None,
        model_x0_count=None,
        auxiliary_x0_count=None,
        object_ids_before=None,
        object_ids_after=None,
        object_identity_preserved=None,
        results=None,
        audit=None,
        terminal_deviation_mwh=None,
        supplied_executed_action=False,
        reason=reason,
    )


def _execute_window(
    scenario: FrozenScenario,
    *,
    iteration: int,
    stop: int,
    outer_plan: OuterPlanRecord,
    realized_soc: Mapping[str, float],
    target_soc: Mapping[str, float],
    preceding_solution: Mapping[str, np.ndarray] | None,
    preceding_attempt_id: str | None = None,
) -> tuple[tuple[CausalAttemptRecord, ...], CausalAttemptRecord | None]:
    slots = attempt_registry(iteration)
    records: list[CausalAttemptRecord] = []
    accepted: CausalAttemptRecord | None = None

    try:
        primary_build = _build_ac(
            scenario, iteration, stop, realized_soc, target_soc
        )
        if preceding_solution is None:
            causal_raw = None
            causal_start = None
        else:
            causal_raw, causal_start = shifted_causal_start(
                preceding_solution,
                primary_build,
                realized_soc_mwh=realized_soc,
                storage_device_ids=_storage_id_order(scenario.storage),
                delta_hours=scenario.control.delta_hours,
                soc_tolerance=scenario.control.acceptance_tolerances[
                    "soc_recurrence_mwh_abs"
                ],
            )
            _assign_complete_start(primary_build, causal_start)
    except Exception as exc:
        reason = f"causal_start_construction_error:{type(exc).__name__}: {exc}"
        records.append(
            _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slots[0],
                "construction_error",
                reason,
            )
        )
        records.extend(
            _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slot,
                "source_unavailable",
                reason,
            )
            for slot in slots[1:]
        )
        return tuple(records), None
    primary = _attempt_from_build(
        scenario,
        iteration=iteration,
        stop=stop,
        outer_plan=outer_plan,
        target_soc=target_soc,
        slot=slots[0],
        source_attempt_id=preceding_attempt_id,
        build=primary_build,
        raw_start=causal_raw,
        assigned_start=None,
        target_free=False,
    )
    records.append(primary)
    if primary.audit is not None and primary.audit.accepted_primal:
        accepted = primary

    target_free: CausalAttemptRecord | None = None
    if accepted is None:
        try:
            target_free_build = _build_ac(
                scenario, iteration, stop, realized_soc, None
            )
            if causal_start is not None:
                _assign_complete_start(target_free_build, causal_start)
        except Exception as exc:
            target_free = _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slots[1],
                "construction_error",
                f"target_free_construction_error:{type(exc).__name__}: {exc}",
                primary.attempt_id,
            )
        else:
            target_free = _attempt_from_build(
                scenario,
                iteration=iteration,
                stop=stop,
                outer_plan=outer_plan,
                target_soc=target_soc,
                slot=slots[1],
                source_attempt_id=preceding_attempt_id,
                build=target_free_build,
                raw_start=causal_raw,
                assigned_start=None,
                target_free=True,
            )
        records.append(target_free)
    else:
        records.append(
            _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slots[1],
                "not_needed_after_acceptance",
                "primary controlling attempt accepted",
            )
        )

    if accepted is None and target_free is not None and target_free.solution_values:
        try:
            copied_build = _build_ac(
                scenario, iteration, stop, realized_soc, target_soc
            )
            _assign_complete_start(copied_build, target_free.solution_values)
        except Exception as exc:
            copied = _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slots[2],
                "construction_error",
                f"copied_start_construction_error:{type(exc).__name__}: {exc}",
                target_free.attempt_id,
            )
        else:
            copied = _attempt_from_build(
                scenario,
                iteration=iteration,
                stop=stop,
                outer_plan=outer_plan,
                target_soc=target_soc,
                slot=slots[2],
                source_attempt_id=target_free.attempt_id,
                build=copied_build,
                raw_start=target_free.solution_values,
                assigned_start=None,
                target_free=False,
            )
        records.append(copied)
        if copied.audit is not None and copied.audit.accepted_primal:
            accepted = copied
    elif accepted is not None:
        records.append(
            _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slots[2],
                "not_needed_after_acceptance",
                "earlier controlling attempt accepted",
            )
        )
    else:
        records.append(
            _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slots[2],
                "source_unavailable",
                "target-free solve was not accepted",
                None if target_free is None else target_free.attempt_id,
            )
        )

    centers = {
        "target_free": (
            None if target_free is None else target_free.solution_values
        ),
        "causal": (
            causal_start
            if causal_start is not None
            else primary.starting_values
        ),
    }
    for slot in slots[3:]:
        if accepted is not None:
            record = _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slot,
                "not_needed_after_acceptance",
                "earlier controlling attempt accepted",
            )
        elif centers[slot.source_kind] is None:
            record = _empty_attempt(
                iteration,
                stop,
                outer_plan,
                realized_soc,
                target_soc,
                slot,
                "source_unavailable",
                f"{slot.source_kind} perturbation center unavailable",
            )
        else:
            try:
                build = _build_ac(
                    scenario, iteration, stop, realized_soc, target_soc
                )
                center = {
                    name: np.asarray(value, dtype=float)
                    for name, value in centers[slot.source_kind].items()
                }
                raw, projected = perturb_start(
                    center,
                    build,
                    scale=float(slot.scale),
                    seed=int(slot.seed),
                )
                _assign_complete_start(build, projected)
            except Exception as exc:
                record = _empty_attempt(
                    iteration,
                    stop,
                    outer_plan,
                    realized_soc,
                    target_soc,
                    slot,
                    "construction_error",
                    f"perturbation_construction_error:{type(exc).__name__}: {exc}",
                )
            else:
                record = _attempt_from_build(
                    scenario,
                    iteration=iteration,
                    stop=stop,
                    outer_plan=outer_plan,
                    target_soc=target_soc,
                    slot=slot,
                    source_attempt_id=(
                        preceding_attempt_id
                        if slot.source_kind == "causal"
                        else target_free.attempt_id
                    ),
                    build=build,
                    raw_start=raw,
                    assigned_start=None,
                    target_free=False,
                )
                if record.audit is not None and record.audit.accepted_primal:
                    accepted = record
        records.append(record)

    if len(records) != 9:
        raise RuntimeError("S3b window must retain exactly nine attempt slots")
    if accepted is not None:
        accepted.supplied_executed_action = True
    return tuple(records), accepted


def _recovery_summary(
    executed_intervals: list[ExecutedIntervalRecord],
    outer_plans: Mapping[str, OuterPlanRecord],
    attempts: list[CausalAttemptRecord],
) -> dict[str, object]:
    executed_attempts = [item for item in attempts if item.solver_executed]
    runtime_by_role = {
        role: float(
            sum(
                item.audit.wall_time_seconds
                for item in executed_attempts
                if item.slot.role == role and item.audit is not None
            )
        )
        for role in {
            "primary_controlling",
            "target_free",
            "copied_target_free",
            "perturbed_target_free",
            "perturbed_causal",
        }
    }
    calls_by_role = {
        role: sum(item.slot.role == role for item in executed_attempts)
        for role in runtime_by_role
    }
    controlling = [item for item in attempts if item.supplied_executed_action]
    later_primary_attempts = [
        item
        for item in attempts
        if item.iteration > 0 and item.slot.role == "primary_controlling"
    ]
    shifted_primary_success = sum(
        item.iteration > 0 and item.slot.role == "primary_controlling"
        for item in controlling
    )
    base = dict(_trajectory_summary(executed_intervals, outer_plans, ()))
    base["cumulative_absolute_signpost_deviation_mwh"] = float(
        sum(
            sum(abs(value) for value in item.terminal_deviation_mwh.values())
            for item in controlling
            if item.terminal_deviation_mwh is not None
        )
    )
    ac_runtime = float(sum(runtime_by_role.values()))
    recovery_counts: dict[str, int] = {}
    for item in controlling:
        key = item.slot.role
        if item.slot.scale is not None:
            key = f"{key}:{item.slot.scale:g}"
        recovery_counts[key] = recovery_counts.get(key, 0) + 1
    return {
        **base,
        "runtime_seconds": float(base["runtime_seconds"]) + ac_runtime,
        "total_outer_runtime_seconds": float(base["runtime_seconds"]),
        "total_ac_runtime_seconds": ac_runtime,
        "runtime_by_role_seconds": runtime_by_role,
        "calls_by_role": calls_by_role,
        "shifted_primary_success_count": shifted_primary_success,
        "shifted_primary_opportunity_count": len(later_primary_attempts),
        "shifted_primary_success_fraction": (
            None
            if not later_primary_attempts
            else shifted_primary_success / len(later_primary_attempts)
        ),
        "successful_controlling_attempts_by_role": recovery_counts,
        "registered_attempt_count": len(attempts),
        "actual_ac_solver_call_count": len(executed_attempts),
        "successful_attempt_ids": [item.attempt_id for item in controlling],
    }


def run_causal_recovery() -> CausalRecoveryRun:
    """Execute the frozen S3b hard-target causal recovery trajectory."""
    scenario = load_frozen_scenario()
    storage_ids = _storage_id_order(scenario.storage)
    realized = _initial_soc(scenario)
    realized_history = [_aligned_values(realized, storage_ids, "initial SoC")]
    executed_b: list[np.ndarray] = []
    executed_intervals: list[ExecutedIntervalRecord] = []
    outer_plans: dict[str, OuterPlanRecord] = {}
    attempts: list[CausalAttemptRecord] = []
    preceding_solution: Mapping[str, np.ndarray] | None = None
    preceding_attempt_id: str | None = None
    termination_iteration = None
    termination_reason = None

    for iteration in range(scenario.control.horizon_steps):
        outer = _solve_outer_plan(scenario, iteration, realized, None)
        outer_plans[outer.outer_plan_id] = outer
        if not outer.audit.accepted_primal:
            termination_iteration = iteration
            termination_reason = f"outer_{outer.audit.outcome}"
            break
        window = min(
            scenario.control.nominal_ac_window_steps,
            scenario.control.horizon_steps - iteration,
        )
        stop = iteration + window
        target = _outer_target(outer, window)
        window_records, accepted = _execute_window(
            scenario,
            iteration=iteration,
            stop=stop,
            outer_plan=outer,
            realized_soc=realized,
            target_soc=target,
            preceding_solution=preceding_solution,
            preceding_attempt_id=preceding_attempt_id,
        )
        attempts.extend(window_records)
        if accepted is None:
            termination_iteration = iteration
            termination_reason = "unresolved_failure"
            break

        first_b = _as_2d(accepted.results["b"])[0]
        first_soc = _as_2d(accepted.results["soc"])[0]
        reconstructed = (
            _aligned_values(realized, storage_ids, "realized SoC")
            - scenario.control.delta_hours * first_b
        )
        tolerance = scenario.control.acceptance_tolerances[
            "soc_recurrence_mwh_abs"
        ]
        if np.max(np.abs(reconstructed - first_soc)) > tolerance:
            raise RuntimeError("Accepted action disagrees with post-step SoC")
        executed_b.append(first_b.copy())
        executed_intervals.append(_executed_interval_record(scenario, accepted))
        realized_history.append(reconstructed.copy())
        realized = dict(zip(storage_ids, reconstructed, strict=True))
        preceding_solution = accepted.solution_values
        preceding_attempt_id = accepted.attempt_id

    completed = len(executed_b) == scenario.control.horizon_steps
    return CausalRecoveryRun(
        outer_plans=outer_plans,
        attempts=tuple(attempts),
        executed_intervals=tuple(executed_intervals),
        realized_soc_mwh=np.asarray(realized_history),
        executed_b_mw=(
            np.asarray(executed_b)
            if executed_b
            else np.empty((0, len(storage_ids)))
        ),
        trajectory_summary=_recovery_summary(
            executed_intervals, outer_plans, attempts
        ),
        completed_intervals=len(executed_b),
        completion_fraction=len(executed_b) / scenario.control.horizon_steps,
        completed=completed,
        termination_iteration=None if completed else termination_iteration,
        termination_reason=None if completed else termination_reason,
    )


def _record_payload(record: CausalAttemptRecord) -> dict:
    payload = asdict(record)
    payload.pop("solution_values")
    return _jsonable(payload)


def execute_to_directory(output_path: Path = DEFAULT_OUTPUT) -> dict:
    """Execute S3b once from a clean reviewed commit and persist its audit."""
    if output_path.exists() and any(output_path.iterdir()):
        raise ValueError(f"S3b output directory is not fresh: {output_path}")
    context = execution_context()
    if context["git_dirty"]:
        raise ValueError("S3b execution requires a clean Git worktree")
    expected_versions = {
        "python": "3.13.2",
        "cvxpy": "1.9.2",
        "numpy": "2.5.1",
        "cyipopt": "1.7.0",
        "clarabel": "0.11.1",
        "ipopt": "3.14.19",
    }
    actual_versions = {
        "python": context["python"],
        "cvxpy": context["packages"]["cvxpy"],
        "numpy": context["packages"]["numpy"],
        "cyipopt": context["packages"]["cyipopt"],
        "clarabel": context["packages"]["clarabel"],
        "ipopt": context["ipopt_version"],
    }
    if actual_versions != expected_versions:
        raise ValueError(f"S3b solver environment differs: {actual_versions!r}")
    pre_runner_hash = _sha256(Path(__file__))
    pre_protocol_hash = _sha256(
        EXPERIMENT_ROOT / "S3B_CAUSAL_RECOVERY_PROTOCOL.md"
    )
    run = run_causal_recovery()
    post_status = _git_output("status", "--porcelain")
    post_commit = _git_output("rev-parse", "HEAD")
    post_runner_hash = _sha256(Path(__file__))
    post_protocol_hash = _sha256(
        EXPERIMENT_ROOT / "S3B_CAUSAL_RECOVERY_PROTOCOL.md"
    )
    stable = (
        not post_status
        and post_commit == context["git_commit"]
        and post_runner_hash == pre_runner_hash
        and post_protocol_hash == pre_protocol_hash
    )
    if not stable:
        raise RuntimeError("S3b execution source changed during the run")
    output_path.mkdir(parents=True, exist_ok=True)
    _atomic_write_gzip_json(
        output_path / "causal_recovery.json.gz",
        {
            "artifact_schema_version": 1,
            "study": "s3b_causal_recovery",
            "outer_plans": {
                plan_id: _outer_payload(plan)
                for plan_id, plan in run.outer_plans.items()
            },
            "attempts": [_record_payload(item) for item in run.attempts],
            "executed_intervals": [
                _executed_payload(item) for item in run.executed_intervals
            ],
            "realized_soc_mwh": _jsonable(run.realized_soc_mwh),
            "executed_b_mw": _jsonable(run.executed_b_mw),
            "trajectory_summary": _jsonable(run.trajectory_summary),
            "completed_intervals": run.completed_intervals,
            "completion_fraction": run.completion_fraction,
            "completed": run.completed,
            "termination_iteration": run.termination_iteration,
            "termination_reason": run.termination_reason,
        },
    )
    summary = {
        "completed": run.completed,
        "completed_intervals": run.completed_intervals,
        "termination_iteration": run.termination_iteration,
        "termination_reason": run.termination_reason,
        "trajectory_summary": run.trajectory_summary,
        "artifact_sha256": _sha256(output_path / "causal_recovery.json.gz"),
        "runner_source_sha256": post_runner_hash,
        "protocol_sha256": post_protocol_hash,
        "scenario_manifest_sha256": _sha256(
            EXPERIMENT_ROOT / "prepared_scenario/manifest.json"
        ),
        "cvxopf_source_sha256": _source_fingerprint(
            sorted((REPOSITORY_ROOT / "src/cvxopf").rglob("*.py"))
        ),
    }
    _atomic_write_text(
        output_path / "summary.json",
        json.dumps(_jsonable(summary), indent=2) + "\n",
    )
    metadata = {
        **context,
        "runner_source_sha256": post_runner_hash,
        "protocol_sha256": post_protocol_hash,
        "post_execution": {
            "git_commit": post_commit,
            "git_status_porcelain": post_status.splitlines(),
            "execution_source_stable": stable,
        },
        "artifacts": {
            name: {
                "bytes": (output_path / name).stat().st_size,
                "sha256": _sha256(output_path / name),
            }
            for name in ("causal_recovery.json.gz", "summary.json")
        },
    }
    _atomic_write_text(
        output_path / "metadata.json",
        json.dumps(_jsonable(metadata), indent=2) + "\n",
    )
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    metadata = execute_to_directory(args.output)
    print(json.dumps(_jsonable(metadata), indent=2))


if __name__ == "__main__":
    main()
