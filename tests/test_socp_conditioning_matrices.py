"""Known sparse spectra and diagnostics, without an optimization solver."""

import numpy as np
from scipy import sparse

from experiments.socp_conditioning.matrix_conditioning import (
    describe,
    distribution,
    equilibrate,
    factorized_spectrum,
    spectrum,
)


def test_diagonal_known_condition_and_equilibration():
    A = sparse.diags([1.0, 10.0, 100.0], format="csr")
    result = spectrum(A)
    assert result["status"] == "estimated_not_certified"
    assert np.isclose(result["condition_2"], 100.0)
    assert result["singular_triplet_residual"] < 1e-9
    assert np.isclose(spectrum(equilibrate(A))["condition_2"], 1.0)


def test_zero_column_and_opposite_rows_are_reported():
    result = describe(sparse.csr_matrix([[1.0, 0.0], [-1.0, 0.0], [0.0, 0.0]]))
    assert result["column_norms"]["zeros"] == 1
    assert result["row_norms"]["zeros"] == 1
    assert result["duplicate_or_opposite_rows"] == 1
    assert result["raw_spectrum"]["condition_2"] == "infinite"


def test_zero_objective_is_not_assigned_a_finite_condition():
    stats = distribution([0.0, 0.0])
    assert stats["nonzero_spread"] is None
    assert stats["zeros"] == 2


def test_spectral_budget_is_checked_during_matrix_products(monkeypatch):
    from experiments.socp_conditioning import matrix_conditioning as m

    monkeypatch.setattr(m, "SPECTRAL_SECONDS", -1.0)
    result = spectrum(sparse.eye(20, format="csr"))
    assert result["status"] == "estimate_unavailable"
    assert result["matrix_products"] == 1
    assert "budget" in result["reason"]


def test_factorized_spectrum_and_transpose_agree():
    A = sparse.csr_matrix([[1.0, 0.0], [0.0, 100.0], [0.0, 0.0]])
    for matrix in (A, A.T):
        result = factorized_spectrum(matrix)
        assert result["status"] == "estimated_not_certified"
        assert np.isclose(result["condition_2"], 100.0)
        assert result["singular_triplet_residual"] < 1e-8


def test_factorized_spectrum_reports_structural_deficiency():
    result = factorized_spectrum(sparse.csr_matrix([[1.0, 0.0], [1.0, 0.0]]))
    assert result["condition_2"] == "infinite"
