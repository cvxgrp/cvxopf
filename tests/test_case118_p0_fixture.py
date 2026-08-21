"""Frozen-input tests for the compact case118 P0 equivalence fixtures."""

import numpy as np
import pytest

from experiments.case118_annual_hierarchy.p0_fixture import (
    P0_AC_WINDOW_STEPS,
    P0_DELTA_HOURS,
    P0_EXPECTED_HASHES,
    P0_EXPECTED_POLICY_SHA256,
    P0_EXPECTED_SOLVE_CONFIG_SHA256,
    P0_STORAGE_DEVICE_ID,
    load_p0_fixture,
    solve_config_sha256,
)


@pytest.mark.parametrize("horizon", [6, 24])
def test_fixture_materializes_the_frozen_public_contract(horizon):
    fixture = load_p0_fixture(horizon)

    assert fixture.inputs.horizon_steps == horizon
    assert fixture.inputs.delta == P0_DELTA_HOURS
    assert fixture.policy.ac_window_steps == P0_AC_WINDOW_STEPS
    assert fixture.policy.outer_policy == "frozen"
    assert fixture.policy.inner_terminal_policy == "hard_equality"
    assert fixture.policy.initialization_policy == "shifted_with_recovery"
    assert fixture.policy_sha256 == P0_EXPECTED_POLICY_SHA256
    assert solve_config_sha256(fixture.solve_config) == (
        P0_EXPECTED_SOLVE_CONFIG_SHA256
    )
    assert fixture.policy.recovery is not None
    assert fixture.policy.recovery.perturbation_scales == (1e-4, 1e-3, 1e-2)
    assert fixture.policy.recovery.seed_base == 17_000_000
    assert fixture.solve_config.outer.solver == "CLARABEL"
    assert dict(fixture.solve_config.outer.options) == {}
    assert fixture.solve_config.ac.solver == "IPOPT"
    assert dict(fixture.solve_config.ac.options) == {}
    assert fixture.storage_device_ids == (P0_STORAGE_DEVICE_ID,)
    assert fixture.case_sha256 == P0_EXPECTED_HASHES[horizon]["case"]
    assert fixture.load_p_sha256 == P0_EXPECTED_HASHES[horizon]["load_p"]
    assert fixture.load_q_sha256 == P0_EXPECTED_HASHES[horizon]["load_q"]
    assert fixture.result_dimensions == {
        "generators": 3,
        "buses": 9,
        "branches": 9,
        "loads": 9,
        "storage": 1,
        "nondispatchable": 0,
        "hvdc": 0,
    }


def test_fixture_preserves_fixed_loads_and_fifty_percent_storage_boundaries():
    fixture = load_p0_fixture(24)

    assert all(unit.shedding_cost_per_mwh is None for unit in fixture.inputs.loads)
    assert fixture.inputs.df_load_p.shape == (24, 9)
    assert fixture.inputs.df_load_q.shape == (24, 9)
    assert np.isfinite(fixture.inputs.df_load_p.to_numpy()).all()
    assert np.isfinite(fixture.inputs.df_load_q.to_numpy()).all()
    storage = fixture.inputs.storage[0]
    assert storage.initial_soc == 0.5 * storage.capacity
    assert storage.terminal_soc == storage.initial_soc
    assert storage.terminal_constraint == "equality"


@pytest.mark.parametrize("horizon", [0, 3, 12, 25])
def test_fixture_rejects_unfrozen_horizons(horizon):
    with pytest.raises(ValueError, match="P0 horizon"):
        load_p0_fixture(horizon)
