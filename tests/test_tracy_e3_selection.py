"""Public synthetic window-selection tests; no private data or OPF solving."""

import numpy as np
import pytest

from experiments.case118_tracy_2021.select_e3 import rank_candidates


def inputs():
    capacity = np.array([1., 100.])
    return np.tile(.5*capacity, (61, 1)), capacity, np.zeros(60)


@pytest.mark.parametrize("anchor,starts", [(0, [0]), (30, list(range(12, 31))), (54, [36])])
def test_complete_event_containment_and_year_edges(anchor, starts):
    candidates = rank_candidates(*inputs(), anchor)
    assert sorted(c["start"] for c in candidates) == starts
    for c in candidates:
        assert c["stop"]-c["start"] == 24
        assert c["start"] <= anchor and anchor+6 <= c["stop"] <= 60


def test_centered_event_and_earliest_start_tie_break():
    soc, capacity, net = inputs()
    assert rank_candidates(soc, capacity, net, 30)[0]["start"] == 21
    soc[:] = .6*capacity
    soc[[20, 44, 22, 46]] = .5*capacity
    candidates = rank_candidates(soc, capacity, net, 30)
    assert [c["start"] for c in candidates[:2]] == [20, 22]
    assert candidates[0]["event_midpoint_distance_hours"] == 1


def test_endpoint_is_boundary_24_not_last_input_interval():
    soc, capacity, net = inputs()
    soc[:] = np.arange(61)[:, None]/60*capacity
    c = next(c for c in rank_candidates(soc, capacity, net, 30) if c["start"] == 20)
    np.testing.assert_allclose(c["start_fraction"], [20/60]*2)
    np.testing.assert_allclose(c["end_fraction"], [44/60]*2)


def test_equal_device_endpoint_score_not_capacity_weighted_fleet_score():
    soc, capacity, net = inputs()
    soc[[20, 44]] = np.array([1., .5])*capacity
    c = next(c for c in rank_candidates(soc, capacity, net, 30) if c["start"] == 20)
    assert c["score_rms_fraction"] == pytest.approx(np.sqrt(.125))
    assert c["max_device_deviation_fraction"] == .5
    assert c["fleet_start_fraction"] == pytest.approx(51/101)
    assert c["score_rms_fraction"] > 50*abs(c["fleet_start_fraction"]-.5)


def test_net_load_does_not_select_and_energy_scores_have_correct_sign():
    soc, capacity, net = inputs()
    first = rank_candidates(soc, capacity, net, 30)
    net[:30], net[30:] = -4., 2.
    second = rank_candidates(soc, capacity, net, 30)
    assert [c["start"] for c in first] == [c["start"] for c in second]
    for c in second:
        assert c["net_energy_mwh"] == pytest.approx(c["positive_net_energy_mwh"]-c["surplus_available_energy_mwh"])


@pytest.mark.parametrize("bad", ["zero_capacity", "nan_capacity", "bad_shape", "missing_boundary",
                                     "nan_soc", "overfull_soc", "negative_soc", "nan_net", "short_net"])
def test_invalid_annual_arrays_fail(bad):
    soc, capacity, net = inputs()
    if bad == "zero_capacity":
        capacity[0] = 0
    elif bad == "nan_capacity":
        capacity[0] = np.nan
    elif bad == "bad_shape":
        soc = soc[:, :1]
    elif bad == "missing_boundary":
        soc = soc[:-1]
    elif bad == "nan_soc":
        soc[0, 0] = np.nan
    elif bad == "overfull_soc":
        soc[0, 0] = 2.
    elif bad == "negative_soc":
        soc[0, 0] = -.1
    elif bad == "nan_net":
        net[0] = np.nan
    else:
        net = net[:23]
    with pytest.raises(ValueError):
        rank_candidates(soc, capacity, net, 30)


@pytest.mark.parametrize("anchor", [-1, 55, True, 30.5])
def test_invalid_event_fails(anchor):
    with pytest.raises(ValueError, match="anchor"):
        rank_candidates(*inputs(), anchor)
