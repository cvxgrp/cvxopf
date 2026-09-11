"""One-time S5 interval-2448 intervention tests; no numerical execution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.case118_annual_hierarchy import (
    s5_operator_intervention as intervention,
)
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.streaming_schema import WindowIndexEntry


def _write(path: Path, value: object) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = value if isinstance(value, bytes) else json.dumps(value).encode()
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    output = tmp_path / "annual"
    diagnostic = tmp_path / "diagnostic"
    output.mkdir()
    diagnostic.mkdir()
    old_context = {
        "git_commit": "a" * 40,
        "git_clean": True,
        "source_fingerprint": "b" * 64,
        "platform": "test",
    }
    new_context = {
        **old_context,
        "git_commit": "c" * 40,
        "source_fingerprint": "d" * 64,
    }
    old_authority = {
        "execution_commit": old_context["git_commit"],
        "source_fingerprint": old_context["source_fingerprint"],
        "annual_registry_sha256": "e" * 64,
        "source_version_contract_sha256": "f" * 64,
    }
    predecessor = {
        "contract": {"continuation_execution": {"context": old_context}},
        "new_authority": old_authority,
    }
    old_entry = WindowIndexEntry(2447, "window-old.json.gz", 10, "1" * 64)
    old_checkpoint = {
        "windows": [old_entry.__dict__],
        "completed_intervals": 1,
        "next_global_iteration": 2448,
        "outer_plan_sha256": "2" * 64,
        "execution_registry_sha256": old_authority["annual_registry_sha256"],
    }
    stopping: dict[str, str] = {}
    for name, value in {
        "progress.json": {"classification": "supervisor_interrupted"},
        "root-outcome-001.json": {"classification": "supervisor_interrupted"},
        "supervision-wave-001-000.json": {"classification": "supervisor_interrupted"},
        "shard-000/checkpoint.json": {"shard": 0},
        "shard-001/checkpoint.json": {"shard": 1},
        "shard-002/checkpoint.json": {"shard": 2},
        "shard-003/checkpoint.json": old_checkpoint,
        "shard-003/window-phase-002448-primary.json": {"events": []},
        "shard-003/window-phase-002448-recovery.json": {"events": []},
        "shard-003/window-supervision-002448-timeout.json": {
            "classification": "timeout"
        },
    }.items():
        stopping[name] = _write(output / name, value)
    diagnostic_evidence = {
        "causal8/result.json": _write(
            diagnostic / "causal8/result.json", {"accepted": True}
        ),
        "diagnostic-result.json": _write(
            diagnostic / "diagnostic-result.json", {"complete": True}
        ),
    }
    predecessor_bytes = json.dumps(predecessor, sort_keys=True).encode()
    predecessor_sha = _write(
        output / "source-version-transition.json", predecessor_bytes
    )
    monkeypatch.setattr(intervention, "PRIOR_COMMIT", old_context["git_commit"])
    monkeypatch.setattr(
        intervention, "PRIOR_SOURCE_FINGERPRINT", old_context["source_fingerprint"]
    )
    monkeypatch.setattr(intervention, "PREDECESSOR_TRANSITION_SHA256", predecessor_sha)
    monkeypatch.setattr(intervention, "STOPPING_EVIDENCE", stopping)
    monkeypatch.setattr(intervention, "DIAGNOSTIC_EVIDENCE", diagnostic_evidence)
    payload = {
        "iteration": intervention.INTERVAL,
        "post_step_soc_mwh": [1.0, 2.0, 3.0, 4.0],
        "operator_intervention": {"bound": True},
    }
    monkeypatch.setattr(
        intervention,
        "build_window_payload",
        lambda _value, *, contract_sha256: payload,
    )
    monkeypatch.setattr(intervention, "validate_window_archive", lambda *a, **k: None)
    monkeypatch.setattr(intervention, "validate_shard_checkpoint", lambda *a, **k: None)
    monkeypatch.setattr(intervention, "verify_shard_artifacts", lambda *a, **k: None)
    monkeypatch.setattr(intervention, "_load_outer", lambda: object())
    monkeypatch.setattr(intervention, "outer_boundaries", lambda _outer: {})
    monkeypatch.setattr(
        intervention,
        "load_s4_fixture",
        lambda: SimpleNamespace(
            policy=SimpleNamespace(
                tolerances=SimpleNamespace(soc_recurrence_mwh_abs=1e-6),
                inner_terminal_policy="hard_equality",
                ac_window_steps=3,
            ),
            inputs=SimpleNamespace(delta=1.0),
        ),
    )
    monkeypatch.setattr(
        intervention,
        "shard_entry",
        lambda _shard: ({}, {"interval": {"start": 2213, "stop": 2965}}),
    )
    monkeypatch.setattr(intervention, "result_dimensions", lambda _inputs: {})
    monkeypatch.setattr(intervention, "residual_tolerances", lambda _policy: {})

    def checkpoint_payload(**kwargs):
        windows = [item.__dict__ for item in kwargs["windows"]]
        return {
            **old_checkpoint,
            "windows": windows,
            "completed_intervals": 2,
            "next_global_iteration": 2449,
            "preceding_controlling_attempt_id": intervention.attempt_id(2448, 8),
            "realized_soc_mwh": payload["post_step_soc_mwh"],
            "execution_source_fingerprint": new_context["source_fingerprint"],
        }

    monkeypatch.setattr(intervention, "shard_checkpoint_payload", checkpoint_payload)
    contract = {
        "schema_version": 1,
        "classification": "reviewed_s5_interval_2448_operator_intervention",
        "review_status": "reviewed",
        "launch_authorized": True,
        "general_recovery_policy_changed": False,
        "interval": 2448,
        "interval_stop": 2451,
        "shard_id": "s4b-shard-003",
        "selected_ordinal": 8,
        "selected_scale": 0.01,
        "selected_seed": 17244823,
        "selected_attempt_id": intervention.attempt_id(2448, 8),
        "preceding_attempt_id": intervention.attempt_id(2447, 0),
        "stopping_evidence": stopping,
        "diagnostic_evidence": diagnostic_evidence,
        "predecessor_transition_sha256": predecessor_sha,
        "prior_execution_context": old_context,
        "prior_numerical_authority": old_authority,
        "continuation_execution": {
            "context": new_context,
            "required_clean_execution_commit": new_context["git_commit"],
            "required_execution_source_fingerprint": new_context["source_fingerprint"],
            "commit_must_match_exactly": True,
            "descendant_commits_implicitly_allowed": False,
        },
    }
    contract_path = tmp_path / "contract.json"
    contract_path.write_text(json.dumps(contract))
    authority = {
        **old_authority,
        "execution_commit": new_context["git_commit"],
        "source_fingerprint": new_context["source_fingerprint"],
        "source_version_contract_sha256": object_sha256(contract),
    }
    return (
        output,
        diagnostic,
        contract_path,
        contract,
        predecessor,
        new_context,
        authority,
        old_checkpoint,
    )


def test_intervention_publishes_archive_then_advances_exactly_once(prepared):
    output, diagnostic, path, contract, predecessor, context, authority, old = prepared
    record = intervention.publish_intervention(
        output,
        path,
        diagnostic,
        context,
        authority,
        predecessor_transition=predecessor,
    )
    checkpoint = json.loads((output / "shard-003/checkpoint.json").read_text())
    assert checkpoint["windows"][:-1] == old["windows"]
    assert checkpoint["next_global_iteration"] == 2449
    assert checkpoint["execution_source_fingerprint"] == context["source_fingerprint"]
    assert record["contract"] == contract
    assert (
        intervention.publish_intervention(
            output,
            path,
            diagnostic,
            context,
            authority,
            predecessor_transition=predecessor,
        )
        == record
    )
    assert (
        len(json.loads((output / "shard-003/checkpoint.json").read_text())["windows"])
        == 2
    )


def test_intervention_retry_finishes_checkpoint_after_record_publication(
    prepared, monkeypatch: pytest.MonkeyPatch
):
    output, diagnostic, path, _contract, predecessor, context, authority, old = prepared
    real_write = intervention.write_shard_checkpoint
    monkeypatch.setattr(
        intervention,
        "write_shard_checkpoint",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("checkpoint failed")),
    )
    with pytest.raises(OSError, match="checkpoint failed"):
        intervention.publish_intervention(
            output,
            path,
            diagnostic,
            context,
            authority,
            predecessor_transition=predecessor,
        )
    assert (output / intervention.RECORD_NAME).is_file()
    assert json.loads((output / "shard-003/checkpoint.json").read_text()) == old
    monkeypatch.setattr(intervention, "write_shard_checkpoint", real_write)
    intervention.publish_intervention(
        output,
        path,
        diagnostic,
        context,
        authority,
        predecessor_transition=predecessor,
    )
    assert (
        json.loads((output / "shard-003/checkpoint.json").read_text())[
            "next_global_iteration"
        ]
        == 2449
    )


def test_intervention_rejects_corrupt_diagnostic_before_checkpoint(prepared):
    output, diagnostic, path, _contract, predecessor, context, authority, old = prepared
    (diagnostic / "causal8/result.json").write_text("{}")
    with pytest.raises(ValueError, match="diagnostic evidence"):
        intervention.publish_intervention(
            output,
            path,
            diagnostic,
            context,
            authority,
            predecessor_transition=predecessor,
        )
    assert json.loads((output / "shard-003/checkpoint.json").read_text()) == old
    assert not (output / intervention.RECORD_NAME).exists()


def test_intervention_accepts_forward_extension_without_rollback(prepared):
    output, diagnostic, path, _contract, predecessor, context, authority, _old = (
        prepared
    )
    record = intervention.publish_intervention(
        output,
        path,
        diagnostic,
        context,
        authority,
        predecessor_transition=predecessor,
    )
    checkpoint_path = output / "shard-003/checkpoint.json"
    extended = json.loads(checkpoint_path.read_text())
    extended["windows"].append(
        WindowIndexEntry(2449, "window-next.json.gz", 11, "9" * 64).__dict__
    )
    extended.update(
        {
            "completed_intervals": 3,
            "next_global_iteration": 2450,
            "preceding_controlling_attempt_id": "ac-2449-00-primary_controlling",
            "realized_soc_mwh": [4.0, 3.0, 2.0, 1.0],
        }
    )
    checkpoint_path.write_text(
        json.dumps(extended, sort_keys=True, separators=(",", ":"))
    )

    assert (
        intervention.load_record(output, predecessor_transition=predecessor) == record
    )
    assert (
        intervention.publish_intervention(
            output,
            path,
            diagnostic,
            context,
            authority,
            predecessor_transition=predecessor,
        )
        == record
    )
    assert json.loads(checkpoint_path.read_text()) == extended


def test_intervention_rejects_extension_without_exact_post_prefix(prepared):
    output, diagnostic, path, _contract, predecessor, context, authority, _old = (
        prepared
    )
    intervention.publish_intervention(
        output,
        path,
        diagnostic,
        context,
        authority,
        predecessor_transition=predecessor,
    )
    checkpoint_path = output / "shard-003/checkpoint.json"
    corrupted = json.loads(checkpoint_path.read_text())
    corrupted["windows"][0]["sha256"] = "8" * 64
    corrupted["windows"].append(
        WindowIndexEntry(2449, "window-next.json.gz", 11, "9" * 64).__dict__
    )
    corrupted.update({"completed_intervals": 3, "next_global_iteration": 2450})
    checkpoint_path.write_text(
        json.dumps(corrupted, sort_keys=True, separators=(",", ":"))
    )

    with pytest.raises(ValueError, match="validated post-intervention extension"):
        intervention.load_record(output, predecessor_transition=predecessor)
