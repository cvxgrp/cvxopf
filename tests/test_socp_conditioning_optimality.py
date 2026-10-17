"""Solver-free tests for the retained-canonical optimality audit."""

from pathlib import Path

import numpy as np
import pytest
from scipy import sparse

from experiments.socp_conditioning.optimality_audit import (
    analyze,
    cone_errors,
    cone_layout,
    fixed_box_multipliers,
    storage_epigraph,
)


def test_fixed_bounds_can_have_large_cancelling_multipliers():
    A = sparse.csc_matrix([[-1.0], [1.0]])
    z = np.array([1e8, 1e8])
    result = fixed_box_multipliers(
        A, np.zeros(2), z, [dict(name="Pg", shape=[1], offset=0)], (0, 2, [])
    )
    assert result["Pg"]["fixed_coordinates"] == 1
    assert result["Pg"]["fraction_total_dual_squared_norm"] == 1
    np.testing.assert_array_equal(A.T @ z, [0.0])
    assert not fixed_box_multipliers(
        A, np.array([0.0, 1.0]), z, [dict(name="Pg", shape=[1], offset=0)], (0, 2, [])
    )


def test_cone_membership_and_dual_free_equalities():
    layout = (1, 2, [3])
    vector = np.array([4.0, 0.0, -2.0, 1.0, 3.0, 4.0])
    assert cone_errors(vector, layout) == dict(
        equality_inf=4.0, nonnegative_violation=2.0, soc_violation=4.0
    )
    assert cone_errors(vector, layout, dual=True)["equality_inf"] == 0


def test_layout_covers_all_rows_and_rejects_unsupported_cones():
    text = (
        "1 equalities, 2 inequalities, 0 exponential cones,\n"
        "SOC constraints: [3], PSD constraints: [],\n 3d power cones [], []."
    )
    assert cone_layout(text, 6) == (1, 2, [3])
    with pytest.raises(ValueError, match="all rows"):
        cone_layout(text, 7)
    with pytest.raises(ValueError, match="unsupported"):
        cone_layout(text.replace("0 exponential", "1 exponential"), 6)


def epigraph_fixture():
    A = sparse.csc_matrix([[-1.0, 1.0], [-1.0, -1.0]])
    P = sparse.csc_matrix((2, 2))
    c, rhs = np.array([0.01, 0.0]), np.zeros(2)
    variables = [
        dict(name="var123", shape=[1], offset=0),
        dict(name="b", shape=[1], offset=1),
    ]
    return A, P, c, rhs, variables, (0, 2, [])


def test_exact_epigraph_recognition_and_feasible_improvement():
    args = epigraph_fixture()
    A, _, c, rhs, _, layout = args
    _, ti, bi, pairs = storage_epigraph(*args)
    np.testing.assert_array_equal(pairs, [[0, 1]])
    x = np.array([5.0, -2.0])
    fixed = x.copy()
    fixed[ti] = abs(x[bi])
    assert cone_errors(rhs - A @ fixed, layout)["nonnegative_violation"] == 0
    assert c @ (x - fixed) == pytest.approx(0.03)
    assert fixed[bi] == x[bi]


@pytest.mark.parametrize("alteration", ["sign", "cost", "rhs", "quadratic"])
def test_reject_non_epigraph_structure(alteration):
    A, P, c, rhs, variables, layout = epigraph_fixture()
    if alteration == "sign":
        A = sparse.csc_matrix([[-1.0, 1.0], [-1.0, 1.0]])
    elif alteration == "cost":
        c[0] = 0
    elif alteration == "rhs":
        rhs[0] = 1
    else:
        P = sparse.eye(2, format="csc")
    with pytest.raises(ValueError, match="verified storage"):
        storage_epigraph(A, P, c, rhs, variables, layout)


@pytest.mark.parametrize("divisor", [1, 1000])
def test_retained_audit_without_any_solver(divisor):
    root = (
        Path(__file__).resolve().parents[1]
        / "experiments/socp_conditioning/results/sweep_001"
    )
    if not root.exists():
        pytest.skip("optional retained local diagnostic")
    result = analyze(root / f"surplus-combined-divisor-{divisor}")
    assert result["unaffected_rows_exactly_unchanged"]
    assert result["physical_variables_unchanged"]
    assert result["storage"]["tightening_rows_min"] == 0
    if divisor == 1000:
        assert result["objective"]["improvement"] == pytest.approx(39.4437080676)
        assert result["storage"]["stationarity_relative_to_coefficient_max"] > 0.999
    else:
        assert result["objective"]["improvement"] < 1e-5
