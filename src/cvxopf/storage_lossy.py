"""
Lossy energy storage model for OPF formulations.

StorageUnitLossy is the ideal storage model (storage.py) with three changes:
charge/discharge efficiencies, proportional self-discharge, and throughput
regularization on separate charge and discharge powers. The approved model
is recorded in plans/milestone-18-lossy-storage.md, Section 11.

Per unit and interval, with retention r = (1 - self_discharge_per_hour)**delta:

    p_ch >= 0,  p_dis >= 0
    b = p_dis - p_ch                                (net grid injection, MW)
    soc[t+1] = r * soc[t] + delta * (eta_ch * p_ch[t] - p_dis[t] / eta_dis)

The operating set on b is the ideal one: b^2 + b_q^2 <= S^2 in AC/SOCP and
|b| <= S in DC. There is no charge/discharge mode constraint. The stage-cost
rate aging_weight * (p_ch + p_dis) regularizes throughput and penalizes
simultaneous charging and discharging.

Constraint, cost, and dynamics helpers accept per-step (ns,) or time-last
(ns, T) power arrays.

Import chain:
  storage_lossy.py  ->  storage.py, cvxpy, numpy
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import cvxpy as cp

from cvxopf import storage


# ---------------------------------------------------------------------------
# Lossy storage unit dataclass
# ---------------------------------------------------------------------------

@dataclass
class StorageUnitLossy:
    """
    Parameters for a single lossy energy storage device.

    Fields shared with StorageUnitIdeal (bus, apparent_power_rating,
    capacity, initial_soc, aging_weight, terminal_soc, terminal_constraint,
    terminal_cost, terminal_weight, device_id) have identical semantics,
    except that aging_weight must be strictly positive.

    Attributes
    ----------
    bus : int
        Bus ID in external (MATPOWER) numbering.
    apparent_power_rating : float
        Rating S (MVA) on net power b = p_dis - p_ch, as for
        StorageUnitIdeal: AC/SOCP b^2 + b_q^2 <= S^2, DC |b| <= S.
    capacity : float
        Energy capacity (MWh). Must be > 0.
    initial_soc : float
        Initial state of charge (MWh), 0 <= initial_soc <= capacity.
    charge_efficiency : float
        Fraction of charging power stored, 0 < eta_ch <= 1.
    discharge_efficiency : float
        Fraction of stored energy withdrawn that reaches the grid,
        0 < eta_dis <= 1.
    self_discharge_per_hour : float
        Fraction of stored energy lost per hour, 0 <= value < 1. The
        per-interval retention is (1 - self_discharge_per_hour) ** delta.
    aging_weight : float
        Weight lambda > 0 on the throughput rate lambda * (p_ch + p_dis), in
        objective units/MWh. Default 1e-2.
    terminal_soc, terminal_constraint, terminal_cost, terminal_weight
        Optional terminal policy; see StorageUnitIdeal.
    device_id : str | None
        Optional stable device identity. When omitted, the builder publishes
        a build-local positional label such as ``"lossy_storage_0"``.
    """
    bus:                     int
    apparent_power_rating:   float
    capacity:                float
    initial_soc:             float
    charge_efficiency:       float
    discharge_efficiency:    float
    self_discharge_per_hour: float
    aging_weight:            float = 1e-2
    terminal_soc:            float | None = None
    terminal_constraint:     str | None = None
    terminal_cost:           str | None = None
    terminal_weight:         float | None = None
    device_id:               str | None = None


# ---------------------------------------------------------------------------
# Validation and prepared data
# ---------------------------------------------------------------------------

def _validate_lossy_storage(storage_units: list, ext_bus_ids: set) -> None:
    """Validate shared storage fields, then the lossy-only fields."""
    storage._validate_storage(storage_units, ext_bus_ids)
    for i, unit in enumerate(storage_units):
        for name in ("charge_efficiency", "discharge_efficiency"):
            value = getattr(unit, name)
            if not 0 < value <= 1:
                raise ValueError(
                    f"Lossy storage unit {i}: {name} must satisfy "
                    f"0 < {name} <= 1, got {value}"
                )
        if not 0 <= unit.self_discharge_per_hour < 1:
            raise ValueError(
                f"Lossy storage unit {i}: self_discharge_per_hour must satisfy "
                f"0 <= self_discharge_per_hour < 1, got "
                f"{unit.self_discharge_per_hour}"
            )
        if unit.aging_weight <= 0:
            raise ValueError(
                f"Lossy storage unit {i}: aging_weight must be > 0 so that "
                "throughput regularization penalizes simultaneous charging "
                f"and discharging, got {unit.aging_weight}"
            )


def _device_identity(storage_units: list) -> tuple[np.ndarray, np.ndarray]:
    """Resolve aligned IDs and mark which identities were supplied explicitly."""
    used = {unit.device_id for unit in storage_units if unit.device_id is not None}
    resolved: list[str] = []
    explicit: list[bool] = []
    for index, unit in enumerate(storage_units):
        if unit.device_id is not None:
            resolved.append(unit.device_id)
            explicit.append(True)
            continue
        base = f"lossy_storage_{index}"
        candidate, suffix = base, 1
        while candidate in used:
            candidate = f"{base}_{suffix}"
            suffix += 1
        used.add(candidate)
        resolved.append(candidate)
        explicit.append(False)
    return np.asarray(resolved, dtype=object), np.asarray(explicit, dtype=bool)


def _static_data(storage_units: list) -> dict:
    """Vectorize static lossy-storage fields into numpy arrays."""
    def column(name: str) -> np.ndarray:
        return np.array([getattr(unit, name) for unit in storage_units], dtype=float)

    def optional_column(name: str) -> np.ndarray:
        return np.array(
            [
                np.nan if getattr(unit, name) is None else getattr(unit, name)
                for unit in storage_units
            ],
            dtype=float,
        )

    return {
        "ls_apparent_power_rating": column("apparent_power_rating"),
        "ls_capacity": column("capacity"),
        "ls_initial_soc": column("initial_soc"),
        "ls_charge_efficiency": column("charge_efficiency"),
        "ls_discharge_efficiency": column("discharge_efficiency"),
        "ls_self_discharge_per_hour": column("self_discharge_per_hour"),
        "ls_aging_weight": column("aging_weight"),
        "ls_terminal_soc": optional_column("terminal_soc"),
        "ls_terminal_constraint": np.array(
            [unit.terminal_constraint for unit in storage_units], dtype=object
        ),
        "ls_terminal_cost": np.array(
            [unit.terminal_cost for unit in storage_units], dtype=object
        ),
        "ls_terminal_weight": optional_column("terminal_weight"),
    }


def _retention(storage_units: list, delta: float) -> np.ndarray:
    """Fraction of stored energy retained over one interval of delta hours."""
    leak = _static_data(storage_units)["ls_self_discharge_per_hour"]
    return (1.0 - leak) ** float(delta)


def _prepare_data(
    storage_units: list,
    nb: int,
    ext_to_int: dict,
    ext_bus_ids: set,
    delta: float,
) -> dict:
    """Validate and prepare formulation-independent lossy-storage data."""
    _validate_lossy_storage(storage_units, ext_bus_ids)
    device_ids, device_id_is_explicit = _device_identity(storage_units)
    return {
        "n_ls": len(storage_units),
        "Cls": storage._make_storage_incidence_matrix(storage_units, nb, ext_to_int),
        "ls_bus": np.array(
            [ext_to_int[unit.bus] for unit in storage_units], dtype=int
        ),
        "ls_device_ids": device_ids,
        "ls_device_id_is_explicit": device_id_is_explicit,
        "ls_delta": float(delta),
        "ls_retention": _retention(storage_units, delta),
        **_static_data(storage_units),
    }


# ---------------------------------------------------------------------------
# Injections, operating set, dynamics, and cost
# ---------------------------------------------------------------------------

def _per_unit(values: np.ndarray, like) -> np.ndarray:
    """Align per-unit parameters with (ns,) or time-last (ns, T) arrays."""
    return values if like.ndim == 1 else values[:, np.newaxis]


def injections(
    p_ch: cp.Variable,
    p_dis: cp.Variable,
    b_q: cp.Variable | None,
    incidence: np.ndarray,
) -> tuple:
    """Return per-unit real and reactive injections; b_q is None in DC."""
    inv_baseMVA = cp.Parameter(nonneg=True, name="lossy_storage_inv_baseMVA")
    p_pu = cp.multiply(inv_baseMVA, incidence @ (p_dis - p_ch))
    q_pu = None if b_q is None else cp.multiply(inv_baseMVA, incidence @ b_q)
    return p_pu, q_pu, inv_baseMVA


def operating_constraints(
    storage_units: list,
    p_ch: cp.Variable,
    p_dis: cp.Variable,
    soc: cp.Expression,
    b_q: cp.Variable | None = None,
) -> list[cp.Constraint]:
    """Ideal-storage operating set on net power b = p_dis - p_ch.

    AC/SOCP: b^2 + b_q^2 <= S^2; DC: |b| <= S. Plus nonnegative split powers
    and SoC bounds. ``soc`` is the post-step energy: (ns,) per step or
    (ns, T) time-last.
    """
    data = _static_data(storage_units)
    rating = _per_unit(data["ls_apparent_power_rating"], p_ch)
    net = p_dis - p_ch
    constraints = [
        p_ch >= 0,
        p_dis >= 0,
        soc >= 0,
        soc <= _per_unit(data["ls_capacity"], soc),
    ]
    if b_q is None:
        constraints += [net >= -rating, net <= rating]
    else:
        constraints.append(cp.square(net) + cp.square(b_q) <= rating ** 2)
    return constraints


def _stored_energy_change(
    storage_units: list,
    p_ch: cp.Variable,
    p_dis: cp.Variable,
    delta: float,
) -> cp.Expression:
    """Energy added to storage over each interval by charging and discharging."""
    data = _static_data(storage_units)
    eta_ch = _per_unit(data["ls_charge_efficiency"], p_ch)
    eta_dis = _per_unit(data["ls_discharge_efficiency"], p_dis)
    return float(delta) * (
        cp.multiply(eta_ch, p_ch) - cp.multiply(1.0 / eta_dis, p_dis)
    )


def coupling_constraints(
    storage_units: list,
    p_ch_list: list,
    p_dis_list: list,
    soc_list: list,
    delta: float,
) -> list[cp.Constraint]:
    """Stepwise lossy SoC dynamics and terminal boundaries."""
    retention = _retention(storage_units, delta)
    previous = _static_data(storage_units)["ls_initial_soc"]
    constraints = []
    for p_ch, p_dis, soc in zip(p_ch_list, p_dis_list, soc_list):
        constraints.append(
            soc
            == cp.multiply(retention, previous)
            + _stored_energy_change(storage_units, p_ch, p_dis, delta)
        )
        previous = soc
    constraints += storage._terminal_soc_constraints(storage_units, soc_list[-1])
    return constraints


def vectorized_coupling_constraints(
    storage_units: list,
    p_ch: cp.Variable,
    p_dis: cp.Variable,
    soc: cp.Variable,
    delta: float,
) -> list[cp.Constraint]:
    """Time-last lossy recurrence over (ns, T + 1) SoC boundaries."""
    retention = _retention(storage_units, delta)[:, np.newaxis]
    constraints = [
        soc[:, 0] == _static_data(storage_units)["ls_initial_soc"],
        soc[:, 1:]
        == cp.multiply(retention, soc[:, :-1])
        + _stored_energy_change(storage_units, p_ch, p_dis, delta),
    ]
    constraints += storage._terminal_soc_constraints(storage_units, soc[:, -1])
    return constraints


def throughput_cost_rate(
    storage_units: list,
    p_ch: cp.Variable,
    p_dis: cp.Variable,
) -> cp.Expression:
    """Throughput regularization rate: scalar per step, or (T,) time-last."""
    weights = _per_unit(_static_data(storage_units)["ls_aging_weight"], p_ch)
    return cp.sum(cp.multiply(weights, p_ch + p_dis), axis=0)


# ---------------------------------------------------------------------------
# Reporting helpers (solved numpy values)
# ---------------------------------------------------------------------------

def _conversion_loss_values(
    p_ch: np.ndarray,
    p_dis: np.ndarray,
    charge_efficiency: np.ndarray,
    discharge_efficiency: np.ndarray,
    delta: float,
) -> np.ndarray:
    """Charge/discharge conversion loss per interval (MWh)."""
    return delta * (
        (1.0 - charge_efficiency) * p_ch
        + (1.0 / discharge_efficiency - 1.0) * p_dis
    )


def _self_discharge_values(
    initial_soc: np.ndarray,
    soc: np.ndarray,
    retention: np.ndarray,
) -> np.ndarray:
    """Self-discharge per interval (MWh) from start-of-interval energy.

    ``soc`` holds post-step values: (ns,) single-step or (T, ns) multistep.
    """
    soc = np.asarray(soc, dtype=float)
    start = (
        initial_soc
        if soc.ndim == 1
        else np.vstack([initial_soc[np.newaxis, :], soc[:-1]])
    )
    return (1.0 - retention) * start


def _split_operation_values(p_ch: np.ndarray, p_dis: np.ndarray) -> np.ndarray:
    """Simultaneous-operation diagnostic min(p_ch, p_dis) in MW."""
    return np.minimum(p_ch, p_dis)


# ---------------------------------------------------------------------------
# Simultaneous-operation check
# ---------------------------------------------------------------------------

# Engineering tolerance for material simultaneous operation (plan Section 5).
SPLIT_TOLERANCE_MW = 1e-3


class SimultaneousOperationError(RuntimeError):
    """Lossy storage charges and discharges simultaneously above tolerance.

    The complete extracted results are attached as ``results`` so the
    solution remains available for inspection.
    """

    def __init__(self, message: str, results: dict):
        super().__init__(message)
        self.results = results


def _check_simultaneous_operation(results: dict) -> None:
    """Raise if any unit's min(p_ch, p_dis) exceeds SPLIT_TOLERANCE_MW."""
    if results.get("ls_split") is None:
        return
    split = np.asarray(results["ls_split"], dtype=float)
    material = split > SPLIT_TOLERANCE_MW
    if not np.any(material):
        return
    index = np.unravel_index(np.argmax(split), split.shape)
    unit = str(results["ls_device_ids"][index[-1]])
    where = f"interval {index[0]}" if split.ndim == 2 else "the single step"
    raise SimultaneousOperationError(
        f"lossy storage charges and discharges simultaneously in "
        f"{int(np.sum(material))} unit-interval(s); the largest is unit "
        f"{unit!r} at {where}, min(p_ch, p_dis) = {split[index]:.6g} MW > "
        f"{SPLIT_TOLERANCE_MW:g} MW. Throughput regularization excludes this "
        "only under the assumptions of plans/milestone-18-lossy-storage.md, "
        "Section 4 (for example, curtailment headroom for surplus energy). "
        "The extracted results are attached as exc.results.",
        results,
    )
