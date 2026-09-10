"""End-to-end checks for the experiment-owned streaming trajectory driver."""

from __future__ import annotations

import gzip
import json
import weakref

import pytest

from experiments.case118_annual_hierarchy import streaming_driver
from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy.streaming_archive import (
    causal_source_from_archive,
)
from experiments.case118_annual_hierarchy.streaming_driver import (
    run_streaming_trajectory,
)


def test_trajectory_stops_safely_then_resumes_to_completion(tmp_path):
    fixture = load_p0_fixture(6)
    observed = []

    def stop_after_two(state):
        observed.append(state)
        return "test boundary" if state.completed_intervals == 2 else None

    partial = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        observer=stop_after_two,
        rss_reader=lambda: 123,
    )

    assert partial.status == "observer_terminated"
    assert partial.completed_intervals == 2
    assert len(partial.completed_window_artifacts) == 2
    assert observed[-1].completed_intervals == 2
    assert observed[-1].resource_samples[-1].phase == "after_release"
    assert all(sample.rss_bytes == 123 for sample in partial.resource_samples)
    phases = {sample.phase for sample in partial.resource_samples}
    assert {
        "before_ac_build",
        "after_ac_build",
        "before_ac_solve",
        "after_ac_solve",
        "after_archive",
        "after_release",
    }.issubset(phases)

    resumed = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        resume=True,
        rss_reader=lambda: 456,
    )

    assert resumed.status == "complete"
    assert resumed.completed_intervals == 6
    assert len(resumed.completed_window_artifacts) == 6
    assert [entry.iteration for entry in resumed.completed_window_artifacts] == list(
        range(6)
    )
    termination = json.loads((tmp_path / "termination.json").read_text())
    assert termination["status"] == "complete"
    assert any(sample.invocation == 0 for sample in resumed.resource_samples)
    assert any(sample.invocation == 1 for sample in resumed.resource_samples)
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    resource_path = tmp_path / checkpoint["resource_evidence"]["relative_path"]
    evidence = json.loads(resource_path.read_text())
    assert len(evidence["samples"]) < len(resumed.resource_samples)
    assert checkpoint["resource_evidence"]["sample_count"] == len(
        resumed.resource_samples
    )
    stored_sample_count = sum(
        len(json.loads(path.read_text())["samples"])
        for path in tmp_path.glob("resource-samples-*.json")
    )
    assert stored_sample_count == len(resumed.resource_samples)
    assert (
        len(list(tmp_path.glob("resource-samples-*.json")))
        == checkpoint["resource_evidence"]["chunk_count"]
    )


def test_archived_controller_reconstructs_complete_build_free_causal_source(tmp_path):
    fixture = load_p0_fixture(6)
    result = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        observer=lambda state: "one window is enough",
    )
    entry = result.completed_window_artifacts[0]
    with gzip.open(tmp_path / entry.relative_path, "rt", encoding="utf-8") as stream:
        archive = json.load(stream)
    attempt = next(
        item
        for item in archive["attempts"]
        if item["attempt_id"] == archive["executed_interval"]["controlling_attempt_id"]
    )
    source = causal_source_from_archive(attempt)

    assert source.attempt_id == attempt["attempt_id"]
    assert source.iteration == 0
    assert source.solution_values
    assert all(not values.flags.writeable for values in source.solution_values.values())


def test_archive_failure_terminates_without_advancing_checkpoint(tmp_path, monkeypatch):
    fixture = load_p0_fixture(6)

    def fail_transaction(*args, **kwargs):
        raise OSError("synthetic persistence failure")

    monkeypatch.setattr(
        streaming_driver, "persist_window_transaction", fail_transaction
    )
    result = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
    )

    assert result.status == "artifact_failure"
    assert result.completed_intervals == 0
    assert result.completed_window_artifacts == ()
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    assert checkpoint["completed_intervals"] == 0
    assert checkpoint["windows"] == []
    assert "synthetic persistence failure" in str(result.termination_reason)

    monkeypatch.undo()
    resumed = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        resume=True,
        observer=lambda state: "zero-boundary resume",
        rss_reader=lambda: 100,
    )
    assert resumed.completed_intervals == 1


def test_fresh_mode_rejects_existing_durable_run(tmp_path):
    fixture = load_p0_fixture(6)
    (tmp_path / "checkpoint.json").write_text("{}")

    with pytest.raises(FileExistsError, match="already contains"):
        run_streaming_trajectory(
            tmp_path,
            fixture.inputs,
            fixture.policy,
            fixture.solve_config,
            source_fingerprint="source",
            scenario_hash="scenario",
        )


def test_live_outer_and_ac_builds_are_released_after_safe_boundary(
    tmp_path, monkeypatch
):
    fixture = load_p0_fixture(6)
    outer_refs = []
    ac_refs = []
    original_outer = streaming_driver.solve_frozen_outer
    original_window = streaming_driver.execute_streaming_window

    def capture_outer(*args, **kwargs):
        outer = original_outer(*args, **kwargs)
        assert outer.build is not None
        outer_refs.append(weakref.ref(outer.build))
        return outer

    def capture_window(*args, **kwargs):
        window = original_window(*args, **kwargs)
        ac_refs.extend(
            weakref.ref(attempt.build)
            for attempt in window.attempts
            if attempt.build is not None
        )
        return window

    monkeypatch.setattr(streaming_driver, "solve_frozen_outer", capture_outer)
    monkeypatch.setattr(streaming_driver, "execute_streaming_window", capture_window)
    result = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        observer=lambda state: "release check",
    )

    assert result.status == "observer_terminated"
    assert outer_refs and all(reference() is None for reference in outer_refs)
    assert ac_refs and all(reference() is None for reference in ac_refs)


def test_macos_rss_uses_current_in_process_value(monkeypatch):
    monkeypatch.setattr(streaming_driver.sys, "platform", "darwin")
    monkeypatch.setattr(streaming_driver, "_darwin_current_rss_bytes", lambda: 12345)

    assert streaming_driver.process_rss_bytes() == 12345


def test_resume_rejects_altered_resource_history(tmp_path):
    fixture = load_p0_fixture(6)
    run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        observer=lambda state: "one window",
        rss_reader=lambda: 100,
    )
    checkpoint = json.loads((tmp_path / "checkpoint.json").read_text())
    resource_path = tmp_path / checkpoint["resource_evidence"]["relative_path"]
    payload = json.loads(resource_path.read_text())
    payload["samples"][0]["rss_bytes"] = 999
    resource_path.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="resource evidence integrity"):
        run_streaming_trajectory(
            tmp_path,
            fixture.inputs,
            fixture.policy,
            fixture.solve_config,
            source_fingerprint="source",
            scenario_hash="scenario",
            resume=True,
        )


def test_resource_publication_failure_preserves_previous_resume_boundary(
    tmp_path, monkeypatch
):
    fixture = load_p0_fixture(6)
    first = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        observer=lambda state: "one window",
        rss_reader=lambda: 100,
    )
    assert first.completed_intervals == 1
    checkpoint_path = tmp_path / "checkpoint.json"
    previous_checkpoint = checkpoint_path.read_bytes()
    previous_payload = json.loads(previous_checkpoint)
    previous_resource = (
        tmp_path / previous_payload["resource_evidence"]["relative_path"]
    )
    previous_resource_bytes = previous_resource.read_bytes()
    original_atomic_json = streaming_driver.atomic_json
    failed = False

    def fail_next_checkpoint(path, value):
        nonlocal failed
        if path == checkpoint_path and not failed:
            failed = True
            raise OSError("synthetic checkpoint publication failure")
        original_atomic_json(path, value)

    monkeypatch.setattr(streaming_driver, "atomic_json", fail_next_checkpoint)
    failed_run = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        resume=True,
        rss_reader=lambda: 200,
    )

    assert failed_run.status == "artifact_failure"
    assert failed_run.completed_intervals == 1
    assert checkpoint_path.read_bytes() == previous_checkpoint
    assert previous_resource.read_bytes() == previous_resource_bytes

    recovered = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        resume=True,
        observer=lambda state: "retried boundary",
        rss_reader=lambda: 300,
    )
    assert recovered.status == "observer_terminated"
    assert recovered.completed_intervals == 2


def test_initial_checkpoint_failure_recovers_from_verified_outer_artifact(
    tmp_path, monkeypatch
):
    fixture = load_p0_fixture(6)
    checkpoint_path = tmp_path / "checkpoint.json"
    original_atomic_json = streaming_driver.atomic_json
    failed = False

    def fail_initial_checkpoint(path, value):
        nonlocal failed
        if path == checkpoint_path and not failed:
            failed = True
            raise OSError("synthetic initial checkpoint failure")
        original_atomic_json(path, value)

    monkeypatch.setattr(streaming_driver, "atomic_json", fail_initial_checkpoint)
    interrupted = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        rss_reader=lambda: 100,
    )

    assert interrupted.status == "artifact_failure"
    assert interrupted.completed_intervals == 0
    assert (tmp_path / "outer-plan.json.gz").is_file()
    assert not checkpoint_path.exists()

    for source, scenario in (
        ("different-source", "scenario"),
        ("source", "different-scenario"),
    ):
        with pytest.raises(ValueError, match="outer-plan artifact"):
            run_streaming_trajectory(
                tmp_path,
                fixture.inputs,
                fixture.policy,
                fixture.solve_config,
                source_fingerprint=source,
                scenario_hash=scenario,
                resume=True,
                rss_reader=lambda: 150,
            )

    recovered = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        resume=True,
        observer=lambda state: "recovered zero boundary",
        rss_reader=lambda: 200,
    )
    assert recovered.status == "observer_terminated"
    assert recovered.completed_intervals == 1
    assert json.loads(checkpoint_path.read_text())["completed_intervals"] == 1


@pytest.mark.parametrize("alteration", ["result", "audit"])
def test_checkpoint_free_recovery_rejects_semantically_altered_outer(
    tmp_path, monkeypatch, alteration
):
    fixture = load_p0_fixture(6)
    checkpoint_path = tmp_path / "checkpoint.json"
    original_atomic_json = streaming_driver.atomic_json
    failed = False

    def fail_initial_checkpoint(path, value):
        nonlocal failed
        if path == checkpoint_path and not failed:
            failed = True
            raise OSError("synthetic initial checkpoint failure")
        original_atomic_json(path, value)

    monkeypatch.setattr(streaming_driver, "atomic_json", fail_initial_checkpoint)
    interrupted = run_streaming_trajectory(
        tmp_path,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint="source",
        scenario_hash="scenario",
        rss_reader=lambda: 100,
    )
    assert interrupted.status == "artifact_failure"
    outer_path = tmp_path / "outer-plan.json.gz"
    with gzip.open(outer_path, "rt", encoding="utf-8") as stream:
        payload = json.load(stream)
    if alteration == "result":
        payload["result"]["soc"][0][0] += 1.0
    else:
        payload["audit"]["status"] = "user_limit"
    with gzip.open(outer_path, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream)

    with pytest.raises(ValueError, match="outer-plan"):
        run_streaming_trajectory(
            tmp_path,
            fixture.inputs,
            fixture.policy,
            fixture.solve_config,
            source_fingerprint="source",
            scenario_hash="scenario",
            resume=True,
            rss_reader=lambda: 200,
        )
