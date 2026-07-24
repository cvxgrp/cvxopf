"""Tests for the Milestone 16 dispatchable-generator component."""

import cvxpy as cp
import numpy as np
import pytest

from cvxopf.generator import (
    DispatchableGenerator,
    _validate_generators,
    ac_operating_constraints,
    coupling_constraints,
    dc_operating_constraints,
    gen_cost_expr,
    gen_from_matpower,
    generator_bounds,
    generator_gencost,
    injections,
    make_generator_incidence,
)
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
    injection, scaling = injections(generators, Pg, {10: 0, 20: 1})
    bounds = generator_bounds(generators, 100.0)

    assert scaling is None
    assert injection.shape == (2,)
    for constraints in (
        ac_operating_constraints(Pg, bounds[0], bounds[1]),
        dc_operating_constraints(Pg, bounds[0], bounds[1]),
    ):
        problem = cp.Problem(cp.Minimize(cp.sum(Pg)), constraints)
        assert problem.is_dcp()


def test_generator_cost_is_dcp_and_memoryless():
    generators = [
        DispatchableGenerator(bus=1, p_max_mw=100, cost_coeffs=(5, 2, 0.01))
    ]
    Pg = cp.Variable(1)
    objective = gen_cost_expr(generator_gencost(generators), 100.0 * Pg)

    assert objective.is_dcp()
    assert coupling_constraints([Pg]) == []


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
