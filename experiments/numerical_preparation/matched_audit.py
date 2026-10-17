"""Symmetric physical, publication, start and canonical economic checks."""
import cvxpy as cp
import numpy as np

from cvxopf import build_opf_multistep, extract_results
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.ac_production_verification.audit import publication_check
from experiments.case118_tracy_2021 import e3_prepared
from experiments.numerical_preparation.audit import serializable
from . import matched_model as model


def restore_ac(build, record):
    start, native, evidence = record["start"], record["native"], record["preparation"]
    assigned = start["assigned_start"]
    graph = model.ac_graph(build, assigned)
    if model.ac_start(build, assigned, graph=graph) != start:
        raise ValueError("fresh physical/canonical start differs")
    _, solver_build, bindings, reduction, data, inverse, mapping = graph
    if build.numerical_preparation.enabled:
        if evidence is None or evidence["native"] != native:
            raise ValueError("prepared native evidence differs")
        for key, wanted in (("assigned_x0", start["complete_x0"]),
                            ("adjusted_x0", start["adjusted_x0"]),
                            ("reduced_x0", start["reduced_x0"]),
                            ("coordinates", start["coordinates"])):
            if evidence[key] != wanted:
                raise ValueError(f"actual prepared handoff differs: {key}")
        if ([[s, a, b] for _, s, a, b in evidence["start_layout"]] != start["layout"] or
                evidence["variable_scale"] != np.ones(mapping.free.size).tolist() or
                evidence["row_scale"] != np.ones(mapping.kept.size).tolist() or
                evidence["checks"]["objective_assembly"] != build.numerical_preparation.objective_assembly):
            raise ValueError("prepared layout/scales/assembly differ")
        maps = evidence["checks"].get("cost_coordinate_maps", [])
        expected = [dict(kind=b.term.kind, shape=list(b.leaf.shape), scale=b.scale.tolist(),
                         physical_start=b.physical_start.tolist()) for b in bindings]
        actual = [dict(kind=m["kind"], shape=start["cost_maps"][i]["shape"],
                       scale=m["scale"], physical_start=m["physical_start"])
                  for i, m in enumerate(maps)]
        if actual != expected:
            raise ValueError("prepared cost maps differ")
    else:
        if evidence is not None or record["handoff"] != dict(
                complete_x0=start["complete_x0"], layout=start["layout"]):
            raise ValueError("baseline actual handoff differs")
    full = mapping.expand(np.asarray(native["x"], float))
    if not np.isfinite(full).all():
        raise ValueError("nonfinite native AC primal")
    reconstructed, _ = canonical_objective(data, inverse, full)
    error = abs(reconstructed-native["obj_val"])
    if error > 1e-7+1e-12*abs(native["obj_val"]) or not np.isfinite(error):
        raise ValueError("native AC objective reconstruction failed")
    for variable in solver_build.prob.variables():
        offset = inverse[-1].var_offsets[variable.id]
        variable.save_value(full[offset:offset+variable.size].reshape(variable.shape, order="F"))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value/binding.scale)
    slack = []
    for owner, auxiliary in reduction.cycling_auxiliaries:
        offset = inverse[-1].var_offsets[auxiliary.id]
        values = full[offset:offset+auxiliary.size].reshape(auxiliary.shape, order="F")
        if bindings:
            delta_cost = values-abs(owner.absolute.args[0].value)
        else:
            # Baseline auxiliary is MW, prepared auxiliary is cost. Convert
            # each baseline slack before signed and non-cancelling aggregation.
            term, argument = owner
            weights = np.broadcast_to(np.asarray(term.weights.value), term.variable.shape)
            active = weights[:, 0] > 0
            if argument.shape != weights[active, :].shape:
                raise ValueError("unsupported baseline cycling selection")
            delta_cost = float(build._cost_coordinate_delta)*weights[active, :]*(values-abs(argument.value))
        slack.extend(delta_cost.ravel())
    build.prob._status = cp.OPTIMAL
    build.prob._value = float(build.prob.objective.value)
    return serializable(extract_results(build)), np.asarray(slack), error


def assess(kwargs, record):
    """Replay a candidate without optimizer calls; gate both treatments alike."""
    native = record.get("native")
    wanted = 0 if kwargs["formulation"] == "ac" else "Solved"
    if native is None or native.get("status") != wanted:
        return dict(passed=False, reason="native solver did not fully converge")
    if record.get("exception") is not None:
        return dict(passed=False, reason=record["exception"])
    with model.no_optimization():
        build = build_opf_multistep(**kwargs)
        try:
            if kwargs["formulation"] == "ac":
                restored, slack, error = restore_ac(build, record)
                canonical = native["obj_val"]
            else:
                restored, offset = model.restored_result(build, native, record["preparation"])
                canonical = native["obj_val"]+offset+native["cvxpy_objective_offset"]
                slack, error = None, None
            publication = publication_check(restored, record["result"])
            named = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
            if named != record["named_costs"]:
                raise ValueError("restored named costs differ")
            physical, relaxation = e3_prepared.audit(build, restored, kwargs, named)
            economic = e3_prepared.economics(canonical, physical["costs"], slack,
                                             formulation=kwargs["formulation"])
            return serializable(dict(passed=physical["passed"] and economic["passed"],
                publication=publication, physical=physical, relaxation=relaxation,
                economics=economic, canonical_reconstruction=error))
        except (AssertionError, ValueError, TypeError, KeyError, AttributeError, IndexError) as exc:
            return dict(passed=False, reason=f"independent replay failed: {exc}")
