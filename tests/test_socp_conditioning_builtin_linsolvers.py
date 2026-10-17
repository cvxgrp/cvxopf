"""Stock backend controls and payload invariance, without solves."""

import os
import pytest

from experiments.socp_conditioning import builtin_linsolvers as b


@pytest.mark.parametrize("method", b.METHODS)
def test_only_method_changes(method):
    assert b.options(method) == b.p.options("CLARABEL") | dict(
        direct_solve_method=method
    )


def test_payload_requires_exact_prepared_problem():
    old = dict(A=[1.0], settings=dict(direct_solve_method="auto", tol_gap_rel=1e-9))
    new = old | dict(settings=old["settings"] | dict(direct_solve_method="faer"))
    b.verify_payload(new, old, "faer")
    with pytest.raises(ValueError, match="more than"):
        b.verify_payload(new | dict(A=[2.0]), old, "faer")


def test_custom_engine_environment_cannot_leak(monkeypatch):
    monkeypatch.setenv("CVXOPF_LU_PYTHON", "wrong")
    monkeypatch.setenv("CVXOPF_SHARED_CONE", "1")
    with b.clean_environment():
        assert not any(k.startswith("CVXOPF_") for k in os.environ)
    assert os.environ["CVXOPF_LU_PYTHON"] == "wrong"
