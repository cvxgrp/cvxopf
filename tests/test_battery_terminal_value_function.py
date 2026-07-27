"""Tests for the fixed-terminal-SoC value-function experiment."""

import pandas as pd
import pytest

from experiments.battery_terminal.scenario import REPRESENTATIVE_WINDOWS
from experiments.battery_terminal.value_function import (
    run_terminal_value_sweep,
)


def _representative_source():
    frames = []
    for spec in REPRESENTATIVE_WINDOWS.values():
        index = pd.date_range(spec.start, spec.end, freq="h")
        frames.append(
            pd.DataFrame(
                {
                    "9q9wtp_solar": 100.0,
                    "9q9wtp_wind": 100.0,
                    "9q9wtp_dist_solar": 10.0,
                    "9q9wtp_load": 1000.0,
                },
                index=index,
            )
        )
    return pd.concat(frames).sort_index()


def test_value_sweep_reports_values_and_secants():
    sweep = run_terminal_value_sweep(
        _representative_source(),
        scenario_names=("low",),
        targets_mwh=(0.0, 50.0, 100.0),
    )

    summary = sweep.summary.loc["low"]
    assert (summary["status"] == "optimal").all()
    assert summary.loc[100.0, "terminal_soc_mwh"] == pytest.approx(100.0)
    assert pd.isna(summary.loc[0.0, "left_secant_cost_per_mwh"])
    assert pd.notna(summary.loc[50.0, "left_secant_cost_per_mwh"])
    assert pd.notna(summary.loc[100.0, "secant_slope_change"])
    assert sweep.runs[("low", 100.0)].decomposition is not None


@pytest.mark.parametrize("targets", [(), (0.0, 0.0), (-1.0,), (1001.0,)])
def test_invalid_targets_are_rejected(targets):
    with pytest.raises(ValueError, match="targets_mwh"):
        run_terminal_value_sweep(
            _representative_source(),
            scenario_names=("low",),
            targets_mwh=targets,
        )
