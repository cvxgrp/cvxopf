"""
Tests for build_acopf (single time-step problem builder).
"""

import numpy as np
import pytest
import cvxpy as cp

from cvxopf.testcases import case9, case14
from cvxopf.problem import build_opf, OPFBuild, OPFOptions
from cvxopf.results import extract_results


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

OBJ_RTOL = 1e-4   # 0.01% relative tolerance on objective


# ---------------------------------------------------------------------------
# Known reference values from working code (see plan inputs)
# ---------------------------------------------------------------------------

CASE9_OBJ  = 5296.686203992538
CASE14_OBJ = 8081.526257050759


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _solve(case_fn, options=None):
    """Build and solve; return (build, results)."""
    build = build_opf(case_fn(), formulation="ac", options=options)
    build.solve()
    results = extract_results(build)
    return build, results


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:

    def test_returns_opfbuild(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac")
        assert isinstance(build, OPFBuild)

    def test_prob_is_cvxpy_problem(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac")
        assert isinstance(build.prob, cp.Problem)

    def test_variables_has_expected_keys(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac")
        expected = {"theta", "v", "P", "Q", "p", "q", "Pg", "Qg"}
        assert set(build.variables.keys()) == expected

    def test_data_has_expected_keys(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac")
        expected = {
            "baseMVA", "nb", "ng", "ref", "pv", "ext_to_int",
            "Ybus", "G", "B", "E", "Z", "Pd", "Qd", "Cg",
            "Pgmin", "Pgmax", "Qgmin", "Qgmax",
        }
        assert expected.issubset(set(build.data.keys()))

    def test_variables_are_cvxpy_variables(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac")
        for name, var in build.variables.items():
            assert isinstance(var, cp.Variable), \
                f"variables['{name}'] should be a cp.Variable"


# ---------------------------------------------------------------------------
# Variable shapes
# ---------------------------------------------------------------------------

class TestVariableShapes:

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_theta_shape(self, case_fn, nb, ng):
        build = build_opf(case_fn(), formulation="ac")
        assert build.variables["theta"].shape == (nb, 1)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_v_shape(self, case_fn, nb, ng):
        build = build_opf(case_fn(), formulation="ac")
        assert build.variables["v"].shape == (nb, 1)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_P_shape(self, case_fn, nb, ng):
        build = build_opf(case_fn(), formulation="ac")
        assert build.variables["P"].shape == (nb, nb)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Q_shape(self, case_fn, nb, ng):
        build = build_opf(case_fn(), formulation="ac")
        assert build.variables["Q"].shape == (nb, nb)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Pg_shape(self, case_fn, nb, ng):
        build = build_opf(case_fn(), formulation="ac")
        assert build.variables["Pg"].shape == (ng,)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Qg_shape(self, case_fn, nb, ng):
        build = build_opf(case_fn(), formulation="ac")
        assert build.variables["Qg"].shape == (ng,)


# ---------------------------------------------------------------------------
# Flat start initialisation
# ---------------------------------------------------------------------------

class TestFlatStart:

    def test_theta_initialised_to_zero(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac", options=OPFOptions(init_flat=True))
        np.testing.assert_array_equal(
            build.variables["theta"].value,
            np.zeros((9, 1))
        )

    def test_v_initialised_to_one(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac", options=OPFOptions(init_flat=True))
        np.testing.assert_array_equal(
            build.variables["v"].value,
            np.ones((9, 1))
        )

    def test_no_init_when_flat_false(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac", options=OPFOptions(init_flat=False))
        assert build.variables["theta"].value is None
        assert build.variables["v"].value is None


# ---------------------------------------------------------------------------
# Solve status
# ---------------------------------------------------------------------------

class TestSolveStatus:

    def test_case9_optimal(self):
        _, results = _solve(case9)
        assert results["status"] == "optimal"

    def test_case14_optimal(self):
        _, results = _solve(case14)
        assert results["status"] == "optimal"


# ---------------------------------------------------------------------------
# Objective value
# ---------------------------------------------------------------------------

class TestObjective:

    def test_case9_objective(self):
        _, results = _solve(case9)
        assert abs(results["objective"] - CASE9_OBJ) / CASE9_OBJ < OBJ_RTOL

    def test_case14_objective(self):
        _, results = _solve(case14)
        assert abs(results["objective"] - CASE14_OBJ) / CASE14_OBJ < OBJ_RTOL


# ---------------------------------------------------------------------------
# Feasibility checks
# ---------------------------------------------------------------------------

class TestFeasibility:

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_voltage_magnitudes_within_bounds(self, case_fn):
        build, results = _solve(case_fn)
        data  = build.data
        vmin  = data["Ybus"]   # not used; pull from case directly
        Vm    = results["Vm"]
        bus   = build.data
        # Vm bounds come from the variable bounds set in build_acopf
        v_var = build.variables["v"]
        lb    = v_var.attributes["bounds"][0].flatten()
        ub    = v_var.attributes["bounds"][1].flatten()
        assert np.all(Vm >= lb - 1e-6), "Vm below lower bound"
        assert np.all(Vm <= ub + 1e-6), "Vm above upper bound"

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_Pg_within_bounds(self, case_fn):
        build, results = _solve(case_fn)
        Pg    = results["Pg"]
        Pgmin = build.data["Pgmin"] * build.data["baseMVA"]
        Pgmax = build.data["Pgmax"] * build.data["baseMVA"]
        assert np.all(Pg >= Pgmin - 1e-4), "Pg below Pgmin"
        assert np.all(Pg <= Pgmax + 1e-4), "Pg above Pgmax"

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_Qg_within_bounds(self, case_fn):
        build, results = _solve(case_fn)
        Qg    = results["Qg"]
        Qgmin = build.data["Qgmin"] * build.data["baseMVA"]
        Qgmax = build.data["Qgmax"] * build.data["baseMVA"]
        assert np.all(Qg >= Qgmin - 1e-4), "Qg below Qgmin"
        assert np.all(Qg <= Qgmax + 1e-4), "Qg above Qgmax"

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_slack_bus_angle_is_zero(self, case_fn):
        build, results = _solve(case_fn)
        ref = build.data["ref"]
        assert abs(results["Va_deg"][ref]) < 1e-6, \
            "Slack bus angle should be zero"


# ---------------------------------------------------------------------------
# Warm-starting
# ---------------------------------------------------------------------------

class TestWarmStart:

    def test_custom_initial_point_does_not_break_solve(self, case9_raw):
        """Setting .value on variables before solve should not raise."""
        build = build_opf(case9_raw, formulation="ac", options=OPFOptions(init_flat=False))
        nb    = build.data["nb"]
        ng    = build.data["ng"]
        build.variables["theta"].value = np.zeros((nb, 1))
        build.variables["v"].value     = np.ones((nb, 1))
        build.variables["Pg"].value    = np.full(ng, 0.1)
        build.variables["Qg"].value    = np.zeros(ng)
        build.solve()
        results = extract_results(build)
        assert results["status"] == "optimal"


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

class TestOptions:

    def test_enforce_branch_limits_raises(self, case9_raw):
        with pytest.raises(NotImplementedError, match="enforce_branch_limits"):
            build_opf(case9_raw, formulation="ac", options=OPFOptions(enforce_branch_limits=True))

    def test_enforce_vset_does_not_raise(self, case9_raw):
        """enforce_vset=True should build without error."""
        build = build_opf(case9_raw, formulation="ac", options=OPFOptions(enforce_vset=True))
        assert isinstance(build, OPFBuild)

    def test_sparsity_tol_accepted(self, case9_raw):
        build = build_opf(case9_raw, formulation="ac", options=OPFOptions(sparsity_tol=1e-12))
        assert isinstance(build, OPFBuild)

    def test_default_options_when_none_passed(self, case9_raw):
        """Passing options=None should use OPFOptions defaults."""
        build = build_opf(case9_raw, formulation="ac", options=None)
        assert isinstance(build, OPFBuild)
