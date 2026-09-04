"""Build-free archival projection for the case118 streaming experiment."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, Sequence, cast

import numpy as np

from cvxopf import (
    ACAttemptRecord,
    HierarchicalInputs,
    HierarchicalPolicy,
    OPFBuild,
    TemporalAssembly,
)

from experiments.case118_annual_hierarchy.audit import ProbeAudit, audit_probe
from experiments.case118_annual_hierarchy.p0_fixture import policy_sha256
from experiments.case118_annual_hierarchy.streaming_runner import (
    CausalControllerSource,
    StreamingOuterPlan,
    StreamingWindowResult,
    causal_source_from_attempt,
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
        for parameter in sorted(build.prob.parameters(), key=lambda item: item.name())
    ]
    return {
        "temporal_assembly": build.temporal_assembly,
        "canonicalization_backend": build.canonicalization_backend,
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
    causal_source = (
        causal_source_from_attempt(attempt)
        if attempt.supplied_executed_action
        else None
    )
    return {
        "attempt_id": attempt.attempt_id,
        "slot_state": attempt.slot_state,
        "role": attempt.role,
        "transformation": attempt.transformation,
        "ordinal": attempt.ordinal,
        "iteration": attempt.iteration,
        "global_interval_start": attempt.global_interval_start,
        "global_interval_stop": attempt.global_interval_stop,
        "outer_plan_id": attempt.outer_plan_id,
        "storage_device_ids": list(attempt.storage_device_ids),
        "initial_soc_mwh": dict(attempt.initial_soc_mwh),
        "target_soc_mwh": dict(attempt.target_soc_mwh),
        "source_kind": attempt.source_kind,
        "source_attempt_id": attempt.source_attempt_id,
        "inner_terminal_policy": attempt.inner_terminal_policy,
        "formulation": "ac",
        "result_dimensions": dict(result_dimensions),
        "scale": attempt.scale,
        "seed": attempt.seed,
        "reason": attempt.reason,
        "timeout_budget_seconds": attempt.timeout_budget_seconds,
        "solver_executed": attempt.slot_state in {"executed", "timeout"},
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
            else _json_value(attempt.result, normalize_nonfinite_scalar=True)
        ),
        "audit": _audit_payload(attempt),
        "causal_source": (
            None
            if causal_source is None
            else {
                "attempt_id": causal_source.attempt_id,
                "ordinal": causal_source.ordinal,
                "role": causal_source.role,
                "iteration": causal_source.iteration,
                "global_interval_start": causal_source.global_interval_start,
                "global_interval_stop": causal_source.global_interval_stop,
                "outer_plan_id": causal_source.outer_plan_id,
                "storage_device_ids": list(causal_source.storage_device_ids),
                "initial_soc_mwh": dict(causal_source.initial_soc_mwh),
                "first_soc_mwh": causal_source.first_soc_mwh.tolist(),
                "first_b_mw": causal_source.first_b_mw.tolist(),
                "solution_values": _json_value(causal_source.solution_values),
            }
        ),
    }


def causal_source_from_archive(
    attempt_payload: Mapping[str, object],
) -> CausalControllerSource:
    """Restore the exact build-free source needed by shifted recovery."""
    raw = attempt_payload.get("causal_source")
    if not isinstance(raw, Mapping):
        raise ValueError("archived controlling attempt lacks its causal source")
    return CausalControllerSource(
        attempt_id=str(raw["attempt_id"]),
        ordinal=int(raw["ordinal"]),
        role=str(raw["role"]),
        iteration=int(raw["iteration"]),
        global_interval_start=int(raw["global_interval_start"]),
        global_interval_stop=int(raw["global_interval_stop"]),
        outer_plan_id=str(raw["outer_plan_id"]),
        storage_device_ids=tuple(str(value) for value in raw["storage_device_ids"]),
        initial_soc_mwh={
            str(key): float(cast(float, value))
            for key, value in cast(
                Mapping[object, object], raw["initial_soc_mwh"]
            ).items()
        },
        first_soc_mwh=np.asarray(raw["first_soc_mwh"], dtype=float),
        first_b_mw=np.asarray(raw["first_b_mw"], dtype=float),
        solution_values={
            str(key): np.asarray(value, dtype=float)
            for key, value in cast(
                Mapping[object, object], raw["solution_values"]
            ).items()
        },
    )


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


def result_dimensions(inputs: HierarchicalInputs) -> dict[str, int]:
    """Return the trusted extracted-result dimensions for checkpoint validation."""
    return _result_dimensions(inputs)


def residual_tolerances(policy: HierarchicalPolicy) -> dict[str, float]:
    """Return the frozen acceptance tolerances used by archive validation."""
    return _residual_tolerances(policy)


def outer_boundaries(outer: StreamingOuterPlan) -> dict[int, dict[str, float]]:
    """Return the immutable outer SoC signposts keyed by global boundary."""
    return _outer_boundaries(outer)


def outer_plan_archive_payload(
    outer: StreamingOuterPlan,
    *,
    inputs: HierarchicalInputs,
    source_fingerprint: str,
    scenario_hash: str,
) -> dict[str, object]:
    """Project the retained outer plan into one build-free artifact."""
    outer.verify_signpost_integrity()
    if outer.build is None:
        raise ValueError("only a live outer plan can be archived")
    if outer.input_fingerprint != execution_input_sha256(inputs):
        raise ValueError("outer plan does not match its archival input snapshot")
    if not source_fingerprint.strip() or not scenario_hash.strip():
        raise ValueError("outer provenance hashes must be nonempty")
    if outer.accepted_primal:
        if outer.boundary_soc_mwh is None:
            raise ValueError("accepted outer plan lacks SoC signposts")
        extracted_soc = np.asarray(outer.result["soc"], dtype=float)
        if not np.array_equal(extracted_soc, outer.boundary_soc_mwh[1:]):
            raise ValueError("outer signposts differ from the retained outer result")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "iteration": 0,
        "outer_plan_id": outer.outer_plan_id,
        "source_fingerprint": source_fingerprint,
        "scenario_hash": scenario_hash,
        "input_fingerprint": outer.input_fingerprint,
        "horizon_steps": outer.horizon_steps,
        "delta_hours": outer.delta_hours,
        "storage_device_ids": list(outer.storage_device_ids),
        "policy_sha256": outer.policy_sha256,
        "solve_config_sha256": outer.solve_config_sha256,
        "temporal_assembly": outer.temporal_assembly,
        "canonicalization_backend": outer.canonicalization_backend,
        "signpost_sha256": outer.signpost_sha256,
        "global_boundary_indices": outer.global_boundary_indices.tolist(),
        "boundary_soc_mwh": (
            None if outer.boundary_soc_mwh is None else outer.boundary_soc_mwh.tolist()
        ),
        "structural_signature": _structural_signature(outer.build),
        "result": _json_value(outer.result, normalize_nonfinite_scalar=True),
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


def load_verified_outer_plan_archive(
    path: Path,
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    expected_solve_config_sha256: str,
    expected_source_fingerprint: str,
    expected_scenario_hash: str,
    expected_artifact: WindowIndexEntry | None = None,
) -> StreamingOuterPlan:
    """Load the immutable outer signposts needed by a resumed trajectory."""
    if expected_artifact is not None:
        if (
            path.stat().st_size != expected_artifact.bytes
            or sha256_path(path) != expected_artifact.sha256
        ):
            raise ValueError("outer-plan artifact integrity check failed")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        payload = cast(Mapping[str, object], json.load(stream))
    required = {
        "outer_plan_id",
        "source_fingerprint",
        "scenario_hash",
        "input_fingerprint",
        "horizon_steps",
        "delta_hours",
        "storage_device_ids",
        "policy_sha256",
        "solve_config_sha256",
        "signpost_sha256",
        "global_boundary_indices",
        "boundary_soc_mwh",
        "result",
        "audit",
    }
    if not required.issubset(payload):
        raise ValueError("outer-plan artifact is incomplete")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("outer-plan artifact schema mismatch")
    audit_payload = cast(Mapping[str, object], payload["audit"])
    required_audit = {
        "status",
        "missing_or_nonfinite_fields",
        "identity_error",
        "residuals",
        "accepted_primal",
        "exception",
        "wall_time_seconds",
    }
    if not required_audit.issubset(audit_payload):
        raise ValueError("outer-plan audit is incomplete")
    if (
        audit_payload["status"] not in {"optimal", "optimal_inaccurate"}
        or audit_payload["accepted_primal"] is not True
        or audit_payload["exception"] is not None
        or audit_payload["identity_error"] is not None
        or audit_payload["missing_or_nonfinite_fields"] != []
    ):
        raise ValueError("outer-plan audit is not an accepted primal")
    wall_time = audit_payload["wall_time_seconds"]
    if (
        not isinstance(wall_time, (int, float))
        or not np.isfinite(wall_time)
        or wall_time < 0
    ):
        raise ValueError("outer-plan audit wall time is invalid")
    residuals = cast(Mapping[str, object], audit_payload["residuals"])
    if not isinstance(residuals, Mapping) or any(
        not isinstance(value, (int, float)) or not np.isfinite(value) or value < 0
        for value in residuals.values()
    ):
        raise ValueError("outer-plan audit residuals are invalid")
    result = cast(Mapping[str, object], payload["result"])
    if not isinstance(result, Mapping):
        raise ValueError("outer-plan result must be a mapping")
    synthetic_build = cast(OPFBuild, SimpleNamespace(formulation="lossy_dc"))
    recomputed = audit_probe(
        inputs.case,
        synthetic_build,
        result,
        generators=inputs.generators,
        loads=inputs.loads,
        nondispatchable=inputs.nondispatchable,
        storage=inputs.storage,
        delta=inputs.delta,
        branch_limit_sentinel=inputs.options.branch_limit_sentinel,
        tolerances=policy.tolerances,
    )
    archived_residuals = {
        name: float(cast(float, value)) for name, value in residuals.items()
    }
    if (
        not recomputed.accepted_primal
        or recomputed.status != audit_payload["status"]
        or recomputed.missing_or_nonfinite_fields
        or recomputed.identity_error is not None
        or recomputed.residuals != archived_residuals
    ):
        raise ValueError("outer-plan audit does not reproduce from archived results")
    boundary = np.asarray(payload["boundary_soc_mwh"], dtype=float)
    result_soc = np.asarray(result.get("soc"), dtype=float)
    expected_initial = np.asarray(
        [unit.initial_soc for unit in inputs.storage], dtype=float
    )
    if (
        boundary.shape != (inputs.horizon_steps + 1, len(inputs.storage))
        or result_soc.shape != (inputs.horizon_steps, len(inputs.storage))
        or not np.all(np.isfinite(boundary))
        or not np.array_equal(boundary[0], expected_initial)
        or not np.array_equal(boundary[1:], result_soc)
    ):
        raise ValueError("outer-plan result and SoC signposts are inconsistent")
    outer = StreamingOuterPlan(
        outer_plan_id=str(payload["outer_plan_id"]),
        build=None,
        result=result,
        audit=ProbeAudit(
            status=cast(str | None, audit_payload["status"]),
            missing_or_nonfinite_fields=tuple(
                cast(Sequence[str], audit_payload["missing_or_nonfinite_fields"])
            ),
            identity_error=cast(str | None, audit_payload["identity_error"]),
            residuals={
                name: float(cast(float, value)) for name, value in residuals.items()
            },
            accepted_primal=bool(audit_payload["accepted_primal"]),
        ),
        exception=cast(str | None, audit_payload["exception"]),
        wall_time_seconds=float(cast(float, audit_payload["wall_time_seconds"])),
        storage_device_ids=tuple(cast(Sequence[str], payload["storage_device_ids"])),
        input_fingerprint=str(payload["input_fingerprint"]),
        horizon_steps=int(cast(int, payload["horizon_steps"])),
        delta_hours=float(cast(float, payload["delta_hours"])),
        policy_sha256=str(payload["policy_sha256"]),
        solve_config_sha256=str(payload["solve_config_sha256"]),
        temporal_assembly=cast(
            TemporalAssembly, payload.get("temporal_assembly", "stepwise")
        ),
        canonicalization_backend=str(payload.get("canonicalization_backend", "CPP")),
        signpost_sha256=str(payload["signpost_sha256"]),
        global_boundary_indices=np.asarray(
            payload["global_boundary_indices"], dtype=int
        ),
        boundary_soc_mwh=np.asarray(payload["boundary_soc_mwh"], dtype=float),
    )
    if outer.input_fingerprint != execution_input_sha256(inputs):
        raise ValueError("outer-plan artifact input fingerprint mismatch")
    if payload["source_fingerprint"] != expected_source_fingerprint:
        raise ValueError("outer-plan artifact source fingerprint mismatch")
    if payload["scenario_hash"] != expected_scenario_hash:
        raise ValueError("outer-plan artifact scenario hash mismatch")
    if outer.policy_sha256 != policy_sha256(policy):
        raise ValueError("outer-plan artifact policy mismatch")
    if outer.solve_config_sha256 != expected_solve_config_sha256:
        raise ValueError("outer-plan artifact solve configuration mismatch")
    if outer.horizon_steps != inputs.horizon_steps or outer.delta_hours != inputs.delta:
        raise ValueError("outer-plan artifact horizon mismatch")
    if outer.storage_device_ids != tuple(
        str(unit.device_id) for unit in inputs.storage
    ):
        raise ValueError("outer-plan artifact storage identity mismatch")
    return outer


def write_verified_outer_plan_archive(
    path: Path,
    outer: StreamingOuterPlan,
    *,
    inputs: HierarchicalInputs,
    source_fingerprint: str,
    scenario_hash: str,
) -> WindowIndexEntry:
    """Atomically persist and byte-verify the one retained outer plan."""
    payload = outer_plan_archive_payload(
        outer,
        inputs=inputs,
        source_fingerprint=source_fingerprint,
        scenario_hash=scenario_hash,
    )
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
    trajectory_start: int = 0,
    trajectory_stop: int | None = None,
    primary_attempt_budget_seconds: float | None = None,
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
        expected_horizon_steps=(
            inputs.horizon_steps if trajectory_stop is None else trajectory_stop
        ),
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=_result_dimensions(inputs),
        expected_delta_hours=inputs.delta,
        expected_outer_boundary_soc_mwh=_outer_boundaries(outer),
        expected_trajectory_start=trajectory_start,
        expected_primary_timeout_seconds=primary_attempt_budget_seconds,
    )
    return payload


def write_verified_window_archive(
    path: Path,
    payload: Mapping[str, object],
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: StreamingOuterPlan,
    trajectory_start: int = 0,
    trajectory_stop: int | None = None,
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
        expected_horizon_steps=(
            inputs.horizon_steps if trajectory_stop is None else trajectory_stop
        ),
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=_result_dimensions(inputs),
        expected_delta_hours=inputs.delta,
        expected_outer_boundary_soc_mwh=_outer_boundaries(outer),
        expected_trajectory_start=trajectory_start,
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
    advance_checkpoint: bool = True,
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
    if artifact_path.exists():
        encoded = json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
        generation = sha256(encoded).hexdigest()[:16]
        artifact_path = (
            directory / f"{prefix}-{window.iteration:06d}-{generation}.json.gz"
        )
    if artifact_path.exists():
        with gzip.open(artifact_path, "rt", encoding="utf-8") as stream:
            existing_payload = json.load(stream)
        if existing_payload != payload:
            raise FileExistsError(
                "window artifact generation contains different evidence"
            )
        entry = WindowIndexEntry(
            iteration=window.iteration,
            relative_path=artifact_path.name,
            bytes=artifact_path.stat().st_size,
            sha256=sha256_path(artifact_path),
        )
    else:
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
    if not advance_checkpoint:
        return PersistedWindow(entry, None, new_entries)
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
    "causal_source_from_archive",
    "load_verified_outer_plan_archive",
    "outer_boundaries",
    "outer_plan_archive_payload",
    "persist_window_transaction",
    "residual_tolerances",
    "result_dimensions",
    "window_archive_payload",
    "write_checkpoint_after_success",
    "write_verified_outer_plan_archive",
    "write_verified_window_archive",
]
