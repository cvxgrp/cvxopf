"""Once-only promotion of the accepted interval-6122 target-free recovery.

The diagnostic used unchanged, already-authorized execution code after the annual
run stopped.  This module binds those exact artifacts, independently re-audits
both solves, publishes one ordinary schema-v1 physical window, and advances only
the shard-008 checkpoint.  It is not a general manual-edit facility.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.run_s4b import _outer
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_execution import (
    shard_checkpoint_payload,
    shard_entry,
    write_shard_checkpoint,
)
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_speculative_archive import (
    audit_candidate,
    invocation,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    outer_boundaries,
    residual_tolerances,
    result_dimensions,
)
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

INTERVAL = 6122
INTERVAL_STOP = 6125
SHARD_ID = "s4b-shard-008"
RECORD_NAME = "operator-recovery-006122.json"
PENDING_RECORD_NAME = "operator-recovery-006122-publication.json"
EVIDENCE_DIRECTORY = "operator-recovery-006122"
BEFORE_CHECKPOINT_SHA256 = (
    "f86389759bc502cb8d8cfe24e527c95f7a70a11d230e5deb77d918c5a049a213"
)
EXECUTION_COMMIT = "9424eebcff28fb11260de0dfc8a75e2025e9684c"
EXECUTION_SOURCE_FINGERPRINT = (
    "c1a1e1c304da303690ea2673190a6a06b1b7dfc5007ed9919fa94349fbe0e386"
)
EVIDENCE = {
    "checkpoint-before.json": BEFORE_CHECKPOINT_SHA256,
    "execution-authority.json": "07d94696f90317fbd3aceea567dd4d3c8d4ccc28e62a758036111c7cff8889db",
    "diagnostic-plan.json": "eb9be0283623d7c668ba369958892cae3f05bb5b8260d18bba421e2910ce056b",
    "diagnostic-result.json": "ed2f9f4c70a856c58736ba8b360e62bb7272a69c1df03084a5c5e91c55bc66d7",
    "window-006121-speculative.json.gz": "01b7a099cd0aeb24cda87c26864121b4320e56710dcf7b2829d17d4fa76080e8",
    "request.json": "1366e2da3e06c9bbe893676e2d17f5641da7e830d2aaff5ccd37abe910a276a2",
    "start.json": "c51483e0ae4fcacafd474657025495b987683bfaced0be45da2f5fc766edb32f",
    "result.json": "64d6d50ac512d70d0f2828eeb00650e54ee2e60bd33064505fe6de0c9f52ea2f",
    "phase.json": "7722d2cbbec1706f91911cba702e8bf5895e7874ce2712056a8bbf8df0dd5b68",
    "copied-target-free/request.json": "905493b9771a6bd25dd1286cae145d256173f8775c92036ae328087bbaa2555d",
    "copied-target-free/start.json": "589d0109e83bf3adb720e567c58856234cb97bcebcdc9da4d417db5b042396ea",
    "copied-target-free/result.json": "c31acf00cd04b085515c81d8ece87091e4aca21dc7f43493a6a16fc7d131271b",
    "copied-target-free/phase.json": "37dd7251a30878eda865492d108b9988e8af522dae6d017430265e72c65dd32c",
}


def _mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, Any], value)


def intervention_identity(contract_sha256: str) -> dict[str, object]:
    return {
        "classification": "reviewed_operator_selected_speculative_recovery",
        "contract_sha256": contract_sha256,
        "target_free_result_sha256": EVIDENCE["result.json"],
        "accepted_result_sha256": EVIDENCE["copied-target-free/result.json"],
        "selected_attempt_id": attempt_id(INTERVAL, 2),
    }


def _bypassed(selected: Mapping[str, object], ordinal: int) -> dict[str, object]:
    item = dict(selected)
    item.update(
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
            "reason": "reviewed_operator_intervention:exact_diagnostic_evidence_retained_externally",
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
    return item


def _causal_payload(candidate: Any) -> dict[str, object]:
    source = candidate.selected_source().source
    return {
        "attempt_id": source.attempt_id,
        "ordinal": source.ordinal,
        "role": source.role,
        "iteration": source.iteration,
        "global_interval_start": source.global_interval_start,
        "global_interval_stop": source.global_interval_stop,
        "outer_plan_id": source.outer_plan_id,
        "storage_device_ids": list(source.storage_device_ids),
        "initial_soc_mwh": dict(source.initial_soc_mwh),
        "first_soc_mwh": source.first_soc_mwh.tolist(),
        "first_b_mw": source.first_b_mw.tolist(),
        "solution_values": {
            key: value.tolist() for key, value in source.solution_values.items()
        },
    }


def _audited_candidates(
    evidence_root: Path, checkpoint: Mapping[str, object]
) -> tuple[Any, Any]:
    fixture, outer = load_s4_fixture(), _outer()
    initial = dict(
        zip(
            cast(Sequence[str], checkpoint["storage_device_ids"]),
            cast(Sequence[float], checkpoint["realized_soc_mwh"]),
            strict=True,
        )
    )
    candidates = []
    for directory in (evidence_root, evidence_root / "copied-target-free"):
        spec = invocation(
            json.loads((directory / "request.json").read_text())["invocation"]
        )
        candidate = audit_candidate(
            directory,
            spec,
            inputs=fixture.inputs,
            policy=fixture.policy,
            outer=outer,
            initial=initial,
            stop=INTERVAL_STOP,
        )
        if candidate.completion.outcome != "accepted":
            raise ValueError(
                "operator recovery candidate is not independently accepted"
            )
        candidates.append(candidate)
    target_free, copied = candidates
    if target_free.spec.source_slot != 1 or copied.spec.source_slot != 2:
        raise ValueError("operator recovery candidates have the wrong roles")
    return target_free, copied


def build_window(
    evidence_root: Path,
    checkpoint: Mapping[str, object],
    *,
    contract_sha256: str,
) -> dict[str, object]:
    target_free, copied = _audited_candidates(evidence_root, checkpoint)
    free_item = dict(target_free.payload["attempt"])
    copied_item = dict(copied.payload["attempt"])
    free_item["supplied_executed_action"] = False
    copied_item.update(
        {
            "source_attempt_id": attempt_id(INTERVAL, 1),
            "supplied_executed_action": True,
            "causal_source": _causal_payload(copied),
        }
    )
    attempts = [_bypassed(copied_item, 0), free_item, copied_item]
    attempts.extend(_bypassed(copied_item, ordinal) for ordinal in range(3, 9))
    result = _mapping(copied_item["result"], "copied target-free result")
    initial = np.asarray(checkpoint["realized_soc_mwh"], dtype=float)
    storage_ids = cast(Sequence[str], checkpoint["storage_device_ids"])
    target = _mapping(copied_item["target_soc_mwh"], "copied target SoC")
    first_b = np.asarray(result["b"], dtype=float)[0]
    fixture = load_s4_fixture()
    return {
        "schema_version": 1,
        "iteration": INTERVAL,
        "interval_start": INTERVAL,
        "interval_stop": INTERVAL_STOP,
        "formulation": "ac",
        "result_dimensions": result_dimensions(fixture.inputs),
        "storage_device_ids": list(storage_ids),
        "initial_soc_mwh": initial.tolist(),
        "target_soc_mwh": [target[item] for item in storage_ids],
        "delta_hours": fixture.inputs.delta,
        "soc_tolerance_mwh": fixture.policy.tolerances.soc_recurrence_mwh_abs,
        "preceding_controlling_attempt_id": checkpoint[
            "preceding_controlling_attempt_id"
        ],
        "attempts": attempts,
        "executed_interval": {
            "controlling_attempt_id": copied_item["attempt_id"],
            "b_mw": first_b.tolist(),
        },
        "post_step_soc_mwh": (initial - fixture.inputs.delta * first_b).tolist(),
        "operator_intervention": intervention_identity(contract_sha256),
    }


def _copy_evidence(source: Path, destination: Path) -> list[dict[str, object]]:
    retained = []
    for relative, expected in EVIDENCE.items():
        data = (source / relative).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise ValueError(f"operator recovery evidence changed: {relative}")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != data:
                raise ValueError("retained operator evidence differs")
        else:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
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
    value: object, output_root: Path, *, require_advanced_checkpoint: bool = True
) -> dict[str, Any]:
    record = _mapping(value, "operator recovery record")
    if (
        record.get("schema_version") != 1
        or record.get("classification") != "applied_s5_interval_6122_operator_recovery"
        or record.get("prior_checkpoint_sha256") != BEFORE_CHECKPOINT_SHA256
        or record.get("execution_commit") != EXECUTION_COMMIT
        or record.get("execution_source_fingerprint") != EXECUTION_SOURCE_FINGERPRINT
    ):
        raise ValueError("operator recovery record identity mismatch")
    base = {key: item for key, item in record.items() if key != "record_sha256"}
    if record.get("record_sha256") != object_sha256(base):
        raise ValueError("operator recovery record hash mismatch")
    contract = {
        key: record[key]
        for key in (
            "schema_version",
            "classification",
            "published_utc",
            "execution_commit",
            "execution_source_fingerprint",
            "prior_checkpoint_sha256",
            "prior_checkpoint_json",
            "evidence",
        )
    }
    if record.get("contract_sha256") != object_sha256(contract):
        raise ValueError("operator recovery contract hash mismatch")
    published = record.get("published_utc")
    if (
        not isinstance(published, str)
        or datetime.fromisoformat(published).utcoffset() is None
    ):
        raise ValueError("operator recovery publication time is invalid")
    evidence_root = output_root / EVIDENCE_DIRECTORY
    for item in cast(Sequence[Mapping[str, object]], record["evidence"]):
        path = output_root / str(item["path"])
        if path.stat().st_size != item["bytes"] or sha256_path(path) != item["sha256"]:
            raise ValueError("retained operator recovery evidence is corrupt")
    before = _mapping(
        json.loads(str(record["prior_checkpoint_json"])), "prior checkpoint"
    )
    if object_sha256(before) != BEFORE_CHECKPOINT_SHA256:
        raise ValueError("operator recovery prior checkpoint differs")
    payload = build_window(
        evidence_root, before, contract_sha256=str(record["contract_sha256"])
    )
    entry = WindowIndexEntry(**cast(dict[str, Any], record["intervention_window"]))
    archive = output_root / "shard-008" / entry.relative_path
    if archive.stat().st_size != entry.bytes or sha256_path(archive) != entry.sha256:
        raise ValueError("operator recovery archive identity mismatch")
    with gzip.open(archive, "rt") as stream:
        if json.load(stream) != payload:
            raise ValueError("operator recovery archive differs from audited evidence")
    if not require_advanced_checkpoint:
        return record
    checkpoint = _mapping(
        json.loads((output_root / "shard-008/checkpoint.json").read_text()),
        "current checkpoint",
    )
    windows = cast(Sequence[object], checkpoint["windows"])
    before_windows = cast(Sequence[object], before["windows"])
    if (
        len(windows) < len(before_windows) + 1
        or list(windows[: len(before_windows)]) != list(before_windows)
        or windows[len(before_windows)] != asdict(entry)
        or checkpoint["next_global_iteration"] < INTERVAL + 1
    ):
        raise ValueError("operator recovery checkpoint was not advanced once")
    return record


def load_record(output_root: Path) -> dict[str, Any] | None:
    path = output_root / RECORD_NAME
    return (
        validate_record(json.loads(path.read_text()), output_root)
        if path.is_file()
        else None
    )


def _advance_published_record(record: Mapping[str, Any], output_root: Path) -> None:
    """Finish checkpoint-last publication after an interrupted prior attempt."""
    checkpoint_path = output_root / "shard-008/checkpoint.json"
    current = _mapping(json.loads(checkpoint_path.read_text()), "current checkpoint")
    before = _mapping(
        json.loads(str(record["prior_checkpoint_json"])), "prior checkpoint"
    )
    if current == before:
        write_shard_checkpoint(
            checkpoint_path,
            cast(Mapping[str, object], record["post_checkpoint"]),
        )


def publish(output_root: Path, diagnostic_root: Path) -> dict[str, Any]:
    """Publish immutable evidence/archive first and advance shard-008 last."""
    path = output_root / RECORD_NAME
    checkpoint_path = output_root / "shard-008/checkpoint.json"
    if path.exists():
        record = validate_record(
            json.loads(path.read_text()),
            output_root,
            require_advanced_checkpoint=False,
        )
        _advance_published_record(record, output_root)
        return validate_record(record, output_root)
    raw = checkpoint_path.read_text()
    if hashlib.sha256(raw.encode()).hexdigest() != BEFORE_CHECKPOINT_SHA256:
        raise ValueError("operator recovery is not at its reviewed stopping checkpoint")
    checkpoint = _mapping(json.loads(raw), "stopped checkpoint")
    retained = _copy_evidence(diagnostic_root, output_root / EVIDENCE_DIRECTORY)
    pending_path = output_root / PENDING_RECORD_NAME
    if pending_path.exists():
        provisional = _mapping(
            json.loads(pending_path.read_text()), "pending operator recovery"
        )
        expected = {
            "schema_version": 1,
            "classification": "applied_s5_interval_6122_operator_recovery",
            "execution_commit": EXECUTION_COMMIT,
            "execution_source_fingerprint": EXECUTION_SOURCE_FINGERPRINT,
            "prior_checkpoint_sha256": BEFORE_CHECKPOINT_SHA256,
            "prior_checkpoint_json": raw,
            "evidence": retained,
        }
        if {key: provisional.get(key) for key in expected} != expected:
            raise ValueError("pending operator recovery identity mismatch")
    else:
        provisional = {
            "schema_version": 1,
            "classification": "applied_s5_interval_6122_operator_recovery",
            "published_utc": datetime.now(timezone.utc).isoformat(),
            "execution_commit": EXECUTION_COMMIT,
            "execution_source_fingerprint": EXECUTION_SOURCE_FINGERPRINT,
            "prior_checkpoint_sha256": BEFORE_CHECKPOINT_SHA256,
            "prior_checkpoint_json": raw,
            "evidence": retained,
        }
        atomic_immutable_json(pending_path, provisional)
    contract_sha = object_sha256(provisional)
    payload = build_window(diagnostic_root, checkpoint, contract_sha256=contract_sha)
    fixture, outer = load_s4_fixture(), _outer()
    _, shard = shard_entry(SHARD_ID)
    interval = _mapping(shard["interval"], "shard interval")
    validate_window_archive(
        payload,
        expected_soc_tolerance_mwh=fixture.policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=residual_tolerances(fixture.policy),
        expected_inner_terminal_policy=fixture.policy.inner_terminal_policy,
        expected_horizon_steps=int(interval["stop"]),
        expected_ac_window_steps=fixture.policy.ac_window_steps,
        expected_result_dimensions=result_dimensions(fixture.inputs),
        expected_delta_hours=fixture.inputs.delta,
        expected_outer_boundary_soc_mwh=outer_boundaries(outer),
        expected_trajectory_start=int(interval["start"]),
        expected_primary_timeout_seconds=300.0,
        expected_operator_intervention=intervention_identity(contract_sha),
    )
    archive_path = output_root / "shard-008" / f"window-{INTERVAL:06d}-operator.json.gz"
    if archive_path.exists():
        with gzip.open(archive_path, "rt") as stream:
            if json.load(stream) != payload:
                raise ValueError("published operator recovery archive differs")
        entry = WindowIndexEntry(
            iteration=INTERVAL,
            relative_path=archive_path.name,
            bytes=archive_path.stat().st_size,
            sha256=sha256_path(archive_path),
        )
    else:
        entry = atomic_gzip_json(archive_path, payload)
    post = shard_checkpoint_payload(
        shard=shard,
        execution_source_fingerprint=str(checkpoint["execution_source_fingerprint"]),
        outer_plan_sha256=str(checkpoint["outer_plan_sha256"]),
        execution_mode="annual",
        realized_soc_mwh=cast(Sequence[float], payload["post_step_soc_mwh"]),
        preceding_controlling_attempt_id=attempt_id(INTERVAL, 2),
        windows=[
            *[
                WindowIndexEntry(**item)
                for item in cast(Sequence[dict[str, Any]], checkpoint["windows"])
            ],
            entry,
        ],
        execution_registry_sha256=str(checkpoint["execution_registry_sha256"]),
        allowed_execution_modes=("annual",),
    )
    final = {
        **provisional,
        "contract_sha256": contract_sha,
        "intervention_window": asdict(entry),
        "post_checkpoint": post,
    }
    final["record_sha256"] = object_sha256(final)
    atomic_immutable_json(path, final)
    write_shard_checkpoint(
        checkpoint_path, cast(Mapping[str, object], final["post_checkpoint"])
    )
    return validate_record(final, output_root)


def timing(directory: Path, archive: Mapping[str, object]) -> Mapping[str, object]:
    record = load_record(directory.parent)
    if record is None or archive.get("operator_intervention") != intervention_identity(
        str(record["contract_sha256"])
    ):
        raise ValueError("operator recovery archive is not record-bound")
    root = directory.parent / EVIDENCE_DIRECTORY
    values = {}
    construction = 0.0
    for name, relative in (
        ("target_free", "phase.json"),
        ("copied", "copied-target-free/phase.json"),
    ):
        events = json.loads((root / relative).read_text())["events"]
        times = {item["phase"]: item["monotonic_seconds"] for item in events}
        values[name] = times["after_ac_solve"] - times["before_ac_solve"]
        construction += times["after_ac_build"] - times["before_ac_build"]
    compute = values["target_free"] + values["copied"] + construction
    return {
        "primary_orchestration_seconds": 0.0,
        "target_free_solver_seconds": 0.0,
        "interrupted_recovery_open_seconds": 0.0,
        "recovery_wall_seconds": compute,
        "model_construction_seconds": construction,
        "diagnostic_compute_seconds": compute,
        "diagnostic_elapsed_seconds": compute,
        "diagnostic_overlap_with_live_recovery_seconds": 0.0,
        "diagnostic_overlapped_live_recovery": False,
    }
