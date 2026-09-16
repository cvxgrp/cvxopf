"""Selected-window archive, cleanup receipt, and independent reconstruction."""

from __future__ import annotations

from dataclasses import asdict
import gzip
import json
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from cvxopf import HierarchicalInputs, HierarchicalPolicy
from experiments.case118_annual_hierarchy.s5_speculative_archive import (
    audit_candidate,
    invocation,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    FINAL_SOURCE_SLOTS,
    POLICY_NAME,
    select_uncapped_replay,
)
from experiments.case118_annual_hierarchy.s5_speculative_process import artifact_ref
from experiments.case118_annual_hierarchy.s5_speculative_transaction import (
    advance_from_winner,
    publish_winner_window,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_runner import StreamingOuterPlan
from experiments.case118_annual_hierarchy.streaming_schema import (
    WindowIndexEntry,
    atomic_immutable_json,
    sha256_path,
)


def receipt_path(directory: Path, iteration: int) -> Path:
    return directory / f"window-{iteration:06d}-speculative-cleanup.json"


def _attempt_directory(directory: Path, relative: str) -> Path:
    path = (directory.parent / relative).resolve()
    if not path.is_relative_to(directory.parent.resolve()):
        raise ValueError("attempt is outside the retained run")
    return path


def _validate_final_recovery(
    specs: Sequence[AttemptSpec],
    completions: Mapping[str, Mapping[str, Any]],
    lifecycles: Mapping[str, Mapping[str, Any]],
) -> None:
    """Reconstruct final-start provenance and the lane freed before each launch."""
    finals = sorted(
        (spec for spec in specs if 11 <= spec.order <= 18), key=lambda item: item.order
    )
    if not finals:
        return
    primary = next((spec for spec in specs if spec.order == 0), None)
    secondary = next((spec for spec in specs if spec.order == 9), None)
    if primary is None:
        raise ValueError("final recovery lacks its primary contender")
    if len({spec.order for spec in finals}) != len(finals) or any(
        spec.source_slot != FINAL_SOURCE_SLOTS[spec.order - 11] for spec in finals
    ):
        raise ValueError("final recovery source sequence differs from policy")
    for spec in finals:
        launch = lifecycles[spec.attempt_id]["launched_monotonic"]

        def freed(contender: AttemptSpec | None) -> bool:
            if contender is None:
                return True
            completion = completions.get(contender.attempt_id)
            return bool(
                completion is not None
                and completion["outcome"] != "accepted"
                and lifecycles[contender.attempt_id]["reaped_monotonic"] <= launch
            )

        if not (freed(primary) or freed(secondary)):
            raise ValueError("final recovery began without a freed uncapped lane")
        predecessors = [
            item
            for item in completions.values()
            if invocation(item["attempt"]).order < spec.order
            and invocation(item["attempt"]).source_slot == spec.source_slot
            and item["complete_x0_retained"] is True
            and item["outcome"] != "accepted"
        ]
        expected_replay = (
            invocation(
                max(
                    predecessors,
                    key=lambda item: invocation(item["attempt"]).order,
                )["attempt"]
            ).attempt_id
            if predecessors
            else None
        )
        if spec.replay_of != expected_replay:
            raise ValueError("final recovery retained-start replay differs")
        if spec.source_slot in (2, 3, 4, 5):
            accepted_sources = [
                item
                for item in completions.values()
                if invocation(item["attempt"]).source_slot == 1
                and item["outcome"] == "accepted"
                and lifecycles[invocation(item["attempt"]).attempt_id][
                    "reaped_monotonic"
                ]
                <= launch
            ]
            if not accepted_sources:
                raise ValueError("final dependent start lacks accepted target-free")


def publish_window(
    directory: Path,
    winner: AttemptSpec,
    completions: Sequence[Completion],
    candidate_directories: Mapping[str, Path],
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: StreamingOuterPlan,
    context: Mapping[str, object],
    contract_sha256: str,
) -> WindowIndexEntry:
    checkpoint = json.loads((directory / "checkpoint.json").read_text())
    iteration = winner.window.iteration
    stop = min(iteration + policy.ac_window_steps, checkpoint["interval"]["stop"])
    ids = list(checkpoint["storage_device_ids"])
    initial = dict(zip(ids, checkpoint["realized_soc_mwh"], strict=True))
    attempts = []
    accepted = []
    for completion in completions:
        if completion.outcome not in {"accepted", "rejected"}:
            continue
        spec = completion.attempt
        candidate = audit_candidate(
            candidate_directories[spec.attempt_id],
            spec,
            inputs=inputs,
            policy=policy,
            outer=outer,
            initial=initial,
            stop=stop,
        )
        if candidate.completion != completion:
            raise ValueError("decision completion differs from independent audit")
        item = dict(candidate.payload["attempt"])
        item.update(
            {"attempt_id": spec.attempt_id, "supplied_executed_action": spec == winner}
        )
        attempts.append(item)
        if completion.outcome == "accepted" and spec.hard_target:
            accepted.append(spec)
    # Earlier accepted batches cannot exist: the coordinator publishes immediately.
    if not accepted or min(accepted, key=lambda spec: spec.order) != winner:
        raise ValueError("winner is not the first batch's hard-target tie winner")
    controlling = next(
        item for item in attempts if item["attempt_id"] == winner.attempt_id
    )
    result = controlling["result"]
    post = (
        np.array(checkpoint["realized_soc_mwh"])
        - inputs.delta * np.asarray(result["b"])[0]
    ).tolist()
    payload = {
        "schema_version": 2,
        "policy": POLICY_NAME,
        "shard_id": winner.window.shard_id,
        "iteration": iteration,
        "interval_start": iteration,
        "interval_stop": stop,
        "formulation": "ac",
        "result_dimensions": result_dimensions(inputs),
        "storage_device_ids": ids,
        "initial_soc_mwh": checkpoint["realized_soc_mwh"],
        "target_soc_mwh": [outer.target_at(stop)[key] for key in ids],
        "delta_hours": inputs.delta,
        "soc_tolerance_mwh": policy.tolerances.soc_recurrence_mwh_abs,
        "preceding_controlling_attempt_id": checkpoint[
            "preceding_controlling_attempt_id"
        ],
        "attempts": attempts,
        "decision_completions": [asdict(item) for item in completions],
        "candidate_directories": {
            key: str(path.relative_to(directory.parent))
            for key, path in candidate_directories.items()
        },
        "executed_interval": {
            "controlling_attempt_id": winner.attempt_id,
            "b_mw": np.asarray(result["b"])[0].tolist(),
        },
        "post_step_soc_mwh": post,
        "execution_context": dict(context),
        "source_version_contract_sha256": contract_sha256,
    }
    return publish_winner_window(directory, checkpoint, payload)


def finalize_window(
    directory: Path,
    entry: WindowIndexEntry,
    *,
    before_advance: Callable[[Mapping[str, Any]], None] | None = None,
) -> None:
    """Require immutable cleanup for every launched contender before advancement."""
    with gzip.open(directory / entry.relative_path, "rt") as stream:
        window = json.load(stream)
    refs = {}
    for key, relative in window["candidate_directories"].items():
        attempt_dir = _attempt_directory(directory, relative)
        path = attempt_dir / "lifecycle.json"
        lifecycle = json.loads(path.read_text())
        if (
            lifecycle.get("reaped") is not True
            or invocation(lifecycle["invocation"]).attempt_id != key
        ):
            raise ValueError("selected window has an unreaped/mismatched contender")
        refs[key] = {
            **artifact_ref(path),
            "relative_path": str(path.relative_to(directory.parent)),
        }
    payload = {
        "policy": POLICY_NAME,
        "winner_window": asdict(entry),
        "contenders": refs,
    }
    path = receipt_path(directory, entry.iteration)
    if path.exists():
        if json.loads(path.read_text()) != payload:
            raise ValueError("cleanup receipt differs from prior finalization")
    else:
        atomic_immutable_json(path, payload)
    if before_advance is not None:
        before_advance(window)
    advance_from_winner(directory, entry)


def validate_window(
    window: Mapping[str, Any],
    directory: Path,
    *,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    outer: StreamingOuterPlan,
    trajectory_stop: int,
) -> Mapping[str, Any]:
    """Verify new-policy science and lifecycle; no numerical solve or model build."""
    iteration = window["iteration"]
    ids = [str(unit.device_id) for unit in inputs.storage]
    stop = min(iteration + policy.ac_window_steps, trajectory_stop)
    if (
        window.get("schema_version") != 2
        or window.get("policy") != POLICY_NAME
        or window.get("storage_device_ids") != ids
        or window.get("interval_stop") != stop
        or window.get("interval_start") != iteration
        or window.get("delta_hours") != inputs.delta
        or window.get("formulation") != "ac"
        or window.get("result_dimensions") != result_dimensions(inputs)
        or window.get("soc_tolerance_mwh") != policy.tolerances.soc_recurrence_mwh_abs
        or window.get("target_soc_mwh") != [outer.target_at(stop)[key] for key in ids]
    ):
        raise ValueError(
            "speculative window geometry/identity differs from frozen physics"
        )
    receipt = json.loads(receipt_path(directory, iteration).read_text())
    entry = receipt["winner_window"]
    path = directory / entry["relative_path"]
    if (
        entry["iteration"] != iteration
        or entry["bytes"] != path.stat().st_size
        or entry["sha256"] != sha256_path(path)
    ):
        raise ValueError("cleanup receipt does not bind the selected window")
    if set(receipt["contenders"]) != set(window["candidate_directories"]):
        raise ValueError("cleanup receipt omits a launched contender")
    initial = dict(zip(ids, window["initial_soc_mwh"], strict=True))
    elapsed_start, elapsed_stop = float("inf"), 0.0
    role_effort = {"primary": 0.0, "target_free": 0.0, "copied": 0.0, "perturbed": 0.0}
    construction = canceled = effort = post_solve = 0.0
    timeouts = 0
    projected = []
    selected = window["executed_interval"]["controlling_attempt_id"]
    completions = {
        invocation(item["attempt"]).attempt_id: item
        for item in window["decision_completions"]
    }
    winners = []
    specs = []
    lifecycles: dict[str, Mapping[str, Any]] = {}
    for key, relative in window["candidate_directories"].items():
        attempt_dir = _attempt_directory(directory, relative)
        life_path = attempt_dir / "lifecycle.json"
        ref = receipt["contenders"][key]
        if ref != {
            **artifact_ref(life_path),
            "relative_path": str(life_path.relative_to(directory.parent)),
        }:
            raise ValueError("contender lifecycle hash mismatch")
        life = json.loads(life_path.read_text())
        spec = invocation(life["invocation"])
        specs.append(spec)
        lifecycles[key] = life
        if (
            spec.attempt_id != key
            or spec.window.iteration != iteration
            or spec.window.shard_id != window["shard_id"]
            or life["reaped"] is not True
        ):
            raise ValueError("contender lifecycle identity mismatch")
        for name, artifact in life["artifacts"].items():
            if artifact != artifact_ref(attempt_dir / name):
                raise ValueError("contender evidence changed after cleanup")
        launch, reaped = life["launched_monotonic"], life["reaped_monotonic"]
        if (
            not np.isfinite([launch, reaped]).all()
            or reaped < launch
            or life["worker_wall_seconds"] != reaped - launch
        ):
            raise ValueError("invalid contender timing")
        elapsed_start, elapsed_stop = (
            min(elapsed_start, launch),
            max(elapsed_stop, reaped),
        )
        phases = {item["phase"]: item["monotonic_seconds"] for item in life["phases"]}
        if len(phases) != len(life["phases"]):
            raise ValueError("duplicate contender phase")
        times = [launch, *phases.values(), reaped]
        if not np.isfinite(times).all() or times != sorted(times):
            raise ValueError("invalid contender phase clock")
        if "before_ac_build" in phases:
            construction += (
                phases.get("after_ac_build", reaped) - phases["before_ac_build"]
            )
        seconds = (
            0.0
            if "before_ac_solve" not in phases
            else phases.get("after_ac_solve", reaped) - phases["before_ac_solve"]
        )
        effort += seconds
        if "after_ac_solve" in phases:
            post_solve += reaped - phases["after_ac_solve"]
        role = (
            "primary"
            if spec.order == 0
            else "target_free"
            if spec.source_slot == 1
            else "copied"
            if spec.source_slot == 2
            else "perturbed"
        )
        role_effort[role] += seconds
        if life["reason"] != "returned":
            canceled += seconds
        if life["reason"] == "solve_budget" and (
            spec.budget_seconds is None or seconds < spec.budget_seconds
        ):
            raise ValueError("timeout contradicts bounded helper solve clock")
        timeouts += int(life["reason"] == "solve_budget")
        completion = completions.get(key)
        if completion is not None and completion["outcome"] in {"accepted", "rejected"}:
            if life["returncode"] != 0 or life["reason"] != "returned":
                raise ValueError(
                    "decision candidate lacks successful process completion"
                )
            candidate = audit_candidate(
                attempt_dir,
                spec,
                inputs=inputs,
                policy=policy,
                outer=outer,
                initial=initial,
                stop=stop,
            )
            if asdict(candidate.completion) != completion:
                raise ValueError("decision completion does not reconstruct")
            item = dict(candidate.payload["attempt"])
            item.update(
                {"attempt_id": key, "supplied_executed_action": key == selected}
            )
            projected.append(item)
            if candidate.completion.outcome == "accepted" and spec.hard_target:
                winners.append(spec)
    if not winners or min(winners, key=lambda item: item.order).attempt_id != selected:
        raise ValueError("selected candidate violates first accepted batch tie rule")
    if sum(spec.order == 0 for spec in specs) != 1:
        raise ValueError("speculative window must retain exactly one primary")
    replays = [spec for spec in specs if spec.order == 9]
    if replays:
        prior = [
            Completion(
                invocation(item["attempt"]),
                item["outcome"],
                item["complete_x0_retained"],
                item["normalized_residual"],
                item["objective"],
            )
            for item in completions.values()
        ]
        if len(replays) != 1 or select_uncapped_replay(prior) != replays[0]:
            raise ValueError("uncapped replay does not match the frozen ranking rule")
    extended = [spec for spec in specs if spec.order == 10]
    if extended:
        first_free = next((spec for spec in specs if spec.order == 4), None)
        if (
            len(extended) != 1
            or first_free is None
            or extended[0].source_slot != 1
            or extended[0].replay_of != first_free.attempt_id
            or completions[first_free.attempt_id]["outcome"] != "timeout"
        ):
            raise ValueError("extended target-free retry differs from frozen policy")
    _validate_final_recovery(specs, completions, lifecycles)
    if projected != window["attempts"]:
        # Candidate directories use launch order; decision batches can differ.
        if {item["attempt_id"]: item for item in projected} != {
            item["attempt_id"]: item for item in window["attempts"]
        }:
            raise ValueError("window candidate projection mismatch")
    controller = next(item for item in projected if item["attempt_id"] == selected)
    b = np.asarray(controller["result"]["b"])[0]
    expected = np.array(window["initial_soc_mwh"]) - inputs.delta * b
    if not np.array_equal(expected, window["post_step_soc_mwh"]) or not np.array_equal(
        b, window["executed_interval"]["b_mw"]
    ):
        raise ValueError("speculative action advances a different physical state")
    return {
        "timeout_count": timeouts,
        "latency_seconds": elapsed_stop - elapsed_start,
        "solve_effort_seconds": effort,
        "canceled_solve_effort_seconds": canceled,
        "role_solve_effort_seconds": role_effort,
        "construction_seconds": construction,
        "post_solve_seconds": post_solve,
        "selected_source": controller["role"],
        "selected_invocation": selected,
    }
