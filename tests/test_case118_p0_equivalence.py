from __future__ import annotations

import pytest

from experiments.case118_annual_hierarchy.p0_equivalence import run_nominal_equivalence
from experiments.case118_annual_hierarchy.p0_equivalence import _canonical_layout


def test_layout_normalization_preserves_model_owned_variable_names():
    layout = [
        {
            "name": "Pg_0",
            "is_original_variable": True,
            "start": 0,
            "stop": 3,
        },
        {
            "name": "var917",
            "is_original_variable": False,
            "start": 3,
            "stop": 6,
        },
    ]

    normalized = _canonical_layout(layout)

    assert normalized[0]["name"] == "Pg_0"
    assert normalized[1]["name"] == "auxiliary_0"


@pytest.mark.parametrize("horizon_steps", [6, 24])
def test_nominal_public_and_streaming_trajectories_are_equivalent(tmp_path, horizon_steps):
    report = run_nominal_equivalence(horizon_steps, tmp_path / str(horizon_steps))

    assert report.equivalent, report.mismatches
    assert report.completed_intervals == horizon_steps
    assert report.outer_plan_count == 1
    assert report.attempt_count == 9 * horizon_steps
    assert report.executed_interval_count == horizon_steps
    assert report.controlling_ordinals == (0,) * horizon_steps
    assert report.public_runtime_seconds > 0
    assert report.streaming_runtime_seconds > 0
    assert report.compared_runtime_numerically is False
