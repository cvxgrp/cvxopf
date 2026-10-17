"""Experimental exact cost coordinates and stock conic boundary observation.

No production imports this module. The sole temporary adapter wrapper observes
the stock call unchanged; it does not tune, replace, or retry the optimizer.
"""

from dataclasses import dataclass, replace
from types import SimpleNamespace
from unittest.mock import patch

import cvxpy as cp
import numpy as np
from scipy import sparse
from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL

from cvxopf import extract_results
from cvxopf._convex_preparation import joint_scales, scaled_data, substitute, restore, cone_layout
from cvxopf._numerical_preparation import FixedCoordinateMap, resolve_fixed_map
from experiments.ac_cost_coordinates.model import Coordinates
from experiments.numerical_preparation import fixture as old
from experiments.numerical_preparation.audit import evidence_record, serializable
from tests.socp_matched import digest as input_digest


def transform(build, delta, scaled):
    if build.formulation not in {"socp", "lossy_dc", "singlenode_dc"} or build.temporal_assembly != "vectorized":
        raise ValueError("vectorized convex build required")
    if not scaled:
        return Coordinates(build, build, {}, {})
    expected = {"generator_cost", "storage_cost", "load_shedding_cost"}
    if build.formulation == "lossy_dc":
        expected.add("dc_loss_cost")
    if {k for k in build.expressions if k.endswith("_cost")} != expected:
        raise ValueError("unsupported cost decomposition")
    indices = np.asarray(build.data["sheddable_load_indices"], int)
    scales = {"b": np.broadcast_to(delta*np.asarray(build.data["storage_aging_weight"])[:, None],
                                    build.variables["b"].shape).copy(),
              "load_shed_fraction": delta*np.asarray(build.data["load_shedding_cost_per_mwh"])[indices, None]
                  * np.asarray(build.data["load_p_source_mw"]).T[indices]}
    leaves, substitutions = {}, {}
    for name, scale in scales.items():
        original = build.variables[name]
        lower, upper = original.get_bounds()
        if scale.shape != original.shape or not np.isfinite(scale).all() or np.any(scale <= 0):
            raise ValueError("positive finite invertible scale required")
        # DC storage uses native leaf bounds; preserving explicit constraints
        # alone would silently enlarge its feasible set. Positive diagonal maps
        # preserve boxes exactly, including their infinite sides.
        if any(value for key, value in original.attributes.items()
               if key not in {"bounds", "sparsity"}):
            raise ValueError("unsupported leaf attribute")
        if original.attributes["sparsity"]:
            raise ValueError("unsupported sparse leaf")
        bounds = [np.asarray(lower)*scale, np.asarray(upper)*scale]
        if not np.isfinite(bounds[0]).any() and not np.isfinite(bounds[1]).any():
            bounds = None
        leaf = cp.Variable(original.shape, name=name+"_cost_coordinate", bounds=bounds)
        leaves[name] = leaf
        substitutions[id(original)] = cp.multiply(1/scale, leaf)
    objective = (sum(build.expressions[k] for k in sorted(expected-{"storage_cost", "load_shedding_cost"}))
                 + cp.sum(cp.abs(leaves["b"])) + cp.sum(leaves["load_shed_fraction"]))
    solver = replace(build, prob=cp.Problem(cp.Minimize(objective),
        [c.tree_copy(substitutions) for c in build.prob.constraints]), variables=build.variables | leaves)
    if {v.id for v in solver.prob.variables()} != (
            {v.id for v in build.prob.variables()}-{build.variables[n].id for n in scales}
            | {v.id for v in leaves.values()}):
        raise ValueError("lost or duplicated physical leaf")
    return Coordinates(build, solver, scales, leaves)


def equivalence(view, point):
    """Compare every constraint argument, including both SOC arguments."""
    view.assign(point)
    left, right = view.physical.prob, view.solver.prob
    difference = abs(float(left.objective.value)-float(right.objective.value))
    errors = []
    for a, b in zip(left.constraints, right.constraints, strict=True):
        if type(a) is not type(b):
            raise ValueError("constraint class changed")
        for x, y in zip(a.args, b.args, strict=True):
            errors.append(float(np.max(abs(np.asarray(x.value)-np.asarray(y.value)), initial=0)))
    maximum = max(errors, default=0.)
    bounds_ok = True
    for name, scale in view.scales.items():
        physical_bounds = view.physical.variables[name].get_bounds()
        solver_bounds = view.leaves[name].get_bounds()
        bounds_ok &= all(np.array_equal(np.asarray(x)*scale, y)
                         for x, y in zip(physical_bounds, solver_bounds, strict=True))
    return dict(passed=bool(bounds_ok and difference <= 1e-8+1e-12*abs(float(left.objective.value)) and maximum <= 1e-8),
                objective_difference=difference, constraint_expression_difference=maximum,
                leaf_bounds_equivalent=bool(bounds_ok))


def fingerprint(data):
    def matrix(value):
        value = sparse.csc_array(value).copy()
        value.sum_duplicates()
        value.sort_indices()
        value.eliminate_zeros()
        return dict(shape=value.shape, data=value.data, indices=value.indices, indptr=value.indptr)
    n = len(data["c"])
    return input_digest(serializable(dict(P=matrix(data.get("P", sparse.csc_array((n, n)))),
        A=matrix(data["A"]), c=data["c"], b=data["b"], cones=cone_layout(data["dims"], len(data["b"])))))


@dataclass
class Canonical:
    data: dict
    chain: object
    inverse: list
    mapping: FixedCoordinateMap
    D: np.ndarray
    R: np.ndarray
    offset: float
    delivered: dict
    layout: list
    signature: dict


def canonical(build):
    chain = build.prob._construct_chain(solver=cp.CLARABEL,
        canon_backend=build.canonicalization_backend, ignore_dpp=True,
        solver_opts=old.solver_options(build.formulation))
    if type(chain.solver) is not CLARABEL:
        raise ValueError("stock CLARABEL adapter required")
    data, inverse = chain.apply(build.prob)
    data = dict(data, P=data.get("P", sparse.csc_array((len(data["c"]),)*2)))
    stuffing = inverse[-2]
    original_ids = {}
    for v in build.prob.variables():
        identity = v.id
        for item in inverse[:-1]:
            if isinstance(item, tuple) and len(item) == 3 and identity in item[0]:
                identity = item[0][identity].id
        original_ids[identity] = v.name()
    layout = [dict(name=original_ids.get(identity), shape=list(stuffing.var_shapes[identity]),
                   start=offset, stop=offset+int(np.prod(stuffing.var_shapes[identity])),
                   is_original_variable=identity in original_ids)
              for identity, offset in sorted(stuffing.var_offsets.items(), key=lambda item: item[1])]
    if build.numerical_preparation.enabled:
        constraints = inverse[-1]["eq_constr"]+inverse[-1]["other_constr"]
        mapping = resolve_fixed_map(build._exact_boxes, inverse[:-1], stuffing, constraints,
                                    len(data["c"]), len(data["b"]))
        reduced, offset = substitute(data, mapping)
        D, R = joint_scales(reduced)
    else:
        mapping = FixedCoordinateMap(len(data["c"]), len(data["b"]),
                                     np.array([], int), np.array([], float), np.array([], int))
        reduced, offset = data, 0.
        D, R = np.ones(len(data["c"])), np.ones(len(data["b"]))
    delivered = scaled_data(reduced, D, R)
    signature = serializable(dict(original_sha256=fingerprint(data), delivered_sha256=fingerprint(delivered),
        objective_constant=float(inverse[-1]["offset"]), substitution_offset=offset,
        layout=layout, full_size=len(data["c"]), row_count=len(data["b"]),
        fixed=mapping.fixed, fixed_values=mapping.values, dropped=mapping.dropped,
        variable_scale=D, row_scale=R))
    return Canonical(data, chain, inverse, mapping, D, R, offset, delivered, layout, signature)


def restored(canonical_problem, native):
    raw = SimpleNamespace(**{k: np.asarray(native[k], float) for k in ("x", "s", "z")},
        status=native["status"], obj_val=native["obj_val"], obj_val_dual=native["obj_val_dual"],
        iterations=native["iterations"], solve_time=native["solve_time"])
    return restore(canonical_problem.data, canonical_problem.mapping, raw,
                   canonical_problem.D, canonical_problem.R, canonical_problem.offset)


def restore_view(view, problem, native):
    full, diagnostics = restored(problem, native)
    # Use the unchanged CVXPY inverse chain to restore all original leaf values.
    view.solver.prob.unpack_results(full, problem.chain, problem.inverse)
    physical = view.restore()
    physical.prob._value = float(view.physical.prob.objective.value)
    result = extract_results(physical)
    named = {k: float(v.value) for k, v in view.physical.expressions.items() if k.endswith("_cost")}
    return full, result, named, diagnostics


def solve_observed(view, problem, options, observer):
    """One unchanged build.solve call; archive the actual adapter input/output."""
    method, count = CLARABEL.solve_via_data, 0

    def observe(adapter, data, warm_start, verbose, solver_opts, solver_cache=None):
        nonlocal count
        count += 1
        if count != 1 or warm_start or fingerprint(data) != problem.signature["delivered_sha256"]:
            raise ValueError("stock adapter call differs from bound canonical data")
        raw = method(adapter, data, warm_start, verbose, solver_opts, solver_cache)
        native = {k: getattr(raw, k) for k in ("x", "s", "z", "obj_val", "obj_val_dual",
            "r_prim", "r_dual", "iterations", "solve_time")}
        native["status"] = str(raw.status)
        observer(serializable(native))
        return raw

    with patch.object(CLARABEL, "solve_via_data", observe):
        view.solver.solve(warm_start=False, verbose=True, **options)
    if count != 1:
        raise ValueError("expected exactly one stock adapter invocation")
    return evidence_record(view.solver.preparation_evidence)
