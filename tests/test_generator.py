"""Tests for the Milestone 16 dispatchable-generator component."""

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf.generator import (
    DispatchableGenerator,
    _validate_generators,
    ac_injections,
    ac_network_constraints,
    ac_operating_constraints,
    coupling_constraints,
    dc_operating_constraints,
    gen_cost_expr,
    gen_from_matpower,
    generator_bounds,
    generator_gencost,
    dc_injections,
    dc_network_constraints,
    make_generator_incidence,
)
from cvxopf.ac_problem import _parse_case as _parse_ac_case
from cvxopf.problem import OPFOptions, build_opf, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import case9, case14
from cvxopf.testcases.case9_pwl import case9_pwl


def test_matpower_model2_roundtrip_preserves_generator_data():
    for case_factory in (case9, case14):
        case = case_factory()
        generators = gen_from_matpower(case["gen"], case["gencost"])

        np.testing.assert_allclose(generator_gencost(generators), case["gencost"])
        assert [g.bus for g in generators] == case["gen"][:, 0].astype(int).tolist()


def test_matpower_mixed_pwl_roundtrip_preserves_generator_data():
    case = case9_pwl()
    generators = gen_from_matpower(case["gen"], case["gencost"])

    assert [g.cost_type for g in generators] == [
        "piecewise_linear",
        "polynomial",
        "piecewise_linear",
    ]
    assert generators[0].cost_points == (
        (0.0, 0.0),
        (100.0, 2500.0),
        (200.0, 5500.0),
        (250.0, 7250.0),
    )
    np.testing.assert_allclose(generator_gencost(generators), case["gencost"])


@pytest.mark.parametrize("case_factory", [case9, case9_pwl])
def test_ac_parser_fallback_matches_explicit_generator_list(case_factory):
    case = case_factory()
    generators = gen_from_matpower(case["gen"], case["gencost"])

    fallback = _parse_ac_case(case, OPFOptions())
    explicit = _parse_ac_case(case, OPFOptions(), generators=generators)

    for key in (
        "Cg",
        "gen_bus",
        "Pgmin",
        "Pgmax",
        "Qgmin",
        "Qgmax",
        "gencost",
    ):
        np.testing.assert_allclose(explicit[key], fallback[key])


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_public_explicit_generators_match_matpower_fallback(formulation):
    case = case9()
    generators = gen_from_matpower(case["gen"], case["gencost"])

    fallback = build_opf(case, formulation=formulation)
    explicit = build_opf(case, formulation=formulation, generators=generators)

    for key in ("Cg", "gen_bus", "Pgmin", "Pgmax", "gencost"):
        if key in fallback.data:
            np.testing.assert_allclose(explicit.data[key], fallback.data[key])

    fallback.solve()
    explicit.solve()
    fallback_results = extract_results(fallback)
    explicit_results = extract_results(explicit)
    assert explicit_results["objective"] == pytest.approx(
        fallback_results["objective"], rel=1e-8, abs=1e-6
    )
    np.testing.assert_allclose(
        explicit_results["Pg"], fallback_results["Pg"], rtol=1e-7, atol=1e-5
    )


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_public_pwl_generators_preserve_matpower_cost_data(formulation):
    case = case9_pwl()
    generators = gen_from_matpower(case["gen"], case["gencost"])

    fallback = build_opf(case, formulation=formulation)
    explicit = build_opf(case, formulation=formulation, generators=generators)

    np.testing.assert_allclose(explicit.data["gencost"], fallback.data["gencost"])
    np.testing.assert_allclose(explicit.data["gencost"], case["gencost"])


@pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc"])
def test_public_explicit_generator_convex_paths_are_dcp(formulation):
    case = case9_pwl()
    generators = gen_from_matpower(case["gen"], case["gencost"])
    build = build_opf(case, formulation=formulation, generators=generators)

    assert build.prob.is_dcp()


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_public_explicit_generators_override_case_generator_data(formulation):
    case = case9()
    generators = gen_from_matpower(case["gen"], case["gencost"])
    generators[0].p_max_mw = 123.0

    build = build_opf(case, formulation=formulation, generators=generators)

    assert build.data["Pgmax"][0] == pytest.approx(1.23)


@pytest.mark.parametrize("formulation", ["ac", "lossy_dc", "singlenode_dc"])
def test_explicit_generators_allow_network_case_without_legacy_tables(formulation):
    complete_case = case9()
    generators = gen_from_matpower(
        complete_case["gen"], complete_case["gencost"]
    )
    network_case = {
        key: value for key, value in complete_case.items()
        if key not in {"gen", "gencost"}
    }

    build = build_opf(
        network_case, formulation=formulation, generators=generators
    )

    assert build.data["ng"] == len(generators)
    assert "gen" not in network_case
    assert "gencost" not in network_case


def test_explicit_generators_allow_network_only_multistep_case():
    complete_case = case9()
    generators = gen_from_matpower(
        complete_case["gen"], complete_case["gencost"]
    )
    network_case = {
        key: value for key, value in complete_case.items()
        if key not in {"gen", "gencost"}
    }
    df_P = pd.DataFrame([complete_case["bus"][:, 2]])

    build = build_opf_multistep(
        network_case,
        df_P,
        None,
        T=1,
        formulation="lossy_dc",
        generators=generators,
    )

    assert build.data["ng"] == len(generators)
    assert build.prob.is_dcp()


def test_bounds_and_incidence_preserve_inactive_generator_semantics():
    generators = [
        DispatchableGenerator(
            bus=10,
            p_min_mw=20,
            p_max_mw=100,
            q_min_mvar=-30,
            q_max_mvar=40,
        ),
        DispatchableGenerator(bus=20, p_max_mw=50, status=0),
    ]

    Cg = make_generator_incidence(generators, 2, {10: 0, 20: 1})
    np.testing.assert_array_equal(Cg, [[1.0, 0.0], [0.0, 0.0]])

    Pgmin, Pgmax, Qgmin, Qgmax = generator_bounds(generators, 100.0)
    np.testing.assert_allclose(Pgmin, [0.2, 0.0])
    np.testing.assert_allclose(Pgmax, [1.0, 0.0])
    np.testing.assert_allclose(Qgmin, [-0.3, 0.0])
    np.testing.assert_allclose(Qgmax, [0.4, 0.0])


def test_injection_and_operating_regions_are_dcp():
    generators = [
        DispatchableGenerator(bus=10, p_max_mw=100),
        DispatchableGenerator(bus=20, p_max_mw=50),
    ]
    Pg = cp.Variable(2)
    Qg = cp.Variable(2)
    p_ac, q_ac, ac_scaling = ac_injections(
        generators, Pg, Qg, {10: 0, 20: 1}
    )
    p_dc, q_dc, dc_scaling = dc_injections(
        generators, Pg, {10: 0, 20: 1}
    )
    bounds = generator_bounds(generators, 100.0)

    assert ac_scaling is dc_scaling is None
    assert p_ac.shape == q_ac.shape == p_dc.shape == (2,)
    assert q_dc is None
    for variables, constraints in (
        (
            [Pg, Qg],
            ac_operating_constraints(Pg, Qg, *bounds),
        ),
        ([Pg], dc_operating_constraints(Pg, bounds[0], bounds[1])),
    ):
        problem = cp.Problem(
            cp.Minimize(sum(cp.sum(variable) for variable in variables)),
            constraints,
        )
        assert problem.is_dcp()


def test_generator_cost_is_dcp_and_memoryless():
    generators = [
        DispatchableGenerator(bus=1, p_max_mw=100, cost_coeffs=(5, 2, 0.01))
    ]
    Pg = cp.Variable(1)
    objective = gen_cost_expr(generator_gencost(generators), 100.0 * Pg)

    assert objective.is_dcp()
    assert coupling_constraints(generators, [Pg]) == []


def test_higher_order_polynomial_cost_recommends_pwl():
    generator_unit = DispatchableGenerator(
        bus=1,
        p_max_mw=100,
        cost_coeffs=(0.0, 1.0, 0.1, 0.01),
    )
    with pytest.raises(
        ValueError,
        match="above degree 2.*piecewise_linear",
    ):
        _validate_generators([generator_unit], {1})


def test_generator_owns_voltage_setpoint_network_constraint():
    generators = [
        DispatchableGenerator(bus=10, p_max_mw=100, vg=1.03),
        DispatchableGenerator(bus=20, p_max_mw=100, vg=1.01, status=0),
    ]
    v = cp.Variable((2, 1))
    constraints = ac_network_constraints(
        generators,
        v,
        {10: 0, 20: 1},
        controlled_buses=[0, 1],
        enforce_vset=True,
    )
    assert len(constraints) == 1
    problem = cp.Problem(cp.Minimize(0), constraints)
    problem.solve()
    assert v.value[0, 0] == pytest.approx(1.03)


def test_generator_network_constraints_are_empty_when_disabled_or_dc():
    generator_unit = DispatchableGenerator(bus=1, p_max_mw=100, vg=1.02)
    v = cp.Variable((1, 1))
    assert ac_network_constraints(
        [generator_unit],
        v,
        {1: 0},
        controlled_buses=[0],
        enforce_vset=False,
    ) == []
    assert dc_network_constraints([generator_unit]) == []


def test_piecewise_linear_generator_cost_delegates_to_cost_module():
    generators = [
        DispatchableGenerator(
            bus=1,
            p_max_mw=200,
            cost_type="piecewise_linear",
            cost_points=((0, 0), (100, 2500), (200, 5500)),
        )
    ]
    _validate_generators(generators, {1})
    Pg = cp.Variable(1)
    objective = gen_cost_expr(generator_gencost(generators), Pg)
    problem = cp.Problem(cp.Minimize(objective), [Pg == 150])

    assert problem.is_dcp()
    problem.solve()
    assert objective.value == pytest.approx(4000.0, abs=1e-4)


@pytest.mark.parametrize(
    ("generator", "message"),
    [
        (DispatchableGenerator(bus=99, p_max_mw=10), "not in case bus table"),
        (
            DispatchableGenerator(bus=1, p_min_mw=20, p_max_mw=10),
            "p_min_mw",
        ),
        (
            DispatchableGenerator(bus=1, p_max_mw=10, q_min_mvar=2, q_max_mvar=1),
            "q_min_mvar",
        ),
        (
            DispatchableGenerator(bus=1, p_max_mw=10, cost_coeffs=(0, 0, -1)),
            "c2",
        ),
        (
            DispatchableGenerator(
                bus=1,
                p_max_mw=10,
                cost_type="piecewise_linear",
                cost_points=((0, 0),),
            ),
            "at least two",
        ),
        (
            DispatchableGenerator(
                bus=1,
                p_max_mw=10,
                cost_type="piecewise_linear",
                cost_points=((0, 0), (0, 1)),
            ),
            "strictly increasing",
        ),
        (
            DispatchableGenerator(bus=1, p_max_mw=10, cost_type="cubic_spline"),
            "unknown cost_type",
        ),
    ],
)
def test_validation_rejects_invalid_generator(generator, message):
    with pytest.raises(ValueError, match=message):
        _validate_generators([generator], {1})
