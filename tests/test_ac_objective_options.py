"""Production objective assembly regressions, not a new Tracy qualification."""
from dataclasses import FrozenInstanceError, replace
import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import (NumericalPreparation, OPFOptions, OPFBuild, StorageUnitIdeal,
                    HVDCLink, build_opf, build_opf_multistep, extract_results,
                    gen_from_matpower, CostAccuracyWarning)
from cvxopf._cost_coordinates import (CostCoordinateTerm, ObjectiveCostContribution,
                                     transform_cost_coordinates)
from cvxopf._numerical_preparation import reject_hierarchical_preparation
from cvxopf.testcases import case9, case14, case57
from cvxopf.testcases.case9_pwl import case9_pwl
from cvxopf.load import loads_from_matpower
from tests.test_cost_coordinates import make_build
from tests.test_ac_objective_assembly import portable  # noqa: F401 -- portable non-solving fixture

POLICY = NumericalPreparation(normalize_device_limits=True, exact_fixed_boxes=True,
                              cost_coordinates=True, objective_assembly="component_first")


def candidate(build):
    return replace(build, _numerical_preparation=replace(build.numerical_preparation,
                   cost_coordinates=True, objective_assembly="component_first"))


@pytest.mark.parametrize("value", [None, True, 1, "auto", "components", ["hourly"]])
def test_closed_option(value):
    with pytest.raises(ValueError, match="objective_assembly"):
        NumericalPreparation(objective_assembly=value)


def test_policy_default_dependency_and_snapshot():
    assert NumericalPreparation().objective_assembly == "hourly"
    assert not NumericalPreparation().enabled
    with pytest.raises(ValueError, match="requires cost_coordinates"):
        NumericalPreparation(objective_assembly="component_first")
    options = OPFOptions(numerical_preparation=POLICY)
    build = build_opf_multistep(case9(), T=2, loads=[], options=options)
    options.numerical_preparation = NumericalPreparation()
    assert build.numerical_preparation is POLICY
    with pytest.raises(FrozenInstanceError):
        build.numerical_preparation.objective_assembly = "hourly"
    with pytest.raises(ValueError, match="Milestone 21"):
        reject_hierarchical_preparation(POLICY)


@pytest.mark.parametrize("form", ["socp", "lossy_dc", "singlenode_dc"])
def test_convex_option_rejected_before_dispatch(monkeypatch, form):
    from cvxopf import problem
    monkeypatch.setattr(problem, "_get_vectorized_multistep_builders", lambda: {
        form: lambda *_a, **_k: pytest.fail("unsupported build dispatched")})
    with pytest.raises(ValueError, match="AC only"):
        build_opf_multistep(case9(), T=2, loads=[], formulation=form,
                            options=OPFOptions(numerical_preparation=replace(POLICY, normalize_device_limits=False)))


@pytest.mark.parametrize("single", [True, False])
def test_single_or_stepwise_rejected_before_construction(single):
    with pytest.raises(ValueError, match="standalone vectorized AC"):
        if single:
            build_opf(case9(), options=OPFOptions(numerical_preparation=POLICY))
        else:
            build_opf_multistep(case9(), T=2, loads=[], temporal_assembly="stepwise",
                                options=OPFOptions(numerical_preparation=POLICY))


@pytest.mark.usefixtures("portable")
def test_default_preserves_expression_structure_and_start():
    from experiments.ac_objective_assembly import model as prior
    from experiments.ac_production_verification.audit import canonical
    _, _, physical, _, _ = prior.construct(prior.arms()[0])
    implicit = canonical(physical)
    explicit = canonical(replace(physical, _numerical_preparation=replace(
        physical.numerical_preparation, objective_assembly="hourly")))
    assert prior.layout(*implicit) == prior.layout(*explicit)
    for key in ("x0", "lb", "ub", "cl", "cu"):
        np.testing.assert_array_equal(implicit[-2][key], explicit[-2][key])
    assert str(implicit[0].prob.objective) == str(explicit[0].prob.objective)


@pytest.mark.usefixtures("portable")
def test_production_matches_successful_experimental_representation():
    from experiments.ac_objective_assembly import model as prior
    from experiments.ac_production_verification.audit import canonical
    from experiments.ac_cost_qualification.audit import canonical_objective
    _, _, physical, _, _ = prior.construct(prior.arms()[0])
    _, _, experimental, _, _ = prior.construct(prior.arms()[1])
    actual, expected = canonical(candidate(physical)), canonical(experimental)
    assert prior.layout(*actual) == prior.layout(*expected)
    for key in ("x0", "lb", "ub", "cl", "cu"):
        np.testing.assert_array_equal(actual[-2][key], expected[-2][key])
    point = .1 + np.arange(len(actual[-2]["x0"])) * .000123
    assert canonical_objective(actual[-2], actual[-1], point)[0] == canonical_objective(expected[-2], expected[-1], point)[0]
    for a, b in zip(actual[-2]["problem"].constraints, expected[-2]["problem"].constraints, strict=True):
        np.testing.assert_array_equal(a.expr.value, b.expr.value)
    assert type(actual[0].prob.objective) is cp.Minimize


@pytest.mark.parametrize("delta", [.25, 1., 2.])
@pytest.mark.parametrize("pwl", [False, True])
def test_complete_costs_integrated_once_with_hvdc_and_terminal(delta, pwl):
    case = case9_pwl() if pwl else case9()
    generators = gen_from_matpower(case["gen"], case["gencost"])
    if not pwl:
        generators[0] = replace(generators[0], cost_coeffs=(17., -2., .1))
    storage = [StorageUnitIdeal(5, 10., 40., 20., aging_weight=.01,
               terminal_soc=20., terminal_cost="quadratic", terminal_weight=.7),
               StorageUnitIdeal(6, 10., 40., 20., aging_weight=0.)]
    loads = [replace(load, shedding_cost_per_mwh=5000.) for load in loads_from_matpower(case["bus"])]
    build = build_opf_multistep(case, T=2, loads=loads, generators=generators, storage=storage,
            hvdc=[HVDCLink(5, 7, -5., 0., cost_coeffs=(3., .1, .01))], delta=delta,
            options=OPFOptions(numerical_preparation=POLICY))
    for value in (.1, .3):
        for variable in build.prob.variables():
            variable.save_value(np.full(variable.shape, value))
        cost = float(build.prob.objective.value)
        residuals = [constraint.expr.value.copy() for constraint in build.prob.constraints]
        solver, bindings = transform_cost_coordinates(build)
        assert solver.prob.objective.is_dcp()
        assert solver.prob.objective.value == pytest.approx(cost, rel=1e-12)
        assert "hvdc" in [item.component for item in build._objective_cost_contributions]
        assert sum(item.terminal is not None for item in build._objective_cost_contributions) == 1
        assert len([b for b in bindings if b.absolute is not None]) == 1
        for constraint, expected in zip(solver.prob.constraints, residuals, strict=True):
            np.testing.assert_allclose(constraint.expr.value, expected, rtol=1e-12, atol=1e-12)
        assert {c.id for c in solver.prob.constraints} == {c.id for c in build.prob.constraints}


def test_parameter_weights_cross_zero_and_maps_rebuild_without_priced_zero_epigraph():
    b, shed = cp.Variable((2, 3)), cp.Variable((2, 3))
    w = cp.Parameter((2, 1), nonneg=True, value=np.array([[.01], [.03]]))
    v = cp.Parameter((2, 1), nonneg=True, value=np.array([[1000.], [2000.]]))
    rates = [cp.sum(cp.multiply(w, cp.abs(b)), axis=0), cp.sum(cp.multiply(v, shed), axis=0)]
    terms = (CostCoordinateTerm(b, w, rates[0], "cycling", 0),
             CostCoordinateTerm(shed, v, rates[1], "shedding", 0))
    delta = .25
    objective = delta * cp.sum(rates[0] + rates[1])
    build = OPFBuild(cp.Problem(cp.Minimize(objective), [b + shed <= 10]), {}, {}, "ac", False,
        temporal_assembly="vectorized", _numerical_preparation=POLICY, _cost_coordinate_delta=delta,
        _cost_coordinate_terms=terms, _objective_cost_expression=objective,
        _objective_cost_contributions=tuple(ObjectiveCostContribution(
            term.kind, term.rate, delta * cp.sum(term.rate), None, (term,)) for term in terms))
    for weights in ([[.01], [.03]], [[0.], [.03]], [[0.], [0.]], [[.02], [.03]]):
        w.value = np.asarray(weights)
        v.value = np.asarray(weights) * 1000.
        b.save_value(np.array([[-2., 3., 1.], [1., -4., 2.]]))
        shed.save_value(np.full((2, 3), .2))
        expected = objective.value
        solver, bindings = transform_cost_coordinates(build)
        assert solver.prob.objective.value == pytest.approx(expected, rel=1e-12)
        active = np.broadcast_to(np.asarray(weights) > 0, b.shape)
        assert bindings[0].absolute is None if not active.any() else bindings[0].absolute.size == active.sum()
        np.testing.assert_array_equal(bindings[0].scale[~active], 1.)


def test_unknown_or_partial_objective_contributions_fail_explicitly():
    build = candidate(make_build("vectorized"))
    from cvxopf._hierarchical_solver import _complete_start
    _complete_start(build)
    changed = replace(build, prob=cp.Problem(cp.Minimize(build.prob.objective.expr + 7.), build.prob.constraints))
    with pytest.raises(ValueError, match="typed component assembly"):
        transform_cost_coordinates(changed)
    with pytest.raises(ValueError, match="complete declared"):
        ObjectiveCostContribution("partial", cp.Constant([1., 2.]), cp.Constant(3.), None,
                                  (build._cost_coordinate_terms[0],))
    altered = replace(build, _objective_cost_contributions=build._objective_cost_contributions[:-1])
    # Removal of a cost-bearing coordinate contribution must not be silent.
    altered = replace(altered, _objective_cost_contributions=tuple(
        c for c in altered._objective_cost_contributions if not c.coordinates))
    with pytest.raises(ValueError, match="coverage differs"):
        transform_cost_coordinates(altered)


def test_repeated_public_solves_resnapshot_parameters_crossing_zero():
    """Exercise the production boundary with a small declared-cost test graph."""
    b, shed, g = (cp.Variable((2, 2), name=name) for name in ("battery", "shed", "generation"))
    w = cp.Parameter((2, 1), nonneg=True, value=np.array([[.01], [.03]]))
    v = cp.Parameter((2, 1), nonneg=True, value=np.array([[1000.], [2000.]]))
    generator_rate = cp.sum(cp.square(g), axis=0)
    rates = (cp.sum(cp.multiply(w, cp.abs(b)), axis=0), cp.sum(cp.multiply(v, shed), axis=0))
    terms = (CostCoordinateTerm(b, w, rates[0], "cycling", 0),
             CostCoordinateTerm(shed, v, rates[1], "shedding", 0))
    delta = .25
    objective = delta * cp.sum(generator_rate + rates[0] + rates[1])
    graph = cp.Problem(cp.Minimize(objective), [b + shed + g == 5, b >= -1, b <= 1,
                                               g >= 0, g <= 3, shed >= 0, shed <= 5])
    build = OPFBuild(graph, {}, {}, "ac", False, temporal_assembly="vectorized",
        _numerical_preparation=POLICY, _cost_coordinate_delta=delta, _cost_coordinate_terms=terms,
        _objective_cost_expression=objective, _objective_cost_contributions=(
            ObjectiveCostContribution("generation", generator_rate, delta * cp.sum(generator_rate), None),
            *(ObjectiveCostContribution(term.kind, term.rate, delta * cp.sum(term.rate), None, (term,))
              for term in terms)))
    parameters = tuple(id(p) for p in graph.parameters())
    for weights in ([[.01], [.03]], [[0.], [.03]], [[0.], [0.]], [[.02], [.03]]):
        w.value = np.asarray(weights)
        v.value = np.asarray(weights) * 100000.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", CostAccuracyWarning)
            build.solve(max_iter=100)
        assert graph.status == "optimal"
        assert build.prob is graph and tuple(id(p) for p in graph.parameters()) == parameters
        for term, record in zip(terms, build.preparation_evidence.checks["cost_coordinate_maps"], strict=True):
            expected = np.broadcast_to(delta * term.weights.value, term.variable.shape)
            np.testing.assert_array_equal(record["scale"], np.where(expected > 0, expected, 1.))
        assert build.preparation_evidence.checks["objective_assembly"] == "component_first"
        assert not build.preparation_evidence.checks["cost_accounting"]["accounting_warning"]
        assert max(np.max(c.violation(), initial=0.) for c in graph.constraints) < 2e-5


@pytest.mark.parametrize("components,zero", [("both", False), ("cycling", True), ("shedding", False)])
def test_public_solve_restores_accounting_and_warning_semantics(components, zero):
    build = candidate(make_build("vectorized", components, zero=zero))
    graph, variables = build.prob, tuple(build.prob.variables())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", CostAccuracyWarning)
        build.solve(max_iter=100)
    assert build.prob is graph and tuple(build.prob.variables()) == variables
    result = extract_results(build)
    assert result["status"] == "optimal"
    checks = build.preparation_evidence.checks
    assert checks["objective_assembly"] == "component_first"
    assert checks["cost_accounting"]["physical_objective"] == pytest.approx(result["objective"])
    assert not checks["cost_accounting"]["accounting_warning"]
    assert max(np.max(c.violation(), initial=0.) for c in build.prob.constraints) < 2e-5


def test_failed_resolve_retains_selection_and_clears_public_values():
    build = candidate(make_build("vectorized"))
    build.solve(max_iter=100)
    build.solve(max_iter=0)
    assert build.prob.status == "user_limit"
    assert all(v.value is None for v in build.prob.variables())
    assert build.preparation_evidence.checks["objective_assembly"] == "component_first"
    assert not build.preparation_evidence.checks["restoration_available"]


@pytest.mark.parametrize("case_fn,fixture_name", [(case9, "case9_pypower_reference.json"),
    (case9_pwl, "case9_pwl_pypower_reference.json"), (case14, "case14_pypower_reference.json"),
    (case57, "case57_pypower_reference.json")])
def test_generator_only_vectorized_t1_against_unchanged_pypower_fixture(case_fn, fixture_name):
    from tests.test_vs_pypower_reference import (_load_fixture, OBJ_RTOL, PG_ATOL, QG_ATOL,
                                               VM_ATOL, VA_ATOL, TestCase57VsPypower)
    reference = _load_fixture(fixture_name)
    case = case_fn()
    build = build_opf_multistep(case, T=1, df_P=pd.DataFrame([case["bus"][:, 2]]),
            df_Q=pd.DataFrame([case["bus"][:, 3]]), options=OPFOptions(numerical_preparation=POLICY))
    build.solve(max_iter=100)
    result = extract_results(build)
    assert result["status"] == "optimal"
    assert result["objective"] == pytest.approx(reference["objective"], rel=OBJ_RTOL)
    # Use the fixture suite's existing case-specific tolerance, not the case9 one.
    pg_tolerance = TestCase57VsPypower.PG_ATOL if case_fn is case57 else PG_ATOL
    for name, tolerance in (("Pg", pg_tolerance), ("Qg", QG_ATOL), ("Vm", VM_ATOL), ("Va_deg", VA_ATOL)):
        np.testing.assert_allclose(np.asarray(result[name])[0], reference[name], atol=tolerance, rtol=0.)
    assert build.preparation_evidence.checks["objective_assembly"] == "component_first"
    baseline = build_opf_multistep(case, T=1, df_P=pd.DataFrame([case["bus"][:, 2]]),
            df_Q=pd.DataFrame([case["bus"][:, 3]]), options=OPFOptions(
                numerical_preparation=replace(POLICY, objective_assembly="hourly")))
    baseline.solve(max_iter=100)
    original = extract_results(baseline)
    assert original["objective"] == result["objective"]
    for name in ("Pg", "Qg", "Vm", "Va_deg"):
        np.testing.assert_array_equal(original[name], result[name])
