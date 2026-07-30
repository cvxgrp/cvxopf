"""Stage 2 tests for AC branch-terminal reporting without enforcement."""

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf.generator import DispatchableGenerator
from cvxopf.network import make_branch_admittance, reindex_case_to_consecutive
from cvxopf.problem import (
    OPFBuild,
    OPFOptions,
    build_opf,
    build_opf_multistep,
)
from cvxopf.results import extract_results
from cvxopf.testcases import case9, make_singlenode_case


BRANCH_EXPRESSION_KEYS = {
    "branch_p_from_pu",
    "branch_q_from_pu",
    "branch_p_to_pu",
    "branch_q_to_pu",
}
BRANCH_RESULT_KEYS = {
    "branch_p_from",
    "branch_q_from",
    "branch_p_to",
    "branch_q_to",
    "branch_s_from",
    "branch_s_to",
}


def _flat_case9_load(steps):
    case = case9()
    return (
        pd.DataFrame(np.tile(case["bus"][:, 2], (steps, 1))),
        pd.DataFrame(np.tile(case["bus"][:, 3], (steps, 1))),
    )


def _independent_terminal_power(build, case):
    reindexed, _ = reindex_case_to_consecutive(case)
    admittance = make_branch_admittance(reindexed)
    voltage = build.variables["v"].value.flatten()
    angle = build.variables["theta"].value.flatten()
    complex_voltage = voltage * np.exp(1j * angle)
    from_voltage = complex_voltage[admittance.from_bus]
    to_voltage = complex_voltage[admittance.to_bus]
    from_current = (
        admittance.yff * from_voltage
        + admittance.yft * to_voltage
    )
    to_current = (
        admittance.ytf * from_voltage
        + admittance.ytt * to_voltage
    )
    scale = build.data["baseMVA"]
    return (
        scale * from_voltage * np.conj(from_current),
        scale * to_voltage * np.conj(to_current),
    )


class TestACBranchBuildContract:

    def test_metadata_preserves_external_and_internal_endpoints(self):
        case = case9()
        build = build_opf(
            case,
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )

        assert build.data["nl"] == case["branch"].shape[0]
        np.testing.assert_array_equal(
            build.data["branch_from_bus_external"], case["branch"][:, 0]
        )
        np.testing.assert_array_equal(
            build.data["branch_to_bus_external"], case["branch"][:, 1]
        )
        np.testing.assert_array_equal(
            build.data["branch_from_bus_internal"], case["branch"][:, 0] - 1
        )
        np.testing.assert_array_equal(
            build.data["branch_to_bus_internal"], case["branch"][:, 1] - 1
        )
        np.testing.assert_array_equal(
            build.data["branch_rate_a_mva"], case["branch"][:, 5]
        )
        np.testing.assert_array_equal(
            build.data["branch_status"], case["branch"][:, 10].astype(bool)
        )

    def test_nonconsecutive_external_endpoints_preserved_before_reindex(self):
        case = case9()
        external_ids = np.array([10, 30, 70, 90, 120, 180, 250, 400, 900])
        original_ids = case["bus"][:, 0].astype(int)
        mapping = dict(zip(original_ids, external_ids, strict=True))
        case["bus"][:, 0] = external_ids
        for column in (0, 1):
            case["branch"][:, column] = [
                mapping[int(bus)] for bus in case["branch"][:, column]
            ]
        case["gen"][:, 0] = [
            mapping[int(bus)] for bus in case["gen"][:, 0]
        ]
        external_from = case["branch"][:, 0].astype(int).copy()
        external_to = case["branch"][:, 1].astype(int).copy()
        external_to_internal = {
            int(external): internal
            for internal, external in enumerate(external_ids)
        }

        build = build_opf(case, formulation="ac")

        np.testing.assert_array_equal(
            build.data["branch_from_bus_external"], external_from
        )
        np.testing.assert_array_equal(
            build.data["branch_to_bus_external"], external_to
        )
        np.testing.assert_array_equal(
            build.data["branch_from_bus_internal"],
            [external_to_internal[int(bus)] for bus in external_from],
        )
        np.testing.assert_array_equal(
            build.data["branch_to_bus_internal"],
            [external_to_internal[int(bus)] for bus in external_to],
        )

    def test_single_step_publishes_lifted_variables(self):
        build = build_opf(case9(), formulation="ac")

        assert BRANCH_EXPRESSION_KEYS <= build.expressions.keys()
        for name in BRANCH_EXPRESSION_KEYS:
            expression = build.expressions[name]
            assert isinstance(expression, cp.Variable)
            assert expression.shape == (build.data["nl"],)

    def test_constrained_indices_select_finite_positive_active_ratings(self):
        case = case9()
        case["branch"][0, 5] = 0.0
        case["branch"][1, 5] = np.inf
        case["branch"][2, 5] = 1.0e12
        case["branch"][3, 10] = 0
        build = build_opf(
            case,
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )

        expected = np.flatnonzero(
            build.data["branch_status"]
            & np.isfinite(build.data["branch_rate_a_mva"])
            & (build.data["branch_rate_a_mva"] > 0)
        )
        np.testing.assert_array_equal(
            build.data["constrained_branch_indices"], expected
        )
        assert 0 not in expected
        assert 1 not in expected
        assert 2 in expected
        assert 3 not in expected

    def test_multistep_publishes_ordered_expression_lists(self):
        df_p, df_q = _flat_case9_load(2)
        build = build_opf_multistep(
            case9(), df_p, df_q, T=2, formulation="ac"
        )

        for name in BRANCH_EXPRESSION_KEYS:
            expressions = build.expressions[name]
            assert isinstance(expressions, list)
            assert len(expressions) == 2
            assert all(isinstance(item, cp.Variable) for item in expressions)
            assert all(
                item.shape == (build.data["nl"],) for item in expressions
            )

    def test_zero_branch_table_uses_empty_constants(self):
        generator = DispatchableGenerator(
            bus=1,
            p_max_mw=100.0,
            q_min_mvar=-100.0,
            q_max_mvar=100.0,
            cost_coeffs=(0.0, 1.0),
        )
        case = make_singlenode_case(50.0, [generator])
        build = build_opf(case, formulation="ac")

        assert build.data["nl"] == 0
        for name in BRANCH_EXPRESSION_KEYS:
            expression = build.expressions[name]
            assert isinstance(expression, cp.Constant)
            assert expression.shape == (0,)


class TestACBranchNumerics:

    def test_single_step_matches_independent_complex_arithmetic(self):
        case = case9()
        build = build_opf(case, formulation="ac")
        build.solve()
        results = extract_results(build)
        from_power, to_power = _independent_terminal_power(build, case)

        np.testing.assert_allclose(
            results["branch_p_from"], from_power.real, atol=1e-8
        )
        np.testing.assert_allclose(
            results["branch_q_from"], from_power.imag, atol=1e-8
        )
        np.testing.assert_allclose(
            results["branch_p_to"], to_power.real, atol=1e-8
        )
        np.testing.assert_allclose(
            results["branch_q_to"], to_power.imag, atol=1e-8
        )
        np.testing.assert_allclose(
            results["branch_s_from"], np.abs(from_power), atol=1e-8
        )
        np.testing.assert_allclose(
            results["branch_s_to"], np.abs(to_power), atol=1e-8
        )

    def test_inactive_branch_reports_exact_zero_at_both_terminals(self):
        case = case9()
        inactive = case["branch"][0].copy()
        inactive[10] = 0
        case["branch"] = np.vstack([case["branch"], inactive])
        inactive_row = case["branch"].shape[0] - 1
        build = build_opf(case, formulation="ac")
        build.solve()
        results = extract_results(build)

        for name in BRANCH_RESULT_KEYS:
            assert results[name][inactive_row] == 0.0

    def test_multistep_result_shapes(self):
        df_p, df_q = _flat_case9_load(2)
        build = build_opf_multistep(
            case9(), df_p, df_q, T=2, formulation="ac"
        )
        build.solve()
        results = extract_results(build)

        for name in BRANCH_RESULT_KEYS:
            assert results[name].shape == (2, build.data["nl"])

    def test_empty_branch_results_have_empty_shape(self):
        generator = DispatchableGenerator(
            bus=1,
            p_max_mw=100.0,
            q_min_mvar=-100.0,
            q_max_mvar=100.0,
            cost_coeffs=(0.0, 1.0),
        )
        case = make_singlenode_case(50.0, [generator])
        build = build_opf(case, formulation="ac")
        build.variables["v"].value = np.ones((1, 1))
        build.variables["theta"].value = np.zeros((1, 1))
        results = extract_results(build)

        for name in BRANCH_RESULT_KEYS:
            assert results[name].shape == (0,)

    def test_multistep_empty_branch_results_have_t_by_zero_shape(self):
        generator = DispatchableGenerator(
            bus=1,
            p_max_mw=100.0,
            q_min_mvar=-100.0,
            q_max_mvar=100.0,
            cost_coeffs=(0.0, 1.0),
        )
        case = make_singlenode_case(50.0, [generator])
        df_p = pd.DataFrame([[50.0], [50.0]])
        df_q = pd.DataFrame([[0.0], [0.0]])
        build = build_opf_multistep(
            case, df_p, df_q, T=2, formulation="ac"
        )
        for voltage, angle in zip(
            build.variables["v"],
            build.variables["theta"],
            strict=True,
        ):
            voltage.value = np.ones((1, 1))
            angle.value = np.zeros((1, 1))
        results = extract_results(build)

        for name in BRANCH_RESULT_KEYS:
            assert results[name].shape == (2, 0)

    def test_apparent_power_requires_all_four_signed_channels(self):
        variables = {
            name: cp.Variable(1)
            for name in ("Pg", "Qg", "v", "theta")
        }
        variables["v"].value = np.ones(1)
        variables["theta"].value = np.zeros(1)
        p_net = cp.Variable(1)
        q_net = cp.Variable(1)
        p_from = cp.Variable(1)
        q_from = cp.Variable(1)
        p_to = cp.Variable(1)
        q_to = cp.Variable(1)
        p_from.value = np.array([0.1])
        q_from.value = np.array([0.2])
        p_to.value = np.array([-0.1])
        build = OPFBuild(
            prob=cp.Problem(cp.Minimize(0)),
            variables=variables,
            data={"baseMVA": 100.0},
            formulation="ac",
            is_convex=False,
            expressions={
                "p_net": p_net,
                "q_net": q_net,
                "branch_p_from_pu": p_from,
                "branch_q_from_pu": q_from,
                "branch_p_to_pu": p_to,
                "branch_q_to_pu": q_to,
            },
        )

        results = extract_results(build)

        np.testing.assert_array_equal(results["branch_p_from"], [10.0])
        np.testing.assert_array_equal(results["branch_q_from"], [20.0])
        np.testing.assert_array_equal(results["branch_p_to"], [-10.0])
        assert results["branch_q_to"] is None
        assert results["branch_s_from"] is None
        assert results["branch_s_to"] is None

    @pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc"])
    def test_branch_result_fields_absent_from_dc(self, formulation):
        build = build_opf(case9(), formulation=formulation)
        build.solve()
        results = extract_results(build)

        assert BRANCH_RESULT_KEYS.isdisjoint(results)
