"""Stage 2 tests for AC branch-terminal reporting without enforcement."""

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest
from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles
from cvxpy.reductions.solvers.nlp_solving_chain import _build_nlp_chain
from scipy import sparse

from cvxopf._cvxpy_dispatch import sparse_dispatch_policy
from cvxopf.ac_problem import _make_branch_terminal_flow
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
    from_power, to_power = _complex_terminal_power(admittance, voltage, angle)
    scale = build.data["baseMVA"]
    return scale * from_power, scale * to_power


def _complex_terminal_power(admittance, voltage, angle):
    complex_voltage = voltage * np.exp(1j * angle)
    from_voltage = complex_voltage[admittance.from_bus]
    to_voltage = complex_voltage[admittance.to_bus]
    coefficients = (admittance.yff, admittance.yft, admittance.ytt, admittance.ytf)
    yff, yft, ytt, ytf = (
        coefficients if voltage.ndim == 1 else tuple(a[:, None] for a in coefficients)
    )
    from_current = (
        yff * from_voltage
        + yft * to_voltage
    )
    to_current = (
        ytf * from_voltage
        + ytt * to_voltage
    )
    return from_voltage * np.conj(from_current), to_voltage * np.conj(to_current)


def _branch_batch_case():
    case = case9()
    branch = case["branch"]
    branch[1, 8:10] = (1.07, 11.0)  # tap and phase shift on a charged line
    branch[2, 10] = 0  # interior offline row with an otherwise positive rating
    branch[3, 5] = 0  # online but unrated
    parallel = branch[1].copy()
    parallel[:2] = parallel[:2][::-1]
    parallel[8:10] = (0.95, -7.0)
    case["branch"] = np.vstack((branch, parallel))
    return case


def _terminal_state(nb, steps):
    bus = np.arange(nb)[:, None]
    step = np.arange(steps)
    return 0.97 + 0.006 * bus + 0.01 * step, 0.01 * bus - 0.004 * bus * step


def _terminal_channels(f, t):
    return f.real, f.imag, t.real, t.imag


class TestACBranchBatching:

    @pytest.mark.parametrize("assembly,steps", [
        ("single", 1), ("stepwise", 2), ("vectorized", 1), ("vectorized", 2),
    ])
    @pytest.mark.parametrize("sparse_pq", [True, False])
    @pytest.mark.parametrize("vectorize_pq", [True, False])
    def test_public_definitions_and_reporting_at_assigned_states(
        self, assembly, steps, sparse_pq, vectorize_pq,
    ):
        case = _branch_batch_case()
        options = OPFOptions(sparse_pq=sparse_pq, vectorize_pq=vectorize_pq)
        if assembly == "single":
            build = build_opf(case, options=options)
        else:
            df_p, df_q = _flat_case9_load(steps)
            build = build_opf_multistep(
                case, df_p, df_q, T=steps, options=options, temporal_assembly=assembly,
            )
        internal, _ = reindex_case_to_consecutive(case)
        admittance = make_branch_admittance(internal)
        np.testing.assert_array_equal(
            build.data["constrained_branch_indices"],
            np.flatnonzero(admittance.status & (admittance.rate_a_mva > 0)),
        )
        voltage, angle = _terminal_state(build.data["nb"], steps)
        from_power, to_power = _complex_terminal_power(admittance, voltage, angle)

        for name, state in (("v", voltage), ("theta", angle)):
            variables = build.variables[name]
            if assembly == "stepwise":
                for hour, var in enumerate(variables):
                    var.value = state[:, hour:hour + 1]
            else:
                variables.value = state

        for name, power in zip(
            ("branch_p_from_pu", "branch_q_from_pu", "branch_p_to_pu", "branch_q_to_pu"),
            _terminal_channels(from_power, to_power), strict=True,
        ):
            expressions = build.expressions[name]
            if assembly == "stepwise":
                pairs = zip(expressions, power.T, strict=True)
            else:
                pairs = [(expressions, power if assembly == "vectorized" else power[:, 0])]
            for variable, expected in pairs:
                (definition,) = (
                    c for c in build.prob.constraints if c.args[0] is variable
                )
                np.testing.assert_allclose(definition.args[1].value, expected, atol=1e-12, rtol=1e-12)
                np.testing.assert_array_equal(definition.args[1].value[~admittance.status], 0.0)
                variable.value = expected + 0.125
                np.testing.assert_allclose(definition.expr.value, 0.125, atol=1e-12, rtol=0)
                variable.value = expected

        results = extract_results(build)
        for name, expected in zip(
            ("branch_p_from", "branch_q_from", "branch_p_to", "branch_q_to",
             "branch_s_from", "branch_s_to"),
            (*_terminal_channels(from_power, to_power), np.abs(from_power), np.abs(to_power)),
            strict=True,
        ):
            output = expected[:, 0] if assembly == "single" else expected.T
            np.testing.assert_allclose(results[name], case["baseMVA"] * output, atol=1e-9, rtol=1e-12)

    def test_thermal_slack_uses_the_original_row_mask_and_both_lifted_ends(self):
        build = build_opf(_branch_batch_case())
        from_p = build.expressions["branch_p_from_pu"]
        to_p = build.expressions["branch_p_to_pu"]
        from_q = build.expressions["branch_q_from_pu"]
        to_q = build.expressions["branch_q_to_pu"]
        rates = build.data["branch_rate_a_mva"] / build.data["baseMVA"]
        from_factor = np.where(np.arange(len(rates)) % 2 == 0, 0.5, 1.5)
        to_factor = 2.0 - from_factor
        from_p.value, to_p.value = rates * from_factor, rates * to_factor
        from_q.value = to_q.value = np.zeros_like(rates)
        lift_ids = {expr.id for expr in (from_p, from_q, to_p, to_q)}
        modeled_slack = [
            -c.expr.value for c in build.prob.constraints
            if isinstance(c, cp.constraints.Inequality) and any(v.id in lift_ids for v in c.variables())
        ]
        active_rated = build.data["branch_status"] & (rates > 0)
        expected = 1.0 - np.column_stack((from_factor, to_factor))[active_rated]**2
        np.testing.assert_allclose(modeled_slack, expected.ravel(), atol=1e-12, rtol=0)

    @pytest.mark.parametrize("horizon", [None, 2])
    @pytest.mark.parametrize("has_active", [False, True])
    def test_zero_or_one_active_row(self, horizon, has_active):
        case = _branch_batch_case()
        case["branch"][:, 10] = 0
        if has_active:
            case["branch"][1, 10] = 1
        internal, _ = reindex_case_to_consecutive(case)
        admittance = make_branch_admittance(internal)
        voltage, angle = _terminal_state(len(case["bus"]), horizon or 1)
        v, theta = cp.Variable(voltage.shape), cp.Variable(angle.shape)
        _, definitions = _make_branch_terminal_flow(theta, v, admittance, suffix="", horizon_steps=horizon)
        if has_active:
            v.value, theta.value = voltage, angle
        expected = _terminal_channels(*_complex_terminal_power(admittance, voltage, angle))
        for constraint, channel in zip(definitions, expected, strict=True):
            direct = constraint.args[1]
            if not has_active:
                assert direct.is_constant()
            np.testing.assert_array_equal(direct.value[~admittance.status], 0.0)
            np.testing.assert_allclose(direct.value, channel[:, 0] if horizon is None else channel,
                                       atol=1e-12, rtol=1e-12)

    @pytest.mark.parametrize("horizon", [None, 2])
    def test_compiled_derivatives_against_complex_reference(self, horizon):
        internal, _ = reindex_case_to_consecutive(_branch_batch_case())
        admittance = make_branch_admittance(internal)
        voltage, angle = _terminal_state(len(internal["bus"]), horizon or 1)
        v, theta = cp.Variable(voltage.shape), cp.Variable(angle.shape)
        v.value, theta.value = voltage, angle

        with sparse_dispatch_policy(False):
            flow, definitions = _make_branch_terminal_flow(
                theta, v, admittance, suffix="", horizon_steps=horizon,
            )
            lifts = (flow.p_from, flow.q_from, flow.p_to, flow.q_to)
            for i, lift in enumerate(lifts):
                lift.value = np.full(lift.shape, 0.1 * (i + 1))
            problem = cp.Problem(cp.Minimize(0), definitions)
            chain, _ = _build_nlp_chain(problem, cp.IPOPT, {})
            data, inverse = chain.apply(problem)
            oracle = Oracles(data["problem"], verbose=False)

        x0 = data["x0"]
        n, m = x0.size, len(data["cl"])

        def value(x, var):
            start = inverse[-1].var_offsets[var.id]
            return x[start:start + var.size].reshape(var.shape, order="F")

        def residual(x):
            expected = _terminal_channels(*_complex_terminal_power(
                admittance, value(x, v), value(x, theta),
            ))
            return np.concatenate([
                (value(x, lift) - (channel[:, 0] if horizon is None else channel)).ravel(order="F")
                for lift, channel in zip(lifts, expected, strict=True)
            ])

        rng = np.random.default_rng(113)
        direction, probe = rng.normal(size=(2, n))
        dual = rng.normal(size=m)
        oracle.objective(x0)
        np.testing.assert_allclose(oracle.constraints(x0), residual(x0), atol=1e-12, rtol=1e-12)
        jac = sparse.coo_matrix((oracle.jacobian(x0), oracle.jacobianstructure()), shape=(m, n))
        eps = 1e-6
        expected_jac_action = (residual(x0 + eps * direction) - residual(x0 - eps * direction)) / (2 * eps)
        np.testing.assert_allclose(jac @ direction, expected_jac_action, atol=1e-7, rtol=1e-8)

        lower = sparse.coo_matrix((oracle.hessian(x0, dual, 0.0), oracle.hessianstructure()), shape=(n, n))
        hess = lower + lower.T - sparse.diags(lower.diagonal())
        eps = 1e-4
        plus, minus = eps * (direction + probe), eps * (direction - probe)
        mixed = (residual(x0 + plus) + residual(x0 - plus)
                 - residual(x0 + minus) - residual(x0 - minus)) / (4 * eps**2)
        np.testing.assert_allclose(probe @ (hess @ direction), dual @ mixed, atol=1e-5, rtol=1e-7)


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

    def test_private_empty_terminal_flow_path_remains_defensive(self):
        generator = DispatchableGenerator(
            bus=1,
            p_max_mw=100.0,
            q_min_mvar=-100.0,
            q_max_mvar=100.0,
            cost_coeffs=(0.0, 1.0),
        )
        case = make_singlenode_case(50.0, [generator])
        reindexed, _ = reindex_case_to_consecutive(case)
        admittance = make_branch_admittance(reindexed)
        theta = cp.Variable((1, 1))
        voltage = cp.Variable((1, 1))

        flow, constraints = _make_branch_terminal_flow(
            theta,
            voltage,
            admittance,
            suffix="",
        )

        assert constraints == []
        for expression in (
            flow.p_from,
            flow.q_from,
            flow.p_to,
            flow.q_to,
        ):
            assert isinstance(expression, cp.Constant)
            assert expression.shape == (0,)

    def test_already_zero_based_case_retains_none_reindex_mapping(self):
        reindexed, _ = reindex_case_to_consecutive(case9())

        build = build_opf(reindexed, formulation="ac")

        assert build.data["ext_to_int"] is None
        np.testing.assert_array_equal(
            build.data["branch_from_bus_external"],
            reindexed["branch"][:, 0],
        )

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
            case9(), df_p, df_q, T=2, formulation="ac", temporal_assembly="stepwise"
        )

        for name in BRANCH_EXPRESSION_KEYS:
            expressions = build.expressions[name]
            assert isinstance(expressions, list)
            assert len(expressions) == 2
            assert all(isinstance(item, cp.Variable) for item in expressions)
            assert all(
                item.shape == (build.data["nl"],) for item in expressions
            )

    def test_single_step_rejects_zero_branch_table(self):
        generator = DispatchableGenerator(
            bus=1,
            p_max_mw=100.0,
            q_min_mvar=-100.0,
            q_max_mvar=100.0,
            cost_coeffs=(0.0, 1.0),
        )
        case = make_singlenode_case(50.0, [generator])

        with pytest.raises(
            ValueError,
            match=r"Branchless AC cases are unsupported.*singlenode_dc",
        ):
            build_opf(case, formulation="ac")


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

    def test_multistep_rejects_zero_branch_table(self):
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
        with pytest.raises(
            ValueError,
            match=r"Branchless AC cases are unsupported.*singlenode_dc",
        ):
            build_opf_multistep(
                case, df_p, df_q, T=2, formulation="ac"
            )

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
