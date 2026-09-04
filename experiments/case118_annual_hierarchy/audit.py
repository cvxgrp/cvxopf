"""Independent residual reconstruction for case118 S0/S1 probes."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Mapping, Sequence

import numpy as np

from cvxopf import (
    DispatchableGenerator,
    Load,
    NondispatchableUnit,
    OPFBuild,
    StorageUnitIdeal,
)
from cvxopf.hierarchical import HierarchicalAcceptanceTolerances


@dataclass(frozen=True)
class ProbeAudit:
    """Status, independent residuals, and fixed-gate classification."""

    status: str | None
    missing_or_nonfinite_fields: tuple[str, ...]
    identity_error: str | None
    residuals: Mapping[str, float]
    accepted_primal: bool


def _as_2d(values: object) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array.reshape(1, -1) if array.ndim == 1 else array


def _required_fields(formulation: str, *, has_nondispatchable: bool) -> tuple[str, ...]:
    if formulation == "ac":
        fields: tuple[str, ...] = (
            "objective",
            "b",
            "b_q",
            "soc",
            "Pg",
            "Qg",
            "Vm",
            "Va_deg",
            "p_net",
            "q_net",
            "branch_p_from",
            "branch_q_from",
            "branch_p_to",
            "branch_q_to",
            "branch_s_from",
            "branch_s_to",
            "p_load",
            "q_load",
            "p_load_served",
            "q_load_served",
        )
        return fields + (
            ("p_nd", "q_nd", "curtailment")
            if has_nondispatchable
            else ()
        )
    fields = (
        "objective",
        "b",
        "soc",
        "Pg",
        "p_net",
        "p_flows",
        "p_load",
        "q_load",
        "p_load_served",
    )
    return fields + (
        ("p_nd", "curtailment") if has_nondispatchable else ()
    )


def _device_injections(
    case: Mapping[str, object],
    generators: Sequence[DispatchableGenerator],
    loads: Sequence[Load],
    nondispatchable: Sequence[NondispatchableUnit],
    storage: Sequence[StorageUnitIdeal],
    result: Mapping[str, object],
    *,
    reactive: bool,
) -> tuple[np.ndarray, np.ndarray | None]:
    external = np.asarray(case["bus"], dtype=float)[:, 0].astype(int)
    bus_index = {bus: index for index, bus in enumerate(external)}
    p = np.zeros((_as_2d(result["Pg"]).shape[0], len(external)))
    q = np.zeros_like(p) if reactive else None
    for column, unit in enumerate(generators):
        p[:, bus_index[unit.bus]] += _as_2d(result["Pg"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(result["Qg"])[:, column]
    for column, unit in enumerate(storage):
        p[:, bus_index[unit.bus]] += _as_2d(result["b"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(result["b_q"])[:, column]
    for column, unit in enumerate(nondispatchable):
        p[:, bus_index[unit.bus]] += _as_2d(result["p_nd"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] += _as_2d(result["q_nd"])[:, column]
    for column, unit in enumerate(loads):
        p[:, bus_index[unit.bus]] -= _as_2d(result["p_load_served"])[:, column]
        if reactive and q is not None:
            q[:, bus_index[unit.bus]] -= _as_2d(
                result["q_load_served"]
            )[:, column]
    return p, q


def _soc_and_terminal_residuals(
    storage: Sequence[StorageUnitIdeal],
    result: Mapping[str, object],
    delta: float,
    *,
    include_terminal: bool,
) -> dict[str, float]:
    soc = _as_2d(result["soc"])
    power = _as_2d(result["b"])
    initial = np.array([unit.initial_soc for unit in storage], dtype=float)
    previous = np.vstack([initial, soc[:-1]])
    residuals = {
        "soc_recurrence_mwh_abs": float(
            np.max(np.abs(soc - previous + delta * power))
        )
    }
    if include_terminal:
        targets = np.array([unit.terminal_soc for unit in storage], dtype=float)
        residuals["terminal_soc_mwh_abs"] = float(
            np.max(np.abs(soc[-1] - targets))
        )
    return residuals


def _ac_residuals(
    case: Mapping[str, object],
    generators: Sequence[DispatchableGenerator],
    loads: Sequence[Load],
    nondispatchable: Sequence[NondispatchableUnit],
    storage: Sequence[StorageUnitIdeal],
    result: Mapping[str, object],
) -> dict[str, float]:
    p_device, q_device = _device_injections(
        case,
        generators,
        loads,
        nondispatchable,
        storage,
        result,
        reactive=True,
    )
    if q_device is None:
        raise RuntimeError("AC audit requires reactive injections")
    base_mva = float(np.asarray(case["baseMVA"]).item())
    bus = np.asarray(case["bus"], dtype=float)
    vm = _as_2d(result["Vm"])
    voltage = np.maximum.reduce(
        [vm - bus[:, 11], bus[:, 12] - vm, np.zeros_like(vm)]
    )
    branch = np.asarray(case["branch"], dtype=float)
    constrained = (branch[:, 10] == 1.0) & (branch[:, 5] > 0.0)
    if np.any(constrained):
        ratings = branch[constrained, 5]
        apparent = np.concatenate(
            [
                _as_2d(result["branch_s_from"])[:, constrained],
                _as_2d(result["branch_s_to"])[:, constrained],
            ],
            axis=1,
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
    else:
        thermal = 0.0
        normalized = 0.0
    branch_loss_by_step = np.sum(
        _as_2d(result["branch_p_from"])
        + _as_2d(result["branch_p_to"]),
        axis=1,
    )
    return {
        "ac_active_balance_pu_abs": float(
            np.max(np.abs((p_device - _as_2d(result["p_net"])) / base_mva))
        ),
        "ac_reactive_balance_pu_abs": float(
            np.max(np.abs((q_device - _as_2d(result["q_net"])) / base_mva))
        ),
        "voltage_bound_pu_abs": float(np.max(voltage)),
        "branch_mva_abs": thermal,
        "branch_normalized_squared_residual": normalized,
        "curtailment_nonnegativity_pu_abs": float(
            np.max(np.maximum(-_as_2d(result["curtailment"]), 0.0)) / base_mva
            if nondispatchable
            else 0.0
        ),
        "branch_loss_nonnegativity_pu_abs": float(
            np.max(np.maximum(-branch_loss_by_step, 0.0)) / base_mva
        ),
    }


def _dc_residuals(
    case: Mapping[str, object],
    generators: Sequence[DispatchableGenerator],
    loads: Sequence[Load],
    nondispatchable: Sequence[NondispatchableUnit],
    storage: Sequence[StorageUnitIdeal],
    result: Mapping[str, object],
    branch_limit_sentinel: float,
) -> dict[str, float]:
    p_device, _ = _device_injections(
        case,
        generators,
        loads,
        nondispatchable,
        storage,
        result,
        reactive=False,
    )
    external = np.asarray(case["bus"], dtype=float)[:, 0].astype(int)
    bus_index = {bus: index for index, bus in enumerate(external)}
    branch = np.asarray(case["branch"], dtype=float)
    incidence = np.zeros((len(external), len(branch)))
    for row, values in enumerate(branch):
        if values[10] == 0.0:
            continue
        incidence[bus_index[int(values[0])], row] = -1.0
        incidence[bus_index[int(values[1])], row] = 1.0
    p_net = _as_2d(result["p_net"])
    enforced_rating = np.where(branch[:, 5] > 0.0, branch[:, 5], branch_limit_sentinel)
    active = branch[:, 10] == 1.0
    branch_violation = float(
        np.max(
            np.maximum(
                np.abs(_as_2d(result["p_flows"])[:, active])
                - enforced_rating[active],
                0.0,
            )
        )
    )
    return {
        "dc_injection_reporting_mw_abs": float(
            np.max(np.abs(p_device - p_net))
        ),
        "dc_nodal_balance_pu_abs": float(
            np.max(
                np.abs(
                    (_as_2d(result["p_flows"]) @ incidence.T + p_net)
                    / float(np.asarray(case["baseMVA"]).item())
                )
            )
        ),
        "branch_mw_abs": branch_violation,
    }


def audit_probe(
    case: Mapping[str, object],
    build: OPFBuild,
    result: Mapping[str, object],
    *,
    generators: Sequence[DispatchableGenerator],
    loads: Sequence[Load],
    nondispatchable: Sequence[NondispatchableUnit],
    storage: Sequence[StorageUnitIdeal],
    delta: float = 1.0,
    branch_limit_sentinel: float = 1e6,
    tolerances: HierarchicalAcceptanceTolerances | None = None,
    include_terminal: bool = True,
) -> ProbeAudit:
    """Independently reconstruct one complete fixed-device probe audit."""
    required = _required_fields(
        build.formulation, has_nondispatchable=bool(nondispatchable)
    )
    missing = tuple(
        field
        for field in required
        if result.get(field) is None
        or not np.isfinite(np.asarray(result[field], dtype=float)).all()
    )
    expected_ids = tuple(str(unit.device_id) for unit in storage)
    raw_ids = result.get("storage_device_ids")
    actual_ids = (
        tuple(str(value) for value in np.asarray(raw_ids).tolist())
        if raw_ids is not None
        else ()
    )
    identity_error = None if actual_ids == expected_ids else "storage identity mismatch"
    residuals: dict[str, float] = {}
    if not missing and identity_error is None:
        residuals.update(
            _soc_and_terminal_residuals(
                storage, result, delta, include_terminal=include_terminal
            )
        )
        if build.formulation == "ac":
            residuals.update(
                _ac_residuals(
                    case,
                    generators,
                    loads,
                    nondispatchable,
                    storage,
                    result,
                )
            )
        else:
            residuals.update(
                _dc_residuals(
                    case,
                    generators,
                    loads,
                    nondispatchable,
                    storage,
                    result,
                    branch_limit_sentinel,
                )
            )
    limits = asdict(tolerances or HierarchicalAcceptanceTolerances())
    limits["branch_mw_abs"] = 1e-4
    limits["curtailment_nonnegativity_pu_abs"] = limits[
        "ac_active_balance_pu_abs"
    ]
    limits["branch_loss_nonnegativity_pu_abs"] = limits[
        "ac_active_balance_pu_abs"
    ]
    accepted = (
        result.get("status") in {"optimal", "optimal_inaccurate"}
        and not missing
        and identity_error is None
        and all(
            np.isfinite(value) and value <= limits[name]
            for name, value in residuals.items()
        )
    )
    return ProbeAudit(
        status=str(result.get("status")) if result.get("status") is not None else None,
        missing_or_nonfinite_fields=missing,
        identity_error=identity_error,
        residuals=residuals,
        accepted_primal=accepted,
    )
