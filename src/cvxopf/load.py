"""First-class fixed and explicitly sheddable load model.

Loads use engineering units at the device boundary.  Fixed active and
reactive withdrawals are scattered to buses with a negative injection sign;
the shared component assembler binds the component-created ``1/baseMVA``
parameter. Optional shedding fields activate an affine interruption-fraction
feasible set and a linear value-of-lost-load stage cost.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral, Real

import cvxpy as cp
import numpy as np


BUS_I = 0
PD = 2
QD = 3


@dataclass
class _PreparedLoadParameters:
    """Synchronized exogenous load parameters for one nonempty horizon."""

    p_load_mw: cp.Parameter
    p_eligible_mw: cp.Parameter
    eligibility_mask: cp.Parameter
    q_load_mvar: cp.Parameter

    @classmethod
    def create(
        cls, p_load_mw: np.ndarray, q_load_mvar: np.ndarray
    ) -> "_PreparedLoadParameters":
        """Create parameters after validating and deriving active channels."""
        p_values, eligible, mask = _derive_active_channels(p_load_mw)
        q_values = np.asarray(q_load_mvar, dtype=float)
        if q_values.shape != p_values.shape:
            raise ValueError(
                "reactive load trajectory must match active load shape "
                f"{p_values.shape}, got {q_values.shape}"
            )
        if not np.all(np.isfinite(q_values)):
            raise ValueError("reactive load trajectory must be finite")
        shape = p_values.shape
        return cls(
            cp.Parameter(shape, value=p_values, name="load_p_mw"),
            cp.Parameter(
                shape, nonneg=True, value=eligible,
                name="load_p_eligible_mw",
            ),
            cp.Parameter(
                shape, nonneg=True, value=mask,
                name="load_eligibility_mask",
            ),
            cp.Parameter(shape, value=q_values, name="load_q_mvar"),
        )

    def update_active(self, p_load_mw: np.ndarray) -> None:
        """Atomically validate, derive, and assign all active-load channels."""
        p_values, eligible, mask = _derive_active_channels(
            p_load_mw, expected_shape=self.p_load_mw.shape
        )
        # All failure-prone validation occurs before the first assignment.
        self.p_load_mw.value = p_values
        self.p_eligible_mw.value = eligible
        self.eligibility_mask.value = mask


def _derive_active_channels(
    p_load_mw: np.ndarray,
    *,
    expected_shape: tuple[int, ...] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Validate signed demand and derive eligible demand and its mask."""
    values = np.asarray(p_load_mw, dtype=float)
    if expected_shape is not None and values.shape != expected_shape:
        raise ValueError(
            f"active load trajectory must have shape {expected_shape}, "
            f"got {values.shape}"
        )
    if values.ndim != 2:
        raise ValueError("active load trajectory must be two-dimensional")
    if not np.all(np.isfinite(values)):
        raise ValueError("active load trajectory must be finite")
    eligible = np.maximum(values, 0.0)
    mask = (values > 0.0).astype(float)
    return values.copy(), eligible, mask


@dataclass
class Load:
    """One active/reactive load channel with an optional shedding policy.

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


def loads_from_matpower(bus: np.ndarray) -> list[Load]:
    """Convert MATPOWER bus rows to one deterministic load per bus."""
    return [
        Load(
            bus=int(row[BUS_I]),
            p_load_mw=float(row[PD]),
            q_load_mvar=float(row[QD]),
            device_id=f"load_bus_{int(row[BUS_I])}",
        )
        for row in np.asarray(bus)
    ]


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
    keys = (
        "nload",
        "nsheddable",
        "Cload",
        "load_device_ids",
        "load_bus_external",
        "load_bus_internal",
        "load_has_reactive",
        "load_is_sheddable",
        "sheddable_load_indices",
        "sheddable_load_device_ids",
        "load_max_shed_fraction",
        "load_shedding_cost_per_mwh",
    )
    return {key: prepared[key] for key in keys}


def ac_injections(
    p_load_mw: cp.Expression | np.ndarray,
    q_load_mvar: cp.Expression | np.ndarray,
    incidence: np.ndarray,
) -> tuple[cp.Expression, cp.Expression, cp.Parameter]:
    """Return fixed signed active/reactive nodal injections for AC."""
    inv_base_mva = cp.Parameter(nonneg=True, name="load_inv_base_mva")
    p_pu = cp.multiply(inv_base_mva, -(incidence @ p_load_mw))
    q_pu = cp.multiply(inv_base_mva, -(incidence @ q_load_mvar))
    return p_pu, q_pu, inv_base_mva


def dc_injections(
    p_load_mw: cp.Expression | np.ndarray,
    incidence: np.ndarray,
) -> tuple[cp.Expression, None, cp.Parameter]:
    """Return fixed signed active nodal injection and no reactive channel."""
    inv_base_mva = cp.Parameter(nonneg=True, name="load_inv_base_mva")
    p_pu = cp.multiply(inv_base_mva, -(incidence @ p_load_mw))
    return p_pu, None, inv_base_mva


def shedding_constraints(
    fraction: cp.Variable,
    maximum_fraction: np.ndarray,
    eligibility_mask: cp.Expression,
) -> list[cp.Constraint]:
    """Return the explicit affine interruption-fraction feasible set."""
    upper = cp.multiply(maximum_fraction, eligibility_mask)
    return [fraction >= 0, fraction <= upper]


def ac_operating_constraints(
    fraction: cp.Variable | None = None,
    maximum_fraction: np.ndarray | None = None,
    eligibility_mask: cp.Expression | None = None,
) -> list[cp.Constraint]:
    """Return the AC load feasible set, empty when no load is sheddable."""
    if fraction is None:
        return []
    if maximum_fraction is None or eligibility_mask is None:
        raise ValueError(
            "sheddable load constraints require maximum_fraction and "
            "eligibility_mask"
        )
    return shedding_constraints(fraction, maximum_fraction, eligibility_mask)


def dc_operating_constraints(
    fraction: cp.Variable | None = None,
    maximum_fraction: np.ndarray | None = None,
    eligibility_mask: cp.Expression | None = None,
) -> list[cp.Constraint]:
    """Return the DC load feasible set under the same interruption policy."""
    return ac_operating_constraints(
        fraction, maximum_fraction, eligibility_mask
    )


def served_and_shed_expressions(
    p_load_mw: cp.Expression,
    q_load_mvar: cp.Expression,
    p_eligible_mw: cp.Expression,
    fraction: cp.Variable | None,
    sheddable_indices: np.ndarray,
    nload: int,
    *,
    interval_axis: int | None = None,
) -> dict[str, cp.Expression]:
    """Construct device-aligned served and conditional shedding channels.

    ``interval_axis`` identifies the time axis for a horizon expression.  The
    device axis remains first, so the same algebra owns scalar and time-last
    assembly while interval totals retain their time dimension.
    """
    expressions: dict[str, cp.Expression] = {
        "p_load": p_load_mw,
        "q_load": q_load_mvar,
    }
    if fraction is None:
        expressions["p_load_served"] = p_load_mw
        expressions["q_load_served"] = q_load_mvar
        return expressions

    nsheddable = len(sheddable_indices)
    scatter = np.zeros((nload, nsheddable), dtype=float)
    scatter[sheddable_indices, np.arange(nsheddable)] = 1.0
    p_shed = cp.multiply(fraction, p_eligible_mw[sheddable_indices])
    q_shed = cp.multiply(fraction, q_load_mvar[sheddable_indices])
    p_total = (
        cp.sum(p_shed)
        if interval_axis is None
        else cp.sum(p_shed, axis=1 - interval_axis)
    )
    expressions.update(
        {
            "p_load_shed": p_shed,
            "q_load_shed": q_shed,
            "load_shed_fraction": fraction,
            "p_load_shed_total": p_total,
            "p_load_served": p_load_mw - scatter @ p_shed,
            "q_load_served": q_load_mvar - scatter @ q_shed,
        }
    )
    return expressions


def shedding_cost_rate(
    p_load_shed: cp.Expression,
    cost_per_mwh: np.ndarray,
    *,
    interval_axis: int | None = None,
) -> cp.Expression:
    """Return the linear value-of-lost-load stage-cost rate."""
    weighted = cp.multiply(cost_per_mwh, p_load_shed)
    return (
        cp.sum(weighted)
        if interval_axis is None
        else cp.sum(weighted, axis=1 - interval_axis)
    )


def coupling_constraints() -> list[cp.Constraint]:
    """Return the empty fixed-load temporal contribution."""
    return []


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
