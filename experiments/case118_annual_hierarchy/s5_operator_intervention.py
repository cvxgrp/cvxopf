"""One reviewed interval-2448 operator intervention for the S5 study.

This is deliberately not a general recovery-policy extension.  It promotes one
already solved, fully audited diagnostic attempt into the stopped shard, while
retaining the interrupted production lifecycle and every diagnostic input as
immutable evidence.  Publication is archive-first and checkpoint-last.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_execution import (
    shard_checkpoint_payload,
    shard_entry,
    validate_shard_checkpoint,
    verify_shard_artifacts,
    write_shard_checkpoint,
)
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.streaming_archive import (
    outer_boundaries,
    residual_tolerances,
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_runner import StreamingOuterPlan
from experiments.case118_annual_hierarchy.streaming_schema import (
    ATTEMPT_ROLES,
    PERTURBATION_SCALES,
    WindowIndexEntry,
    atomic_gzip_json,
    atomic_immutable_json,
    attempt_id,
    perturbation_seed,
    sha256_path,
    validate_window_archive,
)

RECORD_NAME = "operator-intervention-002448.json"
EVIDENCE_DIRECTORY = "operator-intervention-002448"
INTERVAL = 2448
INTERVAL_STOP = 2451
SHARD_ID = "s4b-shard-003"
SELECTED_ORDINAL = 8
SELECTED_SCALE = 0.01
SELECTED_SEED = 17244823
PRIOR_COMMIT = "0c130b0a7eb011d443ea4724fb95de9bb5b140e9"
PRIOR_SOURCE_FINGERPRINT = (
    "ddea6890e4063089e8d28a3d8407a18e7d378e27b451dbcf094f2a7ecfba868c"
)
PREDECESSOR_TRANSITION_SHA256 = (
    "5e1bca0fdc42ca7e87b0b3f0b9741ea343d827aa66e4abb946d578360bbe9c3e"
)
STOPPING_EVIDENCE = {
    "progress.json": "97f65d6bdef28cf31789d6a535b351bfda4ff899009d5a5397bc13f5ea35f8ef",
    "root-outcome-001.json": "5058c01203b283854b63c71cf8e130c3ee8f83ba80e55264ab676f0a3249b96d",
    "supervision-wave-001-000.json": "8bf341a274ba8a381d60097c620eff5feca658a3ccf158dee789e088cd03ea4f",
    "shard-000/checkpoint.json": "0598a454a1f86125dfde5b7f34dedace77149532e53490c2398f2cb91f087736",
    "shard-001/checkpoint.json": "7a95ab4e207ec6f5d90701675552a27bbee2acfad9f048723c291b02599cd455",
    "shard-002/checkpoint.json": "ffbc4907db8323efa7c51d115107aac5c41b0a42da63ef1af297ed634951e591",
    "shard-003/checkpoint.json": "db8b80b6f46a3d05dc4c25a748780877df6cd7994c25dcbbb0f335ccdf5d81e1",
    "shard-003/window-phase-002448-primary.json": "2c0a149e5bbbe04cfb224cd465675265bd0d78ae7074f7e90f57960305307847",
    "shard-003/window-phase-002448-recovery.json": "7bfda01f16ea04ecda35dc2f5b3ea944a8a94cde6b4a8436e8b72492ddad0b53",
    "shard-003/window-supervision-002448-timeout.json": "ae08ff118590edbe8a3c657320402839be72427f7b5ba37ff9d5b6cecc2f46b3",
}
DIAGNOSTIC_EVIDENCE = {
    "README.md": "29b01d58e9d66cb8470420dd10096195a3a99603f15ee9191a95875fd907f271",
    "diagnostic-plan.json": "ba8808df253a7c598add736719f32e9cfd697efa7934c1492b37051aa821a380",
    "diagnostic-result.json": "539e214f58e0bb60cb67527243835e3a8aa483e85924839d604c8c1e02bc98ed",
    "checkpoint.json": "db8b80b6f46a3d05dc4c25a748780877df6cd7994c25dcbbb0f335ccdf5d81e1",
    "window-002447-6e7f6e6d311e6b2d.json.gz": "fd698afd85aa27cc64aae541d00bb444f508eddac3a794ad20bd556c92103455",
    "replay.py": "e191fe12d4a036c7652e864e5b3a976a5f7f87fd74311baf29267f2f6427698e",
    "causal6/canonical-start.json": "df5ed062dce113169921e366110e3b38c67656c6681d1876438134f5d19c2dda",
    "causal6/start.json": "e8e4d1b47380004cb8c24a7a3a873f1bf1efb1dfb469f6f63597f695d43f5be9",
    "causal6/phase.json": "8c4807616d1952b7c16c1dd10835cfbf438a7d37c0569c9ea5ae60b4a5d6a7c0",
    "causal6/worker.log": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "causal7/canonical-start.json": "fb9e4956443249a9313ed321c4065baabe55d33d410682edf239a404326e1e10",
    "causal7/start.json": "fb1167b0d20305d239a523f4f758611860183e28f10a6e8ee200d3198be0c193",
    "causal7/phase.json": "d9ed86636e365b00514e4c4b8645de7ced4f95631a1d1ff3bf7a964d719c9711",
    "causal7/worker.log": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "causal8/canonical-start.json": "f6c5cb97d6e8c37ec8f4809cc03059b7e5bfe3d05bbd1315b125c9c62e4b7cbe",
    "causal8/start.json": "e89a6a459b874b9bf23ccbbf50fba94f4c7d44f1e1e28373dcadd51d27e861bb",
    "causal8/phase.json": "c28fcefecdf8c2547c91d80e07b6e55c045d0fd7ca47d8a3c9d01d7eb517834f",
    "causal8/result.json": "cd22e72e4956f4a5653da4de2688de6fb38b264b4449ad1ef87871fe1fa47f32",
    "causal8/worker.log": "50e103b24e73c51e6400e69004cf5e2d1a779ff9a2a7ae9773c8cc9524a672ce",
}
TIMING_OBSERVATIONS = {
    "live_interrupted_attempt_id": attempt_id(INTERVAL, 6),
    "live_interrupted_attempt_started_monotonic_seconds": 705973.089622,
    "live_interrupted_open_wall_seconds": 2944.5034601688385,
    "live_interrupted_measurement": (
        "recovery-phase publication to terminal wave-supervision creation"
    ),
    "diagnostic_process_compute_seconds": 847.6607469590381,
    "diagnostic_elapsed_seconds": 848.4031040668488,
    "diagnostic_overlapped_live_recovery": True,
}


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bound(path: Path, expected: str, label: str) -> bytes:
    data = path.read_bytes()
    if _digest_bytes(data) != expected:
        raise ValueError(f"{label} hash mismatch")
    return data


def _checkpoint_position(
    output_root: Path,
    *,
    snapshots: Mapping[str, object],
    post_checkpoint: Mapping[str, object],
    context: Mapping[str, object],
) -> str:
    """Classify the live pointer without permitting rollback or reapplication."""
    checkpoint_path = output_root / "shard-003/checkpoint.json"
    if not checkpoint_path.is_file():
        raise ValueError("S5 intervention checkpoint is missing")
    checkpoint_raw = checkpoint_path.read_text()
    old_raw = snapshots["shard-003/checkpoint.json"]
    if checkpoint_raw == old_raw:
        return "before"
    current = _mapping(json.loads(checkpoint_raw), "current shard checkpoint")
    if current == post_checkpoint:
        return "after"

    _, shard = shard_entry(SHARD_ID)
    validate_shard_checkpoint(
        current,
        shard=shard,
        expected_execution_registry_sha256=str(
            post_checkpoint["execution_registry_sha256"]
        ),
        allowed_execution_modes=("annual",),
    )
    post_windows = cast(Sequence[object], post_checkpoint["windows"])
    current_windows = cast(Sequence[object], current["windows"])
    immutable_fields = (
        "schema_version",
        "manifest_sha256",
        "execution_registry_sha256",
        "shard_id",
        "ordinal",
        "interval",
        "outer_plan_sha256",
        "execution_mode",
        "storage_device_ids",
        "initial_soc_mwh",
        "terminal_soc_mwh",
    )
    if (
        len(current_windows) <= len(post_windows)
        or list(current_windows[: len(post_windows)]) != list(post_windows)
        or any(
            current.get(name) != post_checkpoint.get(name) for name in immutable_fields
        )
        or current.get("execution_source_fingerprint") != context["source_fingerprint"]
        or cast(int, current["next_global_iteration"]) <= INTERVAL + 1
    ):
        raise ValueError(
            "S5 intervention checkpoint is not a validated post-intervention extension"
        )
    return "extended"


def intervention_identity(contract_sha256: str) -> dict[str, object]:
    """Return the exact archive-level identity for the reviewed intervention."""
    return {
        "classification": "reviewed_operator_selected_diagnostic_attempt",
        "contract_sha256": contract_sha256,
        "diagnostic_result_sha256": DIAGNOSTIC_EVIDENCE["diagnostic-result.json"],
        "accepted_result_sha256": DIAGNOSTIC_EVIDENCE["causal8/result.json"],
        "selected_attempt_id": attempt_id(INTERVAL, SELECTED_ORDINAL),
    }


def validate_contract(
    value: object,
    *,
    predecessor_transition: Mapping[str, object],
) -> dict[str, Any]:
    """Validate the one owner-reviewed intervention and successor source binding."""
    contract = _mapping(value, "S5 operator intervention contract")
    if (
        contract.get("schema_version") != 1
        or contract.get("classification")
        != "reviewed_s5_interval_2448_operator_intervention"
        or contract.get("review_status") != "reviewed"
        or contract.get("launch_authorized") is not True
        or contract.get("general_recovery_policy_changed") is not False
        or contract.get("interval") != INTERVAL
        or contract.get("interval_stop") != INTERVAL_STOP
        or contract.get("shard_id") != SHARD_ID
        or contract.get("selected_ordinal") != SELECTED_ORDINAL
        or contract.get("selected_scale") != SELECTED_SCALE
        or contract.get("selected_seed") != SELECTED_SEED
        or contract.get("selected_attempt_id") != attempt_id(INTERVAL, SELECTED_ORDINAL)
        or contract.get("preceding_attempt_id") != attempt_id(INTERVAL - 1, 0)
        or contract.get("stopping_evidence") != STOPPING_EVIDENCE
        or contract.get("diagnostic_evidence") != DIAGNOSTIC_EVIDENCE
        or contract.get("predecessor_transition_sha256")
        != PREDECESSOR_TRANSITION_SHA256
    ):
        raise ValueError("S5 operator intervention differs from the reviewed event")
    predecessor_contract = _mapping(
        predecessor_transition.get("contract"), "predecessor transition contract"
    )
    predecessor_execution = _mapping(
        predecessor_contract.get("continuation_execution"),
        "predecessor continuation execution",
    )
    prior_context = _mapping(
        predecessor_execution.get("context"), "prior execution context"
    )
    prior_authority = _mapping(
        predecessor_transition["new_authority"], "prior numerical authority"
    )
    if (
        prior_context.get("git_commit") != PRIOR_COMMIT
        or prior_context.get("source_fingerprint") != PRIOR_SOURCE_FINGERPRINT
        or contract.get("prior_execution_context") != prior_context
        or contract.get("prior_numerical_authority") != prior_authority
    ):
        raise ValueError("S5 operator intervention prior provenance mismatch")
    successor = _mapping(contract.get("continuation_execution"), "successor execution")
    context = _mapping(successor.get("context"), "successor context")
    identities = {"git_commit", "source_fingerprint"}
    if (
        {k: v for k, v in context.items() if k not in identities}
        != {k: v for k, v in prior_context.items() if k not in identities}
        or successor.get("required_clean_execution_commit") != context.get("git_commit")
        or successor.get("required_execution_source_fingerprint")
        != context.get("source_fingerprint")
        or successor.get("commit_must_match_exactly") is not True
        or successor.get("descendant_commits_implicitly_allowed") is not False
    ):
        raise ValueError("S5 operator intervention successor context mismatch")
    for name, length in (("git_commit", 40), ("source_fingerprint", 64)):
        item = context.get(name)
        if not isinstance(item, str) or len(item) != length:
            raise ValueError("S5 operator intervention needs exact successor identity")
    return contract


def _bypassed_attempt(
    selected: Mapping[str, object], ordinal: int
) -> dict[str, object]:
    result = dict(selected)
    result.update(
        {
            "attempt_id": attempt_id(INTERVAL, ordinal),
            "slot_state": "operator_bypassed",
            "role": ATTEMPT_ROLES[ordinal],
            "transformation": (
                "shifted_preceding"
                if ordinal < 2
                else "copy_target_free"
                if ordinal == 2
                else "perturb_target_free"
                if ordinal < 6
                else "perturb_causal"
            ),
            "ordinal": ordinal,
            "scale": None if ordinal < 3 else PERTURBATION_SCALES[(ordinal - 3) % 3],
            "seed": None if ordinal < 3 else perturbation_seed(INTERVAL, ordinal),
            "source_kind": None,
            "source_attempt_id": None,
            "reason": (
                "reviewed_operator_intervention:retained_live_and_diagnostic_"
                "evidence_is_bound_outside_the_promoted_attempt_registry"
            ),
            "timeout_budget_seconds": None,
            "solver_executed": False,
            "supplied_executed_action": False,
            "raw_start": None,
            "assigned_start": None,
            "solver_x0": None,
            "solver_x0_layout": None,
            "solver_evidence": None,
            "structural_signature": None,
            "result": None,
            "audit": None,
            "causal_source": None,
        }
    )
    return result


def build_window_payload(
    accepted_result: Mapping[str, object],
    *,
    contract_sha256: str,
) -> dict[str, object]:
    """Build the exact archive promoted from the retained accepted slot-8 solve."""
    accepted = _mapping(accepted_result, "accepted diagnostic result")
    if (
        accepted.get("accepted") is not True
        or accepted.get("diagnostic_only") is not True
        or accepted.get("action_applied_to_live_run") is not False
        or accepted.get("context_unchanged") is not True
        or accepted.get("label") != "causal8"
    ):
        raise ValueError("diagnostic result is not the accepted untouched slot-8 solve")
    selected = _mapping(accepted.get("attempt"), "accepted diagnostic attempt")
    if (
        selected.get("attempt_id") != attempt_id(INTERVAL, SELECTED_ORDINAL)
        or selected.get("ordinal") != SELECTED_ORDINAL
        or selected.get("scale") != SELECTED_SCALE
        or selected.get("seed") != SELECTED_SEED
        or selected.get("source_attempt_id") != attempt_id(INTERVAL - 1, 0)
        or selected.get("supplied_executed_action") is not True
        or _mapping(selected.get("audit"), "accepted diagnostic audit").get(
            "accepted_primal"
        )
        is not True
    ):
        raise ValueError("diagnostic slot-8 identity or acceptance mismatch")
    storage_ids = cast(Sequence[str], selected["storage_device_ids"])
    initial_map = _mapping(selected["initial_soc_mwh"], "diagnostic initial SoC")
    target_map = _mapping(selected["target_soc_mwh"], "diagnostic target SoC")
    initial = np.asarray([initial_map[item] for item in storage_ids], dtype=float)
    target = np.asarray([target_map[item] for item in storage_ids], dtype=float)
    b_first = np.asarray(
        _mapping(selected["result"], "diagnostic result")["b"], dtype=float
    )[0]
    attempts = [_bypassed_attempt(selected, ordinal) for ordinal in range(8)]
    attempts.append(dict(selected))
    fixture = load_s4_fixture()
    payload: dict[str, object] = {
        "schema_version": 1,
        "iteration": INTERVAL,
        "interval_start": INTERVAL,
        "interval_stop": INTERVAL_STOP,
        "formulation": "ac",
        "result_dimensions": result_dimensions(fixture.inputs),
        "storage_device_ids": list(storage_ids),
        "initial_soc_mwh": initial.tolist(),
        "target_soc_mwh": target.tolist(),
        "delta_hours": fixture.inputs.delta,
        "soc_tolerance_mwh": fixture.policy.tolerances.soc_recurrence_mwh_abs,
        "preceding_controlling_attempt_id": attempt_id(INTERVAL - 1, 0),
        "attempts": attempts,
        "executed_interval": {
            "controlling_attempt_id": selected["attempt_id"],
            "b_mw": b_first.tolist(),
        },
        "post_step_soc_mwh": (initial - fixture.inputs.delta * b_first).tolist(),
        "operator_intervention": intervention_identity(contract_sha256),
    }
    return payload


def _copy_evidence(
    source_root: Path, destination_root: Path
) -> list[dict[str, object]]:
    retained: list[dict[str, object]] = []
    for relative, expected in DIAGNOSTIC_EVIDENCE.items():
        source = source_root / relative
        data = _read_bound(source, expected, f"diagnostic evidence {relative}")
        destination = destination_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.read_bytes() != data:
                raise ValueError(
                    "retained intervention evidence differs from diagnostic"
                )
        else:
            descriptor = os.open(
                destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
        retained.append(
            {
                "path": f"{EVIDENCE_DIRECTORY}/{relative}",
                "bytes": len(data),
                "sha256": expected,
            }
        )
    return retained


def validate_record(
    value: object,
    output_root: Path,
    *,
    predecessor_transition: Mapping[str, object],
) -> dict[str, Any]:
    """Validate the immutable intervention/source-transition record."""
    record = _mapping(value, "S5 operator intervention record")
    contract = validate_contract(
        record.get("contract"), predecessor_transition=predecessor_transition
    )
    contract_sha = object_sha256(contract)
    if (
        record.get("schema_version") != 1
        or record.get("classification")
        != "applied_s5_interval_2448_operator_intervention"
        or record.get("contract_sha256") != contract_sha
        or record.get("predecessor_transition_sha256") != PREDECESSOR_TRANSITION_SHA256
        or record.get("predecessor_transition") != predecessor_transition
        or record.get("prior_authority") != predecessor_transition["new_authority"]
    ):
        raise ValueError("S5 operator intervention record identity mismatch")
    published = record.get("published_utc")
    if (
        not isinstance(published, str)
        or datetime.fromisoformat(published).utcoffset() is None
    ):
        raise ValueError("S5 operator intervention lacks publication time")
    successor = _mapping(contract["continuation_execution"], "successor execution")
    context = _mapping(successor["context"], "successor context")
    expected_authority = {
        **cast(Mapping[str, object], predecessor_transition["new_authority"]),
        "execution_commit": context["git_commit"],
        "source_fingerprint": context["source_fingerprint"],
        "source_version_contract_sha256": contract_sha,
    }
    if record.get("new_authority") != expected_authority:
        raise ValueError("S5 operator intervention successor authority mismatch")
    if record.get("timing_observations") != TIMING_OBSERVATIONS:
        raise ValueError("S5 operator intervention timing evidence mismatch")
    snapshots = _mapping(record.get("stopping_pointer_json"), "stopping snapshots")
    expected_snapshot_names = {
        name for name in STOPPING_EVIDENCE if name.endswith("checkpoint.json")
    } | {"progress.json"}
    if set(snapshots) != expected_snapshot_names:
        raise ValueError("S5 operator intervention snapshot registry mismatch")
    for name in expected_snapshot_names:
        raw = snapshots[name]
        if (
            not isinstance(raw, str)
            or _digest_bytes(raw.encode()) != STOPPING_EVIDENCE[name]
        ):
            raise ValueError("S5 operator intervention stopping snapshot mismatch")
    for name, expected in STOPPING_EVIDENCE.items():
        if name in snapshots:
            continue
        _read_bound(output_root / name, expected, f"stopping evidence {name}")
    evidence = record.get("diagnostic_evidence")
    if not isinstance(evidence, list) or len(evidence) != len(DIAGNOSTIC_EVIDENCE):
        raise ValueError("S5 operator intervention evidence registry mismatch")
    expected_paths = {
        f"{EVIDENCE_DIRECTORY}/{relative}": digest
        for relative, digest in DIAGNOSTIC_EVIDENCE.items()
    }
    if [item.get("path") for item in evidence if isinstance(item, Mapping)] != list(
        expected_paths
    ):
        raise ValueError("S5 operator intervention evidence order mismatch")
    observed_paths: set[str] = set()
    for item in cast(Sequence[Mapping[str, object]], evidence):
        relative = item.get("path")
        if (
            not isinstance(relative, str)
            or relative in observed_paths
            or expected_paths.get(relative) != item.get("sha256")
        ):
            raise ValueError("S5 operator intervention evidence identity mismatch")
        observed_paths.add(relative)
        path = output_root / relative
        if (
            not path.is_file()
            or path.stat().st_size != item.get("bytes")
            or sha256_path(path) != item.get("sha256")
        ):
            raise ValueError("S5 retained diagnostic evidence is missing or corrupt")
    if observed_paths != set(expected_paths):
        raise ValueError("S5 operator intervention evidence registry is incomplete")
    entry = _mapping(record.get("intervention_window"), "intervention window")
    window_path = output_root / "shard-003" / str(entry["relative_path"])
    if (
        not window_path.is_file()
        or window_path.stat().st_size != entry.get("bytes")
        or sha256_path(window_path) != entry.get("sha256")
    ):
        raise ValueError("S5 intervention window is missing or corrupt")
    retained_result_path = output_root / EVIDENCE_DIRECTORY / "causal8/result.json"
    expected_payload = build_window_payload(
        json.loads(retained_result_path.read_text()), contract_sha256=contract_sha
    )
    with gzip.open(window_path, "rt", encoding="utf-8") as stream:
        if json.load(stream) != expected_payload:
            raise ValueError("S5 intervention window differs from accepted diagnostic")
    post_checkpoint = _mapping(
        record.get("post_intervention_checkpoint"), "post-intervention checkpoint"
    )
    if record.get("post_intervention_checkpoint_sha256") != object_sha256(
        post_checkpoint
    ):
        raise ValueError("S5 intervention checkpoint identity mismatch")
    old_checkpoint = _mapping(
        json.loads(str(snapshots["shard-003/checkpoint.json"])),
        "stopped shard checkpoint",
    )
    old_windows = cast(Sequence[object], old_checkpoint.get("windows"))
    post_windows = cast(Sequence[object], post_checkpoint.get("windows"))
    if (
        list(post_windows) != [*old_windows, entry]
        or post_checkpoint.get("completed_intervals")
        != cast(int, old_checkpoint["completed_intervals"]) + 1
        or post_checkpoint.get("next_global_iteration") != INTERVAL + 1
        or post_checkpoint.get("preceding_controlling_attempt_id")
        != attempt_id(INTERVAL, SELECTED_ORDINAL)
        or post_checkpoint.get("realized_soc_mwh")
        != expected_payload["post_step_soc_mwh"]
        or post_checkpoint.get("execution_source_fingerprint")
        != context["source_fingerprint"]
        or post_checkpoint.get("outer_plan_sha256")
        != old_checkpoint.get("outer_plan_sha256")
        or post_checkpoint.get("execution_registry_sha256")
        != old_checkpoint.get("execution_registry_sha256")
    ):
        raise ValueError("S5 intervention checkpoint does not advance exactly once")
    _checkpoint_position(
        output_root,
        snapshots=snapshots,
        post_checkpoint=post_checkpoint,
        context=context,
    )
    return record


def load_record(
    output_root: Path,
    *,
    predecessor_transition: Mapping[str, object],
) -> dict[str, Any] | None:
    path = output_root / RECORD_NAME
    if not path.is_file():
        return None
    return validate_record(
        json.loads(path.read_text()),
        output_root,
        predecessor_transition=predecessor_transition,
    )


def publish_intervention(
    output_root: Path,
    contract_path: Path,
    diagnostic_root: Path,
    context: Mapping[str, object],
    authority: Mapping[str, object],
    *,
    predecessor_transition: Mapping[str, object],
) -> dict[str, Any]:
    """Publish the reviewed diagnostic action, then advance one checkpoint."""
    contract = validate_contract(
        json.loads(contract_path.read_text()),
        predecessor_transition=predecessor_transition,
    )
    contract_sha = object_sha256(contract)
    if contract["continuation_execution"]["context"] != context:
        raise ValueError("S5 intervention does not bind this execution context")
    expected_authority = {
        **cast(Mapping[str, object], predecessor_transition["new_authority"]),
        "execution_commit": context["git_commit"],
        "source_fingerprint": context["source_fingerprint"],
        "source_version_contract_sha256": contract_sha,
    }
    if authority != expected_authority:
        raise ValueError("S5 intervention authority differs from reviewed successor")
    existing = load_record(output_root, predecessor_transition=predecessor_transition)
    if existing is not None:
        if existing["contract"] != contract or existing["new_authority"] != authority:
            raise ValueError("existing S5 intervention differs from reviewed request")
        checkpoint_path = output_root / "shard-003/checkpoint.json"
        position = _checkpoint_position(
            output_root,
            snapshots=_mapping(existing["stopping_pointer_json"], "stopping snapshots"),
            post_checkpoint=_mapping(
                existing["post_intervention_checkpoint"],
                "post-intervention checkpoint",
            ),
            context=context,
        )
        if position == "before":
            write_shard_checkpoint(
                checkpoint_path,
                _mapping(
                    existing["post_intervention_checkpoint"],
                    "post-intervention checkpoint",
                ),
            )
        return existing
    for name, expected in STOPPING_EVIDENCE.items():
        _read_bound(output_root / name, expected, f"stopping evidence {name}")
    if (
        sha256_path(output_root / "source-version-transition.json")
        != PREDECESSOR_TRANSITION_SHA256
    ):
        raise ValueError("S5 intervention predecessor transition mismatch")
    fixture = load_s4_fixture()
    _, shard = shard_entry(SHARD_ID)
    predecessor_authority = _mapping(
        predecessor_transition.get("new_authority"), "predecessor authority"
    )
    verify_shard_artifacts(
        output_root / "shard-003",
        shard=shard,
        outer=_load_outer(),
        expected_execution_registry_sha256=str(
            predecessor_authority["annual_registry_sha256"]
        ),
        allowed_execution_modes=("annual",),
    )
    retained = _copy_evidence(diagnostic_root, output_root / EVIDENCE_DIRECTORY)
    accepted = json.loads((diagnostic_root / "causal8/result.json").read_text())
    payload = build_window_payload(accepted, contract_sha256=contract_sha)
    outer = _load_outer()
    shard_interval = _mapping(shard.get("interval"), "shard interval")
    validate_window_archive(
        payload,
        expected_soc_tolerance_mwh=fixture.policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=residual_tolerances(fixture.policy),
        expected_inner_terminal_policy=fixture.policy.inner_terminal_policy,
        expected_horizon_steps=int(cast(int, shard_interval["stop"])),
        expected_ac_window_steps=fixture.policy.ac_window_steps,
        expected_result_dimensions=result_dimensions(fixture.inputs),
        expected_delta_hours=fixture.inputs.delta,
        expected_outer_boundary_soc_mwh=outer_boundaries(outer),
        expected_trajectory_start=int(cast(int, shard_interval["start"])),
        expected_primary_timeout_seconds=300.0,
        expected_operator_intervention=intervention_identity(contract_sha),
    )
    payload_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    archive_path = (
        output_root / "shard-003" / f"window-{INTERVAL:06d}-{payload_hash}.json.gz"
    )
    if archive_path.exists():
        with gzip.open(archive_path, "rt", encoding="utf-8") as stream:
            if json.load(stream) != payload:
                raise ValueError("existing S5 intervention window differs")
        entry = WindowIndexEntry(
            iteration=INTERVAL,
            relative_path=archive_path.name,
            bytes=archive_path.stat().st_size,
            sha256=sha256_path(archive_path),
        )
    else:
        entry = atomic_gzip_json(archive_path, payload)
    old_checkpoint_raw = (output_root / "shard-003/checkpoint.json").read_text()
    old_checkpoint = _mapping(json.loads(old_checkpoint_raw), "stopped checkpoint")
    windows = [
        WindowIndexEntry(**cast(dict[str, Any], item))
        for item in cast(Sequence[Mapping[str, object]], old_checkpoint["windows"])
    ]
    windows.append(entry)
    checkpoint = shard_checkpoint_payload(
        shard=shard,
        execution_source_fingerprint=str(context["source_fingerprint"]),
        outer_plan_sha256=str(old_checkpoint["outer_plan_sha256"]),
        execution_mode="annual",
        realized_soc_mwh=cast(Sequence[float], payload["post_step_soc_mwh"]),
        preceding_controlling_attempt_id=attempt_id(INTERVAL, SELECTED_ORDINAL),
        windows=windows,
        execution_registry_sha256=str(old_checkpoint["execution_registry_sha256"]),
        allowed_execution_modes=("annual",),
    )
    record = {
        "schema_version": 1,
        "published_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "applied_s5_interval_2448_operator_intervention",
        "contract": contract,
        "contract_sha256": contract_sha,
        "predecessor_transition_sha256": PREDECESSOR_TRANSITION_SHA256,
        "predecessor_transition": predecessor_transition,
        "prior_authority": predecessor_transition["new_authority"],
        "new_authority": dict(authority),
        "timing_observations": dict(TIMING_OBSERVATIONS),
        "stopping_pointer_json": {
            "progress.json": (output_root / "progress.json").read_text(),
            **{
                f"shard-{ordinal:03d}/checkpoint.json": (
                    output_root / f"shard-{ordinal:03d}/checkpoint.json"
                ).read_text()
                for ordinal in range(3)
            },
            "shard-003/checkpoint.json": old_checkpoint_raw,
        },
        "diagnostic_evidence": retained,
        "intervention_window": entry.__dict__,
        "post_intervention_checkpoint": checkpoint,
        "post_intervention_checkpoint_sha256": object_sha256(checkpoint),
    }
    atomic_immutable_json(output_root / RECORD_NAME, record)
    write_shard_checkpoint(output_root / "shard-003/checkpoint.json", checkpoint)
    return validate_record(
        record, output_root, predecessor_transition=predecessor_transition
    )


def _load_outer() -> StreamingOuterPlan:
    """Load the frozen outer plan lazily to avoid experiment import cycles."""
    from experiments.case118_annual_hierarchy.run_s4b import _outer

    return _outer()


__all__ = [
    "INTERVAL",
    "RECORD_NAME",
    "build_window_payload",
    "intervention_identity",
    "load_record",
    "publish_intervention",
    "validate_contract",
    "validate_record",
]
