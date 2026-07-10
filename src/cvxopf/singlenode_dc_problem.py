"""
Single-node DC dispatch problem construction helpers.

This module implements the single-node (copper-plate) optimal dispatch
formulation: the network is collapsed to a single bus, branch flows and
transmission constraints are ignored, and the only physical law enforced
is real power balance.

This is a convex QP; the default solver is CLARABEL (nlp=False).

Formulation
-----------
Variables:
    Pg       (ng,)  per-generator real power output, p.u., nonneg
    b        (ns,)  storage real power, MW, positive = discharging
                    (present only when storage is not None)
    soc      (ns,)  storage state of charge, MWh
                    (present only when storage is not None)
    p_nd     (nnd,) nondispatchable real power, MW, nonneg
                    (present only when nondispatchable is not None)

Objective:
    minimize  G + sum_s aging_weight[s] * |b[s]|

    where
        G = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)   generation cost
        aging term absent when storage is None

Constraints:
    sum(Pg) + (1/baseMVA)*sum(b) + (1/baseMVA)*sum(p_nd) == Pd_total
    Pgmin[k] <= Pg[k] <= Pgmax[k]
    -S_max[s] <= b[s] <= S_max[s]           (storage power bounds)
    0 <= soc[s] <= capacity[s]              (storage SoC bounds)
    soc dynamics across time steps          (storage coupling)
    0 <= p_nd[n] <= R_t[n]                  (nondispatchable bounds)

where Pd_total = sum(bus[:, PD]) / baseMVA  (scalar, all buses summed).

No branch flows, no line losses, no reactive power.

This module is not part of the public API; use problem.py instead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf.network import reindex_case_to_consecutive
from cvxopf.cost import poly_cost_expr
from cvxopf.storage import (
    StorageUnitIdeal,
    _validate_storage,
    _make_storage_incidence_matrix,
    _make_storage_soc_constraints,
)
from cvxopf.nondispatchable import (
    NondispatchableUnit,
    _validate_nondispatchable,
    _make_nd_incidence_matrix,
    _parse_nd_timeseries,
)
from cvxopf.network import BUS_I

# MATPOWER column index constants
PD = 2
GEN_BUS = 0
GEN_STATUS = 7
PMIN = 9
PMAX = 8


def _make_singlenode_dc_step_constraints(
    Pg,
    Pd_total_t: float,
    Pgmin,
    Pgmax,
    baseMVA: float,
    ns: int = 0,
    S_max=None,
    storage_capacity=None,
    b_t=None,
    soc_t=None,
    nnd: int = 0,
    nd_p_available_t=None,
    p_nd_t=None,
) -> list:
    """
    Build constraints for a single time step of the single-node DC formulation.

    Parameters
    ----------
    Pg : cp.Variable
        Generator real power variables (ng,).
    Pd_total_t : float
        Total load for this time step (scalar, per-unit).
    Pgmin, Pgmax : np.ndarray
        Generator bounds (ng,) in per-unit.
    baseMVA : float
        System base MVA.
    ns : int
        Number of storage units (0 if no storage).
    S_max : np.ndarray or None
        Storage apparent power ratings (ns,) in MW.
    storage_capacity : np.ndarray or None
        Storage capacities (ns,) in MWh.
    b_t : cp.Variable or None
        Storage real power variables (ns,) in MW.
    soc_t : cp.Variable or None
        Storage state-of-charge variables (ns,) in MWh.
    nnd : int
        Number of nondispatchable units (0 if none).
    nd_p_available_t : np.ndarray or None
        Nondispatchable available power (nnd,) in MW.
    p_nd_t : cp.Variable or None
        Nondispatchable real power variables (nnd,) in MW.

    Returns
    -------
    list
        List of CVXPY constraints.
    """
    constr = []

    # Section 1: Power balance (exactly one equality constraint)
    storage_term = cp.multiply(1.0 / baseMVA, cp.sum(b_t)) if ns > 0 else 0
    nd_term = cp.multiply(1.0 / baseMVA, cp.sum(p_nd_t)) if nnd > 0 else 0
    constr.append(cp.sum(Pg) + storage_term + nd_term == Pd_total_t)

    # Section 2: Generator bounds
    constr.append(Pg >= Pgmin)
    constr.append(Pg <= Pgmax)

    # Section 3: Storage real power bounds (omitted when ns == 0)
    if ns > 0:
        constr.append(b_t >= -S_max)
        constr.append(b_t <= S_max)

    # Section 3b: Nondispatchable real power bounds (omitted when nnd == 0)
    if nnd > 0:
        constr.append(p_nd_t <= nd_p_available_t)
        # p_nd_t >= 0 is enforced via nonneg=True on Variable declaration

    # Section 4: Storage SoC bounds (omitted when ns == 0)
    if ns > 0:
        constr.append(soc_t >= 0.0)
        constr.append(soc_t <= storage_capacity)

    return constr


def _make_singlenode_dc_step_cost(Pg, gencost, baseMVA) -> cp.Expression:
    """
    Build the cost expression for a single time step.

    Parameters
    ----------
    Pg : cp.Variable
        Generator real power variables (ng,) in per-unit.
    gencost : np.ndarray
        Generator cost data (ng, 7) in MATPOWER format.
    baseMVA : float
        System base MVA.

    Returns
    -------
    cp.Expression
        Total generation cost expression.
    """
    ng = Pg.shape[0]
    # Convert Pg from per-unit to MW for cost calculation
    Pg_MW = [cp.multiply(baseMVA, Pg[k]) for k in range(ng)]
    return poly_cost_expr(gencost, Pg_MW)


def _parse_singlenode_dc_case(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
) -> dict:
    """
    Parse a MATPOWER case dict for the single-node DC formulation.

    Parameters
    ----------
    case : dict
        MATPOWER case dict. May be a full case or a minimal dict from
        make_singlenode_case (with empty branch table).
    options : OPFOptions
        Options object (not used by this formulation, accepted for API consistency).
    storage : list[StorageUnitIdeal] | None
        Storage units, if any.
    delta : float
        Time step duration in hours (used only when storage is present).
    nondispatchable : list[NondispatchableUnit] | None
        Nondispatchable units, if any.

    Returns
    -------
    dict
        Parsed data dict with keys: baseMVA, nb, ng, ext_to_int, ext_bus_ids,
        Pd_total, Pgmin, Pgmax, gencost, and optionally storage/ND keys.

    Notes
    -----
    - Does NOT call validate_case (empty branch tables are acceptable).
    - Returns scalar Pd_total = sum(bus[:, PD]) / baseMVA, not per-bus Pd.
    - Does NOT return A, r, f_max, nogen_buses, gen_bus, nl, or loss_weight.
    """
    # Get external bus IDs for validation (before reindexing)
    original_bus = case["bus"]
    ext_bus_ids = set(original_bus[:, BUS_I].astype(int).tolist())

    # Reindex to consecutive bus numbering
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus = case["bus"]
    gen = case["gen"]
    gencost = case["gencost"]

    nb = bus.shape[0]
    ng = gen.shape[0]

    # Compute total load (scalar, not per-bus)
    Pd_total = float(np.sum(bus[:, PD]) / baseMVA)

    # Extract generator data
    status = gen[:, GEN_STATUS].astype(int)
    Pgmin = gen[:, PMIN].astype(float) / baseMVA
    Pgmax = gen[:, PMAX].astype(float) / baseMVA

    # Zero out bounds for inactive generators
    for k in range(ng):
        if status[k] != 1:
            Pgmin[k] = 0.0
            Pgmax[k] = 0.0

    # Storage data (if present)
    storage_data = {}
    if storage is not None:
        _validate_storage(storage, ext_bus_ids)
        storage_data = {
            "ns": len(storage),
            "Cs": _make_storage_incidence_matrix(storage, nb, ext_to_int),
            "storage_bus": np.array([unit.bus for unit in storage], dtype=int),
            "storage_apparent_power_rating": np.array(
                [unit.apparent_power_rating for unit in storage], dtype=float
            ),
            "storage_capacity": np.array(
                [unit.capacity for unit in storage], dtype=float
            ),
            "storage_initial_soc": np.array(
                [unit.initial_soc for unit in storage], dtype=float
            ),
            "storage_aging_weight": np.array(
                [unit.aging_weight for unit in storage], dtype=float
            ),
            "storage_delta": float(delta),
        }

    # Nondispatchable data (if present)
    nd_data = {}
    if nondispatchable is not None and len(nondispatchable) > 0:
        _validate_nondispatchable(nondispatchable, ext_bus_ids)
        nd_data = {
            "nnd": len(nondispatchable),
            "Cnd": _make_nd_incidence_matrix(nondispatchable, nb, ext_to_int),
            "nd_bus": np.array([unit.bus for unit in nondispatchable], dtype=int),
            "nd_apparent_power_rating": np.array(
                [unit.apparent_power_rating for unit in nondispatchable], dtype=float
            ),
            "nd_p_available": np.array(
                [unit.p_available for unit in nondispatchable], dtype=float
            ),
        }

    return {
        "baseMVA": baseMVA,
        "nb": nb,
        "ng": ng,
        "ext_to_int": ext_to_int,
        "ext_bus_ids": ext_bus_ids,
        "Pd_total": Pd_total,
        "Pgmin": Pgmin,
        "Pgmax": Pgmax,
        "gencost": gencost,
        **storage_data,
        **nd_data,
    }


def _build_singlenode_dc_single(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
) -> "OPFBuild":
    """
    Build a single time-step single-node DC dispatch problem.

    Parameters
    ----------
    case : dict
        MATPOWER case dict.
    options : OPFOptions
        Options (not used by this formulation).
    storage : list[StorageUnitIdeal] | None
        Storage units, if any.
    delta : float
        Time step duration in hours (used only when storage is present).
    nondispatchable : list[NondispatchableUnit] | None
        Nondispatchable units, if any.

    Returns
    -------
    OPFBuild
        Problem container with formulation="singlenode_dc", is_convex=True.
    """
    from cvxopf.problem import OPFBuild

    # Parse the case
    d = _parse_singlenode_dc_case(case, options, storage, delta, nondispatchable)

    # Declare variables
    Pg = cp.Variable(d["ng"], name="Pg", nonneg=True)

    # Storage variables (if present)
    b_t = None
    soc_t = None
    if "ns" in d:
        b_t = cp.Variable(d["ns"], name="b")
        soc_t = cp.Variable(d["ns"], name="soc")

    # Nondispatchable variables (if present)
    p_nd_t = None
    if "nnd" in d:
        p_nd_t = cp.Variable(d["nnd"], name="p_nd", nonneg=True)

    # Build constraints
    constr = _make_singlenode_dc_step_constraints(
        Pg=Pg,
        Pd_total_t=d["Pd_total"],
        Pgmin=d["Pgmin"],
        Pgmax=d["Pgmax"],
        baseMVA=d["baseMVA"],
        ns=d.get("ns", 0),
        S_max=d.get("storage_apparent_power_rating"),
        storage_capacity=d.get("storage_capacity"),
        b_t=b_t,
        soc_t=soc_t,
        nnd=d.get("nnd", 0),
        nd_p_available_t=d.get("nd_p_available"),
        p_nd_t=p_nd_t,
    )

    # Build cost
    cost = _make_singlenode_dc_step_cost(Pg, d["gencost"], d["baseMVA"])

    # Add storage aging cost if present
    if "ns" in d:
        aging_cost = cp.sum(cp.multiply(d["storage_aging_weight"], cp.abs(b_t)))
        cost = cost + aging_cost

    # Add storage SoC constraints if present
    if "ns" in d:
        soc_constr = _make_storage_soc_constraints(
            [b_t], [soc_t], d["storage_initial_soc"], d["storage_delta"], T=1, ns=d["ns"]
        )
        constr.extend(soc_constr)

    # Build the problem
    prob = cp.Problem(cp.Minimize(cost), constr)

    # Assemble variables dict
    variables = {"Pg": Pg}
    if "ns" in d:
        variables["b"] = b_t
        variables["soc"] = soc_t
    if "nnd" in d:
        variables["p_nd"] = p_nd_t

    # Assemble data dict
    data = {
        "baseMVA": d["baseMVA"],
        "nb": d["nb"],
        "ng": d["ng"],
        "ext_to_int": d["ext_to_int"],
        "Pd_total": d["Pd_total"],
        "Pgmin": d["Pgmin"],
        "Pgmax": d["Pgmax"],
        "gencost": d["gencost"],
    }

    # Add storage data if present
    if "ns" in d:
        data.update({
            "ns": d["ns"],
            "Cs": d["Cs"],
            "storage_bus": d["storage_bus"],
            "storage_apparent_power_rating": d["storage_apparent_power_rating"],
            "storage_capacity": d["storage_capacity"],
            "storage_initial_soc": d["storage_initial_soc"],
            "storage_delta": d["storage_delta"],
            "storage_aging_weight": d["storage_aging_weight"],
        })

    # Add nondispatchable data if present
    if "nnd" in d:
        data.update({
            "nnd": d["nnd"],
            "Cnd": d["Cnd"],
            "nd_bus": d["nd_bus"],
            "nd_apparent_power_rating": d["nd_apparent_power_rating"],
            "nd_p_available": d["nd_p_available"],
        })

    return OPFBuild(
        prob=prob,
        variables=variables,
        data=data,
        formulation="singlenode_dc",
        is_convex=True,
    )


def _build_singlenode_dc_multistep(
    case: dict,
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
    T: int,
    options,
    coupling_constraints: list,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    df_nd: pd.DataFrame | None = None,
) -> "OPFBuild":
    """Stub implementation - will be replaced in Step 4."""
    raise NotImplementedError("singlenode_dc multistep builder not yet implemented")