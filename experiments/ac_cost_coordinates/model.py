"""Exact experimental coordinate substitution; never solve on import.

Physical variables remain on the reference build for unchanged extraction and
auditing. The solver build substitutes signed integrated cycling-cost and
integrated shedding-cost coordinates. Positive economic weights are preserved
in the inverse maps, not replaced with a different physical objective. CVXPY
constructs its own abs auxiliaries; this module introduces no epigraph.
"""

from dataclasses import dataclass, replace

import cvxpy as cp
import numpy as np

from experiments.numerical_preparation import fixture as f, run_qualification as q
from experiments.numerical_preparation.audit import serializable
from experiments.numerical_preparation.tracy_variables import expand_primal

ARMS = ((24, False), (24, True), (25, False), (25, True))


def canonical_data(build):
    """Assemble the installed nonlinear representation without optimizing."""
    from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
    from cvxpy.reductions.dnlp2smooth.dnlp2smooth import Dnlp2Smooth
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    from cvxpy.reductions.solvers.solving_chain import SolvingChain
    solver = IPOPT()
    return SolvingChain(reductions=[CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES),
                                   Dnlp2Smooth(), solver]).apply(build.prob)


@dataclass
class Coordinates:
    physical: object
    solver: object
    scales: dict
    leaves: dict

    def assign(self, values):
        """Assign a complete physical start, applying the forward diagonal map."""
        for variable in self.physical.prob.variables():
            variable.save_value(np.asarray(values[variable.name()], float).copy())
        for name, scale in self.scales.items():
            self.leaves[name].save_value(np.asarray(values[name], float) * scale)

    def restore(self):
        """Restore physical values verbatim, without projection or clipping."""
        for name, scale in self.scales.items():
            self.physical.variables[name].save_value(self.leaves[name].value / scale)
        return replace(self.physical, prob=self.solver.prob)


def transform(build, delta, scaled, *, components=("b", "load_shed_fraction")):
    """Substitute the selected cost coordinates; default preserves both maps.

    Component selection supports prospective ablations. Historical four-arm
    construction still uses the original Boolean selection and both components.
    """
    if not components or len(set(components)) != len(components) or not set(components) <= {"b", "load_shed_fraction"}:
        raise ValueError("select cycling and/or shedding coordinates without duplicates")
    if not scaled:
        return Coordinates(build, build, {}, {})
    if build.temporal_assembly != "vectorized" or build.formulation != "ac":
        raise ValueError("diagnostic requires vectorized AC")
    costs = {k for k in build.expressions if k.endswith("_cost")}
    if costs != {"generator_cost", "storage_cost", "load_shedding_cost"}:
        raise ValueError("diagnostic supports only its three frozen cost components")
    weights = np.asarray(build.data["storage_aging_weight"])[:, None]
    indices = np.asarray(build.data["sheddable_load_indices"], int)
    demand = np.asarray(build.data["load_p_source_mw"]).T[indices]
    voll = np.asarray(build.data["load_shedding_cost_per_mwh"])[indices, None]
    candidates = {"b": np.broadcast_to(delta * weights, build.variables["b"].shape).copy(),
                  "load_shed_fraction": delta * voll * demand}
    scales = {name: candidates[name] for name in components}
    leaves, substitutions = {}, {}
    for name, scale in scales.items():
        original = build.variables[name]
        lower, upper = original.get_bounds()
        if (scale.shape != original.shape or not np.isfinite(scale).all() or
                np.any(scale <= 0) or np.isfinite(lower).any() or np.isfinite(upper).any()):
            raise ValueError("positive finite invertible scales and unconstrained leaf attributes required")
        leaf = cp.Variable(original.shape, name=("cycling_cost_coordinate" if name == "b"
                                                else "shedding_cost_coordinate"))
        leaf.save_value(original.value * scale)
        leaves[name] = leaf
        substitutions[id(original)] = cp.multiply(1 / scale, leaf)
    objective = (build.expressions["generator_cost"]
                 + (cp.sum(cp.abs(leaves["b"])) if "b" in leaves else build.expressions["storage_cost"])
                 + (cp.sum(leaves["load_shed_fraction"]) if "load_shed_fraction" in leaves
                    else build.expressions["load_shedding_cost"]))
    constraints = [c.tree_copy(substitutions) for c in build.prob.constraints]
    solver = replace(build, prob=cp.Problem(cp.Minimize(objective), constraints),
                     variables=build.variables | leaves)
    if {v.id for v in solver.prob.variables()} != (
            {v.id for v in build.prob.variables()} - {build.variables[n].id for n in scales}
            | {v.id for v in leaves.values()}):
        raise ValueError("coordinate substitution lost or duplicated a physical leaf")
    return Coordinates(build, solver, scales, leaves)


def construct(historical_call, scaled):
    call = f.calls()[historical_call - 1]
    kwargs = f.kwargs_for_call(call)
    build = f.build_for_call(call, kwargs)
    start = q.physical_start(build, kwargs)
    view = transform(build, kwargs["delta"], scaled)
    return call, kwargs, view, start


def equivalence(view, values):
    """Non-solving paired expression checks at exactly the same physical point."""
    view.assign(values)
    original, transformed = view.physical.prob, view.solver.prob
    a, b = float(original.objective.value), float(transformed.objective.value)
    differences = [float(np.max(abs(np.asarray(c.expr.value) - np.asarray(d.expr.value)), initial=0))
                   for c, d in zip(original.constraints, transformed.constraints, strict=True)]
    maximum = max(differences, default=0.)
    passed = abs(a-b) <= 1e-8 + 1e-12*abs(a) and maximum <= 1e-8
    return dict(passed=bool(passed), objective_difference=abs(a-b),
                constraint_expression_difference=maximum)


def restore_archive(view, record, captured):
    """Assign the full retained native primal using verified canonical offsets."""
    x = expand_primal(record["native"], record["preparation_evidence"])
    originals = {v.name(): v for v in view.solver.prob.variables()}
    used = set()
    for item in captured["layout"]:
        if item["is_original_variable"]:
            variable = originals[item["name"]]
            if list(variable.shape) != list(item["shape"]):
                raise ValueError("archived canonical leaf shape mismatch")
            variable.save_value(x[item["start"]:item["stop"]].reshape(variable.shape, order="F"))
            used.add(variable.name())
    if used != set(originals):
        raise ValueError("incomplete archived original primal")
    return view.restore(), x


def accounting(view, native, evidence, captured, common):
    """Independently reconstruct native cost and cycling epigraph excess."""
    x = expand_primal(native, evidence)
    shape = view.physical.variables["b"].shape
    auxiliaries = [e for e in captured["layout"] if not e["is_original_variable"]
                   and tuple(e["shape"]) == shape]
    if len(auxiliaries) != 1:
        raise ValueError("expected exactly one automatic cycling-cost auxiliary")
    item = auxiliaries[0]
    t = x[item["start"]:item["stop"]].reshape(shape, order="F")
    weights = (np.ones(shape) if "b" in view.scales else
               np.broadcast_to(view.physical.data["storage_delta"] *
                               np.asarray(view.physical.data["storage_aging_weight"])[:, None], shape))
    actual = abs(view.leaves["b"].value if "b" in view.scales else view.physical.variables["b"].value)
    excess = float(np.sum(weights * (t-actual)))
    physical = float(sum(common["costs"].values()))
    canonical = float(native["obj_val"])
    limit = 1e-4 + 1e-6*abs(physical)
    return serializable(dict(canonical_objective=canonical, physical_objective=physical,
        difference=abs(canonical-physical), limit=limit, passed=abs(canonical-physical) <= limit,
        cycling_epigraph_excess=excess, maximum_cost_slack=float(np.max(weights*(t-actual))),
        epigraph_reconstruction_error=abs(canonical-physical-excess)))
