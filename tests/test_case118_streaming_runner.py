"""Focused P0b tests for the public-builder streaming execution seams."""

from dataclasses import replace

import numpy as np
import pytest

from cvxopf import LayerSolveConfig, ShiftedRecoveryConfig

from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy.streaming_runner import (
    assign_start,
    build_window,
    complete_flat_start,
    perturbed_start,
    snapshot_inputs,
    shifted_start,
    solve_frozen_outer,
    validate_solve_config,
    validate_streaming_policy,
)


def test_execution_snapshot_isolates_mutable_inputs():
    fixture = load_p0_fixture(6)
    snapshot = snapshot_inputs(fixture.inputs)

    fixture.inputs.df_load_p.iloc[0, 0] = 999.0
    fixture.inputs.loads[0].p_load_mw = 999.0
    fixture.inputs.storage[0].initial_soc = 100.0
    fixture.inputs.options.loss_weight = 42.0

    assert snapshot.df_load_p.iloc[0, 0] != 999.0
    assert snapshot.loads[0].p_load_mw != 999.0
    assert snapshot.storage[0].initial_soc == 500.0
    assert snapshot.options.loss_weight != 42.0


def test_public_window_builder_preserves_global_slice_and_delta():
    fixture = load_p0_fixture(6)
    build = build_window(
        fixture.inputs, "ac", 3, 6, fixture.inputs.storage
    )

    assert build.data["T"] == 3
    assert build.formulation == "ac"
    assert np.asarray(build.data["Pd_series"]).shape == (3, 9)
    assert np.asarray(build.data["storage_initial_soc"]).tolist() == [500.0]
    assert build.data["storage_delta"] == 1.0


@pytest.mark.parametrize(
    ("changes", "match"),
    [({"ac_window_steps": 2}, "three-hour"),
     ({"outer_policy": "replan_every_step"}, "frozen outer"),
     ({"inner_terminal_policy": "quadratic_soft", "quadratic_soft_weight": 1.0}, "hard_equality"),
     ({"initialization_policy": "flat_only", "recovery": None}, "shifted_with_recovery")],
)
def test_streaming_policy_rejects_unfrozen_choices(changes, match):
    policy = replace(load_p0_fixture(6).policy, **changes)
    with pytest.raises(ValueError, match=match):
        validate_streaming_policy(policy)


@pytest.mark.parametrize(
    "changes",
    [
        {
            "recovery": ShiftedRecoveryConfig(
                perturbation_scales=(1e-5, 1e-3, 1e-2),
                seed_base=17_000_000,
            )
        },
        {
            "recovery": ShiftedRecoveryConfig(
                perturbation_scales=(1e-4, 1e-3, 1e-2),
                seed_base=17_000_001,
            )
        },
        {
            "tolerances": replace(
                load_p0_fixture(6).policy.tolerances,
                voltage_bound_pu_abs=5e-7,
            )
        },
    ],
)
def test_streaming_policy_rejects_recovery_or_tolerance_drift(changes):
    policy = replace(load_p0_fixture(6).policy, **changes)
    with pytest.raises(ValueError, match="frozen contract"):
        validate_streaming_policy(policy)


@pytest.mark.parametrize("layer", ["outer", "ac"])
def test_streaming_solver_configuration_rejects_option_drift(layer):
    fixture = load_p0_fixture(6)
    changed_layer = LayerSolveConfig(
        fixture.solve_config.outer.solver if layer == "outer" else "IPOPT",
        options={"max_iter": 5},
    )
    config = replace(fixture.solve_config, **{layer: changed_layer})

    with pytest.raises(ValueError, match="frozen contract"):
        validate_solve_config(config)


def test_frozen_outer_plan_is_accepted_and_indexed_by_global_boundary():
    fixture = load_p0_fixture(6)
    snapshot = snapshot_inputs(fixture.inputs)
    outer = solve_frozen_outer(snapshot, fixture.policy, fixture.solve_config)

    assert outer.accepted_primal
    assert outer.exception is None
    assert outer.global_boundary_indices.tolist() == list(range(7))
    assert outer.boundary_soc_mwh is not None
    assert outer.boundary_soc_mwh.shape == (7, 1)
    assert outer.target_at(0) == {"p0_storage_bus_7": 500.0}
    assert outer.target_at(6)["p0_storage_bus_7"] == pytest.approx(500.0)


def test_causal_start_transformations_are_complete_and_deterministic():
    fixture = load_p0_fixture(6)
    first = build_window(fixture.inputs, "ac", 0, 3, fixture.inputs.storage)
    destination = build_window(
        fixture.inputs, "ac", 1, 4, fixture.inputs.storage
    )
    flat = complete_flat_start(first)
    raw, shifted = shifted_start(
        flat,
        destination,
        fixture.inputs,
        fixture.policy,
        {"p0_storage_bus_7": 500.0},
    )
    assert set(raw) == set(shifted)
    assign_start(destination, shifted)

    _, perturbed_once = perturbed_start(
        shifted, destination, scale=1e-4, seed=17_000_121
    )
    _, perturbed_twice = perturbed_start(
        shifted, destination, scale=1e-4, seed=17_000_121
    )
    assert all(
        np.array_equal(perturbed_once[name], perturbed_twice[name])
        for name in perturbed_once
    )
