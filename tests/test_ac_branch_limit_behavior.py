"""Stage 4 numerical and behavioral verification of AC branch limits."""

import warnings

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest
from cvxpy.error import SolverError

from cvxopf.network import make_branch_admittance, reindex_case_to_consecutive
from cvxopf.problem import OPFOptions, build_opf, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import (
    case9,
    case14,
    case30,
    case39,
    case57,
    case118,
)
from cvxopf.testcases.case9_dcline import case9_dcline
from cvxopf.testcases.case9_pwl import case9_pwl
from cvxopf.testcases.case30pwl import case30pwl


MVA_ATOL = 1e-4
NORMALIZED_RESIDUAL_ATOL = 1e-7
BRANCH_RESULT_KEYS = {
    "branch_p_from",
    "branch_q_from",
    "branch_p_to",
    "branch_q_to",
    "branch_s_from",
    "branch_s_to",
}
CASE_FACTORIES = (
    case9,
    case9_pwl,
    case9_dcline,
    case14,
    case30,
    case30pwl,
    case39,
    case57,
    case118,
)


def _enabled_options(**kwargs):
    return OPFOptions(enforce_branch_limits=True, **kwargs)


def _flat_load(case, steps):
    return (
        pd.DataFrame(np.tile(case["bus"][:, 2], (steps, 1))),
        pd.DataFrame(np.tile(case["bus"][:, 3], (steps, 1))),
    )


def _independent_terminal_power(case, voltage, angle_rad):
    reindexed, _ = reindex_case_to_consecutive(case)
    admittance = make_branch_admittance(reindexed)
    complex_voltage = voltage * np.exp(1j * angle_rad)
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
    base_mva = float(case["baseMVA"])
    return (
        base_mva * from_voltage * np.conj(from_current),
        base_mva * to_voltage * np.conj(to_current),
    )


def _assert_all_limits(results, build):
    rows = build.data["constrained_branch_indices"]
    ratings = build.data["branch_rate_a_mva"][rows]
    for prefix in ("from", "to"):
        apparent = results[f"branch_s_{prefix}"][..., rows]
        assert np.all(apparent <= ratings + MVA_ATOL)
        p = results[f"branch_p_{prefix}"][..., rows]
        q = results[f"branch_q_{prefix}"][..., rows]
        normalized_residual = (
            np.square(p / ratings) + np.square(q / ratings) - 1.0
        )
        assert np.max(normalized_residual) <= NORMALIZED_RESIDUAL_ATOL


class TestSingleStepBehavior:

    def test_nonbinding_limits_preserve_solution(self):
        disabled = build_opf(
            case9(),
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )
        enabled = build_opf(
            case9(), formulation="ac", options=_enabled_options()
        )
        disabled.solve()
        enabled.solve()
        disabled_results = extract_results(disabled)
        enabled_results = extract_results(enabled)

        assert enabled_results["objective"] == pytest.approx(
            disabled_results["objective"], rel=1e-8, abs=1e-6
        )
        for name in ("Pg", "Qg", "Vm", "Va_deg"):
            np.testing.assert_allclose(
                enabled_results[name],
                disabled_results[name],
                rtol=1e-6,
                atol=1e-5,
            )
        _assert_all_limits(enabled_results, enabled)

    def test_rate_b_rate_c_and_angle_bounds_remain_inert(self):
        baseline_case = case9()
        modified_case = case9()
        modified_case["branch"][:, 6] = 1.0
        modified_case["branch"][:, 7] = 2.0
        modified_case["branch"][:, 11] = -0.01
        modified_case["branch"][:, 12] = 0.01
        baseline = build_opf(baseline_case, formulation="ac")
        modified = build_opf(modified_case, formulation="ac")
        baseline.solve()
        modified.solve()
        baseline_results = extract_results(baseline)
        modified_results = extract_results(modified)

        assert modified_results["objective"] == pytest.approx(
            baseline_results["objective"], rel=1e-9, abs=1e-7
        )
        for name in (
            "Pg",
            "Qg",
            "Vm",
            "Va_deg",
            "branch_p_from",
            "branch_q_from",
            "branch_p_to",
            "branch_q_to",
        ):
            np.testing.assert_allclose(
                modified_results[name],
                baseline_results[name],
                rtol=1e-8,
                atol=1e-7,
            )

    def test_default_enforces_limits_and_false_is_escape_hatch(self):
        constrained_case = case9()
        constrained_case["branch"][0, 5] = 80.0
        default = build_opf(constrained_case, formulation="ac")
        disabled = build_opf(
            constrained_case,
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )
        default.solve()
        disabled.solve()
        default_results = extract_results(default)
        disabled_results = extract_results(disabled)

        assert OPFOptions().enforce_branch_limits is True
        assert default_results["branch_s_from"][0] == pytest.approx(
            80.0, abs=MVA_ATOL
        )
        assert disabled_results["branch_s_from"][0] > 80.0 + 1.0

    def test_binding_limit_redispatches_and_binds_from_terminal(self):
        unconstrained_case = case9()
        constrained_case = case9()
        constrained_case["branch"][0, 5] = 80.0
        disabled = build_opf(unconstrained_case, formulation="ac")
        enabled = build_opf(
            constrained_case,
            formulation="ac",
            options=_enabled_options(),
        )
        disabled.solve()
        enabled.solve()
        disabled_results = extract_results(disabled)
        enabled_results = extract_results(enabled)

        assert enabled.prob.status == cp.OPTIMAL
        assert np.linalg.norm(
            enabled_results["Pg"] - disabled_results["Pg"],
            ord=np.inf,
        ) > 1.0
        assert enabled_results["branch_s_from"][0] == pytest.approx(
            80.0, abs=MVA_ATOL
        )
        assert enabled_results["branch_s_to"][0] < 80.0 - 0.05
        _assert_all_limits(enabled_results, enabled)

        independent_from, independent_to = _independent_terminal_power(
            constrained_case,
            enabled_results["Vm"],
            np.deg2rad(enabled_results["Va_deg"]),
        )
        np.testing.assert_allclose(
            enabled_results["branch_s_from"],
            np.abs(independent_from),
            atol=MVA_ATOL,
        )
        np.testing.assert_allclose(
            enabled_results["branch_s_to"],
            np.abs(independent_to),
            atol=MVA_ATOL,
        )
        assert abs(independent_from[0]) == pytest.approx(
            80.0, abs=MVA_ATOL
        )
        assert abs(independent_to[0]) < 80.0 - 0.05

    def test_physically_unreachable_limit_is_not_reported_optimal(self):
        """An analytical contradiction must never be accepted as optimal.

        Bus 1 has at least 10 MW of generation and only one incident branch,
        whose 5 MVA terminal limit cannot carry that active power. IPOPT may
        stop at USER_LIMIT rather than certify infeasibility, so this is
        deliberately a non-optimality gate rather than a solver-status claim.
        """
        case = case9()
        case["branch"][0, 5] = 5.0
        assert case["gen"][0, 9] == 10.0
        assert np.count_nonzero(
            (case["branch"][:, 0] == 1)
            | (case["branch"][:, 1] == 1)
        ) == 1
        build = build_opf(
            case, formulation="ac", options=_enabled_options()
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            try:
                build.solve(max_iter=1000)
            except SolverError:
                # IPOPT is not an infeasibility certifier. A solver failure
                # is an acceptable non-certificate for this analytically
                # impossible DNLP, but must not be reported as an optimum.
                return

        assert build.prob.status not in {
            cp.OPTIMAL,
            cp.OPTIMAL_INACCURATE,
        }


class TestMultistepAndRepresentation:

    def test_sparse_and_dense_enforced_solutions_match(self):
        sparse = build_opf(
            case9(),
            formulation="ac",
            options=_enabled_options(sparse_pq=True),
        )
        dense = build_opf(
            case9(),
            formulation="ac",
            options=_enabled_options(sparse_pq=False),
        )
        sparse.solve()
        dense.solve()
        sparse_results = extract_results(sparse)
        dense_results = extract_results(dense)

        assert dense_results["objective"] == pytest.approx(
            sparse_results["objective"], rel=1e-7, abs=1e-5
        )
        for name in (
            "Pg", "Qg", "Vm", "Va_deg",
            "branch_p_from", "branch_q_from",
            "branch_p_to", "branch_q_to",
        ):
            np.testing.assert_allclose(
                dense_results[name],
                sparse_results[name],
                rtol=1e-5,
                atol=1e-4,
            )

    def test_multistep_t1_matches_single_step(self):
        case = case9()
        df_p, df_q = _flat_load(case, 1)
        single = build_opf(
            case, formulation="ac", options=_enabled_options()
        )
        multi = build_opf_multistep(
            case,
            df_p,
            df_q,
            T=1,
            formulation="ac",
            options=_enabled_options(),
        )
        single.solve()
        multi.solve()
        single_results = extract_results(single)
        multi_results = extract_results(multi)

        assert multi_results["objective"] == pytest.approx(
            single_results["objective"], rel=1e-7, abs=1e-5
        )
        for name in BRANCH_RESULT_KEYS:
            np.testing.assert_allclose(
                multi_results[name][0],
                single_results[name],
                rtol=1e-5,
                atol=1e-4,
            )

    def test_multistep_limits_hold_at_every_step_in_order(self):
        case = case9()
        case["branch"][0, 5] = 85.0
        scales = np.array([0.9, 1.0, 1.1])
        df_p = pd.DataFrame(np.outer(scales, case["bus"][:, 2]))
        df_q = pd.DataFrame(np.outer(scales, case["bus"][:, 3]))
        build = build_opf_multistep(
            case,
            df_p,
            df_q,
            T=3,
            formulation="ac",
            options=_enabled_options(),
        )
        build.solve()
        results = extract_results(build)

        assert build.prob.status == cp.OPTIMAL
        for name in BRANCH_RESULT_KEYS:
            assert results[name].shape == (3, build.data["nl"])
        _assert_all_limits(results, build)
        assert not np.allclose(
            results["branch_p_from"][0],
            results["branch_p_from"][2],
        )


class TestReferenceAndSchema:

    @pytest.mark.parametrize(
        ("case_factory", "reference_fixture", "angle_atol", "flow_atol"),
        [
            (case9, "case9_ref", 1e-3, 5e-2),
            (case14, "case14_ref", 1e-3, 5e-2),
            (case57, "case57_ref", 1e-2, 1e-1),
        ],
    )
    def test_reported_flows_agree_with_pypower_fixture(
        self,
        request,
        case_factory,
        reference_fixture,
        angle_atol,
        flow_atol,
    ):
        reference = request.getfixturevalue(reference_fixture)
        case = case_factory()
        build = build_opf(case, formulation="ac")
        build.solve()
        results = extract_results(build)
        np.testing.assert_allclose(
            results["Vm"], reference["Vm"], atol=1e-4
        )
        np.testing.assert_allclose(
            results["Va_deg"], reference["Va_deg"], atol=angle_atol
        )
        np.testing.assert_allclose(
            results["branch_p_from"], reference["PF"], atol=flow_atol
        )
        np.testing.assert_allclose(
            results["branch_q_from"], reference["QF"], atol=flow_atol
        )
        np.testing.assert_allclose(
            results["branch_p_to"], reference["PT"], atol=flow_atol
        )
        np.testing.assert_allclose(
            results["branch_q_to"], reference["QT"], atol=flow_atol
        )

    def test_reported_flows_match_internal_equations_at_pypower_voltage(
        self,
        case9_ref,
    ):
        case = case9()
        build = build_opf(case, formulation="ac")
        build.solve()
        results = extract_results(build)
        reference_from, reference_to = _independent_terminal_power(
            case,
            case9_ref["Vm"],
            np.deg2rad(case9_ref["Va_deg"]),
        )
        np.testing.assert_allclose(
            results["branch_p_from"], reference_from.real, atol=5e-2
        )
        np.testing.assert_allclose(
            results["branch_q_from"], reference_from.imag, atol=5e-2
        )
        np.testing.assert_allclose(
            results["branch_p_to"], reference_to.real, atol=5e-2
        )
        np.testing.assert_allclose(
            results["branch_q_to"], reference_to.imag, atol=5e-2
        )

    def test_unsuccessful_ac_build_retains_none_branch_schema(self):
        build = build_opf(
            case9(), formulation="ac", options=_enabled_options()
        )
        for variable in build.variables.values():
            if isinstance(variable, list):
                for item in variable:
                    item.value = None
            else:
                variable.value = None
        marker = cp.Variable()
        build.prob = cp.Problem(
            cp.Minimize(0), [marker >= 1, marker <= 0]
        )
        build.prob.solve()

        results = extract_results(build)

        assert build.prob.status == cp.INFEASIBLE
        assert BRANCH_RESULT_KEYS <= results.keys()
        assert all(results[name] is None for name in BRANCH_RESULT_KEYS)

    @pytest.mark.parametrize("case_factory", CASE_FACTORIES)
    def test_all_bundled_cases_solve_and_respect_limits(
        self,
        case_factory,
    ):
        build = build_opf(
            case_factory(),
            formulation="ac",
            options=_enabled_options(),
        )
        build.solve()
        results = extract_results(build)

        assert build.prob.status == cp.OPTIMAL
        _assert_all_limits(results, build)
