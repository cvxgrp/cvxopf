"""Independent matrix reconstruction and residual accounting checks."""

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import splu

from experiments.socp_conditioning import kkt_factorization as k
from experiments.socp_conditioning import kkt_residual_check as r
from experiments.socp_conditioning.native_step_probe import matrix_json


def test_triangular_snapshot_restores_exact_symmetric_matrix():
    source = sparse.csc_matrix([[2.0, 3.0], [3.0, -4.0]])
    record = dict(K=matrix_json(sparse.triu(source)), triangle="Triu")
    assert np.array_equal(k.matrix(record).toarray(), source.toarray())


def test_independent_solution_and_componentwise_residual():
    K = sparse.csc_matrix([[2.0, 1.0], [1.0, -3.0]])
    rhs = np.array([1.0, 5.0])
    lu = splu(K, **k.OPTIONS)
    x = lu.solve(rhs)
    report = k.metrics(K, rhs, x)
    assert report["residual_inf"] < 1e-14
    assert report["componentwise_backward_error"] < 1e-14
    bad = k.metrics(K, rhs, x + 1.0)
    assert bad["residual_inf"] > 1
    fixed, history, _ = k.refine(K, lu, rhs, x + 1.0)
    assert np.allclose(fixed, x)
    assert history[-1]["residual_inf"] < 1e-14


def test_high_precision_residual_survives_large_term_cancellation():
    K = sparse.csr_matrix([[1e16, 1.0, -1e16]])
    result = r.decimal_residual(K, [0.0], [1.0, 1.0, 1.0])
    assert float(result["residual_inf"]) == 1.0
    assert result["row"] == 0
