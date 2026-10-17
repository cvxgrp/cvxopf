"""Non-solving restoration and original E2 checks for prepared public solves."""

from copy import deepcopy
from dataclasses import asdict, fields, replace

import cvxpy as cp
import numpy as np
from cvxpy.reductions.cvx_attr2constr import CvxAttr2Constr
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from cvxpy.reductions.solvers.solving_chain import SolvingChain

from cvxopf import NumericalPreparation, extract_results
from cvxopf._cost_coordinates import transform_cost_coordinates, cost_canonicalization
from cvxopf._cvxpy_dispatch import sparse_dispatch_policy
from cvxopf._hierarchical_solver import _complete_start
from cvxopf._numerical_preparation import resolve_fixed_map
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.ac_production_verification.audit import publication_check
from experiments.numerical_preparation.audit import serializable
from experiments.m11_socp.e1_compare import canonical_problem, restored_result
from experiments.m11_socp.e2_compare import summary
from tests.socp_matched import fixture, validate_matching, check_pair, check_containment, digest, json_value


def construct(name):
    spec = fixture(name)
    identity = validate_matching(spec, deepcopy(spec))
    # Validate the complete common physical specification before introducing
    # the explicitly different numerical representations. Do not weaken the
    # historical matching guard or change the original fixture digest.
    policies = dict(ac=NumericalPreparation(normalize_device_limits=True, exact_fixed_boxes=True,
        cost_coordinates=True, objective_assembly="component_first" if name == "mixed_vectorized" else "hourly"),
        socp=NumericalPreparation(normalize_device_limits=True, exact_fixed_boxes=True, canonical_scaling="joint5"))
    builds = []
    from cvxopf import build_opf, build_opf_multistep
    for formulation in ("ac", "socp"):
        kwargs = deepcopy(spec["kwargs"])
        kwargs["options"] = replace(kwargs["options"], numerical_preparation=policies[formulation])
        builder = build_opf_multistep if "T" in kwargs else build_opf
        builds.append(builder(deepcopy(spec["case"]), formulation=formulation, **kwargs))
    return spec, *builds, identity


def ac_canonical(build):
    start = serializable(_complete_start(build))
    solver_build, bindings = transform_cost_coordinates(build)
    reduction = cost_canonicalization(bindings)
    solver = IPOPT()
    with sparse_dispatch_policy(build.automatic_sparse_dispatch):
        data, inverse = SolvingChain(reductions=[
            CvxAttr2Constr(reduce_bounds=not solver.BOUNDED_VARIABLES), reduction, solver,
        ]).apply(solver_build.prob)
    mapping = resolve_fixed_map(solver_build._exact_boxes, inverse[:-1], inverse[-1],
        data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    return start, solver_build, bindings, reduction, data, inverse, mapping


def row_binding(name):
    spec, ac, socp, identity = construct(name)
    start, _, bindings, _, data, inverse, mapping = ac_canonical(ac)
    conic, _, _, cm, D, R, offset = canonical_problem(socp)
    layout = [[list(v.shape), inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size]
              for v in data["problem"].variables()]
    def coordinate(m):
        return serializable({f.name:getattr(m,f.name) for f in fields(m) if not f.name.startswith("_")})
    return dict(name=name, input_sha256=identity, inputs=serializable(json_value(spec)),
        policies={k:asdict(b.numerical_preparation) for k,b in (("ac",ac),("socp",socp))},
        physical_start=start, ac_layout=layout, ac_x0=data["x0"].tolist(), ac_map=coordinate(mapping),
        ac_cost_scales=[dict(kind=b.term.kind, shape=list(b.leaf.shape), scale=b.scale.tolist()) for b in bindings],
        socp_map=coordinate(cm), socp_variable_scale=D.tolist(), socp_row_scale=R.tolist(),
        socp_objective_offset=offset, socp_canonical_sha256=digest(dict(
            A=conic["A"].toarray(), P=conic["P"].toarray(), b=conic["b"], c=conic["c"],
            zero=conic["dims"].zero, nonneg=conic["dims"].nonneg, soc=conic["dims"].soc)))


def restore_ac(build, row, evidence, published):
    start, solver_build, bindings, reduction, data, inverse, mapping = ac_canonical(build)
    native = evidence["native"]
    assert native["status"] == 0
    assert start == row["physical_start"] and data["x0"].tolist() == row["ac_x0"] == evidence["assigned_x0"]
    retained = [[s,a,b] for _,s,a,b in evidence["start_layout"]]
    assert retained == row["ac_layout"]
    assert evidence["coordinates"] == row["ac_map"]
    assert evidence["variable_scale"] == np.ones(mapping.free.size).tolist()
    assert evidence["row_scale"] == np.ones(mapping.kept.size).tolist()
    adjusted = data["x0"].copy()
    adjusted[mapping.fixed] = mapping.values
    assert evidence["adjusted_x0"] == adjusted.tolist()
    assert evidence["reduced_x0"] == adjusted[mapping.free].tolist()
    assert evidence["checks"]["restoration_available"]
    assert evidence["checks"]["objective_assembly"] == build.numerical_preparation.objective_assembly
    maps = evidence["checks"].get("cost_coordinate_maps", [])
    assert len(maps) == len(bindings)
    for old, binding in zip(maps, bindings, strict=True):
        assert old["kind"] == binding.term.kind and old["scale"] == binding.scale.tolist()
        assert old["physical_start"] == binding.physical_start.tolist()
        original_slice = next((s,a,b) for identity,s,a,b in evidence["start_layout"] if identity == old["solver_variable_id"])
        offset = inverse[-1].var_offsets[binding.leaf.id]
        assert original_slice == (list(binding.leaf.shape), offset, offset+binding.leaf.size)
    full = mapping.expand(np.asarray(native["x"], float))
    reconstructed, _ = canonical_objective(data, inverse, full)
    assert abs(reconstructed-native["obj_val"]) <= 1e-7+1e-12*abs(native["obj_val"])
    for variable in solver_build.prob.variables():
        offset = inverse[-1].var_offsets[variable.id]
        variable.save_value(full[offset:offset+variable.size].reshape(variable.shape, order="F"))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value/binding.scale)
    build.prob._status, build.prob._value = cp.OPTIMAL, float(build.prob.objective.value)
    result = serializable(extract_results(build))
    publication_check(result, published)
    slack = []
    for binding, auxiliary in reduction.cycling_auxiliaries:
        offset = inverse[-1].var_offsets[auxiliary.id]
        auxiliary_value = full[offset:offset+auxiliary.size].reshape(auxiliary.shape, order="F")
        slack.extend((auxiliary_value-abs(binding.absolute.args[0].value)).ravel())
    from tests.socp_matched import objective_components
    costs = objective_components(fixture(row["name"]), result)
    excess, l1 = float(np.sum(slack)), float(np.sum(abs(np.asarray(slack))))
    physical = float(sum(costs.values()))
    limit = 1e-4+1e-6*abs(costs.get("storage_cost",0.))
    unexplained = native["obj_val"]-physical-excess
    assert abs(unexplained) <= 1e-4+1e-6*abs(physical)
    return dict(cycling_warning=l1>limit, cycling_epigraph_excess=excess,
                cycling_slack_l1=l1, cycling_limit=limit, unexplained_difference=unexplained,
                canonical_objective=native["obj_val"], physical_objective=physical)


def assess(name, row, records):
    """Fresh native restoration and original independent matched-pair gates."""
    spec, ac, socp, identity = construct(name)
    assert identity == row["input_sha256"] and row == row_binding(name)
    assert records["ac"]["native"] == records["ac"]["preparation_evidence"]["native"]
    economics = restore_ac(ac, row, records["ac"]["preparation_evidence"], records["ac"]["result"])
    containment = check_containment(spec, ac, socp, extract_results(ac))
    if "socp" not in records:
        return serializable(dict(ac_containment=containment, economics=economics, passed=True))
    record = records["socp"]
    assert record["native"] == record["preparation_evidence"]["native"]
    assert record["native"]["status"] == "Solved"
    restored, _ = restored_result(socp, record["native"], record["preparation_evidence"])
    publication_check(restored, record["result"])
    checked = check_pair(spec, ac, socp)
    return serializable(dict(summary=summary(spec, checked), economics=economics, passed=True))
