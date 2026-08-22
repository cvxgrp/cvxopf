from __future__ import annotations

from pathlib import Path

import pytest

from experiments.case118_annual_hierarchy.p0_import_gate import scan_source
from experiments.case118_annual_hierarchy.run_s2 import (
    CHECKPOINT_STALL_LIMIT_SECONDS,
    POLL_SECONDS,
    RSS_LIMIT_MIB,
    TOTAL_WALL_LIMIT_SECONDS,
    _classification,
    _stale_invocation_wall_seconds,
    _validate_resume_authorization,
    execution_context,
    s2_source_paths,
)
from experiments.case118_annual_hierarchy.s2_fixture import S2_HORIZON_STEPS


def _completed_worker():
    return {
        "classification": "completed",
        "trajectory_status": "complete",
        "completed_intervals": S2_HORIZON_STEPS,
        "provenance_matches": True,
        "eligible_for_advancement": True,
    }


def test_s2_resource_policy_is_frozen_before_execution():
    assert RSS_LIMIT_MIB == 16 * 1024
    assert CHECKPOINT_STALL_LIMIT_SECONDS == 60 * 60
    assert TOTAL_WALL_LIMIT_SECONDS == 48 * 60 * 60
    assert POLL_SECONDS == 1.0


def test_s2_execution_context_binds_scenario_policy_solver_and_source():
    context = execution_context()

    assert context["git_commit"]
    assert len(context["source_fingerprint"]) == 64
    assert len(context["scenario_hash"]) == 64
    assert len(context["policy_sha256"]) == 64
    assert len(context["solve_config_sha256"]) == 64
    assert context["software_versions"]["ipopt"]


def test_s2_source_registry_includes_imported_helpers_and_nested_cvxopf():
    root = Path(__file__).resolve().parents[1]
    registered = {path.relative_to(root).as_posix() for path in s2_source_paths()}

    assert "experiments/case118_annual_hierarchy/run_s0.py" in registered
    assert "src/cvxopf/testcases/case118.py" in registered


def test_supervision_classification_requires_complete_verified_worker():
    classification, eligible = _classification(
        resource_reason=None,
        returncode=0,
        worker=_completed_worker(),
        context_matches=True,
    )

    assert classification == "completed"
    assert eligible


def test_resource_limit_dominates_a_late_completed_worker_result():
    classification, eligible = _classification(
        resource_reason="rss_limit",
        returncode=-15,
        worker=_completed_worker(),
        context_matches=True,
    )

    assert classification == "resource_limit"
    assert not eligible


def test_worker_safe_boundary_resource_limit_is_not_scientific_termination():
    worker = {
        **_completed_worker(),
        "classification": "resource_limit",
        "trajectory_status": "observer_terminated",
        "completed_intervals": 12,
        "eligible_for_advancement": False,
    }
    classification, eligible = _classification(
        resource_reason=None,
        returncode=0,
        worker=worker,
        context_matches=True,
    )

    assert classification == "resource_limit"
    assert not eligible


def test_resume_is_rejected_after_a_declared_terminal_classification(tmp_path):
    latest = tmp_path / "latest-supervision.json"
    latest.write_text('{"classification": "resource_limit"}')

    with pytest.raises(
        ValueError, match="only after an unexplained worker failure"
    ):
        _validate_resume_authorization(tmp_path)

    latest.write_text('{"classification": "worker_failure"}')
    _validate_resume_authorization(tmp_path)


def test_s2_execution_wrapper_has_no_forbidden_streaming_dependencies():
    root = Path(__file__).resolve().parents[1]
    for relative in (
        "experiments/case118_annual_hierarchy/s2_fixture.py",
        "experiments/case118_annual_hierarchy/s2_analysis.py",
        "experiments/case118_annual_hierarchy/run_s2.py",
    ):
        path = root / relative
        assert scan_source(path.read_text(), path=relative) == ()


def test_stale_supervisor_time_is_retained_and_live_overlap_is_rejected(
    tmp_path, monkeypatch
):
    active = tmp_path / "active-invocation.json"
    active.write_text(
        '{"supervisor_pid": 123, "started_epoch_seconds": 900.0}'
    )
    monkeypatch.setattr("time.time", lambda: 1000.0)

    def stale(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr("os.kill", stale)
    assert _stale_invocation_wall_seconds(tmp_path) == 100.0

    monkeypatch.setattr("os.kill", lambda _pid, _signal: None)
    with pytest.raises(ValueError, match="still active"):
        _stale_invocation_wall_seconds(tmp_path)

    active.write_text(
        '{"supervisor_pid": 123, "worker_pid": 456, '
        '"started_epoch_seconds": 900.0}'
    )

    def live_worker(pid, _signal):
        if pid == 123:
            raise ProcessLookupError

    monkeypatch.setattr("os.kill", live_worker)
    with pytest.raises(ValueError, match="worker is still active"):
        _stale_invocation_wall_seconds(tmp_path)


def test_supervision_rejects_worker_failure_provenance_and_partial_science():
    cases = (
        (None, 1, None, True, "worker_failure"),
        (None, 0, _completed_worker(), False, "provenance_mismatch"),
        (
            None,
            0,
            {
                **_completed_worker(),
                "classification": "scientific_termination",
                "trajectory_status": "recovery_exhausted",
                "completed_intervals": 17,
                "eligible_for_advancement": False,
            },
            True,
            "scientific_termination",
        ),
    )
    for resource, returncode, worker, matches, expected in cases:
        classification, eligible = _classification(
            resource_reason=resource,
            returncode=returncode,
            worker=worker,
            context_matches=matches,
        )
        assert classification == expected
        assert not eligible


def test_worker_declared_provenance_mismatch_precedes_nonzero_return_code():
    worker = {
        "classification": "provenance_mismatch",
        "provenance_matches": False,
        "eligible_for_advancement": False,
    }

    classification, eligible = _classification(
        resource_reason=None,
        returncode=2,
        worker=worker,
        context_matches=True,
    )

    assert classification == "provenance_mismatch"
    assert not eligible
