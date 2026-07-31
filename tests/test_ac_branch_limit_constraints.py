"""Stage 3 tests for AC branch thermal operating constraints."""

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf.ac_problem import (
    _BranchTerminalFlow,
    _make_branch_limit_constraints,
)
from cvxopf.problem import OPFOptions, build_opf, build_opf_multistep
from cvxopf.testcases import case9


def _options(**kwargs):
    return OPFOptions(enforce_branch_limits=True, **kwargs)


def _flat_load(steps):
    case = case9()
    return (
        pd.DataFrame(np.tile(case["bus"][:, 2], (steps, 1))),
        pd.DataFrame(np.tile(case["bus"][:, 3], (steps, 1))),
    )


class TestBranchLimitValidation:

    @pytest.mark.parametrize("rating", [-1.0, np.nan, np.inf])
    def test_invalid_in_service_rating_rejected(self, rating):
        case = case9()
        case["branch"][0, 5] = rating

        with pytest.raises(
            ValueError,
            match=r"in-service rateA.*row 0",
        ):
            build_opf(case, formulation="ac", options=_options())

    @pytest.mark.parametrize("rating", [-1.0, np.nan, np.inf])
    def test_invalid_out_of_service_rating_is_inert(self, rating):
        case = case9()
        case["branch"][0, 5] = rating
        case["branch"][0, 10] = 0

        build = build_opf(case, formulation="ac", options=_options())

        assert 0 not in build.data["constrained_branch_indices"]

    @pytest.mark.parametrize("sparsity_tol", [1e-12, 0.1, -1.0])
    def test_enforcement_requires_exact_sparsity(self, sparsity_tol):
        with pytest.raises(
            ValueError,
            match=r"requires sparsity_tol == 0",
        ):
            build_opf(
                case9(),
                formulation="ac",
                options=_options(sparsity_tol=sparsity_tol),
            )

    def test_invalid_rating_remains_inert_when_enforcement_disabled(self):
        case = case9()
        case["branch"][0, 5] = np.inf

        build = build_opf(
            case,
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )

        assert build.data["branch_rate_a_mva"][0] == np.inf
        assert 0 not in build.data["constrained_branch_indices"]

    @pytest.mark.parametrize(
        ("rating_mva", "base_mva", "normalized"),
        [
            (np.nextafter(0.0, 1.0), 100.0, 0.0),
            (1.0, np.nextafter(0.0, 1.0), np.inf),
        ],
    )
    def test_invalid_normalized_rating_rejected(
        self,
        rating_mva,
        base_mva,
        normalized,
    ):
        flow = _BranchTerminalFlow(
            cp.Variable(1),
            cp.Variable(1),
            cp.Variable(1),
            cp.Variable(1),
        )

        with pytest.raises(
            ValueError,
            match=(
                r"row 0: rateA=.* MVA, baseMVA=.*rating_pu="
                + repr(normalized)
            ),
        ):
            _make_branch_limit_constraints(
                flow,
                np.array([0]),
                np.array([rating_mva]),
                base_mva,
            )


class TestBranchLimitStructure:

    def test_single_step_adds_two_inequalities_per_constrained_branch(self):
        disabled = build_opf(
            case9(),
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )
        enabled = build_opf(
            case9(), formulation="ac", options=_options()
        )
        expected = 2 * len(enabled.data["constrained_branch_indices"])

        assert (
            enabled.prob.size_metrics.num_scalar_leq_constr
            - disabled.prob.size_metrics.num_scalar_leq_constr
            == expected
        )

    def test_multistep_adds_two_inequalities_per_branch_and_step(self):
        df_p, df_q = _flat_load(2)
        disabled = build_opf_multistep(
            case9(),
            df_p,
            df_q,
            T=2,
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )
        enabled = build_opf_multistep(
            case9(),
            df_p,
            df_q,
            T=2,
            formulation="ac",
            options=_options(),
        )
        expected = (
            2
            * 2
            * len(enabled.data["constrained_branch_indices"])
        )

        assert (
            enabled.prob.size_metrics.num_scalar_leq_constr
            - disabled.prob.size_metrics.num_scalar_leq_constr
            == expected
        )

    def test_zero_and_inactive_ratings_add_no_constraints(self):
        case = case9()
        case["branch"][:, 5] = 0.0
        case["branch"][0, 5] = 100.0
        case["branch"][0, 10] = 0
        disabled = build_opf(
            case,
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )
        enabled = build_opf(case, formulation="ac", options=_options())

        assert enabled.data["constrained_branch_indices"].size == 0
        assert (
            enabled.prob.size_metrics.num_scalar_leq_constr
            == disabled.prob.size_metrics.num_scalar_leq_constr
        )

    def test_very_large_finite_rating_remains_active(self):
        case = case9()
        case["branch"][:, 5] = 0.0
        case["branch"][0, 5] = 1.0e12
        disabled = build_opf(
            case,
            formulation="ac",
            options=OPFOptions(enforce_branch_limits=False),
        )
        enabled = build_opf(case, formulation="ac", options=_options())

        np.testing.assert_array_equal(
            enabled.data["constrained_branch_indices"], [0]
        )
        assert (
            enabled.prob.size_metrics.num_scalar_leq_constr
            - disabled.prob.size_metrics.num_scalar_leq_constr
            == 2
        )

    def test_branch_variables_do_not_enter_objective(self):
        build = build_opf(
            case9(), formulation="ac", options=_options()
        )
        objective_variables = build.prob.objective.variables()

        for name in (
            "branch_p_from_pu",
            "branch_q_from_pu",
            "branch_p_to_pu",
            "branch_q_to_pu",
        ):
            assert all(
                variable is not build.expressions[name]
                for variable in objective_variables
            )

    def test_constraints_reuse_published_lifted_variables(self):
        build = build_opf(
            case9(), formulation="ac", options=_options()
        )
        inequalities = [
            constraint
            for constraint in build.prob.constraints
            if constraint.__class__.__name__ == "Inequality"
        ]
        expected_uses = len(build.data["constrained_branch_indices"])

        for name in (
            "branch_p_from_pu",
            "branch_q_from_pu",
            "branch_p_to_pu",
            "branch_q_to_pu",
        ):
            published = build.expressions[name]
            actual_uses = sum(
                any(
                    variable is published
                    for variable in constraint.variables()
                )
                for constraint in inequalities
            )
            assert actual_uses == expected_uses

    def test_normalized_constraints_have_unit_right_hand_side(self):
        p_from = cp.Variable(1)
        q_from = cp.Variable(1)
        p_to = cp.Variable(1)
        q_to = cp.Variable(1)
        flow = _BranchTerminalFlow(p_from, q_from, p_to, q_to)
        constraints = _make_branch_limit_constraints(
            flow,
            np.array([0]),
            np.array([200.0]),
            base_mva=100.0,
        )
        p_from.value = np.array([1.0])
        q_from.value = np.array([0.5])
        p_to.value = np.array([-0.75])
        q_to.value = np.array([0.25])

        assert len(constraints) == 2
        assert constraints[0].expr.value == pytest.approx(
            (1.0 / 2.0) ** 2 + (0.5 / 2.0) ** 2 - 1.0
        )
        assert constraints[1].expr.value == pytest.approx(
            (-0.75 / 2.0) ** 2 + (0.25 / 2.0) ** 2 - 1.0
        )
