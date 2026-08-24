from __future__ import annotations

import numpy as np

from experiments.case118_annual_hierarchy.s2_fixture import load_s2_fixture
from experiments.case118_annual_hierarchy.s3_fixture import (
    S3_HORIZON_STEPS,
    S3_RESTART_BOUNDARIES,
    load_s3_fixture,
)
from experiments.case118_annual_hierarchy.scenario import PILOT_GRID


def test_s3_fixture_loads_with_frozen_hashes() -> None:
    fixture = load_s3_fixture()

    assert fixture.inputs.horizon_steps == 720
    assert S3_HORIZON_STEPS == 720
    assert fixture.start_timestamp == "2025-06-06 00:00:00+00:00"
    assert fixture.stop_timestamp == "2025-07-05 23:00:00+00:00"


def test_s3_preserves_s2_as_exact_prefix() -> None:
    s2 = load_s2_fixture()
    s3 = load_s3_fixture()

    for s2_frame, s3_frame in (
        (s2.inputs.df_load_p, s3.inputs.df_load_p),
        (s2.inputs.df_load_q, s3.inputs.df_load_q),
        (s2.inputs.df_nd, s3.inputs.df_nd),
    ):
        assert s2_frame is not None
        assert s3_frame is not None
        np.testing.assert_array_equal(
            s2_frame.to_numpy(),
            s3_frame.iloc[: len(s2_frame)].to_numpy(),
        )
        assert s2_frame.index.equals(s3_frame.index[: len(s2_frame)])

    assert s2.storage_device_ids == s3.storage_device_ids
    assert s2.policy_sha256 == s3.policy_sha256
    assert s2.solve_config_sha256 == s3.solve_config_sha256


def test_s3_restart_schedule_is_global_and_excludes_completion() -> None:
    assert S3_RESTART_BOUNDARIES == tuple(range(16, 720, 16))
    assert len(S3_RESTART_BOUNDARIES) == 44
    assert S3_RESTART_BOUNDARIES[0] == 16
    assert S3_RESTART_BOUNDARIES[-1] == 704
    assert 720 not in S3_RESTART_BOUNDARIES
    assert len(S3_RESTART_BOUNDARIES) + 1 == 45


def test_s3_fixture_freezes_scientific_scenario_semantics() -> None:
    fixture = load_s3_fixture()
    selected = PILOT_GRID[0]

    assert selected.renewable_energy_share == 0.15
    assert selected.storage_power_fraction_of_peak == 0.05
    assert selected.storage_duration_hours == 4.0
    assert fixture.inputs.delta == 1.0
    assert fixture.inputs.options.enforce_branch_limits is True
    assert fixture.inputs.options.init_flat is True
    assert fixture.storage_device_ids == (
        "storage_bus_41",
        "storage_bus_65",
        "storage_bus_89",
        "storage_bus_105",
    )
    assert tuple(unit.bus for unit in fixture.inputs.storage) == (41, 65, 89, 105)
    for unit in fixture.inputs.storage:
        assert unit.initial_soc == 0.5 * unit.capacity
        assert unit.terminal_soc == 0.5 * unit.capacity
        assert unit.terminal_soc == unit.initial_soc
        assert unit.terminal_constraint == "equality"
        assert unit.terminal_cost is None
        assert unit.terminal_weight is None
