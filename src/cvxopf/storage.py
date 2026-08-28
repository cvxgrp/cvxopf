"""
Energy storage system model for AC-OPF and DC-OPF.

This module contains the StorageUnitIdeal dataclass and validation/helpers
for integrating lossless energy storage devices into OPF problems.

Storage devices are bus-connected inverters with:
- Real and reactive power variables (AC) or real power only (DC)
- State-of-charge dynamics with initial condition
- Apparent power operating set (circle in P-Q plane for AC; real power bound for DC)
- L1 aging penalty on real power cycling in the objective

Import chain:
  storage.py  →  cvxpy, numpy, stdlib (no cvxopf imports)
  problem.py  →  storage.py   (imports StorageUnitIdeal, re-exports for public API)
  ac_problem.py → storage.py  (imports StorageUnitIdeal, _validate_storage,
                              _make_storage_incidence_matrix)
  dc_problem.py → storage.py  (imports StorageUnitIdeal, _validate_storage,
                              _make_storage_incidence_matrix)

No circularity.
"""

from __future__ import annotations

from dataclasses import dataclass
from numbers import Integral

import numpy as np
import cvxpy as cp


# ---------------------------------------------------------------------------
# Storage unit dataclass
# ---------------------------------------------------------------------------

@dataclass
class StorageUnitIdeal:
    """
    Parameters for a single lossless (ideal) energy storage device.

    The operating set is a circle in real-reactive power space (AC) or
    a symmetric real power bound (DC). Charge and discharge efficiency
    are assumed to be 1.0. For lossy storage, see StorageUnitLossy
    (future milestone).

    The time step duration delta is a property of the problem's time
    discretisation, not of the storage device. Pass it via the delta=
    parameter of build_opf / build_opf_multistep (default 1.0 hours).

    Attributes
    ----------
    bus : int
        Bus ID in external (MATPOWER) numbering. Remapped to internal
        0-based index during problem construction via ext_to_int.
    apparent_power_rating : float
        Apparent power rating S_max (MVA). Defines the operating set:
          AC: b_t^2 + b_q_t^2 <= S_max^2  (circle in P-Q plane)
          DC: |b_t| <= S_max               (real power bound only,
              UserWarning emitted)
        Must be > 0.
    capacity : float
        Energy capacity Q (MWh). Must be > 0.
    initial_soc : float
        Initial state of charge q_start (MWh). Must satisfy
        0 <= initial_soc <= capacity.
    aging_weight : float
        Weight lambda on the L1 battery cycling penalty in the objective:
            delta * lambda * sum_t |b_t|
        Penalises real power cycling to extend battery lifetime.
        Reactive power is not penalised.
        Units are objective units/MWh of one-way throughput.
        Default 1e-2. Set to 0.0 for zero-cost storage.
        Reference: Nnorom et al., "Aging-Aware Battery Control via Convex
        Optimization," Optimization and Engineering, 27:1303-1326, 2026.
    terminal_soc : float | None
        Optional terminal state-of-charge target in MWh. Required when either
        ``terminal_constraint`` or ``terminal_cost`` is configured; otherwise
        must be None. Must lie in [0, capacity].
    terminal_constraint : {None, "equality", "shortfall"}
        Optional hard terminal policy. ``"equality"`` enforces
        ``soc[-1] == terminal_soc``. ``"shortfall"`` enforces zero terminal
        shortfall, equivalently ``soc[-1] >= terminal_soc``.
    terminal_cost : {None, "linear", "quadratic", "shortfall_linear",
        "shortfall_quadratic"}
        Optional soft terminal policy. Linear and quadratic modes penalize
        two-sided deviation from ``terminal_soc``. Shortfall modes penalize
        only ``cp.neg(soc[-1] - terminal_soc)``. Mutually exclusive with
        ``terminal_constraint``.
    terminal_weight : float | None
        Positive terminal-cost weight. Required exactly when
        ``terminal_cost`` is configured. Linear weights have objective
        units/MWh; quadratic weights have objective units/MWh^2.
    device_id : str | None
        Optional stable device identity. Explicit nonempty IDs are suitable
        for alignment across independently built problems. When omitted, the
        builder publishes a collision-safe positional label such as
        ``"storage_0"``; that label is local to the build and is not a claim
        of stable cross-build identity.
    connection_window : tuple[int, int] | None
        Optional half-open interval ``[arrival_step, departure_step)`` during
        which the device is connected. Real and reactive power are fixed to
        zero outside the interval. ``None`` means connected for the full
        horizon; an empty interval means disconnected for the full horizon.
    bidirectional : bool
        Whether positive real-power export is permitted while connected.
        Set to ``False`` for charge-only V1G fleets and ``True`` for V2G or
        ordinary storage. Reactive support remains available while connected
        in AC formulations.
    """
    bus:                   int
    apparent_power_rating: float
    capacity:              float
    initial_soc:           float
    aging_weight:          float = 1e-2
    terminal_soc:          float | None = None
    terminal_constraint:   str | None = None
    terminal_cost:         str | None = None
    terminal_weight:       float | None = None
    device_id:             str | None = None
    connection_window:     tuple[int, int] | None = None
    bidirectional:         bool = True


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_connection_window(
    unit: StorageUnitIdeal,
    index: int,
    *,
    horizon_steps: int | None = None,
) -> None:
    """Validate one unit's connection window."""
    if unit.connection_window is None:
        return
    window = unit.connection_window
    if not isinstance(window, tuple) or len(window) != 2:
        raise TypeError(
            f"Storage unit {index}: connection_window must be a "
            "(arrival_step, departure_step) tuple"
        )
    arrival, departure = window
    if any(
        isinstance(step, bool) or not isinstance(step, Integral)
        for step in window
    ):
        raise TypeError(
            f"Storage unit {index}: connection_window steps must be integers"
        )
    if arrival < 0 or arrival > departure:
        raise ValueError(
            f"Storage unit {index}: connection_window must satisfy "
            "0 <= arrival_step <= departure_step"
        )
    if horizon_steps is not None and departure > horizon_steps:
        raise ValueError(
            f"Storage unit {index}: connection_window departure_step "
            f"{departure} exceeds horizon length {horizon_steps}"
        )


def _validate_storage(
    storage_units: list,
    ext_bus_ids: set,
    *,
    horizon_steps: int | None = None,
) -> None:
    """
    Validate a list of StorageUnitIdeal objects.

    Parameters
    ----------
    storage_units : list[StorageUnitIdeal]
    ext_bus_ids : set of int
        Set of valid external bus IDs from the case bus table.

    Raises
    ------
    ValueError
        If any unit fails validation.

    Checks:
    - apparent_power_rating > 0
    - capacity > 0
    - 0 <= initial_soc <= capacity
    - aging_weight >= 0
    - terminal policy fields form one valid hard or soft configuration
    - connection_window is a valid half-open interval within the horizon
    - bus ID present in ext_bus_ids
    """
    if storage_units is None or len(storage_units) == 0:
        return

    explicit_ids: set[str] = set()
    for i, unit in enumerate(storage_units):
        _validate_connection_window(
            unit, i, horizon_steps=horizon_steps
        )
        if unit.device_id is not None:
            if (
                not isinstance(unit.device_id, str)
                or not unit.device_id.strip()
            ):
                raise ValueError(
                    f"Storage unit {i}: device_id must be a nonempty string "
                    "when supplied"
                )
            if unit.device_id in explicit_ids:
                raise ValueError(
                    f"Storage unit {i}: duplicate device_id "
                    f"{unit.device_id!r}"
                )
            explicit_ids.add(unit.device_id)

        numeric_fields = {
            "apparent_power_rating": unit.apparent_power_rating,
            "capacity": unit.capacity,
            "initial_soc": unit.initial_soc,
            "aging_weight": unit.aging_weight,
        }
        for name, value in numeric_fields.items():
            if not np.isfinite(value):
                raise ValueError(
                    f"Storage unit {i}: {name} must be finite, got {value}"
                )
        # Check apparent_power_rating
        if unit.apparent_power_rating <= 0:
            raise ValueError(
                f"Storage unit {i}: apparent_power_rating must be > 0, "
                f"got {unit.apparent_power_rating}"
            )
        # Check capacity
        if unit.capacity <= 0:
            raise ValueError(
                f"Storage unit {i}: capacity must be > 0, got {unit.capacity}"
            )
        
        # Check initial_soc bounds
        if unit.initial_soc < 0:
            raise ValueError(
                f"Storage unit {i}: initial_soc must be >= 0, got {unit.initial_soc}"
            )
        if unit.initial_soc > unit.capacity:
            raise ValueError(
                f"Storage unit {i}: initial_soc must be <= capacity, "
                f"got {unit.initial_soc} > {unit.capacity}"
            )
        
        # Check aging_weight
        if unit.aging_weight < 0:
            raise ValueError(
                f"Storage unit {i}: aging_weight must be >= 0, got {unit.aging_weight}"
            )

        valid_constraints = {None, "equality", "shortfall"}
        valid_costs = {
            None,
            "linear",
            "quadratic",
            "shortfall_linear",
            "shortfall_quadratic",
        }
        if unit.terminal_constraint not in valid_constraints:
            raise ValueError(
                f"Storage unit {i}: terminal_constraint must be one of "
                f"{sorted(value for value in valid_constraints if value is not None)} "
                f"or None, got {unit.terminal_constraint!r}"
            )
        if unit.terminal_cost not in valid_costs:
            raise ValueError(
                f"Storage unit {i}: terminal_cost must be one of "
                f"{sorted(value for value in valid_costs if value is not None)} "
                f"or None, got {unit.terminal_cost!r}"
            )
        if (
            unit.terminal_constraint is not None
            and unit.terminal_cost is not None
        ):
            raise ValueError(
                f"Storage unit {i}: terminal_constraint and terminal_cost "
                "are alternatives and cannot both be configured"
            )

        terminal_active = (
            unit.terminal_constraint is not None
            or unit.terminal_cost is not None
        )
        if terminal_active:
            if unit.terminal_soc is None or not np.isfinite(unit.terminal_soc):
                raise ValueError(
                    f"Storage unit {i}: terminal_soc must be finite when a "
                    "terminal policy is configured"
                )
            if not 0 <= unit.terminal_soc <= unit.capacity:
                raise ValueError(
                    f"Storage unit {i}: terminal_soc must satisfy "
                    f"0 <= terminal_soc <= capacity, got {unit.terminal_soc}"
                )
        elif unit.terminal_soc is not None:
            raise ValueError(
                f"Storage unit {i}: terminal_soc requires "
                "terminal_constraint or terminal_cost"
            )

        if unit.terminal_cost is not None:
            if (
                unit.terminal_weight is None
                or not np.isfinite(unit.terminal_weight)
                or unit.terminal_weight <= 0
            ):
                raise ValueError(
                    f"Storage unit {i}: terminal_weight must be finite and > 0 "
                    "when terminal_cost is configured"
                )
        elif unit.terminal_weight is not None:
            raise ValueError(
                f"Storage unit {i}: terminal_weight requires terminal_cost"
            )
        
        # Check bus ID
        if unit.bus not in ext_bus_ids:
            raise ValueError(
                f"Storage unit {i}: bus {unit.bus} not found in case bus table. "
                f"Valid bus IDs: {sorted(ext_bus_ids)}"
            )


def _storage_device_identity(
    storage_units: list[StorageUnitIdeal],
) -> tuple[np.ndarray, np.ndarray]:
    """Resolve aligned IDs and mark which identities were supplied explicitly.

    Generated labels are deterministic only for the current ordered fleet.
    They are collision-safe convenience labels, not stable device identity.
    """
    reserved = {
        unit.device_id
        for unit in storage_units
        if unit.device_id is not None
    }
    used = set(reserved)
    resolved: list[str] = []
    explicit: list[bool] = []
    for index, unit in enumerate(storage_units):
        if unit.device_id is not None:
            resolved.append(unit.device_id)
            explicit.append(True)
            continue

        base = f"storage_{index}"
        candidate = base
        suffix = 1
        while candidate in used:
            candidate = f"{base}_legacy_{suffix}"
            suffix += 1
        used.add(candidate)
        resolved.append(candidate)
        explicit.append(False)

    return (
        np.asarray(resolved, dtype=object),
        np.asarray(explicit, dtype=bool),
    )


# ---------------------------------------------------------------------------
# Incidence matrix construction
# ---------------------------------------------------------------------------

def _make_storage_incidence_matrix(
    storage_units: list,
    nb: int,
    ext_to_int: dict | None,
) -> np.ndarray:
    """
    Build the (nb, ns) storage-to-bus incidence matrix Cs.

    Cs[i, s] = 1.0 if storage unit s is connected to internal bus i.

    Bus IDs in storage_units are in external (MATPOWER) numbering and
    are remapped via ext_to_int. If ext_to_int is None, bus IDs are
    assumed to be 0-based consecutive.

    Parameters
    ----------
    storage_units : list[StorageUnitIdeal]
    nb : int
        Number of buses (internal).
    ext_to_int : dict | None

    Returns
    -------
    Cs : np.ndarray, shape (nb, ns)
    """
    if storage_units is None or len(storage_units) == 0:
        return np.empty((nb, 0))  # (nb, 0) matrix for zero storage units
    
    ns = len(storage_units)
    Cs = np.zeros((nb, ns))
    
    for s, unit in enumerate(storage_units):
        if ext_to_int is not None:
            internal_bus = ext_to_int[unit.bus]
        else:
            internal_bus = unit.bus
        Cs[internal_bus, s] = 1.0
    
    return Cs


def _storage_static_data(storage_units: list) -> dict:
    """Vectorize static storage fields into numpy arrays."""
    return {
        "storage_apparent_power_rating": np.array(
            [unit.apparent_power_rating for unit in storage_units], dtype=float
        ),
        "storage_capacity": np.array(
            [unit.capacity for unit in storage_units], dtype=float
        ),
        "storage_initial_soc": np.array(
            [unit.initial_soc for unit in storage_units], dtype=float
        ),
        "storage_aging_weight": np.array(
            [unit.aging_weight for unit in storage_units], dtype=float
        ),
        "storage_terminal_soc": np.array(
            [
                np.nan if unit.terminal_soc is None else unit.terminal_soc
                for unit in storage_units
            ],
            dtype=float,
        ),
        "storage_terminal_constraint": np.array(
            [unit.terminal_constraint for unit in storage_units], dtype=object
        ),
        "storage_terminal_cost": np.array(
            [unit.terminal_cost for unit in storage_units], dtype=object
        ),
        "storage_terminal_weight": np.array(
            [
                np.nan if unit.terminal_weight is None else unit.terminal_weight
                for unit in storage_units
            ],
            dtype=float,
        ),
    }


def _prepare_data(
    storage_units: list,
    nb: int,
    ext_to_int: dict,
    ext_bus_ids: set,
    *,
    horizon_steps: int | None = None,
) -> dict:
    """Validate and prepare formulation-independent storage data."""
    _validate_storage(
        storage_units, ext_bus_ids, horizon_steps=horizon_steps
    )
    device_ids, device_id_is_explicit = _storage_device_identity(
        storage_units
    )
    return {
        "ns": len(storage_units),
        "Cs": _make_storage_incidence_matrix(
            storage_units, nb, ext_to_int
        ),
        "storage_bus": np.array(
            [ext_to_int[unit.bus] for unit in storage_units], dtype=int
        ),
        "storage_device_ids": device_ids,
        "storage_device_id_is_explicit": device_id_is_explicit,
        **_storage_static_data(storage_units),
    }


def _build_metadata(prepared: dict) -> dict:
    """Select storage-owned fields published through ``OPFBuild.data``."""
    keys = (
        "ns",
        "Cs",
        "storage_bus",
        "storage_device_ids",
        "storage_device_id_is_explicit",
        "storage_apparent_power_rating",
        "storage_capacity",
        "storage_initial_soc",
        "storage_delta",
        "storage_aging_weight",
        "storage_terminal_soc",
        "storage_terminal_constraint",
        "storage_terminal_cost",
        "storage_terminal_weight",
    )
    return {key: prepared[key] for key in keys}


def ac_injections(
    storage_units: list,
    b: cp.Variable,
    b_q: cp.Variable,
    ext_to_int: dict,
    *,
    nb: int | None = None,
    incidence: np.ndarray | None = None,
) -> tuple:
    """Return coordinated real/reactive storage injections for an AC network."""
    if nb is None:
        nb = len(ext_to_int)
    Cs = (
        _make_storage_incidence_matrix(storage_units, nb, ext_to_int)
        if incidence is None
        else incidence
    )
    inv_baseMVA = cp.Parameter(nonneg=True, name="storage_inv_baseMVA")
    return (
        cp.multiply(inv_baseMVA, Cs @ b),
        cp.multiply(inv_baseMVA, Cs @ b_q),
        inv_baseMVA,
    )


def dc_injections(
    storage_units: list,
    b: cp.Variable,
    ext_to_int: dict,
    *,
    nb: int | None = None,
    incidence: np.ndarray | None = None,
) -> tuple:
    """Return real storage injection and no reactive channel for a DC network."""
    if nb is None:
        nb = len(ext_to_int)
    Cs = (
        _make_storage_incidence_matrix(storage_units, nb, ext_to_int)
        if incidence is None
        else incidence
    )
    inv_baseMVA = cp.Parameter(nonneg=True, name="storage_inv_baseMVA")
    return cp.multiply(inv_baseMVA, Cs @ b), None, inv_baseMVA


def ac_operating_constraints(
    storage_units: list,
    b: cp.Variable,
    b_q: cp.Variable,
    soc: cp.Variable,
    *,
    step: int = 0,
) -> list:
    """AC inverter circle and per-step state-of-charge bounds."""
    data = _storage_static_data(storage_units)
    rating = data["storage_apparent_power_rating"]
    connected, bidirectional = _connection_masks(storage_units, step)
    constraints = [
        cp.sum_squares(cp.vstack([b[s], b_q[s]]))
        <= rating[s] ** 2
        for s in range(len(storage_units))
    ]
    constraints += [
        b >= -rating * connected,
        b <= rating * connected * bidirectional,
        b_q >= -rating * connected,
        b_q <= rating * connected,
        soc >= 0.0,
        soc <= data["storage_capacity"],
    ]
    return constraints


def dc_operating_constraints(
    storage_units: list,
    b: cp.Variable,
    soc: cp.Variable,
    *,
    step: int = 0,
) -> list:
    """DC real-power box and per-step state-of-charge bounds."""
    data = _storage_static_data(storage_units)
    rating = data["storage_apparent_power_rating"]
    connected, bidirectional = _connection_masks(storage_units, step)
    return [
        b >= -rating * connected,
        b <= rating * connected * bidirectional,
        soc >= 0.0,
        soc <= data["storage_capacity"],
    ]


def _connection_masks(
    storage_units: list, step: int
) -> tuple[np.ndarray, np.ndarray]:
    """Return connected and bidirectional multipliers for one step."""
    connected = [
        unit.connection_window is None
        or unit.connection_window[0] <= step < unit.connection_window[1]
        for unit in storage_units
    ]
    return np.asarray(connected, dtype=float), np.asarray(
        [unit.bidirectional for unit in storage_units], dtype=float
    )


def _soc_dynamics_constraints(
    storage_units: list,
    b_list: list,
    soc_list: list,
    delta: float,
) -> list:
    """Initial transition and cross-step ideal-storage SoC dynamics."""
    initial_soc = _storage_static_data(storage_units)["storage_initial_soc"]
    constraints = []
    for s in range(len(storage_units)):
        constraints.append(
            soc_list[0][s] == initial_soc[s] - b_list[0][s] * float(delta)
        )
        for t in range(1, len(b_list)):
            constraints.append(
                soc_list[t][s]
                == soc_list[t - 1][s] - b_list[t][s] * float(delta)
            )
    return constraints


def _terminal_soc_constraints(
    storage_units: list,
    terminal_soc: cp.Variable,
) -> list:
    """Hard terminal boundary conditions for the configured storage units."""
    constraints = []
    for s, unit in enumerate(storage_units):
        if unit.terminal_constraint == "equality":
            constraints.append(terminal_soc[s] == unit.terminal_soc)
        elif unit.terminal_constraint == "shortfall":
            constraints.append(terminal_soc[s] >= unit.terminal_soc)
    return constraints


def coupling_constraints(
    storage_units: list,
    b_list: list,
    soc_list: list,
    delta: float,
) -> list:
    """Storage horizon constraints: SoC dynamics and terminal boundaries."""
    constraints = _soc_dynamics_constraints(
        storage_units, b_list, soc_list, delta
    )
    constraints += _terminal_soc_constraints(storage_units, soc_list[-1])
    return constraints


def storage_cost_expr(storage_units: list, b: cp.Variable) -> cp.Expression:
    """L1 cycling cost rate; integration is owned by shared assembly."""
    weights = _storage_static_data(storage_units)["storage_aging_weight"]
    return cp.sum(cp.multiply(weights, cp.abs(b)))


def terminal_cost_expr(
    storage_units: list,
    terminal_soc: cp.Variable,
) -> cp.Expression | None:
    """Collection-level soft terminal cost, or None when no cost is active."""
    terms = []
    for s, unit in enumerate(storage_units):
        if unit.terminal_cost is None:
            continue
        deviation = terminal_soc[s] - unit.terminal_soc
        if unit.terminal_cost == "linear":
            penalty = cp.abs(deviation)
        elif unit.terminal_cost == "quadratic":
            penalty = cp.square(deviation)
        elif unit.terminal_cost == "shortfall_linear":
            penalty = cp.neg(deviation)
        else:
            penalty = cp.square(cp.neg(deviation))
        terms.append(unit.terminal_weight * penalty)
    return cp.sum(terms) if terms else None


def _terminal_deviation_values(
    targets: np.ndarray,
    terminal_soc: np.ndarray,
) -> np.ndarray:
    """Return signed terminal deviations, with NaN for inactive policies."""
    targets = np.asarray(targets, dtype=float)
    terminal_soc = np.asarray(terminal_soc, dtype=float)
    return np.where(
        np.isfinite(targets),
        terminal_soc - targets,
        np.nan,
    )
