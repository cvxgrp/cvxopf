"""Sparse voltage-product SOCP builders; device mathematics is shared."""

from copy import deepcopy

import cvxpy as cp
import numpy as np

from cvxopf._admittance_preparation import prepare_admittance_case
from cvxopf._component_adapter import (
    SquaredVoltageNetworkState, StepContext, VectorizedContext, HorizonContext,
)
from cvxopf._component_adapters import HVDCInputs, NondispatchableInputs
from cvxopf._component_assembly import (
    assemble_component_step, aggregate_step_contributions,
    assemble_component_horizon, aggregate_horizon_contributions,
    assemble_component_vectorized, aggregate_vectorized_contributions,
    integrate_stage_cost_rates, integrate_component_stage_costs,
    integrate_vectorized_stage_cost_rate, integrate_vectorized_component_stage_costs,
    publish_component_variables, publish_component_expressions,
    publish_vectorized_component_variables, publish_vectorized_component_expressions,
    publish_component_metadata, vectorized_component_result_projections,
)
from cvxopf._temporal_assembly import (
    ResultProjectionRegistry, ResultProjectionSpec, merge_result_projection_registries,
    VariableBoxFamily, box_representation_decision,
)
from cvxopf._voltage_product import make_voltage_product_maps


def _network_block(d, options, horizon=None, suffix=""):
    """The same network equations for one step or a time-last horizon."""
    maps = d["_voltage_product_maps"]
    nb, ne = maps.nb, len(maps.pairs)
    def shape(n):
        return (n,) if horizon is None else (n, horizon)
    w = cp.Variable(shape(nb), name="w" + suffix)
    variables = {"w": w}
    constraints = []
    decision = box_representation_decision("socp", VariableBoxFamily.SOCP_VOLTAGE_SQUARED)
    if decision.representation != "explicit":
        raise RuntimeError("SOCP squared-voltage bounds require explicit policy")
    low, high = d["vmin_arr"]**2, d["vmax_arr"]**2
    if horizon is not None:
        low, high = low[:, None], high[:, None]
    constraints.extend((w >= low, w <= high))
    if ne:
        c = cp.Variable(shape(ne), name="W_re" + suffix)
        s = cp.Variable(shape(ne), name="W_im" + suffix)
        variables.update(W_re=c, W_im=s)
        i, j = maps.pairs.T
        # Flatten each channel in the same explicit order before batched SOC.
        def flatten(x):
            return cp.reshape(x, (x.size,), order="F")
        constraints.append(cp.SOC(flatten(w[i] + w[j]), cp.vstack([
            2 * flatten(c), 2 * flatten(s), flatten(w[i] - w[j]),
        ]), axis=0))
        x = cp.hstack([w, c, s]) if horizon is None else cp.vstack([w, c, s])
    else:
        x = w
    expressions = {"p_net": maps.nodal.p @ x, "q_net": maps.nodal.q @ x}
    if d["nl"]:
        for side, power_map in (("from", maps.branch_from), ("to", maps.branch_to)):
            expressions[f"branch_p_{side}_pu"] = power_map.p @ x
            expressions[f"branch_q_{side}_pu"] = power_map.q @ x
        if options.enforce_branch_limits:
            rows = d["constrained_branch_indices"]
            if len(rows):
                rating = d["branch_rate_a_mva"][rows] / d["baseMVA"]
                if np.any(~np.isfinite(rating) | (rating <= 0)):
                    raise ValueError("SOCP branch rating normalization must be finite and positive")
                if horizon is not None:
                    rating = np.broadcast_to(rating[:, None], (len(rows), horizon))
                for side in ("from", "to"):
                    p = expressions[f"branch_p_{side}_pu"][rows]
                    q = expressions[f"branch_q_{side}_pu"][rows]
                    constraints.append(cp.SOC(rating.reshape(-1, order="F"), cp.vstack([
                        cp.reshape(p, (p.size,), order="F"),
                        cp.reshape(q, (q.size,), order="F"),
                    ]), axis=0))
    state = SquaredVoltageNetworkState(w, tuple(np.r_[[d["ref"]], d["pv"]]), options.enforce_vset)
    return variables, expressions, constraints, state


def _prepare(case, options, storage, delta, nondispatchable, *, T, multistep,
             hvdc, generators, loads, load_inputs=None, load_participates_when_empty=False,
             nd_inputs=None, hvdc_inputs=None):
    d = prepare_admittance_case(
        case, options, storage, delta, nondispatchable, hvdc, generators,
        horizon_steps=T, is_multistep=multistep, loads=loads, load_inputs=load_inputs,
        load_participates_when_empty=load_participates_when_empty,
        nondispatchable_inputs=nd_inputs, hvdc_inputs=hvdc_inputs, formulation="socp",
    )
    low, high = d["vmin_arr"], d["vmax_arr"]
    if np.any(~np.isfinite(low) | ~np.isfinite(high) | (low < 0) | (low > high)):
        raise ValueError("SOCP voltage bounds must be finite with 0 <= VMIN <= VMAX")
    d["_voltage_product_maps"] = make_voltage_product_maps(d["Ybus"], d["branch_admittance"])
    return d


def _metadata(d, options, delta, T, multistep, coupling_constraints):
    maps = d["_voltage_product_maps"]
    keys = ("baseMVA", "nb", "ref", "pv", "ext_to_int", "nl", "Ybus",
            "branch_from_bus_internal", "branch_to_bus_internal",
            "branch_from_bus_external", "branch_to_bus_external", "branch_status",
            "branch_rate_a_mva", "constrained_branch_indices", "vmin_arr", "vmax_arr")
    data = {key: d[key] for key in keys}
    data.update(voltage_product_pairs=maps.pairs.copy(),
                branch_voltage_product_pair=maps.branch_pair.copy(),
                branch_voltage_product_orientation=maps.branch_orientation.copy(),
                sparsity_tol=0.0, enforce_vset=bool(options.enforce_vset),
                enforce_branch_limits=bool(options.enforce_branch_limits), delta=delta)
    if multistep:
        data["T"] = T
    # Frozen numeric/owner inputs for explicit independent diagnostics, not
    # constraint objects or a second copy of device equations in the builder.
    components = d["_components"]
    data["_socp_audit_inputs"] = {
        "case": deepcopy(d["case"]),
        "units": {name: deepcopy(component.units) for name, component in components.components.items()},
        "arrays": {name: value.copy() for name, value in components.flat_data.items()
                   if isinstance(value, np.ndarray)},
        "has_extra_coupling": bool(coupling_constraints),
    }
    return publish_component_metadata(components, data)


def _build_socp_vectorized(
    case, df_P, df_Q, T, options, coupling_constraints, storage=None, delta=1.0,
    nondispatchable=None, df_nd=None, *, hvdc=None, df_hvdc_min=None, df_hvdc_max=None,
    generators=None, loads=None, load_inputs, load_participates_when_empty=False,
    nd_inputs=None, hvdc_inputs=None,
):
    from cvxopf.problem import OPFBuild

    d = _prepare(case, options, storage, delta, nondispatchable, T=T, multistep=True,
                 hvdc=hvdc, generators=generators, loads=loads, load_inputs=load_inputs,
                 load_participates_when_empty=load_participates_when_empty,
                 nd_inputs=nd_inputs, hvdc_inputs=hvdc_inputs)
    variables, network, constraints, state = _network_block(d, options, T)
    context = VectorizedContext("socp", T, delta, d["baseMVA"], d["_component_ext_to_int"], state)
    contributions = assemble_component_vectorized(d["_components"], context)
    aggregate = aggregate_vectorized_contributions(contributions)
    model = aggregate.model
    constraints += [network["p_net"] == model.injection.p_pu,
                    network["q_net"] == model.injection.q_pu]
    constraints += [*model.operating_constraints, *model.network_constraints,
                    *model.horizon.constraints, *coupling_constraints]
    cost = integrate_vectorized_stage_cost_rate(model.stage_cost_rate, delta)
    if model.horizon.terminal_cost is not None:
        cost += model.horizon.terminal_cost
    component_costs = integrate_vectorized_component_stage_costs(contributions, delta)
    projections = ResultProjectionRegistry(
        variables={name: ResultProjectionSpec(name, var.shape[:-1], var.shape[:-1], "interval")
                   for name, var in variables.items()},
        expressions={name: ResultProjectionSpec(name, expr.shape[:-1], expr.shape[:-1], "interval")
                     for name, expr in network.items()},
    )
    problem = cp.Problem(cp.Minimize(cost), constraints)
    if not problem.is_dcp():
        raise ValueError("SOCP requires a DCP objective and coupling constraints")
    return OPFBuild(
        prob=problem, formulation="socp", is_convex=True,
        variables=publish_vectorized_component_variables(aggregate, variables),
        expressions=publish_vectorized_component_expressions(aggregate, {**network, **component_costs}),
        data=_metadata(d, options, delta, T, True, coupling_constraints),
        temporal_assembly="vectorized",
        result_projections=merge_result_projection_registries(
            projections, vectorized_component_result_projections(aggregate, component_costs)),
    )


def _build_steps(d, options, delta, T, multistep, coupling_constraints):
    """Single-step and explicit reference assembly share the network block."""
    from cvxopf.problem import OPFBuild

    steps, aggregates, blocks, constraints = [], [], [], []
    for t in range(T):
        variables, expressions, network_constraints, state = _network_block(d, options, suffix=f"_{t}")
        context = StepContext("socp", t, d["baseMVA"], d["_component_ext_to_int"], state)
        contribution = assemble_component_step(d["_components"], context, variable_suffix=f"_{t}")
        aggregate = aggregate_step_contributions(contribution)
        steps.append(contribution)
        aggregates.append(aggregate)
        blocks.append((variables, expressions))
        constraints.extend((*network_constraints, *aggregate.operating_constraints,
                            *aggregate.network_constraints,
                            expressions["p_net"] == aggregate.injection.p_pu,
                            expressions["q_net"] == aggregate.injection.q_pu))
    horizon_parts = assemble_component_horizon(d["_components"], steps, HorizonContext("socp", T, delta))
    horizon = aggregate_horizon_contributions(horizon_parts)
    cost = integrate_stage_cost_rates([a.cost for a in aggregates], delta)
    if horizon.terminal_cost is not None:
        cost += horizon.terminal_cost
    constraints.extend((*horizon.constraints, *coupling_constraints))
    costs = dict(integrate_component_stage_costs(steps, delta))
    # The legacy step horizon reports storage's once-per-horizon cost separately.
    if "storage" in horizon_parts and horizon_parts["storage"].terminal_cost is not None:
        costs["storage_terminal_cost"] = horizon_parts["storage"].terminal_cost
    variables = {name: [block[0][name] for block in blocks] if multistep else blocks[0][0][name]
                 for name in blocks[0][0]}
    expressions = {name: [block[1][name] for block in blocks] if multistep else blocks[0][1][name]
                   for name in blocks[0][1]}
    problem = cp.Problem(cp.Minimize(cost), constraints)
    if not problem.is_dcp():
        raise ValueError("SOCP requires a DCP objective and coupling constraints")
    return OPFBuild(
        prob=problem, formulation="socp", is_convex=True,
        variables=publish_component_variables(steps, variables, multistep=multistep),
        expressions=publish_component_expressions(aggregates, horizon, {**expressions, **costs}, multistep=multistep),
        data=_metadata(d, options, delta, T, multistep, coupling_constraints),
    )


def _build_socp_single(case, options, storage=None, delta=1.0, nondispatchable=None,
                       *, hvdc=None, generators=None, loads=None):
    d = _prepare(case, options, storage, delta, nondispatchable, T=1, multistep=False,
                 hvdc=hvdc, generators=generators, loads=loads,
                 load_participates_when_empty=loads is not None)
    return _build_steps(d, options, delta, 1, False, [])


def _build_socp_multistep(
    case, df_P, df_Q, T, options, coupling_constraints, storage=None, delta=1.0,
    nondispatchable=None, df_nd=None, *, hvdc=None, df_hvdc_min=None, df_hvdc_max=None,
    generators=None, loads=None, load_inputs, load_participates_when_empty=False,
):
    # Frames already normalized/aligned by problem.py; no second alignment.
    d = _prepare(case, options, storage, delta, nondispatchable, T=T, multistep=True,
                 hvdc=hvdc, generators=generators, loads=loads, load_inputs=load_inputs,
                 load_participates_when_empty=load_participates_when_empty,
                 nd_inputs=None if df_nd is None else NondispatchableInputs(df_nd.to_numpy(dtype=float)),
                 hvdc_inputs=None if not hvdc else HVDCInputs(
                     df_hvdc_min.to_numpy(dtype=float), df_hvdc_max.to_numpy(dtype=float)))
    return _build_steps(d, options, delta, T, True, coupling_constraints)
