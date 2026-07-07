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
  storage.py  →  (no cvxopf imports)
  problem.py  →  storage.py   (imports StorageUnitIdeal, re-exports for public API)
  ac_problem.py → storage.py  (imports StorageUnitIdeal, _validate_storage,
                              _make_storage_incidence_matrix)
  dc_problem.py → storage.py  (imports StorageUnitIdeal, _validate_storage,
                              _make_storage_incidence_matrix)

No circularity. storage.py imports only numpy and standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
import warnings

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


# ---------------------------------------------------------------------------
# SoC dynamics constraint generation
# ---------------------------------------------------------------------------

def _make_storage_soc_constraints(
    b_list: list,
    soc_list: list,
    storage_initial_soc: np.ndarray,
    storage_delta: float,
    T: int,
    ns: int,
) -> list:
    """
    Generate SoC dynamics constraints linking adjacent time steps.

    Returns a list of CVXPY equality constraints:
      soc[0][s] == initial_soc[s] - b[0][s] * delta   for each s
      soc[t][s] == soc[t-1][s] - b[t][s] * delta      for t>=1, each s

    These are cross-step constraints and belong in the coupling
    constraints block, not in per-step constraints.

    Parameters
    ----------
    b_list : list of cp.Variable, length T, each shape (ns,)
    soc_list : list of cp.Variable, length T, each shape (ns,)
    storage_initial_soc : np.ndarray, shape (ns,)
    storage_delta : float
        Time step duration in hours (scalar, same for all units)
    T : int
    ns : int

    Returns
    -------
    list of cp.Constraint
    """
    constr = []
    for s in range(ns):
        constr.append(
            soc_list[0][s] == storage_initial_soc[s] - b_list[0][s] * storage_delta
        )
        for t in range(1, T):
            constr.append(
                soc_list[t][s] == soc_list[t - 1][s] - b_list[t][s] * storage_delta
            )
    return constr