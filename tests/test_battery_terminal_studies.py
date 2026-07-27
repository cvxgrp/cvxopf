"""Tests for terminal-weight and nested-horizon experiment studies."""

import pandas as pd
import pytest

from experiments.battery_terminal.horizon_study import run_horizon_study
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
