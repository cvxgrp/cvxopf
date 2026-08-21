"""
Tests for src/cvxopf/results.py
"""

from types import SimpleNamespace

import numpy as np
import pytest
import cvxpy as cp

from cvxopf import Load
from cvxopf.testcases import case9, case14
from cvxopf.problem import (
    OPFBuild,
    StorageUnitIdeal,
    build_opf,
    build_opf_multistep,
)
from cvxopf.results import extract_results, compare_to_reference


def _mixed_load_fleet():
    """Return one fixed and two heterogeneous sheddable case9 loads."""
    return [
        Load(
            5,
            90.0,
            "load-5",
            q_load_mvar=30.0,
            shedding_cost_per_mwh=5000.0,
            max_shed_fraction=0.5,
        ),
        Load(7, 100.0, "load-7", q_load_mvar=35.0),
        Load(
            9,
            125.0,
            "load-9",
            q_load_mvar=-50.0,
            shedding_cost_per_mwh=7000.0,
            max_shed_fraction=0.25,
        ),
    ]


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

    @pytest.mark.parametrize(
        ("formulation", "fields"),
        [
            (
                "ac",
                {"Pg", "Qg", "Vm", "Va_deg", "p_net", "q_net"},
            ),
            ("lossy_dc", {"Pg", "p_flows", "p_net"}),
            ("singlenode_dc", {"Pg", "p_net"}),
        ],
    )
    @pytest.mark.parametrize("multistep", [False, True])
    def test_no_primal_solution_has_common_result_policy(
        self, formulation, fields, multistep
    ):
        x = cp.Variable(1)
        prob = cp.Problem(cp.Minimize(0), [x >= 1, x <= 0])
        prob.solve()
        value = [x] if multistep else x

        if formulation == "ac":
            variables = {
                name: value
                for name in ("Pg", "Qg", "v", "theta", "p", "q")
            }
        elif formulation == "lossy_dc":
            variables = {"Pg": value, "p_flows": value}
        else:
            variables = {"Pg": value}

        data = {"baseMVA": 100.0}
        if multistep:
            data["T"] = 1
        expressions = {"p_net": [x] if multistep else x}
        build = OPFBuild(
            prob=prob,
            variables=variables,
            data=data,
            formulation=formulation,
            is_convex=formulation != "ac",
            expressions=expressions,
        )

        results = extract_results(build)
        assert results["status"] == "infeasible"
        assert np.isnan(results["objective"])
        assert fields <= results.keys()
        assert all(results[field] is None for field in fields)

    def test_unreachable_hard_terminal_policy_retains_storage_schema(self):
        unit = StorageUnitIdeal(
            bus=1,
            apparent_power_rating=10.0,
            capacity=100.0,
            initial_soc=0.0,
            terminal_soc=100.0,
            terminal_constraint="equality",
        )
        build = build_opf(
            case9(),
            formulation="singlenode_dc",
            storage=[unit],
            delta=1.0,
        )
        build.solve()

        results = extract_results(build)
        assert set(results) == {
            "status",
            "objective",
            "Pg",
            "p_net",
            "b",
            "soc",
            "storage_cost",
            "storage_device_ids",
            "storage_device_id_is_explicit",
            "storage_terminal_deviation",
            "p_load",
            "q_load",
            "p_load_served",
        }
        assert results["status"] == cp.INFEASIBLE
        assert np.isnan(results["objective"])
        assert np.isnan(results["storage_cost"])
        assert all(
            results[field] is None
            for field in (
                "Pg", "p_net", "b", "soc",
                "storage_terminal_deviation",
            )
        )

    def test_device_independent_infeasibility_retains_core_schema(self):
        case = case9()
        case["bus"][:, 2] = 1e6
        build = build_opf(case, formulation="singlenode_dc")
        build.solve()

        results = extract_results(build)
        assert set(results) == {
            "status",
            "objective",
            "Pg",
            "p_net",
            "p_load",
            "q_load",
            "p_load_served",
        }
        assert results["status"] == cp.INFEASIBLE
        assert np.isnan(results["objective"])
        assert results["Pg"] is None
        assert results["p_net"] is None

    def test_derived_results_remain_unavailable_with_partial_primal_values(
        self,
    ):
        pg = cp.Variable(1)
        flow = cp.Variable(1)
        b = cp.Variable(1)
        soc = cp.Variable(1)
        p_nd = cp.Variable(1)
        p_hvdc_in = cp.Variable(1)
        p_hvdc_out = cp.Variable(1)
        p_net = cp.Variable(1)
        prob = cp.Problem(cp.Minimize(0), [pg == 0, flow == 0])
        prob.solve()
        build = OPFBuild(
            prob=SimpleNamespace(status=prob.status, value=None),
            variables={
                "Pg": pg,
                "p_flows": flow,
                "b": b,
                "soc": soc,
                "p_nd": p_nd,
                "p_hvdc_in": p_hvdc_in,
                "p_hvdc_out": p_hvdc_out,
            },
            data={
                "baseMVA": 100.0,
                "ns": 1,
                "storage_device_ids": np.array(["storage_0"], dtype=object),
                "storage_device_id_is_explicit": np.array([False]),
                "storage_terminal_soc": np.array([50.0]),
                "nnd": 1,
                "nd_p_available": np.array([20.0]),
                "n_hvdc": 1,
            },
            formulation="lossy_dc",
            is_convex=True,
            expressions={
                "p_net": p_net,
                "storage_cost": cp.abs(b),
                "storage_terminal_cost": cp.square(soc - 50.0),
            },
        )

        results = extract_results(build)
        assert results["Pg"] is not None
        assert results["p_flows"] is not None
        assert results["p_net"] is None
        assert np.isnan(results["objective"])
        assert results["b"] is None
        assert results["soc"] is None
        assert np.isnan(results["storage_cost"])
        assert results["storage_terminal_deviation"] is None
        assert np.isnan(results["storage_terminal_cost"])
        assert results["p_nd"] is None
        assert results["curtailment"] is None
        assert results["p_hvdc_in"] is None
        assert results["p_hvdc_out"] is None
        assert results["hvdc_loss"] is None

    def test_available_device_values_survive_missing_core_expression(self):
        pg = cp.Variable(1)
        flow = cp.Variable(1)
        p_nd = cp.Variable(1)
        missing_p_net = cp.Variable(1)
        prob = cp.Problem(cp.Minimize(0), [pg == 0, flow == 0])
        prob.solve()
        p_nd.value = np.array([5.0])
        build = OPFBuild(
            prob=prob,
            variables={"Pg": pg, "p_flows": flow, "p_nd": p_nd},
            data={
                "baseMVA": 100.0,
                "nnd": 1,
                "nd_p_available": np.array([20.0]),
            },
            formulation="lossy_dc",
            is_convex=True,
            expressions={"p_net": missing_p_net},
        )

        results = extract_results(build)
        assert results["p_net"] is None
        np.testing.assert_array_equal(results["p_nd"], [5.0])
        np.testing.assert_array_equal(results["curtailment"], [15.0])

    @pytest.mark.parametrize(
        ("with_storage", "soft_terminal", "with_nd", "with_hvdc"),
        [
            (False, False, False, False),
            (True, False, False, False),
            (True, True, False, False),
            (False, False, True, False),
            (False, False, False, True),
            (True, True, True, True),
        ],
    )
    @pytest.mark.parametrize("multistep", [False, True])
    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    def test_no_primal_schema_is_determined_by_built_model(
        self,
        formulation,
        multistep,
        with_storage,
        soft_terminal,
        with_nd,
        with_hvdc,
    ):
        x = cp.Variable(1)
        prob = cp.Problem(cp.Minimize(0), [x >= 1, x <= 0])
        prob.solve()
        value = [x] if multistep else x

        core_variables = {
            "ac": {
                name: value
                for name in ("Pg", "Qg", "v", "theta", "p", "q")
            },
            "lossy_dc": {"Pg": value, "p_flows": value},
            "singlenode_dc": {"Pg": value},
        }[formulation]
        variables = dict(core_variables)
        data = {"baseMVA": 100.0}
        expressions = {"p_net": value}
        expected = {
            "ac": {
                "status", "objective", "Pg", "Qg", "Vm", "Va_deg",
                "p_net", "q_net",
                "branch_p_from", "branch_q_from",
                "branch_p_to", "branch_q_to",
                "branch_s_from", "branch_s_to",
            },
            "lossy_dc": {
                "status", "objective", "Pg", "p_flows", "p_net",
            },
            "singlenode_dc": {
                "status", "objective", "Pg", "p_net",
            },
        }[formulation]
        if formulation == "ac":
            expressions["q_net"] = value
        if multistep:
            data["T"] = 1

        scalar_nan_fields = {"objective"}
        if with_storage:
            variables.update({"b": value, "soc": value})
            if formulation == "ac":
                variables["b_q"] = value
            data.update({
                "ns": 1,
                "storage_device_ids": np.array(
                    ["storage_0"], dtype=object
                ),
                "storage_device_id_is_explicit": np.array([False]),
                "storage_terminal_soc": np.array([50.0]),
            })
            expressions["storage_cost"] = x
            expected |= {
                "b", "soc", "storage_cost",
                "storage_device_ids",
                "storage_device_id_is_explicit",
                "storage_terminal_deviation",
            }
            scalar_nan_fields.add("storage_cost")
            if formulation == "ac":
                expected.add("b_q")
            if soft_terminal:
                expressions["storage_terminal_cost"] = x
                expected.add("storage_terminal_cost")
                scalar_nan_fields.add("storage_terminal_cost")

        if with_nd:
            variables["p_nd"] = value
            if formulation == "ac":
                variables["q_nd"] = value
            data["nnd"] = 1
            data[
                "nd_available" if multistep else "nd_p_available"
            ] = np.array([[20.0]]) if multistep else np.array([20.0])
            expected |= {"p_nd", "curtailment"}
            if formulation == "ac":
                expected.add("q_nd")

        if with_hvdc and formulation != "singlenode_dc":
            variables.update({
                "p_hvdc_in": value,
                "p_hvdc_out": value,
            })
            data["n_hvdc"] = 1
            expected |= {"p_hvdc_in", "p_hvdc_out", "hvdc_loss"}

        build = OPFBuild(
            prob=prob,
            variables=variables,
            data=data,
            formulation=formulation,
            is_convex=formulation != "ac",
            expressions=expressions,
        )
        results = extract_results(build)

        assert set(results) == expected
        assert results["status"] == cp.INFEASIBLE
        assert all(np.isnan(results[field]) for field in scalar_nan_fields)
        assert all(
            results[field] is None
            for field in expected - scalar_nan_fields - {
                "status",
                "storage_device_ids",
                "storage_device_id_is_explicit",
            }
        )
        if with_storage:
            np.testing.assert_array_equal(
                results["storage_device_ids"], ["storage_0"]
            )
            np.testing.assert_array_equal(
                results["storage_device_id_is_explicit"], [False]
            )


class TestSheddableLoadResults:
    """Stage 5 result schemas, units, and unavailable-primal behavior."""

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    @pytest.mark.parametrize("multistep", [False, True])
    def test_successful_result_values_and_shapes(
        self, formulation, multistep
    ):
        loads = _mixed_load_fleet()
        delta = 0.5
        if multistep:
            build = build_opf_multistep(
                case9(),
                T=2,
                formulation=formulation,
                loads=loads,
                delta=delta,
            )
        else:
            build = build_opf(
                case9(),
                formulation=formulation,
                loads=loads,
                delta=delta,
            )
        build.solve()
        results = extract_results(build)

        interval_shape = (2, 3) if multistep else (3,)
        shed_shape = (2, 2) if multistep else (2,)
        total_shape = (2,) if multistep else ()
        assert results["p_load"].shape == interval_shape
        assert results["q_load"].shape == interval_shape
        assert results["p_load_served"].shape == interval_shape
        assert results["p_load_shed"].shape == shed_shape
        assert results["load_shed_fraction"].shape == shed_shape
        assert np.shape(results["p_load_shed_total"]) == total_shape
        assert results["energy_not_served_by_load"].shape == (2,)
        assert np.ndim(results["energy_not_served"]) == 0
        assert np.ndim(results["load_shedding_cost"]) == 0

        expected_p = np.array([90.0, 100.0, 125.0])
        expected_q = np.array([30.0, 35.0, -50.0])
        if multistep:
            expected_p = np.tile(expected_p, (2, 1))
            expected_q = np.tile(expected_q, (2, 1))
        np.testing.assert_allclose(results["p_load"], expected_p)
        np.testing.assert_allclose(results["q_load"], expected_q)

        shed_indices = np.array([0, 2])
        np.testing.assert_allclose(
            results["p_load_served"][..., shed_indices]
            + results["p_load_shed"],
            results["p_load"][..., shed_indices],
        )
        np.testing.assert_allclose(
            results["p_load_served"][..., 1],
            results["p_load"][..., 1],
        )
        np.testing.assert_allclose(
            results["p_load_shed_total"],
            np.sum(results["p_load_shed"], axis=-1),
        )
        if formulation == "ac":
            assert results["q_load_served"].shape == interval_shape
            assert results["q_load_shed"].shape == shed_shape
            np.testing.assert_allclose(
                results["q_load_served"][..., shed_indices]
                + results["q_load_shed"],
                results["q_load"][..., shed_indices],
            )
            np.testing.assert_allclose(
                results["q_load_served"][..., 1],
                results["q_load"][..., 1],
            )
        else:
            assert "q_load_served" not in results
            assert "q_load_shed" not in results

        shed_by_step = np.atleast_2d(results["p_load_shed"])
        expected_ens_by_load = delta * np.sum(shed_by_step, axis=0)
        np.testing.assert_allclose(
            results["energy_not_served_by_load"], expected_ens_by_load
        )
        assert results["energy_not_served"] == pytest.approx(
            np.sum(expected_ens_by_load)
        )
        assert results["load_shedding_cost"] == pytest.approx(
            np.dot(np.array([5000.0, 7000.0]), expected_ens_by_load)
        )

    @pytest.mark.parametrize(
        "formulation", ["ac", "lossy_dc", "singlenode_dc"]
    )
    @pytest.mark.parametrize("multistep", [False, True])
    def test_no_primal_retains_inputs_and_conditional_schema(
        self, formulation, multistep
    ):
        loads = _mixed_load_fleet()
        if multistep:
            build = build_opf_multistep(
                case9(), T=2, formulation=formulation, loads=loads
            )
            expected_p = np.tile([90.0, 100.0, 125.0], (2, 1))
            expected_q = np.tile([30.0, 35.0, -50.0], (2, 1))
        else:
            build = build_opf(case9(), formulation=formulation, loads=loads)
            expected_p = np.array([90.0, 100.0, 125.0])
            expected_q = np.array([30.0, 35.0, -50.0])

        results = extract_results(build)

        assert results["status"] is None
        np.testing.assert_allclose(results["p_load"], expected_p)
        np.testing.assert_allclose(results["q_load"], expected_q)
        unavailable = {
            "p_load_served",
            "p_load_shed",
            "load_shed_fraction",
            "p_load_shed_total",
            "energy_not_served_by_load",
            "energy_not_served",
        }
        if formulation == "ac":
            unavailable |= {"q_load_served", "q_load_shed"}
        else:
            assert "q_load_served" not in results
            assert "q_load_shed" not in results
        assert all(results[name] is None for name in unavailable)
        assert np.isnan(results["load_shedding_cost"])

        expected_metadata = {
            "nload",
            "nsheddable",
            "Cload",
            "load_device_ids",
            "load_bus_external",
            "load_bus_internal",
            "load_has_reactive",
            "load_is_sheddable",
            "sheddable_load_indices",
            "sheddable_load_device_ids",
            "load_max_shed_fraction",
            "load_shedding_cost_per_mwh",
        }
        assert expected_metadata <= build.data.keys()

    def test_partial_multistep_fraction_discards_complete_derived_arrays(self):
        build = build_opf_multistep(
            case9(), T=2, formulation="ac", loads=_mixed_load_fleet()
        )
        build.variables["load_shed_fraction"][0].value = [0.1, 0.2]

        results = extract_results(build)

        np.testing.assert_allclose(
            results["p_load"],
            np.tile([90.0, 100.0, 125.0], (2, 1)),
        )
        for name in (
            "p_load_served",
            "q_load_served",
            "p_load_shed",
            "q_load_shed",
            "load_shed_fraction",
            "p_load_shed_total",
            "energy_not_served_by_load",
            "energy_not_served",
        ):
            assert results[name] is None
        assert np.isnan(results["load_shedding_cost"])

    @pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc"])
    def test_solver_certified_infeasibility_retains_load_inputs(
        self, formulation
    ):
        load = Load(
            5,
            10_000.0,
            "oversized",
            q_load_mvar=2500.0,
            shedding_cost_per_mwh=5000.0,
            max_shed_fraction=0.1,
        )
        build = build_opf(case9(), formulation=formulation, loads=[load])
        build.solve()

        results = extract_results(build)

        assert results["status"] == cp.INFEASIBLE
        np.testing.assert_array_equal(results["p_load"], [10_000.0])
        np.testing.assert_array_equal(results["q_load"], [2500.0])
        for name in (
            "p_load_served",
            "p_load_shed",
            "load_shed_fraction",
            "p_load_shed_total",
            "energy_not_served_by_load",
            "energy_not_served",
        ):
            assert results[name] is None
        assert "q_load_served" not in results
        assert "q_load_shed" not in results
        assert np.isnan(results["load_shedding_cost"])
