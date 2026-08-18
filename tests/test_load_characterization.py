"""M19 compatibility gates for legacy and first-class load handling."""

import warnings

import numpy as np
import pandas as pd
import pytest

from cvxopf.problem import build_opf, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import case9


FORMULATIONS = ("ac", "lossy_dc", "singlenode_dc")
FIXED_LOAD_EXPRESSIONS = {"p_load", "q_load", "p_load_served"}
SHEDDING_KEYS = {
    "p_load_shed",
    "q_load_shed",
    "load_shed_fraction",
    "p_load_shed_total",
    "energy_not_served_by_load",
    "energy_not_served",
    "load_shedding_cost",
}
LOAD_METADATA = {
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
CURRENT_RESULT_KEYS = {
    "ac": {
        "status",
        "objective",
        "Pg",
        "Qg",
        "Vm",
        "Va_deg",
        "p_net",
        "q_net",
        "branch_p_from",
        "branch_q_from",
        "branch_p_to",
        "branch_q_to",
        "branch_s_from",
        "branch_s_to",
        "p_load",
        "q_load",
        "p_load_served",
        "q_load_served",
    },
    "lossy_dc": {
        "status", "objective", "Pg", "p_flows", "p_net",
        "p_load", "q_load", "p_load_served",
    },
    "singlenode_dc": {
        "status", "objective", "Pg", "p_net",
        "p_load", "q_load", "p_load_served",
    },
}

BASELINE = {
    ("ac", False): {
        "objective": 2649.56377404,
        "Pg": [89.78042274, 134.37899510, 94.24925668],
        "Qg": [-14.68617051, 2.81067334, -32.24251565],
    },
    ("ac", True): {
        "objective": 4926.94124351,
        "Pg": [
            [89.78042274, 134.37899510, 94.24925668],
            [79.34873362, 121.60065679, 85.34628856],
        ],
        "Qg": [
            [-14.68617051, 2.81067334, -32.24251565],
            [-22.09883761, -2.76879189, -34.03943641],
        ],
    },
    ("lossy_dc", False): {
        "objective": 2608.03266523,
        "Pg": [86.56556811, 134.37690892, 94.05752297],
    },
    ("lossy_dc", True): {
        "objective": 4854.46248273,
        "Pg": [
            [86.56556811, 134.37690892, 94.05752297],
            [76.69729572, 121.60637563, 85.19632866],
        ],
    },
    ("singlenode_dc", False): {
        "objective": 2608.01330387,
        "Pg": [86.56449806, 134.37758530, 94.05791664],
    },
    ("singlenode_dc", True): {
        "objective": 4854.42725038,
        "Pg": [
            [86.56449807, 134.37758527, 94.05791666],
            [76.69631454, 121.60699484, 85.19669061],
        ],
    },
}

SIGNED_ACTIVE_BASELINE = {
    ("ac", False): {
        "objective": 1594.45155032,
        "Pg": [56.52118998, 94.33520424, 66.03772138],
    },
    ("ac", True): {
        "objective": 5926.48387045,
        "Pg": [
            [89.79298019, 134.35588094, 94.22613552],
            [59.77500850, 98.31842786, 68.83057507],
            [56.52118998, 94.33520424, 66.03772138],
        ],
    },
    ("lossy_dc", False): {
        "objective": 1578.11538993,
        "Pg": [55.23742356, 93.83584480, 65.92673164],
    },
    ("lossy_dc", True): {
        "objective": 5851.74730934,
        "Pg": [
            [86.56556812, 134.37690891, 94.05752297],
            [58.37023801, 97.88995121, 68.73981078],
            [55.23742356, 93.83584480, 65.92673164],
        ],
    },
    ("singlenode_dc", False): {
        "objective": 1578.10544378,
        "Pg": [55.23693134, 93.83602836, 65.92704030],
    },
    ("singlenode_dc", True): {
        "objective": 5851.70783189,
        "Pg": [
            [86.56449808, 134.37758525, 94.05791667],
            [58.36968804, 97.89018401, 68.74012795],
            [55.23693138, 93.83602832, 65.92704031],
        ],
    },
}


def _case_with_negative_reactive_load():
    case = case9()
    case["bus"][4, 3] = -5.0
    return case


def _legacy_frames(case):
    p = case["bus"][:, 2]
    q = case["bus"][:, 3]
    columns = [f"positional_{i}" for i in range(case["bus"].shape[0])]
    return (
        pd.DataFrame(np.vstack([p, 0.9 * p]), columns=columns),
        pd.DataFrame(np.vstack([q, 0.9 * q]), columns=columns),
    )


def _signed_active_inputs():
    static_case = case9()
    static_case["bus"][4, 2] = -10.0
    static_case["bus"][4, 3] = 0.0

    multistep_case = case9()
    multistep_case["bus"][4, 3] = 0.0
    p = np.tile(multistep_case["bus"][:, 2], (3, 1))
    q = np.tile(multistep_case["bus"][:, 3], (3, 1))
    p[:, 4] = [90.0, 0.0, -10.0]
    q[:, 4] = 0.0
    columns = [f"signed_position_{i}" for i in range(9)]
    return (
        static_case,
        multistep_case,
        pd.DataFrame(p, columns=columns),
        pd.DataFrame(q, columns=columns),
    )


def _build(formulation, multistep):
    case = _case_with_negative_reactive_load()
    if not multistep:
        return build_opf(case, formulation=formulation, delta=0.5)
    df_p, df_q = _legacy_frames(case)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        return build_opf_multistep(
            case,
            df_p,
            df_q,
            T=2,
            formulation=formulation,
            delta=0.5,
        )


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("multistep", [False, True])
def test_m19_s2_load_schema(formulation, multistep):
    build = _build(formulation, multistep)

    assert SHEDDING_KEYS.isdisjoint(build.variables)
    assert SHEDDING_KEYS.isdisjoint(build.expressions)
    expected_expressions = set(FIXED_LOAD_EXPRESSIONS)
    if formulation == "ac":
        expected_expressions.add("q_load_served")
    assert expected_expressions <= set(build.expressions)
    assert LOAD_METADATA <= set(build.data)
    assert build.data["nload"] == 9
    assert build.data["nsheddable"] == 0
    np.testing.assert_array_equal(
        build.data["load_device_ids"],
        [f"load_bus_{bus}" for bus in range(1, 10)],
    )
    if multistep:
        assert "Pd" not in build.data
        assert "Pd_total" not in build.data
        assert "Pd_series" in build.data
        expected_shape = (2,) if formulation == "singlenode_dc" else (2, 9)
        assert build.data["Pd_series"].shape == expected_shape
        if formulation == "ac":
            assert build.data["Qd_series"].shape == (2, 9)
        else:
            assert "Qd_series" not in build.data
    elif formulation == "singlenode_dc":
        assert isinstance(build.data["Pd_total"], float)
        assert "Pd" not in build.data
        assert "Qd" not in build.data
    else:
        assert build.data["Pd"].shape == (9,)
        if formulation == "ac":
            assert build.data["Qd"].shape == (9,)
        else:
            assert "Qd" not in build.data


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("multistep", [False, True])
def test_s2_preserves_pre_m19_numerical_and_balance_baseline(
    formulation, multistep
):
    build = _build(formulation, multistep)
    build.solve()
    results = extract_results(build)
    expected = BASELINE[(formulation, multistep)]

    assert results["status"] == "optimal"
    assert results["objective"] == pytest.approx(
        expected["objective"], rel=1e-5, abs=1e-3
    )
    np.testing.assert_allclose(
        results["Pg"], expected["Pg"], rtol=1e-4, atol=5e-2
    )
    assert set(results) == CURRENT_RESULT_KEYS[formulation]
    assert SHEDDING_KEYS.isdisjoint(results)
    expected_p = (
        np.asarray([expression.value for expression in build.expressions["p_load"]])
        if multistep
        else build.expressions["p_load"].value
    )
    np.testing.assert_array_equal(results["p_load"], expected_p)
    np.testing.assert_array_equal(results["p_load_served"], expected_p)
    if formulation == "ac":
        np.testing.assert_allclose(
            results["Qg"], expected["Qg"], rtol=1e-4, atol=2.5e-1
        )

    steps = range(2) if multistep else (None,)
    for step in steps:
        pg = build.variables["Pg"][step].value if multistep else (
            build.variables["Pg"].value
        )
        if formulation == "ac":
            qg = build.variables["Qg"][step].value if multistep else (
                build.variables["Qg"].value
            )
            p = build.variables["p"][step].value if multistep else (
                build.variables["p"].value
            )
            q = build.variables["q"][step].value if multistep else (
                build.variables["q"].value
            )
            pd = (
                build.data["Pd_series"][step]
                if multistep
                else build.data["Pd"]
            )
            qd = (
                build.data["Qd_series"][step]
                if multistep
                else build.data["Qd"]
            )
            np.testing.assert_allclose(
                p, build.data["Cg"] @ pg - pd, atol=1e-6
            )
            np.testing.assert_allclose(
                q, build.data["Cg"] @ qg - qd, atol=1e-6
            )
        elif formulation == "lossy_dc":
            p_flows = (
                build.variables["p_flows"][step].value
                if multistep
                else build.variables["p_flows"].value
            )
            pd = (
                build.data["Pd_series"][step]
                if multistep
                else build.data["Pd"]
            )
            np.testing.assert_allclose(
                build.data["A"] @ p_flows
                + build.data["Cg"] @ pg
                - pd,
                0.0,
                atol=1e-7,
            )
        else:
            pd_total = (
                build.data["Pd_series"][step]
                if multistep
                else build.data["Pd_total"]
            )
            assert np.sum(pg) == pytest.approx(
                pd_total, abs=1e-7
            )


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("multistep", [False, True])
def test_signed_active_load_numerical_baseline(formulation, multistep):
    static_case, multistep_case, df_p, df_q = _signed_active_inputs()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = (
            build_opf_multistep(
                multistep_case,
                df_p,
                df_q,
                T=3,
                formulation=formulation,
                delta=0.5,
            )
            if multistep
            else build_opf(
                static_case, formulation=formulation, delta=0.5
            )
        )
        build.solve()
    results = extract_results(build)
    expected = SIGNED_ACTIVE_BASELINE[(formulation, multistep)]

    assert results["status"] == "optimal"
    assert results["objective"] == pytest.approx(
        expected["objective"], rel=1e-5, abs=1e-3
    )
    np.testing.assert_allclose(
        results["Pg"], expected["Pg"], rtol=1e-4, atol=5e-2
    )

    if formulation == "singlenode_dc":
        expected_demand = np.array([3.15, 2.25, 2.15])
        if multistep:
            np.testing.assert_allclose(
                build.data["Pd_series"], expected_demand
            )
        else:
            assert build.data["Pd_total"] == pytest.approx(
                expected_demand[-1]
            )
    elif multistep:
        np.testing.assert_allclose(
            build.data["Pd_series"][:, 4], [0.9, 0.0, -0.1]
        )
        np.testing.assert_allclose(
            np.asarray(results["p_net"])[:, 4],
            [-90.0, 0.0, 10.0],
            atol=1e-7,
        )
    else:
        assert build.data["Pd"][4] == pytest.approx(-0.1)
        assert results["p_net"][4] == pytest.approx(10.0, abs=1e-7)


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_intentional_multistep_t1_load_schema_and_objective(formulation):
    case = _case_with_negative_reactive_load()
    df_p = pd.DataFrame([case["bus"][:, 2]])
    df_q = pd.DataFrame([case["bus"][:, 3]])

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        single = build_opf(case, formulation=formulation, delta=0.5)
        multi = build_opf_multistep(
            case,
            df_p,
            df_q,
            T=1,
            formulation=formulation,
            delta=0.5,
        )
        single.solve()
        multi.solve()
    single_results = extract_results(single)
    multi_results = extract_results(multi)

    expected_pd_shape = (
        (1,) if formulation == "singlenode_dc" else (1, 9)
    )
    assert multi.data["Pd_series"].shape == expected_pd_shape
    if formulation == "ac":
        assert multi.data["Qd_series"].shape == (1, 9)
    assert isinstance(multi.variables["Pg"], list)
    assert len(multi.variables["Pg"]) == 1
    assert np.asarray(multi_results["Pg"]).shape == (1, 3)
    assert np.asarray(multi_results["p_net"]).shape == expected_pd_shape
    if formulation == "ac":
        assert np.asarray(multi_results["Qg"]).shape == (1, 3)
        assert np.asarray(multi_results["q_net"]).shape == (1, 9)
    assert multi_results["objective"] == pytest.approx(
        single_results["objective"], rel=1e-5, abs=1e-3
    )


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_legacy_positional_columns_and_zero_static_load(formulation):
    case = case9()
    assert case["bus"][0, 2] == 0.0
    p_values = np.vstack([
        np.arange(1.0, 10.0),
        np.arange(11.0, 20.0),
    ])
    q_values = -0.25 * p_values
    columns = [f"not_a_bus_id_{i}" for i in range(9)]
    df_p = pd.DataFrame(p_values, columns=columns)
    df_q = pd.DataFrame(q_values, columns=columns)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case,
            df_p,
            df_q,
            T=2,
            formulation=formulation,
        )

    if formulation == "singlenode_dc":
        np.testing.assert_allclose(
            build.data["Pd_series"],
            p_values.sum(axis=1) / case["baseMVA"],
        )
    else:
        np.testing.assert_allclose(
            build.data["Pd_series"], p_values / case["baseMVA"]
        )
    if formulation == "ac":
        np.testing.assert_allclose(
            build.data["Qd_series"], q_values / case["baseMVA"]
        )


def test_unsuccessful_result_retains_exogenous_and_fixed_served_load():
    case = case9()
    case["bus"][:, 2] *= 100.0
    build = build_opf(case, formulation="singlenode_dc")

    build.solve()
    results = extract_results(build)

    assert results["status"] == "infeasible"
    assert np.isnan(results["objective"])
    assert results["Pg"] is None
    assert results["p_net"] is None
    np.testing.assert_array_equal(results["p_load"], case["bus"][:, 2])
    np.testing.assert_array_equal(results["q_load"], case["bus"][:, 3])
    np.testing.assert_array_equal(
        results["p_load_served"], case["bus"][:, 2]
    )
    assert build.data["Pd_total"] == pytest.approx(315.0)
    assert SHEDDING_KEYS.isdisjoint(results)
