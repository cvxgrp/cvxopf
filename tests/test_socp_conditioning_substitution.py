"""Exact canonical substitution, including nonzero fixed quadratic coordinates."""

from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse

from experiments.socp_conditioning.fixed_substitution import Reduction


def data():
    return dict(
        A=sparse.csc_matrix([[2.0, 0.0], [1.0, 3.0], [-1.0, 2.0]]),
        P=sparse.csc_matrix([[4.0, 1.0], [1.0, 2.0]]),
        b=np.array([4.0, 7.0, 8.0]),
        c=np.array([3.0, 5.0]),
        dims=SimpleNamespace(zero=1),
    )


def test_nonzero_substitution_preserves_quadratic_and_affine_terms():
    original = data()
    reduction = Reduction(original, {0})
    np.testing.assert_array_equal(reduction.values, [2.0])
    np.testing.assert_array_equal(reduction.data["A"].toarray(), [[3.0], [2.0]])
    np.testing.assert_array_equal(reduction.data["P"].toarray(), [[2.0]])
    np.testing.assert_array_equal(reduction.data["c"], [7.0])
    np.testing.assert_array_equal(reduction.data["b"], [5.0, 10.0])
    assert reduction.offset == 14.0
    assert reduction.data["dims"].zero == 0
    assert original["dims"].zero == 1
    assert max(reduction.check().values()) < 1e-12


def test_reconstruction_restores_duals_offset_and_full_primal():
    original = data()
    reduction = Reduction(original, {0})
    raw = SimpleNamespace(
        x=[3.0],
        s=[4.0, 5.0],
        z=[6.0, 7.0],
        status="Solved",
        obj_val=10.0,
        obj_val_dual=9.0,
        r_prim=0.0,
        r_dual=0.0,
        iterations=1,
        solve_time=0.1,
    )
    full = reduction.restore(raw)
    np.testing.assert_array_equal(full.x, [2.0, 3.0])
    np.testing.assert_array_equal(full.s, [0.0, 4.0, 5.0])
    assert full.obj_val == 24.0
    assert full.obj_val_dual == 23.0
    residual = original["P"] @ full.x + original["c"] + original["A"].T @ full.z
    assert residual[0] == 0
    assert full.s @ full.z == 4 * 6 + 5 * 7


def test_only_explicit_allowed_unary_equalities_are_eliminated():
    with pytest.raises(ValueError, match="no exact fixed"):
        Reduction(data(), {1})
    p = data()
    p["dims"].zero = 0
    with pytest.raises(ValueError, match="no exact fixed"):
        Reduction(p, {0})


def test_duplicate_fixed_rows_are_not_silently_removed():
    p = data()
    p["A"] = sparse.csc_matrix([[2.0, 0.0], [1.0, 0.0], [-1.0, 2.0]])
    p["dims"].zero = 2
    with pytest.raises(ValueError, match="multiple defining"):
        Reduction(p, {0})


def test_fixed_values_must_be_finite():
    p = data()
    p["b"][0] = np.inf
    with pytest.raises(ValueError, match="nonfinite"):
        Reduction(p, {0})
