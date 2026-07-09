"""
Tests for src/cvxopf/results.py
"""

import numpy as np
import pytest
import cvxpy as cp

from cvxopf.testcases import case9, case14
from cvxopf.problem import build_opf, build_opf_multistep, OPFOptions, OPFBuild
from cvxopf.results import extract_results, compare_to_reference


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _solved_build(case_fn, options=None):
    build = build_opf(case_fn(), formulation="ac", options=options)
    build.solve()
    return build


def _solved_multistep_build(case_fn, T, df_P, df_Q, options=None):
    build = build_opf_multistep(
        case_fn(), df_P, df_Q, T=T, options=options
    )
    build.solve()
    return build


# ---------------------------------------------------------------------------
# Single-step: result dict structure
# ---------------------------------------------------------------------------

class TestSingleStepStructure:

    def test_all_expected_keys_present(self, case9_raw):
        build   = _solved_build(case9)
        results = extract_results(build)
        expected = {"status", "objective", "Pg", "Qg", "Vm", "Va_deg",
                    "p_net", "q_net"}
        assert expected.issubset(set(results.keys()))

    def test_status_is_string(self, case9_raw):
        results = extract_results(_solved_build(case9))
        assert isinstance(results["status"], str)

    def test_objective_is_float(self, case9_raw):
        results = extract_results(_solved_build(case9))
        assert isinstance(results["objective"], float)

    def test_objective_is_positive(self, case9_raw):
        results = extract_results(_solved_build(case9))
        assert results["objective"] > 0

    def test_status_is_optimal(self, case9_raw):
        results = extract_results(_solved_build(case9))
        assert results["status"] == "optimal"


# ---------------------------------------------------------------------------
# Single-step: array shapes
# ---------------------------------------------------------------------------

class TestSingleStepShapes:

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Pg_shape(self, case_fn, nb, ng):
        results = extract_results(_solved_build(case_fn))
        assert results["Pg"].shape == (ng,)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Qg_shape(self, case_fn, nb, ng):
        results = extract_results(_solved_build(case_fn))
        assert results["Qg"].shape == (ng,)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Vm_shape(self, case_fn, nb, ng):
        results = extract_results(_solved_build(case_fn))
        assert results["Vm"].shape == (nb,)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_Va_deg_shape(self, case_fn, nb, ng):
        results = extract_results(_solved_build(case_fn))
        assert results["Va_deg"].shape == (nb,)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_p_net_shape(self, case_fn, nb, ng):
        results = extract_results(_solved_build(case_fn))
        assert results["p_net"].shape == (nb,)

    @pytest.mark.parametrize("case_fn,nb,ng", [(case9, 9, 3), (case14, 14, 5)])
    def test_q_net_shape(self, case_fn, nb, ng):
        results = extract_results(_solved_build(case_fn))
        assert results["q_net"].shape == (nb,)


# ---------------------------------------------------------------------------
# Single-step: units
# ---------------------------------------------------------------------------

class TestSingleStepUnits:

    def test_Pg_is_in_MW_not_pu(self):
        """
        case9 has baseMVA=100. Pg in p.u. would be ~0.9-1.3.
        In MW the values should be ~90-135.
        """
        results = extract_results(_solved_build(case9))
        assert results["Pg"].max() > 10.0, \
            "Pg should be in MW (> 10), not p.u. (< 2)"

    def test_Qg_is_in_MVAr_not_pu(self):
        results = extract_results(_solved_build(case9))
        # At least one Qg magnitude should exceed 1 MVAr
        assert np.abs(results["Qg"]).max() > 1.0, \
            "Qg should be in MVAr, not p.u."

    def test_Pg_sum_approximately_equals_total_load_plus_losses(self):
        """
        For case9: total load is 315 MW, losses ~3 MW.
        Total Pg should be in [315, 325] MW.
        """
        results = extract_results(_solved_build(case9))
        total_Pg = results["Pg"].sum()
        assert 315.0 <= total_Pg <= 325.0, \
            f"Total Pg={total_Pg:.2f} MW outside expected range [315, 325]"

    def test_Va_deg_is_in_degrees_not_radians(self):
        """
        Voltage angles for case9 are at most ~5 degrees in magnitude.
        If returned in radians they would be ~0.087 rad — still small,
        but the slack bus is pinned to 0 and others should be < 10 deg.
        We check that no angle exceeds 90 degrees (would indicate radians
        were mistakenly scaled or something else went wrong).
        """
        results = extract_results(_solved_build(case9))
        assert np.abs(results["Va_deg"]).max() < 90.0, \
            "Va_deg values appear too large; check units"

    def test_Vm_is_dimensionless_pu(self):
        """
        Voltage magnitudes should be p.u., i.e. in [0.9, 1.1] for case9.
        """
        results = extract_results(_solved_build(case9))
        assert results["Vm"].min() >= 0.85
        assert results["Vm"].max() <= 1.15

    def test_p_net_is_in_MW(self):
        """Net injections in MW: generators inject positive, loads negative."""
        results = extract_results(_solved_build(case9))
        # At least some buses inject power (generators) and some absorb (loads)
        assert results["p_net"].max() > 10.0, \
            "p_net should be in MW; positive injections expected > 10 MW"


# ---------------------------------------------------------------------------
# Single-step: slack bus
# ---------------------------------------------------------------------------

class TestSlackBus:

    @pytest.mark.parametrize("case_fn", [case9, case14])
    def test_slack_bus_angle_zero(self, case_fn):
        build   = _solved_build(case_fn)
        results = extract_results(build)
        ref     = build.data["ref"]
        assert abs(results["Va_deg"][ref]) < 1e-6, \
            f"Slack bus (index {ref}) angle should be 0 degrees"


# ---------------------------------------------------------------------------
# Multi-step: result dict structure and shapes
# ---------------------------------------------------------------------------

class TestMultiStepStructure:

    def test_all_expected_keys_present(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build   = _solved_multistep_build(case9, 3, df_P, df_Q)
        results = extract_results(build)
        expected = {"status", "objective", "Pg", "Qg", "Vm", "Va_deg",
                    "p_net", "q_net"}
        assert expected.issubset(set(results.keys()))

    def test_objective_is_scalar(self, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        build   = _solved_multistep_build(case9, 3, df_P, df_Q)
        results = extract_results(build)
        assert np.ndim(results["objective"]) == 0

    @pytest.mark.parametrize("T", [1, 3])
    def test_Pg_shape_multistep(self, T, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        df_P_t = df_P.iloc[:T]
        df_Q_t = df_Q.iloc[:T]
        build   = _solved_multistep_build(case9, T, df_P_t, df_Q_t)
        results = extract_results(build)
        assert results["Pg"].shape == (T, 3)

    @pytest.mark.parametrize("T", [1, 3])
    def test_Vm_shape_multistep(self, T, case9_multistep_load):
        df_P, df_Q = case9_multistep_load
        df_P_t = df_P.iloc[:T]
        df_Q_t = df_Q.iloc[:T]
        build   = _solved_multistep_build(case9, T, df_P_t, df_Q_t)
        results = extract_results(build)
        assert results["Vm"].shape == (T, 9)


# ---------------------------------------------------------------------------
# compare_to_reference
# ---------------------------------------------------------------------------

class TestCompareToReference:

    def _make_reference(self, results):
        """Build a synthetic reference dict from results (self-comparison)."""
        return {
            "objective": float(results["objective"]),
            "Pg":        results["Pg"].tolist(),
            "Qg":        results["Qg"].tolist(),
            "Vm":        results["Vm"].tolist(),
            "Va_deg":    results["Va_deg"].tolist(),
        }

    def test_self_comparison_abs_diff_is_zero(self):
        build   = _solved_build(case9)
        results = extract_results(build)
        ref     = self._make_reference(results)
        comp    = compare_to_reference(results, ref)
        for field, entry in comp.items():
            np.testing.assert_allclose(
                entry["abs_diff"], 0.0, atol=1e-10,
                err_msg=f"Self-comparison abs_diff for '{field}' should be 0"
            )

    def test_self_comparison_rel_diff_is_zero(self):
        build   = _solved_build(case9)
        results = extract_results(build)
        ref     = self._make_reference(results)
        comp    = compare_to_reference(results, ref)
        for field, entry in comp.items():
            np.testing.assert_allclose(
                entry["rel_diff"], 0.0, atol=1e-10,
                err_msg=f"Self-comparison rel_diff for '{field}' should be 0"
            )

    def test_comparison_returns_expected_fields(self):
        build   = _solved_build(case9)
        results = extract_results(build)
        ref     = self._make_reference(results)
        comp    = compare_to_reference(results, ref)
        assert set(comp.keys()) == {"objective", "Pg", "Qg", "Vm", "Va_deg"}

    def test_each_field_has_required_subkeys(self):
        build   = _solved_build(case9)
        results = extract_results(build)
        ref     = self._make_reference(results)
        comp    = compare_to_reference(results, ref)
        for field, entry in comp.items():
            assert "cvxopf"    in entry, f"Missing 'cvxopf' in '{field}'"
            assert "reference" in entry, f"Missing 'reference' in '{field}'"
            assert "abs_diff"  in entry, f"Missing 'abs_diff' in '{field}'"
            assert "rel_diff"  in entry, f"Missing 'rel_diff' in '{field}'"

    def test_missing_field_in_reference_is_skipped(self):
        build   = _solved_build(case9)
        results = extract_results(build)
        # Reference with only objective
        ref  = {"objective": float(results["objective"])}
        comp = compare_to_reference(results, ref)
        assert set(comp.keys()) == {"objective"}

    def test_known_difference_detected(self):
        build   = _solved_build(case9)
        results = extract_results(build)
        ref     = self._make_reference(results)
        # Perturb reference Pg by 10 MW on first generator
        ref["Pg"][0] += 10.0
        comp = compare_to_reference(results, ref)
        assert comp["Pg"]["abs_diff"][0] > 9.9, \
            "A 10 MW perturbation should show up as abs_diff > 9.9"


# ---------------------------------------------------------------------------
# test_edge_cases
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_extract_results_unknown_formulation_raises(self):
        dummy = OPFBuild(
            prob=cp.Problem(cp.Minimize(0)),
            variables={}, data={},
            formulation="unknown", is_convex=True,
        )
        with pytest.raises(ValueError, match="unknown formulation"):
            extract_results(dummy)
