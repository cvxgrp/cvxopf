"""P0b.3 tests for immutable streaming persistence."""

from dataclasses import replace
import json

import numpy as np
import pytest

from cvxopf import HierarchicalSolveAudit

from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy.streaming_archive import (
    attempt_archive_payload,
    persist_window_transaction,
    window_archive_payload,
    write_checkpoint_after_success,
    write_verified_outer_plan_archive,
    write_verified_window_archive,
)
from experiments.case118_annual_hierarchy.streaming_runner import (
    StreamingWindowResult,
    execute_streaming_window,
    snapshot_inputs,
    solve_frozen_outer,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    load_verified_checkpoint,
)


@pytest.fixture(scope="module")
def archived_window_source():
    fixture = load_p0_fixture(6)
    inputs = snapshot_inputs(fixture.inputs)
    outer = solve_frozen_outer(inputs, fixture.policy, fixture.solve_config)
    window = execute_streaming_window(
        inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        0,
        {"p0_storage_bus_7": 500.0},
        None,
    )
    payload = window_archive_payload(
        window,
        inputs=inputs,
        policy=fixture.policy,
        outer=outer,
        preceding_controlling_attempt_id=None,
    )
    return fixture, inputs, outer, window, payload


def _contains_key(value, target):
    if isinstance(value, dict):
        return target in value or any(
            _contains_key(item, target) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(item, target) for item in value)
    return False


def test_window_projection_is_complete_build_free_and_json_safe(
    archived_window_source,
):
    _fixture, _inputs, _outer, _window, payload = archived_window_source

    assert len(payload["attempts"]) == 9
    assert not _contains_key(payload, "build")
    assert payload["executed_interval"]["controlling_attempt_id"] == (
        "ac-000-00-primary_controlling"
    )
    assert payload["attempts"][0]["solver_x0"]
    assert payload["attempts"][0]["structural_signature"]
    json.dumps(payload, allow_nan=False)


def test_archive_is_detached_from_live_result_arrays(archived_window_source):
    _fixture, _inputs, _outer, window, payload = archived_window_source
    attempt = window.attempts[0]
    assert attempt.result is not None
    archived_pg = np.asarray(payload["attempts"][0]["result"]["Pg"]).copy()
    live_pg = np.asarray(attempt.result["Pg"])

    assert not np.shares_memory(archived_pg, live_pg)
    assert np.array_equal(archived_pg, live_pg)


def test_unsuccessful_attempt_normalizes_unavailable_scalar_results(
    archived_window_source,
):
    fixture, _inputs, _outer, window, _payload = archived_window_source
    accepted = window.attempts[0]
    assert accepted.result is not None
    unsuccessful_result = dict(accepted.result)
    unsuccessful_result["status"] = "user_limit"
    unsuccessful_result["objective"] = float("nan")
    unsuccessful_audit = HierarchicalSolveAudit(
        status="user_limit",
        outcome="unusable_primal",
        accepted_primal=False,
        missing_or_nonfinite_fields=("objective",),
        identity_error=None,
        residuals=accepted.audit.residuals,
        exception=None,
        wall_time_seconds=0.1,
        solver_num_iters=5,
        solver_setup_time_seconds=0.01,
        solver_solve_time_seconds=0.08,
    )
    unsuccessful = replace(
        accepted,
        result=unsuccessful_result,
        audit=unsuccessful_audit,
        supplied_executed_action=False,
    )

    payload = attempt_archive_payload(
        unsuccessful, result_dimensions=fixture.result_dimensions
    )
    assert payload["result"]["objective"] is None
    json.dumps(payload, allow_nan=False)


@pytest.fixture(scope="module")
def unsuccessful_window(archived_window_source):
    _fixture, _inputs, _outer, nominal, _payload = archived_window_source
    base = nominal.attempts[0]
    assert base.result is not None
    assert base.audit is not None
    result = dict(base.result)
    result["status"] = "user_limit"
    result["objective"] = float("nan")
    audit = HierarchicalSolveAudit(
        status="user_limit",
        outcome="unusable_primal",
        accepted_primal=False,
        missing_or_nonfinite_fields=("objective",),
        identity_error=None,
        residuals=base.audit.residuals,
        exception=None,
        wall_time_seconds=0.1,
        solver_num_iters=5,
        solver_setup_time_seconds=0.01,
        solver_solve_time_seconds=0.08,
    )

    def executed_from(template, *, target_free=False):
        return replace(
            template,
            slot_state="executed",
            source_kind="generated_flat",
            source_attempt_id=None,
            build=base.build,
            raw_start=base.raw_start,
            assigned_start=base.assigned_start,
            solver_evidence=base.solver_evidence,
            result=result,
            audit=audit,
            terminal_deviation_mwh=(
                None if target_free else base.terminal_deviation_mwh
            ),
            reason=None,
            supplied_executed_action=False,
        )

    attempts = [executed_from(nominal.attempts[0])]
    attempts.append(executed_from(nominal.attempts[1], target_free=True))
    attempts.extend(
        replace(
            nominal.attempts[ordinal],
            slot_state="source_unavailable",
            reason="target-free solve was not accepted",
        )
        for ordinal in range(2, 6)
    )
    attempts.extend(executed_from(nominal.attempts[ordinal]) for ordinal in range(6, 9))
    return StreamingWindowResult(0, 3, tuple(attempts), None, None)


def test_unsuccessful_window_is_archived_without_checkpoint_advancement(
    tmp_path, archived_window_source, unsuccessful_window
):
    fixture, inputs, outer, _nominal, _payload = archived_window_source
    outer_entry = write_verified_outer_plan_archive(
        tmp_path / "outer-plan.json.gz",
        outer,
        inputs=inputs,
        source_fingerprint="source-fingerprint",
        scenario_hash="scenario-hash",
    )
    transaction = persist_window_transaction(
        tmp_path,
        unsuccessful_window,
        inputs=inputs,
        policy=fixture.policy,
        outer=outer,
        outer_plan_artifact=outer_entry,
        preceding_controlling_attempt_id=None,
        source_fingerprint="source-fingerprint",
        scenario_hash="scenario-hash",
        policy_hash=fixture.policy_sha256,
        initial_soc_mwh=[500.0],
        completed_entries=(),
    )

    assert transaction.artifact.relative_path == "failed-window-000000.json.gz"
    assert transaction.checkpoint is None
    assert transaction.completed_entries == ()
    assert not (tmp_path / "checkpoint.json").exists()


def test_atomic_window_then_checkpoint_round_trip(tmp_path, archived_window_source):
    fixture, inputs, outer, window, payload = archived_window_source
    artifact_path = tmp_path / "window-000.json.gz"
    outer_entry = write_verified_outer_plan_archive(
        tmp_path / "outer-plan.json.gz",
        outer,
        inputs=inputs,
        source_fingerprint="source-fingerprint",
        scenario_hash="scenario-hash",
    )
    entry = write_verified_window_archive(
        artifact_path,
        payload,
        inputs=inputs,
        policy=fixture.policy,
        outer=outer,
    )
    assert artifact_path.is_file()
    assert entry.iteration == 0
    assert entry.bytes == artifact_path.stat().st_size
    assert window.post_step_soc_mwh is not None
    realized = [window.post_step_soc_mwh["p0_storage_bus_7"]]
    checkpoint_path = tmp_path / "checkpoint.json"
    checkpoint = write_checkpoint_after_success(
        checkpoint_path,
        source_fingerprint="source-fingerprint",
        scenario_hash="scenario-hash",
        outer_plan_sha256=outer_entry.sha256,
        policy_hash=fixture.policy_sha256,
        storage_device_ids=fixture.storage_device_ids,
        initial_soc_mwh=[500.0],
        realized_soc_mwh=realized,
        entries=[entry],
    )

    boundaries = {
        boundary: outer.target_at(boundary)
        for boundary in range(inputs.horizon_steps + 1)
    }
    verified = load_verified_checkpoint(
        checkpoint_path,
        expected_source_fingerprint="source-fingerprint",
        expected_scenario_hash="scenario-hash",
        expected_outer_plan_sha256=outer_entry.sha256,
        expected_policy_hash=fixture.policy_sha256,
        expected_soc_tolerance_mwh=(fixture.policy.tolerances.soc_recurrence_mwh_abs),
        expected_residual_tolerances={
            name: getattr(fixture.policy.tolerances, name)
            for name in fixture.policy.tolerances.__dataclass_fields__
        },
        expected_inner_terminal_policy="hard_equality",
        expected_horizon_steps=inputs.horizon_steps,
        expected_ac_window_steps=fixture.policy.ac_window_steps,
        expected_result_dimensions=fixture.result_dimensions,
        expected_delta_hours=inputs.delta,
        expected_outer_boundary_soc_mwh=boundaries,
    )
    assert verified == checkpoint


def test_transaction_archives_before_advancing_checkpoint(
    tmp_path, archived_window_source
):
    fixture, inputs, outer, window, _payload = archived_window_source
    outer_entry = write_verified_outer_plan_archive(
        tmp_path / "outer-plan.json.gz",
        outer,
        inputs=inputs,
        source_fingerprint="source-fingerprint",
        scenario_hash="scenario-hash",
    )
    transaction = persist_window_transaction(
        tmp_path,
        window,
        inputs=inputs,
        policy=fixture.policy,
        outer=outer,
        outer_plan_artifact=outer_entry,
        preceding_controlling_attempt_id=None,
        source_fingerprint="source-fingerprint",
        scenario_hash="scenario-hash",
        policy_hash=fixture.policy_sha256,
        initial_soc_mwh=[500.0],
        completed_entries=(),
    )

    assert transaction.artifact.relative_path == "window-000000.json.gz"
    assert transaction.checkpoint is not None
    assert transaction.completed_entries == (transaction.artifact,)
    assert (tmp_path / transaction.artifact.relative_path).is_file()
    assert (tmp_path / "checkpoint.json").is_file()


def test_transaction_does_not_write_checkpoint_if_archive_write_fails(
    monkeypatch, tmp_path, archived_window_source
):
    fixture, inputs, outer, window, _payload = archived_window_source
    outer_entry = write_verified_outer_plan_archive(
        tmp_path / "outer-plan.json.gz",
        outer,
        inputs=inputs,
        source_fingerprint="source-fingerprint",
        scenario_hash="scenario-hash",
    )

    def fail_write(*_args, **_kwargs):
        raise OSError("synthetic archive failure")

    monkeypatch.setattr(
        "experiments.case118_annual_hierarchy.streaming_archive."
        "write_verified_window_archive",
        fail_write,
    )
    with pytest.raises(OSError, match="synthetic archive failure"):
        persist_window_transaction(
            tmp_path,
            window,
            inputs=inputs,
            policy=fixture.policy,
            outer=outer,
            outer_plan_artifact=outer_entry,
            preceding_controlling_attempt_id=None,
            source_fingerprint="source-fingerprint",
            scenario_hash="scenario-hash",
            policy_hash=fixture.policy_sha256,
            initial_soc_mwh=[500.0],
            completed_entries=(),
        )
    assert not (tmp_path / "checkpoint.json").exists()


def test_window_and_outer_artifacts_cannot_be_replaced(
    tmp_path, archived_window_source
):
    fixture, inputs, outer, _window, payload = archived_window_source
    outer_path = tmp_path / "outer-plan.json.gz"
    window_path = tmp_path / "window-000000.json.gz"
    write_verified_outer_plan_archive(
        outer_path,
        outer,
        inputs=inputs,
        source_fingerprint="source-fingerprint",
        scenario_hash="scenario-hash",
    )
    write_verified_window_archive(
        window_path,
        payload,
        inputs=inputs,
        policy=fixture.policy,
        outer=outer,
    )
    outer_bytes = outer_path.read_bytes()
    window_bytes = window_path.read_bytes()

    with pytest.raises(FileExistsError):
        write_verified_outer_plan_archive(
            outer_path,
            outer,
            inputs=inputs,
            source_fingerprint="source-fingerprint",
            scenario_hash="scenario-hash",
        )
    with pytest.raises(FileExistsError):
        write_verified_window_archive(
            window_path,
            payload,
            inputs=inputs,
            policy=fixture.policy,
            outer=outer,
        )
    assert outer_path.read_bytes() == outer_bytes
    assert window_path.read_bytes() == window_bytes
