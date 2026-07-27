"""Tests for battery-terminal follow-up diagnostics."""

import pandas as pd

from experiments.battery_terminal.followup_studies import (
    run_low_breakpoint_refinement,
    run_moderate_adequacy_diagnostic,
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


def test_adequacy_diagnostic_compares_formulations_and_lookbacks():
    diagnostic = run_moderate_adequacy_diagnostic(
        _representative_source(),
        initial_soc_values=(500.0,),
        lookback_hours=(24, 25),
        prefix_hours=(1, 2),
    )

    assert set(
        diagnostic.initial_soc.index.get_level_values("formulation")
    ) == {"singlenode_dc", "lossy_dc"}
    assert diagnostic.lookback.index.tolist() == [24, 25]
    assert diagnostic.prefix_capacity.index.tolist() == [1, 2]
    assert diagnostic.initial_soc["status"].notna().all()


def test_low_breakpoint_refinement_adds_active_set_metrics():
    sweep = run_low_breakpoint_refinement(
        _representative_source(),
        targets_mwh=(450.0, 451.0),
    )

    assert "generation_above_minimum_mwh" in sweep.summary
    assert "upper_soc_margin_mwh" in sweep.summary
