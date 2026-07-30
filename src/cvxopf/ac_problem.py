"""
AC-OPF problem construction helpers (DNLP formulation).

This module contains the internal builders for the AC optimal power flow
problem. It is not part of the public API; use problem.py instead.

Formulation: DNLP (disciplined nonlinear programming) via CVXPY.
Solver: IPOPT (via cyipopt).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    gen_from_matpower,
)
from cvxopf._component_adapter import (
    ACNetworkState,
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
    horizon_steps: int = 1,
    nd_available_mw: np.ndarray | None = None,
    hvdc_inputs: HVDCInputs | None = None,
    is_multistep: bool = False,
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
        "ac",
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
    components = prepare_components(requests, "ac", preparation)

    formulation_data = dict(
        case=case, baseMVA=baseMVA,
        bus=bus,
        nb=nb,
        Ybus=Ybus, G=G, B=B, E=E, Z=Z,
        rows=rows, cols=cols, G_vec=G_vec, B_vec=B_vec, Rp=Rp,
        ref=ref, pv=pv, ext_to_int=ext_to_int,
        ext_bus_ids=ext_bus_ids,
        vmin_arr=vmin_arr, vmax_arr=vmax_arr,
        Pd=Pd, Qd=Qd,
        _components=components,
    )
    return merge_prepared_component_data(components, formulation_data)


def _make_step_variables(
    nb: int,
    vmin_arr, vmax_arr,
    E,
    suffix: str,
    step: int,
    init_flat: bool,
    sparse_pq: bool,
):
    """
    Construct one set of per-step CVXPY variables.

    When sparse_pq=True, P and Q are represented as flat (nnz,) vectors
    P_vec and Q_vec over the Ybus sparsity pattern.
    When sparse_pq=False, P and Q are dense (nb, nb) matrices.

    Returns a tuple of length 6:
        (theta, v, PQ_P, PQ_Q, p, q)
    where PQ_P is either P_vec (nnz,) or P (nb, nb), and similarly for PQ_Q.
    """
    def name(s):
        return f"{s}{suffix}"

    theta = cp.Variable((nb, 1), name=name("theta"))
    v     = cp.Variable((nb, 1), name=name("v"),
                        bounds=[vmin_arr[:, None], vmax_arr[:, None]])
    p     = cp.Variable(nb, name=name("p"))
    q     = cp.Variable(nb, name=name("q"))
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

    return theta, v, PQ_P, PQ_Q, p, q


def _make_step_constraints(
    theta, v, PQ_P, PQ_Q, p, q,
    G, B, E, Z,
    rows, cols, G_vec, B_vec, Rp,
    component_injection_p, component_injection_q,
    Pd, Qd, ref,
    component_operating_constraints,
    component_network_constraints,
    sparse_pq: bool,
) -> list:
    """
    Build the complete list of CVXPY constraints for one AC time step.

    Internal structure (five sections — do not reorder or split):
      1. Reference bus angle fix
      2. Power flow definitions: p and q from P/Q matrix (sparse or dense)
      3. Nodal power balance: exactly one p== and one q== constraint,
         using aggregate component real/reactive injections.
      4. Ordered component operating constraints.
      5. Ordered component-to-network constraints.

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
    # Active component injections are composed before entering this function.
    # ------------------------------------------------------------------
    constr.append(
        p == component_injection_p - Pd
    )
    constr.append(
        q == component_injection_q - Qd
    )

    # ------------------------------------------------------------------
    # Section 4: Ordered component operating constraints.
    # ------------------------------------------------------------------
    constr += list(component_operating_constraints)

    # ------------------------------------------------------------------
    # Section 5: Ordered component-to-network constraints.
    # ------------------------------------------------------------------
    constr += list(component_network_constraints)

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
    theta, v, PQ_P, PQ_Q, p, q = _make_step_variables(
        d["nb"],
        d["vmin_arr"], d["vmax_arr"],
        E=d["E"],
        suffix="",
        step=0,
        init_flat=options.init_flat,
        sparse_pq=options.sparse_pq,
    )
    step_context = StepContext(
        formulation="ac",
        step=0,
        base_mva=d["baseMVA"],
        ext_to_int=d["ext_to_int"],
        network_state=ACNetworkState(
            v, tuple(np.r_[[d["ref"]], d["pv"]]), options.enforce_vset
        ),
    )

    components: PreparedComponents = d["_components"]
    step_components = assemble_component_step(components, step_context)
    step_aggregate = aggregate_step_contributions(step_components)

    constr = _make_step_constraints(
        theta, v, PQ_P, PQ_Q, p, q,
        d["G"], d["B"], d["E"], d["Z"],
        d["rows"], d["cols"], d["G_vec"], d["B_vec"], d["Rp"],
        step_aggregate.injection.p_pu,
        step_aggregate.injection.q_pu,
        d["Pd"], d["Qd"], d["ref"],
        step_aggregate.operating_constraints,
        step_aggregate.network_constraints,
        sparse_pq=options.sparse_pq,
    )

    # Build the generic component stage cost.
    assert step_aggregate.cost is not None
    total_cost = integrate_stage_cost_rates(
        [step_aggregate.cost],
        delta,
    )
    component_costs = integrate_component_stage_costs(
        [step_components],
        delta,
    )

    horizon = assemble_component_horizon(
        components, [step_components], HorizonContext("ac", 1, delta)
    )
    horizon_aggregate = aggregate_horizon_contributions(horizon)
    storage_horizon = horizon.get("storage")
    storage_terminal_cost = (
        None if storage_horizon is None else storage_horizon.terminal_cost
    )
    if horizon_aggregate.terminal_cost is not None:
        total_cost = total_cost + horizon_aggregate.terminal_cost
    constr.extend(horizon_aggregate.constraints)
    
    prob = cp.Problem(cp.Minimize(total_cost), constr)

    # Build variables dict
    if options.sparse_pq:
        variables = dict(theta=theta, v=v, P_vec=PQ_P, Q_vec=PQ_Q,
                         p=p, q=q)
    else:
        variables = dict(theta=theta, v=v, P=PQ_P, Q=PQ_Q,
                         p=p, q=q)
    variables = publish_component_variables(
        [step_components],
        variables,
        multistep=False,
    )

    # Build data dict
    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"],
        ref=d["ref"], pv=d["pv"], ext_to_int=d["ext_to_int"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        rows=d["rows"], cols=d["cols"], G_vec=d["G_vec"],
        B_vec=d["B_vec"], Rp=d["Rp"],
        Pd=d["Pd"], Qd=d["Qd"],
    )
    data = publish_component_metadata(components, data)

    compatibility_expressions = {"p_net": p, "q_net": q}
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
        prob=prob, variables=variables, data=data,
        formulation="ac", is_convex=False,
        expressions=expressions,
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
    Pd_series, Qd_series = load_timeseries_from_dataframe(df_P, df_Q, case)

    if Pd_series.shape[0] != T:
        raise ValueError(
            f"T={T} but df_P has {Pd_series.shape[0]} rows; they must match."
        )

    # Initialize lists for variables
    theta_list, v_list, PQ_P_list, PQ_Q_list = [], [], [], []
    p_list, q_list = [], []
    component_steps = []
    step_aggregates = []
    components: PreparedComponents = d["_components"]
    all_constr  = []

    for t in range(T):
        # Create step variables
        theta_t, v_t, PQ_P_t, PQ_Q_t, p_t, q_t = \
            _make_step_variables(
                d["nb"],
                d["vmin_arr"], d["vmax_arr"],
                E=d["E"],
                suffix=f"_{t}",
                step=t,
                init_flat=options.init_flat,
                sparse_pq=options.sparse_pq,
            )
        step_context = StepContext(
            formulation="ac",
            step=t,
            base_mva=d["baseMVA"],
            ext_to_int=d["ext_to_int"],
            network_state=ACNetworkState(
                v_t,
                tuple(np.r_[[d["ref"]], d["pv"]]),
                options.enforce_vset,
            ),
        )

        step_components = assemble_component_step(
            components, step_context, variable_suffix=f"_{t}"
        )
        component_steps.append(step_components)
        step_aggregate = aggregate_step_contributions(step_components)
        step_aggregates.append(step_aggregate)

        step_constr = _make_step_constraints(
            theta_t, v_t, PQ_P_t, PQ_Q_t, p_t, q_t,
            d["G"], d["B"], d["E"], d["Z"],
            d["rows"], d["cols"], d["G_vec"], d["B_vec"], d["Rp"],
            step_aggregate.injection.p_pu,
            step_aggregate.injection.q_pu,
            Pd_series[t], Qd_series[t], d["ref"],
            step_aggregate.operating_constraints,
            step_aggregate.network_constraints,
            sparse_pq=options.sparse_pq,
        )

        all_constr.extend(step_constr)

        # Retain the complete component stage-cost rate.
        assert step_aggregate.cost is not None
        theta_list.append(theta_t)
        v_list.append(v_t)
        PQ_P_list.append(PQ_P_t)
        PQ_Q_list.append(PQ_Q_t)
        p_list.append(p_t)
        q_list.append(q_t)

    total_cost = integrate_stage_cost_rates(
        [aggregate.cost for aggregate in step_aggregates],
        delta,
    )
    component_costs = integrate_component_stage_costs(
        component_steps,
        delta,
    )

    horizon = assemble_component_horizon(
        components, component_steps, HorizonContext("ac", T, delta)
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

    # Build variables dict
    if options.sparse_pq:
        variables = dict(
            theta=theta_list, v=v_list,
            P_vec=PQ_P_list, Q_vec=PQ_Q_list,
            p=p_list, q=q_list,
        )
    else:
        variables = dict(
            theta=theta_list, v=v_list,
            P=PQ_P_list, Q=PQ_Q_list,
            p=p_list, q=q_list,
        )
    variables = publish_component_variables(
        component_steps,
        variables,
        multistep=True,
    )

    # Build data dict
    data = dict(
        baseMVA=d["baseMVA"], nb=d["nb"],
        ref=d["ref"], pv=d["pv"], ext_to_int=d["ext_to_int"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        rows=d["rows"], cols=d["cols"], G_vec=d["G_vec"],
        B_vec=d["B_vec"], Rp=d["Rp"],
        T=T,
        Pd_series=Pd_series,
        Qd_series=Qd_series,
    )
    data = publish_component_metadata(components, data)

    compatibility_expressions = {"p_net": p_list, "q_net": q_list}
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
        prob=prob, variables=variables, data=data,
        formulation="ac", is_convex=False,
        expressions=expressions,
    )
