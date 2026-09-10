from __future__ import annotations

from dataclasses import replace

from experiments.case118_annual_hierarchy import p0_persistence_gate
from experiments.case118_annual_hierarchy.p0_persistence_gate import (
    run_persistence_gate,
)


def test_resume_corruption_atomicity_and_release_gate(tmp_path):
    report = run_persistence_gate(tmp_path)

    assert report.passed, report.failures
    assert report.stopped_intervals == 2
    assert report.stopped_status == "observer_terminated"
    assert report.stopped_reason == "p0 safe boundary"
    assert report.resumed_intervals == 6
    assert report.reconstructed_attempt_id.startswith("ac-001-")
    assert report.reconstructed_variable_count > 0
    assert report.outer_build_released
    assert report.ac_builds_released
    assert report.corruption_cases_rejected == ("window", "checkpoint", "resource")
    assert report.prior_checkpoint_preserved
    assert report.retry_boundary_intervals == 2
    assert report.zero_boundary_recovered


def test_persistence_gate_rejects_wrong_observer_stop_contract(
    tmp_path, monkeypatch
):
    original = p0_persistence_gate._run
    first = True

    def wrong_observer_result(*args, **kwargs):
        nonlocal first
        result = original(*args, **kwargs)
        if first:
            first = False
            return replace(
                result,
                status="complete",
                completed_intervals=6,
                termination_reason=None,
            )
        return result

    monkeypatch.setattr(p0_persistence_gate, "_run", wrong_observer_result)

    report = run_persistence_gate(tmp_path)

    assert not report.passed
    assert "observer_stop" in report.failures
