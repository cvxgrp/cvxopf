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
    minimize  G + loss_weight * L

    where
        G = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)   generation cost
        L = sum_e r_e * p_flows_e^2                         line losses

Constraints:
    A @ p_flows + Cg @ Pg == Pd    flow conservation at every bus
    |p_flows[e]| <= f_max[e]       branch flow limits
    Pgmin <= Pg <= Pgmax

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
from cvxopf.data import validate_case, load_timeseries_from_dataframe
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
    HVDCInputs,
    NondispatchableInputs,
    component_requests,
)
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
    is_multistep: bool = False,
) -> dict:
    """
    Validate, reindex, and extract all numpy data needed for DC OPF.
    Returns a flat dict consumed by the DC single-step and multistep builders.
    """
    validate_case(case)
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

    # External bus IDs for validation — use ext_to_int keys (external MATPOWER
    # numbering) rather than the already-reindexed bus table.
    ext_bus_ids = set(ext_to_int.keys())

    preparation = PreparationContext(
        base_mva=baseMVA,
        nb=nb,
        ext_to_int=ext_to_int,
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

    return dict(
        case=case, baseMVA=baseMVA,
        nb=nb, nl=nl,
        ext_to_int=ext_to_int,
        ext_bus_ids=ext_bus_ids,
        A=A,
        r=r, f_max=f_max,
        Pd=Pd,
        loss_weight=options.loss_weight,
        _components=components,
        **components.flat_data,
    )


def _make_dc_step_constraints(
    p_flows,
    component_injection,
    A,
    Pd,
    f_max,
    component_operating_constraints,
) -> tuple[list, cp.Expression]:
    """Build one DC step's constraints and modeled net bus injection."""
    # Section 1: Nodal real power balance
    p_net = component_injection - Pd
    constr = [A @ p_flows + p_net == 0]

    # Section 2: Branch flow limits
    constr.append(cp.abs(p_flows) <= f_max)

    # Section 3: Ordered component operating constraints
    constr += list(component_operating_constraints)

    return constr, p_net


def _make_dc_step_cost(
    generator_cost,
    r, p_flows, loss_weight,
) -> cp.Expression:
    """Build the per-step DC cost expression."""
    L     = cp.sum(cp.multiply(r, cp.square(p_flows)))
    return generator_cost + cp.multiply(loss_weight, L)


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
        case, options, storage, delta, nondispatchable, hvdc, generators
    )

    p_flows = cp.Variable(d["nl"], name="p_flows")
    step_context = StepContext(
        "lossy_dc", 0, d["baseMVA"], d["ext_to_int"], DCNetworkState()
    )
    components: PreparedComponents = d["_components"]
    step_components = assemble_component_step(components, step_context)
    step_aggregate = aggregate_step_contributions(step_components)
    storage_step = step_components.get("storage")

    constr, p_net_expr = _make_dc_step_constraints(
        p_flows,
        step_aggregate.injection.p_pu,
        d["A"], d["Pd"], d["f_max"],
        step_aggregate.operating_constraints,
    )
    constr.extend(step_aggregate.network_constraints)

    assert step_aggregate.cost is not None
    cost = _make_dc_step_cost(
        step_aggregate.cost,
        d["r"], p_flows, d["loss_weight"],
    )

    # Retain the named storage-cost reporting expression.
    storage_cost = None
    if storage_step is not None:
        storage_cost = storage_step.cost
        assert storage_cost is not None

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
    variables.update(
        publish_component_variables([step_components], multistep=False)
    )

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"],
        r=d["r"], f_max=d["f_max"],
        Pd=d["Pd"],
        loss_weight=d["loss_weight"],
    )
    data.update(publish_component_metadata(components))

    expressions = {"p_net": p_net_expr}
    if storage_cost is not None:
        expressions["storage_cost"] = storage_cost
    if storage_terminal_cost is not None:
        expressions["storage_terminal_cost"] = storage_terminal_cost

    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
        expressions=expressions,
    )


def _build_lossy_dc_multistep(
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
    """Build a T-step lossy DC OPF problem as a single cp.Problem."""
    from cvxopf.problem import OPFBuild

    if df_Q is not None:
        warnings.warn(
            "df_Q is ignored for formulation='lossy_dc'. "
            "Reactive power is not modelled in the DC formulation.",
            UserWarning,
            stacklevel=3,
        )

    # Emit warning if storage is present in DC formulation
    if storage:
        warnings.warn(
            "Storage apparent_power_rating is applied as a real power limit "
            "only for formulation='lossy_dc'. Reactive power is not modelled "
            "in the DC formulation.",
            UserWarning,
            stacklevel=3,
        )

    # Use df_P only for load; construct a dummy df_Q with zeros for the
    # shared timeseries loader (which expects matching shapes).
    df_Q_dummy = pd.DataFrame(
        np.zeros_like(df_P.to_numpy()), columns=df_P.columns
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
        is_multistep=True,
    )
    Pd_series, _ = load_timeseries_from_dataframe(df_P, df_Q_dummy, case)

    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    p_flows_list    = []
    component_steps = []
    components: PreparedComponents = d["_components"]
    p_net_expr_list = []
    all_constr      = []
    total_cost      = 0
    storage_cost    = 0

    for t in range(T):
        p_flows_t = cp.Variable(d["nl"], name=f"p_flows_{t}")
        step_context = StepContext(
            "lossy_dc",
            t,
            d["baseMVA"],
            d["ext_to_int"],
            DCNetworkState(),
        )
        step_components = assemble_component_step(
            components, step_context, variable_suffix=f"_{t}"
        )
        component_steps.append(step_components)
        step_aggregate = aggregate_step_contributions(step_components)
        storage_step = step_components.get("storage")

        step_constr, p_net_expr_t = _make_dc_step_constraints(
            p_flows_t,
            step_aggregate.injection.p_pu,
            d["A"], Pd_series[t], d["f_max"],
            step_aggregate.operating_constraints,
        )
        step_constr.extend(step_aggregate.network_constraints)
        assert step_aggregate.cost is not None
        step_cost = _make_dc_step_cost(
            step_aggregate.cost,
            d["r"], p_flows_t, d["loss_weight"],
        )

        # Add storage aging cost if present
        if storage_step is not None:
            step_storage_cost = storage_step.cost
            assert step_storage_cost is not None
            storage_cost = storage_cost + step_storage_cost

        all_constr.extend(step_constr)
        total_cost  = total_cost + step_cost
        p_flows_list.append(p_flows_t)
        p_net_expr_list.append(p_net_expr_t)

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
    variables.update(
        publish_component_variables(component_steps, multistep=True)
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
    data.update(publish_component_metadata(components))

    expressions = {"p_net": p_net_expr_list}
    if "ns" in d:
        expressions["storage_cost"] = storage_cost
    if storage_terminal_cost is not None:
        expressions["storage_terminal_cost"] = storage_terminal_cost

    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
        expressions=expressions,
    )
