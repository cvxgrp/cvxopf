"""Build-free archival projection for the case118 streaming experiment."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import json
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from cvxopf import ACAttemptRecord, HierarchicalInputs, HierarchicalPolicy, OPFBuild

from experiments.case118_annual_hierarchy.streaming_runner import (
    StreamingOuterPlan,
    StreamingWindowResult,
    execution_input_sha256,
    variables_by_name,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    SCHEMA_VERSION,
    WindowIndexEntry,
    atomic_gzip_json,
    atomic_json,
    checkpoint_payload,
    sha256_path,
    validate_window_archive,
)


@dataclass(frozen=True)
class PersistedWindow:
    """Result of one ordered archive/checkpoint transaction."""

    artifact: WindowIndexEntry
    checkpoint: Mapping[str, object] | None
    completed_entries: tuple[WindowIndexEntry, ...]


def _json_value(value: object, *, normalize_nonfinite_scalar: bool = False) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return _json_value(
            value.item(),
            normalize_nonfinite_scalar=normalize_nonfinite_scalar,
        )
    if isinstance(value, Mapping):
        return {
            str(key): _json_value(
                item,
                normalize_nonfinite_scalar=normalize_nonfinite_scalar,
            )
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [
            _json_value(
                item,
                normalize_nonfinite_scalar=normalize_nonfinite_scalar,
            )
            for item in value
        ]
    if isinstance(value, float) and not np.isfinite(value):
        if normalize_nonfinite_scalar:
            return None
        raise ValueError("nonfinite scalar is not valid archive evidence")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported archive value {type(value).__name__}")


def _structural_signature(build: OPFBuild) -> dict[str, object]:
    variables = variables_by_name(build)
    variable_records = [
        {"name": name, "shape": list(variables[name].shape)}
        for name in sorted(variables)
    ]
    constraint_records = [
        f"{type(constraint).__name__}|shape={tuple(constraint.shape)}"
        for constraint in build.prob.constraints
    ]
    parameter_records = [
        {"name": parameter.name(), "shape": list(parameter.shape)}
        for parameter in sorted(
            build.prob.parameters(), key=lambda item: item.name()
        )
    ]
    return {
        "variables": variable_records,
        "constraints": constraint_records,
        "parameters": parameter_records,
    }


def _audit_payload(attempt: ACAttemptRecord) -> dict[str, object] | None:
    if attempt.audit is None:
        return None
    audit = attempt.audit
    return {
        "status": audit.status,
        "outcome": audit.outcome,
        "accepted_primal": audit.accepted_primal,
        "missing_or_nonfinite_fields": list(audit.missing_or_nonfinite_fields),
        "identity_error": audit.identity_error,
        "residuals": _json_value(audit.residuals),
        "exception": audit.exception,
        "wall_time_seconds": audit.wall_time_seconds,
        "solver_num_iters": audit.solver_num_iters,
        "solver_setup_time_seconds": audit.solver_setup_time_seconds,
        "solver_solve_time_seconds": audit.solver_solve_time_seconds,
    }


def attempt_archive_payload(
    attempt: ACAttemptRecord, *, result_dimensions: Mapping[str, int]
) -> dict[str, object]:
    """Project one live public attempt record into immutable JSON data."""
    evidence = attempt.solver_evidence
    assigned = (
        None
        if attempt.assigned_start is None
        else {
            name: _json_value(attempt.assigned_start[name])
            for name in sorted(attempt.assigned_start)
        }
    )
    raw = (
        None
        if attempt.raw_start is None
        else {
            name: _json_value(attempt.raw_start[name])
            for name in sorted(attempt.raw_start)
        }
    )
    return {
        "attempt_id": attempt.attempt_id,
        "slot_state": attempt.slot_state,
        "role": attempt.role,
        "transformation": attempt.transformation,
        "ordinal": attempt.ordinal,
        "iteration": attempt.iteration,
        "source_kind": attempt.source_kind,
        "source_attempt_id": attempt.source_attempt_id,
        "inner_terminal_policy": attempt.inner_terminal_policy,
        "formulation": "ac",
        "result_dimensions": dict(result_dimensions),
        "scale": attempt.scale,
        "seed": attempt.seed,
        "reason": attempt.reason,
        "solver_executed": attempt.slot_state == "executed",
        "supplied_executed_action": attempt.supplied_executed_action,
        "raw_start": raw,
        "assigned_start": assigned,
        "solver_x0": None if evidence is None else evidence.complete_x0.tolist(),
        "solver_x0_layout": (
            None if evidence is None else _json_value(evidence.layout)
        ),
        "solver_evidence": (
            None
            if evidence is None
            else {
                "layout_signature": evidence.layout_signature,
                "model_coordinate_count": evidence.model_coordinate_count,
                "auxiliary_coordinate_count": evidence.auxiliary_coordinate_count,
                "object_ids_before": _json_value(evidence.object_ids_before),
                "object_ids_after": _json_value(evidence.object_ids_after),
            }
        ),
        "structural_signature": (
            None if attempt.build is None else _structural_signature(attempt.build)
        ),
        "result": (
            None
            if attempt.result is None
            else _json_value(
                attempt.result, normalize_nonfinite_scalar=True
            )
        ),
        "audit": _audit_payload(attempt),
    }


def _result_dimensions(inputs: HierarchicalInputs) -> dict[str, int]:
    return {
        "generators": len(inputs.generators),
        "buses": len(np.asarray(inputs.case["bus"])),
        "branches": len(np.asarray(inputs.case["branch"])),
        "loads": len(inputs.loads),
        "storage": len(inputs.storage),
        "nondispatchable": len(inputs.nondispatchable),
        "hvdc": len(inputs.hvdc),
    }


def _residual_tolerances(policy: HierarchicalPolicy) -> dict[str, float]:
    return {
        name: float(getattr(policy.tolerances, name))
        for name in policy.tolerances.__dataclass_fields__
    }


def _outer_boundaries(outer: StreamingOuterPlan) -> dict[int, dict[str, float]]:
    outer.verify_signpost_integrity()
    return {
        int(boundary): outer.target_at(int(boundary))
        for boundary in outer.global_boundary_indices
    }


def outer_plan_archive_payload(
    outer: StreamingOuterPlan, *, inputs: HierarchicalInputs
) -> dict[str, object]:
    """Project the retained outer plan into one build-free artifact."""
    outer.verify_signpost_integrity()
    if outer.input_fingerprint != execution_input_sha256(inputs):
        raise ValueError("outer plan does not match its archival input snapshot")
    if outer.boundary_soc_mwh is None:
        raise ValueError("accepted outer plan lacks SoC signposts")
    extracted_soc = np.asarray(outer.result["soc"], dtype=float)
    if not np.array_equal(extracted_soc, outer.boundary_soc_mwh[1:]):
        raise ValueError("outer signposts differ from the retained outer result")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "iteration": 0,
        "outer_plan_id": outer.outer_plan_id,
        "input_fingerprint": outer.input_fingerprint,
        "horizon_steps": outer.horizon_steps,
        "delta_hours": outer.delta_hours,
        "storage_device_ids": list(outer.storage_device_ids),
        "policy_sha256": outer.policy_sha256,
        "solve_config_sha256": outer.solve_config_sha256,
        "signpost_sha256": outer.signpost_sha256,
        "global_boundary_indices": outer.global_boundary_indices.tolist(),
        "boundary_soc_mwh": outer.boundary_soc_mwh.tolist(),
        "structural_signature": _structural_signature(outer.build),
        "result": _json_value(outer.result),
        "audit": {
            "status": outer.audit.status,
            "missing_or_nonfinite_fields": list(
                outer.audit.missing_or_nonfinite_fields
            ),
            "identity_error": outer.audit.identity_error,
            "residuals": _json_value(outer.audit.residuals),
            "accepted_primal": outer.audit.accepted_primal,
            "exception": outer.exception,
            "wall_time_seconds": outer.wall_time_seconds,
        },
    }
    json.dumps(payload, allow_nan=False)
    return payload


def write_verified_outer_plan_archive(
    path: Path, outer: StreamingOuterPlan, *, inputs: HierarchicalInputs
) -> WindowIndexEntry:
    """Atomically persist and byte-verify the one retained outer plan."""
    payload = outer_plan_archive_payload(outer, inputs=inputs)
    entry = atomic_gzip_json(path, payload)
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        reloaded = json.load(stream)
    if reloaded != payload or entry.sha256 != sha256_path(path):
        raise RuntimeError("outer-plan artifact changed during atomic persistence")
    return entry


def window_archive_payload(
    window: StreamingWindowResult,
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: StreamingOuterPlan,
    preceding_controlling_attempt_id: str | None,
) -> dict[str, object]:
    """Create and validate one complete build-free window archive."""
    ids = tuple(str(unit.device_id) for unit in inputs.storage)
    first = window.attempts[0]
    initial = [float(first.initial_soc_mwh[device_id]) for device_id in ids]
    target = [float(first.target_soc_mwh[device_id]) for device_id in ids]
    controlling = window.controlling_attempt
    executed = None
    if controlling is not None:
        if controlling.result is None or window.post_step_soc_mwh is None:
            raise RuntimeError("controlling attempt lacks executed state evidence")
        b = np.asarray(controlling.result["b"], dtype=float).reshape(
            window.interval_stop - window.iteration, len(ids)
        )[0]
        executed = {
            "controlling_attempt_id": controlling.attempt_id,
            "b_mw": b.tolist(),
        }
    dimensions = _result_dimensions(inputs)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "iteration": window.iteration,
        "interval_start": window.iteration,
        "interval_stop": window.interval_stop,
        "formulation": "ac",
        "result_dimensions": dimensions,
        "storage_device_ids": list(ids),
        "initial_soc_mwh": initial,
        "target_soc_mwh": target,
        "delta_hours": inputs.delta,
        "soc_tolerance_mwh": policy.tolerances.soc_recurrence_mwh_abs,
        "preceding_controlling_attempt_id": preceding_controlling_attempt_id,
        "attempts": [
            attempt_archive_payload(item, result_dimensions=dimensions)
            for item in window.attempts
        ],
        "executed_interval": executed,
        "post_step_soc_mwh": (
            None
            if window.post_step_soc_mwh is None
            else [window.post_step_soc_mwh[device_id] for device_id in ids]
        ),
    }
    validate_window_archive(
        payload,
        expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=_residual_tolerances(policy),
        expected_inner_terminal_policy=policy.inner_terminal_policy,
        expected_horizon_steps=inputs.horizon_steps,
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=_result_dimensions(inputs),
        expected_delta_hours=inputs.delta,
        expected_outer_boundary_soc_mwh=_outer_boundaries(outer),
    )
    return payload


def write_verified_window_archive(
    path: Path,
    payload: Mapping[str, object],
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: StreamingOuterPlan,
) -> WindowIndexEntry:
    """Atomically write, reload, and semantically verify one window."""
    entry = atomic_gzip_json(path, payload)
    if entry.sha256 != sha256_path(path):
        raise RuntimeError("window artifact hash changed after atomic write")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        reloaded = json.load(stream)
    validate_window_archive(
        reloaded,
        expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=_residual_tolerances(policy),
        expected_inner_terminal_policy=policy.inner_terminal_policy,
        expected_horizon_steps=inputs.horizon_steps,
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=_result_dimensions(inputs),
        expected_delta_hours=inputs.delta,
        expected_outer_boundary_soc_mwh=_outer_boundaries(outer),
    )
    return entry


def write_checkpoint_after_success(
    path: Path,
    *,
    source_fingerprint: str,
    scenario_hash: str,
    outer_plan_sha256: str,
    policy_hash: str,
    storage_device_ids: Sequence[str],
    initial_soc_mwh: Sequence[float],
    realized_soc_mwh: Sequence[float],
    entries: Sequence[WindowIndexEntry],
) -> dict[str, object]:
    """Atomically advance the resume checkpoint after an archived action."""
    payload = checkpoint_payload(
        source_fingerprint=source_fingerprint,
        scenario_hash=scenario_hash,
        outer_plan_sha256=outer_plan_sha256,
        policy_hash=policy_hash,
        storage_device_ids=storage_device_ids,
        initial_soc_mwh=initial_soc_mwh,
        realized_soc_mwh=realized_soc_mwh,
        entries=entries,
    )
    atomic_json(path, payload)
    if json.loads(path.read_text()) != payload:
        raise RuntimeError("checkpoint changed during atomic persistence")
    return payload


def persist_window_transaction(
    directory: Path,
    window: StreamingWindowResult,
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: StreamingOuterPlan,
    outer_plan_artifact: WindowIndexEntry,
    preceding_controlling_attempt_id: str | None,
    source_fingerprint: str,
    scenario_hash: str,
    policy_hash: str,
    initial_soc_mwh: Sequence[float],
    completed_entries: Sequence[WindowIndexEntry],
) -> PersistedWindow:
    """Archive one decision, then advance its checkpoint only after success."""
    if window.iteration != len(completed_entries):
        raise ValueError("window iteration must follow completed checkpoint entries")
    root = directory.resolve()
    outer_path = (directory / outer_plan_artifact.relative_path).resolve()
    if (
        not outer_path.is_relative_to(root)
        or not outer_path.is_file()
        or outer_path.stat().st_size != outer_plan_artifact.bytes
        or sha256_path(outer_path) != outer_plan_artifact.sha256
    ):
        raise ValueError("outer-plan artifact integrity check failed")
    payload = window_archive_payload(
        window,
        inputs=inputs,
        policy=policy,
        outer=outer,
        preceding_controlling_attempt_id=preceding_controlling_attempt_id,
    )
    prefix = "window" if window.post_step_soc_mwh is not None else "failed-window"
    artifact_path = directory / f"{prefix}-{window.iteration:06d}.json.gz"
    entry = write_verified_window_archive(
        artifact_path,
        payload,
        inputs=inputs,
        policy=policy,
        outer=outer,
    )
    if window.post_step_soc_mwh is None:
        return PersistedWindow(entry, None, tuple(completed_entries))
    ids = tuple(str(unit.device_id) for unit in inputs.storage)
    new_entries = (*completed_entries, entry)
    checkpoint = write_checkpoint_after_success(
        directory / "checkpoint.json",
        source_fingerprint=source_fingerprint,
        scenario_hash=scenario_hash,
        outer_plan_sha256=outer_plan_artifact.sha256,
        policy_hash=policy_hash,
        storage_device_ids=ids,
        initial_soc_mwh=initial_soc_mwh,
        realized_soc_mwh=[window.post_step_soc_mwh[device_id] for device_id in ids],
        entries=new_entries,
    )
    return PersistedWindow(entry, checkpoint, new_entries)


__all__ = [
    "PersistedWindow",
    "attempt_archive_payload",
    "outer_plan_archive_payload",
    "persist_window_transaction",
    "window_archive_payload",
    "write_checkpoint_after_success",
    "write_verified_outer_plan_archive",
    "write_verified_window_archive",
]
