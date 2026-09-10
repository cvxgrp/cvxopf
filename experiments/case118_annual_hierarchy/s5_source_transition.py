"""One reviewed S5 validation-only source transition, not a migration framework.

The immutable transition preserves literal stopping checkpoints and attributes
their window prefixes to the old execution. Later windows use the new context.
No tolerance, physical state, or original artifact is rewritten by publication.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, cast

from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json

TEMPLATE_PATH = (
    ROOT / "experiments/case118_annual_hierarchy/S5_SOURCE_VERSION_CONTINUATION.json"
)
TEMPLATE_SHA256 = "c6362a88041282cc1b477d0ff0c8d8fd9ec9d7bb56d9d7faa1111b4ffeac8b94"
RECORD_NAME = "source-version-transition.json"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("S5 transition requires an object")
    return cast(dict[str, Any], value)


def validate_contract(value: object) -> dict[str, Any]:
    """Permit only the reviewed stopping point and an explicit successor binding."""
    contract = _object(value)
    raw = TEMPLATE_PATH.read_bytes()
    if _digest(raw) != TEMPLATE_SHA256:
        raise ValueError("S5 transition template identity mismatch")
    template = _object(json.loads(raw))
    mutable = {
        "continuation_execution",
        "launch_authorized",
        "review_status",
        "classification",
    }
    if {k: v for k, v in contract.items() if k not in mutable} != {
        k: v for k, v in template.items() if k not in mutable
    }:
        raise ValueError("S5 transition changed the frozen stopping contract")
    if (
        contract.get("launch_authorized") is not True
        or contract.get("review_status") != "reviewed"
        or contract.get("classification") != "reviewed_s5_source_version_continuation"
    ):
        raise ValueError("S5 source transition is not reviewed and authorized")
    target = _object(contract["continuation_execution"])
    context = _object(target.get("context"))
    prior = template["prior_execution"]["context"]
    identity = {"git_commit", "source_fingerprint"}
    if (
        {k: v for k, v in context.items() if k not in identity}
        != {k: v for k, v in prior.items() if k not in identity}
        or target.get("required_clean_execution_commit") != context.get("git_commit")
        or target.get("required_execution_source_fingerprint")
        != context.get("source_fingerprint")
        or target.get("commit_must_match_exactly") is not True
        or target.get("descendant_commits_implicitly_allowed") is not False
    ):
        raise ValueError("S5 transition execution/scientific context mismatch")
    for field, length in (("git_commit", 40), ("source_fingerprint", 64)):
        item = context.get(field)
        if (
            not isinstance(item, str)
            or len(item) != length
            or any(c not in "0123456789abcdef" for c in item)
        ):
            raise ValueError("S5 transition needs an exact execution identity")
    return contract


def _read_ref(ref: Mapping[str, Any], data: bytes | None = None) -> bytes:
    raw = (ROOT / str(ref["path"])).read_bytes() if data is None else data
    if len(raw) != ref["bytes"] or _digest(raw) != ref["sha256"]:
        raise ValueError(f"S5 stopping artifact mismatch: {ref['path']}")
    return raw


def validate_record(value: object, output_root: Path) -> dict[str, Any]:
    """Verify immutable old evidence, including snapshots of advanced pointers."""
    record = _object(value)
    contract = validate_contract(record.get("contract"))
    if record.get("schema_version") != 1 or record.get(
        "contract_sha256"
    ) != object_sha256(contract):
        raise ValueError("S5 transition record identity mismatch")
    published = record.get("published_utc")
    if (
        not isinstance(published, str)
        or datetime.fromisoformat(published).utcoffset() is None
    ):
        raise ValueError("S5 transition lacks its publication timestamp")
    snapshots = _object(record.get("stopping_pointer_json"))
    expected: dict[str, Any] = {}
    for ref in contract["trusted_stopping_point"]["root_evidence"]:
        if Path(ref["path"]).name == "progress.json":
            expected["progress.json"] = ref
        else:
            # Immutable originals stay in place; never manufacture old evidence.
            path = output_root / Path(ref["path"]).name
            _read_ref(ref, path.read_bytes())
    for shard in contract["trusted_stopping_point"]["shards"]:
        name = f"shard-{int(shard['shard_id'][-3:]):03d}/checkpoint.json"
        expected[name] = shard["checkpoint"]
    if set(snapshots) != set(expected):
        raise ValueError("S5 transition stopping snapshot registry mismatch")
    for name, ref in expected.items():
        if not isinstance(snapshots[name], str):
            raise ValueError("S5 stopping snapshot must retain literal JSON bytes")
        _read_ref(ref, snapshots[name].encode())
    old_authority = contract["prior_execution"]["numerical_authority"]
    authority_json = record.get("prior_authority_json")
    if not isinstance(authority_json, str):
        raise ValueError("S5 transition lacks literal prior authority evidence")
    if (
        json.loads(_read_ref(old_authority, authority_json.encode()))
        != old_authority["payload"]
    ):
        raise ValueError("S5 prior authority payload mismatch")
    if record.get("prior_authority") != old_authority["payload"]:
        raise ValueError("S5 transition prior authority mismatch")
    new_authority = _object(record.get("new_authority"))
    new_context = contract["continuation_execution"]["context"]
    expected_authority = {
        **old_authority["payload"],
        "execution_commit": new_context["git_commit"],
        "source_fingerprint": new_context["source_fingerprint"],
        "source_version_contract_sha256": object_sha256(contract),
    }
    if new_authority != expected_authority:
        raise ValueError("S5 transition successor authority mismatch")
    return record


def load_transition(output_root: Path) -> dict[str, Any] | None:
    path = output_root / RECORD_NAME
    return (
        validate_record(json.loads(path.read_text()), output_root)
        if path.is_file()
        else None
    )


def require_current_transition(
    output_root: Path, context: Mapping[str, object], authority: Mapping[str, object]
) -> dict[str, Any] | None:
    """Require the same recorded transition at root, worker, and child entry."""
    record = load_transition(output_root)
    if record is None:
        if authority.get("source_version_contract_sha256") is not None:
            raise ValueError("S5 execution lacks its reviewed source transition")
    elif (
        record["new_authority"] != authority
        or record["contract"]["continuation_execution"]["context"] != context
    ):
        raise ValueError("S5 execution differs from its reviewed source transition")
    return record


def publish_transition(
    output_root: Path,
    contract_path: Path,
    context: Mapping[str, object],
    authority: Mapping[str, object],
) -> dict[str, Any]:
    """Fully validate the old prefix, then publish one immutable transaction."""
    contract = validate_contract(json.loads(contract_path.read_text()))
    if contract["continuation_execution"]["context"] != context:
        raise ValueError("S5 source transition does not bind this clean execution")
    existing = load_transition(output_root)
    if existing is not None:
        if existing["contract"] != contract or existing["new_authority"] != authority:
            raise ValueError("S5 existing transition differs from reviewed request")
        return existing
    refs = contract["trusted_stopping_point"]["root_evidence"]
    for ref in refs:
        _read_ref(ref, (output_root / Path(ref["path"]).name).read_bytes())
    snapshots = {"progress.json": (output_root / "progress.json").read_text()}
    # Lazy imports keep this provenance helper outside model-module cycles.
    from experiments.case118_annual_hierarchy.run_s4b import _outer
    from experiments.case118_annual_hierarchy.s4b_execution import (
        shard_entry,
        verify_shard_artifacts,
    )
    from experiments.case118_annual_hierarchy.s5_execution import annual_registry

    outer = _outer()
    for item in contract["trusted_stopping_point"]["shards"]:
        directory = output_root / f"shard-{int(item['shard_id'][-3:]):03d}"
        raw = _read_ref(
            item["checkpoint"], (directory / "checkpoint.json").read_bytes()
        )
        snapshots[f"{directory.name}/checkpoint.json"] = raw.decode()
        verify_shard_artifacts(
            directory,
            shard=shard_entry(item["shard_id"])[1],
            outer=outer,
            expected_execution_registry_sha256=str(
                annual_registry()["registry_sha256"]
            ),
            allowed_execution_modes=("annual",),
        )
    record = {
        "schema_version": 1,
        "published_utc": datetime.now(timezone.utc).isoformat(),
        "contract": contract,
        "contract_sha256": object_sha256(contract),
        "prior_authority": contract["prior_execution"]["numerical_authority"][
            "payload"
        ],
        "prior_authority_json": _read_ref(
            contract["prior_execution"]["numerical_authority"]
        ).decode(),
        "new_authority": dict(authority),
        "stopping_pointer_json": snapshots,
    }
    validate_record(record, output_root)
    atomic_immutable_json(output_root / RECORD_NAME, record)
    return record


def verify_checkpoint_segment(
    directory: Path,
    checkpoint: Mapping[str, object],
    *,
    context: Mapping[str, object] | None = None,
) -> bool:
    """Retain the exact old prefix, while attributing newly appended windows."""
    record = load_transition(directory.parent)
    if record is None:
        return False
    contract = record["contract"]
    new_context = contract["continuation_execution"]["context"]
    if context is not None and context != new_context:
        raise ValueError("S5 worker context differs from reviewed source transition")
    key = f"{directory.name}/checkpoint.json"
    snapshots = record["stopping_pointer_json"]
    if key in snapshots:
        old = json.loads(snapshots[key])
        count = old["completed_intervals"]
        windows = cast(list[object], checkpoint["windows"])
        if len(windows) < count or windows[:count] != old["windows"]:
            raise ValueError("S5 source transition changed the trusted window prefix")
        if len(windows) == count:
            if checkpoint != old:
                raise ValueError("S5 source transition changed the stopping checkpoint")
            return True
    if checkpoint["execution_source_fingerprint"] != new_context["source_fingerprint"]:
        raise ValueError("S5 appended windows lack successor source provenance")
    return True


def historical_provenance_matches(
    record: Mapping[str, object],
    transition: Mapping[str, Any] | None,
    context: Mapping[str, object],
    authority: Mapping[str, object],
    *,
    output_root: Path,
) -> bool:
    """Allow frozen historical records from the supplied (possibly restored) run."""
    if (
        record.get("execution_context") == context
        and record.get("authority") == authority
    ):
        return True
    if transition is None:
        return False
    contract = transition["contract"]
    if (
        record.get("execution_context") != contract["prior_execution"]["context"]
        or record.get("authority") != transition["prior_authority"]
    ):
        return False
    old_progress = json.loads(transition["stopping_pointer_json"]["progress.json"])
    # Root progress is copied verbatim into the reviewed-source continuation.
    if record == old_progress:
        return True
    for ref in contract["trusted_stopping_point"]["root_evidence"]:
        if Path(ref["path"]).name in {
            "root-outcome-000.json",
            "supervision-wave-000-000.json",
        }:
            if record == json.loads((output_root / Path(ref["path"]).name).read_text()):
                return True
    return False
