"""
Nondispatchable generator model for AC-OPF and DC-OPF.

Import chain:
  nondispatchable.py  →  data.py
  problem.py          →  nondispatchable.py  (re-exports NondispatchableUnit)
  ac_problem.py       →  nondispatchable.py
  dc_problem.py       →  nondispatchable.py
"""

from __future__ import annotations
from dataclasses import dataclass
import cvxpy as cp
import numpy as np

from cvxopf.data import align_device_dataframe


@dataclass
class NondispatchableUnit:
    """
    Nondispatchable generator unit (wind turbine, PV array, run-of-river hydro).

    Attributes:
        bus: External (MATPOWER) bus ID where the unit is connected.
        p_available: Available real power (MW) for single-step. Must be >= 0.
        apparent_power_rating: Inverter nameplate rating P_max (MVA). Must be > 0.
        device_id: Stable external identity used to align time-series columns.
            Required only when ``df_nd`` is supplied.

    In single-step (`build_opf`), `p_available` is used directly as the available
    power bound. In multi-step (`build_opf_multistep`), `p_available` serves as
    a constant fallback if `df_nd` is not provided.
    """
    bus: int
    p_available: float
    apparent_power_rating: float
    device_id: str | None = None


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
        numeric_fields = {
            "p_available": unit.p_available,
            "apparent_power_rating": unit.apparent_power_rating,
        }
        for name, value in numeric_fields.items():
            if not np.isfinite(value):
                raise ValueError(
                    f"NondispatchableUnit at index {i}: {name} must be finite, "
                    f"got {value}"
                )
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


def _parse_nd_timeseries(df_nd, T, units):
    """
    Parse and validate nondispatchable available power time series.

    Args:
        df_nd: DataFrame with shape (T, nnd), columns keyed by device ID.
        T: Number of time steps.
        units: NondispatchableUnit objects with stable device IDs.

    Returns:
        nd_available: (T, nnd) numpy array of available power in MW.

    Raises:
        ValueError: If validation fails.
    """
    if df_nd is None:
        raise ValueError("df_nd cannot be None")
    return align_device_dataframe(
        df_nd, units, T, "df_nd", nonnegative=True
    )


def _nd_static_data(units: list) -> dict:
    """Vectorize static nondispatchable-unit fields into numpy arrays."""
    return {
        "nd_apparent_power_rating": np.array(
            [unit.apparent_power_rating for unit in units], dtype=float
        ),
        "nd_p_available": np.array(
            [unit.p_available for unit in units], dtype=float
        ),
    }


def _prepare_data(
    units: list,
    nb: int,
    ext_to_int: dict,
    ext_bus_ids: set,
) -> dict:
    """Validate and prepare formulation-independent ND data."""
    _validate_nondispatchable(units, ext_bus_ids)
    return {
        "nnd": len(units),
        "Cnd": _make_nd_incidence_matrix(units, nb, ext_to_int),
        "nd_bus": np.array(
            [ext_to_int[unit.bus] for unit in units], dtype=int
        ),
        **_nd_static_data(units),
    }


def _build_metadata(prepared: dict) -> dict:
    """Select static ND fields published through ``OPFBuild.data``."""
    keys = ("nnd", "Cnd", "nd_bus", "nd_apparent_power_rating")
    return {key: prepared[key] for key in keys}


def _curtailment_values(available, dispatched):
    """Return available minus dispatched real power in MW."""
    return np.asarray(available) - np.asarray(dispatched)


def ac_injections(
    units: list,
    p_nd: cp.Variable,
    q_nd: cp.Variable,
    ext_to_int: dict | None,
    *,
    nb: int | None = None,
    incidence: np.ndarray | None = None,
) -> tuple:
    """Return coordinated real/reactive ND injections for an AC network."""
    if nb is None:
        nb = len(ext_to_int)
    Cnd = (
        _make_nd_incidence_matrix(units, nb, ext_to_int)
        if incidence is None
        else incidence
    )
    inv_baseMVA = cp.Parameter(nonneg=True, name="nd_inv_baseMVA")
    return (
        cp.multiply(inv_baseMVA, Cnd @ p_nd),
        cp.multiply(inv_baseMVA, Cnd @ q_nd),
        inv_baseMVA,
    )


def dc_injections(
    units: list,
    p_nd: cp.Variable,
    ext_to_int: dict | None,
    *,
    nb: int | None = None,
    incidence: np.ndarray | None = None,
) -> tuple:
    """Return real ND injection and no reactive channel for a DC network."""
    if nb is None:
        nb = len(ext_to_int)
    Cnd = (
        _make_nd_incidence_matrix(units, nb, ext_to_int)
        if incidence is None
        else incidence
    )
    inv_baseMVA = cp.Parameter(nonneg=True, name="nd_inv_baseMVA")
    return cp.multiply(inv_baseMVA, Cnd @ p_nd), None, inv_baseMVA


def ac_operating_constraints(
    units: list,
    p_nd: cp.Variable,
    q_nd: cp.Variable,
    p_available,
) -> list:
    """AC availability bounds and inverter apparent-power circles."""
    rating = _nd_static_data(units)["nd_apparent_power_rating"]
    constraints = [p_nd >= 0, p_nd <= p_available]
    constraints += [
        cp.sum_squares(cp.vstack([p_nd[n], q_nd[n]])) <= rating[n] ** 2
        for n in range(len(units))
    ]
    return constraints


def dc_operating_constraints(
    units: list,
    p_nd: cp.Variable,
    p_available,
) -> list:
    """DC real-power availability bounds."""
    return [p_nd >= 0, p_nd <= p_available]


def coupling_constraints(
    units: list,
    p_nd_list: list,
    q_nd_list: list | None = None,
    delta: float = 1.0,
) -> list:
    """ND units are memoryless under the current model."""
    return []
