"""Tests for the battery terminal-policy experiment scenario generator."""

import numpy as np
import pandas as pd
import pytest

from experiments.battery_terminal.scenario import (
    CASE_BASE_LOAD_MW,
    DEFAULT_SOURCE_TO_CASE_SCALE,
    REPRESENTATIVE_WINDOWS,
    SOURCE_MEAN_LOAD_MW,
    ScenarioConfig,
    generate_scenario,
    select_complete_window,
    select_representative_window,
)


def test_default_power_scale_maps_source_mean_load_to_case_load():
    assert (
        DEFAULT_SOURCE_TO_CASE_SCALE * SOURCE_MEAN_LOAD_MW
        == pytest.approx(CASE_BASE_LOAD_MW)
    )


def _source_frame(periods=4):
    index = pd.date_range(
        "2022-12-19 00:00:00-08:00", periods=periods, freq="h"
    )
    return pd.DataFrame(
        {
            "9q9wtp_solar": np.linspace(0.0, 300.0, periods),
            "9q9wtp_wind": np.linspace(400.0, 100.0, periods),
            "9q9wtp_dist_solar": np.linspace(0.0, 30.0, periods),
            "9q9wtp_load": np.linspace(1000.0, 1300.0, periods),
        },
        index=index,
    )


def _resource_total(df_nd, resource):
    columns = [
        column for column in df_nd if column.startswith(f"{resource}_bus_")
    ]
    return df_nd.loc[:, columns].sum(axis=1)


def test_default_scenario_preserves_aggregate_trajectories():
    source = _source_frame()
    scenario = generate_scenario(source)
    scale = DEFAULT_SOURCE_TO_CASE_SCALE

    np.testing.assert_allclose(
        scenario.df_P.sum(axis=1), scale * source["9q9wtp_load"]
    )
    np.testing.assert_allclose(
        _resource_total(scenario.df_nd, "utility_solar"),
        scale * source["9q9wtp_solar"],
    )
    np.testing.assert_allclose(
        _resource_total(scenario.df_nd, "wind"),
        scale * source["9q9wtp_wind"],
    )
    np.testing.assert_allclose(
        _resource_total(scenario.df_nd, "dist_solar"),
        scale * source["9q9wtp_dist_solar"],
    )


def test_load_scaling_and_shift_are_applied_before_spatial_allocation():
    source = _source_frame()
    config = ScenarioConfig(load_scale=1.2, load_shift_mw=50.0)
    scenario = generate_scenario(source, config)

    expected = (
        DEFAULT_SOURCE_TO_CASE_SCALE
        * 1.2
        * source["9q9wtp_load"]
        + 50.0
    )
    np.testing.assert_allclose(scenario.df_P.sum(axis=1), expected)


def test_resource_scales_are_independent():
    source = _source_frame()
    config = ScenarioConfig(
        solar_scale=0.5,
        wind_scale=0.25,
        dist_solar_scale=2.0,
    )
    scenario = generate_scenario(source, config)

    np.testing.assert_allclose(
        _resource_total(scenario.df_nd, "utility_solar"),
        DEFAULT_SOURCE_TO_CASE_SCALE * 0.5 * source["9q9wtp_solar"],
    )
    np.testing.assert_allclose(
        _resource_total(scenario.df_nd, "wind"),
        DEFAULT_SOURCE_TO_CASE_SCALE * 0.25 * source["9q9wtp_wind"],
    )
    np.testing.assert_allclose(
        _resource_total(scenario.df_nd, "dist_solar"),
        DEFAULT_SOURCE_TO_CASE_SCALE * 2.0 * source["9q9wtp_dist_solar"],
    )


def test_spatial_noise_is_seeded_and_preserves_aggregates():
    source = _source_frame()
    config = ScenarioConfig(spatial_noise_std=0.2, random_seed=7)

    first = generate_scenario(source, config)
    second = generate_scenario(source, config)
    different = generate_scenario(
        source, ScenarioConfig(spatial_noise_std=0.2, random_seed=8)
    )

    pd.testing.assert_frame_equal(first.df_P, second.df_P)
    pd.testing.assert_frame_equal(first.df_nd, second.df_nd)
    assert not first.df_P.equals(different.df_P)
    assert not first.df_nd.equals(different.df_nd)
    np.testing.assert_allclose(
        first.df_P.sum(axis=1),
        DEFAULT_SOURCE_TO_CASE_SCALE * source["9q9wtp_load"],
    )
    for resource, source_column in (
        ("utility_solar", "9q9wtp_solar"),
        ("wind", "9q9wtp_wind"),
        ("dist_solar", "9q9wtp_dist_solar"),
    ):
        np.testing.assert_allclose(
            _resource_total(first.df_nd, resource),
            DEFAULT_SOURCE_TO_CASE_SCALE * source[source_column],
        )


def test_reactive_load_preserves_base_bus_q_over_p_ratios():
    scenario = generate_scenario(_source_frame())

    np.testing.assert_allclose(scenario.df_Q[5], scenario.df_P[5] / 3.0)
    np.testing.assert_allclose(scenario.df_Q[7], 0.35 * scenario.df_P[7])
    np.testing.assert_allclose(scenario.df_Q[9], 0.40 * scenario.df_P[9])
    np.testing.assert_allclose(
        scenario.df_Q[[1, 2, 3, 4, 6, 8]], 0.0
    )


def test_select_complete_window_is_inclusive():
    source = _source_frame(periods=6)
    window = select_complete_window(
        source,
        "2022-12-19 01:00:00-08:00",
        "2022-12-19 03:00:00-08:00",
    )

    assert len(window) == 3
    assert window.index[0].hour == 1
    assert window.index[-1].hour == 3


@pytest.mark.parametrize("name", ["low", "moderate", "high"])
def test_representative_windows_are_complete_96_hour_intervals(name):
    spec = REPRESENTATIVE_WINDOWS[name]
    index = pd.date_range(spec.start, spec.end, freq="h")
    source = pd.DataFrame(
        {
            "9q9wtp_solar": 1.0,
            "9q9wtp_wind": 1.0,
            "9q9wtp_dist_solar": 1.0,
            "9q9wtp_load": 1.0,
        },
        index=index,
    )

    window = select_representative_window(source, name)

    assert len(window) == 96
    assert window.index[0] == pd.Timestamp(spec.start)
    assert window.index[-1] == pd.Timestamp(spec.end)


def test_unknown_representative_window_is_rejected():
    with pytest.raises(ValueError, match="Unknown representative window"):
        select_representative_window(_source_frame(), "extreme")


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda frame: frame.drop(frame.index[1]), "hourly cadence"),
        (
            lambda frame: frame.assign(
                **{"9q9wtp_wind": [1.0, np.nan, 1.0, 1.0]}
            ),
            "missing or nonfinite",
        ),
        (
            lambda frame: frame.assign(
                **{"9q9wtp_solar": [1.0, -1.0, 1.0, 1.0]}
            ),
            "nonnegative",
        ),
    ],
)
def test_invalid_source_window_is_rejected(mutation, match):
    with pytest.raises(ValueError, match=match):
        generate_scenario(mutation(_source_frame()))


@pytest.mark.parametrize(
    "config",
    [
        ScenarioConfig(load_scale=-1.0),
        ScenarioConfig(source_to_case_scale=0.0),
        ScenarioConfig(solar_scale=-1.0),
        ScenarioConfig(spatial_noise_std=-0.1),
        ScenarioConfig(load_shift_mw=np.inf),
    ],
)
def test_invalid_config_is_rejected(config):
    with pytest.raises(ValueError):
        generate_scenario(_source_frame(), config)


def test_negative_shifted_load_is_rejected():
    with pytest.raises(ValueError, match="negative load"):
        generate_scenario(
            _source_frame(),
            ScenarioConfig(load_scale=0.0, load_shift_mw=-1.0),
        )
