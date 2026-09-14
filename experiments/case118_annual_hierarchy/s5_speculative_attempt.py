"""Single-attempt construction and pre-solve persistence for speculative S5.

This is an opt-in worker seam, not a launch command or execution authority.
The supervisor owns deadlines, winner publication, and checkpoint advancement.
Model/audit records retain their historical source-slot IDs; the enclosing
invocation identifies actual order and bounded versus uncapped execution.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import json
from pathlib import Path
from typing import Callable, Literal, Mapping, cast

import numpy as np

from cvxopf import (
    ACAttemptRecord,
    HierarchicalInputs,
    HierarchicalPolicy,
    HierarchicalSolveConfig,
    IPOPTStartEvidence,
    OPFBuild,
)
from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.s4b_manifest import object_sha256
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    POLICY_NAME,
    WindowKey,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    _json_value,
    attempt_archive_payload,
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)


@dataclass(frozen=True)
class RetainedStart:
    invocation: AttemptSpec
    request_sha256: str
    raw: Mapping[str, np.ndarray]
    assigned: Mapping[str, np.ndarray]
    evidence: IPOPTStartEvidence
    source_kind: Literal["generated_flat", "attempt"]
    source_attempt_id: str | None


@dataclass(frozen=True)
class AttemptResult:
    """A candidate, not a selected action.

    `record` is the legacy single-attempt evaluation adapter. Its sequential
    `supplied_executed_action` convention is not exported into the speculative
    artifact or used as winner evidence; selection belongs to the coordinator.
    """

    invocation: AttemptSpec
    request_sha256: str
    record: ACAttemptRecord

    def target_free_values(self, request_sha256: str) -> Mapping[str, np.ndarray]:
        if self.request_sha256 != request_sha256 or self.invocation.source_slot != 1:
            raise ValueError(
                "target-free source belongs to a different request or role"
            )
        values = streaming._solution_values(self.record)
        if values is None:
            raise ValueError(
                "target-free source must have an accepted complete solution"
            )
        return values


@dataclass(frozen=True)
class TargetFreeSource:
    """Build-free accepted source, loaded only after an independent audit."""

    invocation: AttemptSpec
    request_sha256: str
    solution_values: Mapping[str, np.ndarray]

    def target_free_values(self, request_sha256: str) -> Mapping[str, np.ndarray]:
        if (
            self.request_sha256 != request_sha256
            or self.invocation.source_slot != 1
            or not self.solution_values
            or any(
                not np.isfinite(value).all() for value in self.solution_values.values()
            )
        ):
            raise ValueError("invalid build-free target-free source")
        return self.solution_values


@dataclass(frozen=True)
class SelectedController:
    """Selected invocation plus the existing build-free physical shift source.

    The source-slot ID is a compatibility identity for the mathematical start
    machinery, not a replacement for the distinct bounded/replayed invocation.
    """

    invocation: AttemptSpec
    source: streaming.CausalControllerSource


def selected_controller(
    result: AttemptResult, winner: AttemptSpec
) -> SelectedController:
    """Detach only the coordinator-selected accepted hard-target result."""
    if result.invocation != winner or not winner.hard_target:
        raise ValueError("candidate is not the selected hard-target invocation")
    record = result.record
    if (
        record.iteration != winner.window.iteration
        or record.ordinal != winner.source_slot
        or record.audit is None
        or not record.audit.accepted_primal
    ):
        raise ValueError("selected candidate lacks matching accepted evidence")
    return SelectedController(winner, streaming.causal_source_from_attempt(record))


@dataclass(frozen=True)
class PreparedAttempt:
    invocation: AttemptSpec
    request_sha256: str
    build: OPFBuild
    initial: Mapping[str, float]
    target: Mapping[str, float]
    stop: int
    raw: Mapping[str, np.ndarray]
    assigned: Mapping[str, np.ndarray]
    source_kind: Literal["generated_flat", "attempt"]
    source_attempt_id: str | None
    replay_start: IPOPTStartEvidence | None


def prepare_attempt(
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
    outer: streaming.StreamingOuterPlan,
    invocation: AttemptSpec,
    realized_soc_mwh: Mapping[str, float],
    preceding: streaming.CausalControllerSource | SelectedController | None,
    *,
    trajectory_start: int,
    trajectory_stop: int,
    trajectory_initial_soc_mwh: Mapping[str, float],
    target_free: AttemptResult | TargetFreeSource | None = None,
    replay: RetainedStart | None = None,
) -> PreparedAttempt:
    """Build exactly one attempt using the shared physical and start functions."""
    iteration = invocation.window.iteration
    preceding_invocation_id: str | None = None
    if isinstance(preceding, SelectedController):
        if (
            preceding.invocation.window.shard_id != invocation.window.shard_id
            or preceding.invocation.window.iteration != iteration - 1
            or preceding.source.ordinal != preceding.invocation.source_slot
            or not preceding.invocation.hard_target
        ):
            raise ValueError("selected source belongs to a different shard/window/role")
        preceding_invocation_id = preceding.invocation.attempt_id
        preceding = preceding.source
    stop, initial, target = streaming.validate_window_request(
        inputs,
        policy,
        solve_config,
        outer,
        iteration,
        realized_soc_mwh,
        preceding,
        trajectory_start=trajectory_start,
        trajectory_stop=trajectory_stop,
        trajectory_initial_soc_mwh=trajectory_initial_soc_mwh,
    )
    request_sha256 = object_sha256(
        {
            "window": asdict(invocation.window),
            "stop": stop,
            "trajectory_start": trajectory_start,
            "trajectory_stop": trajectory_stop,
            "initial": initial,
            "target": target,
            "outer_plan_id": outer.outer_plan_id,
            "signpost_sha256": outer.signpost_sha256,
            "input_fingerprint": outer.input_fingerprint,
            "policy_sha256": outer.policy_sha256,
            "solve_config_sha256": outer.solve_config_sha256,
            "preceding_source": None
            if preceding is None
            else {
                "attempt_id": preceding.attempt_id,
                "invocation_id": preceding_invocation_id,
                "solution_values": _json_value(preceding.solution_values),
            },
        }
    )
    slot = invocation.source_slot
    if invocation.order == 9:
        if replay is None or (
            replay.invocation.attempt_id != invocation.replay_of
            or replay.invocation.source_slot != slot
            or replay.invocation.window != invocation.window
            or not 1 <= replay.invocation.order <= 8
            or replay.request_sha256 != request_sha256
        ):
            raise ValueError("uncapped replay requires the exact bounded start/request")
    elif replay is not None:
        raise ValueError("bounded/primary invocation cannot receive a replay start")
    if slot >= 6 and preceding is None:
        raise ValueError("causal perturbation requires a preceding controller")
    if slot in (2, 3, 4, 5) and replay is None:
        if target_free is None or target_free.invocation.window != invocation.window:
            raise ValueError("accepted target-free source is unavailable")
        target_free.target_free_values(request_sha256)

    storage = streaming._inner_storage(
        inputs, initial, target if invocation.hard_target else None
    )
    build = streaming.build_window(inputs, "ac", iteration, stop, storage)
    source_kind: Literal["generated_flat", "attempt"]
    if replay is not None:
        raw = {name: np.asarray(value).copy() for name, value in replay.raw.items()}
        assigned = {
            name: np.asarray(value).copy() for name, value in replay.assigned.items()
        }
        source_kind, source_id = replay.source_kind, replay.source_attempt_id
    elif slot in (2, 3, 4, 5):
        if target_free is None:
            raise RuntimeError("validated target-free source disappeared")
        center = target_free.target_free_values(request_sha256)
        source_kind, source_id = "attempt", target_free.invocation.attempt_id
        if slot == 2:
            raw = {name: value.copy() for name, value in center.items()}
            assigned = {name: value.copy() for name, value in center.items()}
        else:
            assert invocation.scale is not None and invocation.seed is not None
            raw, assigned = streaming.perturbed_start(
                center, build, scale=invocation.scale, seed=invocation.seed
            )
    else:
        if preceding is None:
            raw = streaming.complete_flat_start(build)
            assigned = raw
            source_kind, source_id = "generated_flat", None
        else:
            raw, assigned = streaming.shifted_start(
                preceding.solution_values, build, inputs, policy, initial
            )
            source_kind, source_id = (
                "attempt",
                preceding_invocation_id or preceding.attempt_id,
            )
        if slot >= 6:
            assert invocation.scale is not None and invocation.seed is not None
            raw, assigned = streaming.perturbed_start(
                assigned, build, scale=invocation.scale, seed=invocation.seed
            )
    streaming.assign_start(build, assigned)
    return PreparedAttempt(
        invocation,
        request_sha256,
        build,
        initial,
        target,
        stop,
        raw,
        assigned,
        source_kind,
        source_id,
        None if replay is None else replay.evidence,
    )


def start_payload(
    prepared: PreparedAttempt, evidence: IPOPTStartEvidence
) -> dict[str, object]:
    """A complete restart packet published before entering native IPOPT."""
    return {
        "schema_version": 1,
        "policy": POLICY_NAME,
        "kind": "speculative_attempt_start",
        "invocation": asdict(prepared.invocation),
        "request_sha256": prepared.request_sha256,
        "raw_start": _json_value(prepared.raw),
        "assigned_start": _json_value(prepared.assigned),
        "source_kind": prepared.source_kind,
        "source_attempt_id": prepared.source_attempt_id,
        "complete_x0": evidence.complete_x0.tolist(),
        "layout": _json_value(evidence.layout),
        "layout_signature": evidence.layout_signature,
        "model_coordinate_count": evidence.model_coordinate_count,
        "auxiliary_coordinate_count": evidence.auxiliary_coordinate_count,
        "object_ids_before": _json_value(evidence.object_ids_before),
        "object_ids_after": _json_value(evidence.object_ids_after),
    }


def load_retained_start(path: Path) -> RetainedStart:
    """Restore a local start packet; validate full named/canonical agreement."""
    payload = json.loads(path.read_text())
    if (
        payload.get("schema_version") != 1
        or payload.get("policy") != POLICY_NAME
        or payload.get("kind") != "speculative_attempt_start"
    ):
        raise ValueError("unsupported speculative start packet")
    spec = dict(payload["invocation"])
    spec["window"] = WindowKey(**spec["window"])
    invocation = AttemptSpec(**spec)
    raw = {
        name: np.asarray(value, dtype=float)
        for name, value in payload["raw_start"].items()
    }
    assigned = {
        name: np.asarray(value, dtype=float)
        for name, value in payload["assigned_start"].items()
    }
    evidence = IPOPTStartEvidence(
        complete_x0=np.asarray(payload["complete_x0"], dtype=float),
        layout=tuple(payload["layout"]),
        layout_signature=payload["layout_signature"],
        model_coordinate_count=payload["model_coordinate_count"],
        auxiliary_coordinate_count=payload["auxiliary_coordinate_count"],
        object_ids_before={
            k: tuple(v) for k, v in payload["object_ids_before"].items()
        },
        object_ids_after={k: tuple(v) for k, v in payload["object_ids_after"].items()},
    )
    if streaming._layout_signature(evidence.layout) != evidence.layout_signature:
        raise ValueError("retained start layout signature mismatch")
    original_names = [
        str(item["name"]) for item in evidence.layout if item["is_original_variable"]
    ]
    if (
        not assigned
        or set(raw) != set(assigned)
        or len(original_names) != len(set(original_names))
        or set(original_names) != set(assigned)
    ):
        raise ValueError("retained named-start namespaces differ")
    for item in evidence.layout:
        shape = tuple(cast(list[int], item["shape"]))
        first, last = int(cast(int, item["start"])), int(cast(int, item["stop"]))
        if (int(np.prod(shape)) if shape else 1) != last - first:
            raise ValueError("retained start layout shape mismatch")
        if item["is_original_variable"]:
            name = str(item["name"])
            if (
                raw[name].shape != shape
                or assigned[name].shape != shape
                or not np.all(np.isfinite(raw[name]))
                or not np.array_equal(
                    assigned[name].flatten(order="F"), evidence.complete_x0[first:last]
                )
            ):
                raise ValueError("retained named start disagrees with complete x0")
    kind = payload["source_kind"]
    source_id = payload["source_attempt_id"]
    if kind not in {"generated_flat", "attempt"} or (
        (kind == "generated_flat" and source_id is not None)
        or (kind == "attempt" and (not isinstance(source_id, str) or not source_id))
    ):
        raise ValueError("retained start source identity is invalid")
    return RetainedStart(
        invocation,
        str(payload["request_sha256"]),
        raw,
        assigned,
        evidence,
        kind,
        source_id,
    )


def execute_prepared_attempt(
    prepared: PreparedAttempt,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
    outer: streaming.StreamingOuterPlan,
    *,
    start_path: Path,
    result_path: Path,
    phase_observer: Callable[[str, int, int], None] | None = None,
) -> AttemptResult:
    """Run one invocation; never elect a winner or advance state.

    Runtime supervision must supply the declared budget externally. This
    function is not exposed through the annual runner until that is connected.
    Separate immutable paths are required for each invocation/replay.
    """
    if start_path.parent.resolve() != result_path.parent.resolve():
        raise ValueError("start and result must share one invocation directory")
    if start_path == result_path or start_path.exists() or result_path.exists():
        raise FileExistsError("speculative invocation output must be fresh")
    spec = prepared.invocation
    slot = streaming._p0_registry(spec.window.iteration)[spec.source_slot]
    if spec.source_slot in (0, 1):
        slot = replace(
            slot,
            transformation=(
                "flat"
                if prepared.source_kind == "generated_flat"
                else "shifted_preceding"
            ),
        )

    def retain_start(evidence: IPOPTStartEvidence) -> None:
        atomic_immutable_json(start_path, start_payload(prepared, evidence))

    record = streaming._execute_attempt(
        inputs,
        policy,
        solve_config,
        outer,
        spec.window.iteration,
        prepared.stop,
        prepared.initial,
        prepared.target,
        slot,
        target_free=not spec.hard_target,
        raw_start=prepared.raw,
        assigned_start=prepared.assigned,
        source_kind=prepared.source_kind,
        source_attempt_id=prepared.source_attempt_id,
        prebuilt=prepared.build,
        phase_observer=phase_observer,
        start_observer=retain_start,
        replay_start=prepared.replay_start,
    )
    result = AttemptResult(spec, prepared.request_sha256, record)
    candidate = attempt_archive_payload(
        record, result_dimensions=result_dimensions(inputs)
    )
    # These two fields encode the old sequential acceptance=selection rule.
    # Do not export them into a new-policy candidate artifact.
    del candidate["supplied_executed_action"]
    del candidate["causal_source"]
    payload = {
        "schema_version": 1,
        "policy": POLICY_NAME,
        "kind": "speculative_attempt_result",
        "invocation": asdict(spec),
        "request_sha256": prepared.request_sha256,
        "start_artifact": (
            {
                "relative_path": start_path.name,
                "bytes": start_path.stat().st_size,
                "sha256": sha256_path(start_path),
            }
            if start_path.exists()
            else None
        ),
        "selected_for_execution": False,
        "attempt": candidate,
        "solution_values": _json_value(streaming._solution_values(record)),
    }
    atomic_immutable_json(result_path, payload)
    return result
