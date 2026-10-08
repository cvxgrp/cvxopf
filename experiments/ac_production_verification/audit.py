"""Reconstruct native objective, starts and physical restoration without solving."""

from dataclasses import fields

import cvxpy as cp
import numpy as np
from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxpy.reductions.solvers.solving_chain import SolvingChain

from cvxopf import extract_results
from cvxopf._cost_coordinates import cost_canonicalization, transform_cost_coordinates
from cvxopf._numerical_preparation import resolve_fixed_map
from experiments.ac_cost_qualification.audit import canonical_objective, cost_check
from experiments.numerical_preparation.audit import physical_audit, serializable, transformation_check
from experiments.numerical_preparation.tracy_variables import expand_primal

from .fixture import construct


class OriginalCyclingCanonicalization(Dnlp2Smooth):
    """Track the fixture's battery abs by leaf identity, not auxiliary shape."""

    def __init__(self, battery_id):
        super().__init__()
        self.battery_id = battery_id
        self.cycling_auxiliaries = []

    def canonicalize_expr(self, expr, args, *rest):
        result, constraints = super().canonicalize_expr(expr, args, *rest)
        if isinstance(expr, cp.abs) and {v.id for v in expr.variables()} == {self.battery_id}:
            self.cycling_auxiliaries.append((0, result))
        return result, constraints


def canonical(build):
    """Apply installed reductions only. No solver or oracle is invoked."""
    if build.numerical_preparation.cost_coordinates:
        solver_build, bindings = transform_cost_coordinates(build)
        reduction = cost_canonicalization(bindings)
    else:
        solver_build, bindings = build, ()
        reduction = OriginalCyclingCanonicalization(build.variables["b"].id)
    solver = IPOPT()
    data, inverse = SolvingChain(reductions=[
        CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES), reduction, solver,
    ]).apply(solver_build.prob)
    if len(reduction.cycling_auxiliaries) != 1:
        raise ValueError("this fixture requires one identity-traced cycling auxiliary")
    return solver_build, bindings, reduction.cycling_auxiliaries[0][1], data, inverse


def economic_checks(native, costs, slack, reconstruction, shedding):
    """Cycling slack is advisory; unexplained accounting stays a hard check."""
    excess, slack_l1 = float(np.sum(slack)), float(np.sum(abs(slack)))
    physical = float(sum(costs.values()))
    cycling = cost_check(costs["storage_cost"] + excess, costs["storage_cost"])
    unexplained = cost_check(native["obj_val"] - excess, physical)
    components = dict(
        generator_cost=cost_check(native["obj_val"] - costs["storage_cost"] - excess - shedding,
                                  costs["generator_cost"]),
        load_shedding_cost=cost_check(shedding, costs["load_shedding_cost"]))
    limit = 1e-7 + 1e-12 * abs(native["obj_val"])
    return dict(physical_objective=physical, canonical_objective=native["obj_val"],
                cycling_epigraph_excess=excess, cycling_slack_l1=slack_l1,
                cycling_warning=not cycling["passed"] or slack_l1 > cycling["limit"],
                unexplained=unexplained, components=components, reconstruction_error=reconstruction,
                reconstruction_limit=limit,
                passed=bool(np.isfinite([excess, slack_l1, reconstruction]).all()
                            and unexplained["passed"] and all(v["passed"] for v in components.values())
                            and reconstruction <= limit))


def publication_check(restored, published):
    """Exact trajectories; roundoff-aware scalar for equivalent cost evaluations."""
    physical, public = restored["objective"], published.get("objective")
    difference = abs(float(public)-physical)
    limit = 1e-7 + 1e-12 * abs(physical)
    if ({k: v for k, v in restored.items() if k != "objective"} !=
            {k: v for k, v in published.items() if k != "objective"} or
            not np.isfinite([physical, public]).all() or difference > limit):
        raise ValueError("public results differ from independently restored native physical values")
    return dict(objective_difference=difference, objective_limit=limit)


def assess(arm, native, evidence, published):
    """Independently restore from native slices and compare public engineering units."""
    call, kwargs, build, start, stress = construct(arm)
    solver_build, bindings, auxiliary, data, inverse = canonical(build)
    layout = [(list(v.shape), inverse[-1].var_offsets[v.id],
               inverse[-1].var_offsets[v.id] + v.size) for v in data["problem"].variables()]
    retained = [(v[1], v[2], v[3]) for v in evidence["start_layout"]]
    if layout != retained or not np.array_equal(data["x0"], evidence["assigned_x0"]):
        raise ValueError("fresh canonical layout/start differs from verified production x0")
    mapping = resolve_fixed_map(solver_build._exact_boxes, inverse[:-1], inverse[-1],
                               data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    expected = serializable({f.name: getattr(mapping, f.name) for f in fields(mapping)
                             if not f.name.startswith("_")})
    if evidence["coordinates"] != expected or evidence["native"] != native:
        raise ValueError("retained fixed map/native differs from fresh preparation")
    full = expand_primal(native, evidence)
    rebuilt, _ = canonical_objective(data, inverse, full)
    for variable in solver_build.prob.variables():
        offset = inverse[-1].var_offsets[variable.id]
        variable.save_value(full[offset:offset+variable.size].reshape(variable.shape, order="F"))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value / binding.scale)
    build.prob._status, build.prob._value = cp.OPTIMAL, float(build.prob.objective.value)
    result = serializable(extract_results(build))
    publication = publication_check(result, published)
    named = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
    common, _ = physical_audit(build, result, kwargs, named)
    costs = common["costs"]
    weights = (np.ones(auxiliary.shape) if bindings else
               kwargs["delta"] * np.asarray(build.data["storage_aging_weight"])[:, None])
    cycling = next((binding for binding in bindings if binding.term.kind == "cycling"), None)
    shedding_binding = next((binding for binding in bindings if binding.term.kind == "shedding"), None)
    battery = cycling.leaf.value if cycling is not None else build.variables["b"].value
    slack = weights * (auxiliary.value - abs(battery))
    shedding = (float(np.sum(shedding_binding.leaf.value)) if shedding_binding is not None else
                float(build.expressions["load_shedding_cost"].value))
    economics = economic_checks(native, costs, slack, abs(rebuilt-native["obj_val"]), shedding)
    captured = dict(complete_x0=evidence["assigned_x0"], layout=[
        dict(shape=shape, start=begin, stop=end) for shape, begin, end in layout])
    structure = transformation_check(call, kwargs, evidence, captured)
    shed = np.asarray(result["p_load_shed"]).sum(axis=1)
    forced_passed = not stress["forced_shedding"] or (
        result["energy_not_served"] > 1e-4 and
        result["energy_not_served"] + 1e-4 >= stress["modified"]["minimum_ens_mwh"] and
        np.all(shed + 1e-4 >= stress["modified"]["minimum_shed_mw"]))
    return serializable(dict(common=common, economics=economics, transformation=structure, publication=publication,
                             forced_shedding_passed=bool(forced_passed),
                             passed=bool(native["status"] == 0 and common["passed"]
                                         and economics["passed"] and structure["passed"] and forced_passed)))
