from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import pytest

from experiments.case118_annual_hierarchy import run_s3


def test_observer_reason_uses_exact_global_schedule() -> None:
    outcomes = {
        boundary: run_s3.observer_reason(boundary, passed_boundary=0)
        for boundary in range(721)
    }
    assert [key for key, value in outcomes.items() if value == "planned_recycle"] == list(
        range(16, 720, 16)
    )
    assert outcomes[720] == "study_complete"
    assert outcomes[0] is None
    assert outcomes[15] is None
    assert outcomes[17] is None


def test_observer_reason_does_not_recycle_passed_local_boundary() -> None:
    assert run_s3.observer_reason(16, passed_boundary=16) is None
    assert run_s3.observer_reason(31, passed_boundary=16) is None
    assert run_s3.observer_reason(32, passed_boundary=16) == "planned_recycle"
    with pytest.raises(ValueError, match="global boundary ordering"):
        run_s3.observer_reason(15, passed_boundary=16)


def test_run_s3_registers_44_recycles_then_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    completed = 0
    records: list[Mapping[str, object]] = []

    def supervise(_directory: Path) -> Mapping[str, object]:
        nonlocal completed
        before = completed
        completed = min(completed + 16, 720)
        classification = (
            "study_complete" if completed == 720 else "planned_recycle"
        )
        record: Mapping[str, object] = {
            "classification": classification,
            "completed_before": before,
            "completed_after": completed,
            "wall_time_seconds": 1.0,
        }
        records.append(record)
        return record

    monkeypatch.setattr(run_s3, "supervise_invocation", supervise)
    result = run_s3.run_s3(tmp_path / "study")

    assert result["complete"] is True
    assert len(records) == 45
    assert [item["completed_after"] for item in records[:-1]] == list(
        range(16, 720, 16)
    )
    assert records[-1]["completed_after"] == 720
    assert records[-1]["classification"] == "study_complete"


def test_characterized_short_lifecycle_recycles_then_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outcomes = iter(
        [
            {
                "classification": "planned_recycle",
                "completed_before": 0,
                "completed_after": 16,
                "wall_time_seconds": 2.0,
            },
            {
                "classification": "study_complete",
                "completed_before": 16,
                "completed_after": 720,
                "wall_time_seconds": 3.0,
            },
        ]
    )
    monkeypatch.setattr(run_s3, "supervise_invocation", lambda _directory: next(outcomes))

    result = run_s3.run_s3(tmp_path / "study")

    assert result["complete"] is True
    assert len(result["records"]) == 2
    assert result["records"][0]["classification"] == "planned_recycle"
    assert result["records"][1]["classification"] == "study_complete"


@pytest.mark.parametrize(
    "classification",
    [
        "rss_limit",
        "invocation_wall_limit",
        "total_wall_limit",
        "checkpoint_stall_limit",
        "worker_failure",
        "artifact_failure",
        "provenance_mismatch",
    ],
)
def test_abnormal_outcome_stops_automatic_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    classification: str,
) -> None:
    calls = 0

    def supervise(_directory: Path) -> Mapping[str, object]:
        nonlocal calls
        calls += 1
        return {
            "classification": classification,
            "completed_before": 16,
            "completed_after": 17,
            "wall_time_seconds": 1.0,
        }

    monkeypatch.setattr(run_s3, "supervise_invocation", supervise)
    result = run_s3.run_s3(tmp_path / "study")

    assert result["complete"] is False
    assert calls == 1


def test_reviewed_continuation_requires_abnormal_retained_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "study"
    trajectory = directory / "trajectory"
    trajectory.mkdir(parents=True)
    (trajectory / "checkpoint.json").write_text("{}")
    context = {"git_clean": True, "source_fingerprint": "frozen"}
    monkeypatch.setattr(run_s3, "verify_checkpoint", lambda _path: {})
    monkeypatch.setattr(run_s3, "execution_context", lambda: context)
    monkeypatch.setattr(
        run_s3, "_checkpoint_candidate", lambda _path: (35, "checkpoint-35")
    )

    retained = {
        "invocation": 0,
        "classification": "rss_limit",
        "completed_after": 35,
        "checkpoint_sha256_after": "checkpoint-35",
        "start_context": context,
        "wall_time_seconds": 1.0,
    }
    (directory / "latest-supervision.json").write_text(json.dumps(retained))
    (directory / "supervision-000.json").write_text(json.dumps(retained))
    (directory / "run-context-000.json").write_text(json.dumps(context))
    prior = run_s3._validate_reviewed_continuation(directory)
    assert prior.prior_record_kind == "supervision"
    assert prior.prior_classification == "rss_limit"
    assert prior.completed_intervals == 35

    (directory / "latest-supervision.json").write_text(
        json.dumps(
            {
                "invocation": 0,
                "classification": "planned_recycle",
                "start_context": context,
            }
        )
    )
    with pytest.raises(ValueError, match="normal S3 outcomes"):
        run_s3._validate_reviewed_continuation(directory)


def test_reviewed_continuation_persists_authorization_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "study"
    trajectory = directory / "trajectory"
    trajectory.mkdir(parents=True)
    checkpoint = trajectory / "checkpoint.json"
    checkpoint.write_text("{}")
    context = {"git_clean": True, "source_fingerprint": "frozen"}
    retained = {
        "invocation": 2,
        "classification": "rss_limit",
        "completed_after": 35,
        "checkpoint_sha256_after": "checkpoint-35",
        "start_context": context,
        "wall_time_seconds": 1.0,
    }
    (directory / "latest-supervision.json").write_text(json.dumps(retained))
    (directory / "supervision-002.json").write_text(json.dumps(retained))
    (directory / "run-context-002.json").write_text(json.dumps(context))
    monkeypatch.setattr(run_s3, "verify_checkpoint", lambda _path: {})
    monkeypatch.setattr(
        run_s3, "_checkpoint_candidate", lambda _path: (35, "checkpoint-35")
    )
    monkeypatch.setattr(run_s3, "_next_invocation", lambda _path: 3)
    monkeypatch.setattr(run_s3, "execution_context", lambda: context)
    def supervise(_path: Path) -> Mapping[str, object]:
        assert (directory / "reviewed-continuation-003.json").is_file()
        return {
            "classification": "study_complete",
            "wall_time_seconds": 1.0,
        }

    monkeypatch.setattr(run_s3, "supervise_invocation", supervise)

    run_s3.run_s3(directory, reviewed=True)

    authorization = json.loads(
        (directory / "reviewed-continuation-003.json").read_text()
    )
    assert authorization["prior_record_kind"] == "supervision"
    assert authorization["prior_invocation"] == 2
    assert authorization["prior_record_path"] == "supervision-002.json"
    assert authorization["prior_classification"] == "rss_limit"
    assert authorization["completed_intervals"] == 35
    assert authorization["checkpoint_sha256"] == "checkpoint-35"
    assert authorization["execution_context"] == context


def test_stale_interruption_is_retained_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "study"
    trajectory = directory / "trajectory"
    trajectory.mkdir(parents=True)
    context = {"git_clean": True, "source_fingerprint": "frozen"}
    (directory / "run-context-003.json").write_text(json.dumps(context))
    (directory / "active-invocation.json").write_text(
        json.dumps(
            {
                "invocation": 3,
                "supervisor_pid": 1001,
                "worker_pid": 1002,
                "started_epoch_seconds": 10.0,
                "completed_before": 32,
                "checkpoint_sha256_before": "checkpoint-32",
            }
        )
    )
    monkeypatch.setattr(run_s3, "_pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(run_s3.time, "time", lambda: 25.0)
    monkeypatch.setattr(
        run_s3, "_checkpoint_candidate", lambda _path: (35, "checkpoint-35")
    )
    monkeypatch.setattr(run_s3, "_safe_execution_context", lambda: context)

    record = run_s3._archive_stale_active_invocation(directory)

    assert record is not None
    assert record["classification"] == "reviewed_interruption"
    assert record["completed_before"] == 32
    assert record["completed_after"] == 35
    assert record["wall_time_seconds"] == 15.0
    assert run_s3._prior_wall_seconds(directory) == 15.0
    assert run_s3._next_invocation(directory) == 4


def test_reviewed_continuation_uses_stale_interruption_without_latest_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "study"
    trajectory = directory / "trajectory"
    trajectory.mkdir(parents=True)
    checkpoint_path = trajectory / "checkpoint.json"
    checkpoint_path.write_text("{}")
    context = {"git_clean": True, "source_fingerprint": "frozen"}
    (directory / "run-context-000.json").write_text(json.dumps(context))
    (directory / "active-invocation.json").write_text(
        json.dumps(
            {
                "invocation": 0,
                "supervisor_pid": 1001,
                "worker_pid": 1002,
                "started_epoch_seconds": 10.0,
                "completed_before": 0,
                "checkpoint_sha256_before": None,
            }
        )
    )
    monkeypatch.setattr(run_s3, "_pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(run_s3.time, "time", lambda: 25.0)
    monkeypatch.setattr(run_s3, "verify_checkpoint", lambda _path: {})
    monkeypatch.setattr(
        run_s3, "_checkpoint_candidate", lambda _path: (5, "checkpoint-5")
    )
    monkeypatch.setattr(run_s3, "_safe_execution_context", lambda: context)
    monkeypatch.setattr(run_s3, "execution_context", lambda: context)
    monkeypatch.setattr(
        run_s3,
        "supervise_invocation",
        lambda _path: {
            "classification": "study_complete",
            "wall_time_seconds": 1.0,
        },
    )

    run_s3.run_s3(directory, reviewed=True)

    authorization = json.loads(
        (directory / "reviewed-continuation-001.json").read_text()
    )
    assert authorization["prior_record_kind"] == "interrupted_invocation"
    assert authorization["prior_invocation"] == 0
    assert authorization["prior_record_path"] == "interrupted-invocation-000.json"
    assert authorization["prior_classification"] == "reviewed_interruption"
    assert authorization["completed_intervals"] == 5


def test_reviewed_continuation_retries_archived_interruption_and_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "study"
    trajectory = directory / "trajectory"
    trajectory.mkdir(parents=True)
    (trajectory / "checkpoint.json").write_text("{}")
    context = {"git_clean": True, "source_fingerprint": "frozen"}
    (directory / "run-context-000.json").write_text(json.dumps(context))
    (directory / "active-invocation.json").write_text(
        json.dumps(
            {
                "invocation": 0,
                "supervisor_pid": 1001,
                "worker_pid": 1002,
                "started_epoch_seconds": 10.0,
                "completed_before": 0,
                "checkpoint_sha256_before": None,
            }
        )
    )
    monkeypatch.setattr(run_s3, "_pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(run_s3.time, "time", lambda: 25.0)
    monkeypatch.setattr(run_s3, "verify_checkpoint", lambda _path: {})
    monkeypatch.setattr(
        run_s3, "_checkpoint_candidate", lambda _path: (5, "checkpoint-5")
    )
    monkeypatch.setattr(run_s3, "_safe_execution_context", lambda: context)
    monkeypatch.setattr(run_s3, "execution_context", lambda: context)
    calls = 0

    def supervise(_path: Path) -> Mapping[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic exit after authorization")
        return {"classification": "study_complete", "wall_time_seconds": 1.0}

    monkeypatch.setattr(run_s3, "supervise_invocation", supervise)

    with pytest.raises(RuntimeError, match="synthetic exit"):
        run_s3.run_s3(directory, reviewed=True)
    assert not (directory / "active-invocation.json").exists()
    assert not (directory / "latest-supervision.json").exists()
    assert (directory / "reviewed-continuation-001.json").is_file()

    result = run_s3.run_s3(directory, reviewed=True)

    assert result["complete"] is True
    assert calls == 2


def test_reviewed_continuation_rejects_mismatched_existing_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "study"
    trajectory = directory / "trajectory"
    trajectory.mkdir(parents=True)
    (trajectory / "checkpoint.json").write_text("{}")
    context = {"git_clean": True, "source_fingerprint": "frozen"}
    retained = {
        "invocation": 0,
        "classification": "rss_limit",
        "completed_after": 5,
        "checkpoint_sha256_after": "checkpoint-5",
        "start_context": context,
    }
    (directory / "latest-supervision.json").write_text(json.dumps(retained))
    (directory / "supervision-000.json").write_text(json.dumps(retained))
    (directory / "run-context-000.json").write_text(json.dumps(context))
    (directory / "reviewed-continuation-001.json").write_text(
        json.dumps({"next_invocation": 1, "checkpoint_sha256": "wrong"})
    )
    monkeypatch.setattr(run_s3, "verify_checkpoint", lambda _path: {})
    monkeypatch.setattr(
        run_s3, "_checkpoint_candidate", lambda _path: (5, "checkpoint-5")
    )
    monkeypatch.setattr(run_s3, "execution_context", lambda: context)
    called = False

    def supervise(_path: Path) -> Mapping[str, object]:
        nonlocal called
        called = True
        return {"classification": "study_complete", "wall_time_seconds": 1.0}

    monkeypatch.setattr(run_s3, "supervise_invocation", supervise)

    with pytest.raises(ValueError, match="authorization mismatch"):
        run_s3.run_s3(directory, reviewed=True)
    assert called is False


def test_resumed_worker_rejects_changed_outer_before_solving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "study"
    trajectory = directory / "trajectory"
    trajectory.mkdir(parents=True)
    (trajectory / "outer-plan.json.gz").write_bytes(b"changed outer")
    context = {
        "git_commit": "commit",
        "git_clean": True,
        "source_fingerprint": "source",
    }
    monkeypatch.setattr(run_s3, "_safe_execution_context", lambda: context)

    returncode = run_s3._worker(
        directory,
        invocation=1,
        passed_boundary=16,
        expected_commit="commit",
        expected_source_fingerprint="source",
        expected_outer_sha256="not-the-current-hash",
    )

    assert returncode == 1
    result = json.loads((directory / "worker-result-001.json").read_text())
    assert result["classification"] == "artifact_failure"
    assert "outer-plan SHA-256 mismatch" in result["exception"]


def test_s3_source_registry_is_sorted_complete_and_recursive() -> None:
    paths = run_s3.s3_source_paths()
    relative = tuple(path.relative_to(run_s3.ROOT).as_posix() for path in paths)

    assert paths == tuple(sorted(set(paths)))
    assert "experiments/case118_annual_hierarchy/run_s3.py" in relative
    assert "experiments/case118_annual_hierarchy/S3_PROTOCOL.md" in relative
    assert "src/cvxopf/testcases/case118.py" in relative
