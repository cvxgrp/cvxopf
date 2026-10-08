"""Viewer regressions: hourly coordinates, stable identities and missing data."""

import numpy as np
import pytest

from experiments.case118_tracy_2021.e3_results import (
    HERE,
    LABELS,
    asset_labels,
    channel,
    load_results,
    retained_path,
    summary_table,
    time_coordinates,
    traces,
)


def test_gross_battery_channels_do_not_cancel_and_soc_has_all_boundaries():
    record = {
        "result": {"b": np.array([[1.0, -1.0], [-2.0, 2.0]])},
        "boundary_soc_mwh": np.array([[2.0, 3.0], [1.0, 4.0], [3.0, 2.0]]),
    }
    np.testing.assert_array_equal(channel(record, "charging").sum(axis=1), [1, 2])
    np.testing.assert_array_equal(channel(record, "discharging").sum(axis=1), [1, 2])
    np.testing.assert_array_equal(
        channel(record, "soc_boundary").sum(axis=1), [5, 5, 5]
    )
    np.testing.assert_array_equal(
        channel(record, "soc_percent", np.array([5, 10])),
        [[40, 30], [20, 40], [60, 20]],
    )


def test_missing_reactive_channels_remain_unavailable():
    record = {"result": {"p_flows": np.array([[3.0], [4.0]])}}
    assert channel(record, "b_q") is None
    assert channel(record, "voltage") is None
    assert channel(record, "terminal_sum") is None
    np.testing.assert_array_equal(channel(record, "from_power"), [[3], [4]])


def test_hourly_steps_and_boundary_zoom_are_not_shifted():
    record = {"arm": {"start": 1165}}
    x, y = traces(record, np.array([10, 20]), "Global input hour", (0, 2), False)
    np.testing.assert_array_equal(x, [1165, 1166, 1167])
    np.testing.assert_array_equal(y, [10, 20, 20])
    x, y = traces(record, np.array([10, 20]), "Global input hour", (0, 1), False)
    np.testing.assert_array_equal(x, [1165, 1166])
    np.testing.assert_array_equal(y, [10, 10])  # No next-interval jump at zoom end.
    x, y = traces(record, np.array([3, 2, 4]), "Hour within window", (1, 2), True)
    np.testing.assert_array_equal(x, [1, 2])
    np.testing.assert_array_equal(y, [2, 4])
    dates = time_coordinates(record, 3, "2021 timestamp (fixed UTC−08:00)")
    assert dates[0].isoformat() == "2021-02-18T13:00:00"
    assert dates.tz is None  # Explicit fixed-source clock, no browser DST conversion.


def test_evidence_path_cannot_escape_run(tmp_path):
    assert (
        retained_path(tmp_path, "arm-000/result.json.gz")
        == tmp_path / "arm-000/result.json.gz"
    )
    with pytest.raises(ValueError, match="escapes"):
        retained_path(tmp_path, "../other/result.json.gz")


def test_retained_prepared_e3_inventory_and_asset_identity():
    if not (HERE / "results/e3_prepared/progress.json").exists():
        pytest.skip("owner retained prepared E3 results unavailable")
    data = load_results()
    assert len(data["cases"]) == 5
    assert all(set(case) == set(LABELS) for case in data["cases"].values())
    assert len(summary_table(data)) == 20
    assert len(asset_labels(data, "Generators")) == 54
    assert len(asset_labels(data, "Batteries")) == 27
    assert len(asset_labels(data, "Renewables")) == 119
    assert len(asset_labels(data, "Loads")) == 99
    assert len(asset_labels(data, "Buses")) == 118
    assert len(asset_labels(data, "Branches")) == 186
    assert not summary_table(data)["Cycling warning"].any()
