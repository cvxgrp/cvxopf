"""Private, solver-free retained-energy handoff and first-action contracts.

Source disposition is provenance, not an acceptance override. No plan imports
an optimizer, constructs a model, or grants execution authority.
"""
from dataclasses import dataclass, replace
import math
import numpy as np


def _ids(value):
    value = tuple(value)
    if not value or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError("storage IDs must be nonempty explicit names")
    if len(set(value)) != len(value):
        raise ValueError("duplicate storage IDs")
    return value


def _array(value, shape):
    result = np.array(value, dtype=float, copy=True)
    if result.shape != shape or not np.isfinite(result).all():
        raise ValueError("nonfinite or misshaped energy/action array")
    # Bytes-backed ownership prevents re-enabling writes through NumPy flags,
    # as well as protecting against mutation through the caller's source array.
    return np.frombuffer(result.tobytes(), dtype=result.dtype).reshape(shape)


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")


@dataclass(frozen=True)
class RetainedEnergyPlan:
    plan_id: str
    source_formulation: str
    source_archive_sha256: str
    source_commit: str
    source_classification: str
    source_use_disposition: str
    source_use_record_sha256: str
    qualification_flags: tuple[str, ...]
    physical_input_sha256: str
    global_start: int
    interval_count: int
    delta_hours: float
    storage_device_ids: tuple[str, ...]
    boundary_soc_mwh: np.ndarray

    def __post_init__(self):
        _integer(self.global_start, "global start")
        _integer(self.interval_count, "interval count", 1)
        if isinstance(self.delta_hours, bool) or not math.isfinite(self.delta_hours) or self.delta_hours <= 0:
            raise ValueError("delta must be positive and finite")
        if not self.plan_id or self.source_formulation not in {"ac", "socp", "lossy_dc", "singlenode_dc"}:
            raise ValueError("invalid plan identity/formulation")
        if self.source_use_disposition not in {"accepted", "owner_approved_qualified"}:
            raise ValueError("unreviewed source disposition")
        for value, length in ((self.source_commit, 40), (self.source_archive_sha256, 64),
                              (self.source_use_record_sha256, 64), (self.physical_input_sha256, 64)):
            if len(value) != length or any(c not in "0123456789abcdef" for c in value):
                raise ValueError("invalid provenance digest/commit")
        ids = _ids(self.storage_device_ids)
        object.__setattr__(self, "storage_device_ids", ids)
        object.__setattr__(self, "qualification_flags", tuple(self.qualification_flags))
        object.__setattr__(self, "boundary_soc_mwh", _array(self.boundary_soc_mwh, (self.interval_count + 1, len(ids))))

    def aligned(self, ids, capacity, initial, final, *, box_tolerance, endpoint_tolerance):
        ids = _ids(ids)
        if set(ids) != set(self.storage_device_ids):
            raise ValueError("source and receiving storage identities differ")
        for tolerance in (box_tolerance, endpoint_tolerance):
            if not math.isfinite(tolerance) or tolerance < 0:
                raise ValueError("invalid source tolerance")
        order = [self.storage_device_ids.index(x) for x in ids]
        states = self.boundary_soc_mwh[:, order]
        capacity, initial, final = [_array(v, (len(ids),)) for v in (capacity, initial, final)]
        if (np.any(capacity <= 0) or np.any(states < -box_tolerance)
                or np.any(states > capacity + box_tolerance)
                or np.max(abs(states[0] - initial)) > endpoint_tolerance
                or np.max(abs(states[-1] - final)) > endpoint_tolerance):
            raise ValueError("retained source energy/boundary check failed; no repair allowed")
        return replace(self, storage_device_ids=ids, boundary_soc_mwh=states)


@dataclass(frozen=True)
class RolloutCursor:
    completed_intervals: int
    storage_device_ids: tuple[str, ...]
    realized_soc_mwh: np.ndarray
    preceding_controlling_attempt_id: str | None = None

    def __post_init__(self):
        _integer(self.completed_intervals, "completed intervals")
        ids = _ids(self.storage_device_ids)
        if (self.completed_intervals == 0) != (self.preceding_controlling_attempt_id is None):
            raise ValueError("cursor preceding-controller/count mismatch")
        object.__setattr__(self, "storage_device_ids", ids)
        object.__setattr__(self, "realized_soc_mwh", _array(self.realized_soc_mwh, (len(ids),)))


@dataclass(frozen=True)
class ACWindowRequest:
    arm_id: str
    plan_id: str
    local_start: int
    local_stop: int
    global_start: int
    global_stop: int
    storage_device_ids: tuple[str, ...]
    initial_soc_mwh: np.ndarray
    target_soc_mwh: np.ndarray
    source_endpoint_soc_mwh: np.ndarray
    target_kind: str

    def __post_init__(self):
        for key in ("local_start", "local_stop", "global_start", "global_stop"):
            _integer(getattr(self, key), key)
        if (not self.arm_id or not self.plan_id or not self.local_start < self.local_stop
                or self.global_stop - self.global_start != self.local_stop - self.local_start
                or self.target_kind not in {"retained_boundary", "original_final_obligation"}):
            raise ValueError("invalid window identity/coverage/target")
        ids = _ids(self.storage_device_ids)
        object.__setattr__(self, "storage_device_ids", ids)
        for key in ("initial_soc_mwh", "target_soc_mwh", "source_endpoint_soc_mwh"):
            object.__setattr__(self, key, _array(getattr(self, key), (len(ids),)))


def request_window(plan, cursor, original_final_soc, window_steps=3, *, arm_id):
    _integer(window_steps, "window steps", 1)
    if cursor.storage_device_ids != plan.storage_device_ids or cursor.completed_intervals >= plan.interval_count:
        raise ValueError("cursor identity/coverage differs from plan")
    k = cursor.completed_intervals
    e = min(k + window_steps, plan.interval_count)
    final = e == plan.interval_count
    return ACWindowRequest(arm_id, plan.plan_id, k, e, plan.global_start + k,
        plan.global_start + e, plan.storage_device_ids, cursor.realized_soc_mwh,
        original_final_soc if final else plan.boundary_soc_mwh[e], plan.boundary_soc_mwh[e],
        "original_final_obligation" if final else "retained_boundary")


def advance_cursor(cursor, request, *, attempt_id, accepted, hard_target, first_b_mw,
                   solved_next_soc_mwh, delta_hours, recurrence_tolerance):
    """Return reconstructed realized state only after accepted hard-target evidence.

    Durable publication and finalized supervision are the caller's prior gates.
    Solved and reconstructed states are retained separately by the experiment.
    """
    if (not accepted or not hard_target or not attempt_id
            or cursor.completed_intervals != request.local_start
            or cursor.storage_device_ids != request.storage_device_ids
            or not np.array_equal(cursor.realized_soc_mwh, request.initial_soc_mwh)):
        raise ValueError("unaccepted/noncontrolling or mismatched first action")
    if (isinstance(delta_hours, bool) or not math.isfinite(delta_hours) or delta_hours <= 0
            or isinstance(recurrence_tolerance, bool) or not math.isfinite(recurrence_tolerance) or recurrence_tolerance < 0):
        raise ValueError("invalid state-transition policy")
    shape = (len(cursor.storage_device_ids),)
    reconstructed = cursor.realized_soc_mwh - delta_hours * _array(first_b_mw, shape)
    if np.max(abs(reconstructed - _array(solved_next_soc_mwh, shape))) > recurrence_tolerance:
        raise ValueError("accepted first action disagrees with solved SoC")
    return RolloutCursor(request.local_start + 1, cursor.storage_device_ids,
                         reconstructed, attempt_id)
