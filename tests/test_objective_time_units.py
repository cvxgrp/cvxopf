"""Regression tests for the time-integrated objective-units contract."""

import warnings

import numpy as np
import pandas as pd
import pytest

from cvxopf import HVDCLink, StorageUnitIdeal
from cvxopf.problem import OPFOptions, build_opf, build_opf_multistep
from cvxopf.results import extract_results
from cvxopf.testcases import case9


FORMULATIONS = ("ac", "lossy_dc", "singlenode_dc")


def _flat_loads(steps: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    case = case9()
    return (
        pd.DataFrame(np.tile(case["bus"][:, 2], (steps, 1))),
        pd.DataFrame(np.tile(case["bus"][:, 3], (steps, 1))),
    )


@pytest.mark.parametrize("formulation", FORMULATIONS)
@pytest.mark.parametrize("multistep", [False, True])
def test_delta_scales_every_stage_objective(formulation, multistep):
    builds = []
    for delta in (1.0, 0.25):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            if multistep:
                df_p, df_q = _flat_loads(2)
                build = build_opf_multistep(
                    case9(),
                    df_p,
                    df_q if formulation == "ac" else None,
                    T=2,
                    formulation=formulation,
                    delta=delta,
                )
            else:
                build = build_opf(
                    case9(),
                    formulation=formulation,
                    delta=delta,
                )
        build.solve()
        builds.append(build)

    baseline, quarter_hour = (extract_results(build) for build in builds)
    assert baseline["status"] == "optimal"
    assert quarter_hour["status"] == "optimal"
    assert quarter_hour["objective"] == pytest.approx(
        0.25 * baseline["objective"],
        rel=2e-5,
        abs=1e-3,
    )
    np.testing.assert_allclose(
        quarter_hour["Pg"],
        baseline["Pg"],
        rtol=2e-4,
        atol=5e-2,
    )


@pytest.mark.parametrize("formulation", FORMULATIONS)
def test_named_costs_reconstruct_objective_without_terminal_cost(formulation):
    storage = StorageUnitIdeal(
        bus=1,
        apparent_power_rating=20.0,
        capacity=40.0,
        initial_soc=20.0,
        terminal_soc=20.0,
        terminal_constraint="equality",
    )
    hvdc = HVDCLink(
        from_bus=4,
        to_bus=9,
        p_min_mw=-5.0,
        p_max_mw=-5.0,
        cost_coeffs=(0.0, 2.0, 0.0),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        build = build_opf(
            case9(),
            formulation=formulation,
            delta=0.5,
            storage=[storage],
            hvdc=[hvdc],
        )
    build.solve()

    expected_names = {"generator_cost", "storage_cost"}
    if formulation != "singlenode_dc":
        expected_names.add("hvdc_cost")
    if formulation == "lossy_dc":
        expected_names.add("dc_loss_cost")
    named_cost = sum(build.expressions[name].value for name in expected_names)

    assert build.prob.value == pytest.approx(named_cost, rel=1e-7, abs=1e-6)


def test_zero_order_hold_refinement_preserves_soft_terminal_tradeoff():
    storage = StorageUnitIdeal(
        bus=1,
        apparent_power_rating=50.0,
        capacity=100.0,
        initial_soc=0.0,
        aging_weight=0.01,
        terminal_soc=50.0,
        terminal_cost="quadratic",
        terminal_weight=10.0,
    )
    solved = []
    for delta, steps in ((1.0, 2), (0.5, 4)):
        df_p, _ = _flat_loads(steps)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            build = build_opf_multistep(
                case9(),
                df_p,
                None,
                T=steps,
                formulation="singlenode_dc",
                storage=[storage],
                delta=delta,
                options=OPFOptions(loss_weight=0.0),
            )
        build.solve()
        solved.append(extract_results(build))

    hourly, half_hourly = solved
    assert hourly["status"] == "optimal"
    assert half_hourly["status"] == "optimal"
    assert half_hourly["objective"] == pytest.approx(
        hourly["objective"], rel=1e-6, abs=1e-4
    )
    assert half_hourly["soc"][-1, 0] == pytest.approx(
        hourly["soc"][-1, 0], abs=1e-4
    )
    hourly_throughput = np.sum(np.abs(hourly["b"])) * 1.0
    half_hourly_throughput = np.sum(np.abs(half_hourly["b"])) * 0.5
    assert half_hourly_throughput == pytest.approx(
        hourly_throughput, abs=1e-4
    )
