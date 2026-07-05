"""
AC-OPF problem construction helpers (DNLP formulation).

This module contains the internal builders for the AC optimal power flow
problem. It is not part of the public API; use problem.py instead.

Formulation: DNLP (disciplined nonlinear programming) via CVXPY.
Solver: IPOPT (via cyipopt).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf.network import (
    reindex_case_to_consecutive,
    make_ybus_matpower,
    make_incidence_matrix,
    make_ybus_sparsity_mask,
)
from cvxopf.cost import poly_cost_expr
from cvxopf.data import validate_case, load_timeseries_from_dataframe

# ---------------------------------------------------------------------------
# MATPOWER column indices
# ---------------------------------------------------------------------------

BUS_TYPE   = 1
VMIN       = 12
VMAX       = 11
PD         = 2
QD         = 3
GEN_BUS    = 0
GEN_STATUS = 7
PMIN       = 9
PMAX       = 8
QMIN       = 4
QMAX       = 3
VG         = 5


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_case(case: dict, options) -> dict:
    """
    Validate, reindex, and extract all numpy data from a case dict.
    Returns a flat dict consumed by the AC single-step and multistep builders.
    """
    validate_case(case)
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    gen     = case["gen"]
    gencost = case["gencost"]
    nb      = bus.shape[0]
    ng      = gen.shape[0]

    Ybus    = make_ybus_matpower(case)
    G       = np.real(Ybus)
    B       = np.imag(Ybus)
    E, Z    = make_ybus_sparsity_mask(Ybus, tol=options.sparsity_tol)

    ref_idx = np.where(bus[:, BUS_TYPE] == 3)[0]
    ref     = int(ref_idx[0])
    pv      = np.where(bus[:, BUS_TYPE] == 2)[0]

    vmin_arr = bus[:, VMIN].astype(float)
    vmax_arr = bus[:, VMAX].astype(float)

    Pd = bus[:, PD].astype(float) / baseMVA
    Qd = bus[:, QD].astype(float) / baseMVA

    status  = gen[:, GEN_STATUS].astype(int)
    Pgmin   = gen[:, PMIN].astype(float) / baseMVA
    Pgmax   = gen[:, PMAX].astype(float) / baseMVA
    Qgmin   = gen[:, QMIN].astype(float) / baseMVA
    Qgmax   = gen[:, QMAX].astype(float) / baseMVA

    for k in range(ng):
        if status[k] != 1:
            Pgmin[k] = Pgmax[k] = Qgmin[k] = Qgmax[k] = 0.0

    Cg      = make_incidence_matrix(case)
    gen_bus = gen[:, GEN_BUS].astype(int)

    return dict(
        case=case, baseMVA=baseMVA,
        bus=bus, gen=gen, gencost=gencost,
        nb=nb, ng=ng,
        Ybus=Ybus, G=G, B=B, E=E, Z=Z,
        ref=ref, pv=pv, ext_to_int=ext_to_int,
        vmin_arr=vmin_arr, vmax_arr=vmax_arr,
        Pd=Pd, Qd=Qd,
        status=status, gen_bus=gen_bus,
        Pgmin=Pgmin, Pgmax=Pgmax,
        Qgmin=Qgmin, Qgmax=Qgmax,
        Cg=Cg,
    )


def _make_step_variables(
    nb: int, ng: int,
    vmin_arr, vmax_arr,
    Pgmin, Pgmax,
    Qgmin, Qgmax,
    suffix: str,
    init_flat: bool,
):
    """Construct one set of per-step CVXPY variables."""
    def name(s):
        return f"{s}{suffix}"

    theta = cp.Variable((nb, 1), name=name("theta"))
    v     = cp.Variable((nb, 1), name=name("v"),
                        bounds=[vmin_arr[:, None], vmax_arr[:, None]])
    P     = cp.Variable((nb, nb), name=name("P"))
    Q     = cp.Variable((nb, nb), name=name("Q"))
    p     = cp.Variable(nb, name=name("p"))
    q     = cp.Variable(nb, name=name("q"))
    Pg    = cp.Variable(ng, name=name("Pg"), bounds=[Pgmin, Pgmax])
    Qg    = cp.Variable(ng, name=name("Qg"), bounds=[Qgmin, Qgmax])

    if init_flat:
        theta.value = np.zeros((nb, 1))
        v.value     = np.ones((nb, 1))

    return theta, v, P, Q, p, q, Pg, Qg


def _make_step_constraints(
    theta, v, P, Q, p, q, Pg, Qg,
    G, B, E, Z, Cg, Pd, Qd, ref,
    pv, status, gen_bus, gen,
    enforce_vset: bool,
    VG_col: int,
) -> list:
    """Build the list of CVXPY constraints for one AC time step."""
    C   = cp.nlp.cos(theta - theta.T)
    S   = cp.nlp.sin(theta - theta.T)
    vvT = v @ v.T

    constr = [
        theta[ref] == 0.0,
        p == cp.sum(P, axis=1),
        q == cp.sum(Q, axis=1),
        P[E] == cp.multiply(
            vvT[E],
            cp.multiply(G[E], C[E]) + cp.multiply(B[E], S[E])
        ),
        Q[E] == cp.multiply(
            vvT[E],
            cp.multiply(G[E], S[E]) - cp.multiply(B[E], C[E])
        ),
        P[Z] == 0.0,
        Q[Z] == 0.0,
        p == Cg @ Pg - Pd,
        q == Cg @ Qg - Qd,
    ]

    if enforce_vset:
        for b in np.r_[np.array([ref]), pv]:
            idx = np.where((gen_bus == int(b)) & (status == 1))[0]
            if idx.size:
                constr.append(v[int(b)] == float(gen[idx[0], VG_col]))

    return constr


# ---------------------------------------------------------------------------
# Public builders (called from problem.py dispatch)
# ---------------------------------------------------------------------------

def _build_ac_single(case: dict, options) -> "OPFBuild":
    """Build a single time-step AC-OPF problem."""
    from cvxopf.problem import OPFBuild

    if options.enforce_branch_limits:
        raise NotImplementedError(
            "enforce_branch_limits is not yet implemented. "
            "It is planned for Milestone 4."
        )

    d = _parse_case(case, options)

    theta, v, P, Q, p, q, Pg, Qg = _make_step_variables(
        d["nb"], d["ng"],
        d["vmin_arr"], d["vmax_arr"],
        d["Pgmin"], d["Pgmax"],
        d["Qgmin"], d["Qgmax"],
        suffix="",
        init_flat=options.init_flat,
    )

    constr = _make_step_constraints(
        theta, v, P, Q, p, q, Pg, Qg,
        d["G"], d["B"], d["E"], d["Z"],
        d["Cg"], d["Pd"], d["Qd"], d["ref"],
        d["pv"], d["status"], d["gen_bus"], d["gen"],
        enforce_vset=options.enforce_vset,
        VG_col=VG,
    )

    cost = poly_cost_expr(d["gencost"], d["baseMVA"] * Pg)
    prob = cp.Problem(cp.Minimize(cost), constr)

    variables = dict(theta=theta, v=v, P=P, Q=Q, p=p, q=q, Pg=Pg, Qg=Qg)
    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"],
        ref=d["ref"], pv=d["pv"], ext_to_int=d["ext_to_int"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        Pd=d["Pd"], Qd=d["Qd"], Cg=d["Cg"],
        Pgmin=d["Pgmin"], Pgmax=d["Pgmax"],
        Qgmin=d["Qgmin"], Qgmax=d["Qgmax"],
    )
    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="ac", is_convex=False,
    )


def _build_ac_multistep(
    case: dict,
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
    T: int,
    options,
    coupling_constraints: list,
) -> "OPFBuild":
    """Build a T-step AC-OPF problem as a single cp.Problem."""
    from cvxopf.problem import OPFBuild

    if options.enforce_branch_limits:
        raise NotImplementedError(
            "enforce_branch_limits is not yet implemented. "
            "It is planned for Milestone 4."
        )

    d = _parse_case(case, options)
    Pd_series, Qd_series = load_timeseries_from_dataframe(df_P, df_Q, case)

    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    theta_list, v_list, P_list, Q_list = [], [], [], []
    p_list, q_list, Pg_list, Qg_list   = [], [], [], []
    all_constr  = []
    total_cost  = 0

    for t in range(T):
        theta_t, v_t, P_t, Q_t, p_t, q_t, Pg_t, Qg_t = _make_step_variables(
            d["nb"], d["ng"],
            d["vmin_arr"], d["vmax_arr"],
            d["Pgmin"], d["Pgmax"],
            d["Qgmin"], d["Qgmax"],
            suffix=f"_{t}",
            init_flat=options.init_flat,
        )

        step_constr = _make_step_constraints(
            theta_t, v_t, P_t, Q_t, p_t, q_t, Pg_t, Qg_t,
            d["G"], d["B"], d["E"], d["Z"],
            d["Cg"], Pd_series[t], Qd_series[t], d["ref"],
            d["pv"], d["status"], d["gen_bus"], d["gen"],
            enforce_vset=options.enforce_vset,
            VG_col=VG,
        )

        all_constr.extend(step_constr)
        total_cost = total_cost + poly_cost_expr(
            d["gencost"], d["baseMVA"] * Pg_t
        )

        theta_list.append(theta_t)
        v_list.append(v_t)
        P_list.append(P_t)
        Q_list.append(Q_t)
        p_list.append(p_t)
        q_list.append(q_t)
        Pg_list.append(Pg_t)
        Qg_list.append(Qg_t)

    all_constr.extend(coupling_constraints)
    prob = cp.Problem(cp.Minimize(total_cost), all_constr)

    variables = dict(
        theta=theta_list, v=v_list, P=P_list, Q=Q_list,
        p=p_list, q=q_list, Pg=Pg_list, Qg=Qg_list,
    )
    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"],
        ref=d["ref"], pv=d["pv"], ext_to_int=d["ext_to_int"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        Cg=d["Cg"],
        Pgmin=d["Pgmin"], Pgmax=d["Pgmax"],
        Qgmin=d["Qgmin"], Qgmax=d["Qgmax"],
        T=T,
        Pd_series=Pd_series,
        Qd_series=Qd_series,
    )
    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="ac", is_convex=False,
    )