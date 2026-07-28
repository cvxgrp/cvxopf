"""
Tests for build_acopf_multistep (multi time-step problem builder).
"""

import numpy as np
import pandas as pd
import pytest
import cvxpy as cp

from cvxopf.testcases import case9
from cvxopf.problem import build_opf, build_opf_multistep, OPFBuild, OPFOptions
from cvxopf.results import extract_results


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

OBJ_RTOL  = 1e-4   # 0.01% relative tolerance on objective
VAL_ATOL  = 1e-3   # absolute tolerance for per-step value comparisons


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_load_dfs(case_fn, T):
    """
    Return (df_P, df_Q) with T identical rows matching the base case load.
    """
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q = pd.DataFrame(np.tile(Qd_base, (T, 1)))
    return df_P, df_Q


def _solve_multistep(case_fn, T, df_P=None, df_Q=None, options=None,
                     coupling_constraints=None):
    if df_P is None or df_Q is None:
        df_P, df_Q = _flat_load_dfs(case_fn, T)
    build = build_opf_multistep(
        case_fn(), df_P, df_Q, T=T, options=options,
        coupling_constraints=coupling_constraints,
    )
    build.solve()
    results = extract_results(build)
    return build, results


def _solve_single(case_fn, options=None):
    build = build_opf(case_fn(), formulation="ac", options=options)
    build.solve()
    results = extract_results(build)
    return build, results


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:

    def test_returns_opfbuild(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build = build_opf_multistep(case9(), df_P, df_Q, T=3)
        assert isinstance(build, OPFBuild)

    def test_prob_is_cvxpy_problem(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build = build_opf_multistep(case9(), df_P, df_Q, T=3)
        assert isinstance(build.prob, cp.Problem)

    def test_variables_has_expected_keys(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        # sparse_pq=True (default): P_vec/Q_vec instead of P/Q
        build = build_opf_multistep(case9(), df_P, df_Q, T=3)
        expected = {"theta", "v", "P_vec", "Q_vec", "p", "q", "Pg", "Qg"}
        assert set(build.variables.keys()) == expected

    def test_variables_has_expected_keys_dense(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        # sparse_pq=False: legacy P/Q keys
        build = build_opf_multistep(case9(), df_P, df_Q, T=3,
                                    options=OPFOptions(sparse_pq=False))
        expected = {"theta", "v", "P", "Q", "p", "q", "Pg", "Qg"}
        assert set(build.variables.keys()) == expected

    def test_variable_lists_have_length_T(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        T     = 3
        # sparse_pq=True (default): P_vec/Q_vec
        build = build_opf_multistep(case9(), df_P, df_Q, T=T)
        for key in ("theta", "v", "P_vec", "Q_vec", "p", "q", "Pg", "Qg"):
            assert isinstance(build.variables[key], list)
            assert len(build.variables[key]) == T, \
                f"variables['{key}'] should have length T={T}"

    def test_variable_lists_have_length_T_dense(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        T     = 3
        # sparse_pq=False: P/Q
        build = build_opf_multistep(case9(), df_P, df_Q, T=T,
                                    options=OPFOptions(sparse_pq=False))
        for key in ("theta", "v", "P", "Q", "p", "q", "Pg", "Qg"):
            assert isinstance(build.variables[key], list)
            assert len(build.variables[key]) == T, \
                f"variables['{key}'] should have length T={T}"

    def test_data_contains_T(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build = build_opf_multistep(case9(), df_P, df_Q, T=3)
        assert build.data["T"] == 3

    def test_data_contains_Pd_series(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build = build_opf_multistep(case9(), df_P, df_Q, T=3)
        assert "Pd_series" in build.data
        assert build.data["Pd_series"].shape == (3, 9)

    def test_data_contains_Qd_series(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build = build_opf_multistep(case9(), df_P, df_Q, T=3)
        assert "Qd_series" in build.data
        assert build.data["Qd_series"].shape == (3, 9)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:

    def test_T_mismatch_raises(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with pytest.raises(ValueError, match="T=5"):
            build_opf_multistep(case9(), df_P, df_Q, T=5)

    def test_wrong_nb_columns_raises(self):
        ppc  = case9()
        df_P = pd.DataFrame(np.zeros((3, 5)))   # wrong number of columns
        df_Q = pd.DataFrame(np.zeros((3, 5)))
        with pytest.raises(ValueError, match="columns"):
            build_opf_multistep(ppc, df_P, df_Q, T=3)

    def test_mismatched_df_rows_raises(self):
        ppc  = case9()
        df_P = pd.DataFrame(np.zeros((3, 9)))
        df_Q = pd.DataFrame(np.zeros((4, 9)))   # different number of rows
        with pytest.raises(ValueError, match="rows"):
            build_opf_multistep(ppc, df_P, df_Q, T=3)

    def test_enforce_branch_limits_raises(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        with pytest.raises(NotImplementedError, match="enforce_branch_limits"):
            build_opf_multistep(
                case9(), df_P, df_Q, T=3,
                options=OPFOptions(enforce_branch_limits=True),
            )


# ---------------------------------------------------------------------------
# T=1 equivalence with single-step
# ---------------------------------------------------------------------------

class TestT1Equivalence:

    def test_objective_matches_single_step(self):
        _, r_single = _solve_single(case9)
        _, r_multi  = _solve_multistep(case9, T=1)
        assert abs(r_multi["objective"] - r_single["objective"]) \
               / abs(r_single["objective"]) < OBJ_RTOL

    def test_Pg_matches_single_step(self):
        _, r_single = _solve_single(case9)
        _, r_multi  = _solve_multistep(case9, T=1)
        # multi Pg is (1, ng); single Pg is (ng,)
        np.testing.assert_allclose(
            r_multi["Pg"][0], r_single["Pg"], atol=VAL_ATOL
        )

    def test_Vm_matches_single_step(self):
        _, r_single = _solve_single(case9)
        _, r_multi  = _solve_multistep(case9, T=1)
        np.testing.assert_allclose(
            r_multi["Vm"][0], r_single["Vm"], atol=VAL_ATOL
        )

    def test_Va_matches_single_step(self):
        _, r_single = _solve_single(case9)
        _, r_multi  = _solve_multistep(case9, T=1)
        np.testing.assert_allclose(
            r_multi["Va_deg"][0], r_single["Va_deg"], atol=VAL_ATOL
        )


# ---------------------------------------------------------------------------
# Flat load: per-step solutions should be identical
# ---------------------------------------------------------------------------

class TestFlatLoad:

    def test_per_step_objectives_equal(self):
        """
        With flat (identical) load across T=3 steps, each step's cost
        should equal the single-step cost.
        """
        T           = 3
        _, r_single = _solve_single(case9)
        _, r_multi  = _solve_multistep(case9, T=T)
        # Total objective should be T * single-step objective
        expected_total = T * r_single["objective"]
        assert abs(r_multi["objective"] - expected_total) \
               / abs(expected_total) < OBJ_RTOL

    def test_per_step_Pg_equal_across_steps(self):
        T          = 3
        _, r_multi = _solve_multistep(case9, T=T)
        Pg         = r_multi["Pg"]   # (T, ng)
        for t in range(1, T):
            np.testing.assert_allclose(Pg[t], Pg[0], atol=VAL_ATOL)

    def test_per_step_Vm_equal_across_steps(self):
        T          = 3
        _, r_multi = _solve_multistep(case9, T=T)
        Vm         = r_multi["Vm"]   # (T, nb)
        for t in range(1, T):
            np.testing.assert_allclose(Vm[t], Vm[0], atol=VAL_ATOL)


# ---------------------------------------------------------------------------
# Varying load: per-step solutions should differ
# ---------------------------------------------------------------------------

class TestVaryingLoad:

    def test_per_step_Pg_distinct(self, case9_multistep_load):
        """With 80/100/120% load scaling, per-step Pg should differ."""
        df_P, df_Q = case9_multistep_load
        _, r_multi = _solve_multistep(case9, T=3, df_P=df_P, df_Q=df_Q)
        Pg = r_multi["Pg"]   # (3, ng)
        # Step 0 (light load) and step 2 (heavy load) should differ
        assert not np.allclose(Pg[0], Pg[2], atol=VAL_ATOL), \
            "80% and 120% load steps should produce different Pg"

    def test_per_step_objectives_distinct(self, case9_multistep_load):
        """Lighter load should have lower cost than heavier load."""
        df_P, df_Q  = case9_multistep_load
        _, r_single_80  = _solve_single(
            case9,
            options=OPFOptions(),
        )
        # Build individual single-step problems for reference costs
        ppc     = case9()
        ppc80   = {**ppc, "bus": ppc["bus"].copy()}
        ppc80["bus"][:, 2] = ppc["bus"][:, 2] * 0.8
        ppc80["bus"][:, 3] = ppc["bus"][:, 3] * 0.8
        ppc120  = {**ppc, "bus": ppc["bus"].copy()}
        ppc120["bus"][:, 2] = ppc["bus"][:, 2] * 1.2
        ppc120["bus"][:, 3] = ppc["bus"][:, 3] * 1.2

        b80  = build_opf(ppc80, formulation="ac")
        b80.prob.solve(solver=cp.IPOPT, nlp=True)
        r80  = extract_results(b80)

        b120 = build_opf(ppc120, formulation="ac")
        b120.prob.solve(solver=cp.IPOPT, nlp=True)
        r120 = extract_results(b120)

        assert r80["objective"] < r120["objective"], \
            "80% load should cost less than 120% load"


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------

class TestResultShapes:

    @pytest.mark.parametrize("T", [1, 3])
    def test_Pg_shape(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert r["Pg"].shape == (T, 3)

    @pytest.mark.parametrize("T", [1, 3])
    def test_Qg_shape(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert r["Qg"].shape == (T, 3)

    @pytest.mark.parametrize("T", [1, 3])
    def test_Vm_shape(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert r["Vm"].shape == (T, 9)

    @pytest.mark.parametrize("T", [1, 3])
    def test_Va_deg_shape(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert r["Va_deg"].shape == (T, 9)

    @pytest.mark.parametrize("T", [1, 3])
    def test_p_net_shape(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert r["p_net"].shape == (T, 9)


# ---------------------------------------------------------------------------
# Coupling constraints hook
# ---------------------------------------------------------------------------

class TestCouplingConstraints:

    def test_empty_coupling_constraints_accepted(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build = build_opf_multistep(
            case9(), df_P, df_Q, T=3,
            coupling_constraints=[],
        )
        assert isinstance(build, OPFBuild)

    def test_none_coupling_constraints_accepted(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build = build_opf_multistep(
            case9(), df_P, df_Q, T=3,
            coupling_constraints=None,
        )
        assert isinstance(build, OPFBuild)

    def test_simple_coupling_constraint_applied(self, case9_multistep_load):
        """
        Add a trivial coupling constraint linking Pg[0] and Pg[1] and
        verify the problem still solves to optimal.
        """
        df_P, df_Q = case9_multistep_load
        build = build_opf_multistep(
            case9(), df_P, df_Q, T=3,
        )
        # Pg[0][0] == Pg[1][0]: first generator output equal in steps 0 and 1
        coupling = [
            build.variables["Pg"][0][0] == build.variables["Pg"][1][0]
        ]
        build2 = build_opf_multistep(
            case9(), df_P, df_Q, T=3,
            coupling_constraints=coupling,
        )
        build2.prob.solve(solver=cp.IPOPT, nlp=True)
        results = extract_results(build2)
        assert results["status"] == "optimal"
