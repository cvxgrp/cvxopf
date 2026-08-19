"""
Lossy DC OPF problem construction helpers.

This module implements the lossy DC optimal power flow formulation from:

    Convex Optimization with Smart Grid Examples,
    https://doi.org/10.2172/3018252

Formulation
-----------
Variables:
    p_flows  (nl,)  branch real power flows, p.u.
    Pg       (ng,)  per-generator real generation, p.u.

Objective:
    minimize  delta * sum_t (G_t + loss_weight * L_t) + terminal_cost

    where
        G_t = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)
        L_t = sum_e r_e * p_flows_e^2

G and the weighted loss proxy are stage-cost rates. Terminal cost is a
once-per-horizon boundary term and is not multiplied by delta.

Constraints:
    A @ p_flows + p_components == 0  flow conservation at every bus
    |p_flows[e]| <= f_max[e]         branch flow limits
    Pgmin <= Pg <= Pgmax

Here ``p_components`` is the generic aggregate of generator, load, and other
device injections. Loads enter with their device-owned negative injection
sign rather than through formulation-local subtraction.

This is a convex QP; the default solver is CLARABEL (nlp=False).

This module is not part of the public API; use problem.py instead.
"""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf.network import (
    reindex_case_to_consecutive,
    make_branch_node_incidence_matrix,
)
from cvxopf.data import validate_case
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
    HVDCInputs,
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
from cvxopf.hvdc import (
    HVDCLink,
)

if TYPE_CHECKING:
    from cvxopf.problem import OPFBuild

# ---------------------------------------------------------------------------
# MATPOWER column indices
# ---------------------------------------------------------------------------

PD         = 2
BR_R       = 2
BR_STATUS  = 10
RATE_A     = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_dc_case(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    hvdc: list[HVDCLink] | None = None,
    generators: list[DispatchableGenerator] | None = None,
    horizon_steps: int = 1,
    nd_available_mw: np.ndarray | None = None,
    hvdc_inputs: HVDCInputs | None = None,
    load_inputs: LoadInputs | None = None,
    is_multistep: bool = False,
    loads: list[Load] | None = None,
    load_participates_when_empty: bool = False,
) -> dict:
    """
    Validate, reindex, and extract all numpy data needed for DC OPF.
    Returns a flat dict consumed by the DC single-step and multistep builders.
    """
    validate_case(case)
    if loads is None:
        loads = loads_from_matpower(case["bus"])
    if generators is None:
        generators = gen_from_matpower(case["gen"], case["gencost"])
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    branch  = case["branch"]
    nb      = bus.shape[0]
    nl      = branch.shape[0]

    A  = make_branch_node_incidence_matrix(case)

    # branch resistances (p.u.)
    r = branch[:, BR_R].astype(float) / 1.0   # already dimensionless p.u.

    # branch flow limits (p.u.), with sentinel substitution for rateA=0
    f_max = np.zeros(nl)
    for e in range(nl):
        rate = float(branch[e, RATE_A])
        if rate == 0.0:
            warnings.warn(
                f"Branch {e} "
                f"(bus {int(branch[e, 0])} -> {int(branch[e, 1])}) "
                f"has rateA=0; substituting "
                f"branch_limit_sentinel={options.branch_limit_sentinel} MW.",
                UserWarning,
                stacklevel=4,
            )
            f_max[e] = options.branch_limit_sentinel / baseMVA
        else:
            f_max[e] = rate / baseMVA

    # nodal load (p.u.)
    Pd = bus[:, PD].astype(float) / baseMVA

    # Component contexts always receive an explicit bus map. Preserve
    # ext_to_int=None as public metadata when no reindexing was necessary.
    component_ext_to_int = (
        ext_to_int
        if ext_to_int is not None
        else {
            int(bus_id): int(bus_id)
            for bus_id in bus[:, 0]
        }
    )
    ext_bus_ids = set(component_ext_to_int.keys())

    preparation = PreparationContext(
        base_mva=baseMVA,
        nb=nb,
        ext_to_int=component_ext_to_int,
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
        "lossy_dc",
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
        hvdc_inputs=hvdc_inputs,
    )
    components = prepare_components(requests, "lossy_dc", preparation)
    load_p_mw = (
        np.asarray(components.flat_data["load_p_mw"], dtype=float)
        if load_inputs is None else load_inputs.p_mw[0]
    )
    Pd = np.asarray(components.flat_data["Cload"]) @ load_p_mw / baseMVA

    formulation_data = dict(
        case=case, baseMVA=baseMVA,
        nb=nb, nl=nl,
        ext_to_int=ext_to_int,
        _component_ext_to_int=component_ext_to_int,
        ext_bus_ids=ext_bus_ids,
        A=A,
        r=r, f_max=f_max,
        Pd=Pd,
        loss_weight=options.loss_weight,
        _components=components,
    )
    return merge_prepared_component_data(components, formulation_data)


def _make_dc_step_constraints(
    p_flows,
    component_injection,
    A,
    f_max,
    component_operating_constraints,
) -> tuple[list, cp.Expression]:
    """Build one DC step's constraints and modeled net bus injection."""
    # Section 1: Nodal real power balance
    p_net = component_injection
    constr = [A @ p_flows + p_net == 0]

    # Section 2: Branch flow limits
    constr.append(cp.abs(p_flows) <= f_max)

    # Section 3: Ordered component operating constraints
    constr += list(component_operating_constraints)

    return constr, p_net


def _make_dc_step_cost(
    component_cost_rate,
    r, p_flows, loss_weight,
) -> tuple[cp.Expression, cp.Expression]:
    """Build the total and network-loss DC stage-cost rates."""
    L     = cp.sum(cp.multiply(r, cp.square(p_flows)))
    loss_cost = cp.multiply(loss_weight, L)
    return component_cost_rate + loss_cost, loss_cost


# ---------------------------------------------------------------------------
# Public builders (called from problem.py dispatch)
# ---------------------------------------------------------------------------

def _build_lossy_dc_single(
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
    """Build a single time-step lossy DC OPF problem."""
    from cvxopf.problem import OPFBuild

    # Emit warning if storage is present in DC formulation
    if storage:
        warnings.warn(
            "Storage apparent_power_rating is applied as a real power limit "
            "only for formulation='lossy_dc'. Reactive power is not modelled "
            "in the DC formulation.",
            UserWarning,
            stacklevel=3,
        )

    d = _parse_dc_case(
        case, options, storage, delta, nondispatchable, hvdc, generators,
        loads=loads,
        load_participates_when_empty=loads is not None,
    )

    p_flows = cp.Variable(d["nl"], name="p_flows")
    step_context = StepContext(
        "lossy_dc",
        0,
        d["baseMVA"],
        d["_component_ext_to_int"],
        DCNetworkState(),
    )
    components: PreparedComponents = d["_components"]
    step_components = assemble_component_step(components, step_context)
    step_aggregate = aggregate_step_contributions(step_components)

    constr, p_net_expr = _make_dc_step_constraints(
        p_flows,
        step_aggregate.injection.p_pu,
        d["A"], d["f_max"],
        step_aggregate.operating_constraints,
    )
    constr.extend(step_aggregate.network_constraints)

    assert step_aggregate.cost is not None
    step_cost_rate, loss_cost_rate = _make_dc_step_cost(
        step_aggregate.cost,
        d["r"], p_flows, d["loss_weight"],
    )
    cost = integrate_stage_cost_rates([step_cost_rate], delta)
    component_costs = integrate_component_stage_costs(
        [step_components],
        delta,
    )
    dc_loss_cost = integrate_stage_cost_rates([loss_cost_rate], delta)

    horizon = assemble_component_horizon(
        components,
        [step_components],
        HorizonContext("lossy_dc", 1, delta),
    )
    horizon_aggregate = aggregate_horizon_contributions(horizon)
    storage_horizon = horizon.get("storage")
    storage_terminal_cost = (
        None if storage_horizon is None else storage_horizon.terminal_cost
    )
    if horizon_aggregate.terminal_cost is not None:
        cost = cost + horizon_aggregate.terminal_cost
    constr.extend(horizon_aggregate.constraints)

    prob      = cp.Problem(cp.Minimize(cost), constr)
    variables = dict(p_flows=p_flows)
    variables = publish_component_variables(
        [step_components],
        variables,
        multistep=False,
    )

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"],
        r=d["r"], f_max=d["f_max"],
        Pd=d["Pd"],
        loss_weight=d["loss_weight"],
    )
    data = publish_component_metadata(components, data)

    compatibility_expressions = {"p_net": p_net_expr}
    compatibility_expressions.update(component_costs)
    compatibility_expressions["dc_loss_cost"] = dc_loss_cost
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
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
        expressions=expressions,
    )


def _build_lossy_dc_multistep(
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
    """Build a T-step lossy DC OPF problem as a single cp.Problem."""
    from cvxopf.problem import OPFBuild

    # Emit warning if storage is present in DC formulation
    if storage:
        warnings.warn(
            "Storage apparent_power_rating is applied as a real power limit "
            "only for formulation='lossy_dc'. Reactive power is not modelled "
            "in the DC formulation.",
            UserWarning,
            stacklevel=3,
        )

    d = _parse_dc_case(
        case,
        options,
        storage,
        delta,
        nondispatchable,
        hvdc,
        generators,
        horizon_steps=T,
        nd_available_mw=(
            None if df_nd is None else df_nd.to_numpy(dtype=float)
        ),
        hvdc_inputs=(
            None
            if not hvdc
            else HVDCInputs(
                df_hvdc_min.to_numpy(dtype=float),
                df_hvdc_max.to_numpy(dtype=float),
            )
        ),
        load_inputs=load_inputs,
        is_multistep=True,
        loads=loads,
        load_participates_when_empty=load_participates_when_empty,
    )
    Pd_series = load_inputs.p_mw @ d["Cload"].T / d["baseMVA"]

    p_flows_list    = []
    component_steps = []
    step_aggregates = []
    components: PreparedComponents = d["_components"]
    p_net_expr_list = []
    all_constr      = []
    step_cost_rates = []
    loss_cost_rates = []

    for t in range(T):
        p_flows_t = cp.Variable(d["nl"], name=f"p_flows_{t}")
        step_context = StepContext(
            "lossy_dc",
            t,
            d["baseMVA"],
            d["_component_ext_to_int"],
            DCNetworkState(),
        )
        step_components = assemble_component_step(
            components, step_context, variable_suffix=f"_{t}"
        )
        component_steps.append(step_components)
        step_aggregate = aggregate_step_contributions(step_components)
        step_aggregates.append(step_aggregate)

        step_constr, p_net_expr_t = _make_dc_step_constraints(
            p_flows_t,
            step_aggregate.injection.p_pu,
            d["A"], d["f_max"],
            step_aggregate.operating_constraints,
        )
        step_constr.extend(step_aggregate.network_constraints)
        assert step_aggregate.cost is not None
        step_cost_rate, loss_cost_rate = _make_dc_step_cost(
            step_aggregate.cost,
            d["r"], p_flows_t, d["loss_weight"],
        )

        all_constr.extend(step_constr)
        step_cost_rates.append(step_cost_rate)
        loss_cost_rates.append(loss_cost_rate)
        p_flows_list.append(p_flows_t)
        p_net_expr_list.append(p_net_expr_t)

    total_cost = integrate_stage_cost_rates(step_cost_rates, delta)
    component_costs = integrate_component_stage_costs(
        component_steps,
        delta,
    )
    dc_loss_cost = integrate_stage_cost_rates(loss_cost_rates, delta)

    horizon = assemble_component_horizon(
        components,
        component_steps,
        HorizonContext("lossy_dc", T, delta),
    )
    horizon_aggregate = aggregate_horizon_contributions(horizon)
    storage_horizon = horizon.get("storage")
    storage_terminal_cost = (
        None if storage_horizon is None else storage_horizon.terminal_cost
    )
    all_constr.extend(horizon_aggregate.constraints)
    if horizon_aggregate.terminal_cost is not None:
        total_cost = total_cost + horizon_aggregate.terminal_cost

    all_constr.extend(coupling_constraints)
    prob = cp.Problem(cp.Minimize(total_cost), all_constr)

    variables = dict(p_flows=p_flows_list)
    variables = publish_component_variables(
        component_steps,
        variables,
        multistep=True,
    )

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"],
        r=d["r"], f_max=d["f_max"],
        loss_weight=d["loss_weight"],
        T=T,
        Pd_series=Pd_series,
    )
    data = publish_component_metadata(components, data)

    compatibility_expressions = {"p_net": p_net_expr_list}
    compatibility_expressions.update(component_costs)
    compatibility_expressions["dc_loss_cost"] = dc_loss_cost
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
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
        expressions=expressions,
    )
