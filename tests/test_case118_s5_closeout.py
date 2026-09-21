"""Scientific checks for closeout units, calendars, and retained input identity."""
import hashlib
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from experiments.case118_annual_hierarchy.s5_closeout import (
    calendar_matrix, input_summary, shortfall_events,
)


def test_calendar_orientation_and_rejection():
    matrix = calendar_matrix(np.arange(8760))
    assert matrix.shape == (24, 365)
    assert matrix[6, 2] == 54
    assert matrix[23, 364] == 8759
    with pytest.raises(ValueError):
        calendar_matrix(np.zeros(8784))


def test_events_do_not_wrap_and_integrate_hourly_deficit():
    index = pd.date_range("2025-01-01", periods=6, freq="h", tz="UTC")
    events = shortfall_events([11, 12, 10, 9, 14, 15], 10, index)
    assert [e["hours"] for e in events] == [2, 2]
    assert [e["energy_mwh"] for e in events] == [3, 9]
    assert [e["peak_mw"] for e in events] == [2, 5]
    assert pd.Timestamp(events[-1]["stop_exclusive"]) == index[-1] + pd.Timedelta(hours=1)


def test_summary_uses_coincident_net_and_not_difference_of_maxima():
    index = pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")
    frame = pd.DataFrame(dict(load_mw=[10, 8, 7], available_nd_mw=[8, 1, 1],
                              net_load_mw=[2, 7, 6], solar_mw=[8, 1, 1], wind_mw=[0, 0, 0]), index=index)
    summary = input_summary(frame, 6)
    assert summary["net_load_mw_max"] == 7
    assert summary["shortfall_energy_mwh"] == 1
    assert summary["load_mwh"] == 25
    assert summary["renewable_to_load_energy"] == .4


def test_saved_final_input_arrays_match_historical_digests():
    # Independently encode the historical array digest, without calling the builder.
    package = Path(__file__).parents[1] / "experiments/case118_annual_hierarchy/s5_closeout"
    expected = {
        "load_p_mw": "2f1845f158ed5149b36cb5587d579c09d821d475e02285d2e348f6521bf27764",
        "load_q_mvar": "e11d9fe44698056270896f6134735d5f1300da774b65b10bbb942ac5f4baf90b",
        "available_nd_mw": "529e2cd33f57c16b9ca5702d2c023e4739d1fedf43d1134456cd1a9ab2c4245b",
    }
    with np.load(package / "final_inputs.npz", allow_pickle=False) as saved:
        for key, digest in expected.items():
            value = np.ascontiguousarray(saved[key], dtype="<f8")
            encoded = f"float64-le|shape={value.shape}|".encode() + value.tobytes()
            assert hashlib.sha256(encoded).hexdigest() == digest
        aggregate = pd.read_csv(package / "aggregate_inputs.csv", float_precision="round_trip")
        np.testing.assert_array_equal(aggregate.load_mw, saved["load_p_mw"].sum(1))
        np.testing.assert_array_equal(aggregate.available_nd_mw, saved["available_nd_mw"].sum(1))
        np.testing.assert_allclose(aggregate.net_load_mw, aggregate.load_mw-aggregate.available_nd_mw, atol=1e-12, rtol=0)
