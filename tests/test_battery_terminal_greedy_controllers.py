"""Tests for the causal greedy battery-controller baselines."""

import numpy as np

from experiments.battery_terminal.greedy_controllers import (
    GreedyConfig,
    naive_control_battery_priority,
    naive_control_dispatchable_priority,
)


def test_priority_changes_deficit_dispatch_order():
    config = GreedyConfig(
        capacity_mwh=100.0,
        max_power_mw=20.0,
        initial_soc_mwh=50.0,
        dispatchable_max_mw=30.0,
    )
    load = np.array([40.0])
    renewable = np.array([0.0])

    dispatchable = naive_control_dispatchable_priority(load, renewable, config)
    battery = naive_control_battery_priority(load, renewable, config)

    assert dispatchable.dispatchable_mw[0] == 30.0
    assert dispatchable.battery_mw[0] == 10.0
    assert battery.battery_mw[0] == 20.0
    assert battery.dispatchable_mw[0] == 20.0


def test_surplus_charging_respects_energy_and_power_limits():
    config = GreedyConfig(
        capacity_mwh=100.0,
        max_power_mw=20.0,
        initial_soc_mwh=90.0,
        dispatchable_max_mw=30.0,
    )

    result = naive_control_dispatchable_priority(
        np.array([0.0]),
        np.array([50.0]),
        config,
    )

    assert result.battery_mw[0] == -10.0
    assert result.curtailment_mw[0] == 40.0
    assert result.soc_mwh[-1] == 100.0


def test_greedy_trajectories_satisfy_balance_and_storage_bounds():
    config = GreedyConfig()
    load = np.array([100.0, 500.0, 50.0, 0.0])
    renewable = np.array([200.0, 0.0, 25.0, 300.0])

    for controller in (
        naive_control_dispatchable_priority,
        naive_control_battery_priority,
    ):
        result = controller(load, renewable, config)
        np.testing.assert_allclose(
            load - renewable,
            result.dispatchable_mw
            + result.battery_mw
            + result.load_shedding_mw
            - result.curtailment_mw,
        )
        assert np.all(result.soc_mwh >= 0)
        assert np.all(result.soc_mwh <= config.capacity_mwh)
        assert np.all(np.abs(result.battery_mw) <= config.max_power_mw)
