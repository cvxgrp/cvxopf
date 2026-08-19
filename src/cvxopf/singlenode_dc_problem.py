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
    minimize  delta * sum_t (
                  G_t + sum_s aging_weight[s] * |b[t, s]|
              ) + terminal_cost

    where
        G_t = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)
        aging term absent when storage is None

Stage-cost rates are integrated over time. Terminal cost is a once-per-horizon
boundary term and is not multiplied by delta.

Constraints:
    p_components == 0
    Pgmin[k] <= Pg[k] <= Pgmax[k]
    -S_max[s] <= b[s] <= S_max[s]           (storage power bounds)
    0 <= soc[s] <= capacity[s]              (storage SoC bounds)
    soc dynamics across time steps          (storage coupling)
    0 <= p_nd[n] <= R_t[n]                  (ND availability bound)
    p_nd[n] <= S_max[n]                     (ND converter rating)

Here ``p_components`` is the generic one-node aggregate of generator, load,
storage, and nondispatchable injections. Device-level loads are collapsed only
when their bus injections enter this balance. ``Pd_total`` remains available
as compatibility metadata.

No branch flows, no line losses, no reactive power.

This module is not part of the public API; use problem.py instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf.network import reindex_case_to_consecutive
from cvxopf.data import validate_branch_status, validate_case_identifiers
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
    integrate_component_stage_costs,
    integrate_stage_cost_rates,
    merge_prepared_component_data,
    prepare_components,
    publish_component_expressions,
    publish_component_metadata,
    publish_component_variables,
)
from cvxopf._component_adapters import (
    LoadInputs,
    NondispatchableInputs,
    component_requests,
)
from cvxopf.load import Load, loads_from_matpower
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
    component_operating_constraints,
) -> tuple[list, cp.Expression]:
    """
    Build constraints for a single time step of the single-node DC formulation.

    Parameters
    ----------
    component_injection : cp.Expression
        Aggregate one-node component injection in per-unit.
    component_operating_constraints : tuple[cp.Constraint, ...]
        Ordered operating constraints from all active components.

    Returns
    -------
    tuple[list, cp.Expression]
        CVXPY constraints and the modeled scalar net injection.
    """
    constr = []

    # Section 1: Power balance (exactly one equality constraint)
    p_net = component_injection[0]
    constr.append(p_net == 0)

    # Section 2: Ordered component operating constraints
    constr += list(component_operating_constraints)

    return constr, p_net


def _make_singlenode_dc_step_cost(
    component_cost_rate: cp.Expression,
) -> cp.Expression:
    """
    Retain the complete component cost-rate expression for one time step.

    Parameters
    ----------
    component_cost_rate : cp.Expression
        Aggregate component-owned stage-cost rate.

    Returns
    -------
    cp.Expression
        Total component stage-cost-rate expression.
    """
    return component_cost_rate


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
    load_inputs: LoadInputs | None = None,
    is_multistep: bool = False,
    loads: list[Load] | None = None,
    load_participates_when_empty: bool = False,
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
        Time step duration in hours. Integrates every stage-cost rate and is
        also used by storage dynamics.
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
    validate_branch_status(case["branch"])
    validate_case_identifiers(case)

    # Get external bus IDs for validation (before reindexing)
    original_bus = case["bus"]
    ext_bus_ids = set(original_bus[:, BUS_I].astype(int).tolist())
    if loads is None:
        loads = loads_from_matpower(original_bus)
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
        load_units=loads,
        load_inputs=load_inputs,
        load_participates_when_empty=load_participates_when_empty,
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
    load_p_mw = (
        np.asarray(components.flat_data["load_p_mw"], dtype=float)
        if load_inputs is None else load_inputs.p_mw[0]
    )
    Pd_total = float(np.sum(load_p_mw) / baseMVA)

    formulation_data = {
        "baseMVA": baseMVA,
        "nb": 1,
        "source_nb": source_nb,
        "ext_to_int": ext_to_int,
        "ext_bus_ids": ext_bus_ids,
        "collapsed_ext_to_int": collapsed_ext_to_int,
        "Pd_total": Pd_total,
        "_components": components,
    }
    return merge_prepared_component_data(components, formulation_data)


def _build_singlenode_dc_single(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    *,
    hvdc=None,
    generators: list[DispatchableGenerator] | None = None,
    loads: list[Load] | None = None,
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
        Time step duration in hours. Integrates every stage-cost rate and is
        also used by storage dynamics.
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
        loads=loads,
        load_participates_when_empty=loads is not None,
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

    constr, p_net_expr = _make_singlenode_dc_step_constraints(
        component_injection=step_aggregate.injection.p_pu,
        component_operating_constraints=(
            step_aggregate.operating_constraints
        ),
    )
    constr.extend(step_aggregate.network_constraints)

    # Build cost
    assert step_aggregate.cost is not None
    step_cost_rate = _make_singlenode_dc_step_cost(step_aggregate.cost)
    cost = integrate_stage_cost_rates([step_cost_rate], delta)
    component_costs = integrate_component_stage_costs(
        [step_components],
        delta,
    )

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
        [step_components],
        multistep=False,
    )

    # Assemble data dict
    data = {
        "baseMVA": d["baseMVA"],
        "nb": d["nb"],
        "source_nb": d["source_nb"],
        "ext_to_int": d["ext_to_int"],
        "Pd_total": d["Pd_total"],
    }
    data = publish_component_metadata(components, data)

    compatibility_expressions = {"p_net": p_net_expr}
    compatibility_expressions.update(component_costs)
    if storage_terminal_cost is not None:
        compatibility_expressions["storage_terminal_cost"] = (
            storage_terminal_cost
        )
    expressions = publish_component_expressions(
        [step_aggregate],
        horizon_aggregate,
        compatibility_expressions,
        multistep=False,
    )

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
    df_P: pd.DataFrame | None,
    df_Q: pd.DataFrame | None,
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
    loads: list[Load] | None = None,
    load_inputs: LoadInputs,
    load_participates_when_empty: bool = False,
) -> "OPFBuild":
    """
    Build a multi-step single-node DC dispatch problem.

    A single cp.Problem containing T sets of per-step variables and
    constraints. The objective is the time integral of per-step cost rates
    plus unscaled horizon-boundary costs. Storage SoC dynamics couple
    consecutive steps.

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
        Time step duration in hours. Integrates every stage-cost rate and is
        also used by storage dynamics.
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
        horizon_steps=T,
        nd_available_mw=(
            None if df_nd is None else df_nd.to_numpy(dtype=float)
        ),
        load_inputs=load_inputs,
        is_multistep=True,
        loads=loads,
        load_participates_when_empty=load_participates_when_empty,
    )

    # Retain the legacy aggregate load metadata in per unit.
    Pd_series = load_inputs.p_mw.sum(axis=1) / d["baseMVA"]

    # Accumulators
    component_steps = []
    step_aggregates = []
    components: PreparedComponents = d["_components"]
    p_net_expr_list = []
    all_constr = []
    step_cost_rates = []

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
        step_aggregates.append(step_aggregate)

        step_constr, p_net_expr_t = _make_singlenode_dc_step_constraints(
            component_injection=step_aggregate.injection.p_pu,
            component_operating_constraints=(
                step_aggregate.operating_constraints
            ),
        )
        step_constr.extend(step_aggregate.network_constraints)
        all_constr.extend(step_constr)

        # Per-step cost
        assert step_aggregate.cost is not None
        step_cost_rates.append(
            _make_singlenode_dc_step_cost(step_aggregate.cost)
        )

        # Accumulate variables
        p_net_expr_list.append(p_net_expr_t)

    total_cost = integrate_stage_cost_rates(step_cost_rates, delta)
    component_costs = integrate_component_stage_costs(
        component_steps,
        delta,
    )

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
        component_steps,
        multistep=True,
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
    data = publish_component_metadata(components, data)

    compatibility_expressions = {"p_net": p_net_expr_list}
    compatibility_expressions.update(component_costs)
    if storage_terminal_cost is not None:
        compatibility_expressions["storage_terminal_cost"] = (
            storage_terminal_cost
        )
    expressions = publish_component_expressions(
        step_aggregates,
        horizon_aggregate,
        compatibility_expressions,
        multistep=True,
    )

    return OPFBuild(
        prob=prob,
        variables=variables,
        data=data,
        formulation="singlenode_dc",
        is_convex=True,
        expressions=expressions,
    )
