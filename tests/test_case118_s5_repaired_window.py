"""Restartability checks for the reviewed interval-6122 repair transaction."""

import json
from types import SimpleNamespace

import pytest

from experiments.case118_annual_hierarchy import s5_repaired_window as repaired
from experiments.case118_annual_hierarchy.streaming_schema import atomic_json
from experiments.case118_annual_hierarchy.streaming_schema import sha256_path


def test_published_repair_retries_checkpoint_last_transition(tmp_path, monkeypatch):
    before = {"next_global_iteration": 6122, "windows": []}
    post = {"next_global_iteration": 6123, "windows": [{"iteration": 6122}]}
    record = {
        "prior_checkpoint_json": json.dumps(before, sort_keys=True),
        "post_checkpoint": post,
    }
    checkpoint = tmp_path / "shard-008/checkpoint.json"
    atomic_json(checkpoint, before)
    real_write = repaired.write_shard_checkpoint
    calls = 0

    def fail_once(path, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("injected checkpoint publication failure")
        real_write(path, payload)

    monkeypatch.setattr(repaired, "write_shard_checkpoint", fail_once)
    with pytest.raises(OSError, match="injected"):
        repaired._advance_published_record(record, tmp_path)
    assert json.loads(checkpoint.read_text()) == before
    repaired._advance_published_record(record, tmp_path)
    assert json.loads(checkpoint.read_text()) == post
    repaired._advance_published_record(record, tmp_path)
    assert json.loads(checkpoint.read_text()) == post


def test_publish_reconciles_record_and_archive_after_checkpoint_failure(
    tmp_path, monkeypatch
):
    output = tmp_path / "output"
    diagnostic = tmp_path / "diagnostic"
    diagnostic.mkdir()
    checkpoint = output / "shard-008/checkpoint.json"
    before = {
        "next_global_iteration": repaired.INTERVAL,
        "windows": [],
        "execution_source_fingerprint": "source",
        "outer_plan_sha256": "outer",
        "execution_registry_sha256": "registry",
    }
    atomic_json(checkpoint, before)
    monkeypatch.setattr(repaired, "BEFORE_CHECKPOINT_SHA256", sha256_path(checkpoint))
    monkeypatch.setattr(repaired, "EVIDENCE", {})
    monkeypatch.setattr(
        repaired,
        "intervention_identity",
        lambda contract: {"contract_sha256": contract},
    )
    monkeypatch.setattr(
        repaired,
        "build_window",
        lambda *args, **kwargs: {
            "iteration": repaired.INTERVAL,
            "post_step_soc_mwh": [1.0],
        },
    )
    monkeypatch.setattr(repaired, "validate_window_archive", lambda *args, **kw: None)
    monkeypatch.setattr(repaired, "residual_tolerances", lambda policy: {})
    monkeypatch.setattr(repaired, "result_dimensions", lambda inputs: {})
    monkeypatch.setattr(repaired, "outer_boundaries", lambda outer: {})
    fixture = SimpleNamespace(
        inputs=SimpleNamespace(delta=1.0),
        policy=SimpleNamespace(
            tolerances=SimpleNamespace(soc_recurrence_mwh_abs=1e-8),
            inner_terminal_policy="hard_equality",
            ac_window_steps=3,
        ),
    )
    monkeypatch.setattr(repaired, "load_s4_fixture", lambda: fixture)
    monkeypatch.setattr(repaired, "_outer", lambda: object())
    monkeypatch.setattr(
        repaired,
        "shard_entry",
        lambda shard_id: (
            8,
            {
                "interval": {"start": 0, "stop": 7000},
                "storage": {"initial_state": {"soc_mwh": [0.0]}},
            },
        ),
    )

    def checkpoint_payload(**kwargs):
        return {
            "next_global_iteration": repaired.INTERVAL + 1,
            "windows": [item.__dict__ for item in kwargs["windows"]],
        }

    monkeypatch.setattr(repaired, "shard_checkpoint_payload", checkpoint_payload)
    real_write = repaired.write_shard_checkpoint
    failed = False

    def fail_once(path, payload):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected checkpoint publication failure")
        real_write(path, payload)

    monkeypatch.setattr(repaired, "write_shard_checkpoint", fail_once)
    with pytest.raises(OSError, match="injected"):
        repaired.publish(output, diagnostic)
    archive = output / "shard-008/window-006122-operator.json.gz"
    record = output / repaired.RECORD_NAME
    assert archive.is_file() and record.is_file()
    archive_sha = sha256_path(archive)
    result = repaired.publish(output, diagnostic)
    assert result["classification"] == "applied_s5_interval_6122_operator_recovery"
    assert sha256_path(archive) == archive_sha
    assert json.loads(checkpoint.read_text())["next_global_iteration"] == 6123
