from __future__ import annotations

import pytest

from experiments.case118_annual_hierarchy.p0_injected_equivalence import (
    INJECTED_CASES,
    run_injected_equivalence,
)


@pytest.mark.parametrize("case", INJECTED_CASES, ids=lambda case: case.name)
def test_injected_recovery_and_termination_matrix(tmp_path, case):
    report = run_injected_equivalence(case, tmp_path / case.name)

    assert report.equivalent, report.mismatches
    assert report.public_completed_intervals == case.expected_completed_intervals
    assert report.streaming_completed_intervals == case.expected_completed_intervals
    assert report.controlling_ordinals == case.expected_controlling_ordinals
    assert report.terminal_outcome == case.expected_terminal_outcome
    assert report.unsuccessful_evidence_count == len(case.outcomes)
    assert report.unsuccessful_evidence_verified
