"""Construction/canonicalization/retention seams; native IPOPT never executes."""

from dataclasses import replace
import json

import numpy as np
import pytest

from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.audit import ProbeAudit
from experiments.case118_annual_hierarchy.p0_fixture import (
    load_p0_fixture,
    policy_sha256,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    execute_prepared_attempt,
    load_retained_start,
    prepare_attempt,
    start_payload,
    selected_controller,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    WindowKey,
)
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.case118_annual_hierarchy.streaming_schema import sha256_path


@pytest.fixture
def fixture_outer():
    """Synthetic accepted signposts, not a numerical experiment or feasibility claim."""
    fixture = load_p0_fixture(6)
    ids = tuple(str(unit.device_id) for unit in fixture.inputs.storage)
    indices = np.arange(7)
    states = np.full((7, 1), 500.0)
    outer = streaming.StreamingOuterPlan(
        outer_plan_id="synthetic-outer",
        build=None,
        result={},
        audit=ProbeAudit("optimal", (), None, {}, True),
        exception=None,
        wall_time_seconds=0,
        storage_device_ids=ids,
        input_fingerprint=streaming.execution_input_sha256(fixture.inputs),
        horizon_steps=6,
        delta_hours=1,
        policy_sha256=policy_sha256(fixture.policy),
        solve_config_sha256=solve_config_sha256(fixture.solve_config),
        temporal_assembly="stepwise",
        canonicalization_backend="CPP",
        signpost_sha256=streaming._signpost_sha256(ids, indices, states),
        global_boundary_indices=indices,
        boundary_soc_mwh=states,
    )
    return fixture, outer


def prepare(fixture, outer, spec=None, *, preceding=None, **kwargs):
    spec = spec or AttemptSpec(WindowKey("synthetic-shard", 0), 0, 0)
    return prepare_attempt(
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        spec,
        {"p0_storage_bus_7": 500},
        preceding,
        trajectory_start=0,
        trajectory_stop=6,
        trajectory_initial_soc_mwh={"p0_storage_bus_7": 500},
        **kwargs,
    )


def intercept_native(monkeypatch, callback=None):
    calls = []

    def intercepted(self, data, warm_start, verbose, solver_opts, solver_cache=None):
        calls.append(np.asarray(data["x0"]).copy())
        if callback is not None:
            callback(data)
        raise RuntimeError("test interception: no native IPOPT solve")

    monkeypatch.setattr(streaming.IPOPT, "solve_via_data", intercepted)
    return calls


def test_start_persisted_before_native_entry_and_exception_result_retained(
    fixture_outer, monkeypatch, tmp_path
):
    fixture, outer = fixture_outer
    prepared = prepare(fixture, outer)
    start = tmp_path / "start.json"
    result = tmp_path / "result.json"

    def at_native(data):
        retained = load_retained_start(start)
        assert np.array_equal(retained.evidence.complete_x0, data["x0"])
        assert not result.exists()

    calls = intercept_native(monkeypatch, at_native)
    outcome = execute_prepared_attempt(
        prepared,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        start_path=start,
        result_path=result,
    )
    assert len(calls) == 1
    assert outcome.record.audit.outcome == "solver_failure"
    assert "test interception" in outcome.record.audit.exception
    payload = json.loads(result.read_text())
    assert payload["selected_for_execution"] is False
    assert payload["start_artifact"] == {
        "relative_path": "start.json",
        "bytes": start.stat().st_size,
        "sha256": sha256_path(start),
    }
    assert "supplied_executed_action" not in payload["attempt"]
    assert "causal_source" not in payload["attempt"]
    assert payload["attempt"]["solver_x0"]
    assert outcome.record.solver_evidence.auxiliary_coordinate_count > 0
    assert {path.name for path in tmp_path.iterdir()} == {"start.json", "result.json"}


def test_observer_failure_prevents_native_execution(fixture_outer, monkeypatch):
    fixture, outer = fixture_outer
    prepared = prepare(fixture, outer)
    calls = intercept_native(monkeypatch)

    def fail(_evidence):
        raise OSError("cannot retain start")

    run = streaming.solve_ac_with_verified_x0(
        prepared.build, fixture.solve_config, start_observer=fail
    )
    assert not calls
    assert "cannot retain start" in run.exception


def test_observer_alone_preserves_default_complete_start(fixture_outer, monkeypatch):
    fixture, outer = fixture_outer
    calls = intercept_native(monkeypatch)
    default = streaming.solve_ac_with_verified_x0(
        prepare(fixture, outer).build, fixture.solve_config
    )
    observed = []
    opt_in = streaming.solve_ac_with_verified_x0(
        prepare(fixture, outer).build,
        fixture.solve_config,
        start_observer=observed.append,
    )
    assert len(calls) == 2
    assert np.array_equal(calls[0], calls[1])
    assert default.evidence.layout_signature == opt_in.evidence.layout_signature
    assert observed[0].object_ids_before == observed[0].object_ids_after


def test_replay_restores_auxiliaries_into_a_fresh_reduction(fixture_outer, monkeypatch):
    fixture, outer = fixture_outer
    first = prepare(fixture, outer)
    calls = intercept_native(monkeypatch)
    initial = streaming.solve_ac_with_verified_x0(first.build, fixture.solve_config)
    evidence = initial.evidence
    assert evidence is not None and evidence.auxiliary_coordinate_count > 0
    changed = evidence.complete_x0.copy()
    auxiliary = next(
        item for item in evidence.layout if not item["is_original_variable"]
    )
    changed[auxiliary["start"] : auxiliary["stop"]] += 0.125
    replay = replace(evidence, complete_x0=changed)
    fresh = prepare(fixture, outer)
    observed = []
    run = streaming.solve_ac_with_verified_x0(
        fresh.build,
        fixture.solve_config,
        replay_start=replay,
        start_observer=observed.append,
    )
    assert len(calls) == 2
    assert np.array_equal(calls[-1], changed)
    assert np.array_equal(observed[0].complete_x0, changed)
    assert np.array_equal(run.evidence.complete_x0, changed)
    assert run.evidence.layout_signature == evidence.layout_signature


@pytest.mark.parametrize("change", ["model", "layout"])
def test_replay_mismatch_is_rejected_before_native(fixture_outer, monkeypatch, change):
    fixture, outer = fixture_outer
    prepared = prepare(fixture, outer)
    calls = intercept_native(monkeypatch)
    evidence = streaming.solve_ac_with_verified_x0(
        prepared.build, fixture.solve_config
    ).evidence
    if change == "layout":
        replay = replace(evidence, layout_signature="wrong")
    else:
        changed = evidence.complete_x0.copy()
        original = next(
            item for item in evidence.layout if item["is_original_variable"]
        )
        changed[original["start"]] += 0.125
        replay = replace(evidence, complete_x0=changed)
    run = streaming.solve_ac_with_verified_x0(
        prepare(fixture, outer).build,
        fixture.solve_config,
        replay_start=replay,
    )
    assert len(calls) == 1
    assert "replay start" in run.exception


def synthetic_preceding(fixture, outer):
    build = streaming.build_window(fixture.inputs, "ac", 0, 3, fixture.inputs.storage)
    values = streaming.complete_flat_start(build)
    for name in values:
        if name.startswith("b_") and not name.startswith("b_q"):
            values[name][:] = 0
        if name.startswith("soc_"):
            values[name][:] = 500
    return streaming.CausalControllerSource(
        attempt_id="ac-000-06-perturbed_causal",
        ordinal=6,
        role="perturbed_causal",
        iteration=0,
        global_interval_start=0,
        global_interval_stop=3,
        outer_plan_id=outer.outer_plan_id,
        storage_device_ids=outer.storage_device_ids,
        initial_soc_mwh={"p0_storage_bus_7": 500},
        first_soc_mwh=np.array([500.0]),
        first_b_mw=np.array([0.0]),
        solution_values=values,
    )


def test_causal_start_uses_existing_shift_and_perturb_without_target_free(
    fixture_outer,
):
    fixture, outer = fixture_outer
    preceding = synthetic_preceding(fixture, outer)
    spec = AttemptSpec(WindowKey("synthetic-shard", 1), 1, 6)
    prepared = prepare(fixture, outer, spec, preceding=preceding)
    _, center = streaming.shifted_start(
        preceding.solution_values,
        prepared.build,
        fixture.inputs,
        fixture.policy,
        prepared.initial,
    )
    raw, assigned = streaming.perturbed_start(
        center,
        prepared.build,
        scale=spec.scale,
        seed=spec.seed,
    )
    assert all(np.array_equal(raw[name], prepared.raw[name]) for name in raw)
    assert all(
        np.array_equal(assigned[name], prepared.assigned[name]) for name in assigned
    )
    assert prepared.source_attempt_id == preceding.attempt_id
    assert prepared.target == outer.target_at(4)


def test_causal_request_checks_physical_state_and_preceding_interval(fixture_outer):
    fixture, outer = fixture_outer
    preceding = synthetic_preceding(fixture, outer)
    spec = AttemptSpec(WindowKey("synthetic-shard", 1), 1, 6)
    with pytest.raises(ValueError, match="preceding iteration"):
        prepare(fixture, outer, spec, preceding=replace(preceding, iteration=1))
    with pytest.raises(ValueError, match="SoC recurrence"):
        prepare(
            fixture,
            outer,
            spec,
            preceding=replace(preceding, first_soc_mwh=np.array([499.0])),
        )


def test_shard_start_causal_and_unaccepted_target_free_sources_rejected(fixture_outer):
    fixture, outer = fixture_outer
    with pytest.raises(ValueError, match="preceding controller"):
        prepare(fixture, outer, AttemptSpec(WindowKey("synthetic-shard", 0), 1, 6))
    with pytest.raises(ValueError, match="target-free source is unavailable"):
        prepare(fixture, outer, AttemptSpec(WindowKey("synthetic-shard", 0), 5, 2))


def test_target_free_has_identical_named_primary_start(fixture_outer):
    fixture, outer = fixture_outer
    preceding = synthetic_preceding(fixture, outer)
    window = WindowKey("synthetic-shard", 1)
    primary = prepare(fixture, outer, AttemptSpec(window, 0, 0), preceding=preceding)
    free = prepare(fixture, outer, AttemptSpec(window, 4, 1), preceding=preceding)
    assert primary.request_sha256 == free.request_sha256
    assert all(
        np.array_equal(value, free.assigned[name])
        for name, value in primary.assigned.items()
    )
    assert len(primary.build.prob.constraints) > len(free.build.prob.constraints)


def test_bounded_packet_restores_uncapped_original_start(
    fixture_outer, monkeypatch, tmp_path
):
    fixture, outer = fixture_outer
    preceding = synthetic_preceding(fixture, outer)
    spec = AttemptSpec(WindowKey("synthetic-shard", 1), 1, 6)
    bounded = prepare(fixture, outer, spec, preceding=preceding)
    intercept_native(monkeypatch)
    packet = tmp_path / "start.json"
    execute_prepared_attempt(
        bounded,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        start_path=packet,
        result_path=tmp_path / "result.json",
    )
    retained = load_retained_start(packet)
    invocation = AttemptSpec(spec.window, 9, 6, replay_of=spec.attempt_id)
    uncapped = prepare(fixture, outer, invocation, preceding=preceding, replay=retained)
    assert uncapped.replay_start is not None
    assert all(
        np.array_equal(value, bounded.assigned[name])
        for name, value in uncapped.assigned.items()
    )
    with pytest.raises(ValueError, match="exact bounded"):
        prepare(
            fixture,
            outer,
            invocation,
            preceding=preceding,
            replay=replace(retained, request_sha256="wrong"),
        )


@pytest.mark.parametrize("change", ["assigned", "layout", "namespace", "raw"])
def test_start_packet_detects_accidental_corruption(
    fixture_outer, monkeypatch, tmp_path, change
):
    fixture, outer = fixture_outer
    prepared = prepare(fixture, outer)
    intercept_native(monkeypatch)
    evidence = streaming.solve_ac_with_verified_x0(
        prepared.build, fixture.solve_config
    ).evidence
    payload = start_payload(prepared, evidence)
    name = next(iter(payload["assigned_start"]))
    if change == "assigned":
        payload["assigned_start"][name] = (
            np.asarray(payload["assigned_start"][name]) + 1
        ).tolist()
    elif change == "layout":
        payload["layout_signature"] = "wrong"
    elif change == "namespace":
        del payload["assigned_start"][name]
    else:
        payload["raw_start"][name] = [0, 0, 0, 0]
    path = tmp_path / "packet.json"
    atomic_immutable_json(path, payload)
    with pytest.raises(ValueError):
        load_retained_start(path)


def test_existing_paths_rejected_before_native(fixture_outer, monkeypatch, tmp_path):
    fixture, outer = fixture_outer
    prepared = prepare(fixture, outer)
    calls = intercept_native(monkeypatch)
    start = tmp_path / "start.json"
    atomic_immutable_json(start, {"retained": True})
    with pytest.raises(FileExistsError):
        execute_prepared_attempt(
            prepared,
            fixture.inputs,
            fixture.policy,
            fixture.solve_config,
            outer,
            start_path=start,
            result_path=tmp_path / "result.json",
        )
    assert not calls


def install_synthetic_acceptance(monkeypatch):
    """Model only the lifecycle seam; these flat values are NOT AC solutions."""
    intercept_native(monkeypatch)
    real_capture = streaming.solve_ac_with_verified_x0

    def synthetic_solve(build, config, **kwargs):
        run = real_capture(build, config, **kwargs)
        assert "test interception" in run.exception
        for name, variable in streaming.variables_by_name(build).items():
            if name.startswith("b_") and not name.startswith("b_q"):
                variable.value = np.zeros(variable.shape)
            if name.startswith("soc_"):
                variable.value = np.full(variable.shape, 500.0)
        build.prob._status = "optimal"
        build.prob._value = 100.0
        return replace(run, exception=None)

    monkeypatch.setattr(streaming, "solve_ac_with_verified_x0", synthetic_solve)
    monkeypatch.setattr(
        streaming,
        "audit_probe",
        lambda *args, **kwargs: ProbeAudit("optimal", (), None, {}, True),
    )


def test_candidate_not_selected_until_coordinator_and_next_window_shift_works(
    fixture_outer, monkeypatch, tmp_path
):
    fixture, outer = fixture_outer
    install_synthetic_acceptance(monkeypatch)
    prepared = prepare(fixture, outer)
    result = execute_prepared_attempt(
        prepared,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        start_path=tmp_path / "start.json",
        result_path=tmp_path / "result.json",
    )
    payload = json.loads((tmp_path / "result.json").read_text())
    assert payload["attempt"]["audit"]["accepted_primal"] is True
    assert payload["selected_for_execution"] is False
    with pytest.raises(ValueError, match="not the selected"):
        selected_controller(result, AttemptSpec(prepared.invocation.window, 1, 6))
    selected = selected_controller(result, prepared.invocation)
    next_spec = AttemptSpec(WindowKey("synthetic-shard", 1), 0, 0)
    following = prepare(fixture, outer, next_spec, preceding=selected)
    assert following.source_attempt_id == prepared.invocation.attempt_id
    assert following.initial == {"p0_storage_bus_7": 500}
    assert following.target == outer.target_at(4)
    for name, value in following.assigned.items():
        if name.startswith("soc_"):
            assert value.tolist() == [500.0]


def test_accepted_target_free_copy_is_available_but_cannot_supply_action(
    fixture_outer, monkeypatch, tmp_path
):
    fixture, outer = fixture_outer
    install_synthetic_acceptance(monkeypatch)
    spec = AttemptSpec(WindowKey("synthetic-shard", 0), 4, 1)
    prepared = prepare(fixture, outer, spec)
    result = execute_prepared_attempt(
        prepared,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        start_path=tmp_path / "start.json",
        result_path=tmp_path / "result.json",
    )
    with pytest.raises(ValueError, match="hard-target"):
        selected_controller(result, spec)
    copied = prepare(fixture, outer, AttemptSpec(spec.window, 5, 2), target_free=result)
    assert copied.source_attempt_id == spec.attempt_id
    center = result.target_free_values(copied.request_sha256)
    assert all(
        np.array_equal(value, center[name]) for name, value in copied.assigned.items()
    )
    with pytest.raises(ValueError, match="different request"):
        result.target_free_values("wrong")


def test_nonzero_shard_start_primary_is_labeled_flat(
    fixture_outer, monkeypatch, tmp_path
):
    fixture, outer = fixture_outer
    intercept_native(monkeypatch)
    spec = AttemptSpec(WindowKey("synthetic-shard", 3), 0, 0)
    prepared = prepare_attempt(
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        spec,
        {"p0_storage_bus_7": 500},
        None,
        trajectory_start=3,
        trajectory_stop=6,
        trajectory_initial_soc_mwh={"p0_storage_bus_7": 500},
    )
    result = execute_prepared_attempt(
        prepared,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        start_path=tmp_path / "start.json",
        result_path=tmp_path / "result.json",
    )
    assert result.record.transformation == "flat"
