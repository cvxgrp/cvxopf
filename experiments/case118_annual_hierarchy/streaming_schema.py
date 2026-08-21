"""Immutable-on-disk archive contract for the case118 streaming experiment."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import json
import os
from pathlib import Path
from pathlib import PurePosixPath
import tempfile
from typing import Mapping, Sequence, cast

import numpy as np


SCHEMA_VERSION = 1
ATTEMPT_ROLES = (
    "primary_controlling",
    "target_free",
    "copied_target_free",
    "perturbed_target_free",
    "perturbed_target_free",
    "perturbed_target_free",
    "perturbed_causal",
    "perturbed_causal",
    "perturbed_causal",
)
SLOT_STATES = {
    "executed",
    "construction_error",
    "source_unavailable",
    "not_needed_after_acceptance",
}
PERTURBATION_SCALES = (1e-4, 1e-3, 1e-2)
ACCEPTED_SOLVER_STATUSES = frozenset({"optimal", "optimal_inaccurate"})
CERTIFIED_INFEASIBLE_STATUSES = frozenset(
    {"infeasible", "infeasible_inaccurate"}
)
ATTEMPT_OUTCOMES = frozenset(
    {
        "accepted",
        "solver_certified_infeasible",
        "solver_failure",
        "unusable_primal",
    }
)
AC_COMMON_RESIDUAL_NAMES = frozenset(
    {
        "soc_recurrence_mwh_abs",
        "ac_active_balance_pu_abs",
        "ac_reactive_balance_pu_abs",
        "voltage_bound_pu_abs",
        "branch_mva_abs",
        "branch_normalized_squared_residual",
        "curtailment_nonnegativity_pu_abs",
        "branch_loss_nonnegativity_pu_abs",
    }
)
RESIDUAL_TOLERANCE_FIELDS = {
    "curtailment_nonnegativity_pu_abs": "ac_active_balance_pu_abs",
    "branch_loss_nonnegativity_pu_abs": "ac_active_balance_pu_abs",
}
REQUIRED_TOLERANCE_FIELDS = frozenset(
    (AC_COMMON_RESIDUAL_NAMES - set(RESIDUAL_TOLERANCE_FIELDS))
    | {"terminal_soc_mwh_abs"}
)
REQUIRED_RESULT_DIMENSIONS = frozenset(
    {
        "generators",
        "buses",
        "branches",
        "loads",
        "storage",
        "nondispatchable",
        "hvdc",
    }
)


def attempt_id(iteration: int, ordinal: int) -> str:
    """Return the public M17 attempt identity without translation."""
    return f"ac-{iteration:03d}-{ordinal:02d}-{ATTEMPT_ROLES[ordinal]}"


def perturbation_seed(iteration: int, ordinal: int) -> int:
    """Return the frozen target-free/causal perturbation seed."""
    if ordinal not in range(3, 9):
        raise ValueError("perturbation ordinal must be 3..8")
    source_code = 1 if ordinal < 6 else 2
    scale_index = ordinal - 2 if ordinal < 6 else ordinal - 5
    return 17_000_000 + 100 * iteration + 10 * source_code + scale_index


@dataclass(frozen=True)
class WindowIndexEntry:
    """One verified immutable window artifact in trajectory order."""

    iteration: int
    relative_path: str
    bytes: int
    sha256: str


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def atomic_gzip_json(path: Path, value: object) -> WindowIndexEntry:
    """Write deterministic gzip JSON and return its integrity entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as raw:
            with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as stream:
                stream.write(_json_bytes(value))
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
    iteration = _nonnegative_int(
        cast(Mapping[str, object], value).get("iteration"), "iteration"
    )
    return WindowIndexEntry(
        iteration=iteration,
        relative_path=path.name,
        bytes=path.stat().st_size,
        sha256=sha256_path(path),
    )


def atomic_json(path: Path, value: object) -> None:
    """Atomically write canonical uncompressed JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{name} must be a sequence")
    return value


def _nonnegative_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _finite_vector(value: object, name: str, size: int) -> np.ndarray:
    vector = np.asarray(value, dtype=float)
    if vector.shape != (size,) or not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must be a finite vector of shape ({size},)")
    return vector


def _finite_array(value: object, name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.size == 0 or not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must be a nonempty finite array")
    return array


def _finite_float(value: object, name: str, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite scalar")
    result = float(value)
    if not np.isfinite(result) or (positive and result <= 0.0):
        qualifier = "positive " if positive else ""
        raise ValueError(f"{name} must be a finite {qualifier}scalar")
    return result


def _validate_executed_evidence(
    attempt: Mapping[str, object],
    residual_tolerances: Mapping[str, float],
    result_shapes: Mapping[str, tuple[int, ...]],
) -> None:
    assigned = _mapping(attempt.get("assigned_start"), "assigned start")
    if not assigned:
        raise ValueError("executed slot requires a nonempty assigned start")
    assigned_arrays = {
        str(name): _finite_array(value, f"assigned start {name}")
        for name, value in assigned.items()
    }
    if any(not name.strip() for name in assigned_arrays):
        raise ValueError("assigned-start names must be nonempty strings")

    x0 = np.asarray(attempt.get("solver_x0"), dtype=float)
    if x0.ndim != 1 or x0.size == 0 or not np.all(np.isfinite(x0)):
        raise ValueError("solver_x0 must be a nonempty finite vector")
    layout = _sequence(attempt.get("solver_x0_layout"), "solver_x0_layout")
    evidence = _mapping(attempt.get("solver_evidence"), "solver evidence")
    model_count = _nonnegative_int(
        evidence.get("model_coordinate_count"), "model coordinate count"
    )
    auxiliary_count = _nonnegative_int(
        evidence.get("auxiliary_coordinate_count"), "auxiliary coordinate count"
    )
    if model_count == 0 or model_count + auxiliary_count != x0.size:
        raise ValueError("solver coordinate counts do not match solver_x0")

    offset = 0
    original_count = 0
    original_names: set[str] = set()
    normalized_layout: list[dict[str, object]] = []
    auxiliary_index = 0
    for index, raw_item in enumerate(layout):
        item = _mapping(raw_item, f"solver_x0_layout[{index}]")
        name = item.get("name")
        shape_raw = _sequence(item.get("shape"), f"layout[{index}].shape")
        shape = tuple(
            _nonnegative_int(value, f"layout[{index}].shape")
            for value in shape_raw
        )
        start = _nonnegative_int(item.get("start"), f"layout[{index}].start")
        stop = _nonnegative_int(item.get("stop"), f"layout[{index}].stop")
        original = item.get("is_original_variable")
        if not isinstance(name, str) or not name:
            raise ValueError("layout variable name must be nonempty")
        if not isinstance(original, bool):
            raise ValueError("layout is_original_variable must be Boolean")
        size = int(np.prod(shape, dtype=int)) if shape else 1
        if start != offset or stop != start + size:
            raise ValueError("solver_x0 layout must be contiguous and match shapes")
        if original:
            if name not in assigned_arrays or assigned_arrays[name].shape != shape:
                raise ValueError("original layout does not match assigned start")
            if name in original_names:
                raise ValueError("original layout variable names must be unique")
            original_names.add(name)
            original_count += size
            if not np.array_equal(
                x0[start:stop], assigned_arrays[name].flatten(order="F")
            ):
                raise ValueError("assigned start does not match solver_x0")
        label = name if original else f"auxiliary_{auxiliary_index}"
        auxiliary_index += int(not original)
        normalized_layout.append(
            {
                "label": label,
                "shape": list(shape),
                "start": start,
                "stop": stop,
                "is_original_variable": original,
            }
        )
        offset = stop
    if (
        offset != x0.size
        or original_count != model_count
        or original_names != set(assigned_arrays)
    ):
        raise ValueError("solver_x0 layout does not cover the assigned model start")
    signature = evidence.get("layout_signature")
    expected_signature = hashlib.sha256(
        json.dumps(
            normalized_layout, sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    if signature != expected_signature:
        raise ValueError("solver_x0 layout signature mismatch")
    before = _mapping(evidence.get("object_ids_before"), "object IDs before")
    after = _mapping(evidence.get("object_ids_after"), "object IDs after")
    if not before or before != after:
        raise ValueError("problem object identities must be retained across solve")
    for name in ("variables", "constraints", "parameters"):
        identifiers = _sequence(before.get(name), f"object IDs {name}")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in identifiers
        ):
            raise ValueError("object identity entries must be integers")

    signature_record = _mapping(
        attempt.get("structural_signature"), "structural signature"
    )
    structural_variables = _sequence(
        signature_record.get("variables"), "structural signature variables"
    )
    structural_names: list[str] = []
    for index, raw_variable in enumerate(structural_variables):
        variable = _mapping(raw_variable, f"structural variable {index}")
        name = variable.get("name")
        shape_raw = _sequence(variable.get("shape"), f"structural variable {index}.shape")
        shape = tuple(
            _nonnegative_int(value, f"structural variable {index}.shape")
            for value in shape_raw
        )
        if not isinstance(name, str) or not name:
            raise ValueError("structural variable name must be nonempty")
        if name not in assigned_arrays or assigned_arrays[name].shape != shape:
            raise ValueError("structural variables do not match assigned start")
        structural_names.append(name)
    if structural_names != list(assigned_arrays):
        raise ValueError("structural variable order does not match assigned start")
    constraints = _sequence(
        signature_record.get("constraints"), "structural signature constraints"
    )
    if not constraints or any(
        not isinstance(value, str) or not value for value in constraints
    ):
        raise ValueError("structural constraint signatures must be nonempty strings")
    parameters = _sequence(
        signature_record.get("parameters"), "structural signature parameters"
    )
    for index, raw_parameter in enumerate(parameters):
        parameter = _mapping(raw_parameter, f"structural parameter {index}")
        if not isinstance(parameter.get("name"), str) or not parameter["name"]:
            raise ValueError("structural parameter name must be nonempty")
        _sequence(parameter.get("shape"), f"structural parameter {index}.shape")

    audit = _mapping(attempt.get("audit"), "executed audit")
    required_audit = {
        "status",
        "outcome",
        "accepted_primal",
        "missing_or_nonfinite_fields",
        "identity_error",
        "residuals",
        "exception",
        "wall_time_seconds",
        "solver_num_iters",
        "solver_setup_time_seconds",
        "solver_solve_time_seconds",
    }
    if not required_audit.issubset(audit):
        raise ValueError("executed audit lacks required acceptance evidence")
    status = audit["status"]
    if status is not None and (not isinstance(status, str) or not status):
        raise ValueError("audit status must be a nonempty string or None")
    outcome = audit["outcome"]
    if outcome not in ATTEMPT_OUTCOMES:
        raise ValueError("audit outcome is unsupported")
    missing = _sequence(audit["missing_or_nonfinite_fields"], "missing fields")
    if any(not isinstance(value, str) or not value for value in missing):
        raise ValueError("missing-field names must be nonempty strings")
    if len(set(missing)) != len(missing):
        raise ValueError("missing-field names must be unique")
    identity_error = audit["identity_error"]
    if identity_error is not None and (
        not isinstance(identity_error, str) or not identity_error
    ):
        raise ValueError("identity error must be a nonempty string or None")
    exception = audit["exception"]
    if exception is not None and (
        not isinstance(exception, str) or not exception
    ):
        raise ValueError("audit exception must be a nonempty string or None")
    raw_residuals = _mapping(audit["residuals"], "audit residuals")
    residuals: dict[str, float] = {}
    for name, value in raw_residuals.items():
        residual = _finite_float(value, f"audit residual {name}")
        if residual < 0.0:
            raise ValueError("audit residuals must be nonnegative")
        residuals[str(name)] = residual
    wall_time = _finite_float(audit["wall_time_seconds"], "audit wall time")
    if wall_time < 0.0:
        raise ValueError("audit wall time must be nonnegative")
    accepted = audit["accepted_primal"]
    if not isinstance(accepted, bool):
        raise ValueError("audit accepted_primal must be Boolean")
    if accepted != (outcome == "accepted"):
        raise ValueError("audit accepted_primal must agree with outcome")
    if accepted and status not in ACCEPTED_SOLVER_STATUSES:
        raise ValueError("accepted primal requires an eligible solver status")
    if exception is not None and outcome != "solver_failure":
        raise ValueError("an exception requires solver_failure outcome")
    if exception is None and outcome == "solver_failure":
        raise ValueError("solver_failure outcome requires an exception")
    if exception is None and status in CERTIFIED_INFEASIBLE_STATUSES:
        if outcome != "solver_certified_infeasible":
            raise ValueError("infeasible status requires certified classification")
    elif outcome == "solver_certified_infeasible":
        raise ValueError("certified infeasibility requires an infeasible status")
    if (
        exception is None
        and status not in ACCEPTED_SOLVER_STATUSES
        and status not in CERTIFIED_INFEASIBLE_STATUSES
        and outcome != "unusable_primal"
    ):
        raise ValueError("remaining solver statuses require unusable_primal")
    if accepted and (missing or identity_error is not None or exception is not None):
        raise ValueError("accepted primal cannot retain failed acceptance gates")
    num_iters = audit["solver_num_iters"]
    invalid_num_iters = isinstance(num_iters, bool) or (
        num_iters is not None and not isinstance(num_iters, (int, str))
    )
    invalid_num_iters |= isinstance(num_iters, int) and num_iters < 0
    invalid_num_iters |= isinstance(num_iters, str) and not num_iters
    if invalid_num_iters:
        raise ValueError("solver_num_iters must be nonnegative, text, or None")
    for name in ("solver_setup_time_seconds", "solver_solve_time_seconds"):
        value = audit[name]
        if value is not None:
            timing = _finite_float(value, name)
            if timing < 0.0:
                raise ValueError(f"{name} must be nonnegative")

    role = attempt.get("role")
    terminal_policy = attempt.get("inner_terminal_policy")
    if terminal_policy != "hard_equality":
        raise ValueError("case118 attempt must use hard_equality terminal policy")
    required_residuals = set(AC_COMMON_RESIDUAL_NAMES)
    if role != "target_free":
        required_residuals.add("terminal_soc_mwh_abs")
    missing_residuals = sorted(required_residuals - set(residuals))
    excessive_residuals = {
        name: residuals[name]
        for name in required_residuals
        if name in residuals
        and residuals[name]
        > residual_tolerances[RESIDUAL_TOLERANCE_FIELDS.get(name, name)]
    }
    other_gate_failure = bool(missing or identity_error or exception)
    residual_gates_passed = (
        not missing_residuals and not excessive_residuals and not other_gate_failure
    )
    if status in ACCEPTED_SOLVER_STATUSES:
        if accepted and missing_residuals:
            raise ValueError(
                f"accepted attempt is missing required residuals {missing_residuals}"
            )
        if accepted and excessive_residuals:
            raise ValueError("accepted attempt exceeds frozen residual tolerances")
        if not accepted and residual_gates_passed:
            raise ValueError("attempt outcome does not match accepted residual gates")
    result = _mapping(attempt.get("result"), "executed result")
    required_result_keys = {"status", "objective", *result_shapes}
    missing_result_keys = sorted(required_result_keys - set(result))
    if missing_result_keys:
        raise ValueError(
            f"executed result is missing required keys {missing_result_keys}"
        )
    if result["status"] != status:
        raise ValueError("result status must match audit status")
    objective = result["objective"]
    if objective is not None:
        _finite_float(objective, "result objective")
    for name, shape in result_shapes.items():
        value = result[name]
        if value is None:
            if accepted:
                raise ValueError(f"accepted result field {name} cannot be None")
            continue
        array = np.asarray(value, dtype=float)
        if array.shape != shape or not np.all(np.isfinite(array)):
            raise ValueError(
                f"result field {name} must be finite with shape {shape}"
            )
    if accepted and objective is None:
        raise ValueError("accepted result objective cannot be None")


def validate_window_archive(
    payload: object,
    *,
    expected_soc_tolerance_mwh: float,
    expected_residual_tolerances: Mapping[str, float],
    expected_inner_terminal_policy: str,
    expected_horizon_steps: int,
    expected_ac_window_steps: int,
    expected_result_dimensions: Mapping[str, int],
    expected_delta_hours: float,
    expected_outer_boundary_soc_mwh: Mapping[int, Mapping[str, float]],
) -> Mapping[str, object]:
    """Validate a complete, build-free, single-window archive."""
    archive = _mapping(payload, "window archive")
    if archive.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported window archive schema")
    iteration = _nonnegative_int(archive.get("iteration"), "iteration")
    horizon = _nonnegative_int(expected_horizon_steps, "expected horizon")
    window = _nonnegative_int(expected_ac_window_steps, "expected AC window")
    if horizon == 0 or iteration >= horizon:
        raise ValueError("archive iteration must lie inside the expected horizon")
    if window != 3:
        raise ValueError("case118 experiment requires three-hour AC windows")
    if _nonnegative_int(archive.get("interval_start"), "interval_start") != iteration:
        raise ValueError("interval_start must equal iteration")
    stop = _nonnegative_int(archive.get("interval_stop"), "interval_stop")
    if stop != min(iteration + window, horizon):
        raise ValueError("interval_stop does not match frozen window geometry")
    raw_storage_ids = tuple(
        _sequence(archive.get("storage_device_ids"), "storage IDs")
    )
    if not raw_storage_ids or any(
        not isinstance(item, str) or not item.strip() for item in raw_storage_ids
    ):
        raise ValueError("storage IDs must be nonempty strings")
    storage_ids = cast(tuple[str, ...], raw_storage_ids)
    if len(set(storage_ids)) != len(storage_ids):
        raise ValueError("storage IDs must be unique")
    _finite_vector(archive.get("initial_soc_mwh"), "initial SoC", len(storage_ids))
    target_soc = _finite_vector(
        archive.get("target_soc_mwh"), "target SoC", len(storage_ids)
    )
    delta = _finite_float(
        archive.get("delta_hours"), "delta_hours", positive=True
    )
    expected_delta = _finite_float(
        expected_delta_hours, "expected delta_hours", positive=True
    )
    if delta != expected_delta:
        raise ValueError("archive delta_hours differs from frozen configuration")

    attempts = _sequence(archive.get("attempts"), "attempts")
    if len(attempts) != 9:
        raise ValueError("shifted recovery requires exactly nine archived slots")
    tolerance = _finite_float(
        expected_soc_tolerance_mwh, "expected SoC tolerance"
    )
    if tolerance < 0.0:
        raise ValueError("expected SoC tolerance must be nonnegative")
    archived_tolerance = _finite_float(
        archive.get("soc_tolerance_mwh"), "soc_tolerance_mwh"
    )
    if archived_tolerance != tolerance:
        raise ValueError("archive SoC tolerance differs from frozen policy")
    if expected_inner_terminal_policy != "hard_equality":
        raise ValueError("case118 experiment requires hard_equality terminal policy")
    if stop not in expected_outer_boundary_soc_mwh:
        raise ValueError("outer plan lacks the required window boundary")
    expected_target = expected_outer_boundary_soc_mwh[stop]
    if set(expected_target) != set(storage_ids):
        raise ValueError("outer target storage identities do not match archive")
    aligned_target = np.asarray(
        [expected_target[device_id] for device_id in storage_ids], dtype=float
    )
    if (
        aligned_target.shape != target_soc.shape
        or not np.all(np.isfinite(aligned_target))
        or np.max(np.abs(target_soc - aligned_target)) > tolerance
    ):
        raise ValueError("archive terminal target differs from frozen outer plan")
    tolerance_names = set(expected_residual_tolerances)
    if not REQUIRED_TOLERANCE_FIELDS.issubset(tolerance_names):
        raise ValueError("frozen residual tolerances are incomplete")
    residual_tolerances = {
        name: _finite_float(
            expected_residual_tolerances[name], f"residual tolerance {name}"
        )
        for name in REQUIRED_TOLERANCE_FIELDS
    }
    if any(value < 0.0 for value in residual_tolerances.values()):
        raise ValueError("residual tolerances must be nonnegative")
    if not REQUIRED_RESULT_DIMENSIONS.issubset(expected_result_dimensions):
        raise ValueError("expected AC result dimensions are incomplete")
    dimensions = {
        name: _nonnegative_int(expected_result_dimensions[name], f"result {name}")
        for name in REQUIRED_RESULT_DIMENSIONS
    }
    if any(dimensions[name] == 0 for name in ("generators", "buses", "storage")):
        raise ValueError("generator, bus, and storage dimensions must be positive")
    if dimensions["storage"] != len(storage_ids):
        raise ValueError("storage result dimension must match storage identities")
    steps = stop - iteration
    result_shapes = {
        "b": (steps, dimensions["storage"]),
        "b_q": (steps, dimensions["storage"]),
        "soc": (steps, dimensions["storage"]),
        "Pg": (steps, dimensions["generators"]),
        "Qg": (steps, dimensions["generators"]),
        "Vm": (steps, dimensions["buses"]),
        "Va_deg": (steps, dimensions["buses"]),
        "p_net": (steps, dimensions["buses"]),
        "q_net": (steps, dimensions["buses"]),
        "branch_p_from": (steps, dimensions["branches"]),
        "branch_q_from": (steps, dimensions["branches"]),
        "branch_p_to": (steps, dimensions["branches"]),
        "branch_q_to": (steps, dimensions["branches"]),
        "branch_s_from": (steps, dimensions["branches"]),
        "branch_s_to": (steps, dimensions["branches"]),
        "p_load": (steps, dimensions["loads"]),
        "q_load": (steps, dimensions["loads"]),
        "p_load_served": (steps, dimensions["loads"]),
        "q_load_served": (steps, dimensions["loads"]),
    }
    if dimensions["nondispatchable"]:
        result_shapes.update(
            {
                "p_nd": (steps, dimensions["nondispatchable"]),
                "q_nd": (steps, dimensions["nondispatchable"]),
                "curtailment": (steps, dimensions["nondispatchable"]),
            }
        )
    if dimensions["hvdc"]:
        result_shapes.update(
            {
                "p_hvdc_in": (steps, dimensions["hvdc"]),
                "p_hvdc_out": (steps, dimensions["hvdc"]),
                "hvdc_loss": (steps, dimensions["hvdc"]),
            }
        )
    preceding_id = archive.get("preceding_controlling_attempt_id")
    if iteration == 0:
        if preceding_id is not None:
            raise ValueError("iteration zero cannot have a preceding controller")
    elif not isinstance(preceding_id, str) or not preceding_id:
        raise ValueError("later window requires preceding controlling attempt ID")

    controlling_attempts: list[Mapping[str, object]] = []
    accepted_controlling_ordinals: list[int] = []
    target_free_accepted = False
    earlier_controller_accepted = False
    window_terminal_policy: object = None
    for ordinal, item in enumerate(attempts):
        attempt = _mapping(item, f"attempt {ordinal}")
        if attempt.get("ordinal") != ordinal:
            raise ValueError("attempt ordinals must be exactly 0..8")
        if attempt.get("role") != ATTEMPT_ROLES[ordinal]:
            raise ValueError("attempt role does not match frozen registry")
        if attempt.get("slot_state") not in SLOT_STATES:
            raise ValueError("unknown attempt slot state")
        if "build" in attempt:
            raise ValueError("streaming archive must not retain a live build")
        if attempt.get("attempt_id") != attempt_id(iteration, ordinal):
            raise ValueError("attempt ID does not match frozen registry")
        terminal_policy = attempt.get("inner_terminal_policy")
        if terminal_policy != expected_inner_terminal_policy:
            raise ValueError("attempt terminal policy differs from frozen policy")
        if ordinal == 0:
            window_terminal_policy = terminal_policy
        elif terminal_policy != window_terminal_policy:
            raise ValueError("attempt terminal policies must match within a window")
        expected_transformation = (
            ("flat" if iteration == 0 else "shifted_preceding")
            if ordinal < 2
            else "copy_target_free"
            if ordinal == 2
            else "perturb_target_free"
            if ordinal < 6
            else "perturb_causal"
        )
        if attempt.get("transformation") != expected_transformation:
            raise ValueError("attempt transformation does not match registry")
        expected_scale = (
            None
            if ordinal < 3
            else PERTURBATION_SCALES[(ordinal - 3) % 3]
        )
        if attempt.get("scale") != expected_scale:
            raise ValueError("attempt scale does not match registry")
        expected_seed = (
            None if ordinal < 3 else perturbation_seed(iteration, ordinal)
        )
        if attempt.get("seed") != expected_seed:
            raise ValueError("attempt seed does not match registry")
        state = attempt["slot_state"]
        source_available = ordinal not in {2, 3, 4, 5} or target_free_accepted
        if earlier_controller_accepted:
            expected_states = {"not_needed_after_acceptance"}
        elif source_available:
            expected_states = {"executed", "construction_error"}
        else:
            expected_states = {"source_unavailable"}
        if state not in expected_states:
            raise ValueError("attempt slot state violates frozen lifecycle")
        source_id = attempt.get("source_attempt_id")
        if state in {"not_needed_after_acceptance", "source_unavailable"}:
            expected_source_kind = None
            expected_source_id = None
        elif ordinal in {0, 1, 6, 7, 8} and iteration == 0:
            expected_source_kind = "generated_flat"
            expected_source_id = None
        elif ordinal in {0, 1, 6, 7, 8}:
            expected_source_kind = "attempt"
            expected_source_id = preceding_id
        else:
            expected_source_kind = "attempt"
            expected_source_id = attempt_id(iteration, 1)
        if attempt.get("source_kind") != expected_source_kind:
            raise ValueError("attempt source kind does not match frozen lifecycle")
        if source_id != expected_source_id:
            raise ValueError("attempt source ID does not match frozen lifecycle")
        solver_executed = attempt.get("solver_executed") is True
        supplied = attempt.get("supplied_executed_action") is True
        audit_value = attempt.get("audit")
        if state == "executed":
            if not solver_executed:
                raise ValueError("executed slot must record solver execution")
            _validate_executed_evidence(
                attempt, residual_tolerances, result_shapes
            )
        else:
            if solver_executed or supplied:
                raise ValueError("unexecuted slot cannot retain execution claims")
            if attempt.get("audit") is not None or attempt.get("result") is not None:
                raise ValueError("unexecuted slot cannot retain audit or result payload")
        if supplied:
            if attempt["role"] == "target_free":
                raise ValueError("target-free attempt cannot supply an action")
            audit = _mapping(attempt.get("audit"), "controlling audit")
            if audit.get("accepted_primal") is not True:
                raise ValueError("controlling attempt must have accepted audit")
            current_attempt_id = attempt.get("attempt_id")
            if not isinstance(current_attempt_id, str) or not current_attempt_id:
                raise ValueError("controlling attempt must have an ID")
            controlling_attempts.append(attempt)
        accepted = (
            state == "executed"
            and isinstance(audit_value, Mapping)
            and audit_value.get("accepted_primal") is True
        )
        if accepted and attempt["role"] != "target_free":
            accepted_controlling_ordinals.append(ordinal)
        if ordinal == 1:
            target_free_accepted = accepted
        if ordinal in {2, 3, 4, 5}:
            if state == "executed":
                if not target_free_accepted:
                    raise ValueError(
                        "target-free-derived attempt lacks accepted source"
                    )
                if source_id != attempt_id(iteration, 1):
                    raise ValueError("target-free-derived source ID mismatch")
        if accepted and attempt["role"] != "target_free":
            earlier_controller_accepted = True
    if accepted_controlling_ordinals:
        first_accepted = min(accepted_controlling_ordinals)
        if len(accepted_controlling_ordinals) != 1:
            raise ValueError("window cannot retain multiple accepted controllers")
        if len(controlling_attempts) != 1 or controlling_attempts[0][
            "ordinal"
        ] != first_accepted:
            raise ValueError("first accepted controller must supply the action")
        for later in attempts[first_accepted + 1 :]:
            if _mapping(later, "later attempt").get("slot_state") != (
                "not_needed_after_acceptance"
            ):
                raise ValueError("later slots must stop after accepted controller")
    elif any(
        _mapping(item, "attempt").get("slot_state")
        == "not_needed_after_acceptance"
        for item in attempts
    ):
        raise ValueError("not-needed slot requires an earlier accepted controller")
    executed = archive.get("executed_interval")
    if executed is None:
        if controlling_attempts:
            raise ValueError("failed window cannot supply an executed action")
    else:
        executed_record = _mapping(executed, "executed interval")
        if len(controlling_attempts) != 1:
            raise ValueError("executed window requires one controlling attempt")
        controlling = controlling_attempts[0]
        if executed_record.get("controlling_attempt_id") != controlling.get(
            "attempt_id"
        ):
            raise ValueError("executed interval controlling attempt mismatch")
        b_mw = _finite_vector(
            executed_record.get("b_mw"), "executed storage power", len(storage_ids)
        )
        initial = _finite_vector(
            archive.get("initial_soc_mwh"), "initial SoC", len(storage_ids)
        )
        post = _finite_vector(
            archive.get("post_step_soc_mwh"),
            "post-step SoC",
            len(storage_ids),
        )
        if np.max(np.abs(post - (initial - delta * b_mw))) > tolerance:
            raise ValueError("executed action and post-step SoC disagree")
    return archive


def checkpoint_payload(
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
    """Build and validate the trajectory resume checkpoint payload."""
    return validate_checkpoint(
        {
            "schema_version": SCHEMA_VERSION,
            "source_fingerprint": source_fingerprint,
            "scenario_hash": scenario_hash,
            "outer_plan_sha256": outer_plan_sha256,
            "policy_hash": policy_hash,
            "storage_device_ids": list(storage_device_ids),
            "initial_soc_mwh": list(initial_soc_mwh),
            "realized_soc_mwh": list(realized_soc_mwh),
            "completed_intervals": len(entries),
            "next_iteration": len(entries),
            "windows": [entry.__dict__ for entry in entries],
        }
    )


def validate_checkpoint(payload: object) -> dict[str, object]:
    """Reject ambiguous, reordered, or incomplete resume state."""
    checkpoint = dict(_mapping(payload, "checkpoint"))
    if checkpoint.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported checkpoint schema")
    for name in (
        "source_fingerprint",
        "scenario_hash",
        "outer_plan_sha256",
        "policy_hash",
    ):
        value = checkpoint.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonempty string")
    ids = tuple(_sequence(checkpoint.get("storage_device_ids"), "storage IDs"))
    if not ids or len(set(ids)) != len(ids):
        raise ValueError("checkpoint storage IDs must be unique and nonempty")
    _finite_vector(checkpoint.get("initial_soc_mwh"), "initial SoC", len(ids))
    _finite_vector(checkpoint.get("realized_soc_mwh"), "realized SoC", len(ids))
    completed = _nonnegative_int(
        checkpoint.get("completed_intervals"), "completed_intervals"
    )
    next_iteration = _nonnegative_int(
        checkpoint.get("next_iteration"), "next_iteration"
    )
    if next_iteration != completed:
        raise ValueError("next_iteration must equal completed_intervals")
    windows = _sequence(checkpoint.get("windows"), "windows")
    if len(windows) != completed:
        raise ValueError("window count must equal completed_intervals")
    paths: set[str] = set()
    for iteration, item in enumerate(windows):
        entry = _mapping(item, f"window index {iteration}")
        if entry.get("iteration") != iteration:
            raise ValueError("window index must be ordered and contiguous")
        relative_path = entry.get("relative_path")
        if not isinstance(relative_path, str) or not relative_path:
            raise ValueError("window relative_path must be a nonempty string")
        pure_path = PurePosixPath(relative_path)
        if pure_path.is_absolute() or any(
            part in {"", ".", ".."} for part in pure_path.parts
        ):
            raise ValueError("window relative_path must be normalized and relative")
        normalized = pure_path.as_posix()
        if normalized != relative_path:
            raise ValueError("window relative_path must use normalized POSIX form")
        if normalized in paths:
            raise ValueError("window relative_path must be unique")
        paths.add(normalized)
        _nonnegative_int(entry.get("bytes"), "window bytes")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("window sha256 must be a 64-character string")
    return checkpoint


def load_verified_checkpoint(
    path: Path,
    *,
    expected_source_fingerprint: str,
    expected_scenario_hash: str,
    expected_outer_plan_sha256: str,
    expected_policy_hash: str,
    expected_soc_tolerance_mwh: float,
    expected_residual_tolerances: Mapping[str, float],
    expected_inner_terminal_policy: str,
    expected_horizon_steps: int,
    expected_ac_window_steps: int,
    expected_result_dimensions: Mapping[str, int],
    expected_delta_hours: float,
    expected_outer_boundary_soc_mwh: Mapping[int, Mapping[str, float]],
) -> dict[str, object]:
    """Load a checkpoint and verify every referenced immutable window."""
    if expected_inner_terminal_policy != "hard_equality":
        raise ValueError("case118 experiment requires hard_equality terminal policy")
    checkpoint = validate_checkpoint(json.loads(path.read_text()))
    expected = {
        "source_fingerprint": expected_source_fingerprint,
        "scenario_hash": expected_scenario_hash,
        "outer_plan_sha256": expected_outer_plan_sha256,
        "policy_hash": expected_policy_hash,
    }
    for name, value in expected.items():
        if checkpoint[name] != value:
            raise ValueError(f"checkpoint {name} mismatch")
    expected_state = _finite_vector(
        checkpoint["initial_soc_mwh"],
        "checkpoint initial SoC",
        len(cast(Sequence[object], checkpoint["storage_device_ids"])),
    )
    checkpoint_ids = tuple(cast(Sequence[str], checkpoint["storage_device_ids"]))
    root = path.parent.resolve()
    preceding_controlling_id: str | None = None
    for item in cast(Sequence[Mapping[str, object]], checkpoint["windows"]):
        window_path = (path.parent / str(item["relative_path"])).resolve()
        if not window_path.is_relative_to(root):
            raise ValueError("checkpoint window escapes archive directory")
        if not window_path.is_file():
            raise ValueError("checkpoint window artifact is missing")
        if window_path.stat().st_size != item["bytes"]:
            raise ValueError("checkpoint window byte count mismatch")
        if sha256_path(window_path) != item["sha256"]:
            raise ValueError("checkpoint window hash mismatch")
        with gzip.open(window_path, "rt", encoding="utf-8") as stream:
            archive = validate_window_archive(
                json.load(stream),
                expected_soc_tolerance_mwh=expected_soc_tolerance_mwh,
                expected_residual_tolerances=expected_residual_tolerances,
                expected_inner_terminal_policy=expected_inner_terminal_policy,
                expected_horizon_steps=expected_horizon_steps,
                expected_ac_window_steps=expected_ac_window_steps,
                expected_result_dimensions=expected_result_dimensions,
                expected_delta_hours=expected_delta_hours,
                expected_outer_boundary_soc_mwh=expected_outer_boundary_soc_mwh,
            )
        if archive["iteration"] != item["iteration"]:
            raise ValueError("checkpoint/archive iteration mismatch")
        if archive["preceding_controlling_attempt_id"] != preceding_controlling_id:
            raise ValueError("archived controller identity chain is discontinuous")
        if tuple(cast(Sequence[str], archive["storage_device_ids"])) != checkpoint_ids:
            raise ValueError("checkpoint/archive storage identity mismatch")
        initial = _finite_vector(
            archive["initial_soc_mwh"], "archive initial SoC", len(checkpoint_ids)
        )
        tolerance = expected_soc_tolerance_mwh
        if np.max(np.abs(initial - expected_state)) > tolerance:
            raise ValueError("archived realized-state chain is discontinuous")
        if archive["executed_interval"] is None:
            raise ValueError("failed window cannot advance completed trajectory")
        executed_record = cast(Mapping[str, object], archive["executed_interval"])
        preceding_controlling_id = cast(
            str, executed_record["controlling_attempt_id"]
        )
        expected_state = _finite_vector(
            archive["post_step_soc_mwh"],
            "archive post-step SoC",
            len(checkpoint_ids),
        )
    final_state = _finite_vector(
        checkpoint["realized_soc_mwh"],
        "checkpoint realized SoC",
        len(checkpoint_ids),
    )
    tolerance = (
        0.0
        if not checkpoint["windows"]
        else expected_soc_tolerance_mwh
    )
    if np.max(np.abs(final_state - expected_state)) > tolerance:
        raise ValueError("checkpoint final realized SoC mismatch")
    return checkpoint


__all__ = [
    "ATTEMPT_ROLES",
    "PERTURBATION_SCALES",
    "SCHEMA_VERSION",
    "SLOT_STATES",
    "WindowIndexEntry",
    "attempt_id",
    "atomic_gzip_json",
    "atomic_json",
    "checkpoint_payload",
    "load_verified_checkpoint",
    "perturbation_seed",
    "sha256_path",
    "validate_checkpoint",
    "validate_window_archive",
]
