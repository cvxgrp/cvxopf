"""Descriptive sensitivity metrics must not hide cancellation or zero baselines."""

import numpy as np
import pandas as pd
import pytest

from experiments.case118_tracy_2021.e3_results import HERE, load_scenarios
from experiments.case118_tracy_2021.e3_sensitivity import (
    percentage,
    sensitivity_tables,
    trajectory_difference,
)


def test_opposing_asset_differences_disappear_only_in_fleet_metric():
    metrics = trajectory_difference(
        np.array([[1.0, -1.0], [2.0, -2.0]]), np.zeros((2, 2)), np.array([2.0, 4.0])
    )
    assert metrics["Fleet peak difference (MW)"] == 0
    assert metrics["Asset peak difference (MW)"] == 2
    assert metrics["Asset absolute trajectory difference (MWh)"] == 6
    assert metrics["Difference hidden by fleet aggregation (%)"] == 100
    assert metrics["Asset peak / own rating (%)"] == 100


def test_small_reference_is_undefined_not_a_huge_percentage():
    assert np.isnan(percentage(100, 1e-9))
    assert percentage(-10, 100) == -10
    metrics = trajectory_difference(np.zeros((2, 2)), np.zeros((2, 2)), np.zeros(2))
    assert np.isnan(metrics["Asset peak / own rating (%)"])
    assert np.isnan(metrics["Difference hidden by fleet aggregation (%)"])


def test_matching_horizon_totals_do_not_imply_matching_trajectories():
    metrics = trajectory_difference(
        np.array([[2.0], [0.0]]), np.array([[0.0], [2.0]]), np.array([2.0])
    )
    assert metrics["Fleet absolute trajectory difference (MWh)"] == 4
    assert metrics["Fleet peak / total rating (%)"] == 100
    assert metrics["Difference hidden by fleet aggregation (%)"] == 0


def test_tables_integrate_throughput_and_keep_operating_conditions_input_based():
    result = dict(
        Pg=np.array([[2.0], [0.0]]),
        b=np.array([[1.0], [-1.0]]),
        p_nd=np.zeros((2, 1)),
        curtailment=np.ones((2, 1)),
        p_load=np.ones((2, 1)) * 4,
        p_load_served=np.ones((2, 1)) * 2,
        p_load_shed=np.ones((2, 1)) * 2,
        p_net=np.zeros((2, 1)),
        objective=10.0,
        branch_s_from=np.ones((2, 1)),
        branch_s_to=np.ones((2, 1)),
    )
    record = dict(
        result=result,
        renewable_available_mw=np.ones((2, 1)),
        arm=dict(initial_soc_mwh=[2.0], terminal_soc_mwh=[2.0], start=100),
        input_identities=dict(loads=["load"]),
        relaxation_audit=dict(edge_gaps=dict(relative_summary=dict(maximum=0.2))),
    )
    other = {
        **record,
        "result": {**result, "Pg": np.array([[0.0], [2.0]]), "b": np.zeros((2, 1))},
    }
    data = dict(
        cases={"Test": {"ac": record, "socp": other}},
        tables={
            "generators": pd.DataFrame(dict(device_id=["gen"], pmax_mw=[2.0])),
            "batteries": pd.DataFrame(
                dict(device_id=["battery"], apparent_power_rating_mva=[1.0])
            ),
            "renewables": pd.DataFrame(
                dict(device_id=["renewable"], apparent_power_rating_mva=[1.0])
            ),
        },
        branches=np.array([[1.0, 2.0, 0.0, 0.0, 0.0, 10.0]]),
    )
    tables = sensitivity_tables(data)
    totals = tables["totals"].set_index("Quantity")
    assert totals.loc["Conventional generation", "Signed difference"] == 0
    assert totals.loc["Battery throughput", "Signed difference"] == -2
    assert (
        tables["conditions"].iloc[0]["Resource-deficit hours (before storage/network)"]
        == 2
    )
    assert set(tables["hours"]["Global input hour"]) == {100, 101}
    pd.testing.assert_frame_equal(
        tables["conditions"], sensitivity_tables(data, "socp")["conditions"]
    )


def test_six_scenario_sensitivity_retains_shedding_and_asset_disagreement():
    if not all(
        (HERE / f"results/{name}/progress.json").exists()
        for name in ("e3_prepared", "e3_plus")
    ):
        pytest.skip("owner retained E3/E3+ results unavailable")
    tables = sensitivity_tables(load_scenarios())
    assert len(tables["conditions"]) == 6
    assert len(tables["totals"]) == 6 * 3 * 7
    assert len(tables["profiles"]) == 6 * 3 * 4
    served = tables["totals"].query("Quantity == 'Served energy'")
    ordinary = served[~served["Scenario"].str.startswith("Forced shedding")]
    assert ordinary["Signed difference"].abs().max() < 1e-4
    forced = served[served["Scenario"].str.startswith("Forced shedding")]
    assert forced.set_index("Model").loc["socp", "Signed difference"] == pytest.approx(
        1405.5061605
    )
