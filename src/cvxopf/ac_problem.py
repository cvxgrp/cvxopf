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
    make_ybus_sparsity_mask,
)
from cvxopf.data import validate_case, load_timeseries_from_dataframe
from cvxopf.generator import (
    DispatchableGenerator,
    _validate_generators,
    gen_from_matpower,
    generator_bounds,
    generator_gencost,
    injections as generator_injections,
    make_generator_incidence,
    ac_operating_constraints as generator_ac_operating_constraints,
    gen_cost_expr,
)
from cvxopf.storage import (
    StorageUnitIdeal,
    _validate_storage,
    _make_storage_incidence_matrix,
    _storage_static_data,
    ac_injections as storage_ac_injections,
    ac_operating_constraints as storage_ac_operating_constraints,
    coupling_constraints as storage_coupling_constraints,
    storage_cost_expr,
)
from cvxopf.nondispatchable import (
    NondispatchableUnit,
    _validate_nondispatchable,
    _make_nd_incidence_matrix,
    _nd_static_data,
    _parse_nd_timeseries,
    ac_injections as nd_ac_injections,
    ac_operating_constraints as nd_ac_operating_constraints,
)
from cvxopf.hvdc import (
    HVDCLink,
    _validate_hvdc,
    _make_hvdc_incidence_matrices,
    _hvdc_static_box,
    ac_injections as hvdc_ac_injections,
    ac_operating_constraints as hvdc_ac_operating_constraints,
    hvdc_cost_expr,
)

# ---------------------------------------------------------------------------
# MATPOWER column indices
# ---------------------------------------------------------------------------

BUS_TYPE   = 1
VMIN       = 12
VMAX       = 11
PD         = 2
QD         = 3


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


def _parse_case(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    hvdc: list[HVDCLink] | None = None,
    generators: list[DispatchableGenerator] | None = None,
) -> dict:
    """
    Validate, reindex, and extract all numpy data from a case dict.
    Returns a flat dict consumed by the AC single-step and multistep builders.
    """
    validate_case(case)
    if generators is None:
        generators = gen_from_matpower(case["gen"], case["gencost"])
    case, ext_to_int = reindex_case_to_consecutive(case)

    baseMVA = float(case["baseMVA"])
    bus     = case["bus"]
    nb      = bus.shape[0]
    ng      = len(generators)

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

    # Get external bus IDs for validation (needed for both storage and nondispatchable)
    if ext_to_int is not None:
        ext_bus_ids = set(ext_to_int.keys())
    else:
        ext_bus_ids = set(bus[:, 0].astype(int).tolist())

    _validate_generators(generators, ext_bus_ids)
    Pgmin, Pgmax, Qgmin, Qgmax = generator_bounds(generators, baseMVA)
    Cg = make_generator_incidence(generators, nb, ext_to_int)
    gen_bus = np.array([ext_to_int[g.bus] for g in generators], dtype=int)
    status = np.array([g.status for g in generators], dtype=int)
    vg = np.array([g.vg for g in generators], dtype=float)
    gencost = generator_gencost(generators)
    
    # Parse storage if present
    storage_data = {}
    if storage is not None:
        _validate_storage(storage, ext_bus_ids)
    
    # Validate nondispatchable units
    if nondispatchable is not None and len(nondispatchable) > 0:
        _validate_nondispatchable(nondispatchable, ext_bus_ids)
    
    # Parse storage if present (continued)
    if storage is not None:
        # Create storage incidence matrix
        Cs = _make_storage_incidence_matrix(storage, nb, ext_to_int)
        
        # Extract storage parameters
        storage_bus = np.array([
            ext_to_int[u.bus] if ext_to_int else u.bus
            for u in storage
        ], dtype=int)
        storage_data = dict(
            ns=len(storage),
            Cs=Cs,
            storage_bus=storage_bus,
            storage_delta=float(delta),
            **_storage_static_data(storage),
        )

    # Parse nondispatchable if present
    nd_data = {}
    if nondispatchable is not None and len(nondispatchable) > 0:
        # Validate nondispatchable units
        _validate_nondispatchable(nondispatchable, ext_bus_ids)
        
        # Create nondispatchable incidence matrix
        Cnd = _make_nd_incidence_matrix(nondispatchable, nb, ext_to_int)
        
        # Extract nondispatchable parameters
        nd_bus = np.array([
            ext_to_int[u.bus] if ext_to_int else u.bus
            for u in nondispatchable
        ], dtype=int)
        nd_data = dict(
            nnd=len(nondispatchable),
            Cnd=Cnd,
            nd_bus=nd_bus,
            **_nd_static_data(nondispatchable),
        )

    # Parse HVDC links if present
    hvdc_data = {}
    if hvdc is not None and len(hvdc) > 0:
        _validate_hvdc(hvdc, ext_bus_ids)
        Ch_from, Ch_to = _make_hvdc_incidence_matrices(hvdc, nb, ext_to_int)
        hvdc_data = dict(
            n_hvdc=len(hvdc),
            Ch_from=Ch_from,
            Ch_to=Ch_to,
        )

    return dict(
        case=case, baseMVA=baseMVA,
        bus=bus, generators=generators, gencost=gencost,
        nb=nb, ng=ng,
        Ybus=Ybus, G=G, B=B, E=E, Z=Z,
        rows=rows, cols=cols, G_vec=G_vec, B_vec=B_vec, Rp=Rp,
        ref=ref, pv=pv, ext_to_int=ext_to_int,
        ext_bus_ids=ext_bus_ids,
        vmin_arr=vmin_arr, vmax_arr=vmax_arr,
        Pd=Pd, Qd=Qd,
        status=status, gen_bus=gen_bus, vg=vg,
        Pgmin=Pgmin, Pgmax=Pgmax,
        Qgmin=Qgmin, Qgmax=Qgmax,
        Cg=Cg,
        **storage_data,
        **nd_data,
        **hvdc_data,
    )


def _make_step_variables(
    nb: int, ng: int,
    vmin_arr, vmax_arr,
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
    Pg    = cp.Variable(ng, name=name("Pg"))
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
    Cg, generator_injection, Pgmin, Pgmax, Pd, Qd, ref,
    pv, status, gen_bus, vg,
    enforce_vset: bool,
    sparse_pq: bool,
    baseMVA: float,
    # Storage — all None when storage=None
    ns: int = 0,
    storage_units=None,
    storage_injection_p=None,
    storage_injection_q=None,
    b_t=None,
    b_q_t=None,
    soc_t=None,
    # Nondispatchable — all None when nondispatchable=None
    nnd: int = 0,
    nd_units=None,
    nd_injection_p=None,
    nd_injection_q=None,
    nd_p_available_t=None,
    p_nd_t=None,
    q_nd_t=None,
    # HVDC — all None/0 when hvdc=None
    n_hvdc: int = 0,
    hvdc_injection_expr=None,
    links=None,
    p_in_t=None,
    p_out_t=None,
    p_min_hvdc_t=None,
    p_max_hvdc_t=None,
    step: int = 0,
) -> list:
    """
    Build the complete list of CVXPY constraints for one AC time step.

    Internal structure (seven sections — do not reorder or split):
      1. Reference bus angle fix
      2. Power flow definitions: p and q from P/Q matrix (sparse or dense)
      3. Nodal power balance: exactly one p== and one q== constraint,
         incorporating storage, nondispatchable, and HVDC injection if present.
         HVDC enters p== only (unity power factor; q== is untouched).
      4. Storage operating constraints (apparent power circle, SoC bounds)
         — omitted when ns==0
      4b. Nondispatchable operating constraints (apparent power circle, real power bounds)
          — omitted when nnd==0
      4c. HVDC operating constraints (box bounds, loss-branch equality)
          — omitted when n_hvdc==0
      5. Voltage setpoint pinning — omitted when enforce_vset=False

    The caller must not append additional p== or q== constraints after
    this function returns.
    """
    # ------------------------------------------------------------------
    # Section 1: Reference bus
    # ------------------------------------------------------------------
    constr = [theta[ref] == 0.0]

    # ------------------------------------------------------------------
    # Section 2: Flow definitions — p and q from P/Q matrix
    # ------------------------------------------------------------------
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
            p == cp.sum(PQ_P, axis=1),
            q == cp.sum(PQ_Q, axis=1),
        ]

    # ------------------------------------------------------------------
    # Section 3: Nodal power balance
    # Exactly one p== and one q== constraint.
    # Storage and nondispatchable injection added here if present.
    # ------------------------------------------------------------------
    storage_injection_p = storage_injection_p if ns > 0 else 0
    storage_injection_q = storage_injection_q if ns > 0 else 0
    nd_injection_p = nd_injection_p if nnd > 0 else 0
    nd_injection_q = nd_injection_q if nnd > 0 else 0
    hvdc_injection_p = hvdc_injection_expr if n_hvdc > 0 else 0

    constr.append(
        p == generator_injection - Pd
        + storage_injection_p + nd_injection_p + hvdc_injection_p
    )
    constr.append(q == Cg @ Qg - Qd + storage_injection_q + nd_injection_q)
    constr += generator_ac_operating_constraints(Pg, Pgmin, Pgmax)

    # ------------------------------------------------------------------
    # Section 4: Storage operating constraints
    # Apparent power circle (AC) and SoC bounds.
    # Omitted entirely when ns == 0.
    # ------------------------------------------------------------------
    if ns > 0:
        constr += storage_ac_operating_constraints(
            storage_units, b_t, b_q_t, soc_t
        )

    # ------------------------------------------------------------------
    # Section 4b: Nondispatchable operating constraints
    # Apparent power circle and real power bounds.
    # Omitted entirely when nnd == 0.
    # ------------------------------------------------------------------
    if nnd > 0:
        constr += nd_ac_operating_constraints(
            nd_units, p_nd_t, q_nd_t, nd_p_available_t
        )

    # ------------------------------------------------------------------
    # Section 4c: HVDC operating constraints
    # Box bounds (p_min_t <= p_in <= p_max_t) and loss-branch equality
    # (p_out == coeff_vec * p_in). Omitted entirely when n_hvdc == 0.
    # ------------------------------------------------------------------
    if n_hvdc > 0:
        constr += hvdc_ac_operating_constraints(
            links, p_in_t, p_out_t, p_min_hvdc_t, p_max_hvdc_t, step
        )

    # ------------------------------------------------------------------
    # Section 5: Voltage setpoint pinning
    # Omitted when enforce_vset=False.
    # ------------------------------------------------------------------
    if enforce_vset:
        for b in np.r_[np.array([ref]), pv]:
            idx = np.where((gen_bus == int(b)) & (status == 1))[0]
            if idx.size:
                constr.append(v[int(b)] == float(vg[idx[0]]))

    return constr


# ---------------------------------------------------------------------------
# Public builders (called from problem.py dispatch)
# ---------------------------------------------------------------------------

def _build_ac_single(
    case: dict,
    options,
    storage: list[StorageUnitIdeal] | None = None,
    delta: float = 1.0,
    nondispatchable: list[NondispatchableUnit] | None = None,
    *,
    hvdc=None,
    generators: list[DispatchableGenerator] | None = None,
) -> "OPFBuild":
    """Build a single time-step AC-OPF problem."""
    from cvxopf.problem import OPFBuild

    if options.enforce_branch_limits:
        raise NotImplementedError(
            "enforce_branch_limits is not yet implemented. "
            "It is planned for Milestone 4."
        )

    d = _parse_case(
        case, options, storage, delta, nondispatchable, hvdc, generators
    )

    # Create step variables
    theta, v, PQ_P, PQ_Q, p, q, Pg, Qg = _make_step_variables(
        d["nb"], d["ng"],
        d["vmin_arr"], d["vmax_arr"],
        d["Qgmin"], d["Qgmax"],
        E=d["E"],
        suffix="",
        init_flat=options.init_flat,
        sparse_pq=options.sparse_pq,
    )

    # Create storage variables if present
    b_t = b_q_t = soc_t = None
    storage_inj_p = storage_inj_q = None
    if "ns" in d and d["ns"] > 0:
        ns = d["ns"]
        # b_t: real power (MW), b_q_t: reactive power (MVAr), soc_t: state of charge (MWh)
        b_t = cp.Variable(ns, name="b")
        b_q_t = cp.Variable(ns, name="b_q")
        soc_t = cp.Variable(ns, name="soc")
        storage_inj_p, storage_inj_q, storage_scaling = storage_ac_injections(
            storage, b_t, b_q_t, d["ext_to_int"]
        )
        storage_scaling.value = 1.0 / d["baseMVA"]

    # Create nondispatchable variables if present
    p_nd_t = q_nd_t = None
    nd_inj_p = nd_inj_q = None
    if "nnd" in d and d["nnd"] > 0:
        nnd = d["nnd"]
        # p_nd_t: real power (MW), q_nd_t: reactive power (MVAr)
        p_nd_t = cp.Variable(nnd, name="p_nd", nonneg=True)
        q_nd_t = cp.Variable(nnd, name="q_nd")
        nd_inj_p, nd_inj_q, nd_scaling = nd_ac_injections(
            nondispatchable, p_nd_t, q_nd_t, d["ext_to_int"]
        )
        nd_scaling.value = 1.0 / d["baseMVA"]

    # Create HVDC variables if present
    p_in = p_out = None
    hvdc_inj_expr = None
    if "n_hvdc" in d:
        n_hvdc = d["n_hvdc"]
        p_in  = cp.Variable((n_hvdc,), name="p_hvdc_in")
        p_out = cp.Variable((n_hvdc,), name="p_hvdc_out")
        hvdc_inj_expr, hvdc_q_inj, inv_bMVA = hvdc_ac_injections(
            hvdc, p_in, p_out, d["ext_to_int"]
        )
        assert hvdc_q_inj is None
        inv_bMVA.value = 1.0 / d["baseMVA"]
        p_min_hvdc, p_max_hvdc = _hvdc_static_box(hvdc)

    generator_inj_expr, generator_scaling = generator_injections(
        d["generators"], Pg, d["ext_to_int"]
    )
    assert generator_scaling is None

    constr = _make_step_constraints(
        theta, v, PQ_P, PQ_Q, p, q, Pg, Qg,
        d["G"], d["B"], d["E"], d["Z"],
        d["rows"], d["cols"], d["G_vec"], d["B_vec"], d["Rp"],
        d["Cg"], generator_inj_expr, d["Pgmin"], d["Pgmax"],
        d["Pd"], d["Qd"], d["ref"],
        d["pv"], d["status"], d["gen_bus"], d["vg"],
        enforce_vset=options.enforce_vset,
        sparse_pq=options.sparse_pq,
        baseMVA=d["baseMVA"],
        ns=d.get("ns", 0),
        storage_units=storage,
        storage_injection_p=storage_inj_p,
        storage_injection_q=storage_inj_q,
        b_t=b_t,
        b_q_t=b_q_t,
        soc_t=soc_t,
        nnd=d.get("nnd", 0),
        nd_units=nondispatchable,
        nd_injection_p=nd_inj_p,
        nd_injection_q=nd_inj_q,
        nd_p_available_t=d.get("nd_p_available"),
        p_nd_t=p_nd_t,
        q_nd_t=q_nd_t,
        n_hvdc=d.get("n_hvdc", 0),
        hvdc_injection_expr=hvdc_inj_expr,
        links=hvdc,
        p_in_t=p_in,
        p_out_t=p_out,
        p_min_hvdc_t=p_min_hvdc if "n_hvdc" in d else None,
        p_max_hvdc_t=p_max_hvdc if "n_hvdc" in d else None,
        step=0,
    )

    # Build cost: generation cost plus storage aging cost plus HVDC cost
    gen_cost = gen_cost_expr(d["gencost"], d["baseMVA"] * Pg)
    if "ns" in d and d["ns"] > 0:
        total_cost = gen_cost + storage_cost_expr(storage, b_t)
    else:
        total_cost = gen_cost
    if "n_hvdc" in d:
        for k in range(d["n_hvdc"]):
            total_cost = total_cost + hvdc_cost_expr(hvdc[k].cost_coeffs, p_in[k])
    
    # Add storage SoC dynamics constraints if present
    if "ns" in d and d["ns"] > 0:
        storage_coupling = storage_coupling_constraints(
            storage, [b_t], [soc_t], d["storage_delta"]
        )
        constr.extend(storage_coupling)
    
    prob = cp.Problem(cp.Minimize(total_cost), constr)

    # Build variables dict
    if options.sparse_pq:
        variables = dict(theta=theta, v=v, P_vec=PQ_P, Q_vec=PQ_Q,
                         p=p, q=q, Pg=Pg, Qg=Qg)
    else:
        variables = dict(theta=theta, v=v, P=PQ_P, Q=PQ_Q,
                         p=p, q=q, Pg=Pg, Qg=Qg)
    
    # Add storage variables if present
    if "ns" in d and d["ns"] > 0:
        variables["b"] = b_t
        variables["b_q"] = b_q_t
        variables["soc"] = soc_t

    # Add nondispatchable variables if present
    if "nnd" in d and d["nnd"] > 0:
        variables["p_nd"] = p_nd_t
        variables["q_nd"] = q_nd_t

    # Add HVDC variables if present
    if "n_hvdc" in d:
        variables["p_hvdc_in"]  = p_in
        variables["p_hvdc_out"] = p_out

    # Build data dict
    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"],
        ref=d["ref"], pv=d["pv"], ext_to_int=d["ext_to_int"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        rows=d["rows"], cols=d["cols"], G_vec=d["G_vec"],
        B_vec=d["B_vec"], Rp=d["Rp"],
        Pd=d["Pd"], Qd=d["Qd"], Cg=d["Cg"],
        gen_bus=d["gen_bus"], gencost=d["gencost"],
        Pgmin=d["Pgmin"], Pgmax=d["Pgmax"],
        Qgmin=d["Qgmin"], Qgmax=d["Qgmax"],
    )

    # Add storage data if present
    if "ns" in d:
        data.update(
            ns=d["ns"],
            Cs=d["Cs"],
            storage_bus=d["storage_bus"],
            storage_apparent_power_rating=d["storage_apparent_power_rating"],
            storage_capacity=d["storage_capacity"],
            storage_initial_soc=d["storage_initial_soc"],
            storage_delta=d["storage_delta"],
            storage_aging_weight=d["storage_aging_weight"],
        )

    # Add nondispatchable data if present
    if "nnd" in d:
        data.update(
            nnd=d["nnd"],
            Cnd=d["Cnd"],
            nd_bus=d["nd_bus"],
            nd_apparent_power_rating=d["nd_apparent_power_rating"],
            nd_p_available=d["nd_p_available"],
        )

    # Add HVDC data if present
    if "n_hvdc" in d:
        data.update(
            n_hvdc=d["n_hvdc"],
            Ch_from=d["Ch_from"],
            Ch_to=d["Ch_to"],
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
    """Build a T-step AC-OPF problem as a single cp.Problem."""
    from cvxopf.problem import OPFBuild

    if options.enforce_branch_limits:
        raise NotImplementedError(
            "enforce_branch_limits is not yet implemented. "
            "It is planned for Milestone 4."
        )

    d = _parse_case(
        case, options, storage, delta, nondispatchable, hvdc, generators
    )
    Pd_series, Qd_series = load_timeseries_from_dataframe(df_P, df_Q, case)
    
    # Parse nondispatchable timeseries if present
    if "nnd" in d:
        if df_nd is not None:
            d["nd_available"] = _parse_nd_timeseries(df_nd, T, nondispatchable)
        else:
            d["nd_available"] = np.tile(d["nd_p_available"], (T, 1))

    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    # Initialize lists for variables
    theta_list, v_list, PQ_P_list, PQ_Q_list = [], [], [], []
    p_list, q_list, Pg_list, Qg_list          = [], [], [], []
    b_list, b_q_list, soc_list               = [], [], []
    p_nd_list, q_nd_list                     = [], []
    p_hvdc_in_list, p_hvdc_out_list          = [], []
    all_constr  = []
    total_cost  = 0

    for t in range(T):
        # Create step variables
        theta_t, v_t, PQ_P_t, PQ_Q_t, p_t, q_t, Pg_t, Qg_t = \
            _make_step_variables(
                d["nb"], d["ng"],
                d["vmin_arr"], d["vmax_arr"],
                d["Qgmin"], d["Qgmax"],
                E=d["E"],
                suffix=f"_{t}",
                init_flat=options.init_flat,
                sparse_pq=options.sparse_pq,
            )

        # Create storage variables if present
        b_t = b_q_t = soc_t = None
        storage_inj_p_t = storage_inj_q_t = None
        if "ns" in d and d["ns"] > 0:
            ns = d["ns"]
            b_t = cp.Variable(ns, name=f"b_{t}")
            b_q_t = cp.Variable(ns, name=f"b_q_{t}")
            soc_t = cp.Variable(ns, name=f"soc_{t}")
            (
                storage_inj_p_t,
                storage_inj_q_t,
                storage_scaling_t,
            ) = storage_ac_injections(
                storage, b_t, b_q_t, d["ext_to_int"]
            )
            storage_scaling_t.value = 1.0 / d["baseMVA"]

        # Create nondispatchable variables if present
        p_nd_t = q_nd_t = None
        nd_inj_p_t = nd_inj_q_t = None
        if "nnd" in d and d["nnd"] > 0:
            nnd = d["nnd"]
            p_nd_t = cp.Variable(nnd, name=f"p_nd_{t}", nonneg=True)
            q_nd_t = cp.Variable(nnd, name=f"q_nd_{t}")
            nd_inj_p_t, nd_inj_q_t, nd_scaling_t = nd_ac_injections(
                nondispatchable, p_nd_t, q_nd_t, d["ext_to_int"]
            )
            nd_scaling_t.value = 1.0 / d["baseMVA"]

        # Create HVDC variables if present
        p_in_t = p_out_t = None
        hvdc_inj_expr_t = None
        p_min_hvdc_t = p_max_hvdc_t = None
        if "n_hvdc" in d:
            n_hvdc = d["n_hvdc"]
            p_in_t  = cp.Variable((n_hvdc,), name=f"p_hvdc_in_{t}")
            p_out_t = cp.Variable((n_hvdc,), name=f"p_hvdc_out_{t}")
            hvdc_inj_expr_t, hvdc_q_inj_t, inv_bMVA_t = hvdc_ac_injections(
                hvdc, p_in_t, p_out_t, d["ext_to_int"]
            )
            assert hvdc_q_inj_t is None
            inv_bMVA_t.value = 1.0 / d["baseMVA"]
            p_min_hvdc_t = df_hvdc_min.iloc[t].values.astype(float)
            p_max_hvdc_t = df_hvdc_max.iloc[t].values.astype(float)

        # Get available power for this time step
        if "nnd" in d:
            nd_p_available_t = (
                d["nd_available"][t, :]
                if "nd_available" in d else d["nd_p_available"]
            )
        else:
            nd_p_available_t = None

        generator_inj_expr_t, generator_scaling_t = generator_injections(
            d["generators"], Pg_t, d["ext_to_int"]
        )
        assert generator_scaling_t is None

        step_constr = _make_step_constraints(
            theta_t, v_t, PQ_P_t, PQ_Q_t, p_t, q_t, Pg_t, Qg_t,
            d["G"], d["B"], d["E"], d["Z"],
            d["rows"], d["cols"], d["G_vec"], d["B_vec"], d["Rp"],
            d["Cg"], generator_inj_expr_t, d["Pgmin"], d["Pgmax"],
            Pd_series[t], Qd_series[t], d["ref"],
            d["pv"], d["status"], d["gen_bus"], d["vg"],
            enforce_vset=options.enforce_vset,
            sparse_pq=options.sparse_pq,
            baseMVA=d["baseMVA"],
            ns=d.get("ns", 0),
            storage_units=storage,
            storage_injection_p=storage_inj_p_t,
            storage_injection_q=storage_inj_q_t,
            b_t=b_t,
            b_q_t=b_q_t,
            soc_t=soc_t,
            nnd=d.get("nnd", 0),
            nd_units=nondispatchable,
            nd_injection_p=nd_inj_p_t,
            nd_injection_q=nd_inj_q_t,
            nd_p_available_t=nd_p_available_t,
            p_nd_t=p_nd_t,
            q_nd_t=q_nd_t,
            n_hvdc=d.get("n_hvdc", 0),
            hvdc_injection_expr=hvdc_inj_expr_t,
            links=hvdc,
            p_in_t=p_in_t,
            p_out_t=p_out_t,
            p_min_hvdc_t=p_min_hvdc_t,
            p_max_hvdc_t=p_max_hvdc_t,
            step=t,
        )

        all_constr.extend(step_constr)

        # Add generation cost and HVDC cost (inside loop, per-step)
        gen_cost = gen_cost_expr(d["gencost"], d["baseMVA"] * Pg_t)
        total_cost = total_cost + gen_cost
        if "n_hvdc" in d:
            for k in range(d["n_hvdc"]):
                total_cost = total_cost + hvdc_cost_expr(hvdc[k].cost_coeffs, p_in_t[k])

        theta_list.append(theta_t)
        v_list.append(v_t)
        PQ_P_list.append(PQ_P_t)
        PQ_Q_list.append(PQ_Q_t)
        p_list.append(p_t)
        q_list.append(q_t)
        Pg_list.append(Pg_t)
        Qg_list.append(Qg_t)
        
        # Add storage variables to lists
        if "ns" in d and d["ns"] > 0:
            b_list.append(b_t)
            b_q_list.append(b_q_t)
            soc_list.append(soc_t)

        # Add nondispatchable variables to lists
        if "nnd" in d and d["nnd"] > 0:
            p_nd_list.append(p_nd_t)
            q_nd_list.append(q_nd_t)

        # Add HVDC variables to lists
        if "n_hvdc" in d:
            p_hvdc_in_list.append(p_in_t)
            p_hvdc_out_list.append(p_out_t)

    # Add storage aging cost if present
    if "ns" in d and d["ns"] > 0:
        for t in range(T):
            total_cost = total_cost + storage_cost_expr(storage, b_list[t])

    # Add storage SoC dynamics constraints if present
    if "ns" in d and d["ns"] > 0:
        storage_coupling = storage_coupling_constraints(
            storage, b_list, soc_list, d["storage_delta"]
        )
        all_constr.extend(storage_coupling)

    all_constr.extend(coupling_constraints)
    prob = cp.Problem(cp.Minimize(total_cost), all_constr)

    # Build variables dict
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
    
    # Add storage variables if present
    if "ns" in d and d["ns"] > 0:
        variables["b"] = b_list
        variables["b_q"] = b_q_list
        variables["soc"] = soc_list

    # Add nondispatchable variables if present
    if "nnd" in d and d["nnd"] > 0:
        variables["p_nd"] = p_nd_list
        variables["q_nd"] = q_nd_list

    # Add HVDC variables if present
    if "n_hvdc" in d:
        variables["p_hvdc_in"] = p_hvdc_in_list
        variables["p_hvdc_out"] = p_hvdc_out_list

    # Build data dict
    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"],
        ref=d["ref"], pv=d["pv"], ext_to_int=d["ext_to_int"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        rows=d["rows"], cols=d["cols"], G_vec=d["G_vec"],
        B_vec=d["B_vec"], Rp=d["Rp"],
        Cg=d["Cg"],
        gen_bus=d["gen_bus"], gencost=d["gencost"],
        Pgmin=d["Pgmin"], Pgmax=d["Pgmax"],
        Qgmin=d["Qgmin"], Qgmax=d["Qgmax"],
        T=T,
        Pd_series=Pd_series,
        Qd_series=Qd_series,
    )
    
    # Add storage data if present
    if "ns" in d:
        data.update(
            ns=d["ns"],
            Cs=d["Cs"],
            storage_bus=d["storage_bus"],
            storage_apparent_power_rating=d["storage_apparent_power_rating"],
            storage_capacity=d["storage_capacity"],
            storage_initial_soc=d["storage_initial_soc"],
            storage_delta=d["storage_delta"],
            storage_aging_weight=d["storage_aging_weight"],
        )

    # Add nondispatchable data if present
    if "nnd" in d:
        data.update(
            nnd=d["nnd"],
            Cnd=d["Cnd"],
            nd_bus=d["nd_bus"],
            nd_apparent_power_rating=d["nd_apparent_power_rating"],
            nd_available=d.get("nd_available"),  # Only present in multistep
        )

    # Add HVDC data if present
    if "n_hvdc" in d:
        data.update(
            n_hvdc=d["n_hvdc"],
            Ch_from=d["Ch_from"],
            Ch_to=d["Ch_to"],
        )

    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="ac", is_convex=False,
    )
