"""First-class fixed-load model and future shedding-policy data.

Loads use engineering units at the device boundary.  Fixed active and
reactive withdrawals are scattered to buses with a negative injection sign;
the shared component assembler binds the component-created ``1/baseMVA``
parameter.  Optional shedding fields are part of the final public data model,
but their optimization semantics are activated in Milestone 19 Stage 4.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral, Real

import cvxpy as cp
import numpy as np


@dataclass
class Load:
    """One active/reactive load channel with optional future shedding policy.

    ``p_load_mw`` is signed net demand: positive values are withdrawal and
    negative values are fixed net injection.  ``q_load_mvar=None`` means that
    no reactive channel is defined; its numerical value is zero but this is
    distinguished from an explicitly configured zero by metadata.
    """

    bus: int
    p_load_mw: float
    device_id: str
    q_load_mvar: float | None = None
    shedding_cost_per_mwh: float | None = None
    max_shed_fraction: float = 1.0

    def __post_init__(self) -> None:
        """Validate fields whose meaning is independent of a network case."""
        _validate_load_fields(self)


def _finite_real(name: str, value: object, label: str) -> float:
    """Return one finite real value or raise a labeled validation error."""
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{label}: {name} must be a real scalar")
    numeric = float(value)
    if not np.isfinite(numeric):
        raise ValueError(f"{label}: {name} must be finite, got {value}")
    return numeric


def _validate_load_fields(unit: Load, *, label: str = "Load") -> None:
    """Validate one load without requiring a network bus table."""
    if isinstance(unit.bus, bool) or not isinstance(unit.bus, Integral):
        raise TypeError(f"{label}: bus must be an integer")
    if not isinstance(unit.device_id, str) or not unit.device_id:
        raise ValueError(f"{label}: device_id must be a nonempty string")

    _finite_real("p_load_mw", unit.p_load_mw, label)
    if unit.q_load_mvar is not None:
        _finite_real("q_load_mvar", unit.q_load_mvar, label)
    if unit.shedding_cost_per_mwh is not None:
        cost = _finite_real(
            "shedding_cost_per_mwh",
            unit.shedding_cost_per_mwh,
            label,
        )
        if cost <= 0:
            raise ValueError(
                f"{label}: shedding_cost_per_mwh must be > 0"
            )
    fraction = _finite_real(
        "max_shed_fraction", unit.max_shed_fraction, label
    )
    if not 0 < fraction <= 1:
        raise ValueError(
            f"{label}: max_shed_fraction must satisfy "
            f"0 < value <= 1, got {unit.max_shed_fraction}"
        )


def _validate_loads(loads: list[Load], ext_bus_ids: set[int]) -> None:
    """Validate load fields, identity, and external bus membership."""
    seen_ids: set[str] = set()
    for index, unit in enumerate(loads):
        label = f"Load {index}"
        _validate_load_fields(unit, label=label)
        if int(unit.bus) not in ext_bus_ids:
            raise ValueError(
                f"Load {index}: bus {unit.bus} not in case bus table. "
                f"Valid IDs: {sorted(ext_bus_ids)}"
            )
        if unit.device_id in seen_ids:
            raise ValueError(
                f"Load {index}: duplicate device_id {unit.device_id!r}"
            )
        seen_ids.add(unit.device_id)


def make_load_incidence(
    loads: list[Load], nb: int, ext_to_int: dict[int, int]
) -> np.ndarray:
    """Return the duplicate-safe bus/load incidence matrix ``(nb, nload)``."""
    incidence = np.zeros((nb, len(loads)), dtype=float)
    for index, unit in enumerate(loads):
        incidence[ext_to_int[int(unit.bus)], index] = 1.0
    return incidence


def _prepare_data(
    loads: list[Load],
    nb: int,
    ext_to_int: dict[int, int],
    ext_bus_ids: set[int],
) -> dict[str, object]:
    """Validate and vectorize formulation-independent static load data."""
    _validate_loads(loads, ext_bus_ids)
    p_load = np.asarray([unit.p_load_mw for unit in loads], dtype=float)
    q_load = np.asarray(
        [
            0.0 if unit.q_load_mvar is None else unit.q_load_mvar
            for unit in loads
        ],
        dtype=float,
    )
    return {
        "nload": len(loads),
        "nsheddable": sum(
            unit.shedding_cost_per_mwh is not None for unit in loads
        ),
        "Cload": make_load_incidence(loads, nb, ext_to_int),
        "load_device_ids": np.asarray(
            [unit.device_id for unit in loads], dtype=object
        ),
        "load_bus_external": np.asarray(
            [int(unit.bus) for unit in loads], dtype=int
        ),
        "load_bus_internal": np.asarray(
            [ext_to_int[int(unit.bus)] for unit in loads], dtype=int
        ),
        "load_p_mw": p_load,
        "load_q_mvar": q_load,
        "load_has_reactive": np.asarray(
            [unit.q_load_mvar is not None for unit in loads], dtype=bool
        ),
        "load_is_sheddable": np.asarray(
            [unit.shedding_cost_per_mwh is not None for unit in loads],
            dtype=bool,
        ),
        "sheddable_load_indices": np.flatnonzero(
            [unit.shedding_cost_per_mwh is not None for unit in loads]
        ).astype(int),
        "sheddable_load_device_ids": np.asarray(
            [
                unit.device_id
                for unit in loads
                if unit.shedding_cost_per_mwh is not None
            ],
            dtype=object,
        ),
        "load_max_shed_fraction": np.asarray(
            [unit.max_shed_fraction for unit in loads], dtype=float
        ),
        "load_shedding_cost_per_mwh": np.asarray(
            [
                np.nan
                if unit.shedding_cost_per_mwh is None
                else unit.shedding_cost_per_mwh
                for unit in loads
            ],
            dtype=float,
        ),
    }


def _build_metadata(prepared: dict[str, object]) -> dict[str, object]:
    """Return static load fields published through ``OPFBuild.data``."""
    return dict(prepared)


def ac_injections(
    p_load_mw: np.ndarray,
    q_load_mvar: np.ndarray,
    incidence: np.ndarray,
) -> tuple[cp.Expression, cp.Expression, cp.Parameter]:
    """Return fixed signed active/reactive nodal injections for AC."""
    inv_base_mva = cp.Parameter(nonneg=True, name="load_inv_base_mva")
    p_pu = cp.multiply(inv_base_mva, -(incidence @ p_load_mw))
    q_pu = cp.multiply(inv_base_mva, -(incidence @ q_load_mvar))
    return p_pu, q_pu, inv_base_mva


def dc_injections(
    p_load_mw: np.ndarray,
    incidence: np.ndarray,
) -> tuple[cp.Expression, None, cp.Parameter]:
    """Return fixed signed active nodal injection and no reactive channel."""
    inv_base_mva = cp.Parameter(nonneg=True, name="load_inv_base_mva")
    p_pu = cp.multiply(inv_base_mva, -(incidence @ p_load_mw))
    return p_pu, None, inv_base_mva


def fixed_expressions(
    p_load_mw: np.ndarray,
    q_load_mvar: np.ndarray,
    *,
    reactive_service: bool,
) -> dict[str, cp.Expression]:
    """Return fixed input and served-load reporting expressions."""
    p_load = cp.Constant(p_load_mw)
    q_load = cp.Constant(q_load_mvar)
    expressions: dict[str, cp.Expression] = {
        "p_load": p_load,
        "q_load": q_load,
        "p_load_served": p_load,
    }
    if reactive_service:
        expressions["q_load_served"] = q_load
    return expressions
