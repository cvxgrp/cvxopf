"""Tests for the lossy-DC terminal-policy experiment runner."""

import pandas as pd
import pytest

from experiments.battery_terminal.runner import (
    LINEAR_TERMINAL_WEIGHT,
    POLICIES,
    QUADRATIC_TERMINAL_WEIGHT,
    run_lossy_dc_sweep,
)
from experiments.battery_terminal.scenario import REPRESENTATIVE_WINDOWS


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


def test_policy_matrix_uses_approved_weights():
    assert set(POLICIES) == {
        "none",
        "equality",
        "shortfall",
        "linear",
        "quadratic",
        "shortfall_linear",
        "shortfall_quadratic",
    }
    assert POLICIES["linear"].terminal_weight == LINEAR_TERMINAL_WEIGHT
    assert (
        POLICIES["shortfall_linear"].terminal_weight
        == LINEAR_TERMINAL_WEIGHT
    )
    assert POLICIES["quadratic"].terminal_weight == QUADRATIC_TERMINAL_WEIGHT
    assert (
        POLICIES["shortfall_quadratic"].terminal_weight
        == QUADRATIC_TERMINAL_WEIGHT
    )


def test_one_run_retains_summary_and_soc_geometry():
    sweep = run_lossy_dc_sweep(
        _representative_source(),
        scenario_names=("low",),
        policy_names=("equality",),
    )

    summary = sweep.summary.loc[("low", "equality")]
    run = sweep.runs[("low", "equality")]
    assert summary["status"] == "optimal"
    assert summary["terminal_soc_mwh"] == pytest.approx(500.0)
    assert summary["terminal_deviation_mwh"] == pytest.approx(0.0)
    assert len(run.decomposition.states) == 97


def test_unknown_policy_is_rejected():
    with pytest.raises(ValueError, match="Unknown policies"):
        run_lossy_dc_sweep(
            _representative_source(),
            scenario_names=("low",),
            policy_names=("band",),
        )
