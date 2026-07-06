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

def _make_row_sum_matrix(rows: np.ndarray, cols: np.ndarray, nb: int) -> np.ndarray:
    """
    Build a (nb, nnz) constant numpy matrix Rp such that Rp @ x_vec
    gives the row sums of the (nb, nb) matrix whose nonzero entry at
    position k is (rows[k], cols[k]).

    Rp[i, k] = 1.0 if rows[k] == i, else 0.0.

    Used in the sparse P/Q formulation to express nodal injections
        p = Rp @ P_vec,  q = Rp @ Q_vec
    without materialising a dense (nb, nb) matrix variable.

    Parameters
    ----------
    rows : np.ndarray, shape (nnz,)
        Row indices of Ybus nonzero entries.
    cols : np.ndarray, shape (nnz,)
        Column indices of Ybus nonzero entries.
    nb : int
        Number of buses.

    Returns
    -------
    Rp : np.ndarray, shape (nb, nnz)
    """
    nnz = len(rows)
    Rp  = np.zeros((nb, nnz))
    for k in range(nnz):
        Rp[rows[k], k] = 1.0
    return Rp


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

    rows  = E[0]
    cols  = E[1]
    G_vec = G[rows, cols]
    B_vec = B[rows, cols]
    Rp    = _make_row_sum_matrix(rows, cols, nb)

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
        rows=rows, cols=cols, G_vec=G_vec, B_vec=B_vec, Rp=Rp,
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
    E,
    suffix: str,
    init_flat: bool,
    sparse_pq: bool,
):
    """
    Construct one set of per-step CVXPY variables.

    When sparse_pq=True, P and Q are represented as flat (nnz,) vectors
    P_vec and Q_vec over the Ybus sparsity pattern.
    When sparse_pq=False, P and Q are dense (nb, nb) matrices.

    Returns a tuple of length 8:
        (theta, v, PQ_P, PQ_Q, p, q, Pg, Qg)
    where PQ_P is either P_vec (nnz,) or P (nb, nb), and similarly for PQ_Q.
    """
    def name(s):
        return f"{s}{suffix}"

    theta = cp.Variable((nb, 1), name=name("theta"))
    v     = cp.Variable((nb, 1), name=name("v"),
                        bounds=[vmin_arr[:, None], vmax_arr[:, None]])
    p     = cp.Variable(nb, name=name("p"))
    q     = cp.Variable(nb, name=name("q"))
    Pg    = cp.Variable(ng, name=name("Pg"), bounds=[Pgmin, Pgmax])
    Qg    = cp.Variable(ng, name=name("Qg"), bounds=[Qgmin, Qgmax])

    if sparse_pq:
        nnz   = len(E[0])
        PQ_P  = cp.Variable(nnz, name=name("P_vec"))
        PQ_Q  = cp.Variable(nnz, name=name("Q_vec"))
    else:
        PQ_P  = cp.Variable((nb, nb), name=name("P"))
        PQ_Q  = cp.Variable((nb, nb), name=name("Q"))

    if init_flat:
        theta.value = np.zeros((nb, 1))
        v.value     = np.ones((nb, 1))

    return theta, v, PQ_P, PQ_Q, p, q, Pg, Qg


def _make_step_constraints(
    theta, v, PQ_P, PQ_Q, p, q, Pg, Qg,
    G, B, E, Z,
    rows, cols, G_vec, B_vec, Rp,
    Cg, Pd, Qd, ref,
    pv, status, gen_bus, gen,
    enforce_vset: bool,
    VG_col: int,
    sparse_pq: bool,
) -> list:
    """
    Build the list of CVXPY constraints for one AC time step.

    Branches on sparse_pq:
      True  — PQ_P and PQ_Q are flat (nnz,) vectors; nodal injections
              use the scatter matrix Rp @ PQ_P.
      False — PQ_P and PQ_Q are dense (nb, nb) matrices; nodal injections
              use cp.sum(..., axis=1). Identical to pre-Milestone 9 behaviour.
    """
    constr = [
        theta[ref] == 0.0,
        p == Cg @ Pg - Pd,
        q == Cg @ Qg - Qd,
    ]

    if sparse_pq:
        # TODO: vectorize once https://github.com/cvxpy/cvxpy/issues/3442 is
        # resolved. The natural vectorised form:
        #
        #   C_vec  = cp.nlp.cos(theta[rows, 0] - theta[cols, 0])
        #   S_vec  = cp.nlp.sin(theta[rows, 0] - theta[cols, 0])
        #   vv_vec = cp.multiply(v[rows, 0], v[cols, 0])
        #   constr += [PQ_P == cp.multiply(vv_vec, ...),
        #              PQ_Q == cp.multiply(vv_vec, ...)]
        #
        # crashes inside init_hessian_coo_lower_tri because numpy array
        # indexing of a CVXPY variable produces a compound gather expression
        # that the DNLP Hessian sparsity analyser cannot handle. Scalar
        # integer indexing in a loop works correctly.
        nnz = len(rows)
        for k in range(nnz):
            i   = int(rows[k])
            j   = int(cols[k])
            C_k = cp.nlp.cos(theta[i, 0] - theta[j, 0])
            S_k = cp.nlp.sin(theta[i, 0] - theta[j, 0])
            vv_k = v[i, 0] * v[j, 0]
            constr.append(
                PQ_P[k] == vv_k * (float(G_vec[k]) * C_k + float(B_vec[k]) * S_k)
            )
            constr.append(
                PQ_Q[k] == vv_k * (float(G_vec[k]) * S_k - float(B_vec[k]) * C_k)
            )

        constr += [
            p == Rp @ PQ_P,
            q == Rp @ PQ_Q,
        ]
    else:
        C   = cp.nlp.cos(theta - theta.T)
        S   = cp.nlp.sin(theta - theta.T)
        vvT = v @ v.T

        constr += [
            p == cp.sum(PQ_P, axis=1),
            q == cp.sum(PQ_Q, axis=1),
            PQ_P[E] == cp.multiply(
                vvT[E],
                cp.multiply(G[E], C[E]) + cp.multiply(B[E], S[E])
            ),
            PQ_Q[E] == cp.multiply(
                vvT[E],
                cp.multiply(G[E], S[E]) - cp.multiply(B[E], C[E])
            ),
            PQ_P[Z] == 0.0,
            PQ_Q[Z] == 0.0,
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

    theta, v, PQ_P, PQ_Q, p, q, Pg, Qg = _make_step_variables(
        d["nb"], d["ng"],
        d["vmin_arr"], d["vmax_arr"],
        d["Pgmin"], d["Pgmax"],
        d["Qgmin"], d["Qgmax"],
        E=d["E"],
        suffix="",
        init_flat=options.init_flat,
        sparse_pq=options.sparse_pq,
    )

    constr = _make_step_constraints(
        theta, v, PQ_P, PQ_Q, p, q, Pg, Qg,
        d["G"], d["B"], d["E"], d["Z"],
        d["rows"], d["cols"], d["G_vec"], d["B_vec"], d["Rp"],
        d["Cg"], d["Pd"], d["Qd"], d["ref"],
        d["pv"], d["status"], d["gen_bus"], d["gen"],
        enforce_vset=options.enforce_vset,
        VG_col=VG,
        sparse_pq=options.sparse_pq,
    )

    cost = poly_cost_expr(d["gencost"], d["baseMVA"] * Pg)
    prob = cp.Problem(cp.Minimize(cost), constr)

    if options.sparse_pq:
        variables = dict(theta=theta, v=v, P_vec=PQ_P, Q_vec=PQ_Q,
                         p=p, q=q, Pg=Pg, Qg=Qg)
    else:
        variables = dict(theta=theta, v=v, P=PQ_P, Q=PQ_Q,
                         p=p, q=q, Pg=Pg, Qg=Qg)

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"],
        ref=d["ref"], pv=d["pv"], ext_to_int=d["ext_to_int"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        rows=d["rows"], cols=d["cols"], G_vec=d["G_vec"],
        B_vec=d["B_vec"], Rp=d["Rp"],
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

    theta_list, v_list, PQ_P_list, PQ_Q_list = [], [], [], []
    p_list, q_list, Pg_list, Qg_list          = [], [], [], []
    all_constr  = []
    total_cost  = 0

    for t in range(T):
        theta_t, v_t, PQ_P_t, PQ_Q_t, p_t, q_t, Pg_t, Qg_t = \
            _make_step_variables(
                d["nb"], d["ng"],
                d["vmin_arr"], d["vmax_arr"],
                d["Pgmin"], d["Pgmax"],
                d["Qgmin"], d["Qgmax"],
                E=d["E"],
                suffix=f"_{t}",
                init_flat=options.init_flat,
                sparse_pq=options.sparse_pq,
            )

        step_constr = _make_step_constraints(
            theta_t, v_t, PQ_P_t, PQ_Q_t, p_t, q_t, Pg_t, Qg_t,
            d["G"], d["B"], d["E"], d["Z"],
            d["rows"], d["cols"], d["G_vec"], d["B_vec"], d["Rp"],
            d["Cg"], Pd_series[t], Qd_series[t], d["ref"],
            d["pv"], d["status"], d["gen_bus"], d["gen"],
            enforce_vset=options.enforce_vset,
            VG_col=VG,
            sparse_pq=options.sparse_pq,
        )

        all_constr.extend(step_constr)
        total_cost = total_cost + poly_cost_expr(
            d["gencost"], d["baseMVA"] * Pg_t
        )

        theta_list.append(theta_t)
        v_list.append(v_t)
        PQ_P_list.append(PQ_P_t)
        PQ_Q_list.append(PQ_Q_t)
        p_list.append(p_t)
        q_list.append(q_t)
        Pg_list.append(Pg_t)
        Qg_list.append(Qg_t)

    all_constr.extend(coupling_constraints)
    prob = cp.Problem(cp.Minimize(total_cost), all_constr)

    if options.sparse_pq:
        variables = dict(
            theta=theta_list, v=v_list,
            P_vec=PQ_P_list, Q_vec=PQ_Q_list,
            p=p_list, q=q_list, Pg=Pg_list, Qg=Qg_list,
        )
    else:
        variables = dict(
            theta=theta_list, v=v_list,
            P=PQ_P_list, Q=PQ_Q_list,
            p=p_list, q=q_list, Pg=Pg_list, Qg=Qg_list,
        )

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"],
        ref=d["ref"], pv=d["pv"], ext_to_int=d["ext_to_int"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        rows=d["rows"], cols=d["cols"], G_vec=d["G_vec"],
        B_vec=d["B_vec"], Rp=d["Rp"],
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