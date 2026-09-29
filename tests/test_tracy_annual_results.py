"""Descriptive annual analysis: calendar and retained-data consistency."""

import numpy as np
import pytest

from experiments.case118_tracy_2021.annual_results import (
    HERE,
    aggregate,
    calendar,
    comparison,
    load_results,
    summaries,
)


def test_calendar_fixed_hour_day_layout():
    grid = calendar(np.arange(8760))
    assert grid.shape == (24, 365)
    assert grid[0, 0] == 0 and grid[23, 0] == 23
    assert grid[0, 1] == 24 and grid[23, -1] == 8759
    with pytest.raises(ValueError, match="8760"):
        calendar(np.arange(8761))


def test_aggregate_soc_is_interval_start_and_gross_device_throughput():
    # Simultaneously charging/discharging different batteries must not cancel
    # when reporting gross fleet charging and discharging.
    record = dict(
        boundary_soc_mwh=np.array([[2.0, 3.0], [1.0, 4.0], [2.0, 3.0]]),
        result={
            "Pg": np.zeros((2, 1)),
            "p_nd": np.zeros((2, 1)),
            "curtailment": np.zeros((2, 1)),
            "b": np.array([[1.0, -1.0], [-1.0, 1.0]]),
            "p_load_shed_total": np.zeros(2),
            "p_load_served": np.zeros((2, 1)),
        },
    )
    a = aggregate(record)
    np.testing.assert_array_equal(a["Battery power (MW; + discharge)"], [0, 0])
    np.testing.assert_array_equal(a["Battery charging (MW)"], [1, 1])
    np.testing.assert_array_equal(a["Battery discharging (MW)"], [1, 1])
    np.testing.assert_array_equal(a["SoC at interval start (MWh)"], [5, 5])


def test_retained_annual_monthly_and_difference_consistency():
    if not (HERE / "results/stage_c/analysis.json").exists():
        pytest.skip("owner retained annual results unavailable")
    data = load_results()
    annual, monthly = summaries(data)
    assert len(annual) == 2 and len(monthly) == 24
    for f, group in monthly.groupby("Formulation"):
        row = annual.loc[annual.formulation == f].iloc[0]
        assert group["Dispatchable generation (MWh)"].sum() == pytest.approx(
            row.generation_mwh
        )
        assert group["Curtailment (MWh)"].sum() == pytest.approx(row.curtailed_mwh)
        assert group["Generator cost"].sum() == pytest.approx(row.generator_cost)
        assert group["SoC start (MWh)"].iloc[0] == pytest.approx(
            group["SoC end (MWh)"].iloc[-1]
        )
        np.testing.assert_allclose(
            group["SoC end (MWh)"].iloc[:-1], group["SoC start (MWh)"].iloc[1:]
        )
    c = comparison(data)
    assert c["trajectories"]["Dispatchable generation (MW)"]["mean_difference"] > 0
    assert 0 <= c["congestion"]["hours_any_branch"] <= 8760
