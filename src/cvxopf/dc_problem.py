"""
Lossy DC OPF problem construction helpers.

This module implements the lossy DC optimal power flow formulation from:

    Convex Optimization with Smart Grid Examples,
    https://doi.org/10.2172/3018252

Formulation
-----------
Variables:
    p_flows  (nl,)  branch real power flows, p.u.
    p_gen    (nb,)  nodal real generation, p.u., nonneg

Objective:
    minimize  G + loss_weight * L

    where
        G = sum_k (c0_k + c1_k * Pg_k + c2_k * Pg_k^2)   generation cost
        L = sum_e r_e * p_flows_e^2                         line losses

Constraints:
    A @ p_flows + p_gen == Pd      flow conservation at every bus
    |p_flows[e]| <= f_max[e]       branch flow limits
    Pgmin[k] <= p_gen[gen_bus[k]] <= Pgmax[k]
    p_gen[non_gen_buses] == 0

This is a convex QP; the default solver is CLARABEL (nlp=False).

This module is not part of the public API; use problem.py instead.
"""

from __future__ import annotations

import warnings

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf.network import (
    reindex_case_to_consecutive,
    make_branch_node_incidence_matrix,
    make_incidence_matrix,
)
from cvxopf.cost import poly_cost_expr
from cvxopf.data import validate_case, load_timeseries_from_dataframe

# ---------------------------------------------------------------------------
# MATPOWER column indices
# ---------------------------------------------------------------------------

PD         = 2
GEN_BUS    = 0
GEN_STATUS = 7
PMIN       = 9
PMAX       = 8
BR_R       = 2
BR_STATUS  = 10
RATE_A     = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_dc_case(case: dict, options) -> dict:
    """
    Validate, reindex, and extract all numpy data needed for DC OPF.
    Returns a flat dict consumed by the DC single-step and multistep builders.
    """
    validate_case(case)
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    branch  = case["branch"]
    gen     = case["gen"]
    gencost = case["gencost"]
    nb      = bus.shape[0]
    ng      = gen.shape[0]
    nl      = branch.shape[0]

    A  = make_branch_node_incidence_matrix(case)
    Cg = make_incidence_matrix(case)

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

    # generator data
    status  = gen[:, GEN_STATUS].astype(int)
    gen_bus = gen[:, GEN_BUS].astype(int)
    Pgmin   = gen[:, PMIN].astype(float) / baseMVA
    Pgmax   = gen[:, PMAX].astype(float) / baseMVA

    for k in range(ng):
        if status[k] != 1:
            Pgmin[k] = Pgmax[k] = 0.0

    # non-generator bus indices
    all_buses    = set(range(nb))
    gen_bus_set  = set(gen_bus[status == 1].tolist())
    nogen_buses  = sorted(all_buses - gen_bus_set)

    return dict(
        case=case, baseMVA=baseMVA,
        nb=nb, ng=ng, nl=nl,
        ext_to_int=ext_to_int,
        A=A, Cg=Cg,
        r=r, f_max=f_max,
        Pd=Pd,
        status=status, gen_bus=gen_bus,
        Pgmin=Pgmin, Pgmax=Pgmax,
        gencost=gencost,
        nogen_buses=nogen_buses,
        loss_weight=options.loss_weight,
    )


def _make_dc_step_constraints(
    p_flows, p_gen,
    A, Pd, f_max, gen_bus, Pgmin, Pgmax, nogen_buses,
) -> list:
    """Build the list of CVXPY constraints for one DC time step."""
    constr = [
        A @ p_flows + p_gen == Pd,
        cp.abs(p_flows) <= f_max,
        p_gen[gen_bus] >= Pgmin,
        p_gen[gen_bus] <= Pgmax,
    ]
    if nogen_buses:
        constr.append(p_gen[nogen_buses] == 0.0)
    return constr


def _make_dc_step_cost(
    p_gen, gen_bus, gencost, baseMVA,
    r, p_flows, loss_weight,
) -> cp.Expression:
    """Build the per-step DC cost expression."""
    ng    = len(gen_bus)
    Pg_MW = [baseMVA * p_gen[int(gen_bus[k])] for k in range(ng)]
    G     = poly_cost_expr(gencost, Pg_MW)
    L     = cp.sum(cp.multiply(r, cp.square(p_flows)))
    return G + loss_weight * L


# ---------------------------------------------------------------------------
# Public builders (called from problem.py dispatch)
# ---------------------------------------------------------------------------

def _build_lossy_dc_single(case: dict, options) -> "OPFBuild":
    """Build a single time-step lossy DC OPF problem."""
    from cvxopf.problem import OPFBuild

    d = _parse_dc_case(case, options)

    p_flows = cp.Variable(d["nl"], name="p_flows")
    p_gen   = cp.Variable(d["nb"], name="p_gen", nonneg=True)

    constr = _make_dc_step_constraints(
        p_flows, p_gen,
        d["A"], d["Pd"], d["f_max"],
        d["gen_bus"], d["Pgmin"], d["Pgmax"],
        d["nogen_buses"],
    )

    cost = _make_dc_step_cost(
        p_gen, d["gen_bus"], d["gencost"], d["baseMVA"],
        d["r"], p_flows, d["loss_weight"],
    )

    prob      = cp.Problem(cp.Minimize(cost), constr)
    variables = dict(p_flows=p_flows, p_gen=p_gen)
    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"], Cg=d["Cg"],
        r=d["r"], f_max=d["f_max"],
        Pd=d["Pd"],
        gen_bus=d["gen_bus"],
        Pgmin=d["Pgmin"], Pgmax=d["Pgmax"],
        loss_weight=d["loss_weight"],
    )
    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
    )


def _build_lossy_dc_multistep(
    case: dict,
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
    T: int,
    options,
    coupling_constraints: list,
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

    # Use df_P only for load; construct a dummy df_Q with zeros for the
    # shared timeseries loader (which expects matching shapes).
    df_Q_dummy = pd.DataFrame(
        np.zeros_like(df_P.to_numpy()), columns=df_P.columns
    )

    d = _parse_dc_case(case, options)
    Pd_series, _ = load_timeseries_from_dataframe(df_P, df_Q_dummy, case)

    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    p_flows_list = []
    p_gen_list   = []
    all_constr   = []
    total_cost   = 0

    for t in range(T):
        p_flows_t = cp.Variable(d["nl"], name=f"p_flows_{t}")
        p_gen_t   = cp.Variable(d["nb"], name=f"p_gen_{t}", nonneg=True)

        step_constr = _make_dc_step_constraints(
            p_flows_t, p_gen_t,
            d["A"], Pd_series[t], d["f_max"],
            d["gen_bus"], d["Pgmin"], d["Pgmax"],
            d["nogen_buses"],
        )
        step_cost = _make_dc_step_cost(
            p_gen_t, d["gen_bus"], d["gencost"], d["baseMVA"],
            d["r"], p_flows_t, d["loss_weight"],
        )

        all_constr.extend(step_constr)
        total_cost  = total_cost + step_cost
        p_flows_list.append(p_flows_t)
        p_gen_list.append(p_gen_t)

    all_constr.extend(coupling_constraints)
    prob = cp.Problem(cp.Minimize(total_cost), all_constr)

    variables = dict(p_flows=p_flows_list, p_gen=p_gen_list)
    data      = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"], Cg=d["Cg"],
        r=d["r"], f_max=d["f_max"],
        gen_bus=d["gen_bus"],
        Pgmin=d["Pgmin"], Pgmax=d["Pgmax"],
        loss_weight=d["loss_weight"],
        T=T,
        Pd_series=Pd_series,
    )
    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
    )