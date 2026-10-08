"""AC cost-coordinate algebra, restoration and warning regression coverage.

Small regression solves are not new Tracy qualification or default adoption.
"""
from dataclasses import replace
import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import (NumericalPreparation, OPFOptions, OPFBuild, StorageUnitIdeal,
                    CostAccuracyWarning, build_opf, build_opf_multistep,
                    extract_results, gen_from_matpower)
from cvxopf._cost_coordinates import CostCoordinateTerm, transform_cost_coordinates
from cvxopf.testcases import case9
from cvxopf.testcases.case9_pwl import case9_pwl
from cvxopf.load import loads_from_matpower


POLICY = NumericalPreparation(normalize_device_limits=True, exact_fixed_boxes=True,
                              cost_coordinates=True)


def test_policy_is_opt_in_ac_only():
    assert not NumericalPreparation().cost_coordinates
    assert NumericalPreparation(cost_coordinates=True).enabled
    with pytest.raises(TypeError):
        NumericalPreparation(cost_coordinates=1)
    for formulation in ("socp", "lossy_dc", "singlenode_dc"):
        with pytest.raises(ValueError, match="AC only"):
            build_opf(case9(), formulation=formulation,
                      options=OPFOptions(numerical_preparation=NumericalPreparation(cost_coordinates=True)))


@pytest.mark.parametrize("weights", [[.01, .03], [0., .03], [0., 0.]])
@pytest.mark.parametrize("axis", [None, 0])
def test_exact_substitution_retains_unrelated_objective_and_parameters(weights, axis):
    shape = (2,) if axis is None else (2, 3)
    b, shedding, other = cp.Variable(shape), cp.Variable(shape), cp.Variable()
    w = cp.Constant(np.array(weights) if axis is None else np.array(weights)[:, None])
    load_w = cp.Parameter(shape, nonneg=True, value=np.full(shape, 5000.))
    cycling_rate = cp.sum(cp.multiply(w, cp.abs(b)), axis=axis)
    shedding_rate = cp.sum(cp.multiply(load_w, shedding), axis=axis)
    delta = .25
    objective = delta * cp.sum(cycling_rate + shedding_rate) + cp.square(other - 7.) + 3.
    constraint = b + shedding <= 100.
    build = OPFBuild(cp.Problem(cp.Minimize(objective), [constraint]), {}, {}, "ac", False,
                     _cost_coordinate_terms=(CostCoordinateTerm(b, w, cycling_rate, "cycling", axis),
                                             CostCoordinateTerm(shedding, load_w, shedding_rate, "shedding", axis)),
                     _cost_coordinate_delta=delta)
    b.save_value(np.arange(b.size).reshape(shape) - 2.)
    shedding.save_value(np.full(shape, .2))
    other.save_value(8.)
    for factor in (1., 0., 2.):
        load_w.value = np.full(shape, 5000. * factor)
        expected_cost = objective.value
        expected_constraint = constraint.expr.value.copy()
        solver, bindings = transform_cost_coordinates(build)
        assert solver.prob.objective.is_dcp()
        assert solver.prob.constraints[0].is_dcp()
        assert solver.prob.objective.value == pytest.approx(expected_cost)
        np.testing.assert_allclose(solver.prob.constraints[0].expr.value, expected_constraint)
        assert load_w in solver.prob.parameters()
        assert build.prob.constraints[0] is constraint
        for binding in bindings:
            np.testing.assert_allclose(binding.leaf.value / binding.scale, binding.term.variable.value)
        assert solver.prob.constraints[0].id == constraint.id


def make_build(assembly, components="both", zero=False, pwl=False):
    case = case9_pwl() if pwl else case9()
    kwargs = dict(options=OPFOptions(numerical_preparation=POLICY), delta=.5)
    if components in ("cycling", "both"):
        kwargs["storage"] = [StorageUnitIdeal(5, 10., 40., 20., aging_weight=0. if zero else .01,
                              terminal_soc=20., terminal_cost="quadratic", terminal_weight=.1)]
    if components in ("shedding", "both"):
        kwargs["loads"] = [replace(load, shedding_cost_per_mwh=5000.)
                           for load in loads_from_matpower(case["bus"])]
        generators = gen_from_matpower(case["gen"], case["gencost"])
        kwargs["generators"] = [replace(gen, p_max_mw=70.) for gen in generators]
    if assembly == "single":
        return build_opf(case, **kwargs)
    if "loads" not in kwargs:
        kwargs.update(df_P=pd.DataFrame(np.tile(case["bus"][:, 2], (2, 1))),
                      df_Q=pd.DataFrame(np.tile(case["bus"][:, 3], (2, 1))))
    return build_opf_multistep(case, T=2, temporal_assembly=assembly, **kwargs)


@pytest.mark.parametrize("assembly", ["single", "stepwise", "vectorized"])
@pytest.mark.parametrize("components", ["cycling", "shedding", "both"])
def test_solve_restores_physical_units_and_cost_accounting(assembly, components):
    build = make_build(assembly, components)
    ids = tuple(id(v) for v in build.prob.variables())
    parameters = tuple(id(p) for p in build.prob.parameters())
    constraints = tuple(id(c) for c in build.prob.constraints)
    build.solve(max_iter=100)
    result = extract_results(build)
    assert result["status"] == "optimal"
    assert tuple(id(v) for v in build.prob.variables()) == ids
    assert tuple(id(p) for p in build.prob.parameters()) == parameters
    assert tuple(id(c) for c in build.prob.constraints) == constraints
    assert set(build.prob.solution.primal_vars) == {v.id for v in build.prob.variables()}
    accounting = build.preparation_evidence.checks["cost_accounting"]
    assert accounting["physical_objective"] == pytest.approx(build.prob.objective.value)
    assert accounting["native_objective"] == build.preparation_evidence.native["obj_val"]
    assert accounting["physical_objective"] == pytest.approx(build.prob.value)
    assert not accounting["accounting_warning"]
    if components != "cycling":
        assert float(result["energy_not_served"]) > 40.
    if components != "shedding":
        assert np.max(np.abs(result["b"])) <= 10. + 1e-5
        assert np.allclose(np.diff(result["soc"], axis=0).reshape(-1),
                           -.5 * np.asarray(result["b"])[1:].reshape(-1)) if assembly != "single" else True
    assert max(float(np.max(constraint.violation(), initial=0.))
               for constraint in build.prob.constraints) < 2e-5
    with pytest.raises(TypeError):
        accounting["cycling_warning"] = True


@pytest.mark.parametrize("assembly", ["single", "stepwise", "vectorized"])
def test_zero_aging_weight_has_no_cycling_auxiliary(assembly):
    build = make_build(assembly, "cycling", zero=True)
    build.solve(max_iter=100)
    assert build.prob.status == "optimal"
    assert build.preparation_evidence.checks["cost_accounting"]["cycling_slack_l1"] == 0.


@pytest.mark.parametrize("assembly", ["single", "stepwise", "vectorized"])
def test_mixed_zero_aging_weights_restore_all_coordinates(assembly):
    case = case9()
    storage = [StorageUnitIdeal(5, 10., 40., 20., aging_weight=.01),
               StorageUnitIdeal(6, 10., 40., 20., aging_weight=0.)]
    kwargs = dict(storage=storage, options=OPFOptions(numerical_preparation=POLICY), delta=.5)
    if assembly == "single":
        build = build_opf(case, **kwargs)
    else:
        build = build_opf_multistep(case, T=2, temporal_assembly=assembly,
                 df_P=pd.DataFrame(np.tile(case["bus"][:, 2], (2, 1))),
                 df_Q=pd.DataFrame(np.tile(case["bus"][:, 3], (2, 1))), **kwargs)
    build.solve(max_iter=100)
    assert build.prob.status == "optimal"
    result = extract_results(build)
    expected = .5 * .01 * np.sum(np.abs(np.asarray(result["b"])[..., 0]))
    assert result["storage_cost"] == pytest.approx(expected)
    assert not build.preparation_evidence.checks["cost_accounting"]["accounting_warning"]
    for mapping in build.preparation_evidence.checks["cost_coordinate_maps"]:
        np.testing.assert_array_equal(mapping["scale"][1], 1.)


def test_pwl_auxiliary_not_misclassified_as_cycling():
    build = make_build("single", "cycling", pwl=True)
    build.solve(max_iter=100)
    assert build.prob.status == "optimal"
    assert not build.preparation_evidence.checks["cost_accounting"]["accounting_warning"]


def test_cost_only_policy_composes_with_exact_fixed_boxes():
    from tests.test_preparation_bridges import build as fixed_build
    for policy in (NumericalPreparation(cost_coordinates=True), POLICY):
        build = fixed_build("ac", "vectorized", policy, with_storage=True)
        build.variables["b"].save_value(np.full((1, 2), .1))
        build.solve(max_iter=100)
        assert build.prob.status == "optimal"
        assert bool(build.preparation_evidence.coordinates.fixed.size) == policy.exact_fixed_boxes
        maps = build.preparation_evidence.checks["cost_coordinate_maps"]
        for mapping in maps:
            layout = {item[0]: (item[2], item[3]) for item in build.preparation_evidence.start_layout}
            start, stop = layout[mapping["solver_variable_id"]]
            np.testing.assert_allclose(build.preparation_evidence.assigned_x0[start:stop],
                (mapping["physical_start"] * mapping["scale"]).ravel(order="F"))
        np.testing.assert_allclose(extract_results(build)["Pg"][:, 0], 50.)


def test_repeated_solve_resnapshots_current_load_weights():
    build = make_build("single")
    build.solve(max_iter=100)
    term = next(term for term in build._cost_coordinate_terms if term.kind == "shedding")
    parameter = term.weights.parameters()[0]
    before = next(item["scale"].copy() for item in build.preparation_evidence.checks["cost_coordinate_maps"]
                  if item["kind"] == "shedding")
    # The served-load and shedding-cost expressions share this eligibility
    # Parameter. Both graph semantics and the fresh cost map follow its value.
    parameter.value = .9 * parameter.value
    build.solve(max_iter=100)
    assert build.prob.status == "optimal"
    after = next(item["scale"] for item in build.preparation_evidence.checks["cost_coordinate_maps"]
                 if item["kind"] == "shedding")
    np.testing.assert_allclose(after, np.where(before == 1., 1., .9 * before))
    assert not build.preparation_evidence.checks["cost_accounting"]["accounting_warning"]


def test_native_failure_retains_maps_but_no_public_primals():
    build = make_build("single")
    build.solve(max_iter=0)
    assert build.prob.status == "user_limit"
    assert all(variable.value is None for variable in build.prob.variables())
    assert not build.preparation_evidence.checks["restoration_available"]
    assert build.preparation_evidence.checks["cost_coordinate_maps"]


def test_failed_resolve_clears_original_physical_variables(monkeypatch):
    build = make_build("single")
    build.solve(max_iter=100)
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    def fail(*args, **kwargs):
        raise RuntimeError("test numerical failure")
    monkeypatch.setattr(IPOPT, "solve_via_data", fail)
    with pytest.raises(cp.error.SolverError):
        build.solve()
    assert all(variable.value is None for variable in build.prob.variables())
    assert build.prob.value is None


@pytest.mark.parametrize("warning_as_error", [False, True])
@pytest.mark.parametrize("cancelling_slack", [False, True])
@pytest.mark.parametrize("objective_assembly", ["hourly", "component_first"])
def test_large_cycling_slack_warns_without_discarding_solution(monkeypatch, warning_as_error, cancelling_slack, objective_assembly):
    build = make_build("vectorized" if cancelling_slack or objective_assembly == "component_first" else "single", "cycling")
    build = replace(build, _numerical_preparation=replace(build.numerical_preparation,
                                                       objective_assembly=objective_assembly))
    from cvxopf import _cost_coordinates as coordinates
    original_factory = coordinates.cost_canonicalization
    captured = []
    def record_reduction(bindings):
        reduction = original_factory(bindings)
        captured.append(reduction)
        return reduction
    monkeypatch.setattr(coordinates, "cost_canonicalization", record_reduction)
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    original = IPOPT.solve_via_data
    def inject_slack(self, data, *args, **kwargs):
        raw = original(self, data, *args, **kwargs)
        # The identity-bound cycling auxiliary excludes network and terminal
        # auxiliaries, even when their shapes happen to be identical.
        assert len(captured[0].cycling_auxiliaries) == 1
        auxiliary = captured[0].cycling_auxiliaries[0][1]
        mapping = kwargs["solver_cache"]["oracles"].mapping
        # Find the auxiliary from the canonical leaf order, then reduced index.
        variables = data["problem"].variables()
        position = next(i for i, variable in enumerate(variables) if variable is auxiliary)
        offset = sum(v.size for v in variables[:position])
        index = np.flatnonzero(mapping.free == offset)[0]
        raw["x"][index] += .1
        if cancelling_slack:
            assert auxiliary.size == 2
            second = np.flatnonzero(mapping.free == offset + 1)[0]
            raw["x"][second] -= .1
        else:
            raw["obj_val"] += .1
        return raw
    monkeypatch.setattr(IPOPT, "solve_via_data", inject_slack)
    if warning_as_error:
        with warnings.catch_warnings():
            warnings.simplefilter("error", CostAccuracyWarning)
            with pytest.raises(CostAccuracyWarning):
                build.solve(max_iter=100)
    else:
        with pytest.warns(CostAccuracyWarning):
            build.solve(max_iter=100)
    assert build.prob.status == "optimal"
    record = build.preparation_evidence.checks["cost_accounting"]
    assert record["cycling_warning"]
    assert not record["accounting_warning"]
    if cancelling_slack:
        assert abs(record["cycling_epigraph_excess"]) < 1e-4
        assert record["cycling_slack_l1"] > .19
    assert extract_results(build)["b"] is not None
