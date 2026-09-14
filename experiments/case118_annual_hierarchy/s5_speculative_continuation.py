"""Read-only binding of a reviewed speculative-policy continuation.

There is deliberately no publisher or default approval in this implementation.
At cutover the owner reviews a record binding the actual stopping checkpoints,
previous transition, and clean successor source. Completed prefixes stay byte
identical; this is a policy phase, not a reinterpretation of historical solves.
"""

from __future__ import annotations

import json
import hashlib
from pathlib import Path
from typing import Any, Mapping

from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_speculative_policy import POLICY_NAME

RECORD_NAME = "speculative-policy-source-transition.json"
CLASSIFICATION = "applied_s5_speculative_policy_continuation"


def policy_identity() -> dict[str, object]:
    return {
        "name": POLICY_NAME,
        "maximum_active_shards": 2,
        "maximum_solver_processes": 3,
        "helper_source_slots": [6, 7, 8, 1, 2, 3, 4, 5],
        "bounded_solve_seconds": 300.0,
        "primary_budget_seconds": None,
        "uncapped_replay_budget_seconds": None,
        "per_worker_rss_mib": 16384.0,
        "aggregate_rss_mib": 24576.0,
        "helper_reserve_mib": 8192.0,
        "helper_pressure_mib": 22528.0,
        "terminal_policy": "hard_equality",
        "acceptance_gate": "unchanged_m17",
        "selection": "first_audited_batch_primary_then_helper_order",
        "replay": "minimum_complete_normalized_residual_then_objective_then_order_or_first_timed_out_x0",
    }


def prepare_record(
    output_root: Path,
    predecessor: Mapping[str, Any],
    context: Mapping[str, object],
    *,
    execution_authorized: bool = False,
) -> dict[str, Any]:
    """Prepare a reviewable binding from copied/stopped records; never publish it.

    Defaults to a non-executable proposal. Final authorization must be supplied
    explicitly after owner approval and a stopped run. Two reads detect ordinary
    in-flight checkpoint changes; this is not a snapshot-lock substitute.
    """
    paths = [
        output_root / "progress.json",
        *sorted(output_root.glob("shard-*/checkpoint.json")),
    ]
    snapshots = {str(path.relative_to(output_root)): path.read_text() for path in paths}
    progress = json.loads(snapshots["progress.json"])
    prior_context = predecessor["contract"]["continuation_execution"]["context"]
    if (
        progress.get("execution_context") != prior_context
        or progress.get("authority") != predecessor["new_authority"]
    ):
        raise ValueError("stopping progress differs from predecessor execution")
    if execution_authorized and (
        context.get("git_clean") is not True
        or progress.get("classification")
        not in {"partial", "supervisor_interrupted", "driver_failure"}
    ):
        raise ValueError("final binding requires a stopped run and clean successor")
    first = {}
    for key, text in snapshots.items():
        if key != "progress.json":
            checkpoint = json.loads(text)
            first[checkpoint["shard_id"]] = (
                None if checkpoint["complete"] else checkpoint["next_global_iteration"]
            )
    contract = {
        "policy": policy_identity(),
        "predecessor_transition_sha256": object_sha256(predecessor),
        "prior_execution_context": prior_context,
        "continuation_execution": {"context": dict(context)},
        "execution_authorized": execution_authorized,
        "checkpoint_sha256": {
            key: hashlib.sha256(text.encode()).hexdigest()
            for key, text in snapshots.items()
        },
        "first_affected_interval": first,
    }
    digest = object_sha256(contract)
    authority = {
        **predecessor["new_authority"],
        "execution_commit": context["git_commit"],
        "source_fingerprint": context["source_fingerprint"],
        "source_version_contract_sha256": digest,
        "recovery_policy": POLICY_NAME,
        "maximum_solver_processes": 3,
    }
    if snapshots != {
        str(path.relative_to(output_root)): path.read_text() for path in paths
    }:
        raise ValueError("checkpoint changed while preparing continuation")
    return {
        "schema_version": 1,
        "classification": CLASSIFICATION,
        "contract": contract,
        "contract_sha256": digest,
        "predecessor_transition": dict(predecessor),
        "prior_authority": predecessor["new_authority"],
        "new_authority": authority,
        "stopping_pointer_json": snapshots,
    }


def load_record(
    output_root: Path, predecessor: Mapping[str, Any]
) -> dict[str, Any] | None:
    path = output_root / RECORD_NAME
    if not path.is_file():
        return None
    record = json.loads(path.read_text())
    contract = record["contract"]
    if (
        record.get("schema_version") != 1
        or record.get("classification") != CLASSIFICATION
        or record.get("predecessor_transition") != predecessor
        or record.get("contract_sha256") != object_sha256(contract)
        or contract.get("policy") != policy_identity()
        or contract.get("predecessor_transition_sha256") != object_sha256(predecessor)
        or contract.get("prior_execution_context")
        != predecessor["contract"]["continuation_execution"]["context"]
        or contract.get("execution_authorized") is not True
    ):
        raise ValueError("speculative continuation identity/approval mismatch")
    context = contract["continuation_execution"]["context"]
    authority = record["new_authority"]
    if (
        context.get("git_clean") is not True
        or authority.get("execution_commit") != context.get("git_commit")
        or authority.get("source_fingerprint") != context.get("source_fingerprint")
        or authority.get("source_version_contract_sha256") != record["contract_sha256"]
        or authority.get("recovery_policy") != POLICY_NAME
        or authority.get("maximum_solver_processes") != 3
        or record.get("prior_authority") != predecessor["new_authority"]
    ):
        raise ValueError("speculative continuation source/authority mismatch")
    snapshots = record["stopping_pointer_json"]
    if "progress.json" not in snapshots or set(snapshots) != set(
        contract["checkpoint_sha256"]
    ):
        raise ValueError("speculative continuation lacks stopping checkpoints")
    for key, text in snapshots.items():
        if key != "progress.json" and (
            not key.startswith("shard-")
            or Path(key).parts != (key.split("/")[0], "checkpoint.json")
        ):
            raise ValueError("invalid stopping checkpoint name")
        if (
            hashlib.sha256(text.encode()).hexdigest()
            != contract["checkpoint_sha256"][key]
        ):
            raise ValueError("stopping checkpoint hash mismatch")
        old = json.loads(text)
        if key == "progress.json":
            continue
        if contract["first_affected_interval"].get(old["shard_id"]) != (
            None if old["complete"] else old["next_global_iteration"]
        ):
            raise ValueError("policy phase begins at the wrong physical boundary")
    return dict(record)


def verify_phase_window(
    record: Mapping[str, Any], directory: Path, window: Mapping[str, Any], index: int
) -> None:
    """Reject new-policy windows before the cutover and old-policy ones after it."""
    text = record["stopping_pointer_json"].get(f"{directory.name}/checkpoint.json")
    count = 0 if text is None else json.loads(text)["completed_intervals"]
    if index < count:
        return  # Historical validation retains its own original policy/authority.
    expected_context = record["contract"]["continuation_execution"]["context"]
    if (
        window.get("schema_version") != 2
        or window.get("policy") != POLICY_NAME
        or window.get("execution_context") != expected_context
        or window.get("source_version_contract_sha256") != record["contract_sha256"]
    ):
        raise ValueError("window does not belong to the reviewed policy phase")
