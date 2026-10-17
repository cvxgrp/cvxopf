"""Independent canonical/physical economic replay; no optimization calls."""

import numpy as np
from dataclasses import fields
from cvxopf._numerical_preparation import resolve_fixed_map

from experiments.ac_cost_coordinates import model as m, run as diagnostic
from experiments.numerical_preparation.audit import serializable
from experiments.numerical_preparation.tracy_variables import expand_primal
from .fixture import GATES


def cost_check(canonical, physical):
    limit = GATES["cost_abs"] + GATES["cost_rel"] * abs(physical)
    difference = abs(canonical-physical)
    return dict(canonical=float(canonical), physical=float(physical), difference=float(difference),
                limit=float(limit), passed=bool(np.isfinite([canonical, physical]).all() and difference <= limit))


def normalized_layout(layout):
    # Automatically generated auxiliary names/IDs differ across fresh builds.
    return [(item["name"] if item["is_original_variable"] else None,
             list(item["shape"]), item["start"], item["stop"], item["is_original_variable"])
            for item in layout]


def canonical_objective(data, inverse, x, *, gradient=False):
    """Evaluate only the smooth canonical objective, not network derivatives.

    The installed canonicalization already owns the auxiliary representation.
    Assign its exact leaf slices, then use the CVXPY expression's value and
    objective-only gradient. No constraint Jacobian/Lagrangian Hessian or native
    optimizer is instantiated. Network-only coordinates have zero objective
    gradient and cannot increase its maximum.
    """
    for variable in data["problem"].variables():
        offset = inverse[-1].var_offsets[variable.id]
        variable.save_value(x[offset:offset+variable.size].reshape(variable.shape, order="F"))
    expression = data["problem"].objective.expr
    value = float(expression.value)
    maximum = None
    if gradient:
        derivatives = expression.grad
        maximum = 0.
        for variable in expression.variables():
            block = derivatives[variable]
            if block is None:
                raise ValueError("canonical objective gradient unavailable")
            values = block.toarray() if hasattr(block, "toarray") else np.asarray(block)
            if not np.isfinite(values).all():
                raise ValueError("nonfinite canonical objective gradient")
            maximum = max(maximum, float(np.max(abs(values), initial=0)))
    return value, maximum


def assess(view, call, kwargs, start, stress, native, evidence, captured):
    """Rebuild start/layout/objective, restore native primal and audit in MW/MWh."""
    view.assign(start)
    data, inverse = m.canonical_data(view.solver)
    original_ids = {v.id for v in view.solver.prob.variables()}
    layout = []
    for variable in data["problem"].variables():
        offset = inverse[-1].var_offsets[variable.id]
        layout.append(dict(name=variable.name(), shape=list(variable.shape), start=offset,
                           stop=offset+variable.size, is_original_variable=variable.id in original_ids))
    if (normalized_layout(layout) != normalized_layout(captured["layout"]) or
            not np.array_equal(data["x0"], np.asarray(captured["complete_x0"], float))):
        raise ValueError("fresh canonical layout/start differs from retained verified x0")
    if evidence is not None:
        mapping = resolve_fixed_map(view.solver._exact_boxes, inverse[:-1], inverse[-1],
            data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
        expected = serializable({field.name: getattr(mapping, field.name) for field in fields(mapping)
                                 if not field.name.startswith("_")})
        if evidence["coordinates"] != expected or evidence["native"] != serializable(native):
            raise ValueError("retained preparation map/native differs from fresh identity-bound map")
    full = expand_primal(native, evidence)
    if full.shape != np.asarray(data["x0"]).shape:
        raise ValueError("retained native primal dimension differs from fresh canonical problem")
    rebuilt_objective, _ = canonical_objective(data, inverse, full)
    reconstruction = abs(rebuilt_objective - native["obj_val"])
    reconstruction_limit = GATES["reconstruction_abs"] + GATES["reconstruction_rel"] * abs(native["obj_val"])
    _, initial_gradient = canonical_objective(data, inverse, np.asarray(data["x0"]), gradient=True)
    m.restore_archive(view, dict(native=native, preparation_evidence=evidence), captured)
    checks = diagnostic.assess(view, call, kwargs, native, evidence, captured)
    physical = checks["common"]["costs"]
    cycling = physical["storage_cost"] + checks["accounting"]["cycling_epigraph_excess"]
    if "load_shed_fraction" in view.leaves:
        shedding = float(np.sum(view.leaves["load_shed_fraction"].value))
    else:
        indices = np.asarray(view.physical.data["sheddable_load_indices"], int)
        demand = np.asarray(view.physical.data["load_p_source_mw"]).T[indices]
        voll = np.asarray(view.physical.data["load_shedding_cost_per_mwh"])[indices, None]
        shedding = float(kwargs["delta"] * np.sum(demand * voll * view.physical.variables["load_shed_fraction"].value))
    components = {
        "storage_cost": cost_check(cycling, physical["storage_cost"]),
        "load_shedding_cost": cost_check(shedding, physical["load_shedding_cost"]),
        "generator_cost": cost_check(native["obj_val"]-cycling-shedding, physical["generator_cost"]),
    }
    # Bound absolute auxiliary excess as well as net excess: positive/negative
    # per-coordinate errors must not cancel each other or other cost components.
    shape = view.physical.variables["b"].shape
    auxiliary = next(item for item in captured["layout"] if not item["is_original_variable"]
                     and tuple(item["shape"]) == shape)
    t = full[auxiliary["start"]:auxiliary["stop"]].reshape(shape, order="F")
    if "b" in view.leaves:
        slack = t - abs(view.leaves["b"].value)
    else:
        slack = kwargs["delta"] * np.asarray(view.physical.data["storage_aging_weight"])[:, None] * (
            t - abs(view.physical.variables["b"].value))
    slack_l1 = float(np.sum(abs(slack)))
    slack_pass = slack_l1 <= components["storage_cost"]["limit"]
    ens = float(checks["result"]["energy_not_served"])
    forced = dict(required=stress["forced_shedding"], energy_not_served_mwh=ens,
                  minimum_ens_mwh=stress["modified"]["minimum_ens_mwh"], passed=True)
    if forced["required"]:
        shed = np.asarray(checks["result"]["p_load_shed"], float).sum(axis=1)
        forced["passed"] = bool(ens > GATES["forced_shedding_energy_mwh"] and
            ens + GATES["forced_shedding_energy_mwh"] >= forced["minimum_ens_mwh"] and
            np.all(shed + 1e-4 >= stress["modified"]["minimum_shed_mw"]))
    economics = dict(total=cost_check(native["obj_val"], sum(physical.values())), components=components,
        cycling_slack_l1=slack_l1, minimum_cycling_cost_slack=float(np.min(slack)),
        maximum_cycling_cost_slack=float(np.max(slack)), cycling_slack_passed=slack_pass,
        reconstructed_canonical_objective=rebuilt_objective, reconstruction_error=reconstruction,
        reconstruction_limit=reconstruction_limit,
        initial_maximum_objective_gradient=initial_gradient,
        inferred_default_gradient_objective_scale=min(1., 100./initial_gradient) if initial_gradient else 1.)
    economics["passed"] = bool(economics["total"]["passed"] and all(v["passed"] for v in components.values())
                               and slack_pass and np.isfinite(reconstruction) and reconstruction <= reconstruction_limit)
    checks.update(economics=economics, forced_shedding=forced)
    checks["passed"] = bool(native["status"] == 0 and checks["common"]["passed"]
                            and checks["transformation"]["passed"] and economics["passed"] and forced["passed"])
    return serializable(checks)
