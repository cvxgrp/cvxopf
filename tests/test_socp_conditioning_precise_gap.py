"""Saved-vector arithmetic must not depend on platform longdouble precision."""

import numpy as np
from scipy import sparse

from experiments.socp_conditioning.optimality_audit import precise_gap_identity


def test_gap_identity_retains_small_term_between_large_cancelling_terms():
    A = sparse.csc_matrix([[1e16, 1.0, -1e16]])
    terms, gap = precise_gap_identity(
        A,
        sparse.csc_matrix((3, 3)),
        np.zeros(3),
        np.array([1.0]),
        np.ones(3),
        np.zeros(1),
        np.ones(1),
        1.0,
    )
    assert terms == dict(
        x_dot_stationarity=1.0, complementarity=0.0, minus_z_dot_primal_error=0.0
    )
    assert gap == 1.0


def test_gap_identity_includes_quadratic_objective_and_divisor():
    terms, gap = precise_gap_identity(
        sparse.csc_matrix([[2.0]]),
        sparse.csc_matrix([[3.0]]),
        np.array([4.0]),
        np.array([5.0]),
        np.array([6.0]),
        np.array([7.0]),
        np.array([8.0]),
        2.0,
    )
    assert gap == 2 * (3 * 6**2 + 4 * 6 + 5 * 8)
    assert sum(terms.values()) == gap
