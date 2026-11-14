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
from cvxopf._cost_coordinates import CostCoordinateLeaf, CostCoordinateTerm, transform_cost_coordinates
from tests.test_preparation_bridges import _lossy_units
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
                     _cost_coordinate_terms=(CostCoordinateTerm(cycling_rate, (
                         CostCoordinateLeaf(b, w, cycling_rate, "cycling"),), axis),
                         CostCoordinateTerm(shedding_rate, (
                         CostCoordinateLeaf(shedding, load_w, shedding_rate, "shedding"),), axis)),
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
            np.testing.assert_allclose(binding.leaf.value / binding.scale, binding.coordinate.variable.value)
        assert solver.prob.constraints[0].id == constraint.id


@pytest.mark.parametrize("assembly,T,component_first", [
    ("single", 1, False), ("stepwise", 1, False), ("stepwise", 3, False),
    ("vectorized", 1, False), ("vectorized", 3, False),
    ("vectorized", 1, True), ("vectorized", 3, True),
])
def test_lossy_complete_rate_algebra_and_leaf_provenance(assembly, T, component_first):
    build = make_build(assembly, lossy=True, T=T)
    if component_first:
        build = replace(build, _numerical_preparation=replace(
            build.numerical_preparation, objective_assembly="component_first"))
    for variable in build.prob.variables():
        # Deliberately arbitrary points test the map, not physical feasibility.
        variable.save_value(.1 + np.arange(variable.size).reshape(variable.shape, order="F")*.07)
    ids = tuple(id(v) for v in build.prob.variables())
    parameters = tuple(id(p) for p in build.prob.parameters())
    constraints = tuple(id(c) for c in build.prob.constraints)
    expected = float(build.prob.objective.value)
    residuals = [c.expr.value.copy() for c in build.prob.constraints]
    solver, bindings = transform_cost_coordinates(build)
    assert solver.prob.objective.value == pytest.approx(expected, rel=1e-12)
    for c, residual in zip(solver.prob.constraints, residuals, strict=True):
        np.testing.assert_allclose(c.expr.value, residual, rtol=1e-12, atol=1e-12)
    linear = [b for b in bindings if b.coordinate.kind == "linear_cycling"]
    assert len(linear) == (2*T if assembly == "stepwise" else 2)
    for binding in linear:
        assert binding.absolute is None
        assert binding.rate.is_affine()
        assert any(binding.coordinate is leaf for leaf in binding.term.leaves)
        np.testing.assert_allclose(binding.leaf.value,
            build._cost_coordinate_delta*binding.coordinate.weights.value*binding.physical_start)
        np.testing.assert_allclose(binding.leaf.value/binding.scale, binding.coordinate.variable.value)
    assert tuple(id(v) for v in build.prob.variables()) == ids
    assert tuple(id(p) for p in build.prob.parameters()) == parameters
    assert tuple(id(c) for c in build.prob.constraints) == constraints
    from cvxopf._cost_coordinates import cost_canonicalization
    from cvxopf._cvxpy_dispatch import sparse_dispatch_policy
    reduction = cost_canonicalization(bindings)
    with sparse_dispatch_policy(False):
        reduction.apply(solver.prob)
    assert all(binding.coordinate.kind == "cycling" for binding, _ in reduction.cycling_auxiliaries)
    assert len(reduction.cycling_auxiliaries) == sum(b.absolute is not None for b in bindings)


def test_complete_rate_contract_rejects_partial_and_overlapping_ownership():
    charge, discharge = cp.Variable(2), cp.Variable(2)
    weights = cp.Constant([.01, .03])
    rates = [cp.sum(cp.multiply(weights, p)) for p in (charge, discharge)]
    leaves = tuple(CostCoordinateLeaf(p, weights, rate, "linear_cycling")
                   for p, rate in zip((charge, discharge), rates, strict=True))
    rate = rates[0] + rates[1]
    term = CostCoordinateTerm(rate, leaves)
    for partial in (leaves[:1], leaves[::-1]):
        with pytest.raises(ValueError, match="complete declared"):
            CostCoordinateTerm(rate, partial)
    with pytest.raises(ValueError, match="unique variable"):
        CostCoordinateTerm(rate, (leaves[0], leaves[0]))
    with pytest.raises(ValueError, match="complete declared"):
        CostCoordinateTerm(rate+7., leaves)
    from cvxopf._cost_coordinates import ObjectiveCostContribution
    with pytest.raises(ValueError, match="complete declared"):
        ObjectiveCostContribution("lossy", rate, rate, None, (
            CostCoordinateTerm(rates[0], leaves[:1]), CostCoordinateTerm(rates[1], leaves[1:])
        ))
    charge.value, discharge.value = np.ones(2), np.ones(2)
    build = OPFBuild(cp.Problem(cp.Minimize(rate), []), {}, {}, "ac", False,
                     _cost_coordinate_terms=(term, CostCoordinateTerm(rates[0], leaves[:1])))
    with pytest.raises(ValueError, match="overlapping"):
        transform_cost_coordinates(build)


def make_build(assembly, components="both", zero=False, pwl=False, lossy=False, T=2, lossy_price=None):
    case = case9_pwl() if pwl else case9()
    kwargs = dict(options=OPFOptions(numerical_preparation=POLICY), delta=.5)
    if lossy:
        kwargs["lossy_storage"] = [replace(unit, aging_weight=(.01+.02*k if lossy_price is None else lossy_price),
            terminal_soc=unit.initial_soc, terminal_cost="quadratic", terminal_weight=.1)
            for k, unit in enumerate(_lossy_units())]
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
        kwargs.update(df_P=pd.DataFrame(np.tile(case["bus"][:, 2], (T, 1))),
                      df_Q=pd.DataFrame(np.tile(case["bus"][:, 3], (T, 1))))
    return build_opf_multistep(case, T=T, temporal_assembly=assembly, **kwargs)


@pytest.mark.parametrize("assembly", ["single", "stepwise", "vectorized"])
@pytest.mark.parametrize("components", ["cycling", "shedding", "both"])
@pytest.mark.parametrize("lossy", [False, True])
def test_solve_restores_physical_units_and_cost_accounting(assembly, components, lossy):
    build = make_build(assembly, components, lossy=lossy)
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
    if lossy:
        assert accounting["linear_throughput_physical_cost"] == pytest.approx(result["ls_cost"])
        assert accounting["linear_throughput_epigraph_excess"] == 0.
        np.testing.assert_allclose(result["ls_b"], result["ls_p_dis"]-result["ls_p_ch"])
        assert result["ls_split"].max() <= 1e-3
    if components != "cycling":
        assert float(result["energy_not_served"]) > (0. if lossy else 40.)
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


@pytest.mark.parametrize("lossy,T", [(False, 2), (True, 1), (True, 2)])
def test_cost_only_policy_composes_with_exact_fixed_boxes(lossy, T):
    from tests.test_preparation_bridges import build as fixed_build
    for policy in (NumericalPreparation(cost_coordinates=True), POLICY):
        build = fixed_build("ac", "vectorized", policy, with_storage=True, with_lossy=lossy, T=T)
        build.variables["b"].save_value(np.full((1, T), .1))
        if lossy:
            for name, value in (("ls_p_ch", 1.), ("ls_p_dis", 2.), ("ls_b_q", 3.), ("ls_soc", 4.)):
                variable = build.variables[name]
                variable.value = np.arange(variable.size).reshape(variable.shape, order="F") + value
        build.solve(max_iter=100)
        assert build.prob.status == "optimal"
        assert bool(build.preparation_evidence.coordinates.fixed.size) == policy.exact_fixed_boxes
        maps = build.preparation_evidence.checks["cost_coordinate_maps"]
        if lossy:
            linear = [m for m in maps if m["kind"] == "linear_cycling"]
            assert [m["physical_variable_id"] for m in linear] == [
                build.variables[name].id for name in ("ls_p_ch", "ls_p_dis")
            ]
            for m, name, value in zip(linear, ("ls_p_ch", "ls_p_dis"), (1., 2.), strict=True):
                np.testing.assert_array_equal(m["physical_start"],
                    np.arange(2*T).reshape((2, T), order="F") + value)
            layout = {item[0]: item[2:] for item in build.preparation_evidence.start_layout}
            start, stop = layout[build.variables["ls_soc"].id]
            initial = build.preparation_evidence.assigned_x0[start:stop].reshape((2, T+1), order="F")
            np.testing.assert_array_equal(initial[:, 0], [20., 30.])
            np.testing.assert_array_equal(initial[:, 1:],
                (np.arange(2*(T+1)).reshape((2, T+1), order="F") + 4.)[:, 1:])
        for mapping in maps:
            layout = {item[0]: (item[2], item[3]) for item in build.preparation_evidence.start_layout}
            start, stop = layout[mapping["solver_variable_id"]]
            np.testing.assert_allclose(build.preparation_evidence.assigned_x0[start:stop],
                (mapping["physical_start"] * mapping["scale"]).ravel(order="F"))
        np.testing.assert_allclose(extract_results(build)["Pg"][:, 0], 50.)


@pytest.mark.parametrize("lossy", [False, True])
def test_repeated_solve_resnapshots_current_load_weights(lossy):
    build = make_build("single", lossy=lossy)
    build.solve(max_iter=100)
    leaf = next(leaf for term in build._cost_coordinate_terms for leaf in term.leaves
                if leaf.kind == "shedding")
    parameter = leaf.weights.parameters()[0]
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


@pytest.mark.parametrize("lossy", [False, True])
def test_native_failure_retains_maps_but_no_public_primals(lossy):
    build = make_build("single", lossy=lossy)
    build.solve(max_iter=0)
    assert build.prob.status == "user_limit"
    assert all(variable.value is None for variable in build.prob.variables())
    assert not build.preparation_evidence.checks["restoration_available"]
    assert build.preparation_evidence.checks["cost_coordinate_maps"]


@pytest.mark.parametrize("lossy", [False, True])
@pytest.mark.parametrize("failure", ["exception", "nonfinite", "missing"])
def test_failed_resolve_clears_original_physical_variables(monkeypatch, lossy, failure):
    build = make_build("single", lossy=lossy)
    build.solve(max_iter=100)
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    def fail(self, data, *args, **kwargs):
        if failure == "exception":
            raise RuntimeError("test numerical failure")
        result = dict(status=0, obj_val=1., num_iters=0)
        if failure == "nonfinite":
            result["x"] = np.full(data["x0"].size, np.nan)
        return result
    monkeypatch.setattr(IPOPT, "solve_via_data", fail)
    with pytest.raises(cp.error.SolverError):
        build.solve()
    assert all(variable.value is None for variable in build.prob.variables())
    assert build.prob.value is None
    if lossy:
        result = extract_results(build)
        assert all(result[key] is None for key in ("ls_p_ch", "ls_p_dis", "ls_b", "ls_b_q", "ls_soc"))
        assert np.isnan(result["ls_cost"])


@pytest.mark.parametrize("warning_as_error", [False, True])
@pytest.mark.parametrize("cancelling_slack", [False, True])
@pytest.mark.parametrize("objective_assembly", ["hourly", "component_first"])
@pytest.mark.parametrize("lossy_price", [None, .03, 1e6])
def test_large_cycling_slack_warns_without_discarding_solution(monkeypatch, warning_as_error, cancelling_slack, objective_assembly, lossy_price):
    build = make_build("vectorized" if cancelling_slack or objective_assembly == "component_first" else "single", "cycling")
    build = replace(build, _numerical_preparation=replace(build.numerical_preparation,
                                                       objective_assembly=objective_assembly))
    if lossy_price is not None:
        assembly = "vectorized" if cancelling_slack or objective_assembly == "component_first" else "single"
        build = make_build(assembly, "cycling", lossy=True, lossy_price=lossy_price)
        build = replace(build, _numerical_preparation=replace(build.numerical_preparation,
                                                            objective_assembly=objective_assembly))
        fixed = [build.variables["b"] == 1., build.variables["ls_p_ch"] == 0.,
                 build.variables["ls_p_dis"] == 1.]
        build.prob = cp.Problem(build.prob.objective, [*build.prob.constraints, *fixed])
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
    if lossy_price is not None:
        expected_ideal = .5*.01*(2 if "T" in build.data else 1)
        assert record["cycling_physical_cost"] == pytest.approx(expected_ideal)
        assert record["cycling_limit"] == pytest.approx(1e-4+1e-6*expected_ideal)
        assert record["linear_throughput_epigraph_excess"] == 0.
    if cancelling_slack:
        assert abs(record["cycling_epigraph_excess"]) < 1e-4
        assert record["cycling_slack_l1"] > .19
    assert extract_results(build)["b"] is not None


@pytest.mark.parametrize("objective_assembly", ["hourly", "component_first"])
def test_affine_throughput_mismatch_is_not_explained_as_cycling_slack(monkeypatch, objective_assembly):
    build = make_build("vectorized", "shedding", lossy=True)
    build = replace(build, _numerical_preparation=replace(
        build.numerical_preparation, objective_assembly=objective_assembly))
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    original = IPOPT.solve_via_data

    def mismatch(self, *args, **kwargs):
        raw = original(self, *args, **kwargs)
        raw["obj_val"] += 1000.
        return raw

    monkeypatch.setattr(IPOPT, "solve_via_data", mismatch)
    with pytest.warns(CostAccuracyWarning):
        build.solve(max_iter=100)
    record = build.preparation_evidence.checks["cost_accounting"]
    assert record["accounting_warning"] and not record["cycling_warning"]
    assert record["linear_throughput_epigraph_excess"] == record["cycling_epigraph_excess"] == 0.
    assert record["unexplained_objective_difference"] == pytest.approx(1000., abs=1e-3)
    assert extract_results(build)["ls_p_ch"] is not None
