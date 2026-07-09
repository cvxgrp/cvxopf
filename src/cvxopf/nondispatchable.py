"""
Nondispatchable generator model for AC-OPF and DC-OPF.

Import chain:
  nondispatchable.py  →  (numpy and standard library only)
  problem.py          →  nondispatchable.py  (re-exports NondispatchableUnit)
  ac_problem.py       →  nondispatchable.py
  dc_problem.py       →  nondispatchable.py
"""

from __future__ import annotations
from dataclasses import dataclass
import numpy as np


@dataclass
class NondispatchableUnit:
    """
    Nondispatchable generator unit (wind turbine, PV array, run-of-river hydro).

    Attributes:
        bus: External (MATPOWER) bus ID where the unit is connected.
        p_available: Available real power (MW) for single-step. Must be >= 0.
        apparent_power_rating: Inverter nameplate rating P_max (MVA). Must be > 0.

    In single-step (`build_opf`), `p_available` is used directly as the available
    power bound. In multi-step (`build_opf_multistep`), `p_available` serves as
    a constant fallback if `df_nd` is not provided.
    """
    bus: int
    p_available: float
    apparent_power_rating: float


def _validate_nondispatchable(units, ext_bus_ids):
    """
    Validate nondispatchable units.

    Args:
        units: List of NondispatchableUnit instances.
        ext_bus_ids: Set of valid external bus IDs.

    Raises:
        ValueError: If any validation fails.
    """
    if not units:
        return

    for i, unit in enumerate(units):
        if unit.apparent_power_rating <= 0:
            raise ValueError(
                f"NondispatchableUnit at index {i}: apparent_power_rating must be > 0, "
                f"got {unit.apparent_power_rating}"
            )
        if unit.p_available < 0:
            raise ValueError(
                f"NondispatchableUnit at index {i}: p_available must be >= 0, "
                f"got {unit.p_available}"
            )
        if unit.bus not in ext_bus_ids:
            raise ValueError(
                f"NondispatchableUnit at index {i}: bus {unit.bus} not found in case. "
                f"Valid bus IDs: {sorted(ext_bus_ids)}"
            )


def _make_nd_incidence_matrix(units, nb, ext_to_int):
    """
    Create bus-unit incidence matrix Cnd.

    Args:
        units: List of NondispatchableUnit instances.
        nb: Number of buses.
        ext_to_int: Mapping from external to internal bus indices, or None.

    Returns:
        Cnd: (nb, nnd) numpy array where Cnd[i, n] = 1 if unit n is on bus i, else 0.
    """
    nnd = len(units)
    Cnd = np.zeros((nb, nnd), dtype=float)
    
    for n, unit in enumerate(units):
        bus_int = ext_to_int[unit.bus] if ext_to_int else unit.bus
        Cnd[bus_int, n] = 1.0
    
    return Cnd


def _parse_nd_timeseries(df_nd, T, ext_bus_ids, ext_to_int):
    """
    Parse and validate nondispatchable available power time series.

    Args:
        df_nd: DataFrame with shape (T, nnd), column names are external bus IDs.
        T: Number of time steps.
        ext_bus_ids: Set of valid external bus IDs.
        ext_to_int: Mapping from external to internal bus indices.

    Returns:
        nd_available: (T, nnd) numpy array of available power in MW.

    Raises:
        ValueError: If validation fails.
    """
    if df_nd is None:
        raise ValueError("df_nd cannot be None")

    # Validate shape
    if df_nd.shape[0] != T:
        raise ValueError(
            f"df_nd has {df_nd.shape[0]} rows but T={T}. "
            f"Expected {T} rows (one per time step)."
        )

    # Validate column names and create bus mapping
    nd_bus = []
    for col in df_nd.columns:
        try:
            ext_bus_id = int(col)
        except (ValueError, TypeError) as e:
            raise ValueError(
                f"Column name '{col}' cannot be cast to int. "
                f"All column names must be valid external bus IDs."
            ) from e
        
        if ext_bus_id not in ext_bus_ids:
            raise ValueError(
                f"Column name {ext_bus_id} is not a valid external bus ID. "
                f"Valid bus IDs: {sorted(ext_bus_ids)}"
            )
        
        nd_bus.append(ext_bus_id)

    # Validate all values are non-negative
    if (df_nd.values < 0).any():
        negative_mask = df_nd.values < 0
        negative_indices = np.where(negative_mask)
        raise ValueError(
            f"df_nd contains negative values at positions {list(zip(negative_indices[0].tolist(), negative_indices[1].tolist()))}. "
            f"All values must be >= 0."
        )

    # Convert to numpy array and ensure correct shape
    nd_available = df_nd.values.astype(float)
    if nd_available.shape != (T, len(nd_bus)):
        nd_available = nd_available.reshape(T, len(nd_bus))

    return nd_available