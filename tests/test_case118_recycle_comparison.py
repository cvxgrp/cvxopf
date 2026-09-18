from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.case118_annual_hierarchy.reference.extract_s2_reference import (
    EXPECTED_OUTER_SHA256,
)
from experiments.case118_annual_hierarchy import run_recycle_comparison
from experiments.case118_annual_hierarchy.run_recycle_comparison import (
    ARM_ORDER,
    COMPARISON_SOURCE_PATHS,
    _archive_stale_active_invocation,
    _checkpoint_candidate,
    _next_invocation,
    _prior_wall_seconds,
    _software_versions,
    _validate_reviewed_resume,
    _worker,
    comparison_source_fingerprint,
    comparison_source_paths,
    is_restart_endpoint_candidate,
    observer_reason,
    resume_arm,
    s2_source_paths_resolved,
    seed_fresh_arm,
)
from experiments.case118_annual_hierarchy.streaming_schema import sha256_path


def test_frozen_arm_order_and_planned_boundaries():
    assert ARM_ORDER == ("never", "recycle_32", "recycle_16")
    assert observer_reason("never", 32) is None
    assert observer_reason("never", 64) == "study_complete"
    assert observer_reason("recycle_32", 32) == "planned_recycle"
    assert observer_reason("recycle_16", 16) == "planned_recycle"
    assert observer_reason("recycle_16", 32) == "planned_recycle"
    assert observer_reason("recycle_16", 48) == "planned_recycle"
    assert observer_reason("recycle_16", 64) == "study_complete"


def test_passed_boundary_does_not_retrigger_after_interruption_resume():
    assert observer_reason("recycle_16", 16, already_passed=20) is None
    assert observer_reason("recycle_16", 20, already_passed=20) is None
    assert observer_reason("recycle_16", 32, already_passed=20) == ("planned_recycle")


def test_unknown_arm_is_rejected():
    with pytest.raises(ValueError, match="unknown comparison arm"):
        observer_reason("unknown", 1)


def test_restart_endpoint_requires_exactly_one_new_verified_generation():
    before = (16, "old")

    assert is_restart_endpoint_candidate(before, before) is False
    assert is_restart_endpoint_candidate(before, (16, "changed")) is False
    assert is_restart_endpoint_candidate(before, (18, "changed")) is False
    assert is_restart_endpoint_candidate(before, (17, "old")) is False
    assert is_restart_endpoint_candidate(before, (17, "changed")) is True
    assert is_restart_endpoint_candidate(None, (1, "first")) is False


def test_checkpoint_candidate_hashes_and_parses_one_byte_snapshot(tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    encoded = b'{"completed_intervals":17}'
    path.write_bytes(encoded)

    candidate = _checkpoint_candidate(path)

    assert candidate == (17, hashlib.sha256(encoded).hexdigest())
    path.write_text("not-json")
    assert _checkpoint_candidate(path) is None
    path.unlink()
    assert _checkpoint_candidate(path) is None


def test_seed_fresh_arm_copies_outer_once_and_refuses_overwrite(tmp_path: Path):
    arm = tmp_path / "never"

    outer = seed_fresh_arm(arm)

    assert sha256_path(outer) == EXPECTED_OUTER_SHA256
    with pytest.raises(FileExistsError, match="absent directory"):
        seed_fresh_arm(arm)
    assert sha256_path(outer) == EXPECTED_OUTER_SHA256


def test_first_worker_invocation_uses_checkpoint_free_resume_true(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    context = {
        "git_commit": "commit",
        "git_clean": True,
        "comparison_source_fingerprint": "comparison",
        "model_source_fingerprint": (
            run_recycle_comparison.HISTORICAL_SOURCE_FINGERPRINT
        ),
    }
    monkeypatch.setattr(run_recycle_comparison, "execution_context", lambda: context)
    captured: dict[str, object] = {}

    def run_streaming(*args, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            status="observer_terminated",
            completed_intervals=64,
            termination_reason="study_complete",
        )

    monkeypatch.setattr(
        run_recycle_comparison, "run_streaming_trajectory", run_streaming
    )

    returncode = _worker(
        tmp_path,
        arm="never",
        invocation=0,
        passed_boundary=0,
        expected_commit="commit",
        expected_comparison_fingerprint="comparison",
    )

    assert returncode == 0
    assert captured["resume"] is True
    assert captured["source_fingerprint"] == (
        run_recycle_comparison.HISTORICAL_SOURCE_FINGERPRINT
    )
    worker = json.loads((tmp_path / "worker-result-000.json").read_text())
    assert worker["classification"] == "study_complete"
    assert worker["completed_intervals"] == 64


def test_software_provenance_includes_the_complete_solver_stack():
    versions = _software_versions()

    assert {
        "python",
        "cvxopf",
        "cvxpy",
        "numpy",
        "pandas",
        "clarabel",
        "cyipopt",
        "ipopt",
    }.issubset(versions)
    assert versions["ipopt"] is None or len(versions["ipopt"]) >= 3


def test_comparison_source_registry_is_complete_ordered_and_disjoint():
    paths = comparison_source_paths()

    assert tuple(sorted(set(COMPARISON_SOURCE_PATHS))) == COMPARISON_SOURCE_PATHS
    assert all(path.is_file() for path in paths)
    assert not set(paths).intersection(s2_source_paths_resolved())
    assert any(path.name == "RECYCLE_COMPARISON_PROTOCOL.md" for path in paths)
    assert all(path.name != "RECYCLE_COMPARISON_RESULTS.json" for path in paths)
    assert len(comparison_source_fingerprint()) == 64


def test_reviewed_resume_counts_all_retained_wall_and_future_recycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    root = tmp_path / "comparison"
    never = root / "never"
    arm = root / "recycle_16"
    never.mkdir(parents=True)
    arm.mkdir()
    (never / "supervision-000.json").write_text('{"wall_time_seconds": 10.0}')
    (arm / "supervision-000.json").write_text('{"wall_time_seconds": 20.0}')
    monkeypatch.setattr(
        run_recycle_comparison,
        "_validate_reviewed_resume",
        lambda directory: {},
    )
    prior_totals: list[float] = []
    outcomes = iter(
        [
            {"classification": "planned_recycle", "wall_time_seconds": 5.0},
            {"classification": "study_complete", "wall_time_seconds": 7.0},
        ]
    )

    def supervise(directory: Path, *, arm: str, total_prior_wall_seconds: float):
        assert directory == root / "recycle_16"
        assert arm == "recycle_16"
        prior_totals.append(total_prior_wall_seconds)
        return next(outcomes)

    monkeypatch.setattr(run_recycle_comparison, "supervise_invocation", supervise)

    records = resume_arm(root, "recycle_16")

    assert [record["classification"] for record in records] == [
        "planned_recycle",
        "study_complete",
    ]
    assert prior_totals == [30.0, 35.0]


def test_stale_active_invocation_is_archived_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    active = {
        "invocation": 2,
        "supervisor_pid": 100,
        "worker_pid": 101,
        "started_epoch_seconds": 90.0,
    }
    (tmp_path / "active-invocation.json").write_text(json.dumps(active))
    monkeypatch.setattr(run_recycle_comparison, "_pid_is_alive", lambda pid: False)
    monkeypatch.setattr(run_recycle_comparison.time, "time", lambda: 100.0)

    record = _archive_stale_active_invocation(tmp_path)

    assert record is not None
    assert record["classification"] == "reviewed_interruption"
    assert record["wall_time_seconds"] == 10.0
    assert not (tmp_path / "active-invocation.json").exists()
    assert (tmp_path / "interrupted-invocation-002.json").is_file()
    assert _next_invocation(tmp_path) == 3
    assert _prior_wall_seconds(tmp_path) == 10.0


def test_stale_active_marker_after_supervision_does_not_double_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    (tmp_path / "active-invocation.json").write_text(
        json.dumps(
            {
                "invocation": 0,
                "supervisor_pid": 100,
                "worker_pid": 101,
                "started_epoch_seconds": 90.0,
            }
        )
    )
    (tmp_path / "supervision-000.json").write_text(
        json.dumps({"wall_time_seconds": 4.0})
    )
    monkeypatch.setattr(run_recycle_comparison, "_pid_is_alive", lambda pid: False)

    assert _archive_stale_active_invocation(tmp_path) is None
    assert not (tmp_path / "active-invocation.json").exists()
    assert not (tmp_path / "interrupted-invocation-000.json").exists()
    assert _prior_wall_seconds(tmp_path) == 4.0


def test_live_active_process_blocks_reviewed_resume_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    active_path = tmp_path / "active-invocation.json"
    active_path.write_text(
        json.dumps(
            {
                "invocation": 0,
                "supervisor_pid": 100,
                "worker_pid": 101,
                "started_epoch_seconds": 90.0,
            }
        )
    )
    monkeypatch.setattr(run_recycle_comparison, "_pid_is_alive", lambda pid: pid == 101)

    with pytest.raises(ValueError, match="still active"):
        _archive_stale_active_invocation(tmp_path)

    assert active_path.is_file()
    assert not (tmp_path / "interrupted-invocation-000.json").exists()


def test_reviewed_resume_rejects_changed_executable_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    arm = tmp_path / "recycle_16"
    trajectory = arm / "trajectory"
    trajectory.mkdir(parents=True)
    (trajectory / "checkpoint.json").write_text("{}")
    (arm / "supervision-000.json").write_text('{"wall_time_seconds": 1.0}')
    (arm / "latest-supervision.json").write_text('{"classification": "worker_failure"}')
    previous = {
        "git_commit": "commit",
        "git_clean": True,
        "model_source_fingerprint": "model",
        "comparison_source_fingerprint": "old-comparison",
        "scenario_hash": "scenario",
        "policy_sha256": "policy",
        "solve_config_sha256": "solve",
        "outer_plan_sha256": "outer",
    }
    (arm / "run-context-000.json").write_text(json.dumps(previous))
    monkeypatch.setattr(run_recycle_comparison, "verify_checkpoint", lambda path: {})
    current = dict(previous)
    current["comparison_source_fingerprint"] = "new-comparison"
    monkeypatch.setattr(run_recycle_comparison, "execution_context", lambda: current)

    with pytest.raises(ValueError, match="comparison_source_fingerprint"):
        _validate_reviewed_resume(arm)
