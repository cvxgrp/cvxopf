"""Independent archive reconstruction and advancement audit for S2."""

from __future__ import annotations

from dataclasses import replace
import gzip
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence, cast

import cvxpy as cp
import numpy as np

from cvxopf import HierarchicalInputs, OPFBuild
from experiments.case118_annual_hierarchy.audit import audit_probe
from cvxopf.generator import gen_cost_expr, generator_gencost
from cvxopf.storage import storage_cost_expr
from experiments.case118_annual_hierarchy.p0_fixture import (
    frozen_p0_policy,
    frozen_p0_solve_config,
    policy_sha256,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.s2_fixture import (
    S2_HORIZON_STEPS,
    load_s2_fixture,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    load_verified_outer_plan_archive,
    outer_boundaries,
    residual_tolerances,
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_driver import (
    _load_resource_samples,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    load_verified_checkpoint,
    sha256_path,
)


def _read_gzip(path: Path) -> Mapping[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return cast(Mapping[str, object], json.load(stream))


def _number(value: object) -> float:
    return float(cast(float, value))


def _integer(value: object) -> int:
    return int(cast(int, value))


def _shifted_primary_statistics(
    metrics: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    opportunities = [
        item for item in metrics if _integer(item["iteration"]) > 0
    ]
    successes = sum(
        _integer(item["controlling_ordinal"]) == 0
        for item in opportunities
    )
    first = next(
        (item for item in metrics if _integer(item["iteration"]) == 0),
        None,
    )
    return {
        "first_flat_primary_accepted": bool(
            first is not None and _integer(first["controlling_ordinal"]) == 0
        ),
        "shifted_primary_opportunity_count": len(opportunities),
        "shifted_primary_success_count": successes,
        "shifted_primary_success_fraction": (
            successes / len(opportunities) if opportunities else None
        ),
    }


def _controlling_attempt(window: Mapping[str, object]) -> Mapping[str, object]:
    executed = cast(Mapping[str, object], window["executed_interval"])
    attempt_id = executed["controlling_attempt_id"]
    matches = [
        cast(Mapping[str, object], attempt)
        for attempt in cast(Sequence[object], window["attempts"])
        if cast(Mapping[str, object], attempt)["attempt_id"] == attempt_id
    ]
    if len(matches) != 1:
        raise ValueError("S2 window lacks one controlling attempt")
    return matches[0]


def _interval_metrics(
    window: Mapping[str, object], fixture_inputs: HierarchicalInputs
) -> Mapping[str, object]:
    inputs = fixture_inputs
    controlling = _controlling_attempt(window)
    result = cast(Mapping[str, object], controlling["result"])
    pg = np.asarray(result["Pg"], dtype=float)[0]
    b = np.asarray(result["b"], dtype=float)[0]
    soc = np.asarray(result["soc"], dtype=float)[0]
    initial = np.asarray(window["initial_soc_mwh"], dtype=float)
    post = np.asarray(window["post_step_soc_mwh"], dtype=float)
    delta = float(inputs.delta)
    generation = float(
        gen_cost_expr(
            generator_gencost(list(inputs.generators)), cp.Constant(pg)
        ).value
    )
    cycling = float(
        storage_cost_expr(list(inputs.storage), cp.Constant(b)).value
    )
    p_from = np.asarray(result["branch_p_from"], dtype=float)[0]
    p_to = np.asarray(result["branch_p_to"], dtype=float)[0]
    raw_loss = float(np.sum(p_from + p_to))
    p_net_loss = float(np.sum(np.asarray(result["p_net"], dtype=float)[0]))
    curtailment = float(np.sum(np.asarray(result["curtailment"], dtype=float)[0]))
    vm = np.asarray(result["Vm"], dtype=float)[0]
    bus = np.asarray(inputs.case["bus"], dtype=float)
    voltage = float(
        np.max(
            np.maximum.reduce(
                [vm - bus[:, 11], bus[:, 12] - vm, np.zeros_like(vm)]
            )
        )
    )
    branch = np.asarray(inputs.case["branch"], dtype=float)
    limited = (branch[:, 10] == 1) & (branch[:, 5] > 0) & np.isfinite(branch[:, 5])
    ratings = branch[limited, 5]
    apparent = np.concatenate(
        [
            np.asarray(result["branch_s_from"], dtype=float)[0, limited],
            np.asarray(result["branch_s_to"], dtype=float)[0, limited],
        ]
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
    served = np.asarray(result["p_load_served"], dtype=float)[0]
    requested = np.asarray(result["p_load"], dtype=float)[0]
    audit = cast(Mapping[str, object], controlling["audit"])
    ids = tuple(str(unit.device_id) for unit in inputs.storage)
    initial_by_id = dict(
        zip(ids, cast(Sequence[float], window["initial_soc_mwh"]), strict=True)
    )
    target_by_id = dict(
        zip(ids, cast(Sequence[float], window["target_soc_mwh"]), strict=True)
    )
    window_storage = tuple(
        replace(
            unit,
            initial_soc=float(initial_by_id[device_id]),
            terminal_soc=float(target_by_id[device_id]),
            terminal_constraint="equality",
            terminal_cost=None,
            terminal_weight=None,
        )
        for unit, device_id in zip(inputs.storage, ids, strict=True)
    )
    build = cast(OPFBuild, SimpleNamespace(formulation="ac"))
    reconstructed_audit = audit_probe(
        inputs.case,
        build,
        result,
        generators=inputs.generators,
        loads=inputs.loads,
        nondispatchable=inputs.nondispatchable,
        storage=window_storage,
        delta=inputs.delta,
        tolerances=frozen_p0_policy().tolerances,
    )
    archived_residuals = cast(Mapping[str, object], audit["residuals"])
    audit_agrees = bool(
        reconstructed_audit.accepted_primal
        and audit["accepted_primal"] is True
        and reconstructed_audit.status == audit["status"]
        and list(reconstructed_audit.missing_or_nonfinite_fields)
        == audit["missing_or_nonfinite_fields"]
        and reconstructed_audit.identity_error == audit["identity_error"]
        and set(reconstructed_audit.residuals) == set(archived_residuals)
        and all(
            value == _number(archived_residuals[name])
            for name, value in reconstructed_audit.residuals.items()
        )
    )
    executed_attempts = [
        cast(Mapping[str, object], attempt)
        for attempt in cast(Sequence[object], window["attempts"])
        if cast(Mapping[str, object], attempt).get("slot_state") == "executed"
    ]
    runtime_by_role: dict[str, float] = {}
    for attempt in executed_attempts:
        role = str(attempt["role"])
        attempt_audit = cast(Mapping[str, object], attempt["audit"])
        runtime_by_role[role] = runtime_by_role.get(role, 0.0) + _number(
            attempt_audit["wall_time_seconds"]
        )
    deviation = np.asarray(
        result["storage_terminal_deviation"], dtype=float
    ).reshape(-1)
    return {
        "iteration": int(cast(int, window["iteration"])),
        "controlling_ordinal": int(cast(int, controlling["ordinal"])),
        "generation_cost": delta * generation,
        "storage_cycling_cost": delta * cycling,
        "renewable_curtailment_mwh": delta * max(curtailment, 0.0),
        "active_loss_mwh": delta * max(raw_loss, 0.0),
        "active_loss_crosscheck_mw_abs": abs(raw_loss - p_net_loss),
        "voltage_violation_pu": voltage,
        "thermal_residual_mva": thermal,
        "normalized_squared_thermal_residual": normalized,
        "soc_recurrence_residual_mwh_abs": float(
            np.max(np.abs(post - initial + delta * b))
        ),
        "reported_soc_residual_mwh_abs": float(np.max(np.abs(post - soc))),
        "fixed_load_service_residual_mw_abs": float(
            np.max(np.abs(served - requested))
        ),
        "terminal_deviation_mwh_abs": float(np.sum(np.abs(deviation))),
        "storage_throughput_mwh": (delta * np.abs(b)).tolist(),
        "attempt_wall_time_seconds": _number(audit["wall_time_seconds"]),
        "executed_attempt_count": len(executed_attempts),
        "attempt_wall_time_by_role_seconds": runtime_by_role,
        "controlling_audit_reconstructed_and_equal": audit_agrees,
    }


def analyze_s2(
    directory: Path,
    *,
    source_fingerprint: str,
    scenario_hash: str,
    trajectory_status: str,
) -> Mapping[str, object]:
    """Verify persisted S2 state and reconstruct all realized summaries."""
    fixture = load_s2_fixture()
    inputs = fixture.inputs
    policy = frozen_p0_policy()
    solve_config = frozen_p0_solve_config()
    if scenario_hash != fixture.scenario_hash:
        raise ValueError("S2 analysis scenario hash mismatch")
    trajectory = directory / "trajectory"
    outer_path = trajectory / "outer-plan.json.gz"
    checkpoint_path = trajectory / "checkpoint.json"
    outer = load_verified_outer_plan_archive(
        outer_path,
        inputs=inputs,
        policy=policy,
        expected_solve_config_sha256=solve_config_sha256(solve_config),
        expected_source_fingerprint=source_fingerprint,
        expected_scenario_hash=scenario_hash,
    )
    checkpoint = load_verified_checkpoint(
        checkpoint_path,
        expected_source_fingerprint=source_fingerprint,
        expected_scenario_hash=scenario_hash,
        expected_outer_plan_sha256=sha256_path(outer_path),
        expected_policy_hash=policy_sha256(policy),
        expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=residual_tolerances(policy),
        expected_inner_terminal_policy=policy.inner_terminal_policy,
        expected_horizon_steps=S2_HORIZON_STEPS,
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=result_dimensions(inputs),
        expected_delta_hours=inputs.delta,
        expected_outer_boundary_soc_mwh=outer_boundaries(outer),
    )
    samples, _, _ = _load_resource_samples(
        trajectory,
        checkpoint,
        source_fingerprint=source_fingerprint,
        scenario_hash=scenario_hash,
        policy_hash=policy_sha256(policy),
    )
    entries = cast(Sequence[Mapping[str, object]], checkpoint["windows"])
    windows = [_read_gzip(trajectory / str(item["relative_path"])) for item in entries]
    metrics = [_interval_metrics(window, inputs) for window in windows]
    completed = len(metrics)
    storage_ids = tuple(str(unit.device_id) for unit in inputs.storage)
    throughput = np.sum(
        np.asarray([item["storage_throughput_mwh"] for item in metrics]),
        axis=0,
    ) if metrics else np.zeros(len(storage_ids))
    final_soc = np.asarray(checkpoint["realized_soc_mwh"], dtype=float)
    if any(unit.terminal_soc is None for unit in inputs.storage):
        raise ValueError("S2 storage fleet lacks its global terminal target")
    target = np.asarray(
        [cast(float, unit.terminal_soc) for unit in inputs.storage], dtype=float
    )
    ordinals = [_integer(item["controlling_ordinal"]) for item in metrics]
    accepted_for_s3 = bool(
        trajectory_status == "complete"
        and completed == S2_HORIZON_STEPS
        and all(
            item["controlling_audit_reconstructed_and_equal"] is True
            for item in metrics
        )
        and np.max(np.abs(final_soc - target))
        <= policy.tolerances.terminal_soc_mwh_abs
        and max(
            (_number(item["soc_recurrence_residual_mwh_abs"]) for item in metrics),
            default=0.0,
        )
        <= policy.tolerances.soc_recurrence_mwh_abs
        and max(
            (_number(item["fixed_load_service_residual_mw_abs"]) for item in metrics),
            default=0.0,
        )
        <= policy.tolerances.ac_active_balance_pu_abs
        * _number(inputs.case["baseMVA"])
        and max(
            (_number(item["voltage_violation_pu"]) for item in metrics),
            default=0.0,
        )
        <= policy.tolerances.voltage_bound_pu_abs
        and max(
            (_number(item["thermal_residual_mva"]) for item in metrics),
            default=0.0,
        )
        <= policy.tolerances.branch_mva_abs
        and max(
            (
                _number(item["normalized_squared_thermal_residual"])
                for item in metrics
            ),
            default=0.0,
        )
        <= policy.tolerances.branch_normalized_squared_residual
    )
    runtime_by_role: dict[str, float] = {}
    for item in metrics:
        values = cast(
            Mapping[str, object], item["attempt_wall_time_by_role_seconds"]
        )
        for role, value in values.items():
            runtime_by_role[role] = runtime_by_role.get(role, 0.0) + _number(
                value
            )
    shifted_statistics = _shifted_primary_statistics(metrics)
    return {
        "schema_version": 1,
        "trajectory_status": trajectory_status,
        "accepted_for_s3": accepted_for_s3,
        "completed_intervals": completed,
        "coverage_fraction": completed / S2_HORIZON_STEPS,
        "storage_device_ids": list(storage_ids),
        "final_soc_mwh": final_soc.tolist(),
        "terminal_deviation_mwh": (final_soc - target).tolist(),
        "storage_throughput_mwh": throughput.tolist(),
        "controlling_ordinal_counts": {
            str(ordinal): ordinals.count(ordinal) for ordinal in sorted(set(ordinals))
        },
        "recovery_window_count": sum(ordinal != 0 for ordinal in ordinals),
        **shifted_statistics,
        "generation_cost": sum(_number(item["generation_cost"]) for item in metrics),
        "storage_cycling_cost": sum(
            _number(item["storage_cycling_cost"]) for item in metrics
        ),
        "renewable_curtailment_mwh": sum(
            _number(item["renewable_curtailment_mwh"]) for item in metrics
        ),
        "active_loss_mwh": sum(_number(item["active_loss_mwh"]) for item in metrics),
        "maximum_active_loss_crosscheck_mw_abs": max(
            (_number(item["active_loss_crosscheck_mw_abs"]) for item in metrics),
            default=0.0,
        ),
        "maximum_voltage_violation_pu": max(
            (_number(item["voltage_violation_pu"]) for item in metrics), default=0.0
        ),
        "maximum_thermal_residual_mva": max(
            (_number(item["thermal_residual_mva"]) for item in metrics), default=0.0
        ),
        "maximum_normalized_squared_thermal_residual": max(
            (
                _number(item["normalized_squared_thermal_residual"])
                for item in metrics
            ),
            default=0.0,
        ),
        "maximum_soc_recurrence_residual_mwh_abs": max(
            (
                _number(item["soc_recurrence_residual_mwh_abs"])
                for item in metrics
            ),
            default=0.0,
        ),
        "maximum_fixed_load_service_residual_mw_abs": max(
            (
                _number(item["fixed_load_service_residual_mw_abs"])
                for item in metrics
            ),
            default=0.0,
        ),
        "cumulative_absolute_signpost_deviation_mwh": sum(
            _number(item["terminal_deviation_mwh_abs"]) for item in metrics
        ),
        "controlling_attempt_wall_time_seconds": sum(
            _number(item["attempt_wall_time_seconds"]) for item in metrics
        ),
        "executed_attempt_count": sum(
            _integer(item["executed_attempt_count"]) for item in metrics
        ),
        "attempt_wall_time_by_role_seconds": runtime_by_role,
        "resource_sample_count": len(samples),
        "maximum_current_rss_mib": max(
            (sample.rss_bytes / (1024.0**2) for sample in samples), default=0.0
        ),
        "outer_plan_sha256": sha256_path(outer_path),
        "checkpoint_sha256": sha256_path(checkpoint_path),
    }


__all__ = ["analyze_s2"]
