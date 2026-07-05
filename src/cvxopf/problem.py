"""
CVXPY problem builders for AC-OPF using the DNLP framework.

Public API
----------
build_acopf(case, *, options)              -> OPFBuild   single time step
build_acopf_multistep(case, df_P, df_Q,
                      *, T, options,
                      coupling_constraints) -> OPFBuild   T time steps
"""

from __future__ import annotations

from dataclasses import dataclass, field

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

# MATPOWER column indices
BUS_TYPE = 1
VMIN     = 12
VMAX     = 11
PD       = 2
QD       = 3
GEN_BUS  = 0
GEN_STATUS = 7
PMIN     = 9
PMAX     = 8
QMIN     = 4
QMAX     = 3
VG       = 5


@dataclass
class OPFOptions:
    """
    Solver and formulation options for build_acopf / build_acopf_multistep.

    Attributes
    ----------
    enforce_vset : bool
        If True, pin PV and slack bus voltage magnitudes to the Vg setpoint
        declared in the gen table. Default False.
    sparsity_tol : float
        Entries of Ybus with |G| <= tol AND |B| <= tol are treated as
        structural zeros and excluded from DNLP trig constraints.
        Default 0.0 (exact sparsity).
    init_flat : bool
        If True, initialise theta = 0 and v = 1 (flat start) before
        returning. The caller may overwrite .value on any variable before
        calling prob.solve(). Default True.
    enforce_branch_limits : bool
        If True, enforce per-branch thermal (apparent power) limits via
        rateA. Not yet implemented; raises NotImplementedError. Default False.
    """
    enforce_vset:          bool  = False
    sparsity_tol:          float = 0.0
    init_flat:             bool  = True
    enforce_branch_limits: bool  = False


@dataclass
class OPFBuild:
    """
    Container returned by the problem builders.

    Attributes
    ----------
    prob : cp.Problem
        The CVXPY problem. Call prob.solve(solver=cp.IPOPT, nlp=True) to solve.
    variables : dict
        Named CVXPY variables.

        Single-step keys:
            theta, v, P, Q, p, q, Pg, Qg

        Multi-step keys (each value is a list of length T):
            theta, v, P, Q, p, q, Pg, Qg

    data : dict
        Pre-computed numpy arrays and metadata.
        Keys: baseMVA, nb, ng, ref, pv, ext_to_int,
              Ybus, G, B, E, Z, Pd, Qd, Cg,
              Pgmin, Pgmax, Qgmin, Qgmax
        Multi-step additionally contains: T, Pd_series, Qd_series
    """
    prob:      cp.Problem
    variables: dict
    data:      dict


def build_acopf(
    case: dict,
    *,
    options: OPFOptions | None = None,
) -> OPFBuild:
    """
    Build a single time-step AC-OPF problem in DNLP form.

    The formulation follows the DNLP paper (Cederberg, Zhang, Nobel, Boyd 2026):
    auxiliary (nb x nb) matrices P, Q express power flows via elementwise
    trig expressions on the Ybus sparsity pattern; nodal injections p, q
    are row sums of P, Q; generator variables Pg, Qg are linked to p, q
    via the incidence matrix Cg.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict. Need not be pre-reindexed.
    options : OPFOptions, optional
        Formulation and solver options. Defaults to OPFOptions().

    Returns
    -------
    build : OPFBuild
        Contains the cp.Problem, named variables, and pre-computed data.
        Call build.prob.solve(solver=cp.IPOPT, nlp=True) to solve.
        Warm-starting: set .value on any variable in build.variables before
        solving.
    """
    if options is None:
        options = OPFOptions()

    if options.enforce_branch_limits:
        raise NotImplementedError(
            "enforce_branch_limits is not yet implemented. "
            "It is planned for Milestone 4."
        )

    validate_case(case)
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    gen     = case["gen"]
    gencost = case["gencost"]
    nb      = bus.shape[0]
    ng      = gen.shape[0]

    Ybus = make_ybus_matpower(case)
    G    = np.real(Ybus)
    B    = np.imag(Ybus)
    E, Z = make_ybus_sparsity_mask(Ybus, tol=options.sparsity_tol)

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

    Cg = make_incidence_matrix(case)

    # --- variables ---
    theta = cp.Variable((nb, 1), name="theta")
    v     = cp.Variable((nb, 1), name="v",
                        bounds=[vmin_arr[:, None], vmax_arr[:, None]])
    P     = cp.Variable((nb, nb), name="P")
    Q     = cp.Variable((nb, nb), name="Q")
    p     = cp.Variable(nb, name="p")
    q     = cp.Variable(nb, name="q")
    Pg    = cp.Variable(ng, name="Pg", bounds=[Pgmin, Pgmax])
    Qg    = cp.Variable(ng, name="Qg", bounds=[Qgmin, Qgmax])

    if options.init_flat:
        theta.value = np.zeros((nb, 1))
        v.value     = np.ones((nb, 1))

    # --- DNLP trig expressions ---
    C   = cp.nlp.cos(theta - theta.T)
    S   = cp.nlp.sin(theta - theta.T)
    vvT = v @ v.T

    # --- constraints ---
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

    if options.enforce_vset:
        gen_bus = gen[:, GEN_BUS].astype(int)
        for b in np.r_[np.array([ref]), pv]:
            idx = np.where((gen_bus == int(b)) & (status == 1))[0]
            if idx.size:
                constr.append(v[int(b)] == float(gen[idx[0], VG]))

    # --- objective ---
    cost = poly_cost_expr(gencost, baseMVA * Pg)
    prob = cp.Problem(cp.Minimize(cost), constr)

    variables = dict(theta=theta, v=v, P=P, Q=Q, p=p, q=q, Pg=Pg, Qg=Qg)
    data = dict(
        baseMVA=baseMVA, nb=nb, ng=ng,
        ref=ref, pv=pv, ext_to_int=ext_to_int,
        Ybus=Ybus, G=G, B=B, E=E, Z=Z,
        Pd=Pd, Qd=Qd, Cg=Cg,
        Pgmin=Pgmin, Pgmax=Pgmax,
        Qgmin=Qgmin, Qgmax=Qgmax,
    )
    return OPFBuild(prob=prob, variables=variables, data=data)


def build_acopf_multistep(
    case: dict,
    df_P: pd.DataFrame,
    df_Q: pd.DataFrame,
    *,
    T: int,
    options: OPFOptions | None = None,
    coupling_constraints: list[cp.Constraint] | None = None,
) -> OPFBuild:
    """
    Build a T-step AC-OPF problem in DNLP form as a single cp.Problem.

    Each time step has its own independent set of network variables and
    constraints. The objective is the sum of per-step generation costs.
    Time coupling (e.g., battery state-of-charge dynamics) is supported via
    the coupling_constraints parameter; in v1 this list is empty.

    Parameters
    ----------
    case : dict
        MATPOWER-format case dict. Network topology is fixed across all steps.
    df_P : pd.DataFrame, shape (T, nb)
        Active load time series in MW. Row t is the load profile for step t.
    df_Q : pd.DataFrame, shape (T, nb)
        Reactive load time series in MVAr.
    T : int
        Number of time steps. Must equal df_P.shape[0].
    options : OPFOptions, optional
        Formulation and solver options. Applied identically to all steps.
    coupling_constraints : list of cp.Constraint, optional
        Additional constraints linking variables across time steps (e.g.,
        battery SoC dynamics). Appended to the problem without modification.
        Default: empty list.

    Returns
    -------
    build : OPFBuild
        build.variables contains lists of length T for each variable type:
            theta[t], v[t], P[t], Q[t], p[t], q[t], Pg[t], Qg[t]
        build.data additionally contains: T, Pd_series (T,nb), Qd_series (T,nb)

    Raises
    ------
    ValueError
        If T does not match df_P.shape[0].
    """
    if options is None:
        options = OPFOptions()
    if coupling_constraints is None:
        coupling_constraints = []

    if options.enforce_branch_limits:
        raise NotImplementedError(
            "enforce_branch_limits is not yet implemented. "
            "It is planned for Milestone 4."
        )

    validate_case(case)
    case, ext_to_int = reindex_case_to_consecutive(case)

    Pd_series, Qd_series = load_timeseries_from_dataframe(df_P, df_Q, case)

    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    gen     = case["gen"]
    gencost = case["gencost"]
    nb      = bus.shape[0]
    ng      = gen.shape[0]

    Ybus = make_ybus_matpower(case)
    G    = np.real(Ybus)
    B    = np.imag(Ybus)
    E, Z = make_ybus_sparsity_mask(Ybus, tol=options.sparsity_tol)

    ref_idx = np.where(bus[:, BUS_TYPE] == 3)[0]
    ref     = int(ref_idx[0])
    pv      = np.where(bus[:, BUS_TYPE] == 2)[0]

    vmin_arr = bus[:, VMIN].astype(float)
    vmax_arr = bus[:, VMAX].astype(float)

    status = gen[:, GEN_STATUS].astype(int)
    Pgmin  = gen[:, PMIN].astype(float) / baseMVA
    Pgmax  = gen[:, PMAX].astype(float) / baseMVA
    Qgmin  = gen[:, QMIN].astype(float) / baseMVA
    Qgmax  = gen[:, QMAX].astype(float) / baseMVA

    for k in range(ng):
        if status[k] != 1:
            Pgmin[k] = Pgmax[k] = Qgmin[k] = Qgmax[k] = 0.0

    Cg = make_incidence_matrix(case)

    # Per-step variable lists
    theta_list = []
    v_list     = []
    P_list     = []
    Q_list     = []
    p_list     = []
    q_list     = []
    Pg_list    = []
    Qg_list    = []

    all_constr = []
    total_cost = 0

    gen_bus = gen[:, GEN_BUS].astype(int)

    for t in range(T):
        theta_t = cp.Variable((nb, 1), name=f"theta_{t}")
        v_t     = cp.Variable((nb, 1), name=f"v_{t}",
                              bounds=[vmin_arr[:, None], vmax_arr[:, None]])
        P_t     = cp.Variable((nb, nb), name=f"P_{t}")
        Q_t     = cp.Variable((nb, nb), name=f"Q_{t}")
        p_t     = cp.Variable(nb, name=f"p_{t}")
        q_t     = cp.Variable(nb, name=f"q_{t}")
        Pg_t    = cp.Variable(ng, name=f"Pg_{t}", bounds=[Pgmin, Pgmax])
        Qg_t    = cp.Variable(ng, name=f"Qg_{t}", bounds=[Qgmin, Qgmax])

        if options.init_flat:
            theta_t.value = np.zeros((nb, 1))
            v_t.value     = np.ones((nb, 1))

        C_t   = cp.nlp.cos(theta_t - theta_t.T)
        S_t   = cp.nlp.sin(theta_t - theta_t.T)
        vvT_t = v_t @ v_t.T

        Pd_t = Pd_series[t]
        Qd_t = Qd_series[t]

        step_constr = [
            theta_t[ref] == 0.0,
            p_t == cp.sum(P_t, axis=1),
            q_t == cp.sum(Q_t, axis=1),
            P_t[E] == cp.multiply(
                vvT_t[E],
                cp.multiply(G[E], C_t[E]) + cp.multiply(B[E], S_t[E])
            ),
            Q_t[E] == cp.multiply(
                vvT_t[E],
                cp.multiply(G[E], S_t[E]) - cp.multiply(B[E], C_t[E])
            ),
            P_t[Z] == 0.0,
            Q_t[Z] == 0.0,
            p_t == Cg @ Pg_t - Pd_t,
            q_t == Cg @ Qg_t - Qd_t,
        ]

        if options.enforce_vset:
            for b in np.r_[np.array([ref]), pv]:
                idx = np.where((gen_bus == int(b)) & (status == 1))[0]
                if idx.size:
                    step_constr.append(v_t[int(b)] == float(gen[idx[0], VG]))

        all_constr.extend(step_constr)
        total_cost = total_cost + poly_cost_expr(gencost, baseMVA * Pg_t)

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
        baseMVA=baseMVA, nb=nb, ng=ng,
        ref=ref, pv=pv, ext_to_int=ext_to_int,
        Ybus=Ybus, G=G, B=B, E=E, Z=Z,
        Cg=Cg,
        Pgmin=Pgmin, Pgmax=Pgmax,
        Qgmin=Qgmin, Qgmax=Qgmax,
        T=T,
        Pd_series=Pd_series,
        Qd_series=Qd_series,
    )
    return OPFBuild(prob=prob, variables=variables, data=data)
