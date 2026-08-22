from __future__ import annotations

from experiments.case118_annual_hierarchy.p0_persistence_gate import (
    run_persistence_gate,
)


def test_resume_corruption_atomicity_and_release_gate(tmp_path):
    report = run_persistence_gate(tmp_path)

    assert report.passed, report.failures
    assert report.stopped_intervals == 2
    assert report.resumed_intervals == 6
    assert report.reconstructed_attempt_id.startswith("ac-001-")
    assert report.reconstructed_variable_count > 0
    assert report.outer_build_released
    assert report.ac_builds_released
    assert report.corruption_cases_rejected == ("window", "checkpoint", "resource")
    assert report.prior_checkpoint_preserved
    assert report.retry_boundary_intervals == 2
    assert report.zero_boundary_recovered
