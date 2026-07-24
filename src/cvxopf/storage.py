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
            lambda * sum_t |b_t|
        Penalises real power cycling to extend battery lifetime.
        Reactive power is not penalised.
        Default 1e-2. Set to 0.0 for zero-cost storage.
        Reference: Nnorom et al., "Aging-Aware Battery Control via Convex
        Optimization," Optimization and Engineering, 27:1303-1326, 2026.
    """
    bus:                   int
    apparent_power_rating: float
    capacity:              float
    initial_soc:           float
    aging_weight:          float = 1e-2


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_storage(
    storage_units: list,
    ext_bus_ids: set,
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
    - bus ID present in ext_bus_ids
    """
    if storage_units is None or len(storage_units) == 0:
        return
    
    for i, unit in enumerate(storage_units):
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
        
        # Check bus ID
        if unit.bus not in ext_bus_ids:
            raise ValueError(
                f"Storage unit {i}: bus {unit.bus} not found in case bus table. "
                f"Valid bus IDs: {sorted(ext_bus_ids)}"
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
    }


def _prepare_data(
    storage_units: list,
    nb: int,
    ext_to_int: dict,
    ext_bus_ids: set,
) -> dict:
    """Validate and prepare formulation-independent storage data."""
    _validate_storage(storage_units, ext_bus_ids)
    return {
        "ns": len(storage_units),
        "Cs": _make_storage_incidence_matrix(
            storage_units, nb, ext_to_int
        ),
        "storage_bus": np.array(
            [ext_to_int[unit.bus] for unit in storage_units], dtype=int
        ),
        **_storage_static_data(storage_units),
    }


def _build_metadata(prepared: dict) -> dict:
    """Select storage-owned fields published through ``OPFBuild.data``."""
    keys = (
        "ns",
        "Cs",
        "storage_bus",
        "storage_apparent_power_rating",
        "storage_capacity",
        "storage_initial_soc",
        "storage_delta",
        "storage_aging_weight",
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
) -> list:
    """AC inverter circle and per-step state-of-charge bounds."""
    data = _storage_static_data(storage_units)
    constraints = [
        cp.sum_squares(cp.vstack([b[s], b_q[s]]))
        <= data["storage_apparent_power_rating"][s] ** 2
        for s in range(len(storage_units))
    ]
    constraints += [
        soc >= 0.0,
        soc <= data["storage_capacity"],
    ]
    return constraints


def dc_operating_constraints(
    storage_units: list,
    b: cp.Variable,
    soc: cp.Variable,
) -> list:
    """DC real-power box and per-step state-of-charge bounds."""
    data = _storage_static_data(storage_units)
    rating = data["storage_apparent_power_rating"]
    return [
        b >= -rating,
        b <= rating,
        soc >= 0.0,
        soc <= data["storage_capacity"],
    ]


def coupling_constraints(
    storage_units: list,
    b_list: list,
    soc_list: list,
    delta: float,
) -> list:
    """Cross-step ideal-storage state-of-charge dynamics."""
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


def storage_cost_expr(storage_units: list, b: cp.Variable) -> cp.Expression:
    """Per-step L1 cycling cost; reactive power is intentionally unpenalized."""
    weights = _storage_static_data(storage_units)["storage_aging_weight"]
    return cp.sum(cp.multiply(weights, cp.abs(b)))
