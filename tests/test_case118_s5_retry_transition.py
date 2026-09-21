"""Source-binding tests for the S5 immutable-window retry correction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from experiments.case118_annual_hierarchy import s5_retry_transition as retry
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy import s5_source_transition as chain


def _write(root: Path, name: str, value: object) -> dict[str, object]:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))
    raw = path.read_bytes()
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


@pytest.fixture(params=("retry", "audit"))
def reviewed_transition(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request):
    if request.param == "audit":
        monkeypatch.setattr(
            retry, "CONTRACT_CLASSIFICATION", retry.AUDIT_SPEC.contract_classification
        )
        monkeypatch.setattr(
            retry, "RECORD_CLASSIFICATION", retry.AUDIT_SPEC.record_classification
        )
        monkeypatch.setattr(retry, "RECORD_NAME", retry.AUDIT_SPEC.record_name)
        monkeypatch.setattr(retry, "CHANGE_SCOPE", retry.AUDIT_SPEC.change_scope)
    old_context = {
        "git_commit": "a" * 40,
        "source_fingerprint": "b" * 64,
        "git_clean": True,
        "platform": "test-platform",
    }
    new_context = {
        **old_context,
        "git_commit": "c" * 40,
        "source_fingerprint": "d" * 64,
    }
    old_authority = {
        "classification": "reviewed_s5_numerical_execution_authorized",
        "execution_commit": old_context["git_commit"],
        "source_fingerprint": old_context["source_fingerprint"],
        "source_version_contract_sha256": "e" * 64,
    }
    predecessor = {
        "schema_version": 1,
        "classification": "applied_s5_interval_2448_operator_intervention",
        "contract": {"continuation_execution": {"context": old_context}},
        "new_authority": old_authority,
    }
    predecessor_sha = object_sha256(predecessor)
    monkeypatch.setattr(retry, "PREDECESSOR_RECORD_SHA256", predecessor_sha)
    evidence = {
        "progress.json": _write(
            tmp_path,
            "progress.json",
            {
                "state": "stopped",
                "execution_context": old_context,
                "authority": old_authority,
            },
        ),
        "shard-000/checkpoint.json": _write(
            tmp_path,
            "shard-000/checkpoint.json",
            {
                "next_global_iteration": 4,
                "completed_intervals": 1,
                "windows": [{"iteration": 3}],
            },
        ),
        "supervision.json": _write(
            tmp_path, "supervision.json", {"classification": "interrupted"}
        ),
    }
    monkeypatch.setattr(retry, "STOPPING_EVIDENCE", evidence)
    monkeypatch.setattr(
        retry, "CHECKPOINT_COORDINATES", {"shard-000/checkpoint.json": 4}
    )
    contract = {
        "schema_version": 1,
        "classification": retry.CONTRACT_CLASSIFICATION,
        "review_status": "reviewed",
        "launch_authorized": True,
        "predecessor_transition_sha256": predecessor_sha,
        "trusted_stopping_evidence": evidence,
        "checkpoint_coordinates": {"shard-000/checkpoint.json": 4},
        "change_scope": retry.CHANGE_SCOPE,
        "prior_execution_context": old_context,
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
    return tmp_path, predecessor, contract_path, new_context, authority


def test_completed_checkpoint_finalization_retains_original_source(
    reviewed_transition, monkeypatch
):
    root, predecessor, contract_path, context, authority = reviewed_transition
    checkpoint_path = root / "shard-000/checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint.update(complete=True, execution_source_fingerprint="b" * 64)
    evidence = dict(retry.STOPPING_EVIDENCE)
    evidence["shard-000/checkpoint.json"] = _write(
        root, "shard-000/checkpoint.json", checkpoint
    )
    monkeypatch.setattr(retry, "STOPPING_EVIDENCE", evidence)
    contract = json.loads(contract_path.read_text())
    contract["trusted_stopping_evidence"] = evidence
    contract_path.write_text(json.dumps(contract))
    authority = {**authority, "source_version_contract_sha256": object_sha256(contract)}
    record = retry.publish_transition(
        root, contract_path, context, authority, predecessor_transition=predecessor
    )
    # Exercise this path only under the separately identified audit successor.
    record["classification"] = retry.AUDIT_SPEC.record_classification
    binding = chain.completed_shard_finalization_binding(
        root / "shard-000", checkpoint, context, record
    )
    worker = {
        "execution_source_fingerprint": "b" * 64,
        "completed_checkpoint_finalization": binding,
    }
    assert chain.worker_source_matches(root / "shard-000", worker, context, record)
    assert binding["new_intervals_executed"] == 0
    later = {
        "classification": "applied_s5_speculative_policy_continuation",
        "contract_sha256": "9" * 64,
        "contract": {
            "continuation_execution": {
                "context": {**context, "source_fingerprint": "9" * 64}
            }
        },
        "predecessor_transition": record,
    }
    # The same historical audit-only worker remains valid across successive
    # continuations, without rewriting its identity under the latest policy.
    assert chain.worker_source_matches(root / "shard-000", worker, context, later)
    another = {"contract_sha256": "8" * 64, "predecessor_transition": later}
    assert chain.worker_source_matches(root / "shard-000", worker, context, another)
    with pytest.raises(ValueError, match="audit-only finalization"):
        chain.worker_source_matches(
            root / "shard-000",
            worker,
            {**context, "source_fingerprint": "9" * 64},
            later,
        )
    with pytest.raises(ValueError, match="audit-only finalization"):
        chain.worker_source_matches(
            root / "shard-000",
            worker,
            context,
            {**later, "predecessor_transition": None},
        )
    assert not chain.worker_source_matches(
        root / "shard-000",
        {**worker, "execution_source_fingerprint": "f" * 64},
        context,
        record,
    )
    with pytest.raises(ValueError, match="audit-only finalization"):
        chain.completed_shard_finalization_binding(
            root / "shard-000", {**checkpoint, "complete": False}, context, record
        )
    with pytest.raises(ValueError, match="audit-only finalization"):
        chain.completed_shard_finalization_binding(
            root / "shard-000", checkpoint, {**context, "git_commit": "f" * 40}, record
        )
    checkpoint["windows"] = [{"iteration": 99}]
    checkpoint_path.write_text(json.dumps(checkpoint))
    with pytest.raises(ValueError, match="audit-only finalization"):
        chain.worker_source_matches(root / "shard-000", worker, context, record)
    with pytest.raises(ValueError, match="audit-only finalization"):
        chain.worker_source_matches(root / "shard-000", worker, context, later)


def test_retry_transition_publishes_and_survives_pointer_advancement(
    reviewed_transition,
) -> None:
    root, predecessor, contract_path, context, authority = reviewed_transition
    record = retry.publish_transition(
        root,
        contract_path,
        context,
        authority,
        predecessor_transition=predecessor,
    )
    assert record["classification"] == retry.RECORD_CLASSIFICATION
    assert retry.load_record(root, predecessor_transition=predecessor) == record

    # Mutable pointers may advance; their exact stopping bytes remain in the record.
    (root / "progress.json").write_text('{"state":"running"}')
    (root / "shard-000/checkpoint.json").write_text('{"next":5}')
    assert retry.load_record(root, predecessor_transition=predecessor) == record


def test_audit_successor_dispatch_and_idempotent_load(reviewed_transition, monkeypatch):
    root, predecessor, contract_path, context, authority = reviewed_transition
    spec = retry.retry_spec()
    monkeypatch.setattr(retry, "AUDIT_SPEC", spec)
    monkeypatch.setattr(chain, "load_base_transition", lambda _root: predecessor)
    from experiments.case118_annual_hierarchy import (
        s5_operator_intervention as intervention,
    )

    monkeypatch.setattr(intervention, "load_record", lambda *args, **kwargs: None)
    original_load = retry.load_record

    def load(root, *, predecessor_transition, spec=None):
        if spec is None:
            return predecessor
        return original_load(
            root, predecessor_transition=predecessor_transition, spec=spec
        )

    monkeypatch.setattr(retry, "load_record", load)
    record = chain.publish_transition(root, contract_path, context, authority)
    assert chain.load_transition(root) == record
    assert chain.publish_transition(root, contract_path, context, authority) == record

    # Mutable pointers may advance; their exact stopping bytes remain in the record.
    (root / "progress.json").write_text('{"state":"running"}')
    (root / "shard-000/checkpoint.json").write_text('{"next":5}')
    assert chain.load_transition(root) == record


def test_retry_transition_rejects_static_evidence_corruption(
    reviewed_transition,
) -> None:
    root, predecessor, contract_path, context, authority = reviewed_transition
    retry.publish_transition(
        root,
        contract_path,
        context,
        authority,
        predecessor_transition=predecessor,
    )
    (root / "supervision.json").write_text("corrupt")
    with pytest.raises(ValueError, match="supervision.json mismatch"):
        retry.load_record(root, predecessor_transition=predecessor)


def test_retry_transition_rejects_authority_or_scope_drift(
    reviewed_transition,
) -> None:
    root, predecessor, contract_path, context, authority = reviewed_transition
    wrong = {**authority, "execution_commit": "f" * 40}
    with pytest.raises(ValueError, match="authority mismatch"):
        retry.publish_transition(
            root,
            contract_path,
            context,
            wrong,
            predecessor_transition=predecessor,
        )

    changed = json.loads(contract_path.read_text())
    changed["change_scope"] = {**retry.CHANGE_SCOPE, "scientific_model_changed": True}
    contract_path.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="reviewed scope"):
        retry.validate_contract(changed, predecessor_transition=predecessor)


def test_retry_chain_accepts_old_evidence_and_new_checkpoint_prefix(
    reviewed_transition,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, predecessor, contract_path, context, authority = reviewed_transition
    record = retry.publish_transition(
        root,
        contract_path,
        context,
        authority,
        predecessor_transition=predecessor,
    )
    old_context = record["contract"]["prior_execution_context"]
    # Use the actual predecessor context path, not the original base contract shape.
    progress = json.loads(record["stopping_pointer_json"]["progress.json"])
    assert chain.historical_provenance_matches(
        progress,
        record,
        context,
        authority,
        output_root=root,
    )
    monkeypatch.setattr(chain, "load_transition", lambda _root: record)
    checkpoint = json.loads(
        record["stopping_pointer_json"]["shard-000/checkpoint.json"]
    )
    assert chain.verify_checkpoint_segment(
        root / "shard-000", checkpoint, context=context
    )
    extended = {
        **checkpoint,
        "completed_intervals": 2,
        "next_global_iteration": 5,
        "windows": [*checkpoint["windows"], {"iteration": 4}],
        "execution_source_fingerprint": context["source_fingerprint"],
    }
    assert chain.verify_checkpoint_segment(
        root / "shard-000", extended, context=context
    )
    with pytest.raises(ValueError, match="trusted window prefix"):
        chain.verify_checkpoint_segment(
            root / "shard-000",
            {**extended, "windows": [{"iteration": 0}]},
            context=context,
        )
    with pytest.raises(ValueError, match="successor source provenance"):
        chain.verify_checkpoint_segment(
            root / "shard-000",
            {
                **extended,
                "execution_source_fingerprint": old_context["source_fingerprint"],
            },
            context=context,
        )
