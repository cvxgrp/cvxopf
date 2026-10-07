"""Independent original-space costs, projections, feasibility and warnings."""

from numbers import Real

import numpy as np
from scipy import sparse

from experiments.numerical_preparation.audit import physical_audit, serializable
from experiments.numerical_preparation.tracy_variables import check_coordinates
from . import fixture as f, model


def cost_check(canonical, physical):
    error = abs(float(canonical)-float(physical))
    limit = f.GATES["cost_abs"]+f.GATES["cost_rel"]*abs(float(physical))
    return dict(canonical=float(canonical), physical=float(physical), difference=error,
                limit=limit, passed=bool(np.isfinite([canonical, physical]).all() and error <= limit))


def economic_checks(problem, full, view, kwargs, physical):
    """Account for automatic abs slack without hiding it in a large total bill.

    The frozen polynomial-only fixtures have diagonal quadratic terms, generator
    linear terms, affine shedding and exactly one positively priced abs auxiliary.
    Constraint-only auxiliaries (e.g. normalized disks) are not cycling costs.
    Reject other objective structures rather than silently misattributing them.
    """
    x, P, c = np.asarray(full.x), problem.data["P"], problem.data["c"]
    layout = problem.layout
    by_name = {v["name"]: v for v in layout if v["is_original_variable"]}

    def indices(name):
        item = by_name[name]
        return np.arange(item["start"], item["stop"])

    auxiliaries = [v for v in layout if not v["is_original_variable"]
                   and tuple(v["shape"]) == view.physical.variables["b"].shape
                   and np.all(c[v["start"]:v["stop"]] > 0)]
    if len(auxiliaries) != 1:
        raise ValueError("expected exactly one positively priced cycling auxiliary")
    item = auxiliaries[0]
    cycling_indices = np.arange(item["start"], item["stop"])
    shedding_indices = indices("load_shed_fraction_cost_coordinate" if view.scales else "load_shed_fraction")
    generation_indices = indices("Pg")
    loss_indices = indices("p_flows") if kwargs["formulation"] == "lossy_dc" else np.array([], int)
    allowed_linear = np.concatenate((cycling_indices, shedding_indices, generation_indices))
    if np.any(c[np.setdiff1d(np.arange(len(c)), allowed_linear)] != 0) or (P-sparse.diags(P.diagonal())).nnz:
        raise ValueError("unsupported canonical objective terms/cross terms")
    if np.any(P.diagonal()[np.concatenate((cycling_indices, shedding_indices))] != 0):
        raise ValueError("unexpected nonlinear cycling/shedding cost")
    terms = .5*x*(P@x)
    loss = float(terms[loss_indices].sum())
    generation = float(terms.sum()-loss+c[generation_indices]@x[generation_indices]+problem.signature["objective_constant"])
    cycling = float(c[cycling_indices]@x[cycling_indices])
    shedding = float(c[shedding_indices]@x[shedding_indices])
    canonical = float(.5*x@(P@x)+c@x+problem.signature["objective_constant"])
    native_restored = float(full.obj_val)+problem.signature["objective_constant"]
    reconstruction = abs(canonical-native_restored)
    reconstruction_limit = f.GATES["reconstruction_abs"]+f.GATES["reconstruction_rel"]*abs(native_restored)
    components = {"generator_cost": cost_check(generation, physical["generator_cost"]),
                  "storage_cost": cost_check(cycling, physical["storage_cost"]),
                  "load_shedding_cost": cost_check(shedding, physical["load_shedding_cost"])}
    if kwargs["formulation"] == "lossy_dc":
        components["dc_loss_cost"] = cost_check(loss, physical["dc_loss_cost"])
    weights = np.broadcast_to(kwargs["delta"]*np.asarray(view.physical.data["storage_aging_weight"])[:, None],
                              view.physical.variables["b"].shape)
    t = x[cycling_indices].reshape(weights.shape, order="F")
    expected_coefficients = np.ones(weights.shape) if view.scales else weights
    np.testing.assert_array_equal(c[cycling_indices], expected_coefficients.ravel(order="F"))
    slack = expected_coefficients*t - weights*abs(view.physical.variables["b"].value)
    slack_l1 = float(np.sum(abs(slack)))
    cycling_ok = components["storage_cost"]["passed"] and slack_l1 <= components["storage_cost"]["limit"]
    total = cost_check(canonical, sum(physical.values()))
    # An explained cycling gap must not reject indirectly through total cost.
    explained = cost_check(canonical-float(slack.sum()), sum(physical.values()))
    hard = bool(np.isfinite(x).all() and np.isfinite(reconstruction) and reconstruction <= reconstruction_limit
                and explained["passed"] and all(v["passed"] for k, v in components.items() if k != "storage_cost"))
    return dict(passed=hard, total=total, total_excluding_cycling_gap=explained, components=components,
                canonical_objective=canonical, restored_native_objective=native_restored,
                reconstruction_error=reconstruction, reconstruction_limit=reconstruction_limit,
                cycling_slack_l1=slack_l1, cycling_slack_sum=float(slack.sum()),
                minimum_cycling_cost_slack=float(np.min(slack)), maximum_cycling_cost_slack=float(np.max(slack)),
                cycling_gap_warning=not cycling_ok,
                warnings=[] if cycling_ok else ["cycling cost/absolute auxiliary slack exceeds advisory threshold"])


def assess(view, kwargs, stress, native, preparation_evidence, signature):
    problem = model.canonical(view.solver)
    if problem.signature != signature:
        raise ValueError("fresh canonical data/map/layout differs from bound signature")
    if bool(preparation_evidence) != view.solver.numerical_preparation.enabled:
        raise ValueError("missing or unexpected preparation evidence")
    if preparation_evidence:
        if (preparation_evidence["native"]["status"] != native["status"] or
                preparation_evidence["coordinates"]["fixed"] != signature["fixed"] or
                preparation_evidence["coordinates"]["values"] != signature["fixed_values"] or
                preparation_evidence["coordinates"]["dropped"] != signature["dropped"] or
                preparation_evidence["variable_scale"] != signature["variable_scale"] or
                preparation_evidence["row_scale"] != signature["row_scale"] or
                preparation_evidence["objective_offset"] != signature["substitution_offset"]):
            raise ValueError("retained preparation map differs from fresh map")
        for name in ("x", "s", "z", "obj_val", "obj_val_dual"):
            np.testing.assert_array_equal(preparation_evidence["native"][name], native[name])
    full, result, named, diagnostics = model.restore_view(view, problem, native)
    common, relaxation = physical_audit(view.physical, result, kwargs, named)
    # Independently map original canonical slices through cost-coordinate inverses
    # before checking engineering units, time axes and all published leaf arrays.
    projected_x, layout = np.asarray(full.x).copy(), []
    physical_raw = {name: var.value for name, var in view.physical.variables.items()}
    aliases = {name+"_cost_coordinate": name for name in view.scales}
    for item in problem.layout:
        item = dict(item)
        name = item["name"]
        if name in aliases:
            name = aliases[name]
            projected_x[item["start"]:item["stop"]] /= view.scales[name].ravel(order="F")
            item["name"] = name
        layout.append(item)
    projection = check_coordinates(dict(full_x=projected_x, layout=layout, raw=physical_raw), result, kwargs)
    economics = economic_checks(problem, full, view, kwargs, common["costs"])
    diagnostics_ok = all(isinstance(native.get(key), Real) and not isinstance(native[key], bool)
                         and np.isfinite(native[key]) for key in
                         ("obj_val", "obj_val_dual", "r_prim", "r_dual", "solve_time", "iterations"))
    diagnostics_ok = bool(diagnostics_ok and all(native[key] >= 0 for key in
                          ("r_prim", "r_dual", "solve_time", "iterations"))
                          and isinstance(native["iterations"], int))
    forced = dict(required=stress["forced_shedding"], passed=True,
                  energy_not_served_mwh=float(result["energy_not_served"]),
                  minimum_ens_mwh=stress["modified"]["minimum_ens_mwh"])
    if forced["required"]:
        shed = np.sum(np.asarray(result["p_load_shed"], float), axis=1)
        forced["passed"] = bool(forced["energy_not_served_mwh"] > f.GATES["forced_shedding_energy_mwh"] and
            forced["energy_not_served_mwh"]+1e-4 >= forced["minimum_ens_mwh"] and
            np.all(shed+1e-4 >= stress["modified"]["minimum_shed_mw"]))
    return serializable(dict(passed=bool(native["status"] == "Solved" and common["passed"] and
        diagnostics_ok and projection["passed"] and economics["passed"] and forced["passed"]),
        native_diagnostics_passed=diagnostics_ok, result=result, named_costs=named,
        common=common, relaxation=relaxation, coordinate_checks=projection, economics=economics,
        forced_shedding=forced, original_space_diagnostics=diagnostics))
