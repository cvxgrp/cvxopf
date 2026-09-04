from __future__ import annotations

from copy import deepcopy
import json

from experiments.case118_annual_hierarchy import p0_s3b_gate
from experiments.case118_annual_hierarchy.p0_s3b_gate import (
    FIXTURE_PATH,
    run_s3b_normalization_gate,
    validate_normalized_fixture,
)


def test_authoritative_s3b_copied_recovery_normalizes_without_loss():
    report = run_s3b_normalization_gate()

    assert report.passed, report.failures
    assert report.tracked_fixture_digest_verified
    assert report.recovery_iteration == 80
    assert report.slot_count == 9
    assert report.executed_ordinals == (0, 1, 2)
    assert report.controlling_ordinal == 2
    if report.source_artifact_available:
        assert report.source_integrity_verified
        assert report.derived_fixture_verified
    else:
        assert not report.source_integrity_verified
        assert not report.derived_fixture_verified


def test_tracked_s3b_derivative_digest_rejects_material_drift():
    fixture = json.loads(FIXTURE_PATH.read_text())
    changed = deepcopy(fixture)
    changed["attempts"][2]["target_soc_mwh"]["battery_bus_7"] += 1.0

    failures = validate_normalized_fixture(changed)

    assert "tracked_fixture_digest" in failures


def test_ci_only_gate_does_not_claim_source_rederivation(tmp_path, monkeypatch):
    monkeypatch.setattr(p0_s3b_gate, "ARTIFACT_PATH", tmp_path / "absent.json.gz")

    report = run_s3b_normalization_gate()

    assert report.passed
    assert report.tracked_fixture_digest_verified
    assert not report.source_artifact_available
    assert not report.source_integrity_verified
    assert not report.derived_fixture_verified
