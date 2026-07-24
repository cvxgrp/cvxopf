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
    _validate_generators,
    gen_from_matpower,
    generator_bounds,
    generator_gencost,
    dc_injections as generator_dc_injections,
    make_generator_incidence,
    dc_operating_constraints as generator_dc_operating_constraints,
    dc_network_constraints as generator_dc_network_constraints,
    coupling_constraints as generator_coupling_constraints,
    gen_cost_expr,
)
from cvxopf.storage import (
    StorageUnitIdeal,
    _validate_storage,
    _make_storage_incidence_matrix,
    _storage_static_data,
    dc_injections as storage_dc_injections,
    dc_operating_constraints as storage_dc_operating_constraints,
    coupling_constraints as storage_coupling_constraints,
    storage_cost_expr,
)
from cvxopf.nondispatchable import (
    NondispatchableUnit,
    _validate_nondispatchable,
    _make_nd_incidence_matrix,
    _nd_static_data,
    dc_injections as nd_dc_injections,
    dc_operating_constraints as nd_dc_operating_constraints,
    coupling_constraints as nd_coupling_constraints,
)
from cvxopf.hvdc import (
    HVDCLink,
    _validate_hvdc,
    _make_hvdc_incidence_matrices,
    _hvdc_static_box,
    dc_injections as hvdc_dc_injections,
    dc_operating_constraints as hvdc_dc_operating_constraints,
    coupling_constraints as hvdc_coupling_constraints,
    hvdc_cost_expr,
)

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
    ng      = len(generators)
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

    _validate_generators(generators, ext_bus_ids)
    Pgmin, Pgmax, _, _ = generator_bounds(generators, baseMVA)
    Cg = make_generator_incidence(generators, nb, ext_to_int)
    gen_bus = np.array([ext_to_int[g.bus] for g in generators], dtype=int)
    gencost = generator_gencost(generators)
    
    # Parse storage if present
    storage_data = {}
    if storage:
        # Validate storage units
        _validate_storage(storage, ext_bus_ids)
        
        # Create storage incidence matrix
        Cs = _make_storage_incidence_matrix(storage, nb, ext_to_int)
        
        # Extract storage parameters
        storage_bus = np.array([
            ext_to_int[u.bus] if ext_to_int is not None else u.bus
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
            ext_to_int[u.bus] if ext_to_int is not None else u.bus
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
        nb=nb, ng=ng, nl=nl,
        ext_to_int=ext_to_int,
        ext_bus_ids=ext_bus_ids,
        A=A, Cg=Cg,
        r=r, f_max=f_max,
        Pd=Pd,
        generators=generators, gen_bus=gen_bus,
        Pgmin=Pgmin, Pgmax=Pgmax,
        gencost=gencost,
        loss_weight=options.loss_weight,
        **storage_data,
        **nd_data,
        **hvdc_data,
    )


def _make_dc_step_constraints(
    p_flows, Pg, generator_injection,
    A, Pd, f_max, Pgmin, Pgmax,
    baseMVA: float,
    ns: int = 0,
    storage_units=None,
    storage_injection=None,
    b_t=None,
    soc_t=None,
    nnd: int = 0,
    nd_units=None,
    nd_injection=None,
    nd_p_available_t=None,
    p_nd_t=None,
    n_hvdc: int = 0,
    hvdc_injection_expr=None,
    links=None,
    p_in_t=None,
    p_out_t=None,
    p_min_hvdc_t=None,
    p_max_hvdc_t=None,
    step: int = 0,
) -> list:
    """Build the list of CVXPY constraints for one DC time step."""
    # Section 1: Nodal real power balance
    storage_term = storage_injection if ns > 0 else 0
    nd_term = nd_injection if nnd > 0 else 0
    hvdc_term = hvdc_injection_expr if n_hvdc > 0 else 0
    constr = [
        A @ p_flows + generator_injection
        + storage_term + nd_term + hvdc_term == Pd
    ]

    # Section 2: Branch flow limits
    constr.append(cp.abs(p_flows) <= f_max)

    # Section 3: Generator bounds
    constr += generator_dc_operating_constraints(Pg, Pgmin, Pgmax)

    # Section 5: Storage real power bounds (omitted when ns == 0)
    if ns > 0:
        constr += storage_dc_operating_constraints(storage_units, b_t, soc_t)

    # Section 5b: Nondispatchable real power bounds (omitted when nnd == 0)
    if nnd > 0:
        constr += nd_dc_operating_constraints(
            nd_units, p_nd_t, nd_p_available_t
        )

    # Section 5c: HVDC operating constraints (omitted when n_hvdc == 0)
    if n_hvdc > 0:
        constr += hvdc_dc_operating_constraints(
            links, p_in_t, p_out_t, p_min_hvdc_t, p_max_hvdc_t, step
        )

    return constr


def _make_dc_step_cost(
    Pg, gencost, baseMVA,
    r, p_flows, loss_weight,
) -> cp.Expression:
    """Build the per-step DC cost expression."""
    G     = gen_cost_expr(gencost, cp.multiply(baseMVA, Pg))
    L     = cp.sum(cp.multiply(r, cp.square(p_flows)))
    return G + cp.multiply(loss_weight, L)


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
    Pg = cp.Variable(d["ng"], name="Pg")

    # Create storage variables if present
    b_t = soc_t = None
    storage_inj = None
    if "ns" in d:
        ns = d["ns"]
        b_t = cp.Variable(ns, name="b")
        soc_t = cp.Variable(ns, name="soc")
        storage_inj, storage_q_inj, storage_scaling = storage_dc_injections(
            storage, b_t, d["ext_to_int"]
        )
        assert storage_q_inj is None
        storage_scaling.value = 1.0 / d["baseMVA"]

    # Create nondispatchable variables if present
    p_nd_t = None
    nd_inj = None
    if "nnd" in d and d["nnd"] > 0:
        nnd = d["nnd"]
        p_nd_t = cp.Variable(nnd, name="p_nd")
        nd_inj, nd_q_inj, nd_scaling = nd_dc_injections(
            nondispatchable, p_nd_t, d["ext_to_int"]
        )
        assert nd_q_inj is None
        nd_scaling.value = 1.0 / d["baseMVA"]

    # Create HVDC variables if present
    p_in = p_out = None
    hvdc_inj_expr = None
    if "n_hvdc" in d and d["n_hvdc"] > 0:
        n_hvdc = d["n_hvdc"]
        p_in  = cp.Variable((n_hvdc,), name="p_hvdc_in")
        p_out = cp.Variable((n_hvdc,), name="p_hvdc_out")
        hvdc_inj_expr, hvdc_q_inj, inv_bMVA = hvdc_dc_injections(
            hvdc, p_in, p_out, d["ext_to_int"]
        )
        assert hvdc_q_inj is None
        inv_bMVA.value = 1.0 / d["baseMVA"]
        p_min_hvdc, p_max_hvdc = _hvdc_static_box(hvdc)

    generator_inj_expr, generator_q_inj, generator_scaling = generator_dc_injections(
        d["generators"], Pg, d["ext_to_int"]
    )
    assert generator_q_inj is None
    assert generator_scaling is None

    constr = _make_dc_step_constraints(
        p_flows, Pg, generator_inj_expr,
        d["A"], d["Pd"], d["f_max"],
        d["Pgmin"], d["Pgmax"],
        baseMVA=d["baseMVA"],
        ns=d.get("ns", 0),
        storage_units=storage,
        storage_injection=storage_inj,
        b_t=b_t,
        soc_t=soc_t,
        nnd=d.get("nnd", 0),
        nd_units=nondispatchable,
        nd_injection=nd_inj,
        nd_p_available_t=d.get("nd_p_available"),
        p_nd_t=p_nd_t,
        n_hvdc=d.get("n_hvdc", 0),
        hvdc_injection_expr=hvdc_inj_expr,
        links=hvdc,
        p_in_t=p_in,
        p_out_t=p_out,
        p_min_hvdc_t=p_min_hvdc if "n_hvdc" in d else None,
        p_max_hvdc_t=p_max_hvdc if "n_hvdc" in d else None,
        step=0,
    )
    constr.extend(
        generator_dc_network_constraints(
            d["generators"],
            p_flows,
            d["ext_to_int"],
            controlled_buses=(),
            enforce_vset=False,
        )
    )

    cost = _make_dc_step_cost(
        Pg, d["gencost"], d["baseMVA"],
        d["r"], p_flows, d["loss_weight"],
    )

    # Add storage aging cost if present
    storage_cost = None
    if "ns" in d:
        storage_cost = storage_cost_expr(storage, b_t)
        cost = cost + storage_cost

    # Add HVDC cost if present
    if "n_hvdc" in d and d["n_hvdc"] > 0:
        cost = cost + hvdc_cost_expr(hvdc, p_in)

    # Add storage SoC dynamics constraints if present
    if "ns" in d:
        storage_coupling = storage_coupling_constraints(
            storage, [b_t], [soc_t], d["storage_delta"]
        )
        constr.extend(storage_coupling)

    prob      = cp.Problem(cp.Minimize(cost), constr)
    variables = dict(p_flows=p_flows, Pg=Pg)

    # Add storage variables if present
    if "ns" in d:
        variables["b"] = b_t
        variables["soc"] = soc_t

    # Add nondispatchable variables if present
    if "nnd" in d and d["nnd"] > 0:
        variables["p_nd"] = p_nd_t

    # Add HVDC variables if present
    if "n_hvdc" in d and d["n_hvdc"] > 0:
        variables["p_hvdc_in"]  = p_in
        variables["p_hvdc_out"] = p_out

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"], Cg=d["Cg"],
        r=d["r"], f_max=d["f_max"],
        Pd=d["Pd"],
        gen_bus=d["gen_bus"], gencost=d["gencost"],
        Pgmin=d["Pgmin"], Pgmax=d["Pgmax"],
        loss_weight=d["loss_weight"],
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
    if "nnd" in d and d["nnd"] > 0:
        data.update(
            nnd=d["nnd"],
            Cnd=d["Cnd"],
            nd_bus=d["nd_bus"],
            nd_apparent_power_rating=d["nd_apparent_power_rating"],
            nd_p_available=d["nd_p_available"],
        )

    # Add HVDC data if present
    if "n_hvdc" in d and d["n_hvdc"] > 0:
        data.update(
            n_hvdc=d["n_hvdc"],
            Ch_from=d["Ch_from"],
            Ch_to=d["Ch_to"],
        )

    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
        expressions=(
            {"storage_cost": storage_cost} if storage_cost is not None else {}
        ),
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
        case, options, storage, delta, nondispatchable, hvdc, generators
    )
    Pd_series, _ = load_timeseries_from_dataframe(df_P, df_Q_dummy, case)
    
    # Parse nondispatchable timeseries if present
    if "nnd" in d:
        if df_nd is not None:
            d["nd_available"] = df_nd.to_numpy(dtype=float)
        else:
            d["nd_available"] = np.tile(d["nd_p_available"], (T, 1))

    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    p_flows_list    = []
    Pg_list         = []
    b_list          = []
    soc_list        = []
    p_nd_list       = []
    p_hvdc_in_list  = []
    p_hvdc_out_list = []
    all_constr      = []
    total_cost      = 0
    storage_cost    = 0

    for t in range(T):
        p_flows_t = cp.Variable(d["nl"], name=f"p_flows_{t}")
        Pg_t = cp.Variable(d["ng"], name=f"Pg_{t}")

        # Create storage variables if present
        b_t = soc_t = None
        storage_inj_t = None
        if "ns" in d:
            ns = d["ns"]
            b_t = cp.Variable(ns, name=f"b_{t}")
            soc_t = cp.Variable(ns, name=f"soc_{t}")
            (
                storage_inj_t,
                storage_q_inj_t,
                storage_scaling_t,
            ) = storage_dc_injections(storage, b_t, d["ext_to_int"])
            assert storage_q_inj_t is None
            storage_scaling_t.value = 1.0 / d["baseMVA"]

        # Create nondispatchable variables if present
        p_nd_t = None
        nd_inj_t = None
        if "nnd" in d and d["nnd"] > 0:
            nnd = d["nnd"]
            p_nd_t = cp.Variable(nnd, name=f"p_nd_{t}")
            nd_inj_t, nd_q_inj_t, nd_scaling_t = nd_dc_injections(
                nondispatchable, p_nd_t, d["ext_to_int"]
            )
            assert nd_q_inj_t is None
            nd_scaling_t.value = 1.0 / d["baseMVA"]

        # Create HVDC variables if present
        p_in_t = p_out_t = None
        hvdc_inj_expr_t = None
        p_min_hvdc_t = p_max_hvdc_t = None
        if "n_hvdc" in d and d["n_hvdc"] > 0:
            n_hvdc = d["n_hvdc"]
            p_in_t  = cp.Variable((n_hvdc,), name=f"p_hvdc_in_{t}")
            p_out_t = cp.Variable((n_hvdc,), name=f"p_hvdc_out_{t}")
            hvdc_inj_expr_t, hvdc_q_inj_t, inv_bMVA_t = hvdc_dc_injections(
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

        generator_inj_expr_t, generator_q_inj_t, generator_scaling_t = generator_dc_injections(
            d["generators"], Pg_t, d["ext_to_int"]
        )
        assert generator_q_inj_t is None
        assert generator_scaling_t is None

        step_constr = _make_dc_step_constraints(
            p_flows_t, Pg_t, generator_inj_expr_t,
            d["A"], Pd_series[t], d["f_max"],
            d["Pgmin"], d["Pgmax"],
            baseMVA=d["baseMVA"],
            ns=d.get("ns", 0),
            storage_units=storage,
            storage_injection=storage_inj_t,
            b_t=b_t,
            soc_t=soc_t,
            nnd=d.get("nnd", 0),
            nd_units=nondispatchable,
            nd_injection=nd_inj_t,
            nd_p_available_t=nd_p_available_t,
            p_nd_t=p_nd_t,
            n_hvdc=d.get("n_hvdc", 0),
            hvdc_injection_expr=hvdc_inj_expr_t,
            links=hvdc,
            p_in_t=p_in_t,
            p_out_t=p_out_t,
            p_min_hvdc_t=p_min_hvdc_t,
            p_max_hvdc_t=p_max_hvdc_t,
            step=t,
        )
        step_constr.extend(
            generator_dc_network_constraints(
                d["generators"],
                p_flows_t,
                d["ext_to_int"],
                controlled_buses=(),
                enforce_vset=False,
            )
        )
        step_cost = _make_dc_step_cost(
            Pg_t, d["gencost"], d["baseMVA"],
            d["r"], p_flows_t, d["loss_weight"],
        )

        # Add storage aging cost if present
        if "ns" in d:
            step_storage_cost = storage_cost_expr(storage, b_t)
            storage_cost = storage_cost + step_storage_cost
            step_cost = step_cost + step_storage_cost

        # Add HVDC cost if present
        if "n_hvdc" in d and d["n_hvdc"] > 0:
            step_cost = step_cost + hvdc_cost_expr(hvdc, p_in_t)

        all_constr.extend(step_constr)
        total_cost  = total_cost + step_cost
        p_flows_list.append(p_flows_t)
        Pg_list.append(Pg_t)

        # Add storage variables to lists
        if "ns" in d:
            b_list.append(b_t)
            soc_list.append(soc_t)

        # Add nondispatchable variables to lists
        if "nnd" in d and d["nnd"] > 0:
            p_nd_list.append(p_nd_t)

        # Add HVDC variables to lists
        if "n_hvdc" in d and d["n_hvdc"] > 0:
            p_hvdc_in_list.append(p_in_t)
            p_hvdc_out_list.append(p_out_t)

    # Add storage SoC dynamics constraints if present
    if "ns" in d:
        storage_coupling = storage_coupling_constraints(
            storage, b_list, soc_list, d["storage_delta"]
        )
        all_constr.extend(storage_coupling)
    all_constr.extend(
        generator_coupling_constraints(
            d["generators"], Pg_list, delta=delta
        )
    )
    if "nnd" in d:
        all_constr.extend(
            nd_coupling_constraints(
                nondispatchable, p_nd_list, delta=delta
            )
        )
    if "n_hvdc" in d:
        all_constr.extend(
            hvdc_coupling_constraints(
                hvdc, p_hvdc_in_list, p_hvdc_out_list, delta=delta
            )
        )

    all_constr.extend(coupling_constraints)
    prob = cp.Problem(cp.Minimize(total_cost), all_constr)

    variables = dict(p_flows=p_flows_list, Pg=Pg_list)

    # Add storage variables if present
    if "ns" in d:
        variables["b"] = b_list
        variables["soc"] = soc_list

    # Add nondispatchable variables if present
    if "nnd" in d and d["nnd"] > 0:
        variables["p_nd"] = p_nd_list

    # Add HVDC variables if present
    if "n_hvdc" in d and d["n_hvdc"] > 0:
        variables["p_hvdc_in"]  = p_hvdc_in_list
        variables["p_hvdc_out"] = p_hvdc_out_list

    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"], ng=d["ng"], nl=d["nl"],
        ext_to_int=d["ext_to_int"],
        A=d["A"], Cg=d["Cg"],
        r=d["r"], f_max=d["f_max"],
        gen_bus=d["gen_bus"], gencost=d["gencost"],
        Pgmin=d["Pgmin"], Pgmax=d["Pgmax"],
        loss_weight=d["loss_weight"],
        T=T,
        Pd_series=Pd_series,
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
    if "nnd" in d and d["nnd"] > 0:
        data.update(
            nnd=d["nnd"],
            Cnd=d["Cnd"],
            nd_bus=d["nd_bus"],
            nd_apparent_power_rating=d["nd_apparent_power_rating"],
            nd_available=d.get("nd_available"),  # Only present in multistep
        )

    # Add HVDC data if present
    if "n_hvdc" in d and d["n_hvdc"] > 0:
        data.update(
            n_hvdc=d["n_hvdc"],
            Ch_from=d["Ch_from"],
            Ch_to=d["Ch_to"],
        )

    return OPFBuild(
        prob=prob, variables=variables, data=data,
        formulation="lossy_dc", is_convex=True,
        expressions={"storage_cost": storage_cost} if "ns" in d else {},
    )
