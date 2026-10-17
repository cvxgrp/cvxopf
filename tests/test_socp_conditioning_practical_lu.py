"""Practical tolerance controls; no OPF solves."""

import os
import pytest

from experiments.socp_conditioning import practical_lu as p
from experiments.socp_conditioning.practical_lu_analysis import convergence


def test_only_full_relative_gap_changes():
    base = p.lu.m.options("CLARABEL")
    assert p.options("CLARABEL") == base | dict(tol_gap_rel=1e-9)
    assert p.options("CLARABEL")["tol_feas"] == 1e-10
    assert p.options("CLARABEL")["reduced_tol_gap_rel"] == 1e-10


def test_exact_input_gate_rejects_undeclared_change():
    old = dict(A=[1], settings=dict(tol_gap_rel=1e-10, tol_feas=1e-10))
    new = old | dict(settings=old["settings"] | dict(tol_gap_rel=1e-9))
    p.compare_input(new, old)
    with pytest.raises(ValueError, match="more than"):
        p.compare_input(new | dict(A=[2]), old)


def test_only_lu_switch_survives(monkeypatch):
    monkeypatch.setenv("CVXOPF_SHARED_CONE", "1")
    monkeypatch.setenv("CVXOPF_COMPENSATED_CONE", "1")
    monkeypatch.setenv("CVXOPF_KKT_SCALING", "5")
    with p.native_environment():
        assert {k for k in os.environ if k.startswith("CVXOPF_")} == {
            "CVXOPF_LU_PYTHON"
        }
    assert os.environ["CVXOPF_SHARED_CONE"] == "1"


def test_relative_gap_is_sufficient_but_feasibility_still_required():
    info = dict(
        gap_abs=2.1e-9,
        gap_rel=6.74e-10,
        res_primal=3e-14,
        res_dual=6e-16,
        ktratio=2e-14,
    )
    settings = p.options("CLARABEL")
    assert convergence(info, settings)
    assert not convergence(info, settings | dict(tol_gap_rel=1e-10))
    assert not convergence(info | dict(res_primal=2e-10), settings)
    assert not convergence(info | dict(gap_rel=float("nan")), settings)
