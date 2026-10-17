"""Symmetric scaling is an invertible linear-coordinate change."""

import numpy as np
import pytest
from scipy import sparse
from scipy.sparse.linalg import splu

from experiments.socp_conditioning import kkt_scaling as s


@pytest.mark.parametrize("passes", s.PASSES)
def test_scaling_preserves_system_and_solution(passes):
    K = sparse.csc_matrix([[1e8, 2.0, 0.0], [2.0, -1e-5, 3.0], [0.0, 3.0, -1e4]])
    rhs = np.array([1.0, 2.0, 3.0])
    A, D, _ = s.ruiz(K, passes)
    assert (A != A.T).nnz == 0
    x = D * splu(A).solve(D * rhs)
    assert np.allclose(K @ x, rhs, atol=1e-10, rtol=1e-10)
    rng = np.random.default_rng(0)
    y = rng.normal(size=3)
    assert np.allclose(A @ y, D * (K @ (D * y)))
    if passes == 0:
        assert np.array_equal(D, np.ones(3))
        assert (A != K).nnz == 0


def test_zero_row_is_rejected():
    with pytest.raises(ValueError, match="zero"):
        s.ruiz(sparse.diags([1.0, 0.0]), 5)
