"""Solver-free reconstruction/diagnostic-policy checks."""

from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse

from experiments.socp_conditioning.termination_probe import (
    exact_matrices,
    options,
    replay_problem,
    dual_vector,
    conic_objective,
)


def fixture():
    rng = np.random.default_rng(44)
    return dict(
        A=sparse.csc_matrix(rng.normal(size=(19, 4))),
        P=sparse.diags([1.0, 2.0, 3.0, 4.0]).tocsc(),
        b=rng.normal(size=19),
        c=rng.normal(size=4),
    )


def test_replay_preserves_quadratic_soc_matrices_and_consecutive_cones():
    data = fixture()
    prob, _ = replay_problem(data, (2, 3, [3, 3, 5, 3]))
    canonical, _, _ = prob.get_problem_data("CLARABEL", canon_backend="SCIPY")
    exact_matrices(data, canonical)
    assert canonical["dims"].soc == [3, 3, 5, 3]
    assert dual_vector(prob) is None


def test_replay_rejects_incomplete_layout():
    with pytest.raises(ValueError, match="incomplete"):
        replay_problem(fixture(), (2, 3, [3]))


def test_conic_objective_matches_quadratic_without_dense_factorization():
    data = fixture()
    prob, y = replay_problem(data, (2, 3, [3, 3, 5, 3]))
    y.value = np.array([1.0, -2.0, 0.5, 4.0])
    assert conic_objective(data, y).value == pytest.approx(prob.objective.value)
    data["P"] = sparse.csc_matrix(
        [[1, 0.1, 0, 0], [0.1, 2, 0, 0], [0, 0, 3, 0], [0, 0, 0, 4]]
    )
    with pytest.raises(ValueError, match="diagonal"):
        conic_objective(data, y)


@pytest.mark.parametrize("key", ["A", "P", "b", "c"])
def test_matrix_drift_is_detected(key):
    data = fixture()
    with pytest.raises((ValueError, AssertionError)):
        exact_matrices(data, data | {key: data[key] * 2})


def test_diagnostic_stopping_changes_do_not_relax_full_tolerances():
    opt = options("CLARABEL")
    for key in ("tol_gap_abs", "tol_gap_rel", "tol_feas"):
        assert opt[key] == opt["reduced_" + key] == 1e-10
    assert options("COPT")["FeasTol"] == 1e-9
    assert options("COPT")["reoptimize"] is False
    assert options("MOSEK")["mosek_params"]["MSK_IPAR_NUM_THREADS"] == 1
    with pytest.raises(ValueError):
        options("unknown")


def test_dual_reconstruction_retains_soc_coordinate_order():
    import cvxpy as cp

    y = cp.Variable(4)
    constraints = [
        y[:2] == 0,
        y[2:] <= 1,
        cp.SOC(y[:2], cp.reshape(y, (2, 2), order="F")),
    ]
    constraints[0].save_dual_value(np.array([1.0, 2.0]))
    constraints[1].save_dual_value(np.array([3.0, 4.0]))
    constraints[2].save_dual_value(np.arange(6.0))
    result = dual_vector(SimpleNamespace(constraints=constraints))
    np.testing.assert_array_equal(result, np.r_[1.0, 2.0, 3.0, 4.0, np.arange(6.0)])


def test_residual_only_audit_does_not_relabel_rejected_solver():
    from experiments.socp_conditioning.termination_analysis import numerical_checks

    audit = dict(passed=False, residuals=dict(balance=1e-8), limits=dict(balance=1e-4))
    check = numerical_checks(audit)
    assert check["residual_checks_passed"] is True
    assert check["status_inclusive_audit_passed"] is False
    assert audit["passed"] is False
    audit["residuals"]["balance"] = 2e-3
    assert numerical_checks(audit)["residual_checks_passed"] is False
    audit["residuals"]["balance"] = float("nan")
    assert numerical_checks(audit)["residual_checks_passed"] is False
