"""Byte-bound reuse of the previously audited, immutable S5 shard prefix.

The anchor is not execution authority. It replaces repeated semantic audits of
completed shards 000–009 only; active shards and every later window retain the
normal validation path.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, cast

from experiments.case118_annual_hierarchy.s4b_manifest import (
    S4B_MANIFEST_PATH,
    canonical_json,
    object_sha256,
)
from experiments.case118_annual_hierarchy.s5_execution import ANNUAL_SHARD_IDS
from experiments.case118_annual_hierarchy.s5_source_transition import (
    load_transition,
    transition_context_authority_pairs,
)
from experiments.case118_annual_hierarchy.streaming_schema import sha256_path


ANCHOR_PATH = Path(__file__).with_name("S5_COMPLETED_PREFIX_ANCHOR.json")
ANCHOR_SHA256 = "a998656ac27842c76ca9c307eda5b99d23328f2b2041244d4f73331be653f52c"
CONTINUATION_NAME = "reviewed-continuation-008.json"
FIRST_WAVE_NAME = "speculative-wave-005-001"
FIRST_ACTIVE_ITERATIONS = (7635, 8320)
CERTIFIED_SHARDS = ANNUAL_SHARD_IDS[:10]


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return cast(dict[str, Any], value)


def _read(path: Path) -> dict[str, Any]:
    return _mapping(json.loads(path.read_text()), str(path))


def _file_ref(path: Path, root: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(root)), "sha256": sha256_path(path)}


def _supervision_ref(
    root: Path, shard_id: str, worker: Mapping[str, object], wave_index: int
) -> dict[str, str]:
    matches = []
    for path in sorted(root.glob(f"supervision-wave-{wave_index:03d}-*.json")):
        record = _read(path)
        if (
            _mapping(record.get("worker_results"), "supervision workers").get(shard_id)
            == worker
            and _mapping(record.get("returncodes"), "supervision codes").get(shard_id)
            == 0
        ):
            matches.append(path)
    if not matches:
        raise ValueError(f"anchor lacks zero-exit supervision for {shard_id}")
    return _file_ref(matches[-1], root)


def derive_completed_prefix(root: Path) -> dict[str, object]:
    """Re-derive the reviewed anchor from retained bytes, not new solves."""
    root = root.resolve()
    manifest = _mapping(_read(S4B_MANIFEST_PATH)["manifest"], "manifest")
    shards = manifest["shards"]
    if len(shards) != 12 or [item["shard_id"] for item in shards[:10]] != list(
        CERTIFIED_SHARDS
    ):
        raise ValueError("anchor shard registry changed")

    continuation_path = root / CONTINUATION_NAME
    continuation = _read(continuation_path)
    reviewed = _mapping(continuation["reviewed_source"], "reviewed source")
    if (
        continuation["classification"] != "explicit_reviewed_continuation"
        or continuation["next_wave"] != 5
        or reviewed["next_wave"] != 5
        or reviewed["completed_shards"] != list(CERTIFIED_SHARDS)
        or continuation["reviewed_source_sha256"] != object_sha256(reviewed)
    ):
        raise ValueError("anchor lacks the reviewed completed-prefix restart")

    active_starts = []
    for item, iteration in zip(shards[10:], FIRST_ACTIVE_ITERATIONS, strict=True):
        shard_id = item["shard_id"]
        path = (
            root
            / FIRST_WAVE_NAME
            / shard_id
            / f"ac-{iteration:06d}-spec-00"
            / "request.json"
        )
        request = _read(path)
        if request["execution_context"] != continuation["execution_context"] or request[
            "invocation"
        ]["window"] != {"shard_id": shard_id, "iteration": iteration}:
            raise ValueError("anchor first-wave request identity changed")
        active_starts.append(
            {
                "shard_id": shard_id,
                "iteration": iteration,
                "request": _file_ref(path, root),
                "starting_checkpoint_sha256": request["checkpoint_sha256"],
                "semantic_prefix_certified_by_this_draft": False,
            }
        )

    completed = []
    for item in shards[:10]:
        shard_id = item["shard_id"]
        interval = item["interval"]
        directory = root / f"shard-{int(shard_id[-3:]):03d}"
        checkpoint_path = directory / "checkpoint.json"
        worker_path = directory / "shard-result.json"
        checkpoint, worker = _read(checkpoint_path), _read(worker_path)
        checkpoint_hash = sha256_path(checkpoint_path)
        start, stop = interval["start"], interval["stop"]
        if (
            checkpoint["shard_id"] != shard_id
            or checkpoint["complete"] is not True
            or checkpoint["completed_intervals"] != stop - start
            or checkpoint["next_global_iteration"] != stop
            or len(checkpoint["windows"]) != stop - start
            or worker["shard_id"] != shard_id
            or worker["classification"] != "accepted"
            or worker["execution_complete"] is not True
            or worker["all_independent_audits_agree"] is not True
            or worker["checkpoint_sha256"] != checkpoint_hash
        ):
            raise ValueError(f"anchor completed-shard evidence differs: {shard_id}")
        for offset, entry in enumerate(checkpoint["windows"]):
            if entry["iteration"] != start + offset:
                raise ValueError("anchor window index is discontinuous")
            archive = (directory / entry["relative_path"]).resolve()
            if (
                not archive.is_relative_to(directory.resolve())
                or archive.stat().st_size != entry["bytes"]
                or sha256_path(archive) != entry["sha256"]
            ):
                raise ValueError(f"anchor window bytes differ: {archive}")
        completed.append(
            {
                "shard_id": shard_id,
                "interval": [start, stop],
                "checkpoint_sha256": checkpoint_hash,
                "worker_result_sha256": sha256_path(worker_path),
                "worker_summary_sha256": worker["summary_sha256"],
                "window_chain_sha256": worker["window_chain_sha256"],
                "supervision": _supervision_ref(
                    root, shard_id, worker, item["ordinal"] // 2
                ),
            }
        )

    payload = {
        "scope": "immutable_completed_shards_only",
        "next_wave": 5,
        "manifest_sha256": sha256_path(S4B_MANIFEST_PATH),
        "execution_context": continuation["execution_context"],
        "authority_sha256": object_sha256(continuation["authority"]),
        "restart_pass_evidence": {
            "reviewed_continuation": _file_ref(continuation_path, root),
            "new_wave": FIRST_WAVE_NAME,
        },
        "completed_shards": completed,
        "active_starting_checkpoints": active_starts,
    }
    return {
        "schema_version": 1,
        "classification": "draft_s5_completed_prefix_anchor",
        "execution_authorized": False,
        "semantic_audit_reused_from_prior_restart": True,
        "active_shard_semantic_prefix_certified": False,
        "payload": payload,
        "payload_sha256": object_sha256(payload),
    }


def verified_completed_prefix(root: Path) -> frozenset[str]:
    """Return only the ten immutable shard IDs after exact byte rederivation."""
    if sha256_path(ANCHOR_PATH) != ANCHOR_SHA256:
        raise ValueError("S5 completed-prefix anchor file identity changed")
    anchor_bytes = ANCHOR_PATH.read_bytes()
    anchor = _read(ANCHOR_PATH)
    if anchor_bytes != canonical_json(anchor):
        raise ValueError("S5 completed-prefix anchor is not canonical")
    if canonical_json(derive_completed_prefix(root)) != anchor_bytes:
        raise ValueError("S5 completed-prefix anchor no longer matches retained data")
    transition = load_transition(root)
    historical = (
        anchor["payload"]["execution_context"],
        _read(root / CONTINUATION_NAME)["authority"],
    )
    if transition is None or historical not in transition_context_authority_pairs(
        transition
    ):
        raise ValueError("S5 completed-prefix anchor lacks source lineage")
    return frozenset(CERTIFIED_SHARDS)
