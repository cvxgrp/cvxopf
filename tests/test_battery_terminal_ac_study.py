"""Tests for the staged AC terminal-policy study."""

import pandas as pd
import pytest

from experiments.battery_terminal.ac_study import (
    AC_POLICIES,
    run_ac_study,
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


def test_ac_study_requires_approved_staging_contract():
    with pytest.raises(ValueError, match="horizons"):
        run_ac_study(_representative_source(), horizons=(12,))

    reversed_policies = dict(reversed(tuple(AC_POLICIES.items())))
    with pytest.raises(ValueError, match="policies"):
        run_ac_study(
            _representative_source(),
            policies=reversed_policies,
        )
