"""Tests for the battery terminal experiment's provisional device factory."""

import pandas as pd
import pytest

from experiments.battery_terminal.devices import (
    STORAGE_CAPACITY_MWH,
    STORAGE_INITIAL_SOC_MWH,
    STORAGE_POWER_MVA,
    make_dispatchable_generators,
    make_nondispatchable_units,
    make_storage,
)


def _availability(scale=1.0):
    return pd.DataFrame(
        {
            "utility_solar_bus_1": [0.0, 10.0 * scale],
            "utility_solar_bus_2": [0.0, 40.0 * scale],
            "wind_bus_2": [5.0 * scale, 10.0 * scale],
            "wind_bus_3": [20.0 * scale, 40.0 * scale],
            "dist_solar_bus_5": [0.0, 5.0 * scale],
            "dist_solar_bus_7": [0.0, 6.0 * scale],
            "dist_solar_bus_9": [0.0, 7.0 * scale],
        }
    )


def test_dispatchable_fleet_has_approved_locations_and_total_capacity():
    generators = make_dispatchable_generators()

    assert [unit.bus for unit in generators] == [1, 2, 3]
    assert sum(unit.p_max_mw for unit in generators) == pytest.approx(350.0)
    assert [unit.p_min_mw for unit in generators] == [10.0, 10.0, 10.0]


def test_nondispatchable_ratings_cover_all_supplied_scenarios():
    frames = [_availability(scale=1.0), _availability(scale=2.0)]
    units = make_nondispatchable_units(frames, rating_multiplier=1.10)
    maxima = pd.concat(frames).max()

    assert [unit.device_id for unit in units] == list(frames[0].columns)
    assert [unit.bus for unit in units] == [1, 2, 2, 3, 5, 7, 9]
    for unit in units:
        assert unit.apparent_power_rating == pytest.approx(
            1.10 * maxima[unit.device_id]
        )


def test_nondispatchable_frames_must_have_same_device_contract():
    reordered = _availability()[list(reversed(_availability().columns))]

    with pytest.raises(ValueError, match="identical ordered device columns"):
        make_nondispatchable_units([_availability(), reordered])


def test_storage_physical_specification_is_independent_of_terminal_policy():
    inactive = make_storage()
    equality = make_storage(
        terminal_soc=STORAGE_INITIAL_SOC_MWH,
        terminal_constraint="equality",
    )

    for unit in (inactive, equality):
        assert unit.bus == 7
        assert unit.apparent_power_rating == STORAGE_POWER_MVA
        assert unit.capacity == STORAGE_CAPACITY_MWH
        assert unit.initial_soc == STORAGE_INITIAL_SOC_MWH
    assert inactive.terminal_soc is None
    assert equality.terminal_soc == STORAGE_INITIAL_SOC_MWH
    assert equality.terminal_constraint == "equality"
