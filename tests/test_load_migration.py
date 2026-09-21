"""Stage 2 gates for migrating legacy load arithmetic into the adapter."""

import ast
import inspect
import warnings

import numpy as np
import pandas as pd
import pytest

from cvxopf import build_opf, build_opf_multistep, extract_results
from cvxopf import ac_problem, dc_problem, singlenode_dc_problem
from cvxopf.testcases import case9


FORMULATIONS = ("ac", "lossy_dc", "singlenode_dc")


def _renumber_case(case):
    external_ids = np.array([10, 30, 70, 90, 120, 180, 250, 400, 900])
    original_ids = case["bus"][:, 0].astype(int)
    mapping = dict(zip(original_ids, external_ids, strict=True))
    case["bus"][:, 0] = external_ids
    for column in (0, 1):
        case["branch"][:, column] = [
            mapping[int(bus)] for bus in case["branch"][:, column]
        ]
    case["gen"][:, 0] = [mapping[int(bus)] for bus in case["gen"][:, 0]]
    return case, external_ids


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_imported_loads_preserve_external_and_internal_bus_identity(formulation):
    case, external_ids = _renumber_case(case9())
    build = build_opf(case, formulation=formulation)

    np.testing.assert_array_equal(
        build.data["load_bus_external"], external_ids
    )
    np.testing.assert_array_equal(
        build.data["load_device_ids"],
        [f"load_bus_{bus}" for bus in external_ids],
    )
    expected_internal = (
        np.zeros(9, dtype=int)
        if formulation == "singlenode_dc"
        else np.arange(9)
    )
    np.testing.assert_array_equal(
        build.data["load_bus_internal"], expected_internal
    )


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_legacy_multistep_channels_reach_device_level_reporting_exactly(
    formulation,
):
    case = case9()
    p_mw = np.vstack([case["bus"][:, 2], 0.5 * case["bus"][:, 2]])
    q_mvar = np.vstack([case["bus"][:, 3], -0.25 * case["bus"][:, 3]])
    p_mw[1, 4] = -10.0
    columns = [f"legacy_position_{index}" for index in range(9)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf_multistep(
            case,
            pd.DataFrame(p_mw, columns=columns),
            pd.DataFrame(q_mvar, columns=columns),
            T=2,
            formulation=formulation,
        )

    results = extract_results(build)

    np.testing.assert_array_equal(results["p_load"], p_mw)
    np.testing.assert_array_equal(results["q_load"], q_mvar)
    np.testing.assert_array_equal(results["p_load_served"], p_mw)
    if formulation == "ac":
        np.testing.assert_array_equal(results["q_load_served"], q_mvar)
    else:
        assert "q_load_served" not in results
    expected_pd = p_mw / case["baseMVA"]
    if formulation == "singlenode_dc":
        expected_pd = expected_pd.sum(axis=1)
    np.testing.assert_array_equal(build.data["Pd_series"], expected_pd)


@pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc"])
def test_legacy_dc_none_reactive_input_retains_zero_compatibility_channel(
    formulation,
):
    case = case9()
    p_mw = pd.DataFrame([case["bus"][:, 2]])
    build = build_opf_multistep(
        case,
        p_mw,
        None,
        T=1,
        formulation=formulation,
    )

    results = extract_results(build)

    np.testing.assert_array_equal(results["q_load"], np.zeros((1, 9)))


@pytest.mark.parametrize(
    "helper",
    [
        ac_problem._make_step_constraints,
        dc_problem._make_dc_step_constraints,
        singlenode_dc_problem._make_singlenode_dc_step_constraints,
    ],
)
def test_balance_helpers_have_no_formulation_local_load_arithmetic(helper):
    forbidden = {"Pd", "Qd", "Pd_total", "Pd_total_t"}
    assert forbidden.isdisjoint(inspect.signature(helper).parameters)
    names = {
        node.id
        for node in ast.walk(ast.parse(inspect.getsource(helper)))
        if isinstance(node, ast.Name)
    }
    assert forbidden.isdisjoint(names)
