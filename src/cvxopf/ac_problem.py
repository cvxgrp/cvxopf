"""
AC-OPF problem construction helpers (DNLP formulation).

This module contains the internal builders for the AC optimal power flow
problem. It is not part of the public API; use problem.py instead.

Formulation: DNLP (disciplined nonlinear programming) via CVXPY.
Solver: IPOPT (via cyipopt).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import cvxpy as cp

from cvxopf._admittance_preparation import prepare_admittance_case as _parse_case

from cvxopf.network import (
    BranchAdmittance,
)
from cvxopf.generator import (
    DispatchableGenerator,
)
from cvxopf._component_adapter import (
    ACNetworkState,
    HorizonContext,
    StepContext,
    VectorizedContext,
)
from cvxopf._component_assembly import (
    PreparedComponents,
    aggregate_horizon_contributions,
    aggregate_step_contributions,
    assemble_component_horizon,
    assemble_component_step,
    integrate_component_stage_costs,
    integrate_stage_cost_rates,
    publish_component_expressions,
    publish_component_metadata,
    publish_component_variables,
    assemble_component_vectorized,
    aggregate_vectorized_contributions,
    integrate_vectorized_stage_cost_rate,
    integrate_vectorized_component_stage_costs,
    publish_vectorized_component_expressions,
    publish_vectorized_component_variables,
    vectorized_component_result_projections,
)
from cvxopf._temporal_assembly import (
    ResultProjectionRegistry,
    ResultProjectionSpec,
    merge_result_projection_registries,
)
from cvxopf._component_adapters import (
    HVDCInputs,
    LoadInputs,
)
from cvxopf.load import Load
from cvxopf.storage import (
    StorageUnitIdeal,
)
from cvxopf.nondispatchable import (
    NondispatchableUnit,
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


@dataclass(frozen=True)
class _BranchTerminalFlow:
    """Four per-unit branch-terminal power channels in branch row order."""

    p_from: cp.Expression
    q_from: cp.Expression
    p_to: cp.Expression
    q_to: cp.Expression


def _terminal_power_expression(
    theta,
    v,
    i: int,
    j: int,
    yii: complex,
    yij: complex,
    *,
    vectorized: bool = False,
) -> tuple[cp.Expression, cp.Expression]:
    """Construct one oriented branch-terminal complex-power expression."""
    ti, tj = (theta[i, :], theta[j, :]) if vectorized else (theta[i, 0], theta[j, 0])
    vi, vj = (v[i, :], v[j, :]) if vectorized else (v[i, 0], v[j, 0])
    cosine = cp.nlp.cos(ti - tj)
    sine = cp.nlp.sin(ti - tj)
    self_p = float(yii.real) * cp.square(vi)
    self_q = -float(yii.imag) * cp.square(vi)
    cross_scale = cp.multiply(vi, vj)
    cross_p = cp.multiply(cross_scale, (
        float(yij.real) * cosine + float(yij.imag) * sine
    ))
    cross_q = cp.multiply(cross_scale, (
        float(yij.real) * sine - float(yij.imag) * cosine
    ))
    return self_p + cross_p, self_q + cross_q


def _make_branch_terminal_flow(
    theta,
    v,
    admittance: BranchAdmittance,
    *,
    suffix: str,
    horizon_steps: int | None = None,
) -> tuple[_BranchTerminalFlow, list[cp.Constraint]]:
    """Create lifted terminal flows and their authoritative definitions."""
    nl = len(admittance.from_bus)
    if nl == 0:
        empty = cp.Constant(np.empty(0))
        return _BranchTerminalFlow(empty, empty, empty, empty), []

    p_from_direct = []
    q_from_direct = []
    p_to_direct = []
    q_to_direct = []
    for e in range(nl):
        if not admittance.status[e]:
            zero = cp.Constant(0.0 if horizon_steps is None else np.zeros(horizon_steps))
            p_from_direct.append(zero)
            q_from_direct.append(zero)
            p_to_direct.append(zero)
            q_to_direct.append(zero)
            continue

        f = int(admittance.from_bus[e])
        t = int(admittance.to_bus[e])
        pf, qf = _terminal_power_expression(
            theta, v, f, t, admittance.yff[e], admittance.yft[e],
            vectorized=horizon_steps is not None,
        )
        pt, qt = _terminal_power_expression(
            theta, v, t, f, admittance.ytt[e], admittance.ytf[e],
            vectorized=horizon_steps is not None,
        )
        p_from_direct.append(pf)
        q_from_direct.append(qf)
        p_to_direct.append(pt)
        q_to_direct.append(qt)

    stack = cp.hstack if horizon_steps is None else cp.vstack
    shape = (nl,) if horizon_steps is None else (nl, horizon_steps)
    direct = _BranchTerminalFlow(
        stack(p_from_direct),
        stack(q_from_direct),
        stack(p_to_direct),
        stack(q_to_direct),
    )
    lifted = _BranchTerminalFlow(
        cp.Variable(shape, name=f"branch_p_from_pu{suffix}"),
        cp.Variable(shape, name=f"branch_q_from_pu{suffix}"),
        cp.Variable(shape, name=f"branch_p_to_pu{suffix}"),
        cp.Variable(shape, name=f"branch_q_to_pu{suffix}"),
    )
    defining_equalities = [
        lifted.p_from == direct.p_from,
        lifted.q_from == direct.q_from,
        lifted.p_to == direct.p_to,
        lifted.q_to == direct.q_to,
    ]
    return lifted, defining_equalities


def _branch_expression_mapping(
    flow: _BranchTerminalFlow,
) -> dict[str, cp.Expression]:
    """Return the stable modeled-expression names for terminal flow."""
    return {
        "branch_p_from_pu": flow.p_from,
        "branch_q_from_pu": flow.q_from,
        "branch_p_to_pu": flow.p_to,
        "branch_q_to_pu": flow.q_to,
    }




def _make_branch_limit_constraints(
    flow: _BranchTerminalFlow,
    constrained_branch_indices: np.ndarray,
    branch_rate_a_mva: np.ndarray,
    base_mva: float,
) -> list[cp.Constraint]:
    """Apply normalized apparent-power limits at both branch terminals."""
    constraints = []
    for e in constrained_branch_indices:
        rating_mva = float(branch_rate_a_mva[e])
        with np.errstate(
            divide="ignore",
            invalid="ignore",
            over="ignore",
            under="ignore",
        ):
            rating_pu = float(np.divide(rating_mva, base_mva))
        if not np.isfinite(rating_pu) or rating_pu <= 0:
            raise ValueError(
                "AC branch-limit normalization produced a nonpositive or "
                f"nonfinite rating for row {int(e)}: "
                f"rateA={rating_mva!r} MVA, baseMVA={base_mva!r}, "
                f"rating_pu={rating_pu!r}."
            )
        constraints.extend(
            [
                cp.square(flow.p_from[e] / rating_pu)
                + cp.square(flow.q_from[e] / rating_pu)
                <= 1.0,
                cp.square(flow.p_to[e] / rating_pu)
                + cp.square(flow.q_to[e] / rating_pu)
                <= 1.0,
            ]
        )
    return constraints


def _make_network_operating_constraints(
    flow: _BranchTerminalFlow,
    options,
    constrained_branch_indices: np.ndarray,
    branch_rate_a_mva: np.ndarray,
    base_mva: float,
) -> list[cp.Constraint]:
    """Return the formulation-owned AC network operating set."""
    if not options.enforce_branch_limits:
        return []
    return _make_branch_limit_constraints(
        flow,
        constrained_branch_indices,
        branch_rate_a_mva,
        base_mva,
    )




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


def _pq_entry_expressions(theta_i, theta_j, voltage_i, voltage_j, g, b):
    """The same P/Q equations for scalar entries or arrays of entries/times."""
    angle = theta_i - theta_j
    cosine, sine = cp.nlp.cos(angle), cp.nlp.sin(angle)
    vv = cp.multiply(voltage_i, voltage_j)
    return (
        cp.multiply(vv, cp.multiply(g, cosine) + cp.multiply(b, sine)),
        cp.multiply(vv, cp.multiply(g, sine) - cp.multiply(b, cosine)),
    )


def _pq_flow_expressions(theta, voltage, rows, cols, conductance, susceptance):
    """Ybus-entry powers with shape (nnz, T), including T=1 stepwise builds.

    Pair row/column gathers on the spatial axis and broadcast admittances
    along time. CVXPY >= 1.9.3 includes the repeated-index derivative fix
    needed by these gathers (cvxpy issue #3442).
    """
    return _pq_entry_expressions(
        theta[rows, :], theta[cols, :], voltage[rows, :], voltage[cols, :],
        conductance[:, None], susceptance[:, None],
    )


def _make_step_constraints(
    theta, v, PQ_P, PQ_Q, p, q,
    E, Z,
    rows, cols, G_vec, B_vec, Rp,
    component_injection_p, component_injection_q,
    ref,
    component_operating_constraints,
    component_network_constraints,
    branch_flow_defining_constraints,
    network_operating_constraints,
    sparse_pq: bool,
    vectorize_pq: bool = True,
) -> list:
    """
    Build the complete list of CVXPY constraints for one AC time step.

    Internal structure (seven sections — do not reorder or split):
      1. Reference bus angle fix
      2. Power flow definitions: p and q from P/Q matrix (sparse or dense)
      3. Branch-terminal flow definitions.
      4. Nodal power balance: exactly one p== and one q== constraint,
         using aggregate component real/reactive injections.
      5. Formulation-owned network operating constraints.
      6. Ordered component operating constraints.
      7. Ordered component-to-network constraints.

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
    if vectorize_pq:
        p_flow, q_flow = _pq_flow_expressions(theta, v, rows, cols, G_vec, B_vec)
        lhs_p, lhs_q = (PQ_P, PQ_Q) if sparse_pq else (PQ_P[E], PQ_Q[E])
        constr += [lhs_p == p_flow[:, 0], lhs_q == q_flow[:, 0]]
    else:
        for k, (i, j) in enumerate(zip(rows, cols, strict=True)):
            p_flow, q_flow = _pq_entry_expressions(
                theta[i, 0], theta[j, 0], v[i, 0], v[j, 0], G_vec[k], B_vec[k],
            )
            lhs_p, lhs_q = (PQ_P[k], PQ_Q[k]) if sparse_pq else (PQ_P[i, j], PQ_Q[i, j])
            constr += [lhs_p == p_flow, lhs_q == q_flow]
    if sparse_pq:
        constr += [
            p == Rp @ PQ_P,
            q == Rp @ PQ_Q,
        ]
    else:
        if vectorize_pq:
            constr += [PQ_P[Z] == 0.0, PQ_Q[Z] == 0.0]
        else:
            for i, j in zip(*Z, strict=True):
                constr += [PQ_P[i, j] == 0.0, PQ_Q[i, j] == 0.0]
        constr += [
            p == cp.sum(PQ_P, axis=1),
            q == cp.sum(PQ_Q, axis=1),
        ]

    # ------------------------------------------------------------------
    # Section 3: Branch-terminal flow definitions.
    # ------------------------------------------------------------------
    constr += list(branch_flow_defining_constraints)

    # ------------------------------------------------------------------
    # Section 4: Nodal power balance
    # Exactly one p== and one q== constraint.
    # Active component injections are composed before entering this function.
    # ------------------------------------------------------------------
    constr.append(p == component_injection_p)
    constr.append(q == component_injection_q)

    # ------------------------------------------------------------------
    # Section 5: Formulation-owned network operating constraints.
    # ------------------------------------------------------------------
    constr += list(network_operating_constraints)

    # ------------------------------------------------------------------
    # Section 6: Ordered component operating constraints.
    # ------------------------------------------------------------------
    constr += list(component_operating_constraints)

    # ------------------------------------------------------------------
    # Section 7: Ordered component-to-network constraints.
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
    loads: list[Load] | None = None,
) -> "OPFBuild":
    """Build a single time-step AC-OPF problem."""
    from cvxopf.problem import OPFBuild

    d = _parse_case(
        case, options, storage, delta, nondispatchable, hvdc, generators,
        loads=loads,
        load_participates_when_empty=loads is not None,
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
    branch_flow, branch_flow_defining_constraints = (
        _make_branch_terminal_flow(
            theta,
            v,
            d["branch_admittance"],
            suffix="",
        )
    )
    network_operating_constraints = _make_network_operating_constraints(
        branch_flow,
        options,
        d["constrained_branch_indices"],
        d["branch_rate_a_mva"],
        d["baseMVA"],
    )
    step_context = StepContext(
        formulation="ac",
        step=0,
        base_mva=d["baseMVA"],
        ext_to_int=d["_component_ext_to_int"],
        network_state=ACNetworkState(
            v, tuple(np.r_[[d["ref"]], d["pv"]]), options.enforce_vset
        ),
        numerical_preparation=options.numerical_preparation,
    )

    components: PreparedComponents = d["_components"]
    step_components = assemble_component_step(components, step_context)
    step_aggregate = aggregate_step_contributions(step_components)

    constr = _make_step_constraints(
        theta, v, PQ_P, PQ_Q, p, q,
        d["E"], d["Z"],
        d["rows"], d["cols"], d["G_vec"], d["B_vec"], d["Rp"],
        step_aggregate.injection.p_pu,
        step_aggregate.injection.q_pu,
        d["ref"],
        step_aggregate.operating_constraints,
        step_aggregate.network_constraints,
        branch_flow_defining_constraints,
        network_operating_constraints,
        sparse_pq=options.sparse_pq,
        vectorize_pq=options.vectorize_pq,
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
        nl=d["nl"],
        branch_from_bus_internal=d["branch_from_bus_internal"],
        branch_to_bus_internal=d["branch_to_bus_internal"],
        branch_from_bus_external=d["branch_from_bus_external"],
        branch_to_bus_external=d["branch_to_bus_external"],
        branch_status=d["branch_status"],
        branch_rate_a_mva=d["branch_rate_a_mva"],
        constrained_branch_indices=d["constrained_branch_indices"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        rows=d["rows"], cols=d["cols"], G_vec=d["G_vec"],
        B_vec=d["B_vec"], Rp=d["Rp"],
        Pd=d["Pd"], Qd=d["Qd"],
    )
    data = publish_component_metadata(components, data)

    compatibility_expressions = {"p_net": p, "q_net": q}
    compatibility_expressions.update(_branch_expression_mapping(branch_flow))
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
        _numerical_preparation=options.numerical_preparation,
        _exact_boxes=step_aggregate.exact_boxes,
    )


def _build_ac_vectorized(
    case, df_P, df_Q, T, options, coupling_constraints,
    storage=None, delta=1.0, nondispatchable=None, df_nd=None, *,
    hvdc=None, df_hvdc_min=None, df_hvdc_max=None, generators=None,
    loads=None, load_inputs, load_participates_when_empty=False,
    nd_inputs=None, hvdc_inputs=None,
) -> "OPFBuild":
    """Build AC power flow with native spatial axes and time last.

    Ybus P/Q definitions batch across time and optionally across spatial
    entries. Branch-terminal equations retain their separate lifted variables.
    """
    from cvxopf.problem import OPFBuild

    d = _parse_case(
        case, options, storage, delta, nondispatchable, hvdc, generators,
        horizon_steps=T, nondispatchable_inputs=nd_inputs,
        hvdc_inputs=hvdc_inputs, load_inputs=load_inputs, is_multistep=True,
        loads=loads, load_participates_when_empty=load_participates_when_empty,
    )
    nb = d["nb"]
    theta = cp.Variable((nb, T), name="theta")
    voltage = cp.Variable((nb, T), name="v", bounds=[
        np.broadcast_to(d["vmin_arr"][:, None], (nb, T)),
        np.broadcast_to(d["vmax_arr"][:, None], (nb, T)),
    ])
    p = cp.Variable((nb, T), name="p")
    q = cp.Variable((nb, T), name="q")
    pq_shape = (len(d["rows"]), T) if options.sparse_pq else (nb * nb, T)
    p_name, q_name = ("P_vec", "Q_vec") if options.sparse_pq else ("P", "Q")
    PQ_P, PQ_Q = cp.Variable(pq_shape, name=p_name), cp.Variable(pq_shape, name=q_name)
    if options.init_flat:
        theta.value = np.zeros(theta.shape)
        voltage.value = np.ones(voltage.shape)
    components: PreparedComponents = d["_components"]
    context = VectorizedContext(
        "ac", T, delta, d["baseMVA"], d["_component_ext_to_int"],
        ACNetworkState(voltage, tuple(np.r_[[d["ref"]], d["pv"]]), options.enforce_vset),
        numerical_preparation=options.numerical_preparation,
    )
    contributions = assemble_component_vectorized(components, context)
    aggregate = aggregate_vectorized_contributions(contributions)
    flow, defining = _make_branch_terminal_flow(
        theta, voltage, d["branch_admittance"], suffix="", horizon_steps=T,
    )

    constraints = [theta[d["ref"]] == 0]
    entries = d["rows"] * nb + d["cols"]
    if options.vectorize_pq:
        p_flow, q_flow = _pq_flow_expressions(
            theta, voltage, d["rows"], d["cols"], d["G_vec"], d["B_vec"],
        )
        lhs_p, lhs_q = ((PQ_P, PQ_Q) if options.sparse_pq
                        else (PQ_P[entries, :], PQ_Q[entries, :]))
        constraints += [lhs_p == p_flow, lhs_q == q_flow]
    else:
        for k, (i, j) in enumerate(zip(d["rows"], d["cols"], strict=True)):
            p_flow, q_flow = _pq_entry_expressions(
                theta[i, :], theta[j, :], voltage[i, :], voltage[j, :],
                d["G_vec"][k], d["B_vec"][k],
            )
            entry = k if options.sparse_pq else entries[k]
            constraints += [PQ_P[entry, :] == p_flow, PQ_Q[entry, :] == q_flow]
    if options.sparse_pq:
        constraints += [p == d["Rp"] @ PQ_P, q == d["Rp"] @ PQ_Q]
    else:
        zeros = d["Z"][0] * nb + d["Z"][1]
        if len(zeros):
            if options.vectorize_pq:
                constraints += [PQ_P[zeros, :] == 0, PQ_Q[zeros, :] == 0]
            else:
                for entry in zeros:
                    constraints += [PQ_P[entry, :] == 0, PQ_Q[entry, :] == 0]
        constraints += [p == np.kron(np.eye(nb), np.ones((1, nb))) @ PQ_P,
                        q == np.kron(np.eye(nb), np.ones((1, nb))) @ PQ_Q]
    constraints += defining
    constraints += [p == aggregate.model.injection.p_pu,
                    q == aggregate.model.injection.q_pu]
    constraints += _make_network_operating_constraints(
        flow, options, d["constrained_branch_indices"], d["branch_rate_a_mva"], d["baseMVA"],
    )
    constraints += list(aggregate.model.operating_constraints)
    constraints += list(aggregate.model.network_constraints)
    constraints += list(aggregate.model.horizon.constraints)
    constraints += list(coupling_constraints)
    total_cost = integrate_vectorized_stage_cost_rate(aggregate.model.stage_cost_rate, delta)
    component_costs = integrate_vectorized_component_stage_costs(contributions, delta)
    if aggregate.model.horizon.terminal_cost is not None:
        total_cost += aggregate.model.horizon.terminal_cost

    variables = publish_vectorized_component_variables(aggregate, {
        "theta": theta, "v": voltage, p_name: PQ_P, q_name: PQ_Q, "p": p, "q": q,
    })
    expressions = publish_vectorized_component_expressions(aggregate, {
        "p_net": p, "q_net": q, **_branch_expression_mapping(flow), **component_costs,
    })
    keys = ("baseMVA", "nb", "ref", "pv", "ext_to_int", "nl",
            "branch_from_bus_internal", "branch_to_bus_internal",
            "branch_from_bus_external", "branch_to_bus_external", "branch_status",
            "branch_rate_a_mva", "constrained_branch_indices", "Ybus", "G", "B",
            "E", "Z", "rows", "cols", "G_vec", "B_vec", "Rp")
    data = {key: d[key] for key in keys}
    data["T"] = T
    for channel, result_key in (("p", "Pd_series"), ("q", "Qd_series")):
        unit = "mw" if channel == "p" else "mvar"
        field = f"_load_{channel}_{unit}"
        if components.flat_data[f"_load_{channel}_temporal_class"] == "static":
            native = d["Cload"] @ components.flat_data[field + "_source"] / d["baseMVA"]
            data[result_key] = np.broadcast_to(native, (T, nb))
        else:
            data[result_key] = components.flat_data[field + "_by_step"] @ d["Cload"].T / d["baseMVA"]
    data = publish_component_metadata(components, data)
    network_projections = ResultProjectionRegistry(
        variables={name: ResultProjectionSpec(name, variable.shape[:-1],
                    (nb,) if name in ("theta", "v") else variable.shape[:-1], "interval")
                   for name, variable in (("theta", theta), ("v", voltage), ("p", p), ("q", q),
                                           (p_name, PQ_P), (q_name, PQ_Q))},
        expressions={name: ResultProjectionSpec(name, expression.shape[:-1],
                                                expression.shape[:-1], "interval")
                     for name, expression in {"p_net": p, "q_net": q,
                                               **_branch_expression_mapping(flow)}.items()},
    )
    return OPFBuild(
        prob=cp.Problem(cp.Minimize(total_cost), constraints), variables=variables,
        data=data, formulation="ac", is_convex=False, expressions=expressions,
        temporal_assembly="vectorized", result_projections=merge_result_projection_registries(
            network_projections,
            vectorized_component_result_projections(aggregate, integrated_component_costs=component_costs),
        ),
        _numerical_preparation=options.numerical_preparation,
        _exact_boxes=aggregate.model.exact_boxes,
    )


def _build_ac_multistep(
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
    """Build a T-step AC-OPF problem as a single cp.Problem."""
    from cvxopf.problem import OPFBuild

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
        load_inputs=load_inputs,
        is_multistep=True,
        loads=loads,
        load_participates_when_empty=load_participates_when_empty,
    )
    Pd_series = load_inputs.p_mw @ d["Cload"].T / d["baseMVA"]
    Qd_series = load_inputs.q_mvar @ d["Cload"].T / d["baseMVA"]

    # Initialize lists for variables
    theta_list, v_list, PQ_P_list, PQ_Q_list = [], [], [], []
    p_list, q_list = [], []
    branch_flow_lists = {
        "branch_p_from_pu": [],
        "branch_q_from_pu": [],
        "branch_p_to_pu": [],
        "branch_q_to_pu": [],
    }
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
        branch_flow_t, branch_flow_defining_constraints = (
            _make_branch_terminal_flow(
                theta_t,
                v_t,
                d["branch_admittance"],
                suffix=f"_{t}",
            )
        )
        network_operating_constraints = _make_network_operating_constraints(
            branch_flow_t,
            options,
            d["constrained_branch_indices"],
            d["branch_rate_a_mva"],
            d["baseMVA"],
        )
        step_context = StepContext(
            formulation="ac",
            step=t,
            base_mva=d["baseMVA"],
            ext_to_int=d["_component_ext_to_int"],
            network_state=ACNetworkState(
                v_t,
                tuple(np.r_[[d["ref"]], d["pv"]]),
                options.enforce_vset,
            ),
            numerical_preparation=options.numerical_preparation,
        )

        step_components = assemble_component_step(
            components, step_context, variable_suffix=f"_{t}"
        )
        component_steps.append(step_components)
        step_aggregate = aggregate_step_contributions(step_components)
        step_aggregates.append(step_aggregate)

        step_constr = _make_step_constraints(
            theta_t, v_t, PQ_P_t, PQ_Q_t, p_t, q_t,
            d["E"], d["Z"],
            d["rows"], d["cols"], d["G_vec"], d["B_vec"], d["Rp"],
            step_aggregate.injection.p_pu,
            step_aggregate.injection.q_pu,
            d["ref"],
            step_aggregate.operating_constraints,
            step_aggregate.network_constraints,
            branch_flow_defining_constraints,
            network_operating_constraints,
            sparse_pq=options.sparse_pq,
            vectorize_pq=options.vectorize_pq,
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
        for name, expression in _branch_expression_mapping(
            branch_flow_t
        ).items():
            branch_flow_lists[name].append(expression)

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
        nl=d["nl"],
        branch_from_bus_internal=d["branch_from_bus_internal"],
        branch_to_bus_internal=d["branch_to_bus_internal"],
        branch_from_bus_external=d["branch_from_bus_external"],
        branch_to_bus_external=d["branch_to_bus_external"],
        branch_status=d["branch_status"],
        branch_rate_a_mva=d["branch_rate_a_mva"],
        constrained_branch_indices=d["constrained_branch_indices"],
        Ybus=d["Ybus"], G=d["G"], B=d["B"], E=d["E"], Z=d["Z"],
        rows=d["rows"], cols=d["cols"], G_vec=d["G_vec"],
        B_vec=d["B_vec"], Rp=d["Rp"],
        T=T,
        Pd_series=Pd_series,
        Qd_series=Qd_series,
    )
    data = publish_component_metadata(components, data)

    compatibility_expressions = {"p_net": p_list, "q_net": q_list}
    compatibility_expressions.update(branch_flow_lists)
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
        _numerical_preparation=options.numerical_preparation,
        _exact_boxes=tuple(box for step in step_aggregates for box in step.exact_boxes),
    )
