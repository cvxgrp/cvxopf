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

    (directory / "latest-supervision.json").write_text(
        json.dumps({"classification": "rss_limit", "start_context": context})
    )
    (directory / "run-context-000.json").write_text(json.dumps(context))
    run_s3._validate_reviewed_continuation(directory)

    (directory / "latest-supervision.json").write_text(
        json.dumps({"classification": "planned_recycle", "start_context": context})
    )
    with pytest.raises(ValueError, match="normal S3 outcomes"):
        run_s3._validate_reviewed_continuation(directory)


def test_stale_interruption_is_retained_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "study"
    directory.mkdir()
    (directory / "active-invocation.json").write_text(
        json.dumps(
            {
                "invocation": 3,
                "supervisor_pid": 1001,
                "worker_pid": 1002,
                "started_epoch_seconds": 10.0,
            }
        )
    )
    monkeypatch.setattr(run_s3, "_pid_is_alive", lambda _pid: False)
    monkeypatch.setattr(run_s3.time, "time", lambda: 25.0)

    record = run_s3._archive_stale_active_invocation(directory)

    assert record is not None
    assert record["classification"] == "reviewed_interruption"
    assert record["wall_time_seconds"] == 15.0
    assert run_s3._prior_wall_seconds(directory) == 15.0
    assert run_s3._next_invocation(directory) == 4


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
