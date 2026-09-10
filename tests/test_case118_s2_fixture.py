from __future__ import annotations

import numpy as np

from experiments.case118_annual_hierarchy.p0_fixture import (
    P0_EXPECTED_POLICY_SHA256,
    P0_EXPECTED_SOLVE_CONFIG_SHA256,
)
from experiments.case118_annual_hierarchy.s2_fixture import (
    S2_EXPECTED_HASHES,
    S2_HORIZON_STEPS,
    load_s2_fixture,
)


def test_s2_fixture_materializes_the_frozen_rated_week():
    fixture = load_s2_fixture()
    inputs = fixture.inputs

    assert S2_HORIZON_STEPS == 168
    assert inputs.horizon_steps == 168
    assert inputs.delta == 1.0
    assert fixture.start_timestamp == "2025-06-06 00:00:00+00:00"
    assert fixture.stop_timestamp == "2025-06-12 23:00:00+00:00"
    assert len(inputs.generators) == 54
    assert len(inputs.storage) == 4
    assert len(inputs.nondispatchable) == 2
    assert len(inputs.loads) > 0
    assert inputs.df_load_p.shape == (168, len(inputs.loads))
    assert inputs.df_load_q is not None
    assert inputs.df_load_q.shape == inputs.df_load_p.shape
    assert inputs.df_nd is not None
    assert inputs.df_nd.shape == (168, 2)
    assert np.isfinite(inputs.df_load_p.to_numpy()).all()
    assert np.isfinite(inputs.df_load_q.to_numpy()).all()
    assert np.isfinite(inputs.df_nd.to_numpy()).all()
    assert all(load.shedding_cost_per_mwh is None for load in inputs.loads)
    assert all(unit.initial_soc == unit.terminal_soc for unit in inputs.storage)
    assert all(
        unit.terminal_constraint == "equality" for unit in inputs.storage
    )
    assert fixture.policy_sha256 == P0_EXPECTED_POLICY_SHA256
    assert fixture.solve_config_sha256 == P0_EXPECTED_SOLVE_CONFIG_SHA256


def test_s2_fixture_hashes_are_frozen():
    fixture = load_s2_fixture()

    assert fixture.hashes == S2_EXPECTED_HASHES
    assert len(fixture.scenario_hash) == 64
