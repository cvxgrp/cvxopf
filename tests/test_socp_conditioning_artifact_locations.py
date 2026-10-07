"""Portable path and integrity checks, without retained research artifacts."""

import hashlib
import json

import pytest

from experiments.socp_conditioning.artifact_locations import resolve_artifact


@pytest.fixture
def preserved(tmp_path):
    current = tmp_path / "results"
    current.mkdir()
    artifact = current / "arm.json"
    artifact.write_bytes(b'{"status":"historical"}\n')
    inventory = tmp_path / "inventory.json"
    inventory.write_text(
        json.dumps(
            {
                "entries": [
                    {
                        "root": "conditioning",
                        "path": "arm.json",
                        "bytes": artifact.stat().st_size,
                        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    }
                ]
            }
        )
    )
    locations = tmp_path / "locations.json"
    locations.write_text(
        json.dumps(
            {
                "repository_root": ".",
                "inventory": {
                    "path": "inventory.json",
                    "sha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
                },
                "roots": {
                    "conditioning": {
                        "original": "/historical/results",
                        "current": "results",
                    }
                },
            }
        )
    )
    return locations, artifact, inventory


@pytest.mark.parametrize(
    "reference",
    [
        "/historical/results/arm.json",
        "results/arm.json",
    ],
)
def test_old_and_current_paths_resolve_verified_bytes(preserved, reference):
    locations, artifact, _ = preserved
    assert resolve_artifact(reference, locations=locations) == artifact


def test_current_absolute_path(preserved):
    locations, artifact, _ = preserved
    assert resolve_artifact(artifact, locations=locations) == artifact


def test_reject_changed_artifact(preserved):
    locations, artifact, _ = preserved
    artifact.write_bytes(b"changed")
    with pytest.raises(ValueError, match="size or hash"):
        resolve_artifact("results/arm.json", locations=locations)


def test_reject_changed_inventory(preserved):
    locations, _, inventory = preserved
    inventory.write_text("{}")
    with pytest.raises(ValueError, match="inventory hash"):
        resolve_artifact("results/arm.json", locations=locations)


@pytest.mark.parametrize(
    "reference, message",
    [
        ("/historical/results/missing.json", "not in"),
        ("/elsewhere/arm.json", "outside"),
        ("results/../arm.json", "traverse"),
    ],
)
def test_reject_uninventoried_or_escaping_path(preserved, reference, message):
    locations, _, _ = preserved
    with pytest.raises(ValueError, match=message):
        resolve_artifact(reference, locations=locations)


def test_missing_retained_file_is_not_silently_replayed(preserved):
    locations, artifact, _ = preserved
    artifact.unlink()
    with pytest.raises(FileNotFoundError):
        resolve_artifact("results/arm.json", locations=locations)
