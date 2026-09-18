"""Reviewed S5 source transition for immutable per-window retry evidence."""

from __future__ import annotations

from datetime import datetime, timezone
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, cast

from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)

SCHEMA_VERSION = 1
CONTRACT_CLASSIFICATION = "reviewed_s5_window_retry_source_continuation"
RECORD_CLASSIFICATION = "applied_s5_window_retry_source_continuation"
RECORD_NAME = "window-retry-source-transition.json"
PREDECESSOR_RECORD_NAME = "operator-intervention-002448.json"
PREDECESSOR_RECORD_SHA256 = (
    "55512372dad547ab0331750cd74badf010fd85b3ec33c2c3e7096c31e4439b3a"
)

STOPPING_EVIDENCE: dict[str, dict[str, object]] = {
    "progress.json": {
        "bytes": 3604,
        "sha256": "b95eb1e8d5e0677d4f4a35a07b73586ef050915c3ddaa494d3163000cee46a4f",
    },
    "supervision-wave-001-001.json": {
        "bytes": 69112,
        "sha256": "ed66194b300362ae354893693a10d6a7b09b20fccc4d57f9389276b59eb07728",
    },
    "root-outcome-002.json": {
        "bytes": 3439,
        "sha256": "9c5bb462aecb9b7f90af526ce44f728a658f0c48e468a4718e07303933bdec1b",
    },
    "shard-000/checkpoint.json": {
        "bytes": 114126,
        "sha256": "0598a454a1f86125dfde5b7f34dedace77149532e53490c2398f2cb91f087736",
    },
    "shard-001/checkpoint.json": {
        "bytes": 129319,
        "sha256": "7a95ab4e207ec6f5d90701675552a27bbee2acfad9f048723c291b02599cd455",
    },
    "shard-002/checkpoint.json": {
        "bytes": 42125,
        "sha256": "ffbc4907db8323efa7c51d115107aac5c41b0a42da63ef1af297ed634951e591",
    },
    "shard-002/termination-001.json": {
        "bytes": 422,
        "sha256": "4b2944ad71c098c4fd62af2ad553ee85a7f9688cce135ffe344a017148553521",
    },
    "shard-002/window-process-001698-primary.log": {
        "bytes": 559,
        "sha256": "383c2a4431f584eb398509ac91508a636f7abea52def51a84dfba07a4e15f155",
    },
    "shard-002/window-phase-001698-primary.json": {
        "bytes": 336,
        "sha256": "6d0f0d57c539ece8c452180996cb34fee6f67902a2bb91118b67014453af1358",
    },
    "shard-002/window-supervision-001698-interrupted.json": {
        "bytes": 647,
        "sha256": "b6cc5ee77c8ec2b484e7e5b34481cc6e3a1b6d928e3274b04d8b3cc0943f8cc1",
    },
    "shard-003/checkpoint.json": {
        "bytes": 40452,
        "sha256": "fa46cb75bc414d5d18d2e2ede3a2bd3f28b8f0b1becc8e411b82c4a2dfb5f038",
    },
    "shard-003/termination-001.json": {
        "bytes": 305,
        "sha256": "9f694aed939d59faafce52c14998a603a16bbce7b37a0b4d7aec52e55241086f",
    },
    "shard-003/window-process-002449-primary.log": {
        "bytes": 559,
        "sha256": "383c2a4431f584eb398509ac91508a636f7abea52def51a84dfba07a4e15f155",
    },
    "shard-003/window-phase-002449-primary.json": {
        "bytes": 336,
        "sha256": "9ec8ab8b99ba0552f77cd95910e7372fdc2bb168c242d2eb27827ae29974d18e",
    },
    "shard-003/window-supervision-002449-interrupted.json": {
        "bytes": 648,
        "sha256": "bedaadaf4859071b4068a5476c0151bfceee6c64c5a3f62bde733be4a7e93c9b",
    },
}

CHECKPOINT_COORDINATES = {
    "shard-000/checkpoint.json": 682,
    "shard-001/checkpoint.json": 1452,
    "shard-002/checkpoint.json": 1698,
    "shard-003/checkpoint.json": 2449,
}

CHANGE_SCOPE = {
    "classification": "operational_retry_lifecycle_only",
    "summary": (
        "Preserve interrupted window-process evidence under attempt zero and assign "
        "deterministic immutable retry-NNN identities to later attempts."
    ),
    "scientific_model_changed": False,
    "scenario_changed": False,
    "hierarchy_policy_changed": False,
    "solver_configuration_changed": False,
    "acceptance_gate_changed": False,
    "checkpoint_or_realized_state_changed": False,
}


@dataclass(frozen=True)
class TransitionSpec:
    """Frozen identity of one permitted operational source correction."""

    contract_classification: str
    record_classification: str
    record_name: str
    predecessor_sha256: str
    evidence: Mapping[str, Mapping[str, object]]
    coordinates: Mapping[str, int]
    change_scope: Mapping[str, object]


def retry_spec() -> TransitionSpec:
    return TransitionSpec(
        CONTRACT_CLASSIFICATION,
        RECORD_CLASSIFICATION,
        RECORD_NAME,
        PREDECESSOR_RECORD_SHA256,
        STOPPING_EVIDENCE,
        CHECKPOINT_COORDINATES,
        CHANGE_SCOPE,
    )


AUDIT_SPEC = TransitionSpec(
    "reviewed_s5_recovery_audit_source_continuation",
    "applied_s5_recovery_audit_source_continuation",
    "recovery-audit-source-transition.json",
    "0c802287865e8036d6894570aa49acb32baf8c38ef89192c2b3389c120121418",
    {
        "progress.json": {
            "bytes": 4047,
            "sha256": "8325c0746d16f8c19ec69f7eaa8f25f360c82aadb19b779733c93dc764f8f478",
        },
        "supervision-wave-001-002.json": {
            "bytes": 58385990,
            "sha256": "cab1cfffd2cfb00edb8ed4a72278b067e45c7810fa218c0c4f84b23ad94618cd",
        },
        "root-outcome-003.json": {
            "bytes": 3897,
            "sha256": "30a9434c1b5cb50b86f81c9512944b0b7ace3e234d2cc07d7d7c92e20ba24e77",
        },
        "shard-000/checkpoint.json": {
            "bytes": 114126,
            "sha256": "0598a454a1f86125dfde5b7f34dedace77149532e53490c2398f2cb91f087736",
        },
        "shard-001/checkpoint.json": {
            "bytes": 129319,
            "sha256": "7a95ab4e207ec6f5d90701675552a27bbee2acfad9f048723c291b02599cd455",
        },
        "shard-002/checkpoint.json": {
            "bytes": 128134,
            "sha256": "f3191b3086526afffd2d73f9520e6b98e9dae0fd9b7f691c354f34468430bcf2",
        },
        "shard-003/checkpoint.json": {
            "bytes": 126622,
            "sha256": "075d92a2c288b9ff596c0986d09f84d48bca5acba09dd2ac8a741a6607163054",
        },
    },
    {
        "shard-000/checkpoint.json": 682,
        "shard-001/checkpoint.json": 1452,
        "shard-002/checkpoint.json": 2213,
        "shard-003/checkpoint.json": 2965,
    },
    {
        "classification": "recovery_audit_and_completed_shard_finalization_only",
        "audit_fix_commit": "89211f12e204645a5de70d9635350f4d41ba22e4",
        "summary": "Validate the archived recovery tree and finalize the completed shard without new solves; retain all original execution evidence.",
        "scientific_model_changed": False,
        "scenario_changed": False,
        "hierarchy_policy_changed": False,
        "solver_configuration_changed": False,
        "primal_acceptance_tolerances_changed": False,
        "checkpoint_or_realized_state_changed": False,
    },
)


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _read_bound(path: Path, expected: Mapping[str, object], label: str) -> bytes:
    raw = path.read_bytes()
    if (
        len(raw) != expected["bytes"]
        or hashlib.sha256(raw).hexdigest() != expected["sha256"]
    ):
        raise ValueError(f"S5 retry continuation {label} mismatch")
    return raw


def validate_contract(
    value: object,
    *,
    predecessor_transition: Mapping[str, object],
    spec: TransitionSpec | None = None,
) -> dict[str, Any]:
    """Validate one reviewed successor binding without changing experiment policy."""
    contract = _mapping(value, "S5 retry continuation contract")
    spec = retry_spec() if spec is None else spec
    fixed = {
        "schema_version": SCHEMA_VERSION,
        "classification": spec.contract_classification,
        "review_status": "reviewed",
        "launch_authorized": True,
        "predecessor_transition_sha256": spec.predecessor_sha256,
        "trusted_stopping_evidence": spec.evidence,
        "checkpoint_coordinates": spec.coordinates,
        "change_scope": spec.change_scope,
    }
    if {key: contract.get(key) for key in fixed} != fixed:
        raise ValueError("S5 retry continuation differs from the reviewed scope")
    if object_sha256(predecessor_transition) != spec.predecessor_sha256:
        raise ValueError("S5 retry continuation predecessor mismatch")
    target = _mapping(contract.get("continuation_execution"), "continuation execution")
    context = _mapping(target.get("context"), "continuation context")
    prior_context = _mapping(
        _mapping(predecessor_transition["contract"], "predecessor contract")[
            "continuation_execution"
        ]["context"],
        "predecessor context",
    )
    if contract.get("prior_execution_context") != prior_context:
        raise ValueError("S5 retry continuation prior context mismatch")
    identity = {"git_commit", "source_fingerprint"}
    if (
        {key: val for key, val in context.items() if key not in identity}
        != {key: val for key, val in prior_context.items() if key not in identity}
        or target.get("required_clean_execution_commit") != context.get("git_commit")
        or target.get("required_execution_source_fingerprint")
        != context.get("source_fingerprint")
        or target.get("commit_must_match_exactly") is not True
        or target.get("descendant_commits_implicitly_allowed") is not False
    ):
        raise ValueError("S5 retry continuation execution context mismatch")
    for field, length in (("git_commit", 40), ("source_fingerprint", 64)):
        item = context.get(field)
        if (
            not isinstance(item, str)
            or len(item) != length
            or any(char not in "0123456789abcdef" for char in item)
        ):
            raise ValueError("S5 retry continuation needs an exact execution identity")
    return contract


def validate_record(
    value: object,
    output_root: Path,
    *,
    predecessor_transition: Mapping[str, object],
    spec: TransitionSpec | None = None,
) -> dict[str, Any]:
    """Verify the immutable stopped state and its reviewed successor authority."""
    record = _mapping(value, "S5 retry continuation record")
    spec = retry_spec() if spec is None else spec
    contract = validate_contract(
        record.get("contract"), predecessor_transition=predecessor_transition, spec=spec
    )
    if (
        record.get("schema_version") != SCHEMA_VERSION
        or record.get("classification") != spec.record_classification
        or record.get("contract_sha256") != object_sha256(contract)
        or record.get("predecessor_transition") != predecessor_transition
        or record.get("predecessor_transition_sha256") != spec.predecessor_sha256
    ):
        raise ValueError("S5 retry continuation record identity mismatch")
    published = record.get("published_utc")
    if (
        not isinstance(published, str)
        or datetime.fromisoformat(published).utcoffset() is None
    ):
        raise ValueError("S5 retry continuation lacks its publication timestamp")
    snapshots = _mapping(record.get("stopping_pointer_json"), "stopping snapshots")
    expected_snapshot_names = {"progress.json", *spec.coordinates}
    if set(snapshots) != expected_snapshot_names:
        raise ValueError("S5 retry continuation snapshot registry mismatch")
    for name in snapshots:
        expected = spec.evidence[name]
        raw = snapshots[name]
        if not isinstance(raw, str):
            raise ValueError("S5 retry continuation snapshots must retain JSON text")
        encoded = raw.encode()
        if (
            len(encoded) != expected["bytes"]
            or hashlib.sha256(encoded).hexdigest() != expected["sha256"]
        ):
            raise ValueError("S5 retry continuation snapshot mismatch")
        if name in spec.coordinates:
            checkpoint = _mapping(json.loads(raw), "stopping checkpoint")
            if checkpoint.get("next_global_iteration") != spec.coordinates[name]:
                raise ValueError("S5 retry continuation stopping coordinate mismatch")
    for name, expected in spec.evidence.items():
        if name not in expected_snapshot_names:
            _read_bound(output_root / name, expected, name)
    prior_authority = _mapping(
        predecessor_transition["new_authority"], "predecessor authority"
    )
    context = contract["continuation_execution"]["context"]
    expected_authority = {
        **prior_authority,
        "execution_commit": context["git_commit"],
        "source_fingerprint": context["source_fingerprint"],
        "source_version_contract_sha256": object_sha256(contract),
    }
    if (
        record.get("prior_authority") != prior_authority
        or record.get("new_authority") != expected_authority
    ):
        raise ValueError("S5 retry continuation authority mismatch")
    return record


def load_record(
    output_root: Path,
    *,
    predecessor_transition: Mapping[str, object],
    spec: TransitionSpec | None = None,
) -> dict[str, Any] | None:
    spec = retry_spec() if spec is None else spec
    path = output_root / spec.record_name
    return (
        validate_record(
            json.loads(path.read_text()),
            output_root,
            predecessor_transition=predecessor_transition,
            spec=spec,
        )
        if path.is_file()
        else None
    )


def publish_transition(
    output_root: Path,
    contract_path: Path,
    context: Mapping[str, object],
    authority: Mapping[str, object],
    *,
    predecessor_transition: Mapping[str, object],
    spec: TransitionSpec | None = None,
) -> dict[str, Any]:
    """Publish the reviewed source successor after exact stopped-state validation."""
    spec = retry_spec() if spec is None else spec
    contract = validate_contract(
        json.loads(contract_path.read_text()),
        predecessor_transition=predecessor_transition,
        spec=spec,
    )
    if contract["continuation_execution"]["context"] != context:
        raise ValueError("S5 retry continuation does not bind this clean execution")
    existing = load_record(
        output_root, predecessor_transition=predecessor_transition, spec=spec
    )
    if existing is not None:
        if existing["contract"] != contract or existing["new_authority"] != authority:
            raise ValueError("existing S5 retry continuation differs")
        return existing
    for name, expected in spec.evidence.items():
        _read_bound(output_root / name, expected, name)
    snapshots = {
        name: (output_root / name).read_text()
        for name in ("progress.json", *spec.coordinates)
    }
    record = {
        "schema_version": SCHEMA_VERSION,
        "classification": spec.record_classification,
        "published_utc": datetime.now(timezone.utc).isoformat(),
        "contract": contract,
        "contract_sha256": object_sha256(contract),
        "predecessor_transition": predecessor_transition,
        "predecessor_transition_sha256": spec.predecessor_sha256,
        "prior_authority": predecessor_transition["new_authority"],
        "new_authority": dict(authority),
        "stopping_pointer_json": snapshots,
    }
    validate_record(
        record, output_root, predecessor_transition=predecessor_transition, spec=spec
    )
    path = output_root / spec.record_name
    atomic_immutable_json(path, record)
    if sha256_path(path) != object_sha256(record):
        raise RuntimeError("S5 retry continuation publication changed bytes")
    return record


__all__ = [
    "CONTRACT_CLASSIFICATION",
    "RECORD_NAME",
    "load_record",
    "publish_transition",
    "validate_contract",
    "validate_record",
]
