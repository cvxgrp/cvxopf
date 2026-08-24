from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experiments.case118_annual_hierarchy.reference import extract_s2_reference


def test_tracked_recycle_reference_has_frozen_tier_geometry():
    reference = extract_s2_reference.verify_tracked_reference()

    assert reference["source"] == {
        "final_checkpoint_sha256": (
            "7e34a0ab1f00db2bfb164d1f9d6765231fdce8cd1a0f91f66dbc8230df546685"
        ),
        "historical_source_fingerprint": (
            "b62be721077ee5b5a3c61c93211197abeaf4e1ab5dabc2bc7d76b86b3520f4fd"
        ),
        "outer_plan_sha256": (
            "b4ac7b18b6e913e96991d5bbe217b01462c81be5b55587cbad17b2ca589d0b90"
        ),
        "policy_sha256": (
            "2186334bd2e7be3760636f0b20575c81deaff5f293fb9a725270157379957520"
        ),
        "prefix_intervals": 64,
        "scenario_sha256": (
            "f602d67563d35e62df03cc716f82f0c3ba823813d0719c623b12a727f92ae12b"
        ),
        "solve_config_sha256": (
            "bfb818de03ddbfd983bb02def3aa3c51d0e6c1b075486ec66bca3035d82e2977"
        ),
    }
    assert reference["storage_device_ids"] == [
        "storage_bus_41",
        "storage_bus_65",
        "storage_bus_89",
        "storage_bus_105",
    ]
    tier_a = reference["tier_a"]
    assert [record["iteration"] for record in tier_a] == list(range(64))
    assert [record["source_window"]["iteration"] for record in tier_a] == list(
        range(64)
    )
    tier_b = reference["tier_b"]
    assert [record["boundary"] for record in tier_b] == [0, 16, 32, 48]
    expected_evidence = {
        "assigned_start",
        "causal_source",
        "solver_evidence",
        "solver_x0",
        "solver_x0_layout",
    }
    assert all(set(record["evidence"]) == expected_evidence for record in tier_b)


def test_tier_b_records_bind_the_deduplicated_structural_signature():
    reference = extract_s2_reference.verify_tracked_reference()
    invariants = reference["invariants"]
    structural_hash = invariants["structural_signature_sha256"]

    assert structural_hash == extract_s2_reference._value_sha256(
        invariants["structural_signature"]
    )
    assert all(
        record["structural_signature_sha256"] == structural_hash
        for record in reference["tier_b"]
    )


def test_clean_reference_verification_rejects_sidecar_tampering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reference_path = tmp_path / extract_s2_reference.REFERENCE_PATH.name
    sidecar_path = tmp_path / extract_s2_reference.SIDECAR_PATH.name
    reference_path.write_bytes(extract_s2_reference.REFERENCE_PATH.read_bytes())
    sidecar_path.write_text("0" * 64 + f"  {reference_path.name}\n")
    monkeypatch.setattr(extract_s2_reference, "REFERENCE_PATH", reference_path)
    monkeypatch.setattr(extract_s2_reference, "SIDECAR_PATH", sidecar_path)

    with pytest.raises(ValueError, match="sidecar mismatch"):
        extract_s2_reference.verify_tracked_reference()


def test_clean_reference_verification_rejects_coherent_reference_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reference_path = tmp_path / extract_s2_reference.REFERENCE_PATH.name
    sidecar_path = tmp_path / extract_s2_reference.SIDECAR_PATH.name
    payload = json.loads(extract_s2_reference.REFERENCE_PATH.read_text())
    payload["tier_a"][0]["executed_b_mw"][0] += 1.0
    encoded = extract_s2_reference.canonical_bytes(payload)
    reference_path.write_bytes(encoded)
    digest = hashlib.sha256(encoded).hexdigest()
    sidecar_path.write_text(f"{digest}  {reference_path.name}\n")
    monkeypatch.setattr(extract_s2_reference, "REFERENCE_PATH", reference_path)
    monkeypatch.setattr(extract_s2_reference, "SIDECAR_PATH", sidecar_path)

    with pytest.raises(ValueError, match="frozen SHA-256 mismatch"):
        extract_s2_reference.verify_tracked_reference()


def test_reference_regenerates_from_the_validated_s2_source():
    tracked = json.loads(extract_s2_reference.REFERENCE_PATH.read_text())

    regenerated = extract_s2_reference.build_reference()

    assert extract_s2_reference.canonical_bytes(regenerated) == (
        extract_s2_reference.canonical_bytes(tracked)
    )
