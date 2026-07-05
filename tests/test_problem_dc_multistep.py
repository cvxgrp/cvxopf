"""
Tests for build_opf_multistep with formulation='lossy_dc'.

Reference: Convex Optimization with Smart Grid Examples,
https://doi.org/10.2172/3018252
"""

import warnings

import numpy as np
import pandas as pd
import pytest

from cvxopf.testcases import case9, case14
from cvxopf.problem import build_opf, build_opf_multistep, OPFBuild, OPFOptions
from cvxopf.results import extract_results


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

OBJ_RTOL = 1e-4
VAL_ATOL = 1e-3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_load_dfs(case_fn, T):
    """Return (df_P, df_Q) with T identical rows matching base case load."""
    ppc     = case_fn()
    Pd_base = ppc["bus"][:, 2].copy()
    Qd_base = ppc["bus"][:, 3].copy()
    df_P    = pd.DataFrame(np.tile(Pd_base, (T, 1)))
    df_Q    = pd.DataFrame(np.tile(Qd_base, (T, 1)))
    return df_P, df_Q


def _solve_multistep(case_fn, T, df_P=None, df_Q=None, options=None,
                     coupling_constraints=None):
    if df_P is None or df_Q is None:
        df_P, df_Q = _flat_load_dfs(case_fn, T)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case_fn(), df_P, df_Q, T=T,
            formulation="lossy_dc", options=options,
            coupling_constraints=coupling_constraints,
        )
    build.solve()
    results = extract_results(build)
    return build, results


def _solve_single(case_fn, options=None):
    build = build_opf(case_fn(), formulation="lossy_dc", options=options)
    build.solve()
    results = extract_results(build)
    return build, results


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:

    def test_returns_opfbuild(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3, formulation="lossy_dc"
            )
        assert isinstance(build, OPFBuild)

    def test_formulation_field_is_lossy_dc(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3, formulation="lossy_dc"
            )
        assert build.formulation == "lossy_dc"

    def test_is_convex_is_true(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3, formulation="lossy_dc"
            )
        assert build.is_convex is True

    def test_variable_lists_have_length_T(self):
        T          = 3
        df_P, df_Q = _flat_load_dfs(case9, T)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=T, formulation="lossy_dc"
            )
        for key in ("p_flows", "p_gen"):
            assert isinstance(build.variables[key], list)
            assert len(build.variables[key]) == T, \
                f"variables['{key}'] should have length T={T}"

    def test_data_contains_T(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3, formulation="lossy_dc"
            )
        assert build.data["T"] == 3

    def test_data_contains_Pd_series(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3, formulation="lossy_dc"
            )
        assert "Pd_series" in build.data
        assert build.data["Pd_series"].shape == (3, 9)


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:

    def test_T_mismatch_raises(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with pytest.raises(ValueError, match="T=5"):
                build_opf_multistep(
                    case9(), df_P, df_Q, T=5, formulation="lossy_dc"
                )

    def test_wrong_nb_columns_raises(self):
        ppc  = case9()
        df_P = pd.DataFrame(np.zeros((3, 5)))   # wrong number of columns
        df_Q = pd.DataFrame(np.zeros((3, 5)))
        with pytest.raises(ValueError, match="columns"):
            build_opf_multistep(
                ppc, df_P, df_Q, T=3, formulation="lossy_dc"
            )

    def test_df_Q_ignored_with_warning(self):
        """df_Q should be accepted but trigger a UserWarning."""
        df_P, df_Q = _flat_load_dfs(case9, 1)
        with pytest.warns(UserWarning, match="df_Q is ignored"):
            build_opf_multistep(
                case9(), df_P, df_Q, T=1, formulation="lossy_dc"
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
        np.testing.assert_allclose(
            r_multi["Pg"][0], r_single["Pg"], atol=VAL_ATOL
        )

    def test_p_flows_matches_single_step(self):
        _, r_single = _solve_single(case9)
        _, r_multi  = _solve_multistep(case9, T=1)
        np.testing.assert_allclose(
            r_multi["p_flows"][0], r_single["p_flows"], atol=VAL_ATOL
        )


# ---------------------------------------------------------------------------
# Flat load: per-step solutions should be identical
# ---------------------------------------------------------------------------

class TestFlatLoad:

    def test_per_step_objectives_equal(self):
        """Total objective should be T * single-step objective."""
        T           = 3
        _, r_single = _solve_single(case9)
        _, r_multi  = _solve_multistep(case9, T=T)
        expected    = T * r_single["objective"]
        assert abs(r_multi["objective"] - expected) \
               / abs(expected) < OBJ_RTOL

    def test_per_step_Pg_equal_across_steps(self):
        T          = 3
        _, r_multi = _solve_multistep(case9, T=T)
        Pg         = r_multi["Pg"]   # (T, ng)
        for t in range(1, T):
            np.testing.assert_allclose(Pg[t], Pg[0], atol=VAL_ATOL)

    def test_per_step_p_flows_equal_across_steps(self):
        T          = 3
        _, r_multi = _solve_multistep(case9, T=T)
        p_flows    = r_multi["p_flows"]   # (T, nl)
        for t in range(1, T):
            np.testing.assert_allclose(p_flows[t], p_flows[0], atol=VAL_ATOL)


# ---------------------------------------------------------------------------
# Varying load: per-step solutions should differ
# ---------------------------------------------------------------------------

class TestVaryingLoad:

    def test_per_step_Pg_distinct(self):
        """With 80/100/120% load scaling, per-step Pg should differ."""
        ppc     = case9()
        Pd_base = ppc["bus"][:, 2].copy()
        Qd_base = ppc["bus"][:, 3].copy()
        scales  = [0.8, 1.0, 1.2]
        df_P    = pd.DataFrame(np.outer(scales, Pd_base))
        df_Q    = pd.DataFrame(np.outer(scales, Qd_base))

        _, r_multi = _solve_multistep(case9, T=3, df_P=df_P, df_Q=df_Q)
        Pg         = r_multi["Pg"]   # (3, ng)
        assert not np.allclose(Pg[0], Pg[2], atol=VAL_ATOL), \
            "80% and 120% load steps should produce different Pg"


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------

class TestResultShapes:

    @pytest.mark.parametrize("T", [1, 3])
    def test_Pg_shape(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert r["Pg"].shape == (T, 3)

    @pytest.mark.parametrize("T", [1, 3])
    def test_p_flows_shape(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert r["p_flows"].shape == (T, 9)

    @pytest.mark.parametrize("T", [1, 3])
    def test_p_net_shape(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert r["p_net"].shape == (T, 9)

    @pytest.mark.parametrize("T", [1, 3])
    def test_Vm_absent(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert "Vm" not in r

    @pytest.mark.parametrize("T", [1, 3])
    def test_Qg_absent(self, T):
        _, r = _solve_multistep(case9, T=T)
        assert "Qg" not in r


# ---------------------------------------------------------------------------
# Coupling constraints hook
# ---------------------------------------------------------------------------

class TestCouplingConstraints:

    def test_empty_coupling_constraints_accepted(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3,
                formulation="lossy_dc",
                coupling_constraints=[],
            )
        assert isinstance(build, OPFBuild)

    def test_none_coupling_constraints_accepted(self):
        df_P, df_Q = _flat_load_dfs(case9, 3)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(), df_P, df_Q, T=3,
                formulation="lossy_dc",
                coupling_constraints=None,
            )
        assert isinstance(build, OPFBuild)
