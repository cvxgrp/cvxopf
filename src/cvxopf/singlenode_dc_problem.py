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
    0 <= p_nd[n] <= R_t[n]                  (ND availability bound)
    p_nd[n] <= S_max[n]                     (ND converter rating)

where Pd_total = sum(bus[:, PD]) / baseMVA  (scalar, all buses summed).

No branch flows, no line losses, no reactive power.

This module is not part of the public API; use problem.py instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf.network import reindex_case_to_consecutive
from cvxopf.generator import (
    DispatchableGenerator,
    gen_from_matpower,
)
from cvxopf._component_adapter import (
    DCNetworkState,
    HorizonContext,
    PreparationContext,
    StepContext,
)
from cvxopf._component_assembly import (
    PreparedComponents,
    aggregate_horizon_contributions,
    aggregate_step_contributions,
    assemble_component_horizon,
    assemble_component_step,
    prepare_components,
    publish_component_metadata,
    publish_component_variables,
)
from cvxopf._component_adapters import (
    NondispatchableInputs,
    component_requests,
)
from cvxopf.storage import (
    StorageUnitIdeal,
)
from cvxopf.nondispatchable import (
    NondispatchableUnit,
)
from cvxopf.network import BUS_I

if TYPE_CHECKING:
    from cvxopf.problem import OPFBuild

# MATPOWER column index constants
PD = 2


def _make_singlenode_dc_step_constraints(
    component_injection,
    Pd_total_t: float,
    component_operating_constraints,
) -> tuple[list, cp.Expression]:
    """
    Build constraints for a single time step of the single-node DC formulation.

    Parameters
    ----------
    component_injection : cp.Expression
        Aggregate one-node component injection in per-unit.
    Pd_total_t : float
        Total load for this time step (scalar, per-unit).
    component_operating_constraints : tuple[cp.Constraint, ...]
        Ordered operating constraints from all active components.

    Returns
    -------
    tuple[list, cp.Expression]
        CVXPY constraints and the modeled scalar net injection.
    """
    constr = []

    # Section 1: Power balance (exactly one equality constraint)
    p_net = component_injection[0] - Pd_total_t
    constr.append(p_net == 0)

    # Section 2: Ordered component operating constraints
    constr += list(component_operating_constraints)

    return constr, p_net


def _make_singlenode_dc_step_cost(
    generator_cost: cp.Expression,
) -> cp.Expression:
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
    return generator_cost


def _parse_singlenode_dc_case(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    generators: list[DispatchableGenerator] | None = None,
    hvdc=None,
    horizon_steps: int = 1,
    nd_available_mw: np.ndarray | None = None,
    is_multistep: bool = False,
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
    - Does NOT return A, r, f_max, nogen_buses, nl, or loss_weight.
      ``Cg`` and ``gen_bus`` use the collapsed one-node representation.
    """
    # Get external bus IDs for validation (before reindexing)
    original_bus = case["bus"]
    ext_bus_ids = set(original_bus[:, BUS_I].astype(int).tolist())
    if generators is None:
        generators = gen_from_matpower(case["gen"], case["gencost"])

    # Reindex to consecutive bus numbering
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus = case["bus"]

    source_nb = bus.shape[0]
    # Compute total load (scalar, not per-bus)
    Pd_total = float(np.sum(bus[:, PD]) / baseMVA)

    collapsed_ext_to_int = {bus_id: 0 for bus_id in ext_bus_ids}
    preparation = PreparationContext(
        base_mva=baseMVA,
        nb=1,
        ext_to_int=collapsed_ext_to_int,
        ext_bus_ids=frozenset(ext_bus_ids),
        horizon_steps=horizon_steps,
        delta=delta,
        is_multistep=is_multistep,
    )
    if nondispatchable and nd_available_mw is None:
        nd_available_mw = np.array(
            [[unit.p_available for unit in nondispatchable]],
            dtype=float,
        )
    requests = component_requests(
        "singlenode_dc",
        generators=generators,
        storage_units=storage or (),
        nondispatchable_units=nondispatchable or (),
        nondispatchable_inputs=(
            None
            if not nondispatchable
            else NondispatchableInputs(nd_available_mw)
        ),
        hvdc_links=hvdc or (),
    )
    components = prepare_components(
        requests, "singlenode_dc", preparation
    )

    return {
        "baseMVA": baseMVA,
        "nb": 1,
        "source_nb": source_nb,
        "ext_to_int": ext_to_int,
        "ext_bus_ids": ext_bus_ids,
        "collapsed_ext_to_int": collapsed_ext_to_int,
        "Pd_total": Pd_total,
        "_components": components,
        **components.flat_data,
    }


def _build_singlenode_dc_single(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    *,
    hvdc=None,
    generators: list[DispatchableGenerator] | None = None,
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
    d = _parse_singlenode_dc_case(
        case,
        options,
        storage,
        delta,
        nondispatchable,
        generators,
        hvdc=hvdc,
    )

    step_context = StepContext(
        "singlenode_dc",
        0,
        d["baseMVA"],
        d["collapsed_ext_to_int"],
        DCNetworkState(),
    )
    components: PreparedComponents = d["_components"]
    step_components = assemble_component_step(components, step_context)
    step_aggregate = aggregate_step_contributions(step_components)
    storage_step = step_components.get("storage")

    constr, p_net_expr = _make_singlenode_dc_step_constraints(
        component_injection=step_aggregate.injection.p_pu,
        Pd_total_t=d["Pd_total"],
        component_operating_constraints=(
            step_aggregate.operating_constraints
        ),
    )
    constr.extend(step_aggregate.network_constraints)

    # Build cost
    assert step_aggregate.cost is not None
    cost = _make_singlenode_dc_step_cost(step_aggregate.cost)

    # Retain the named storage-cost reporting expression.
    storage_cost = None
    if storage_step is not None:
        storage_cost = storage_step.cost
        assert storage_cost is not None

    horizon = assemble_component_horizon(
        components,
        [step_components],
        HorizonContext("singlenode_dc", 1, delta),
    )
    horizon_aggregate = aggregate_horizon_contributions(horizon)
    storage_horizon = horizon.get("storage")
    storage_terminal_cost = (
        None if storage_horizon is None else storage_horizon.terminal_cost
    )
    if horizon_aggregate.terminal_cost is not None:
        cost = cost + horizon_aggregate.terminal_cost
    constr.extend(horizon_aggregate.constraints)

    # Build the problem
    prob = cp.Problem(cp.Minimize(cost), constr)

    # Assemble variables dict
    variables = publish_component_variables(
        [step_components], multistep=False
    )

    # Assemble data dict
    data = {
        "baseMVA": d["baseMVA"],
        "nb": d["nb"],
        "source_nb": d["source_nb"],
        "ext_to_int": d["ext_to_int"],
        "Pd_total": d["Pd_total"],
    }
    data.update(publish_component_metadata(components))

    expressions = {"p_net": p_net_expr}
    if storage_cost is not None:
        expressions["storage_cost"] = storage_cost
    if storage_terminal_cost is not None:
        expressions["storage_terminal_cost"] = storage_terminal_cost

    return OPFBuild(
        prob=prob,
        variables=variables,
        data=data,
        formulation="singlenode_dc",
        is_convex=True,
        expressions=expressions,
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
    *,
    hvdc=None,
    df_hvdc_min=None,
    df_hvdc_max=None,
    generators: list[DispatchableGenerator] | None = None,
) -> "OPFBuild":
    """
    Build a multi-step single-node DC dispatch problem.

    A single cp.Problem containing T sets of per-step variables and
    constraints. The objective is the sum of per-step costs. Storage SoC
    dynamics couple consecutive steps.

    Parameters
    ----------
    case : dict
        MATPOWER case dict.
    df_P : pd.DataFrame
        Real load time series, shape (T, nb). Bus loads are summed across
        columns each step to form a scalar total.
    df_Q : pd.DataFrame or None
        Reactive load time series. Ignored (no reactive power in DC);
        a UserWarning is emitted when not None.
    T : int
        Number of time steps.
    options : OPFOptions
        Options (not used by this formulation).
    coupling_constraints : list
        Extra constraints appended without modification.
    storage : list[StorageUnitIdeal] | None
        Storage units, if any.
    delta : float
        Time step duration in hours (used only when storage is present).
    nondispatchable : list[NondispatchableUnit] | None
        Nondispatchable units, if any.
    df_nd : pd.DataFrame | None
        Available power time series, shape (T, nnd), columns = external bus
        IDs. Never None when nondispatchable is not None (problem.py tiles
        p_available upstream).

    Returns
    -------
    OPFBuild
        Problem container with formulation="singlenode_dc", is_convex=True.
    """
    import warnings

    from cvxopf.problem import OPFBuild

    # df_Q is ignored for the DC formulation
    if df_Q is not None:
        warnings.warn(
            "df_Q is ignored for formulation='singlenode_dc'. "
            "Reactive power is not modelled in the DC formulation.",
            UserWarning,
            stacklevel=3,
        )

    # Parse the case
    d = _parse_singlenode_dc_case(
        case,
        options,
        storage,
        delta,
        nondispatchable,
        generators,
        hvdc=hvdc,
        horizon_steps=T,
        nd_available_mw=(
            None if df_nd is None else df_nd.to_numpy(dtype=float)
        ),
        is_multistep=True,
    )

    # Validate df_P column count before summing
    if df_P.shape[1] != d["source_nb"]:
        raise ValueError(
            f"df_P has {df_P.shape[1]} columns but case has "
            f"{d['source_nb']} source buses."
        )

    # Compute total load per step (scalar per step, not per-bus)
    Pd_series = df_P.values.sum(axis=1) / d["baseMVA"]  # shape (T,)
    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    # Accumulators
    component_steps = []
    components: PreparedComponents = d["_components"]
    p_net_expr_list = []
    all_constr = []
    total_cost = 0
    storage_cost = 0

    for t in range(T):
        step_context = StepContext(
            "singlenode_dc",
            t,
            d["baseMVA"],
            d["collapsed_ext_to_int"],
            DCNetworkState(),
        )
        step_components = assemble_component_step(
            components, step_context, variable_suffix=f"_{t}"
        )
        component_steps.append(step_components)
        step_aggregate = aggregate_step_contributions(step_components)
        storage_step = step_components.get("storage")

        step_constr, p_net_expr_t = _make_singlenode_dc_step_constraints(
            component_injection=step_aggregate.injection.p_pu,
            Pd_total_t=float(Pd_series[t]),
            component_operating_constraints=(
                step_aggregate.operating_constraints
            ),
        )
        step_constr.extend(step_aggregate.network_constraints)
        all_constr.extend(step_constr)

        # Per-step cost
        assert step_aggregate.cost is not None
        step_cost = _make_singlenode_dc_step_cost(step_aggregate.cost)
        if storage_step is not None:
            step_storage_cost = storage_step.cost
            assert step_storage_cost is not None
            storage_cost = storage_cost + step_storage_cost
        total_cost = total_cost + step_cost

        # Accumulate variables
        p_net_expr_list.append(p_net_expr_t)

    horizon = assemble_component_horizon(
        components,
        component_steps,
        HorizonContext("singlenode_dc", T, delta),
    )
    horizon_aggregate = aggregate_horizon_contributions(horizon)
    storage_horizon = horizon.get("storage")
    storage_terminal_cost = (
        None if storage_horizon is None else storage_horizon.terminal_cost
    )
    all_constr.extend(horizon_aggregate.constraints)
    if horizon_aggregate.terminal_cost is not None:
        total_cost = total_cost + horizon_aggregate.terminal_cost

    # Append user coupling constraints unchanged
    all_constr.extend(coupling_constraints)

    # Build the problem
    prob = cp.Problem(cp.Minimize(total_cost), all_constr)

    # Assemble variables dict
    variables = publish_component_variables(
        component_steps, multistep=True
    )

    # Assemble data dict
    data = dict(
        baseMVA=d["baseMVA"],
        nb=d["nb"],
        source_nb=d["source_nb"],
        ext_to_int=d["ext_to_int"],
        T=T,
        Pd_series=Pd_series,
    )
    data.update(publish_component_metadata(components))

    expressions = {"p_net": p_net_expr_list}
    if "ns" in d:
        expressions["storage_cost"] = storage_cost
    if storage_terminal_cost is not None:
        expressions["storage_terminal_cost"] = storage_terminal_cost

    return OPFBuild(
        prob=prob,
        variables=variables,
        data=data,
        formulation="singlenode_dc",
        is_convex=True,
        expressions=expressions,
    )
