"""Tests for the time-resolution study helpers."""

import numpy as np
import pandas as pd
import pytest

from experiments.battery_terminal.resolution_study import (
    aggregate_power_to_hourly,
    refine_frame_zero_order_hold,
    sample_soc_at_hourly_boundaries,
)


def test_zero_order_hold_preserves_channel_energy():
    index = pd.date_range("2020-01-01", periods=3, freq="h", tz="-08:00")
    frame = pd.DataFrame({"a": [1.0, 2.0, 3.0]}, index=index)

    refined = refine_frame_zero_order_hold(frame, 0.25)

    assert len(refined) == 12
    assert 0.25 * refined["a"].sum() == pytest.approx(frame["a"].sum())
    assert refined.index.to_series().diff().dropna().eq(
        pd.Timedelta(minutes=15)
    ).all()


def test_hourly_aggregation_and_boundary_sampling():
    power = np.array([1.0, 3.0, 2.0, 4.0])
    soc = np.array([9.0, 10.0, 18.0, 20.0])

    assert aggregate_power_to_hourly(power, 0.5).tolist() == [2.0, 3.0]
    assert sample_soc_at_hourly_boundaries(soc, 0.5).tolist() == [
        10.0,
        20.0,
    ]


def test_nondividing_resolution_is_rejected():
    frame = pd.DataFrame(
        {"a": [1.0]},
        index=pd.date_range("2020-01-01", periods=1, freq="h"),
    )

    with pytest.raises(ValueError, match="subdivide"):
        refine_frame_zero_order_hold(frame, 0.3)
