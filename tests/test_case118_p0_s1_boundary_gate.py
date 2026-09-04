from __future__ import annotations

from copy import deepcopy
import json

from experiments.case118_annual_hierarchy import p0_s1_boundary_gate
from experiments.case118_annual_hierarchy.p0_s1_boundary_gate import (
    FIXTURE_PATH,
    run_s1_boundary_gate,
    validate_normalized_fixture,
)


def test_case118_s1_outer_endpoint_archive_gate():
    report = run_s1_boundary_gate()

    assert report.passed, report.failures
    assert report.tracked_fixture_digest_verified
    assert report.networks == ("pglib_rated", "pglib_effectively_unlimited")
    assert report.accepted_record_count == 4
    assert report.direct_ac_nonexecution_count == 2
    if report.source_artifact_available:
        assert report.source_integrity_verified
        assert report.source_rederived
        assert report.reconstructed_audit_count == 4
    else:
        assert not report.source_integrity_verified
        assert not report.source_rederived
        assert report.reconstructed_audit_count == 0


def test_s1_tracked_derivative_digest_rejects_material_drift():
    fixture = json.loads(FIXTURE_PATH.read_text())
    changed = deepcopy(fixture)
    changed["workers"][0]["records"][1]["audit"]["status"] = "user_limit"

    assert "tracked_fixture_digest" in validate_normalized_fixture(changed)


def test_s1_ci_only_gate_does_not_claim_source_reconstruction(tmp_path, monkeypatch):
    monkeypatch.setattr(
        p0_s1_boundary_gate, "ARTIFACT_PATH", tmp_path / "absent.json.gz"
    )

    report = run_s1_boundary_gate()

    assert report.passed
    assert report.tracked_fixture_digest_verified
    assert not report.source_artifact_available
    assert not report.source_integrity_verified
    assert not report.source_rederived
    assert report.reconstructed_audit_count == 0
