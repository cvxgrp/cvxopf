"""The completed-prefix cache cannot become execution authority."""

import json

import pytest

from experiments.case118_annual_hierarchy import s5_prefix_anchor as anchor
from experiments.case118_annual_hierarchy.s4b_manifest import canonical_json
from experiments.case118_annual_hierarchy.streaming_schema import sha256_path


def test_frozen_anchor_is_canonical_and_nonauthorizing():
    raw = anchor.ANCHOR_PATH.read_bytes()
    value = json.loads(raw)
    assert sha256_path(anchor.ANCHOR_PATH) == anchor.ANCHOR_SHA256
    assert raw == canonical_json(value)
    assert value["execution_authorized"] is False
    assert value["active_shard_semantic_prefix_certified"] is False
    assert [item["shard_id"] for item in value["payload"]["completed_shards"]] == list(
        anchor.CERTIFIED_SHARDS
    )


def test_changed_anchor_bytes_cannot_skip_scientific_audit(tmp_path, monkeypatch):
    changed = tmp_path / "anchor.json"
    changed.write_bytes(anchor.ANCHOR_PATH.read_bytes() + b"\n")
    monkeypatch.setattr(anchor, "ANCHOR_PATH", changed)
    with pytest.raises(ValueError, match="anchor file identity changed"):
        anchor.verified_completed_prefix(tmp_path)
