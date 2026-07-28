"""
Tests for build_opf with formulation='lossy_dc' (single time-step).

Reference: Convex Optimization with Smart Grid Examples,
https://doi.org/10.2172/3018252
"""

import warnings

import numpy as np
import pytest

from cvxopf.testcases import case9, case14
from cvxopf.problem import build_opf, build_opf_multistep, OPFBuild, OPFOptions
from cvxopf.results import extract_results


# ---------------------------------------------------------------------------
# Tolerances
# ---------------------------------------------------------------------------

FLOW_CONSERVATION_ATOL = 1e-4
BOUND_ATOL             = 1e-4


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _solve(case_fn, options=None):
    """Build and solve; return (build, results)."""
    build = build_opf(case_fn(), formulation="lossy_dc", options=options)
    build.solve()
    results = extract_results(build)
    return build, results


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------

class TestReturnType:

    def test_returns_opfbuild(self):
        build = build_opf(case9(), formulation="lossy_dc")
        assert isinstance(build, OPFBuild)

    def test_formulation_field_is_lossy_dc(self):
        build = build_opf(case9(), formulation="lossy_dc")
        assert build.formulation == "lossy_dc"

    def test_is_convex_is_true(self):
        build = build_opf(case9(), formulation="lossy_dc")
        assert build.is_convex is True

    def test_variables_has_expected_keys(self):
        build = build_opf(case9(), formulation="lossy_dc")
        assert set(build.variables.keys()) == {"p_flows", "Pg"}

    def test_variables_does_not_have_ac_keys(self):
        build = build_opf(case9(), formulation="lossy_dc")
        ac_keys = {"theta", "v", "P", "Q", "p", "q", "Qg"}
        assert ac_keys.isdisjoint(set(build.variables.keys()))

    def test_data_has_expected_keys(self):
        build = build_opf(case9(), formulation="lossy_dc")
        expected = {
            "baseMVA", "nb", "ng", "nl", "ext_to_int",
            "A", "Cg", "r", "f_max", "Pd", "gen_bus",
            "Pgmin", "Pgmax", "loss_weight",
        }
        assert expected.issubset(set(build.data.keys()))


# ---------------------------------------------------------------------------
# Solve defaults — convex formulation must use CLARABEL, not IPOPT
# ---------------------------------------------------------------------------

class TestSolveDefaults:

    def test_solve_status_optimal_case9(self):
        _, results = _solve(case9)
        assert results["status"] == "optimal"

    def test_solve_status_optimal_case14(self):
        _, results = _solve(case14)
        assert results["status"] == "optimal"

    def test_solve_uses_clarabel(self):
        """Verify CLARABEL is used by checking solver_stats after solve."""
        build = build_opf(case9(), formulation="lossy_dc")
        build.solve()
        assert "clarabel" in build.prob.solver_stats.solver_name.lower()

    def test_solve_does_not_require_nlp(self):
        """DC problem should solve without nlp=True."""
        build = build_opf(case9(), formulation="lossy_dc")
        # explicit nlp=False should not raise
        build.prob.solve(solver="CLARABEL", verbose=False)
        assert build.prob.status == "optimal"


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------

class TestObjective:

    def test_objective_is_positive_case9(self):
        _, results = _solve(case9)
        assert results["objective"] > 0

    def test_objective_is_positive_case14(self):
        _, results = _solve(case14)
        assert results["objective"] > 0

    def test_objective_is_float(self):
        _, results = _solve(case9)
        assert isinstance(results["objective"], float)

    def test_loss_weight_zero_reduces_objective(self):
        """
        With loss_weight=0, line losses are not penalised. The objective
        should be <= the loss_weight=1 case since the solver has more
        freedom.
        """
        _, r1 = _solve(case9, options=OPFOptions(loss_weight=1.0))
        _, r0 = _solve(case9, options=OPFOptions(loss_weight=0.0))
        assert r0["objective"] <= r1["objective"] + 1e-3


# ---------------------------------------------------------------------------
# Feasibility
# ---------------------------------------------------------------------------

class TestFeasibility:

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_Pg_within_bounds(self, case_fn):
        build, results = _solve(case_fn)
        Pg    = results["Pg"]
        Pgmin = build.data["Pgmin"] * build.data["baseMVA"]
        Pgmax = build.data["Pgmax"] * build.data["baseMVA"]
        assert np.all(Pg >= Pgmin - BOUND_ATOL), "Pg below Pgmin"
        assert np.all(Pg <= Pgmax + BOUND_ATOL), "Pg above Pgmax"

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_flow_conservation_satisfied(self, case_fn):
        """
        A @ p_flows + Cg @ Pg == Pd must hold at every bus.
        All quantities in p.u.
        """
        build, _ = _solve(case_fn)
        A        = build.data["A"]
        Cg       = build.data["Cg"]
        Pd       = build.data["Pd"]
        Pg       = build.variables["Pg"].value
        p_flows  = build.variables["p_flows"].value
        residual = A @ p_flows + Cg @ Pg - Pd
        assert np.allclose(residual, 0.0, atol=FLOW_CONSERVATION_ATOL), \
            f"Flow conservation violated; max residual: {np.abs(residual).max():.2e}"

    def test_branch_flows_within_limits_case9(self):
        build, results = _solve(case9)
        f_max = build.data["f_max"] * build.data["baseMVA"]
        assert np.all(np.abs(results["p_flows"]) <= f_max + BOUND_ATOL), \
            "Branch flow exceeds limit"


# ---------------------------------------------------------------------------
# Branch limit sentinel
# ---------------------------------------------------------------------------

class TestBranchLimitSentinel:

    def test_zero_rateA_emits_warning(self):
        """case9 branch 0 has rateA=250; force it to 0 to trigger warning."""
        ppc = case9()
        ppc["branch"] = ppc["branch"].copy()
        ppc["branch"][0, 5] = 0.0   # rateA = 0
        with pytest.warns(UserWarning, match="rateA=0"):
            build_opf(ppc, formulation="lossy_dc")

    def test_zero_rateA_uses_sentinel_value(self):
        """Sentinel should be substituted when rateA=0."""
        ppc = case9()
        ppc["branch"] = ppc["branch"].copy()
        ppc["branch"][0, 5] = 0.0
        sentinel = 500.0
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf(
                ppc, formulation="lossy_dc",
                options=OPFOptions(branch_limit_sentinel=sentinel)
            )
        baseMVA = build.data["baseMVA"]
        assert abs(build.data["f_max"][0] - sentinel / baseMVA) < 1e-10


# ---------------------------------------------------------------------------
# Results schema
# ---------------------------------------------------------------------------

class TestResults:

    def test_Pg_shape_is_ng_case9(self):
        _, results = _solve(case9)
        assert results["Pg"].shape == (3,)

    def test_Pg_shape_is_ng_case14(self):
        _, results = _solve(case14)
        assert results["Pg"].shape == (5,)

    def test_p_flows_shape_is_nl_case9(self):
        _, results = _solve(case9)
        assert results["p_flows"].shape == (9,)

    def test_p_flows_shape_is_nl_case14(self):
        _, results = _solve(case14)
        assert results["p_flows"].shape == (20,)

    def test_p_net_shape_is_nb_case9(self):
        _, results = _solve(case9)
        assert results["p_net"].shape == (9,)

    def test_Vm_absent_from_results(self):
        _, results = _solve(case9)
        assert "Vm" not in results

    def test_Qg_absent_from_results(self):
        _, results = _solve(case9)
        assert "Qg" not in results

    def test_Va_deg_absent_from_results(self):
        _, results = _solve(case9)
        assert "Va_deg" not in results

    def test_q_net_absent_from_results(self):
        _, results = _solve(case9)
        assert "q_net" not in results

    def test_status_key_present(self):
        _, results = _solve(case9)
        assert "status" in results

    def test_objective_key_present(self):
        _, results = _solve(case9)
        assert "objective" in results


# ---------------------------------------------------------------------------
# Loss weight
# ---------------------------------------------------------------------------

class TestLossWeight:

    def test_default_loss_weight_is_one(self):
        build = build_opf(case9(), formulation="lossy_dc")
        assert build.data["loss_weight"] == 1.0

    def test_higher_loss_weight_increases_objective(self):
        """
        With a higher penalty on losses the solver trades off more
        generation cost to reduce losses; total objective goes up.
        """
        _, r1   = _solve(case9, options=OPFOptions(loss_weight=1.0))
        _, r100 = _solve(case9, options=OPFOptions(loss_weight=100.0))
        assert r100["objective"] >= r1["objective"] - 1e-3


# ---------------------------------------------------------------------------
# Unknown formulation
# ---------------------------------------------------------------------------

class TestUnknownFormulation:

    def test_unknown_formulation_raises_valueerror(self):
        with pytest.raises(ValueError, match="Unknown formulation"):
            build_opf(case9(), formulation="banana")

    def test_unknown_formulation_multistep_raises_valueerror(self):
        import pandas as pd
        ppc  = case9()
        df_P = pd.DataFrame(np.zeros((1, 9)))
        df_Q = pd.DataFrame(np.zeros((1, 9)))
        with pytest.raises(ValueError, match="Unknown formulation"):
            build_opf_multistep(ppc, df_P, df_Q, T=1, formulation="banana")


# ---------------------------------------------------------------------------
# Deprecation warnings
# ---------------------------------------------------------------------------

class TestDeprecationWarning:

    def test_build_acopf_emits_deprecation_warning(self):
        from cvxopf.problem import build_acopf
        with pytest.warns(DeprecationWarning, match="build_acopf is deprecated"):
            build_acopf(case9())

    def test_build_acopf_multistep_emits_deprecation_warning(self):
        import pandas as pd
        from cvxopf.problem import build_acopf_multistep
        ppc  = case9()
        df_P = pd.DataFrame(np.tile(ppc["bus"][:, 2], (1, 1)))
        df_Q = pd.DataFrame(np.tile(ppc["bus"][:, 3], (1, 1)))
        with pytest.warns(DeprecationWarning, match="build_acopf_multistep is deprecated"):
            build_acopf_multistep(ppc, df_P, df_Q, T=1)
