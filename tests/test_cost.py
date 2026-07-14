"""
Tests for src/cvxopf/cost.py -- generator cost expression builders.

Focus: MODEL=1 piecewise-linear support (_lower_convex_hull, _pwl_cost_expr)
and its integration through poly_cost_expr, alongside the existing MODEL=2
polynomial path.

A convex PWL curve must be reproduced exactly by the max-of-affine-pieces
form; a nonconvex PWL curve must be replaced by its lower convex hull and
emit a UserWarning.
"""

import warnings

import numpy as np
import pytest
import cvxpy as cp

from cvxopf.cost import _lower_convex_hull, _pwl_cost_expr, poly_cost_expr


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _eval_pwl(x, f, at, warn="ignore"):
    """Solve min cost s.t. P == at, return the PWL cost value at that point."""
    P = cp.Variable()
    with warnings.catch_warnings():
        warnings.simplefilter(warn)
        cost = _pwl_cost_expr(x, f, P)
    cp.Problem(cp.Minimize(cost), [P == at]).solve()
    return float(np.asarray(cost.value))


def _true_pwl(x, f, at):
    """Reference: linear interpolation of (x, f) at `at` (numpy)."""
    return float(np.interp(at, x, f))


# ---------------------------------------------------------------------------
# _lower_convex_hull
# ---------------------------------------------------------------------------

class TestLowerConvexHull:
    def test_already_convex_returns_all_points(self):
        # Convex increasing: every vertex lies on the lower boundary.
        x = np.array([0.0, 100.0, 200.0, 250.0])
        f = np.array([0.0, 2500.0, 5500.0, 7250.0])
        xv, fv = _lower_convex_hull(x, f)
        np.testing.assert_allclose(xv, x)
        np.testing.assert_allclose(fv, f)

    def test_drops_caved_in_point(self):
        # (100, 2000) sits above the lower hull of the other three points.
        xv, fv = _lower_convex_hull([0, 100, 200, 300], [0, 2000, 2500, 4500])
        np.testing.assert_allclose(xv, [0.0, 200.0, 300.0])
        np.testing.assert_allclose(fv, [0.0, 2500.0, 4500.0])

    def test_nonconvex_collapses_to_single_line(self):
        # Slopes 30 then 10 (decreasing): hull is the endpoints line.
        xv, fv = _lower_convex_hull([0, 100, 200], [0, 3000, 4000])
        np.testing.assert_allclose(xv, [0.0, 200.0])
        np.testing.assert_allclose(fv, [0.0, 4000.0])

    def test_two_points_returns_both(self):
        xv, fv = _lower_convex_hull([0, 150], [0, 3000])
        np.testing.assert_allclose(xv, [0.0, 150.0])
        np.testing.assert_allclose(fv, [0.0, 3000.0])

    def test_collinear_keeps_endpoints_only(self):
        # Perfectly collinear: interior point is not a vertex.
        xv, fv = _lower_convex_hull([0, 100, 200], [0, 1000, 2000])
        np.testing.assert_allclose(xv, [0.0, 200.0])
        np.testing.assert_allclose(fv, [0.0, 2000.0])


# ---------------------------------------------------------------------------
# _pwl_cost_expr -- convex inputs (exact)
# ---------------------------------------------------------------------------

class TestPWLConvex:
    # case9_dcline gen 0 cost curve.
    X = [0.0, 100.0, 200.0, 250.0]
    F = [0.0, 2500.0, 5500.0, 7250.0]

    def test_exact_at_breakpoints(self):
        for xi, fi in zip(self.X, self.F):
            assert _eval_pwl(self.X, self.F, xi) == pytest.approx(fi, abs=1e-4)

    def test_exact_at_interior_points(self):
        for at in [50.0, 120.0, 175.0, 225.0]:
            assert _eval_pwl(self.X, self.F, at) == pytest.approx(
                _true_pwl(self.X, self.F, at), abs=1e-4
            )

    def test_no_warning_for_convex(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            P = cp.Variable()
            _pwl_cost_expr(self.X, self.F, P)
        assert [x for x in w if issubclass(x.category, UserWarning)] == []

    def test_expression_is_dcp_convex(self):
        P = cp.Variable()
        expr = _pwl_cost_expr(self.X, self.F, P)
        assert expr.is_convex()
        assert cp.Problem(cp.Minimize(expr), [P >= 0, P <= 250]).is_dcp()

    def test_two_breakpoint_curve_is_single_affine(self):
        # A 2-breakpoint PWL is one segment; must still build (no cp.maximum
        # of a single arg).
        assert _eval_pwl([0.0, 200.0], [0.0, 4000.0], 100.0) == pytest.approx(
            2000.0, abs=1e-4
        )


# ---------------------------------------------------------------------------
# _pwl_cost_expr -- nonconvex inputs (hull + warning)
# ---------------------------------------------------------------------------

class TestPWLNonconvex:
    # Slopes 30 then 10 -> nonconvex; lower hull is the line slope 20.
    X = [0.0, 100.0, 200.0]
    F = [0.0, 3000.0, 4000.0]

    def test_emits_userwarning(self):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            P = cp.Variable()
            _pwl_cost_expr(self.X, self.F, P)
        uw = [x for x in w if issubclass(x.category, UserWarning)]
        assert len(uw) == 1
        assert "nonconvex" in str(uw[0].message)
        assert "convex hull" in str(uw[0].message)

    def test_uses_lower_convex_hull_values(self):
        # Hull line: f = 20 * P. Below the original interior point (3000 @ 100).
        for at in [50.0, 100.0, 150.0]:
            assert _eval_pwl(self.X, self.F, at) == pytest.approx(
                20.0 * at, abs=1e-4
            )

    def test_hull_at_or_below_original_curve(self):
        # The convex hull never exceeds the original PWL cost.
        for at in [25.0, 75.0, 125.0, 175.0]:
            assert _eval_pwl(self.X, self.F, at) <= _true_pwl(
                self.X, self.F, at
            ) + 1e-6

    def test_hulled_expression_is_dcp_convex(self):
        P = cp.Variable()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            expr = _pwl_cost_expr(self.X, self.F, P)
        assert expr.is_convex()


# ---------------------------------------------------------------------------
# poly_cost_expr integration (MODEL=1, MODEL=2, mixed)
# ---------------------------------------------------------------------------

class TestPolyCostExprModels:
    def test_model1_single_gen(self):
        # MODEL=1 row: [model, startup, shutdown, n, x0,f0, x1,f1, x2,f2]
        gencost = np.array([[1, 0, 0, 3, 0, 0, 100, 2500, 200, 5500]])
        Pg = cp.Variable(1)
        cost = poly_cost_expr(gencost, Pg)
        cp.Problem(cp.Minimize(cost), [Pg == 150]).solve()
        # linear interp between (100,2500) and (200,5500) at 150 -> 4000
        assert float(np.asarray(cost.value)) == pytest.approx(4000.0, abs=1e-4)

    def test_model2_still_works(self):
        # Quadratic 0.01*P^2 + 2*P + 100 at P=50 -> 25 + 100 + 100 = 225
        gencost = np.array([[2, 0, 0, 3, 0.01, 2.0, 100.0]])
        Pg = cp.Variable(1)
        cost = poly_cost_expr(gencost, Pg)
        cp.Problem(cp.Minimize(cost), [Pg == 50]).solve()
        assert float(np.asarray(cost.value)) == pytest.approx(225.0, abs=1e-4)

    def test_mixed_model1_and_model2(self):
        # Two gens: one PWL, one polynomial. Cost is the sum.
        gencost = np.array([
            [1, 0, 0, 3, 0, 0, 100, 2500, 200, 5500],   # PWL: 4000 @ 150
            [2, 0, 0, 2, 10.0, 0.0, 0, 0, 0, 0],         # linear 10*P: 500 @ 50
        ])
        Pg = cp.Variable(2)
        cost = poly_cost_expr(gencost, Pg)
        cp.Problem(cp.Minimize(cost), [Pg[0] == 150, Pg[1] == 50]).solve()
        assert float(np.asarray(cost.value)) == pytest.approx(4500.0, abs=1e-4)

    def test_unrecognised_model_raises(self):
        gencost = np.array([[3, 0, 0, 2, 1.0, 0.0]])
        Pg = cp.Variable(1)
        with pytest.raises(ValueError, match="MODEL=3"):
            poly_cost_expr(gencost, Pg)
