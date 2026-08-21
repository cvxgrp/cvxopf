"""Focused P0b tests for the public-builder streaming execution seams."""

from dataclasses import replace

import numpy as np
import pytest

from cvxopf import HierarchicalSolveAudit, LayerSolveConfig, ShiftedRecoveryConfig

from experiments.case118_annual_hierarchy import streaming_runner

from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy.streaming_runner import (
    assign_start,
    build_window,
    complete_flat_start,
    execute_streaming_window,
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


@pytest.fixture(scope="module")
def nominal_window():
    fixture = load_p0_fixture(6)
    snapshot = snapshot_inputs(fixture.inputs)
    outer = solve_frozen_outer(snapshot, fixture.policy, fixture.solve_config)
    window = execute_streaming_window(
        snapshot,
        fixture.policy,
        fixture.solve_config,
        outer,
        0,
        {"p0_storage_bus_7": 500.0},
        None,
    )
    return fixture, snapshot, outer, window


def test_nominal_window_captures_complete_x0_and_advances_exactly_once(
    nominal_window,
):
    _fixture, _snapshot, _outer, window = nominal_window

    assert len(window.attempts) == 9
    assert [attempt.attempt_id for attempt in window.attempts] == [
        "ac-000-00-primary_controlling",
        "ac-000-01-target_free",
        "ac-000-02-copied_target_free",
        "ac-000-03-perturbed_target_free",
        "ac-000-04-perturbed_target_free",
        "ac-000-05-perturbed_target_free",
        "ac-000-06-perturbed_causal",
        "ac-000-07-perturbed_causal",
        "ac-000-08-perturbed_causal",
    ]
    assert [attempt.scale for attempt in window.attempts[3:]] == [
        1e-4, 1e-3, 1e-2, 1e-4, 1e-3, 1e-2
    ]
    assert [attempt.seed for attempt in window.attempts[3:]] == [
        17_000_011,
        17_000_012,
        17_000_013,
        17_000_021,
        17_000_022,
        17_000_023,
    ]
    assert window.controlling_attempt is window.attempts[0]
    assert window.attempts[0].attempt_id == "ac-000-00-primary_controlling"
    assert window.attempts[0].slot_state == "executed"
    assert all(
        attempt.slot_state == "not_needed_after_acceptance"
        for attempt in window.attempts[1:]
    )
    evidence = window.attempts[0].solver_evidence
    assert evidence is not None
    assert evidence.model_coordinate_count + evidence.auxiliary_coordinate_count == (
        evidence.complete_x0.size
    )
    assert evidence.object_ids_before == evidence.object_ids_after
    assigned = window.attempts[0].assigned_start
    assert assigned is not None
    original_coordinates = 0
    auxiliary_coordinates = 0
    for item in evidence.layout:
        start = int(item["start"])
        stop = int(item["stop"])
        if bool(item["is_original_variable"]):
            values = np.asarray(assigned[str(item["name"])], dtype=float)
            assert np.array_equal(
                evidence.complete_x0[start:stop], values.flatten(order="F")
            )
            original_coordinates += stop - start
        else:
            auxiliary_coordinates += stop - start
    assert original_coordinates == evidence.model_coordinate_count
    assert auxiliary_coordinates == evidence.auxiliary_coordinate_count
    result = window.attempts[0].result
    assert result is not None
    expected = float(np.asarray(result["soc"], dtype=float).reshape(3, 1)[0, 0])
    assert window.post_step_soc_mwh == {
        "p0_storage_bus_7": pytest.approx(expected)
    }


def test_copied_target_free_recovery_selects_first_controller(
    monkeypatch, nominal_window
):
    fixture, snapshot, outer, nominal = nominal_window
    base = nominal.attempts[0]
    rejected_audit = HierarchicalSolveAudit(
        status="optimal",
        outcome="unusable_primal",
        accepted_primal=False,
        missing_or_nonfinite_fields=("synthetic_gate",),
        identity_error=None,
        residuals=base.audit.residuals,
        exception=None,
        wall_time_seconds=0.0,
        solver_num_iters=0,
        solver_setup_time_seconds=0.0,
        solver_solve_time_seconds=0.0,
    )

    def injected(*args, **kwargs):
        slot = args[8]
        if slot.ordinal == 0:
            return replace(
                base,
                audit=rejected_audit,
                supplied_executed_action=False,
            )
        if slot.ordinal == 1:
            return replace(
                base,
                attempt_id="ac-000-01-target_free",
                role="target_free",
                transformation="flat",
                ordinal=1,
                terminal_deviation_mwh=None,
                supplied_executed_action=False,
            )
        assert slot.ordinal == 2
        return replace(
            base,
            attempt_id="ac-000-02-copied_target_free",
            role="copied_target_free",
            transformation="copy_target_free",
            ordinal=2,
            source_kind="attempt",
            source_attempt_id="ac-000-01-target_free",
        )

    monkeypatch.setattr(streaming_runner, "_execute_attempt", injected)
    window = execute_streaming_window(
        snapshot,
        fixture.policy,
        fixture.solve_config,
        outer,
        0,
        {"p0_storage_bus_7": 500.0},
        None,
    )

    assert window.controlling_attempt is window.attempts[2]
    assert [attempt.slot_state for attempt in window.attempts[3:]] == [
        "not_needed_after_acceptance"
    ] * 6
    assert window.post_step_soc_mwh is not None


def test_later_window_uses_immediately_preceding_controller_as_causal_source(
    nominal_window,
):
    fixture, snapshot, outer, first = nominal_window
    assert first.controlling_attempt is not None
    assert first.post_step_soc_mwh is not None

    second = execute_streaming_window(
        snapshot,
        fixture.policy,
        fixture.solve_config,
        outer,
        1,
        first.post_step_soc_mwh,
        first.controlling_attempt,
    )

    assert second.attempts[0].source_kind == "attempt"
    assert second.attempts[0].source_attempt_id == (
        first.controlling_attempt.attempt_id
    )
    assert second.attempts[0].transformation == "shifted_preceding"
    assert second.controlling_attempt is not None
    assert second.post_step_soc_mwh is not None


def test_iteration_zero_rejects_an_unexpected_predecessor(nominal_window):
    fixture, snapshot, outer, first = nominal_window
    assert first.controlling_attempt is not None

    with pytest.raises(ValueError, match="iteration zero"):
        execute_streaming_window(
            snapshot,
            fixture.policy,
            fixture.solve_config,
            outer,
            0,
            {"p0_storage_bus_7": 500.0},
            first.controlling_attempt,
        )


def test_later_window_requires_a_preceding_controller(nominal_window):
    fixture, snapshot, outer, first = nominal_window
    assert first.post_step_soc_mwh is not None

    with pytest.raises(ValueError, match="require the preceding controller"):
        execute_streaming_window(
            snapshot,
            fixture.policy,
            fixture.solve_config,
            outer,
            1,
            first.post_step_soc_mwh,
            None,
        )


def test_iteration_zero_requires_the_frozen_initial_state(nominal_window):
    fixture, snapshot, outer, _first = nominal_window

    with pytest.raises(ValueError, match="physical state handoff"):
        execute_streaming_window(
            snapshot,
            fixture.policy,
            fixture.solve_config,
            outer,
            0,
            {"p0_storage_bus_7": 501.0},
            None,
        )


def test_later_window_rejects_discontinuous_realized_state(nominal_window):
    fixture, snapshot, outer, first = nominal_window
    assert first.controlling_attempt is not None
    assert first.post_step_soc_mwh is not None
    discontinuous = {
        key: value + 1.0 for key, value in first.post_step_soc_mwh.items()
    }

    with pytest.raises(ValueError, match="physical state handoff"):
        execute_streaming_window(
            snapshot,
            fixture.policy,
            fixture.solve_config,
            outer,
            1,
            discontinuous,
            first.controlling_attempt,
        )


def test_later_window_rejects_malformed_causal_sources(nominal_window):
    fixture, snapshot, outer, first = nominal_window
    base = first.controlling_attempt
    assert base is not None
    assert first.post_step_soc_mwh is not None
    target_free = replace(
        base,
        attempt_id="ac-000-01-target_free",
        role="target_free",
        ordinal=1,
        supplied_executed_action=False,
        terminal_deviation_mwh=None,
    )
    current = replace(
        base,
        iteration=1,
        global_interval_start=1,
        global_interval_stop=4,
    )
    wrong_interval = replace(base, global_interval_stop=2, local_interval_stop=2)
    wrong_identity = replace(
        base,
        storage_device_ids=("other-storage",),
        initial_soc_mwh={"other-storage": 500.0},
        target_soc_mwh={"other-storage": 500.0},
        terminal_deviation_mwh={"other-storage": 0.0},
    )
    wrong_id = replace(base, attempt_id="corrupted-attempt-id")
    wrong_ordinal = replace(
        base,
        attempt_id="ac-000-02-copied_target_free",
        ordinal=2,
    )
    wrong_role = replace(base, role="copied_target_free")

    for malformed, match, iteration in (
        (target_free, "accepted controlling", 1),
        (current, "immediately preceding", 1),
        (base, "immediately preceding", 2),
        (wrong_interval, "wrong preceding-window", 1),
        (wrong_identity, "storage identities", 1),
        (wrong_id, "attempt ID", 1),
        (wrong_ordinal, "role does not match", 1),
        (wrong_role, "role does not match", 1),
    ):
        with pytest.raises(ValueError, match=match):
            execute_streaming_window(
                snapshot,
                fixture.policy,
                fixture.solve_config,
                outer,
                iteration,
                first.post_step_soc_mwh,
                malformed,
            )


def test_window_rejects_outer_plan_from_another_horizon(nominal_window):
    _fixture, _snapshot, outer, _first = nominal_window
    other = load_p0_fixture(24)
    other_snapshot = snapshot_inputs(other.inputs)

    with pytest.raises(ValueError, match="execution input snapshot"):
        execute_streaming_window(
            other_snapshot,
            other.policy,
            other.solve_config,
            outer,
            0,
            {"p0_storage_bus_7": 500.0},
            None,
        )


@pytest.mark.parametrize("mutation", ["profile", "network"])
def test_window_rejects_outer_plan_after_snapshot_drift(
    nominal_window, mutation
):
    fixture, _snapshot, outer, _first = nominal_window
    changed = snapshot_inputs(fixture.inputs)
    if mutation == "profile":
        changed.df_load_p.iloc[0, 0] += 1.0
    else:
        changed_case = {
            name: value.copy() if isinstance(value, np.ndarray) else value
            for name, value in changed.case.items()
        }
        np.asarray(changed_case["branch"])[0, 2] += 1e-3
        changed = replace(changed, case=changed_case)

    with pytest.raises(ValueError, match="execution input snapshot"):
        execute_streaming_window(
            changed,
            fixture.policy,
            fixture.solve_config,
            outer,
            0,
            {"p0_storage_bus_7": 500.0},
            None,
        )


@pytest.mark.parametrize("field", ["policy_sha256", "solve_config_sha256"])
def test_window_rejects_corrupted_outer_configuration_hash(
    nominal_window, field
):
    fixture, snapshot, outer, _first = nominal_window
    changed_outer = replace(outer, **{field: "0" * 64})

    with pytest.raises(ValueError, match="outer plan .* hash"):
        execute_streaming_window(
            snapshot,
            fixture.policy,
            fixture.solve_config,
            changed_outer,
            0,
            {"p0_storage_bus_7": 500.0},
            None,
        )


def test_outer_signpost_arrays_are_read_only(nominal_window):
    _fixture, _snapshot, outer, _first = nominal_window
    assert outer.boundary_soc_mwh is not None

    with pytest.raises(ValueError, match="read-only"):
        outer.global_boundary_indices[0] = 1
    with pytest.raises(ValueError, match="read-only"):
        outer.boundary_soc_mwh[0, 0] += 1.0


def test_outer_signpost_hash_rejects_replacement_or_forced_mutation(
    nominal_window,
):
    _fixture, _snapshot, outer, _first = nominal_window
    assert outer.boundary_soc_mwh is not None
    altered = outer.boundary_soc_mwh.copy()
    altered[3, 0] += 1.0

    with pytest.raises(ValueError, match="signpost integrity hash"):
        replace(outer, boundary_soc_mwh=altered)

    tampered = replace(outer)
    assert tampered.boundary_soc_mwh is not None
    tampered.boundary_soc_mwh.setflags(write=True)
    tampered.boundary_soc_mwh[3, 0] += 1.0
    with pytest.raises(ValueError, match="signpost integrity hash"):
        tampered.target_at(3)


def test_recovery_exhaustion_never_advances_state(monkeypatch, nominal_window):
    fixture, snapshot, outer, _nominal = nominal_window

    def fail_construction(*args, **kwargs):
        slot = args[8]
        return streaming_runner._empty_attempt(
            snapshot,
            fixture.policy,
            outer,
            0,
            3,
            {"p0_storage_bus_7": 500.0},
            outer.target_at(3),
            slot,
            "construction_error",
            "synthetic construction failure",
            source_kind=kwargs["source_kind"],
            source_attempt_id=kwargs["source_attempt_id"],
        )

    monkeypatch.setattr(streaming_runner, "_execute_attempt", fail_construction)
    window = execute_streaming_window(
        snapshot,
        fixture.policy,
        fixture.solve_config,
        outer,
        0,
        {"p0_storage_bus_7": 500.0},
        None,
    )

    assert len(window.attempts) == 9
    assert window.controlling_attempt is None
    assert window.post_step_soc_mwh is None
