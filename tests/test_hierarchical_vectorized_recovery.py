"""Bounded continuous-trajectory and representation tests for inner AC recovery."""

from dataclasses import replace

import numpy as np
import pytest

from cvxopf import (
    HierarchicalPolicy, HierarchicalSolveConfig, LayerSolveConfig, StorageUnitIdeal,
    solve_hierarchical_opf,
)
from cvxopf import _hierarchical_solver as controller
from cvxopf._ac_start_mapping import (
    pack_start, stepwise_template, stepwise_values, unpack_values,
)
from tests.test_hierarchical_solver import _inputs


MODES = ("stepwise", "vectorized")
BOUNDED = HierarchicalSolveConfig(ac=LayerSolveConfig(
    "IPOPT", {"max_iter": 300, "max_cpu_time": 10.0},
))


def _two_storage_inputs(horizon=5):
    inputs = _inputs(horizon_steps=horizon)
    # Deliberately non-sorted IDs and different initial states distinguish axes.
    first = replace(inputs.storage[0], device_id="z", initial_soc=420.0, terminal_soc=420.0)
    second = StorageUnitIdeal(5, 30.0, 300.0, 120.0, device_id="a",
                              terminal_soc=120.0, terminal_constraint="equality")
    p, q = inputs.df_load_p.copy(), inputs.df_load_q.copy()
    for step in range(horizon):
        p.iloc[step] *= 0.95 + 0.025 * step
        q.iloc[step] *= 0.95 + 0.025 * step
    return replace(inputs, storage=(first, second), df_load_p=p, df_load_q=q, delta=0.5)


def _build(snapshot, mode, horizon):
    return controller._build_window(snapshot, "ac", 0, horizon, snapshot.storage,
                                    temporal_assembly=mode)


@pytest.mark.parametrize("sparse", [False, True])
def test_round_trip_and_seeded_perturbation_preserve_physical_coordinates(sparse):
    inputs = _two_storage_inputs(3)
    snapshot = controller._execution_snapshot(replace(inputs, options=replace(inputs.options, sparse_pq=sparse)))
    step, vector = [_build(snapshot, mode, 3) for mode in MODES]
    logical = controller._complete_start(step)
    # Distinct per-coordinate values detect transposes even for square arrays.
    for number, (name, value) in enumerate(logical.items()):
        logical[name] = np.arange(value.size).reshape(value.shape) / 100 + number
    initial = [420.0, 120.0]
    packed = pack_start(logical, vector, initial)
    restored = unpack_values(packed, logical)
    for name in logical:
        np.testing.assert_array_equal(restored[name], logical[name])
    np.testing.assert_array_equal(packed["soc"][:, 0], initial)
    raw_step, projected_step = controller._perturbed_start(logical, step, scale=1e-3, seed=17)
    raw_vector, projected_vector = controller._perturbed_start(packed, vector, scale=1e-3, seed=17)
    for actual, expected in [(raw_vector, raw_step), (projected_vector, projected_step)]:
        actual_step = stepwise_values(vector, actual)
        for name in expected:
            np.testing.assert_array_equal(actual_step[name], expected[name])
        np.testing.assert_array_equal(actual["soc"][:, 0], initial)
    assert stepwise_template(vector).keys() == logical.keys()


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("destination_steps", [3, 2, 1])
def test_shift_uses_identity_aligned_state_and_correct_tail(mode, destination_steps):
    snapshot = controller._execution_snapshot(_two_storage_inputs(3))
    source = _build(snapshot, "stepwise", 3)
    logical = controller._complete_start(source)
    for step in range(3):
        logical[f"b_{step}"] = np.array([step + 1.0, -step - 2.0])
        logical[f"b_q_{step}"] = np.array([step + 4.0, step + 5.0])
        logical[f"Pg_{step}"] = np.full(3, step + 1.0)
    destination = _build(snapshot, mode, destination_steps)
    # Mapping order differs from device order: z must remain the first column.
    realized = {"a": 120.0, "z": 420.0}
    raw, assigned = controller._shifted_start(logical, destination, snapshot,
                                            HierarchicalPolicy(ac_window_steps=3), realized)
    actual = stepwise_values(destination, assigned)
    state = np.array([420.0, 120.0])
    for step in range(destination_steps):
        b = logical[f"b_{step + 1}"] if step < 2 else np.zeros(2)
        np.testing.assert_array_equal(actual[f"b_{step}"], b)
        state = state - snapshot.delta * b
        np.testing.assert_array_equal(actual[f"soc_{step}"], state)
    if destination_steps == 3:
        np.testing.assert_array_equal(actual["Pg_2"], logical["Pg_2"])
        np.testing.assert_array_equal(actual["b_q_2"], np.zeros(2))
    if mode == "vectorized":
        np.testing.assert_array_equal(raw["soc"][:, 0], [420.0, 120.0])


def _assert_continuous_trajectory(result, mode, horizon, first_winner=0):
    assert result.completed, result.termination_reason
    assert result.completed_intervals == horizon
    assert result.realized_soc_mwh.shape == (horizon + 1, 2)
    np.testing.assert_allclose(np.diff(result.realized_soc_mwh, axis=0),
                               -0.5 * result.executed_b_mw, atol=1e-5)
    controlling = []
    for iteration in range(horizon):
        slots = result.ac_attempts[iteration * 9:(iteration + 1) * 9]
        assert [item.ordinal for item in slots] == list(range(9))
        winner = next(item for item in slots if item.supplied_executed_action)
        assert winner.ordinal == (first_winner if iteration == 0 else 0)
        controlling.append(winner)
        build = winner.build
        assert build.temporal_assembly == mode
        assert build.data["T"] == min(3, horizon - iteration)
        assert winner.storage_device_ids == ("z", "a")
        np.testing.assert_allclose(winner.result["soc"][0], result.realized_soc_mwh[iteration + 1])
        if iteration:
            assert slots[0].source_attempt_id == controlling[-2].attempt_id
            source = stepwise_values(controlling[-2].build, controller._solution_values(controlling[-2].build))
            assigned = stepwise_values(slots[0].build, slots[0].assigned_start)
            np.testing.assert_allclose(assigned["Pg_0"], source["Pg_1"], atol=1e-12)
            np.testing.assert_allclose(assigned["soc_0"],
                result.realized_soc_mwh[iteration] - 0.5 * assigned["b_0"], atol=1e-12)
        for attempt in slots:
            if attempt.slot_state != "executed":
                continue
            evidence = attempt.solver_evidence
            assert evidence is not None
            assert evidence.auxiliary_coordinate_count > 0
            assert evidence.complete_x0.size == evidence.model_coordinate_count + evidence.auxiliary_coordinate_count
            assert np.isfinite(evidence.complete_x0).all()
            assert evidence.object_ids_before == evidence.object_ids_after
            for item in evidence.layout:
                if item["is_original_variable"]:
                    np.testing.assert_array_equal(
                        evidence.complete_x0[item["start"]:item["stop"]],
                        attempt.assigned_start[item["name"]].flatten(order="F"),
                    )
            if mode == "vectorized":
                np.testing.assert_array_equal(attempt.assigned_start["soc"][:, 0], result.realized_soc_mwh[iteration])
            if attempt.role == "target_free":
                assert not attempt.supplied_executed_action
    return controlling


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("terminal", ["hard_equality", "quadratic_soft"])
def test_bounded_sequential_native_trajectory(mode, terminal):
    policy = HierarchicalPolicy(ac_window_steps=3, initialization_policy="shifted_with_recovery",
                                inner_terminal_policy=terminal,
                                quadratic_soft_weight=1.0 if terminal == "quadratic_soft" else None)
    result = solve_hierarchical_opf(_two_storage_inputs(), policy, BOUNDED,
                                    inner_temporal_assembly=mode)
    _assert_continuous_trajectory(result, mode, 5)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("first_winner", [2, 3, 6])
def test_recovered_solution_becomes_next_causal_source(monkeypatch, mode, first_winner):
    original = controller._solve_ac_with_verified_x0
    calls = 0

    def reject_selected_native_attempts(build, config):
        nonlocal calls
        ordinal = calls
        calls += 1
        run = original(build, config)
        if ordinal < first_winner and ordinal != 1:
            return replace(run, exception="controlled rejection to exercise recovery")
        return run

    monkeypatch.setattr(controller, "_solve_ac_with_verified_x0", reject_selected_native_attempts)
    result = solve_hierarchical_opf(_two_storage_inputs(4),
        HierarchicalPolicy(ac_window_steps=3, initialization_policy="shifted_with_recovery"),
        BOUNDED, inner_temporal_assembly=mode)
    winners = _assert_continuous_trajectory(result, mode, 4, first_winner)
    if first_winner in (2, 3):
        assert winners[0].source_attempt_id == result.ac_attempts[1].attempt_id
    else:
        assert winners[0].source_kind == "generated_flat"
    if first_winner == 2:
        np.testing.assert_array_equal(winners[0].assigned_start["soc" if mode == "vectorized" else "soc_0"],
            controller._solution_values(result.ac_attempts[1].build)["soc" if mode == "vectorized" else "soc_0"])


def test_public_inner_default_and_invalid_selector():
    with pytest.raises(ValueError, match="inner_temporal_assembly"):
        solve_hierarchical_opf(_inputs(), HierarchicalPolicy(ac_window_steps=3), inner_temporal_assembly="unknown")
    result = solve_hierarchical_opf(_inputs(horizon_steps=1),
        HierarchicalPolicy(ac_window_steps=1, initialization_policy="flat_only"), BOUNDED)
    assert result.completed
    assert result.ac_attempts[0].build.temporal_assembly == "vectorized"
