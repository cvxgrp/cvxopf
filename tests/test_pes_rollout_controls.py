"""The joint-AC control uses the appendix's interior, equal-device metric."""

import numpy as np
import pytest

from experiments.pes2027.analyze_rollout_controls import trajectory_distance


def test_interior_boundaries_equal_device_weighting_and_fleet_cancellation():
    reference = [[0, 0], [5, 50], [5, 50], [0, 0]]
    realized = [[10, 100], [6, 49], [4, 51], [10, 100]]
    result = trajectory_distance(realized, reference, [10, 100])
    assert result["capacity_normalized_mae"] == pytest.approx(0.055)
    assert result["individual_mae_percentage_points"] == pytest.approx(5.5)
    assert result["individual_max_percentage_points"] == pytest.approx(10)
    assert result["fleet_mae_mwh"] == pytest.approx(0)
    assert result["interior_boundaries"] == 2
    assert result["batteries"] == 2
    assert "relative_reduction" not in result


def test_identical_control_has_zero_distance():
    result = trajectory_distance([[0], [5], [0]], [[0], [5], [0]], [10])
    assert result["capacity_normalized_mae"] == 0
    assert result["individual_max_percentage_points"] == 0


@pytest.mark.parametrize(
    "realized,reference,capacity",
    [
        ([[0], [1]], [[0], [1]], [10]),
        ([[0], [1], [0]], [[0], [0]], [10]),
        ([[0], [np.nan], [0]], [[0], [0], [0]], [10]),
        ([[0], [1], [0]], [[0], [0], [0]], [0]),
    ],
)
def test_invalid_distance_inputs_rejected(realized, reference, capacity):
    with pytest.raises(ValueError):
        trajectory_distance(realized, reference, capacity)
