"""Tests for terminal-weight and nested-horizon experiment studies."""

from types import SimpleNamespace

import pandas as pd
import pytest

from experiments.battery_terminal.horizon_study import run_horizon_study
from experiments.battery_terminal.reproduce import _scenario_input_table
from experiments.battery_terminal.scenario import REPRESENTATIVE_WINDOWS
from experiments.battery_terminal.soft_weights import run_soft_weight_sweep


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


def test_scenario_input_table_retains_each_scenario_once():
    index = pd.date_range("2024-01-01", periods=2, freq="h")
    scenario = SimpleNamespace(
        df_P=pd.DataFrame({1: [10.0, 20.0], 2: [1.0, 2.0]}, index=index),
        df_nd=pd.DataFrame(
            {"solar": [4.0, 5.0], "wind": [2.0, 3.0]},
            index=index,
        ),
    )
    sweep = SimpleNamespace(
        runs={
            ("low", "none"): SimpleNamespace(scenario=scenario),
            ("low", "equality"): SimpleNamespace(scenario=scenario),
        }
    )

    table = _scenario_input_table(sweep)

    assert len(table) == 2
    assert table["scenario"].tolist() == ["low", "low"]
    assert table["load_mw"].tolist() == [11.0, 22.0]
    assert table["renewable_available_mw"].tolist() == [6.0, 8.0]
    assert table["net_load_mw"].tolist() == [5.0, 14.0]


def test_larger_soft_weight_reduces_terminal_deviation():
    sweep = run_soft_weight_sweep(
        _representative_source(),
        scenario_names=("low",),
        weight_grids={"quadratic": (0.001, 0.1)},
    )
    deviations = sweep.summary["absolute_deviation_mwh"]

    assert deviations.loc[("low", "quadratic", 0.1)] <= deviations.loc[
        ("low", "quadratic", 0.001)
    ]


def test_horizon_study_uses_nested_suffix_and_reports_locality():
    study = run_horizon_study(
        _representative_source(),
        scenario_names=("low",),
        horizons=(12, 24),
    )

    assert len(study.runs[("low", 12, "none")].results["soc"]) == 12
    assert len(study.runs[("low", 24, "none")].results["soc"]) == 24
    assert ("low", 24, "equality") in study.locality.index
    assert study.summary.loc[
        ("low", 24, "equality"), "terminal_soc_mwh"
    ] == pytest.approx(500.0)


@pytest.mark.parametrize("horizons", [(), (0,), (97,), (12, 12)])
def test_invalid_horizons_are_rejected(horizons):
    with pytest.raises(ValueError, match="Horizons"):
        run_horizon_study(
            _representative_source(),
            scenario_names=("low",),
            horizons=horizons,
        )
