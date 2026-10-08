"""Two objective assemblies, using production coordinate mapping and public solve.

The component-first arm uses a local Objective.tree_copy adapter. When the
production mapper supplies its typed rate substitutions, this adapter assembles
their cost-valued leaves as generator + cycling + shedding horizon totals.
It does not patch a solver, reduction registry, production function or class.
The adapter disappears before DNLP: it returns an ordinary cp.Minimize.
"""

from dataclasses import asdict, dataclass, fields, replace

import cvxpy as cp
from cvxpy.atoms.affine.sum import Sum
import numpy as np

from cvxopf import extract_results
from cvxopf._cvxpy_dispatch import sparse_dispatch_policy
from cvxopf._numerical_preparation import resolve_fixed_map
from experiments.ac_production_verification import fixture as production, audit
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.numerical_preparation.audit import physical_audit, serializable, transformation_check
from experiments.numerical_preparation.tracy_variables import expand_primal

LIMITS = dict(max_launches=2, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=360., poll_seconds=1.)


@dataclass(frozen=True)
class Arm:
    id: int
    assembly: str


def arms():
    return (Arm(1, "hourly"), Arm(2, "component_first"))


class ComponentFirstObjective(cp.Minimize):
    """Fixture-only grouping at the existing production substitution boundary."""

    def __init__(self, build):
        terms = build._cost_coordinate_terms
        if (build.formulation != "ac" or build.temporal_assembly != "vectorized"
                or build._cost_coordinate_delta != 1.
                or {k for k in build.expressions if k.endswith("_cost")} !=
                {"generator_cost", "storage_cost", "load_shedding_cost"}
                or len(terms) != 2 or {term.kind for term in terms} != {"cycling", "shedding"}
                or any(np.any(np.asarray(term.weights.value) <= 0) for term in terms)):
            raise ValueError("component-first adapter requires the frozen positive-cost AC fixture")
        self.terms = {term.kind: term for term in terms}
        self.generator = build.expressions["generator_cost"]
        super().__init__(build.prob.objective.expr)

    def tree_copy(self, id_objects=None):
        # Ordinary copies are ordinary objectives. Only the production map's
        # complete, typed substitutions activate the diagnostic grouping.
        present = [id(term.rate) in (id_objects or {}) for term in self.terms.values()]
        if any(present) and not all(present):
            raise ValueError("incomplete production cost rate substitutions")
        if not all(present):
            return cp.Minimize(self.args[0].tree_copy(id_objects))
        parts = {}
        for kind, term in self.terms.items():
            rate = id_objects[id(term.rate)]
            if not isinstance(rate, Sum) or rate.axis != 0:
                raise ValueError("production cost rate no longer has unit cost-valued weights")
            weighted = rate.args[0]
            if (not isinstance(weighted, cp.multiply)
                    or not np.array_equal(np.asarray(weighted.args[0].value), np.ones(term.variable.shape))):
                raise ValueError("production cost rate no longer has unit cost-valued weights")
            parts[kind] = weighted.args[1]
        if not isinstance(parts["cycling"], cp.abs) or not isinstance(parts["shedding"], cp.Variable):
            raise ValueError("production cost coordinate representation changed")
        return cp.Minimize(self.generator.tree_copy(id_objects)
                           + cp.sum(parts["cycling"]) + cp.sum(parts["shedding"]))


def construct(arm, prepared=None):
    if arm not in arms():
        raise ValueError("undeclared diagnostic arm")
    source = replace(production.arms()[-1], id=arm.id)
    call, kwargs, build, start, stress = production.construct(source, prepared)
    if arm.assembly == "component_first":
        build = replace(build, prob=cp.Problem(ComponentFirstObjective(build), build.prob.constraints))
    return call, kwargs, build, start, stress


def canonical(build):
    with sparse_dispatch_policy(build.automatic_sparse_dispatch):
        return audit.canonical(build)


def layout(solver_build, bindings, auxiliary, data, inverse):
    """Stable labels for physical leaves and the identity-traced cycling auxiliary."""
    names = {binding.leaf.id: binding.term.kind + "_coordinate" for binding in bindings}
    originals = {v.id for v in solver_build.prob.variables()}
    other = 0
    rows = []
    for v in data["problem"].variables():
        name = names.get(v.id, v.name())
        if v.id == auxiliary.id:
            name = "cycling_epigraph"
        elif v.id not in originals:
            name = f"network_auxiliary_{other}"
            other += 1
        offset = inverse[-1].var_offsets[v.id]
        rows.append(dict(name=name, shape=list(v.shape), start=offset, stop=offset+v.size))
    return rows


def row_binding(arm, prepared=None):
    source = replace(production.arms()[-1], id=arm.id)
    frozen = production.row_binding(source, prepared)
    _, _, build, _, _ = construct(arm, prepared)
    values = canonical(build)
    return serializable(dict(arm=asdict(arm), fixture=frozen, layout=layout(*values),
                             canonical_x0=values[-2]["x0"]))


def assess(arm, native, evidence, published):
    """Independently replay this arm's native layout and production restoration."""
    call, kwargs, build, _, stress = construct(arm)
    solver_build, bindings, auxiliary, data, inverse = canonical(build)
    retained = [(v[1], v[2], v[3]) for v in evidence["start_layout"]]
    fresh = [(list(v.shape), inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
             for v in data["problem"].variables()]
    if fresh != retained or not np.array_equal(data["x0"], evidence["assigned_x0"]):
        raise ValueError("canonical layout/start differs from retained production evidence")
    mapping = resolve_fixed_map(solver_build._exact_boxes, inverse[:-1], inverse[-1],
                               data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    expected = serializable({f.name: getattr(mapping, f.name) for f in fields(mapping)
                             if not f.name.startswith("_")})
    if evidence["coordinates"] != expected or evidence["native"] != native:
        raise ValueError("fixed map/native differs from retained production evidence")
    full = expand_primal(native, evidence)
    rebuilt, _ = canonical_objective(data, inverse, full)
    for v in solver_build.prob.variables():
        offset = inverse[-1].var_offsets[v.id]
        v.save_value(full[offset:offset+v.size].reshape(v.shape, order="F"))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value / binding.scale)
    build.prob._status, build.prob._value = cp.OPTIMAL, float(build.prob.objective.value)
    result = serializable(extract_results(build))
    publication = audit.publication_check(result, published)
    named = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
    common, _ = physical_audit(build, result, kwargs, named)
    cycling = next(b for b in bindings if b.term.kind == "cycling")
    shedding = next(b for b in bindings if b.term.kind == "shedding")
    slack = auxiliary.value - abs(cycling.leaf.value)
    economics = audit.economic_checks(native, common["costs"], slack,
                                     abs(rebuilt-native["obj_val"]), float(np.sum(shedding.leaf.value)))
    structure = transformation_check(call, kwargs, evidence, dict(complete_x0=evidence["assigned_x0"],
        layout=[dict(shape=shape, start=begin, stop=end) for shape, begin, end in fresh]))
    shed = np.asarray(result["p_load_shed"]).sum(axis=1)
    forced = bool(result["energy_not_served"] > 1e-4
                  and result["energy_not_served"]+1e-4 >= stress["modified"]["minimum_ens_mwh"]
                  and np.all(shed+1e-4 >= stress["modified"]["minimum_shed_mw"]))
    return serializable(dict(common=common, economics=economics, publication=publication,
        transformation=structure, forced_shedding_passed=forced,
        passed=bool(native["status"] == 0 and common["passed"] and economics["passed"]
                    and structure["passed"] and forced)))
