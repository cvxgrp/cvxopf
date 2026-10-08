"""Matched objective grouping; production preparation and public solving stay fixed.

This is a closed Tracy fixture experiment, not a proposed general cost registry.
The AC candidate reuses the reviewed local typed-substitution adapter that
reproduced the historical graph. Convex candidates sum existing integrated
component costs, without cost-coordinate substitution. No production defaults,
solver methods, or reduction registries are changed here.
"""

from dataclasses import asdict, dataclass, fields, replace

import cvxpy as cp
import numpy as np

from cvxopf import NumericalPreparation, build_opf_multistep, extract_results
from cvxopf._numerical_preparation import resolve_fixed_map
from experiments.ac_cost_coordinates.model import Coordinates
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.ac_objective_assembly import model as ac_grouping
from experiments.ac_production_verification import audit as ac_audit
from experiments.convex_cost_qualification import fixture as convex_fixture, model as convex_model
from experiments.convex_cost_qualification.audit import assess as convex_assess
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import physical_audit, serializable, transformation_check
from experiments.numerical_preparation.tracy_variables import expand_primal
from tests.socp_matched import digest

FORMS = ("ac", "socp", "lossy_dc", "singlenode_dc")
FIXTURES = ((3, False), (3, True), (6, True), (24, True))
LIMITS = dict(max_launches=32, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=5760., poll_seconds=1.)


@dataclass(frozen=True)
class Arm:
    id: int
    formulation: str
    T: int
    forced_shedding: bool
    assembly: str
    start: int = 1165

    @property
    def group(self):
        return f"{self.formulation}:tracy:T{self.T}:{self.start}:forced={self.forced_shedding}"


def arms():
    return tuple(Arm(i+1, form, T, forced, assembly)
                 for i, (form, T, forced, assembly) in enumerate(
                     (form, T, forced, assembly) for T, forced in FIXTURES for form in FORMS
                     for assembly in ("hourly", "component_first")))


def component_first(build):
    """Change grouping only; reject costs outside the reviewed fixture schema.

    Named costs already integrate delta. Do not multiply by delta again or
    subtract terms from the total (which would introduce cancellation). Richer
    costs/terminal penalties require a separate generalization, not silent loss.
    """
    if build.formulation == "ac":
        objective = ac_grouping.ComponentFirstObjective(build)
    else:
        expected = ["generator_cost", "storage_cost", "load_shedding_cost"]
        if build.formulation == "lossy_dc":
            expected.append("dc_loss_cost")
        if (build.formulation not in FORMS[1:] or build.temporal_assembly != "vectorized"
                or {k for k in build.expressions if k.endswith("_cost")} != set(expected)
                or build.numerical_preparation.cost_coordinates):
            raise ValueError("unsupported objective-assembly fixture")
        objective = cp.Minimize(sum(build.expressions[k] for k in expected))
    candidate = replace(build, prob=cp.Problem(objective, build.prob.constraints))
    if {v.id for v in candidate.prob.variables()} != {v.id for v in build.prob.variables()}:
        raise ValueError("objective assembly changed physical leaves")
    return candidate


def construct(arm, prepared=None):
    if arm not in arms():
        raise ValueError("undeclared objective-assembly arm")
    source = convex_fixture.Arm(arm.id, arm.formulation, "tracy", arm.T, arm.start,
                               arm.forced_shedding, True, False)
    call, kwargs, stress = convex_fixture.kwargs_for_arm(source, prepared)
    if arm.formulation == "ac":
        call = replace(call, treatment="combined_ac")
        kwargs["options"] = replace(kwargs["options"], numerical_preparation=NumericalPreparation(
            normalize_device_limits=True, exact_fixed_boxes=True, cost_coordinates=True))
    physical = build_opf_multistep(**kwargs)
    if arm.formulation == "ac":
        start = q.physical_start(physical, kwargs)
    else:
        # Algebra only: CLARABEL's actual optimizer starts cold, in both arms.
        start = {v.name(): np.zeros(v.shape) for v in physical.prob.variables()}
        start["b"][:] = 2.
        start["load_shed_fraction"][:] = .1
    solver = component_first(physical) if arm.assembly == "component_first" else physical
    view = Coordinates(physical, solver, {}, {})
    check = convex_model.equivalence(view, start)
    if not check["passed"] or (arm.formulation != "ac" and not solver.prob.is_dcp()):
        raise ValueError("objective grouping algebra/constraint equivalence failed")
    return call, kwargs, view, stress, start, check


def canonical(view):
    if view.solver.formulation == "ac":
        return ac_grouping.canonical(view.solver)
    return convex_model.canonical(view.solver)


def signature(view, problem):
    if view.solver.formulation != "ac":
        return problem.signature
    data = problem[-2]
    return serializable(dict(layout=ac_grouping.layout(*problem), canonical_x0=data["x0"],
        bounds_sha256=digest(serializable({k: data[k] for k in ("lb", "ub", "cl", "cu")}))))


def row_binding(arm, prepared=None):
    call, kwargs, view, stress, start, check = construct(arm, prepared)
    frozen = original.call_binding(call, kwargs)
    frozen["policy"] = asdict(view.solver.numerical_preparation)
    return serializable(dict(arm=asdict(arm), group=arm.group,
        frozen=frozen, stress=stress,
        physical_start=start if arm.formulation == "ac" else None,
        algebra_point=start, equivalence=check, canonical=signature(view, canonical(view))))


def assess_ac(arm, native, evidence, published):
    """Replay AC slices, typed coordinate inverse, accounting and public values."""
    call, kwargs, view, stress, _, _ = construct(arm)
    build = view.solver
    solver, bindings, auxiliary, data, inverse = canonical(view)
    layout = [(list(v.shape), inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
              for v in data["problem"].variables()]
    if (layout != [(v[1], v[2], v[3]) for v in evidence["start_layout"]]
            or not np.array_equal(data["x0"], evidence["assigned_x0"])):
        raise ValueError("fresh AC canonical layout/start differs from retained evidence")
    mapping = resolve_fixed_map(solver._exact_boxes, inverse[:-1], inverse[-1],
        data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    expected = serializable({f.name: getattr(mapping, f.name) for f in fields(mapping)
                             if not f.name.startswith("_")})
    if evidence["coordinates"] != expected or evidence["native"] != native:
        raise ValueError("AC retained map/native differs from independent replay")
    full = expand_primal(native, evidence)
    rebuilt, _ = canonical_objective(data, inverse, full)
    for variable in solver.prob.variables():
        offset = inverse[-1].var_offsets[variable.id]
        variable.save_value(full[offset:offset+variable.size].reshape(variable.shape, order="F"))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value/binding.scale)
    build.prob._status, build.prob._value = cp.OPTIMAL, float(view.physical.prob.objective.value)
    result = serializable(extract_results(build))
    publication = ac_audit.publication_check(result, published)
    named = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
    common, _ = physical_audit(build, result, kwargs, named)
    cycling = next(b for b in bindings if b.term.kind == "cycling")
    shedding = next(b for b in bindings if b.term.kind == "shedding")
    economics = ac_audit.economic_checks(native, common["costs"], auxiliary.value-abs(cycling.leaf.value),
        abs(rebuilt-native["obj_val"]), float(np.sum(shedding.leaf.value)))
    structure = transformation_check(call, kwargs, evidence, dict(complete_x0=evidence["assigned_x0"],
        layout=[dict(shape=shape, start=begin, stop=end) for shape, begin, end in layout]))
    shed = np.asarray(result["p_load_shed"]).sum(axis=1)
    forced = not stress["forced_shedding"] or bool(result["energy_not_served"] > 1e-4
        and result["energy_not_served"]+1e-4 >= stress["modified"]["minimum_ens_mwh"]
        and np.all(shed+1e-4 >= stress["modified"]["minimum_shed_mw"]))
    return serializable(dict(common=common, economics=economics, publication=publication,
        transformation=structure, forced_shedding_passed=forced, result=result,
        passed=bool(native["status"] == 0 and common["passed"] and economics["passed"]
                    and structure["passed"] and forced)))


def assess(arm, native, evidence, published):
    if arm.formulation == "ac":
        return assess_ac(arm, native, evidence, published)
    _, kwargs, view, stress, _, _ = construct(arm)
    checks = convex_assess(view, kwargs, stress, native, evidence, signature(view, canonical(view)))
    # Installed CVXPY recomputes the public objective after variable inversion;
    # native canonical cycling slack is retained separately in economic checks.
    checks["publication"] = ac_audit.publication_check(serializable(checks["result"]), published)
    return serializable(checks)


def warning(checks):
    economics = checks.get("economics", {})
    return bool(economics.get("cycling_warning", economics.get("cycling_gap_warning", False)))


def physical_cost(checks):
    return float(sum(checks["common"]["costs"].values()))
