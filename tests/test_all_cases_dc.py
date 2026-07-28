"""
Smoke tests for lossy DC OPF across all built-in test cases.

Verifies that each case solves to optimality with CLARABEL and that
basic result shapes are correct. These tests do not validate numerical
accuracy — they confirm that the problem builds, solves, and returns
well-formed results for every supported case.

Note: There is no Pypower oracle for DC validation. Correctness is
verified via internal consistency checks in test_problem_dc.py and
test_problem_dc_multistep.py.
"""

import warnings

import numpy as np
import pytest

from cvxopf.testcases import case9, case14, case30, case39, case57, case118
from cvxopf.problem import build_opf
from cvxopf.results import extract_results


# ---------------------------------------------------------------------------
# Parametrize over all cases
# ---------------------------------------------------------------------------

ALL_CASES = [
    ("case9",   case9,   9,   3,   9),
    ("case14",  case14,  14,  5,   20),
    ("case30",  case30,  30,  6,   41),
    ("case39",  case39,  39,  10,  46),
    ("case57",  case57,  57,  7,   80),
    ("case118", case118, 118, 54, 186),
]


def _build_and_solve(case_fn):
    """Build and solve lossy DC OPF; return (build, results)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf(case_fn(), formulation="lossy_dc")
    build.solve()
    results = extract_results(build)
    return build, results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAllCasesDC:

    @pytest.mark.parametrize("name,case_fn,nb,ng,nl", ALL_CASES)
    def test_solves_to_optimal(self, name, case_fn, nb, ng, nl):
        _, r = _build_and_solve(case_fn)
        assert r["status"] == "optimal", \
            f"{name}: expected optimal, got {r['status']}"

    @pytest.mark.parametrize("name,case_fn,nb,ng,nl", ALL_CASES)
    def test_objective_is_positive_finite(self, name, case_fn, nb, ng, nl):
        _, r = _build_and_solve(case_fn)
        assert np.isfinite(r["objective"]), \
            f"{name}: objective is not finite"
        assert r["objective"] > 0, \
            f"{name}: objective should be positive"

    @pytest.mark.parametrize("name,case_fn,nb,ng,nl", ALL_CASES)
    def test_Pg_shape(self, name, case_fn, nb, ng, nl):
        _, r = _build_and_solve(case_fn)
        assert r["Pg"].shape == (ng,), \
            f"{name}: expected Pg shape ({ng},), got {r['Pg'].shape}"

    @pytest.mark.parametrize("name,case_fn,nb,ng,nl", ALL_CASES)
    def test_p_flows_shape(self, name, case_fn, nb, ng, nl):
        _, r = _build_and_solve(case_fn)
        assert r["p_flows"].shape == (nl,), \
            f"{name}: expected p_flows shape ({nl},), got {r['p_flows'].shape}"

    @pytest.mark.parametrize("name,case_fn,nb,ng,nl", ALL_CASES)
    def test_p_net_shape(self, name, case_fn, nb, ng, nl):
        _, r = _build_and_solve(case_fn)
        assert r["p_net"].shape == (nb,), \
            f"{name}: expected p_net shape ({nb},), got {r['p_net'].shape}"

    @pytest.mark.parametrize("name,case_fn,nb,ng,nl", ALL_CASES)
    def test_flow_conservation(self, name, case_fn, nb, ng, nl):
        """A @ p_flows + Cg @ Pg == Pd at every bus (p.u.)."""
        build, _ = _build_and_solve(case_fn)
        A       = build.data["A"]
        Cg      = build.data["Cg"]
        Pd      = build.data["Pd"]
        Pg      = build.variables["Pg"].value
        p_flows = build.variables["p_flows"].value
        residual = A @ p_flows + Cg @ Pg - Pd
        assert np.allclose(residual, 0.0, atol=1e-4), \
            f"{name}: flow conservation violated; " \
            f"max residual={np.abs(residual).max():.2e}"

    @pytest.mark.parametrize("name,case_fn,nb,ng,nl", ALL_CASES)
    def test_network_dimensions(self, name, case_fn, nb, ng, nl):
        """Confirm the case loads with the expected dimensions."""
        ppc = case_fn()
        assert ppc["bus"].shape[0]    == nb
        assert ppc["gen"].shape[0]    == ng
        assert ppc["branch"].shape[0] == nl
