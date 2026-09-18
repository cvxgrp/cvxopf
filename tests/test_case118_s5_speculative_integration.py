"""Real non-solving subprocesses and synthetic science adapters; no OPF solves."""

from dataclasses import asdict
import hashlib
import json
import os
import sys

import pytest

from experiments.case118_annual_hierarchy import s5_speculative_archive as archives
from experiments.case118_annual_hierarchy import s5_speculative_runtime as runtime
from experiments.case118_annual_hierarchy import (
    s5_speculative_worker as speculative_worker,
)
from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.audit import ProbeAudit
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    execute_prepared_attempt,
)
from experiments.case118_annual_hierarchy.s5_speculative_continuation import (
    load_record,
    policy_identity,
    RECORD_NAME,
    CLASSIFICATION,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    POLICY_NAME,
    WindowKey,
)
from experiments.case118_annual_hierarchy.s5_speculative_process import (
    SubprocessBackend,
    artifact_ref,
)
from experiments.case118_annual_hierarchy.s5_speculative_window import (
    _validate_final_recovery,
    publish_window,
    finalize_window,
)
from experiments.case118_annual_hierarchy.s5_speculative_worker import (
    source_payload,
    restore_source,
)
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.streaming_schema import (
    AC_COMMON_RESIDUAL_NAMES,
    atomic_json,
    atomic_immutable_json,
)
from tests import test_case118_s5_speculative_attempt as attempt_tests
from tests.test_case118_s5_speculative_attempt import (
    prepare,
    intercept_native,
    install_synthetic_acceptance,
)  # noqa: F401

fixture_outer = attempt_tests.fixture_outer


def test_post_solve_unrelated_worktree_drift_is_recorded_not_rejected(
    tmp_path, monkeypatch
):
    start = {
        "git_commit": "a" * 40,
        "git_clean": True,
        "source_fingerprint": "b" * 64,
        "scenario_sha256": "c" * 64,
    }
    observed = {**start, "git_clean": False, "git_commit": "new-unrelated-commit"}
    monkeypatch.setattr(speculative_worker, "execution_context", lambda: observed)
    speculative_worker.verify_end_context(tmp_path, start)
    note = json.loads((tmp_path / "end-context.json").read_text())
    assert note["classification"] == "non_source_worktree_change_after_solve"
    assert note["start_context"] == start
    assert note["end_context"] == observed


def test_new_candidate_keeps_bound_wave_context_during_unrelated_drift(
    tmp_path, monkeypatch
):
    bound = {
        "git_commit": "a" * 40,
        "git_clean": True,
        "source_fingerprint": "b" * 64,
    }
    monkeypatch.setattr(
        speculative_worker,
        "execution_context",
        lambda: {**bound, "git_clean": False, "git_commit": "new-unrelated-commit"},
    )
    assert (
        speculative_worker.bound_execution_context(
            tmp_path, {"execution_context": bound}
        )
        == bound
    )
    note = json.loads((tmp_path / "entry-context.json").read_text())
    assert note["classification"] == "non_source_worktree_change_before_solve"


def test_new_candidate_rejects_executable_source_drift(tmp_path, monkeypatch):
    bound = {
        "git_commit": "a" * 40,
        "git_clean": True,
        "source_fingerprint": "b" * 64,
    }
    monkeypatch.setattr(
        speculative_worker,
        "execution_context",
        lambda: {**bound, "git_clean": False, "source_fingerprint": "changed"},
    )
    with pytest.raises(ValueError, match="source changed"):
        speculative_worker.bound_execution_context(
            tmp_path, {"execution_context": bound}
        )


@pytest.mark.parametrize("field", ["source_fingerprint", "scenario_sha256"])
def test_post_solve_scientific_identity_change_is_rejected(
    tmp_path, monkeypatch, field
):
    start = {
        "git_commit": "a" * 40,
        "git_clean": True,
        "source_fingerprint": "b" * 64,
        "scenario_sha256": "c" * 64,
    }
    monkeypatch.setattr(
        speculative_worker,
        "execution_context",
        lambda: {**start, "git_clean": False, field: "changed"},
    )
    with pytest.raises(ValueError, match="execution context changed"):
        speculative_worker.verify_end_context(tmp_path, start)
    assert not (tmp_path / "end-context.json").exists()


def test_target_free_source_selection_is_confined_to_current_window(tmp_path):
    adapter = object.__new__(runtime.WaveAdapter)
    adapter.output_root = tmp_path
    adapter.authority_path = tmp_path / "authority.json"
    adapter.context = {"source_fingerprint": "test"}
    adapter.contract_sha256 = "contract"
    adapter.fixture, adapter.outer = object(), object()
    current = WindowKey("s4b-shard-000", 5)
    other = WindowKey("s4b-shard-001", 5)
    shard = adapter.shard_directory(current)
    atomic_json(
        shard / "checkpoint.json",
        {
            "next_global_iteration": 5,
            "windows": [],
            "preceding_controlling_attempt_id": None,
        },
    )
    current_free = AttemptSpec(current, 4, 1)
    other_free = AttemptSpec(
        other, 10, 1, replay_of=AttemptSpec(other, 4, 1).attempt_id
    )
    adapter.directories = {}
    for spec in (current_free, other_free):
        directory = tmp_path / spec.window.shard_id / f"candidate-{spec.order}"
        atomic_json(
            directory / "result.json",
            {
                "invocation": asdict(spec),
                "attempt": {"audit": {"accepted_primal": True}},
            },
        )
        adapter.directories[spec.attempt_id] = directory
    request_directory = tmp_path / "request"
    copied = AttemptSpec(current, 5, 2)
    adapter.command(copied, request_directory)
    request = json.loads((request_directory / "request.json").read_text())
    assert request["target_free_directory"] == str(
        adapter.directories[current_free.attempt_id].resolve()
    )


def test_final_winner_can_precede_primary_return_and_fresh_starts_are_valid():
    window = WindowKey("s4b-shard-000", 5)
    primary = AttemptSpec(window, 0, 0)
    secondary = AttemptSpec(
        window, 9, 6, replay_of=AttemptSpec(window, 1, 6).attempt_id
    )
    final = AttemptSpec(window, 12, 7)
    completions = {
        secondary.attempt_id: asdict(Completion(secondary, "rejected", True)),
        final.attempt_id: asdict(Completion(final, "accepted", True)),
    }
    lifecycles = {
        primary.attempt_id: {"launched_monotonic": 0.0, "reaped_monotonic": 20.0},
        secondary.attempt_id: {
            "launched_monotonic": 5.0,
            "reaped_monotonic": 10.0,
        },
        final.attempt_id: {"launched_monotonic": 11.0, "reaped_monotonic": 15.0},
    }
    _validate_final_recovery([primary, secondary, final], completions, lifecycles)

    bounded_free = AttemptSpec(window, 4, 1)
    final_free = AttemptSpec(window, 14, 1)
    fresh = {
        bounded_free.attempt_id: asdict(Completion(bounded_free, "accepted", True)),
        final_free.attempt_id: asdict(Completion(final_free, "rejected")),
    }
    fresh_lifecycles = {
        primary.attempt_id: lifecycles[primary.attempt_id],
        bounded_free.attempt_id: {
            "launched_monotonic": 2.0,
            "reaped_monotonic": 4.0,
        },
        final_free.attempt_id: {
            "launched_monotonic": 6.0,
            "reaped_monotonic": 8.0,
        },
    }
    _validate_final_recovery(
        [primary, bounded_free, final_free], fresh, fresh_lifecycles
    )


@pytest.mark.parametrize("successor", [False, True])
def test_transition_report_preserves_recovery_audit_ancestor(tmp_path, successor):
    from experiments.case118_annual_hierarchy import s5_analysis, s5_source_transition
    from experiments.case118_annual_hierarchy import s5_retry_transition as retry
    from experiments.case118_annual_hierarchy import (
        s5_operator_intervention as intervention,
    )

    base = {
        "contract": {"trusted_stopping_point": {"completed_intervals": 123}},
        "published_utc": "2026-09-14T00:00:00+00:00",
    }
    operator = {
        "classification": "applied_s5_interval_2448_operator_intervention",
        "contract": {},
        "published_utc": base["published_utc"],
        "predecessor_transition": base,
        "intervention_window": {"sha256": "a" * 64},
    }
    retried = {
        "classification": retry.RECORD_CLASSIFICATION,
        "contract": {},
        "published_utc": base["published_utc"],
        "predecessor_transition": operator,
    }
    audited = {
        **retried,
        "classification": retry.AUDIT_SPEC.record_classification,
        "predecessor_transition": retried,
    }
    speculative = {
        "classification": CLASSIFICATION,
        "contract_sha256": "b" * 64,
        "contract": {
            "first_affected_interval": {"s4b-shard-004": 3239},
            "policy": policy_identity(),
        },
        "predecessor_transition": audited,
    }
    for name, value in [
        (s5_source_transition.RECORD_NAME, base),
        (intervention.RECORD_NAME, operator),
        (retry.RECORD_NAME, retried),
        (retry.AUDIT_SPEC.record_name, audited),
        (RECORD_NAME, speculative),
    ]:
        atomic_json(tmp_path / name, value)
    if successor:
        speculative = {
            **speculative,
            "contract_sha256": "c" * 64,
            "predecessor_transition": speculative,
        }
        atomic_json(
            tmp_path / "speculative-policy-source-transition-001.json", speculative
        )
    summary = s5_analysis._transition_summary(tmp_path, speculative)
    if successor:
        name = "speculative-policy-source-transition-001.json"
        assert summary["latest_path"] == name
        assert summary["latest_sha256"] == artifact_ref(tmp_path / name)["sha256"]
        assert summary["contract_sha256"] == "c" * 64
        summary = summary["predecessor"]
        assert summary["contract_sha256"] == "b" * 64
    for name, classification in [
        (RECORD_NAME, CLASSIFICATION),
        (retry.AUDIT_SPEC.record_name, retry.AUDIT_SPEC.record_classification),
        (retry.RECORD_NAME, retry.RECORD_CLASSIFICATION),
        (intervention.RECORD_NAME, operator["classification"]),
    ]:
        assert summary["latest_path"] == name
        assert summary["latest_sha256"] == artifact_ref(tmp_path / name)["sha256"]
        assert summary["classification"] == classification
        summary = summary.get("predecessor")


@pytest.mark.parametrize("phase_prefix", ["ac", "dc"])
def test_real_process_return_is_audited_and_reaped(tmp_path, monkeypatch, phase_prefix):
    spec = AttemptSpec(WindowKey("s4b-shard-000", 0), 0, 0)
    calls = []

    def command(attempt, directory):
        # Real process/real phase files, deliberately no CVXPY or solver imports.
        return [
            sys.executable,
            "-c",
            (
                "import json,time,pathlib; p=pathlib.Path("
                + repr(str(directory))
                + "); "
                "s=json.loads(" + repr(json.dumps(asdict(attempt))) + "); "
                "events=[{'phase':name,'monotonic_seconds':time.monotonic()} for name in "
                f"['before_{phase_prefix}_build','after_{phase_prefix}_build',"
                f"'before_{phase_prefix}_solve','after_{phase_prefix}_solve']]; "
                "(p/'phase.json').write_text(json.dumps({'invocation':s,'events':events})); "
                "(p/'result.json').write_text('{}')"
            ),
        ]

    def audit(attempt, directory):
        calls.append(attempt)
        return Completion(attempt, "accepted", True)

    backend = SubprocessBackend(
        tmp_path / "phase",
        cwd=tmp_path,
        command=command,
        audit=audit,
        publish=lambda *args: None,
        advance=lambda *args: None,
        phase_prefix=phase_prefix,
    )
    backend.launch(spec)
    child = backend.children[spec.attempt_id]
    child.process.wait(timeout=10)
    assert backend.poll_audited(spec).outcome == "accepted"
    assert calls == [spec]
    assert backend.solve_started_at(spec) >= child.launched
    assert backend.solve_returned_at(spec) >= backend.solve_started_at(spec)
    backend.reap(spec)
    life = json.loads((child.directory / "lifecycle.json").read_text())
    assert life["returncode"] == 0 and life["reaped"]
    from datetime import datetime, timezone, timedelta

    anchor = life["clock_anchor"]
    assert (
        anchor
        == json.loads((child.directory / "launch.json").read_text())["clock_anchor"]
    )
    wall_launch = datetime.fromisoformat(anchor["utc"]) + timedelta(
        seconds=life["launched_monotonic"] - anchor["monotonic_seconds"]
    )
    assert 0 <= (datetime.now(timezone.utc) - wall_launch).total_seconds() < 10
    assert life["post_solve_seconds"] == life[
        "reaped_monotonic"
    ] - backend.solve_returned_at(spec)
    assert life["artifacts"]["result.json"] == artifact_ref(
        child.directory / "result.json"
    )
    from experiments.case118_annual_hierarchy import (
        s5_speculative_process as process_module,
    )
    from experiments.case118_annual_hierarchy.run_s4b import ProcessObservation

    monkeypatch.setattr(
        process_module,
        "process_observations",
        lambda: (ProcessObservation(os.getpid(), 1, "test", 100.0, 1.0),),
    )
    assert backend.memory().aggregate_mib == 100.0
    backend.event("test_complete", spec, backend.clock())
    assert (
        json.loads((backend.root / "supervisor-progress.json").read_text())[
            "memory_samples"
        ]
        == backend.samples
    )


def test_real_sleeping_process_is_canceled_and_lease_can_be_released(tmp_path):
    spec = AttemptSpec(WindowKey("s4b-shard-000", 0), 1, 6)
    backend = SubprocessBackend(
        tmp_path,
        cwd=tmp_path,
        command=lambda *args: [sys.executable, "-c", "import time; time.sleep(30)"],
        audit=lambda *args: pytest.fail("running process must not be audited"),
        publish=lambda *args: None,
        advance=lambda *args: None,
    )
    backend.launch(spec)
    backend.cancel_and_reap(spec, "lost_race")
    child = backend.children[spec.attempt_id]
    assert child.process.poll() is not None and child.reaped
    assert (
        json.loads((child.directory / "lifecycle.json").read_text())["reason"]
        == "lost_race"
    )
    backend.cancel_and_reap(spec, "lost_race")  # idempotent cleanup


def test_nonzero_exit_never_trusts_an_accepted_payload(tmp_path):
    spec = AttemptSpec(WindowKey("s4b-shard-000", 0), 0, 0)
    backend = SubprocessBackend(
        tmp_path,
        cwd=tmp_path,
        command=lambda *args: [sys.executable, "-c", "raise SystemExit(7)"],
        audit=lambda *args: pytest.fail("failed worker cannot be accepted"),
        publish=lambda *args: None,
        advance=lambda *args: None,
    )
    backend.launch(spec)
    backend.children[spec.attempt_id].process.wait(timeout=10)
    assert backend.poll_audited(spec).outcome == "construction_error"
    backend.reap(spec)


def test_real_candidate_failure_is_reconstructed_without_build(
    fixture_outer, monkeypatch, tmp_path
):
    fixture, outer = fixture_outer
    intercept_native(monkeypatch)
    prepared = prepare(fixture, outer)
    execute_prepared_attempt(
        prepared,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        start_path=tmp_path / "start.json",
        result_path=tmp_path / "result.json",
    )
    monkeypatch.setattr(
        streaming,
        "build_window",
        lambda *args, **kwargs: pytest.fail("audit must be build-free"),
    )
    result = archives.audit_candidate(
        tmp_path,
        prepared.invocation,
        inputs=fixture.inputs,
        policy=fixture.policy,
        outer=outer,
        initial=prepared.initial,
        stop=prepared.stop,
    )
    assert result.completion.outcome == "rejected"
    assert result.completion.normalized_residual is None


def synthetic_accepted(fixture, outer, spec, path, monkeypatch, preceding=None):
    install_synthetic_acceptance(monkeypatch)

    def synthetic_audit(*args, **kwargs):
        residuals = {name: 0.0 for name in AC_COMMON_RESIDUAL_NAMES}
        if kwargs.get("include_terminal", True):
            residuals["terminal_soc_mwh_abs"] = 0.0
        return ProbeAudit("optimal", (), None, residuals, True)

    monkeypatch.setattr(streaming, "audit_probe", synthetic_audit)
    monkeypatch.setattr(archives, "audit_probe", synthetic_audit)
    prepared = prepare(fixture, outer, spec, preceding=preceding)
    execute_prepared_attempt(
        prepared,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        outer,
        start_path=path / "start.json",
        result_path=path / "result.json",
    )
    return prepared, archives.audit_candidate(
        path,
        spec,
        inputs=fixture.inputs,
        policy=fixture.policy,
        outer=outer,
        initial=prepared.initial,
        stop=prepared.stop,
    )


def test_build_free_controller_handoff_and_target_free_restriction(
    fixture_outer, monkeypatch, tmp_path
):
    fixture, outer = fixture_outer
    spec = AttemptSpec(WindowKey("synthetic-shard", 0), 0, 0)
    prepared, candidate = synthetic_accepted(
        fixture, outer, spec, tmp_path, monkeypatch
    )
    selected = restore_source(source_payload(candidate.selected_source()))
    following = prepare(
        fixture,
        outer,
        AttemptSpec(WindowKey("synthetic-shard", 1), 0, 0),
        preceding=selected,
    )
    assert following.source_attempt_id == spec.attempt_id
    with pytest.raises(ValueError, match="target-free"):
        candidate.target_free_source()
    assert following.initial == prepared.initial


def test_selected_archive_cleanup_and_full_shard_audit(
    fixture_outer, monkeypatch, tmp_path
):
    """Tests integration, not feasibility of the synthetic flat trajectory."""
    from experiments.case118_annual_hierarchy import s4b_execution as shards
    from experiments.case118_annual_hierarchy import s2_analysis
    from experiments.case118_annual_hierarchy import s5_source_transition

    fixture, outer = fixture_outer
    ids = [str(unit.device_id) for unit in fixture.inputs.storage]
    monkeypatch.setattr(shards, "STORAGE_DEVICE_IDS", tuple(ids))
    monkeypatch.setattr(shards, "load_s4_fixture", lambda: fixture)
    directory = tmp_path / "shard-000"
    directory.mkdir()
    shard = {
        "shard_id": "s4b-shard-000",
        "ordinal": 0,
        "interval": {"start": 0, "stop": 1},
        "storage": {
            "initial_state": {"soc_mwh": [500.0]},
            "terminal_state": {"soc_mwh": [500.0]},
        },
    }
    # One interval means the hard target is boundary 1, not boundary 3.
    # original helper fixes trajectory_stop=6, so prepare directly for this boundary.
    from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
        prepare_attempt,
    )

    monkeypatch.setattr(
        sys.modules[__name__],
        "prepare",
        lambda f, o, spec, **kwargs: prepare_attempt(
            f.inputs,
            f.policy,
            f.solve_config,
            o,
            spec,
            {ids[0]: 500.0},
            kwargs.get("preceding"),
            trajectory_start=0,
            trajectory_stop=1,
            trajectory_initial_soc_mwh={ids[0]: 500.0},
        ),
    )
    cp = shards.shard_checkpoint_payload(
        shard=shard,
        execution_source_fingerprint="new-source",
        outer_plan_sha256="a" * 64,
        execution_mode="ordinary",
        realized_soc_mwh=[500.0],
        preceding_controlling_attempt_id=None,
        windows=(),
        allowed_execution_modes=("ordinary",),
    )
    atomic_json(directory / "checkpoint.json", cp)
    spec = AttemptSpec(WindowKey(shard["shard_id"], 0), 0, 0)
    contender = tmp_path / "phase" / "primary"
    contender.mkdir(parents=True)
    prepared, candidate = synthetic_accepted(
        fixture, outer, spec, contender, monkeypatch
    )
    # S4b's metric extractor is Case118-specific and expects an ND reporting
    # channel; retain a zero synthetic channel for this smaller lifecycle fixture.
    payload = json.loads((contender / "result.json").read_text())
    payload["attempt"]["result"]["curtailment"] = [[0.0]]
    atomic_json(contender / "result.json", payload)
    candidate = archives.audit_candidate(
        contender,
        spec,
        inputs=fixture.inputs,
        policy=fixture.policy,
        outer=outer,
        initial=prepared.initial,
        stop=prepared.stop,
    )
    monkeypatch.setattr(s2_analysis, "audit_probe", archives.audit_probe)
    record = {
        "classification": CLASSIFICATION,
        "contract_sha256": "contract",
        "stopping_pointer_json": {},
        "contract": {
            "continuation_execution": {"context": {"source_fingerprint": "new-source"}}
        },
    }
    monkeypatch.setattr(s5_source_transition, "load_transition", lambda path: record)
    entry = publish_window(
        directory,
        spec,
        [candidate.completion],
        {spec.attempt_id: contender},
        inputs=fixture.inputs,
        policy=fixture.policy,
        outer=outer,
        context={"source_fingerprint": "new-source"},
        contract_sha256="contract",
    )
    with pytest.raises(FileNotFoundError):
        finalize_window(directory, entry)
    assert (
        json.loads((directory / "checkpoint.json").read_text())["completed_intervals"]
        == 0
    )
    atomic_immutable_json(
        contender / "lifecycle.json",
        {
            "invocation": asdict(spec),
            "reaped": True,
            "reason": "returned",
            "returncode": 0,
            "launched_monotonic": 10.0,
            "reaped_monotonic": 15.0,
            "worker_wall_seconds": 5.0,
            "phases": [
                {"phase": name, "monotonic_seconds": stamp}
                for name, stamp in [
                    ("before_ac_build", 10.0),
                    ("after_ac_build", 11.0),
                    ("before_ac_solve", 11.0),
                    ("after_ac_solve", 14.0),
                ]
            ],
            "artifacts": {
                name: artifact_ref(contender / name)
                for name in ("start.json", "result.json")
            },
        },
    )
    finalize_window(directory, entry)
    finalize_window(directory, entry)
    summary = shards.audit_shard(
        directory, shard=shard, outer=outer, allowed_execution_modes=("ordinary",)
    )
    assert summary["classification"] == "accepted"
    assert summary["completed_intervals"] == 1
    assert summary["speculative_policy"]["solve_effort_seconds"] == 3.0
    assert summary["speculative_policy"]["post_solve_seconds"] == 1.0
    assert summary["timing"]["total_window_path_seconds"] == 5.0


def test_policy_continuation_binds_actual_stopping_state(tmp_path):
    old_context = {"git_commit": "old", "source_fingerprint": "old-source"}
    predecessor = {
        "new_authority": {"old": True},
        "contract": {"continuation_execution": {"context": old_context}},
    }
    context = {
        "git_clean": True,
        "git_commit": "new",
        "source_fingerprint": "new-source",
    }
    snapshots = {
        "progress.json": json.dumps({"classification": "partial"}),
        "shard-002/checkpoint.json": json.dumps(
            {
                "shard_id": "s4b-shard-002",
                "complete": False,
                "next_global_iteration": 3000,
            }
        ),
    }
    contract = {
        "policy": policy_identity(),
        "predecessor_transition_sha256": object_sha256(predecessor),
        "execution_authorized": True,
        "prior_execution_context": old_context,
        "continuation_execution": {"context": context},
        "checkpoint_sha256": {
            key: hashlib.sha256(value.encode()).hexdigest()
            for key, value in snapshots.items()
        },
        "first_affected_interval": {"s4b-shard-002": 3000},
    }
    authority = {
        "execution_commit": "new",
        "source_fingerprint": "new-source",
        "recovery_policy": POLICY_NAME,
        "maximum_solver_processes": 3,
        "source_version_contract_sha256": object_sha256(contract),
    }
    record = {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "contract": contract,
        "contract_sha256": object_sha256(contract),
        "new_authority": authority,
        "prior_authority": predecessor["new_authority"],
        "predecessor_transition": predecessor,
        "stopping_pointer_json": snapshots,
    }
    atomic_json(tmp_path / RECORD_NAME, record)
    assert load_record(tmp_path, predecessor) == record
    record["stopping_pointer_json"]["shard-002/checkpoint.json"] += " "
    atomic_json(tmp_path / RECORD_NAME, record)
    with pytest.raises(ValueError, match="checkpoint hash"):
        load_record(tmp_path, predecessor)


def test_prepare_continuation_is_read_only_and_nonexecuting_by_default(tmp_path):
    from experiments.case118_annual_hierarchy.s5_speculative_continuation import (
        prepare_record,
    )

    prior_context = {"git_commit": "old", "source_fingerprint": "old"}
    predecessor = {
        "contract": {"continuation_execution": {"context": prior_context}},
        "new_authority": {"old": True},
    }
    atomic_json(
        tmp_path / "progress.json",
        {
            "execution_context": prior_context,
            "authority": {"old": True},
            "classification": "running",
        },
    )
    atomic_json(
        tmp_path / "shard-002/checkpoint.json",
        {"shard_id": "s4b-shard-002", "complete": False, "next_global_iteration": 3000},
    )
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*.json")}
    target = {"git_commit": "new", "source_fingerprint": "new", "git_clean": True}
    record = prepare_record(tmp_path, predecessor, target)
    assert record["contract"]["execution_authorized"] is False
    assert record["contract"]["first_affected_interval"] == {"s4b-shard-002": 3000}
    assert before == {str(path): path.read_bytes() for path in tmp_path.rglob("*.json")}
    with pytest.raises(ValueError, match="stopped run"):
        prepare_record(tmp_path, predecessor, target, execution_authorized=True)


def test_dirty_wave_refuses_before_output(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "execution_context", lambda: {"git_clean": False})
    with pytest.raises(ValueError, match="clean committed"):
        runtime.run_wave(
            ["s4b-shard-000"],
            authority_path=tmp_path / "authority",
            output_root=tmp_path / "run",
            reviewed_resume=True,
        )
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("peer_fails", [False, True])
def test_wave_keeps_supervising_peer_until_quiescent_before_slow_shard_audit(
    tmp_path, monkeypatch, peer_fails
):
    """Real root/coordinator lifecycle; fake processes and physical audit only."""
    from types import SimpleNamespace
    from experiments.case118_annual_hierarchy import run_s5
    from experiments.case118_annual_hierarchy.s5_speculative_process import Child
    from experiments.case118_annual_hierarchy.s5_speculative_supervisor import (
        MemorySample,
    )

    context = {"git_clean": True, "git_commit": "test", "source_fingerprint": "test"}
    monkeypatch.setattr(runtime, "execution_context", lambda: context)
    monkeypatch.setattr(
        runtime,
        "load_numerical_authority",
        lambda *a, **k: {"recovery_policy": POLICY_NAME},
    )
    monkeypatch.setattr(
        runtime, "require_current_transition", lambda *a: {"contract_sha256": "test"}
    )
    monkeypatch.setattr(run_s5, "_validate_completed_prefix", lambda *a: None)
    monkeypatch.setattr(runtime, "annual_registry", lambda: {"registry_sha256": "test"})
    monkeypatch.setattr(runtime, "sha256_path", lambda path: "test")
    monkeypatch.setattr(
        runtime,
        "shard_checkpoint_payload",
        lambda **kw: {
            "complete": False,
            "next_global_iteration": 0,
            "windows": [],
            "execution_source_fingerprint": "test",
            "interval": {"start": 0, "stop": 1},
        },
    )
    # Replace sleeping with a deterministic clock advance. A slow final audit
    # advances far beyond the helper budget, but must see no live contender.
    now = [0.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        runtime.time, "sleep", lambda seconds: now.__setitem__(0, now[0] + seconds)
    )
    backends, audits = [], []

    class Adapter:
        def __init__(self, *args):
            self.outer = object()

        def shard_directory(self, key):
            return tmp_path / f"shard-{int(key.shard_id[-3:]):03d}"

        def command(self, *args):
            raise AssertionError("no numerical workers")

        def audit(self, *args):
            raise AssertionError("fake completions only")

        def publish(self, *args):
            pass

        def advance(self, winner, directories):
            path = self.shard_directory(winner.window) / "checkpoint.json"
            cp = json.loads(path.read_text())
            atomic_json(path, {**cp, "complete": True})

    class Backend(SubprocessBackend):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, clock=lambda: now[0])
            self.polls = {}
            backends.append(self)

        def launch(self, attempt):
            directory = self.root / attempt.window.shard_id
            directory.mkdir(parents=True)
            child = Child(attempt, directory, now[0])
            child.process = SimpleNamespace(wait=lambda: 0, returncode=0)
            self.children[attempt.attempt_id] = child
            self.polls[attempt.attempt_id] = 0

        def solve_started_at(self, attempt):
            return None

        def solve_returned_at(self, attempt):
            return None

        def poll_audited(self, attempt):
            self.polls[attempt.attempt_id] += 1
            if attempt.window.shard_id.endswith("001"):
                if self.polls[attempt.attempt_id] < 3:
                    return None
                if peer_fails:
                    raise OSError("simulated peer failure")
            result = Completion(attempt, "accepted", True)
            self.children[attempt.attempt_id].completion = result
            return result

        def reap(self, attempt):
            self._finish(self.children[attempt.attempt_id], "returned")

        def cancel_and_reap(self, attempt, reason):
            self._finish(self.children[attempt.attempt_id], reason)

        def memory(self):
            self.samples.append(
                {
                    "monotonic_seconds": now[0],
                    "aggregate_mib": 2.0,
                    "worker_trees_mib": [1.0],
                    "supervisor_current_rss_mib": 1.0,
                }
            )
            return MemorySample(2.0, (1.0,))

    def slow_audit(directory, **kwargs):
        backend = backends[0]
        assert all(child.reaped for child in backend.children.values())
        assert (
            backend.polls[next(key for key in backend.polls if "shard-001" in key)] == 3
        )
        now[0] += 1000.0
        audits.append(kwargs["shard"]["shard_id"])
        return {"classification": "accepted", "shard_id": audits[-1]}

    monkeypatch.setattr(runtime, "WaveAdapter", Adapter)
    monkeypatch.setattr(runtime, "SubprocessBackend", Backend)
    monkeypatch.setattr(runtime, "audit_shard", slow_audit)
    record = runtime.run_wave(
        runtime.ANNUAL_WAVES[0],
        authority_path=tmp_path / "authority",
        output_root=tmp_path,
        reviewed_resume=True,
    )
    assert audits == list(runtime.ANNUAL_WAVES[0][: 1 if peer_fails else 2])
    assert record["classification"] == (
        "supervisor_failure" if peer_fails else "accepted"
    )
    assert record["elapsed_critical_path_seconds"] >= 1000.0 * len(audits)
    assert set(record["worker_results"]) == set(audits)


@pytest.mark.parametrize("early_failure", [False, True])
def test_helper_lease_and_eligibility_are_reconstructed(early_failure):
    a, b = [WindowKey(key, 0) for key in runtime.ANNUAL_WAVES[0]]
    primary, peer = AttemptSpec(a, 0, 0), AttemptSpec(b, 0, 0)
    helper, second = AttemptSpec(a, 1, 6), AttemptSpec(b, 1, 6)

    def event(kind, spec, stamp):
        return {"kind": kind, "invocation": asdict(spec), "monotonic_seconds": stamp}

    events = [event("launched", primary, 0), event("launched", peer, 0)]
    eligible = 2.0 if early_failure else 301.0
    if early_failure:
        events.append(event("audited_return", primary, 2))
    events += [
        event("helper_eligible", primary, eligible),
        event("launched", helper, eligible),
    ]
    life = {
        "phases": [{"phase": "before_ac_solve", "monotonic_seconds": 1.0}],
        "completion": asdict(Completion(primary, "rejected", True)),
    }
    lives = {primary.attempt_id: life}
    runtime._validate_helper_lifecycle(events, lives)
    # The second helper still leaves only three processes when the first primary
    # failed. Total process count is therefore not a sufficient lease check.
    with pytest.raises(ValueError, match="multiple shared helpers"):
        runtime._validate_helper_lifecycle(
            events
            + [
                event("helper_eligible", peer, 301),
                event("launched", second, 301),
            ]
        )
    with pytest.raises(ValueError, match="prior eligibility"):
        runtime._validate_helper_lifecycle(
            [item for item in events if item["kind"] != "helper_eligible"]
        )
    if early_failure:
        life["completion"] = asdict(Completion(primary, "accepted", True))
        with pytest.raises(ValueError, match="failed primary"):
            runtime._validate_helper_lifecycle(events, lives)
    else:
        life["phases"][0]["monotonic_seconds"] = 2.0
        with pytest.raises(ValueError, match="precedes primary"):
            runtime._validate_helper_lifecycle(events, lives)


def test_wave_lifecycle_distinguishes_replacement_from_shared_helper():
    a, b = [WindowKey(key, 0) for key in runtime.ANNUAL_WAVES[0]]
    primary, peer = AttemptSpec(a, 0, 0), AttemptSpec(b, 0, 0)
    replacement = AttemptSpec(a, 11, 6)
    shared = AttemptSpec(b, 1, 6)

    def event(kind, spec, stamp):
        return {"kind": kind, "invocation": asdict(spec), "monotonic_seconds": stamp}

    events = [
        event("launched", primary, 0.0),
        event("launched", peer, 0.0),
        event("audited_return", primary, 2.0),
        event("replacement_launched", replacement, 3.0),
        event("helper_eligible", peer, 301.0),
        event("launched", shared, 301.0),
    ]
    peak = runtime._wave_record_concurrency(events)
    _, active = runtime._validate_helper_lifecycle(events)
    assert peak == 3
    assert active == {replacement.attempt_id, shared.attempt_id, peer.attempt_id}
    with pytest.raises(ValueError, match="replacement launch"):
        runtime._validate_helper_lifecycle(
            events + [event("replacement_launched", AttemptSpec(a, 12, 7), 302.0)]
        )


def test_wave_record_peak_counts_replacement_and_shared_helper():
    """The writer and validator reconstruct the same three-process peak."""
    a, b = [WindowKey(key, 0) for key in runtime.ANNUAL_WAVES[0]]
    primary, peer = AttemptSpec(a, 0, 0), AttemptSpec(b, 0, 0)
    replacement = AttemptSpec(a, 11, 6)
    shared = AttemptSpec(b, 1, 6)

    def event(kind, spec, stamp):
        return {"kind": kind, "invocation": asdict(spec), "monotonic_seconds": stamp}

    events = [
        event("launched", primary, 0.0),
        event("launched", peer, 0.0),
        event("audited_return", primary, 2.0),
        event("replacement_launched", replacement, 3.0),
        event("helper_eligible", peer, 301.0),
        event("launched", shared, 301.0),
        event("audited_return", replacement, 302.0),
        event("audited_return", shared, 303.0),
        event("audited_return", peer, 304.0),
    ]
    peak = runtime._wave_record_concurrency(events)
    _, active = runtime._validate_helper_lifecycle(events)
    record = {
        "schema_version": 2,
        "policy": POLICY_NAME,
        "manifest_sha256": runtime.EXPECTED_MANIFEST_SHA256,
        "wave_index": 0,
        "frozen_wave": list(runtime.ANNUAL_WAVES[0]),
        "requested_shards": list(runtime.ANNUAL_WAVES[0]),
        "requested_concurrency": 2,
        "maximum_observed_concurrency": peak,
        "classification": "supervisor_failure",
        "worker_results": {},
        "returncodes": {},
        "events": events,
        "clock_anchor": {
            "monotonic_seconds": 0.0,
            "utc": "2026-01-01T00:00:00+00:00",
        },
        "resource_triggers": [],
        "resource_samples": [],
        "peak_worker_rss_mib": {"solver_tree": 0.0},
        "peak_aggregate_rss_mib": 0.0,
    }
    assert peak == 3
    assert not active
    assert runtime.validate_wave(record)["maximum_observed_concurrency"] == 3


@pytest.mark.parametrize("complete", [False, True])
def test_annual_analyzer_consumes_new_policy_complete_and_partial_waves(
    tmp_path, monkeypatch, complete
):
    from experiments.case118_annual_hierarchy import s5_analysis
    from tests import test_case118_s5_execution as legacy

    context, authority = legacy._context(), legacy._authority()
    monkeypatch.setattr(
        s5_analysis, "load_numerical_authority", lambda *a, **k: authority
    )
    monkeypatch.setattr(s5_analysis, "_outer", lambda: object())
    monkeypatch.setattr(
        s5_analysis,
        "audit_shard",
        lambda directory, **kw: json.loads(
            (directory / "shard-result.json").read_text()
        ),
    )
    monkeypatch.setattr(s5_analysis, "verify_shard_artifacts", lambda *a, **k: ({}, ()))
    monkeypatch.setattr(s5_analysis, "analysis_context", lambda: {"git_clean": True})
    merged = {
        "classification": "accepted_annual_partition",
        "execution_complete": True,
        "all_independent_audits_agree": True,
    }
    monkeypatch.setattr(s5_analysis, "merge_shard_summaries", lambda *a, **k: merged)
    registry, completed = [], []
    for index in range(6 if complete else 1):
        record = retained_wave(tmp_path, index)
        record.update(execution_context=context, authority=authority)
        # Exercise normalization of v2 records, including those without the
        # legacy resource_triggers field that originally crashed this analyzer.
        record.pop("resource_triggers")
        ids = list(record["worker_results"])
        if not complete:
            record["classification"] = "supervisor_failure"
            record["worker_results"].pop(ids[1])
            record["returncodes"].pop(ids[1])
            ids = ids[:1]
        for key in ids:
            worker = {
                "shard_id": key,
                "classification": "accepted",
                "execution_complete": True,
                "all_independent_audits_agree": True,
                "execution_context": context,
                "execution_source_fingerprint": context["source_fingerprint"],
                "execution_mode": "annual",
                "checkpoint_sha256": "1" * 64,
                "window_chain_sha256": "2" * 64,
            }
            atomic_json(
                tmp_path / f"shard-{int(key[-3:]):03d}" / "shard-result.json", worker
            )
            record["worker_results"][key] = worker
            completed.append(key)
        path = tmp_path / f"supervision-wave-{index:03d}-000.json"
        atomic_json(path, record)
        registry.append(
            {
                "path": path.name,
                "sha256": artifact_ref(path)["sha256"],
                "wave_index": index,
                "classification": record["classification"],
            }
        )
    progress = {
        "schema_version": 1,
        "classification": "accepted" if complete else "partial",
        "manifest_sha256": runtime.EXPECTED_MANIFEST_SHA256,
        "execution_context": context,
        "authority": authority,
        "next_wave": 6 if complete else 0,
        "completed_shards": completed,
        "supervision_records": registry,
        "reviewed_continuations": [],
        "root_outcomes": [],
    }
    if complete:
        path = tmp_path / "merged-result.json"
        atomic_json(path, merged)
        progress["merged_result"] = {
            "path": path.name,
            "sha256": artifact_ref(path)["sha256"],
        }
        atomic_json(tmp_path / "run-result.json", progress)
    atomic_json(tmp_path / "progress.json", progress)
    atomic_json(tmp_path / "run-context.json", context)
    result = s5_analysis.analyze_s5(
        tmp_path, authority_path=tmp_path / "authority.json"
    )
    assert result["execution_complete"] is complete
    assert result["accepted_for_s6"] is complete
    assert result["classification"] == ("accepted" if complete else "partial")
    assert all(item["resource_triggers"] == [] for item in result["wave_lifecycle"])


def retained_wave(tmp_path, wave_index=0):
    """Synthetic lifecycle receipt for annual handoff checks, not solver evidence."""
    wave = list(runtime.ANNUAL_WAVES[wave_index])
    anchor = {"monotonic_seconds": 0.0, "utc": "2026-09-14T00:00:00+00:00"}
    events = []
    refs = {}
    results = {key: {"classification": "accepted", "shard_id": key} for key in wave}
    for key in wave:
        spec = AttemptSpec(WindowKey(key, 0), 0, 0)
        events.append(
            {"kind": "launched", "invocation": asdict(spec), "monotonic_seconds": 1.0}
        )
        path = tmp_path / key / "lifecycle.json"
        atomic_immutable_json(
            path,
            {
                "invocation": asdict(spec),
                "reaped": True,
                "phases": [
                    {"phase": "before_ac_solve", "monotonic_seconds": 1.0},
                    {"phase": "after_ac_solve", "monotonic_seconds": 2.0},
                ],
                "completion": asdict(Completion(spec, "accepted", True)),
                "clock_anchor": anchor,
            },
        )
        refs[spec.attempt_id] = {
            **artifact_ref(path),
            "path": str(path.relative_to(tmp_path)),
        }
    for key in wave:
        events.append(
            {
                "kind": "audited_return",
                "invocation": asdict(AttemptSpec(WindowKey(key, 0), 0, 0)),
                "monotonic_seconds": 2.0,
            }
        )
    samples = [
        {
            "aggregate_mib": 10.0,
            "worker_trees_mib": [4.0, 5.0],
            "supervisor_current_rss_mib": 1.0,
        }
    ]
    progress = tmp_path / f"phase-{wave_index}" / "supervisor-progress.json"
    atomic_json(
        progress, {"clock_anchor": anchor, "events": events, "memory_samples": samples}
    )
    record = {
        "schema_version": 2,
        "policy": POLICY_NAME,
        "manifest_sha256": runtime.EXPECTED_MANIFEST_SHA256,
        "requested_shards": wave,
        "frozen_wave": wave,
        "wave_index": wave_index,
        "requested_concurrency": 2,
        "maximum_observed_concurrency": 2,
        "classification": "accepted",
        "worker_results": results,
        "returncodes": {key: 0 for key in wave},
        "events": events,
        "clock_anchor": anchor,
        "resource_triggers": [],
        "worker_logs": {},
        "elapsed_critical_path_seconds": 2.0,
        "resource_samples": samples,
        "peak_worker_rss_mib": {"solver_tree": 5.0},
        "peak_aggregate_rss_mib": 10.0,
        "contender_lifecycles": refs,
        "phase_progress": {
            **artifact_ref(progress),
            "path": str(progress.relative_to(tmp_path)),
        },
        "execution_context": {"source_fingerprint": "new"},
        "authority": {"recovery_policy": POLICY_NAME},
    }
    return record


def test_new_wave_receipt_opens_next_wave_only_after_complete_peer_audits(
    tmp_path, monkeypatch
):
    from experiments.case118_annual_hierarchy import run_s5

    record = retained_wave(tmp_path)
    assert runtime.validate_wave(record, tmp_path) == record
    atomic_json(tmp_path / "supervision-wave-000-000.json", record)
    monkeypatch.setattr(run_s5, "load_transition", lambda root: None)
    monkeypatch.setattr(
        run_s5, "_completed_shards", lambda root: list(runtime.ANNUAL_WAVES[0])
    )
    audited = []

    def audit(directory, shard_id, context):
        audited.append(shard_id)
        return record["worker_results"][shard_id], {"classification": "accepted"}

    monkeypatch.setattr(run_s5, "_audited_completed_worker", audit)
    run_s5._validate_completed_prefix(
        tmp_path, 1, record["execution_context"], record["authority"]
    )
    assert audited == list(runtime.ANNUAL_WAVES[0])
    with pytest.raises(ValueError, match="wave schedule"):
        run_s5._validate_completed_prefix(
            tmp_path, 2, record["execution_context"], record["authority"]
        )


def test_reviewed_anchor_skips_only_immutable_completed_shards(tmp_path, monkeypatch):
    from experiments.case118_annual_hierarchy import run_s5, s5_prefix_anchor

    anchored = list(s5_prefix_anchor.CERTIFIED_SHARDS)
    monkeypatch.setattr(run_s5, "_completed_shards", lambda root: anchored)
    monkeypatch.setattr(
        s5_prefix_anchor, "verified_completed_prefix", lambda root: frozenset(anchored)
    )
    monkeypatch.setattr(
        run_s5,
        "_audited_completed_worker",
        lambda *args: pytest.fail("anchored shard was re-audited"),
    )
    run_s5._validate_completed_prefix(
        tmp_path, 5, {}, {}, use_completed_prefix_anchor=True
    )


def test_invalid_anchor_falls_back_to_full_completed_audit(tmp_path, monkeypatch):
    from experiments.case118_annual_hierarchy import run_s5, s5_prefix_anchor

    completed = list(s5_prefix_anchor.CERTIFIED_SHARDS)
    monkeypatch.setattr(run_s5, "_completed_shards", lambda root: completed)

    def reject_anchor(root):
        raise ValueError("anchor bytes changed")

    monkeypatch.setattr(s5_prefix_anchor, "verified_completed_prefix", reject_anchor)
    audited = []

    def audit(directory, shard_id, context):
        audited.append(shard_id)
        return {}, {}

    monkeypatch.setattr(run_s5, "_audited_completed_worker", audit)
    monkeypatch.setattr(run_s5, "_require_completed_worker_binding", lambda *a: None)
    run_s5._validate_completed_prefix(
        tmp_path, 5, {}, {}, use_completed_prefix_anchor=True
    )
    assert audited == completed


def test_failed_v2_launch_reconciliation_retry_preserves_checkpoints(tmp_path):
    """A retained pre-solve failure is reconciled once, with cleanup checked."""
    from experiments.case118_annual_hierarchy import run_s5

    record = retained_wave(tmp_path)
    record.update(
        classification="supervisor_failure", worker_results={}, returncodes={}
    )
    record["events"] = [e for e in record["events"] if e["kind"] == "launched"]
    record["events"].append(
        {"kind": "supervisor_failure", "invocation": None, "monotonic_seconds": 2.0}
    )
    record["exception"] = "PermissionError: Operation not permitted: 'ps'"
    for ref in record["contender_lifecycles"].values():
        path = tmp_path / ref["path"]
        lifecycle = json.loads(path.read_text())
        lifecycle.update(phases=[], completion=None)
        atomic_json(path, lifecycle)
        ref.update(artifact_ref(path))
    phase = tmp_path / record["phase_progress"]["path"]
    atomic_json(
        phase,
        {
            "clock_anchor": record["clock_anchor"],
            "events": record["events"],
            "memory_samples": record["resource_samples"],
        },
    )
    record["phase_progress"].update(artifact_ref(phase))
    path = tmp_path / "supervision-wave-000-000.json"
    atomic_immutable_json(path, record)
    checkpoint = tmp_path / "shard-000/checkpoint.json"
    atomic_json(checkpoint, {"completed_intervals": 7, "windows": ["retained"]})
    before = checkpoint.read_bytes()
    records = []
    run_s5._reconcile_supervision_records(tmp_path, records)
    assert len(records) == 1 and records[0]["classification"] == "supervisor_failure"
    run_s5._reconcile_supervision_records(tmp_path, records)
    assert len(records) == 1 and checkpoint.read_bytes() == before
    # Matching hashes alone must not excuse an unreaped contender on retry.
    ref = next(iter(record["contender_lifecycles"].values()))
    child = tmp_path / ref["path"]
    lifecycle = json.loads(child.read_text())
    lifecycle["reaped"] = False
    atomic_json(child, lifecycle)
    ref.update(artifact_ref(child))
    atomic_json(path, record)
    with pytest.raises(ValueError, match="unreaped"):
        run_s5._reconcile_supervision_records(tmp_path, [])
    assert checkpoint.read_bytes() == before


def test_speculative_source_correction_retains_immutable_predecessor(tmp_path):
    from experiments.case118_annual_hierarchy.s5_speculative_continuation import (
        prepare_record,
    )

    old_context = {"git_commit": "old", "source_fingerprint": "old", "git_clean": True}
    predecessor = {
        "contract": {"continuation_execution": {"context": old_context}},
        "new_authority": {"old": True},
    }
    checkpoint = tmp_path / "shard-004/checkpoint.json"
    atomic_json(
        checkpoint,
        {"shard_id": "s4b-shard-004", "complete": False, "next_global_iteration": 3248},
    )
    atomic_json(
        tmp_path / "progress.json",
        {
            "classification": "partial",
            "execution_context": old_context,
            "authority": predecessor["new_authority"],
        },
    )
    context = {**old_context, "git_commit": "first", "source_fingerprint": "first"}
    first = prepare_record(tmp_path, predecessor, context, execution_authorized=True)
    atomic_immutable_json(tmp_path / RECORD_NAME, first)
    original_bytes = (tmp_path / RECORD_NAME).read_bytes()
    checkpoint_bytes = checkpoint.read_bytes()
    atomic_json(
        tmp_path / "progress.json",
        {
            "classification": "partial",
            "execution_context": context,
            "authority": first["new_authority"],
        },
    )
    corrected = {
        **context,
        "git_commit": "corrected",
        "source_fingerprint": "corrected",
    }
    second = prepare_record(tmp_path, first, corrected, execution_authorized=True)
    successor = tmp_path / "speculative-policy-source-transition-001.json"
    atomic_immutable_json(successor, second)
    assert load_record(tmp_path, predecessor) == second
    assert (tmp_path / RECORD_NAME).read_bytes() == original_bytes
    assert checkpoint.read_bytes() == checkpoint_bytes
    assert (
        second["contract"]["first_affected_interval"]
        == first["contract"]["first_affected_interval"]
    )
    successor.rename(tmp_path / "speculative-policy-source-transition-002.json")
    with pytest.raises(ValueError, match="discontinuous"):
        load_record(tmp_path, predecessor)


def test_root_consumes_mixed_wave_schemas_and_finishes_annual_merge(
    tmp_path, monkeypatch
):
    """Root/next-wave/merger/analyzer plumbing, with synthetic physical summaries.

    Actual cross-version provenance is covered by the copied-record rehearsal;
    this fixture exercises successful progression without numerical simulation.
    """
    from experiments.case118_annual_hierarchy import run_s5, s5_analysis
    from tests import test_case118_s5_execution as legacy

    context, authority = legacy._context(), legacy._authority()
    for module in (run_s5, s5_analysis):
        monkeypatch.setattr(
            module, "load_numerical_authority", lambda *a, **k: authority
        )
        monkeypatch.setattr(module, "_outer", lambda: object())
        monkeypatch.setattr(
            module,
            "audit_shard",
            lambda directory, **kw: legacy._summary(kw["shard"]["shard_id"]),
        )
    monkeypatch.setattr(run_s5, "execution_context", lambda: context)
    monkeypatch.setattr(s5_analysis, "verify_shard_artifacts", lambda *a, **k: ({}, ()))
    monkeypatch.setattr(s5_analysis, "analysis_context", lambda: {"git_clean": True})
    seen = []

    def supervise(shard_ids, **kwargs):
        index = runtime.wave_index_for_request(shard_ids)
        assert index == len(seen)
        # The real admission gate must accept every completed prior wave.
        run_s5._validate_completed_prefix(tmp_path, index, context, authority)
        seen.append(index)
        record = legacy._supervision() if index == 0 else retained_wave(tmp_path, index)
        record.update(execution_context=context, authority=authority)
        record["worker_results"] = {key: legacy._worker(key) for key in shard_ids}
        for key, worker in record["worker_results"].items():
            atomic_json(
                tmp_path / f"shard-{int(key[-3:]):03d}" / "shard-result.json", worker
            )
        # Synthetic legacy worker logs still pass the real reader/hash checks.
        if index == 0:
            record["worker_logs"] = {}
            for key in shard_ids:
                path = tmp_path / f"{key}.log"
                atomic_json(path, {"synthetic": True})
                record["worker_logs"][key] = {
                    "path": path.name,
                    "sha256": artifact_ref(path)["sha256"],
                }
        path = tmp_path / f"supervision-wave-{index:03d}-000.json"
        atomic_json(path, record)
        return {
            **record,
            "record_path": path.name,
            "record_sha256": artifact_ref(path)["sha256"],
        }

    # run_annual requires a nonexistent output root on first use.
    output = tmp_path / "run"
    tmp_path = output
    result = run_s5.run_annual(output_root=output, supervisor=supervise)
    assert seen == list(range(6))
    assert result["classification"] == "accepted"
    analysis = s5_analysis.analyze_s5(output)
    assert analysis["execution_complete"] is True
    assert analysis["accepted_for_s6"] is True
    assert analysis["merged_result"]["completed_intervals"] == 8760
    assert len(analysis["wave_lifecycle"]) == 6


@pytest.mark.parametrize(
    "change",
    ["missing_peer", "missing_cleanup", "wrong_peak", "wrong_context_boundary"],
)
def test_wave_and_policy_boundary_mismatches_are_rejected(tmp_path, change):
    record = retained_wave(tmp_path)
    if change == "missing_peer":
        record["worker_results"].pop(runtime.ANNUAL_WAVES[0][1])
    elif change == "missing_cleanup":
        record["contender_lifecycles"].pop(next(iter(record["contender_lifecycles"])))
    elif change == "wrong_peak":
        record["peak_aggregate_rss_mib"] = 9.0
    else:
        from experiments.case118_annual_hierarchy.s5_speculative_continuation import (
            verify_phase_window,
        )

        transition = {
            "stopping_pointer_json": {
                "shard-000/checkpoint.json": json.dumps({"completed_intervals": 5})
            },
            "contract_sha256": "new",
            "contract": {"continuation_execution": {"context": {"source": "new"}}},
        }
        verify_phase_window(
            transition, tmp_path / "shard-000", {"schema_version": 1}, 4
        )
        with pytest.raises(ValueError, match="policy phase"):
            verify_phase_window(
                transition, tmp_path / "shard-000", {"schema_version": 1}, 5
            )
        return
    with pytest.raises(ValueError):
        runtime.validate_wave(record, tmp_path)


def test_box_ranking_includes_generator_storage_and_frozen_availability(fixture_outer):
    from dataclasses import replace
    from cvxopf import NondispatchableUnit

    fixture, _ = fixture_outer
    inputs = replace(
        fixture.inputs,
        nondispatchable=(
            NondispatchableUnit(
                bus=7, p_available=4.0, apparent_power_rating=5.0, device_id="nd"
            ),
        ),
    )
    result = {
        "Pg": [[g.p_min_mw for g in inputs.generators]],
        "Qg": [[g.q_min_mvar for g in inputs.generators]],
        "b": [[0.0]],
        "b_q": [[0.0]],
        "soc": [[500.0]],
        "p_load": [[0.0]],
        "q_load": [[0.0]],
        "p_load_served": [[0.0]],
        "q_load_served": [[0.0]],
        "p_nd": [[6.0]],
        "q_nd": [[0.0]],
        "curtailment": [[0.0]],
    }
    values = archives.box_residuals(inputs, result)
    assert values["nd_p_box_pu_abs"] == 0.02
    assert values["nd_circle_pu_abs"] == 0.01
    result["soc"] = [[inputs.storage[0].capacity + 1.0]]
    result["Pg"][0][0] = inputs.generators[0].p_max_mw + 1.0
    assert archives.box_residuals(inputs, result)["storage_soc_box_mwh_abs"] == 1.0
    assert archives.box_residuals(inputs, result)["generator_p_box_pu_abs"] == 0.01


def test_worker_entry_builds_one_problem_and_retains_complete_x0_without_native_solve(
    fixture_outer, monkeypatch, tmp_path
):
    from experiments.case118_annual_hierarchy import s5_speculative_worker as worker
    from experiments.case118_annual_hierarchy.streaming_schema import sha256_path

    fixture, outer = fixture_outer
    context = {"git_clean": True, "git_commit": "test", "source_fingerprint": "test"}
    monkeypatch.setattr(worker, "execution_context", lambda: context)
    monkeypatch.setattr(
        worker,
        "load_numerical_authority",
        lambda *args, **kwargs: {"recovery_policy": POLICY_NAME},
    )
    monkeypatch.setattr(
        worker, "require_current_transition", lambda *args: {"contract_sha256": "test"}
    )
    monkeypatch.setattr(worker, "load_s4_fixture", lambda: fixture)
    monkeypatch.setattr(worker, "_outer", lambda: outer)
    calls = intercept_native(monkeypatch)
    spec = AttemptSpec(WindowKey("s4b-shard-000", 0), 0, 0)
    cp_path = tmp_path / "checkpoint.json"
    atomic_json(
        cp_path,
        {
            "shard_id": spec.window.shard_id,
            "execution_source_fingerprint": "test",
            "preceding_controlling_attempt_id": None,
            "next_global_iteration": 0,
            "storage_device_ids": ["p0_storage_bus_7"],
            "realized_soc_mwh": [500.0],
            "initial_soc_mwh": [500.0],
            "interval": {"start": 0, "stop": 6},
        },
    )
    directory = tmp_path / "attempt"
    atomic_immutable_json(
        directory / "request.json",
        {
            "execution_context": context,
            "authority_path": "unused",
            "output_root": str(tmp_path),
            "contract_sha256": "test",
            "invocation": asdict(spec),
            "checkpoint_path": str(cp_path),
            "checkpoint_sha256": sha256_path(cp_path),
            "preceding_source": None,
            "source_sha256": object_sha256(None),
            "target_free_directory": None,
            "replay_start": None,
        },
    )
    worker.execute(directory)
    assert len(calls) == 1
    phases = json.loads((directory / "phase.json").read_text())["events"]
    assert [item["phase"] for item in phases] == [
        "before_ac_build",
        "after_ac_build",
        "before_ac_solve",
        "after_ac_solve",
    ]
    assert (directory / "start.json").is_file()
    candidate = archives.audit_candidate(
        directory,
        spec,
        inputs=fixture.inputs,
        policy=fixture.policy,
        outer=outer,
        initial={"p0_storage_bus_7": 500.0},
        stop=3,
    )
    assert candidate.completion.outcome == "rejected"
