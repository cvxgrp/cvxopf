"""Input-only selection mechanics; no private Tracy data or numerical solve."""

import numpy as np
import pytest

from experiments.case118_tracy_2021.select_stage_d import select_periods


def fixture():
    net = np.zeros(160)
    net[0:6] = -10
    net[25:31] = 10
    net[50:53], net[53:56] = -8, 8
    net[80:83], net[83:86] = 8, -8
    return net


def test_integrated_energy_transitions_and_earliest_ties():
    net = fixture()
    net[110] = 40  # Largest instantaneous peak, but not six-hour energy.
    selected = select_periods(net, [0, 160])
    assert [p["start"] for p in selected] == [0, 25, 50, 80]
    assert [p["net_energy_mwh"] for p in selected] == [-60, 60, 0, 0]
    assert selected[2]["change_mw"] == 16
    assert selected[3]["change_mw"] == -16
    net[120:126] = -10  # An exact energy tie retains earlier start.
    assert select_periods(net, [0, 160])[0]["start"] == 0


def test_padding_inside_shards_and_nonoverlap():
    net = fixture()
    net[145:151] = -100  # Ineligible: insufficient 17-hour padding.
    boundaries = [0, 40, 70, 100, 160]
    selected = select_periods(net, boundaries)
    assert selected[0]["start"] != 145
    for p in selected:
        assert p["start"] + 17 <= boundaries[p["shard"] + 1]
    for i, p in enumerate(selected):
        for q in selected[i + 1 :]:
            assert p["start"] + 6 <= q["start"] or q["start"] + 6 <= p["start"]


def test_no_crossing_is_not_silently_replaced():
    with pytest.raises(ValueError, match="Surplus to deficit"):
        select_periods(np.ones(100), [0, 100])


def test_nonfinite_input_rejected():
    net = fixture()
    net[0] = np.nan
    with pytest.raises(ValueError, match="finite"):
        select_periods(net, [0, 160])
