"""Build-free S4b shard persistence, reconstruction, and deterministic merge."""

from __future__ import annotations

from copy import deepcopy
import gzip
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.p0_fixture import frozen_p0_policy
from experiments.case118_annual_hierarchy.s2_analysis import _interval_metrics
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    PRIMARY_ATTEMPT_BUDGET_SECONDS,
    S4B_MANIFEST_PATH,
    STORAGE_DEVICE_IDS,
    canonical_json,
    load_verified_manifest,
    object_sha256,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    outer_boundaries,
    residual_tolerances,
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_runner import StreamingOuterPlan
from experiments.case118_annual_hierarchy.streaming_schema import (
    WindowIndexEntry,
    atomic_json,
    sha256_path,
    validate_window_archive,
)


SCHEMA_VERSION = 1
AUTHORITY_FILENAME = "S4B_EXECUTION_AUTHORITY.json"
QUALIFICATION_INTERVALS = (
    ("s4b-qualification-ordinary", 0, 24, "ordinary"),
    ("s4b-qualification-partition-a", 0, 12, "partitioned"),
    ("s4b-qualification-partition-b", 12, 24, "partitioned"),
)
EXPECTED_QUALIFICATION_REGISTRY_SHA256 = (
    "e9e695346df50af4d663454f9f750ec7b2c81d4ee1b6bbcb0e4c65f2884d7a3a"
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{label} must be a sequence")
    return cast(Sequence[object], value)


def _finite_vector(value: object, label: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != (len(STORAGE_DEVICE_IDS),) or not np.all(np.isfinite(array)):
        raise ValueError(f"{label} must be one finite value per storage device")
    return array


def load_qualification_authority(
    path: Path,
    *,
    expected_execution_commit: str,
    expected_source_fingerprint: str,
) -> Mapping[str, object]:
    """Load the separate reviewed authority required before any S4b process starts."""
    if not path.is_file():
        raise ValueError("S4b numerical execution remains unauthorized")
    value = _mapping(json.loads(path.read_text()), "S4b execution authority")
    expected = {
        "schema_version": SCHEMA_VERSION,
        "classification": "reviewed_s4b_qualification_execution_authorized",
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "qualification_registry_sha256": EXPECTED_QUALIFICATION_REGISTRY_SHA256,
        "qualification_execution_authorized": True,
        "annual_execution_authorized": False,
        "execution_scope": "bounded_24h_qualification",
        "execution_commit": expected_execution_commit,
        "source_fingerprint": expected_source_fingerprint,
    }
    if value != expected:
        raise ValueError("S4b execution authority does not match the frozen run")
    return value


def qualification_registry(outer: StreamingOuterPlan) -> Mapping[str, object]:
    """Materialize the frozen 24-hour ordinary/partitioned qualification registry."""
    annual = _mapping(load_verified_manifest()["manifest"], "annual manifest")
    template = deepcopy(
        _mapping(_sequence(annual["shards"], "annual shards")[0], "annual shard")
    )
    storage_template = _mapping(template["storage"], "storage")
    boundary = outer.boundary_soc_mwh
    if boundary is None or boundary.shape[0] < 25:
        raise ValueError("accepted outer plan lacks 24-hour qualification signposts")
    ids = tuple(
        str(item) for item in _sequence(storage_template["device_ids"], "storage IDs")
    )
    shards: list[dict[str, object]] = []
    for ordinal, (shard_id, start, stop, policy_arm) in enumerate(
        QUALIFICATION_INTERVALS
    ):
        shard = cast(dict[str, object], deepcopy(template))
        initial_state: dict[str, object] = {
            "global_boundary": start,
            "storage_device_ids": list(ids),
            "soc_mwh": boundary[start].tolist(),
        }
        terminal_state: dict[str, object] = {
            "global_boundary": stop,
            "storage_device_ids": list(ids),
            "soc_mwh": boundary[stop].tolist(),
        }
        initial_state["boundary_sha256"] = object_sha256(initial_state)
        terminal_state["boundary_sha256"] = object_sha256(terminal_state)
        shard.update(
            {
                "shard_id": shard_id,
                "ordinal": ordinal,
                "qualification_arm": policy_arm,
                "interval": {"start": start, "stop": stop, "half_open": True},
                "predecessor_boundary_sha256": (
                    None if start == 0 else initial_state["boundary_sha256"]
                ),
                "successor_boundary_sha256": terminal_state["boundary_sha256"],
                "locations": {
                    "output_directory": (
                        "experiments/case118_annual_hierarchy/results/"
                        f"s4b_qualification/{shard_id}"
                    ),
                    "checkpoint": (
                        "experiments/case118_annual_hierarchy/results/"
                        f"s4b_qualification/{shard_id}/checkpoint.json"
                    ),
                },
                "run_locations": {
                    mode: (
                        "experiments/case118_annual_hierarchy/results/"
                        f"s4b_qualification/{mode}/{shard_id}"
                    )
                    for mode in (
                        ("ordinary",)
                        if policy_arm == "ordinary"
                        else (
                            "partitioned_one_process",
                            "partitioned_fresh_sequential",
                            "partitioned_fresh_concurrent",
                        )
                    )
                },
            }
        )
        storage = cast(dict[str, object], shard["storage"])
        storage["initial_state"] = initial_state
        storage["terminal_state"] = terminal_state
        shards.append(shard)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "classification": "bounded_qualification_registry",
        "annual_manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "horizon_steps": 24,
        "forced_boundary": 12,
        "shards": shards,
    }
    registry = {**payload, "registry_sha256": object_sha256(payload)}
    if registry["registry_sha256"] != EXPECTED_QUALIFICATION_REGISTRY_SHA256:
        raise ValueError(
            "S4b qualification registry drifted from frozen identity: "
            f"{registry['registry_sha256']}"
        )
    return registry


def qualification_shard_entry(
    shard_id: str, outer: StreamingOuterPlan
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Select one bounded qualification shard, never an annual shard."""
    registry = qualification_registry(outer)
    matches = [
        _mapping(item, "qualification shard")
        for item in _sequence(registry["shards"], "qualification shards")
        if _mapping(item, "qualification shard").get("shard_id") == shard_id
    ]
    if len(matches) != 1:
        raise ValueError(f"unknown S4b qualification shard {shard_id!r}")
    return registry, matches[0]


def shard_entry(
    shard_id: str,
    *,
    manifest_path: Path = S4B_MANIFEST_PATH,
) -> tuple[Mapping[str, object], Mapping[str, object]]:
    """Return one uniquely selected entry from the verified immutable manifest."""
    envelope = load_verified_manifest(manifest_path)
    manifest = _mapping(envelope["manifest"], "S4b manifest")
    matches = [
        _mapping(item, "S4b shard")
        for item in _sequence(manifest.get("shards"), "S4b shards")
        if _mapping(item, "S4b shard").get("shard_id") == shard_id
    ]
    if len(matches) != 1:
        raise ValueError("S4b shard ID is absent or duplicated")
    return envelope, matches[0]


def shard_checkpoint_payload(
    *,
    shard: Mapping[str, object],
    execution_source_fingerprint: str,
    outer_plan_sha256: str,
    execution_mode: str,
    realized_soc_mwh: Sequence[float],
    preceding_controlling_attempt_id: str | None,
    windows: Sequence[WindowIndexEntry],
) -> dict[str, object]:
    """Create the one atomic global-coordinate shard checkpoint."""
    interval = _mapping(shard.get("interval"), "shard interval")
    start = int(cast(int, interval["start"]))
    stop = int(cast(int, interval["stop"]))
    storage = _mapping(shard.get("storage"), "shard storage")
    completed = len(windows)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "qualification_registry_sha256": EXPECTED_QUALIFICATION_REGISTRY_SHA256,
        "shard_id": shard["shard_id"],
        "ordinal": shard["ordinal"],
        "interval": dict(interval),
        "execution_source_fingerprint": execution_source_fingerprint,
        "outer_plan_sha256": outer_plan_sha256,
        "execution_mode": execution_mode,
        "storage_device_ids": list(STORAGE_DEVICE_IDS),
        "initial_soc_mwh": _mapping(storage.get("initial_state"), "initial state")[
            "soc_mwh"
        ],
        "terminal_soc_mwh": _mapping(storage.get("terminal_state"), "terminal state")[
            "soc_mwh"
        ],
        "realized_soc_mwh": list(realized_soc_mwh),
        "completed_intervals": completed,
        "next_global_iteration": start + completed,
        "preceding_controlling_attempt_id": preceding_controlling_attempt_id,
        "windows": [entry.__dict__ for entry in windows],
        "complete": start + completed == stop,
    }
    validate_shard_checkpoint(payload, shard=shard)
    return payload


def validate_shard_checkpoint(
    value: object,
    *,
    shard: Mapping[str, object],
) -> Mapping[str, object]:
    """Validate checkpoint identity and its global contiguous window registry."""
    checkpoint = _mapping(value, "S4b shard checkpoint")
    interval = _mapping(shard.get("interval"), "shard interval")
    start = int(cast(int, interval["start"]))
    stop = int(cast(int, interval["stop"]))
    windows = _sequence(checkpoint.get("windows"), "checkpoint windows")
    completed = checkpoint.get("completed_intervals")
    if (
        checkpoint.get("schema_version") != SCHEMA_VERSION
        or checkpoint.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or checkpoint.get("qualification_registry_sha256")
        != EXPECTED_QUALIFICATION_REGISTRY_SHA256
        or checkpoint.get("shard_id") != shard.get("shard_id")
        or checkpoint.get("ordinal") != shard.get("ordinal")
        or checkpoint.get("interval") != interval
        or checkpoint.get("storage_device_ids") != list(STORAGE_DEVICE_IDS)
        or not isinstance(checkpoint.get("execution_source_fingerprint"), str)
        or not checkpoint["execution_source_fingerprint"]
        or not isinstance(checkpoint.get("outer_plan_sha256"), str)
        or len(cast(str, checkpoint["outer_plan_sha256"])) != 64
        or checkpoint.get("execution_mode")
        not in _mapping(shard.get("run_locations"), "qualification run locations")
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or completed != len(windows)
        or not 0 <= completed <= stop - start
        or checkpoint.get("next_global_iteration") != start + completed
        or checkpoint.get("complete") is not (completed == stop - start)
    ):
        raise ValueError("S4b shard checkpoint identity mismatch")
    storage = _mapping(shard.get("storage"), "shard storage")
    initial = _finite_vector(
        checkpoint.get("initial_soc_mwh"), "checkpoint initial SoC"
    )
    terminal = _finite_vector(
        checkpoint.get("terminal_soc_mwh"), "checkpoint terminal SoC"
    )
    realized = _finite_vector(
        checkpoint.get("realized_soc_mwh"), "checkpoint realized SoC"
    )
    if not np.array_equal(
        initial,
        _finite_vector(
            _mapping(storage["initial_state"], "manifest initial state")["soc_mwh"],
            "manifest initial SoC",
        ),
    ) or not np.array_equal(
        terminal,
        _finite_vector(
            _mapping(storage["terminal_state"], "manifest terminal state")["soc_mwh"],
            "manifest terminal SoC",
        ),
    ):
        raise ValueError("S4b checkpoint boundary state differs from manifest")
    paths: set[str] = set()
    for offset, raw in enumerate(windows):
        entry = _mapping(raw, "checkpoint window entry")
        expected_iteration = start + offset
        relative = entry.get("relative_path")
        pure = PurePosixPath(str(relative))
        if (
            entry.get("iteration") != expected_iteration
            or not isinstance(relative, str)
            or not relative
            or pure.is_absolute()
            or any(part in {"", ".", ".."} for part in pure.parts)
            or pure.as_posix() != relative
            or relative in paths
            or not isinstance(entry.get("bytes"), int)
            or cast(int, entry["bytes"]) <= 0
            or not isinstance(entry.get("sha256"), str)
            or len(cast(str, entry["sha256"])) != 64
        ):
            raise ValueError("S4b checkpoint window registry mismatch")
        paths.add(relative)
    if completed == 0:
        if checkpoint.get("preceding_controlling_attempt_id") is not None:
            raise ValueError("empty S4b checkpoint cannot name a controller")
        if not np.array_equal(realized, initial):
            raise ValueError("empty S4b checkpoint changed its initial state")
    else:
        predecessor = checkpoint.get("preceding_controlling_attempt_id")
        if not isinstance(predecessor, str) or not predecessor:
            raise ValueError("advanced S4b checkpoint lacks its controller identity")
    return checkpoint


def write_shard_checkpoint(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically replace only the resumable checkpoint pointer."""
    atomic_json(path, payload)
    if json.loads(path.read_text()) != payload:
        raise RuntimeError("S4b checkpoint changed during publication")


def verify_shard_artifacts(
    directory: Path,
    *,
    shard: Mapping[str, object],
    outer: StreamingOuterPlan,
) -> tuple[Mapping[str, object], tuple[Mapping[str, object], ...]]:
    """Independently verify a shard checkpoint and every immutable window."""
    checkpoint_path = directory / "checkpoint.json"
    checkpoint = validate_shard_checkpoint(
        json.loads(checkpoint_path.read_text()), shard=shard
    )
    fixture = load_s4_fixture()
    policy = frozen_p0_policy()
    stop = int(cast(int, _mapping(shard["interval"], "shard interval")["stop"]))
    start = int(cast(int, _mapping(shard["interval"], "shard interval")["start"]))
    expected_state = _finite_vector(checkpoint["initial_soc_mwh"], "initial SoC")
    preceding_id: str | None = None
    archives: list[Mapping[str, object]] = []
    for raw_entry in _sequence(checkpoint["windows"], "checkpoint windows"):
        entry = _mapping(raw_entry, "window entry")
        path = (directory / str(entry["relative_path"])).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("S4b window path escapes its shard directory")
        if (
            not path.is_file()
            or path.stat().st_size != entry["bytes"]
            or sha256_path(path) != entry["sha256"]
        ):
            raise ValueError("S4b window artifact integrity mismatch")
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            archive = validate_window_archive(
                json.load(stream),
                expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
                expected_residual_tolerances=residual_tolerances(policy),
                expected_inner_terminal_policy=policy.inner_terminal_policy,
                expected_horizon_steps=stop,
                expected_ac_window_steps=policy.ac_window_steps,
                expected_result_dimensions=result_dimensions(fixture.inputs),
                expected_delta_hours=fixture.inputs.delta,
                expected_outer_boundary_soc_mwh=outer_boundaries(outer),
                expected_trajectory_start=start,
                expected_primary_timeout_seconds=PRIMARY_ATTEMPT_BUDGET_SECONDS,
            )
        if archive["iteration"] != entry["iteration"]:
            raise ValueError("S4b checkpoint/window iteration mismatch")
        if archive["preceding_controlling_attempt_id"] != preceding_id:
            raise ValueError("S4b controller chain is discontinuous")
        initial = _finite_vector(archive["initial_soc_mwh"], "window initial SoC")
        if (
            np.max(np.abs(initial - expected_state))
            > policy.tolerances.soc_recurrence_mwh_abs
        ):
            raise ValueError("S4b realized-state chain is discontinuous")
        executed = _mapping(archive["executed_interval"], "executed interval")
        preceding_id = str(executed["controlling_attempt_id"])
        expected_state = _finite_vector(archive["post_step_soc_mwh"], "post-step SoC")
        archives.append(archive)
    if preceding_id != checkpoint["preceding_controlling_attempt_id"]:
        raise ValueError("S4b checkpoint controller does not match its archive chain")
    if (
        np.max(
            np.abs(
                expected_state
                - _finite_vector(
                    checkpoint["realized_soc_mwh"], "checkpoint realized SoC"
                )
            )
        )
        > policy.tolerances.soc_recurrence_mwh_abs
    ):
        raise ValueError("S4b checkpoint final state does not match its archives")
    if (
        checkpoint["complete"]
        and np.max(
            np.abs(
                expected_state
                - _finite_vector(checkpoint["terminal_soc_mwh"], "terminal SoC")
            )
        )
        > policy.tolerances.terminal_soc_mwh_abs
    ):
        raise ValueError("completed S4b shard missed its manifest terminal state")
    return checkpoint, tuple(archives)


def audit_shard(
    directory: Path,
    *,
    shard: Mapping[str, object],
    outer: StreamingOuterPlan,
) -> Mapping[str, object]:
    """Reconstruct scientific metrics from retained results, never worker labels."""
    checkpoint, archives = verify_shard_artifacts(directory, shard=shard, outer=outer)
    fixture = load_s4_fixture()
    metrics = [_interval_metrics(archive, fixture.inputs) for archive in archives]
    timeouts = sum(
        1
        for archive in archives
        for attempt in cast(Sequence[Mapping[str, object]], archive["attempts"])
        if attempt["slot_state"] == "timeout"
    )
    recoveries = sum(
        cast(Mapping[str, object], archive["executed_interval"])[
            "controlling_attempt_id"
        ]
        != cast(Sequence[Mapping[str, object]], archive["attempts"])[0]["attempt_id"]
        for archive in archives
    )
    orchestration_seconds = 0.0
    recovery_seconds = 0.0
    solver_seconds = 0.0
    primary_solver_seconds = 0.0
    target_free_solver_seconds = 0.0
    copied_solver_seconds = 0.0
    construction_seconds = 0.0
    storage_throughput = 0.0
    signpost_deviation = 0.0
    shifted_opportunities = 0
    shifted_successes = 0
    for archive in archives:
        iteration = int(cast(int, archive["iteration"]))
        attempts = cast(Sequence[Mapping[str, object]], archive["attempts"])
        primary = attempts[0]
        timed_out = primary["slot_state"] == "timeout"
        supervision_path = directory / (
            f"window-supervision-{iteration:06d}-"
            f"{'timeout' if timed_out else 'primary'}.json"
        )
        supervision = _mapping(
            json.loads(supervision_path.read_text()), "window supervision"
        )
        if (
            supervision.get("iteration") != iteration
            or supervision.get("primary_budget_seconds")
            != PRIMARY_ATTEMPT_BUDGET_SECONDS
            or supervision.get("classification")
            != ("timeout" if timed_out else "completed")
        ):
            raise ValueError("S4b timeout/archive supervision evidence disagrees")
        phase_path = directory / str(supervision["phase_record"])
        if not phase_path.is_file() or supervision.get(
            "phase_record_sha256"
        ) != sha256_path(phase_path):
            raise ValueError("S4b primary phase evidence is missing or corrupt")
        phase_events = _sequence(
            _mapping(json.loads(phase_path.read_text()), "primary phases")["events"],
            "primary phase events",
        )
        phase_times = {
            (
                str(_mapping(item, "primary phase")["phase"]),
                int(cast(int, _mapping(item, "primary phase")["attempt_ordinal"])),
            ): float(cast(float, _mapping(item, "primary phase")["monotonic_seconds"]))
            for item in phase_events
        }
        for ordinal in range(9):
            before = phase_times.get(("before_ac_build", ordinal))
            after = phase_times.get(("after_ac_build", ordinal))
            if before is not None and after is not None:
                construction_seconds += after - before
        primary_started = any(
            _mapping(item, "primary phase").get("phase") == "before_ac_solve"
            and _mapping(item, "primary phase").get("attempt_ordinal") == 0
            for item in phase_events
        )
        primary_completed = any(
            _mapping(item, "primary phase").get("phase") == "after_ac_solve"
            and _mapping(item, "primary phase").get("attempt_ordinal") == 0
            for item in phase_events
        )
        if (
            not primary_started
            or (timed_out and primary_completed)
            or (not timed_out and not primary_completed)
        ):
            raise ValueError("S4b primary phase lifecycle contradicts timeout state")
        orchestration_seconds += float(
            cast(float, supervision["orchestration_wall_seconds"])
        )
        if timed_out:
            recovery_path = directory / f"window-recovery-{iteration:06d}.json"
            recovery = _mapping(
                json.loads(recovery_path.read_text()), "window recovery supervision"
            )
            if (
                recovery.get("iteration") != iteration
                or recovery.get("returncode") != 0
                or recovery.get("timeout_supervision_sha256")
                != sha256_path(supervision_path)
            ):
                raise ValueError("S4b timeout recovery evidence is not linked")
            if (
                supervision.get("returncode") == 0
                or supervision.get("primary_budget_consumed_seconds")
                != PRIMARY_ATTEMPT_BUDGET_SECONDS
                or recovery.get("phase_record_sha256") is None
            ):
                raise ValueError("S4b timeout/recovery timing evidence is incomplete")
            recovery_phase_path = directory / str(recovery["phase_record"])
            if not recovery_phase_path.is_file() or recovery[
                "phase_record_sha256"
            ] != sha256_path(recovery_phase_path):
                raise ValueError("S4b recovery phase evidence is missing or corrupt")
            recovery_events = _sequence(
                _mapping(
                    json.loads(recovery_phase_path.read_text()), "recovery phases"
                )["events"],
                "recovery phase events",
            )
            recovery_phase_times = {
                (
                    str(_mapping(item, "recovery phase")["phase"]),
                    int(cast(int, _mapping(item, "recovery phase")["attempt_ordinal"])),
                ): float(
                    cast(float, _mapping(item, "recovery phase")["monotonic_seconds"])
                )
                for item in recovery_events
            }
            for ordinal in range(9):
                before = recovery_phase_times.get(("before_ac_build", ordinal))
                after = recovery_phase_times.get(("after_ac_build", ordinal))
                if before is not None and after is not None:
                    construction_seconds += after - before
            solve_events = [
                (
                    _mapping(item, "recovery phase")["phase"],
                    _mapping(item, "recovery phase")["attempt_ordinal"],
                )
                for item in recovery_events
                if _mapping(item, "recovery phase")["phase"]
                in {"before_ac_solve", "after_ac_solve"}
            ]
            if solve_events[:4] != [
                ("before_ac_solve", 1),
                ("after_ac_solve", 1),
                ("before_ac_solve", 2),
                ("after_ac_solve", 2),
            ]:
                raise ValueError("S4b recovery solve phases violate frozen ordering")
            executed_id = _mapping(archive["executed_interval"], "executed interval")[
                "controlling_attempt_id"
            ]
            if (
                attempts[1]["slot_state"] != "executed"
                or _mapping(attempts[1]["audit"], "target-free audit")[
                    "accepted_primal"
                ]
                is not True
                or attempts[2]["attempt_id"] != executed_id
            ):
                raise ValueError("S4b timeout recovery controller chain is invalid")
            recovery_seconds += float(cast(float, recovery["wall_seconds"]))
        if iteration > int(cast(int, _mapping(shard["interval"], "interval")["start"])):
            shifted_opportunities += 1
            shifted_successes += int(
                primary["slot_state"] == "executed"
                and _mapping(primary["audit"], "primary audit")["accepted_primal"]
                is True
            )
        controlling_id = _mapping(archive["executed_interval"], "executed interval")[
            "controlling_attempt_id"
        ]
        controlling = next(
            item for item in attempts if item["attempt_id"] == controlling_id
        )
        result = _mapping(controlling["result"], "controlling result")
        audit = _mapping(controlling["audit"], "controlling audit")
        solver_seconds += float(cast(float, audit["wall_time_seconds"]))
        for ordinal, name in (
            (0, "primary"),
            (1, "target_free"),
            (2, "copied"),
        ):
            attempt = attempts[ordinal]
            if attempt["slot_state"] == "executed":
                elapsed = float(
                    cast(
                        float,
                        _mapping(attempt["audit"], f"{name} audit")[
                            "wall_time_seconds"
                        ],
                    )
                )
                if ordinal == 0:
                    primary_solver_seconds += elapsed
                elif ordinal == 1:
                    target_free_solver_seconds += elapsed
                else:
                    copied_solver_seconds += elapsed
        storage_throughput += float(
            np.sum(np.abs(np.asarray(result["b"], dtype=float)[0]))
            * fixture.inputs.delta
        )
        final_soc = np.asarray(result["soc"], dtype=float)[-1]
        target = np.asarray(archive["target_soc_mwh"], dtype=float)
        signpost_deviation += float(np.sum(np.abs(final_soc - target)))
    storage = _mapping(shard["storage"], "shard storage")
    initial_state = _mapping(storage["initial_state"], "shard initial state")
    terminal_state = _mapping(storage["terminal_state"], "shard terminal state")
    terminal_deviation = float(
        np.max(
            np.abs(
                _finite_vector(checkpoint["realized_soc_mwh"], "realized SoC")
                - _finite_vector(checkpoint["terminal_soc_mwh"], "terminal SoC")
            )
        )
    )
    audits_agree = all(
        bool(item["controlling_audit_reconstructed_and_equal"]) for item in metrics
    )
    scientifically_accepted = bool(
        checkpoint["complete"]
        and audits_agree
        and terminal_deviation <= frozen_p0_policy().tolerances.terminal_soc_mwh_abs
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "qualification_registry_sha256": EXPECTED_QUALIFICATION_REGISTRY_SHA256,
        "shard_id": shard["shard_id"],
        "interval": shard["interval"],
        "classification": "accepted" if scientifically_accepted else "rejected",
        "execution_complete": checkpoint["complete"],
        "completed_intervals": checkpoint["completed_intervals"],
        "initial_state": dict(initial_state),
        "terminal_state": dict(terminal_state),
        "checkpoint_sha256": sha256_path(directory / "checkpoint.json"),
        "execution_source_fingerprint": checkpoint["execution_source_fingerprint"],
        "outer_plan_sha256": checkpoint["outer_plan_sha256"],
        "window_chain_sha256": object_sha256(checkpoint["windows"]),
        "timeout_count": timeouts,
        "recovery_window_count": recoveries,
        "coverage_fraction": float(cast(int, checkpoint["completed_intervals"]))
        / (
            int(cast(int, _mapping(shard["interval"], "interval")["stop"]))
            - int(cast(int, _mapping(shard["interval"], "interval")["start"]))
        ),
        "shifted_primary_success_fraction": (
            1.0
            if shifted_opportunities == 0
            else shifted_successes / shifted_opportunities
        ),
        "shifted_primary_opportunities": shifted_opportunities,
        "shifted_primary_successes": shifted_successes,
        "storage_throughput_mwh": storage_throughput,
        "cumulative_absolute_signpost_deviation_mwh": signpost_deviation,
        "terminal_deviation_mwh": terminal_deviation,
        "timing": {
            "accepted_solver_wall_seconds": solver_seconds,
            "primary_solver_wall_seconds": primary_solver_seconds,
            "target_free_solver_wall_seconds": target_free_solver_seconds,
            "copied_solver_wall_seconds": copied_solver_seconds,
            "model_construction_wall_seconds": construction_seconds,
            "primary_orchestration_wall_seconds": orchestration_seconds,
            "recovery_wall_seconds": recovery_seconds,
            "recovery_restart_overhead_seconds": max(
                0.0,
                recovery_seconds - target_free_solver_seconds - copied_solver_seconds,
            ),
            "total_window_path_seconds": orchestration_seconds + recovery_seconds,
        },
        "generation_cost": float(
            sum(cast(float, item["generation_cost"]) for item in metrics)
        ),
        "storage_cycling_cost": float(
            sum(cast(float, item["storage_cycling_cost"]) for item in metrics)
        ),
        "active_losses_mwh": float(
            sum(cast(float, item["active_loss_mwh"]) for item in metrics)
        ),
        "renewable_curtailment_mwh": float(
            sum(cast(float, item["renewable_curtailment_mwh"]) for item in metrics)
        ),
        "maximum_voltage_violation_pu": float(
            max(
                (cast(float, item["voltage_violation_pu"]) for item in metrics),
                default=0.0,
            )
        ),
        "maximum_thermal_violation_mva": float(
            max(
                (cast(float, item["thermal_residual_mva"]) for item in metrics),
                default=0.0,
            )
        ),
        "all_independent_audits_agree": audits_agree,
    }
    return {**summary, "summary_sha256": object_sha256(summary)}


def merge_shard_summaries(
    summaries: Sequence[Mapping[str, object]],
    *,
    manifest_path: Path = S4B_MANIFEST_PATH,
    registry_shards: Sequence[Mapping[str, object]] | None = None,
) -> Mapping[str, object]:
    """Merge complete shard summaries deterministically in global interval order."""
    envelope = load_verified_manifest(manifest_path)
    manifest = _mapping(envelope["manifest"], "S4b manifest")
    shard_values = (
        _sequence(manifest["shards"], "manifest shards")
        if registry_shards is None
        else registry_shards
    )
    registered = {
        str(_mapping(item, "manifest shard")["shard_id"]): _mapping(
            item, "manifest shard"
        )
        for item in shard_values
    }
    supplied: dict[str, Mapping[str, object]] = {}
    execution_fingerprints: set[str] = set()
    outer_plan_hashes: set[str] = set()
    for raw in summaries:
        summary = _mapping(raw, "shard summary")
        shard_id = str(summary.get("shard_id"))
        if shard_id in supplied or shard_id not in registered:
            raise ValueError("S4b merge contains duplicate or unknown shard")
        base = {key: value for key, value in summary.items() if key != "summary_sha256"}
        if (
            summary.get("summary_sha256") != object_sha256(base)
            or summary.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
            or summary.get("qualification_registry_sha256")
            != EXPECTED_QUALIFICATION_REGISTRY_SHA256
            or summary.get("interval") != registered[shard_id]["interval"]
            or summary.get("classification") != "accepted"
            or summary.get("execution_complete") is not True
            or summary.get("completed_intervals")
            != int(cast(int, _mapping(summary["interval"], "summary interval")["stop"]))
            - int(cast(int, _mapping(summary["interval"], "summary interval")["start"]))
            or summary.get("initial_state")
            != _mapping(registered[shard_id]["storage"], "manifest storage")[
                "initial_state"
            ]
            or summary.get("terminal_state")
            != _mapping(registered[shard_id]["storage"], "manifest storage")[
                "terminal_state"
            ]
        ):
            raise ValueError("S4b shard summary is not accepted merge evidence")
        for name in (
            "coverage_fraction",
            "shifted_primary_success_fraction",
            "storage_throughput_mwh",
            "cumulative_absolute_signpost_deviation_mwh",
            "terminal_deviation_mwh",
            "generation_cost",
            "storage_cycling_cost",
            "active_losses_mwh",
            "renewable_curtailment_mwh",
            "maximum_voltage_violation_pu",
            "maximum_thermal_violation_mva",
        ):
            value = summary.get(name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not np.isfinite(float(value))
                or float(value) < 0.0
            ):
                raise ValueError(f"S4b shard metric {name} is invalid")
        supplied[shard_id] = summary
        execution_fingerprints.add(str(summary.get("execution_source_fingerprint")))
        outer_plan_hashes.add(str(summary.get("outer_plan_sha256")))
    if len(execution_fingerprints) != 1 or len(outer_plan_hashes) != 1:
        raise ValueError("S4b merge mixes execution or outer-plan provenance")
    if set(supplied) != set(registered):
        raise ValueError("S4b merge requires every manifest shard exactly once")
    ordered = sorted(
        supplied.values(),
        key=lambda item: int(
            cast(int, _mapping(item["interval"], "interval")["start"])
        ),
    )
    expected_start = 0
    previous_terminal: object = None
    for summary in ordered:
        interval = _mapping(summary["interval"], "summary interval")
        if (
            interval.get("start") != expected_start
            or interval.get("half_open") is not True
        ):
            raise ValueError("S4b merged intervals overlap or contain a gap")
        if (
            previous_terminal is not None
            and summary["initial_state"] != previous_terminal
        ):
            raise ValueError("S4b merged boundary states are discontinuous")
        expected_start = int(cast(int, interval["stop"]))
        previous_terminal = summary["terminal_state"]
    registry_start = min(
        int(cast(int, _mapping(item["interval"], "interval")["start"]))
        for item in registered.values()
    )
    registry_stop = max(
        int(cast(int, _mapping(item["interval"], "interval")["stop"]))
        for item in registered.values()
    )
    expected_horizon = registry_stop - registry_start
    payload = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "qualification_registry_sha256": EXPECTED_QUALIFICATION_REGISTRY_SHA256,
        "classification": (
            "accepted_annual_partition"
            if expected_horizon == 8_760
            else "accepted_bounded_partition"
        ),
        "expected_horizon_steps": expected_horizon,
        "execution_complete": expected_start == registry_stop,
        "shard_ids": [summary["shard_id"] for summary in ordered],
        "shard_summary_sha256": [summary["summary_sha256"] for summary in ordered],
        "completed_intervals": sum(
            cast(int, item["completed_intervals"]) for item in ordered
        ),
        "timeout_count": sum(cast(int, item["timeout_count"]) for item in ordered),
        "recovery_window_count": sum(
            cast(int, item["recovery_window_count"]) for item in ordered
        ),
        "shifted_primary_opportunities": sum(
            cast(int, item["shifted_primary_opportunities"]) for item in ordered
        ),
        "shifted_primary_successes": sum(
            cast(int, item["shifted_primary_successes"]) for item in ordered
        ),
        "coverage_fraction": float(
            sum(cast(int, item["completed_intervals"]) for item in ordered)
            / expected_horizon
        ),
        "storage_throughput_mwh": float(
            sum(cast(float, item["storage_throughput_mwh"]) for item in ordered)
        ),
        "cumulative_absolute_signpost_deviation_mwh": float(
            sum(
                cast(float, item["cumulative_absolute_signpost_deviation_mwh"])
                for item in ordered
            )
        ),
        "terminal_deviation_mwh": float(
            cast(float, ordered[-1]["terminal_deviation_mwh"])
        ),
        "generation_cost": float(
            sum(cast(float, item["generation_cost"]) for item in ordered)
        ),
        "storage_cycling_cost": float(
            sum(cast(float, item["storage_cycling_cost"]) for item in ordered)
        ),
        "active_losses_mwh": float(
            sum(cast(float, item["active_losses_mwh"]) for item in ordered)
        ),
        "renewable_curtailment_mwh": float(
            sum(cast(float, item["renewable_curtailment_mwh"]) for item in ordered)
        ),
        "maximum_voltage_violation_pu": float(
            max(cast(float, item["maximum_voltage_violation_pu"]) for item in ordered)
        ),
        "maximum_thermal_violation_mva": float(
            max(cast(float, item["maximum_thermal_violation_mva"]) for item in ordered)
        ),
        "all_independent_audits_agree": all(
            bool(item["all_independent_audits_agree"]) for item in ordered
        ),
        "timing": {
            name: float(
                sum(
                    cast(float, _mapping(item["timing"], "shard timing")[name])
                    for item in ordered
                )
            )
            for name in _mapping(ordered[0]["timing"], "shard timing")
        },
    }
    payload["shifted_primary_success_fraction"] = (
        1.0
        if payload["shifted_primary_opportunities"] == 0
        else cast(int, payload["shifted_primary_successes"])
        / cast(int, payload["shifted_primary_opportunities"])
    )
    if (
        not payload["execution_complete"]
        or payload["completed_intervals"] != expected_horizon
    ):
        raise ValueError("S4b merged trajectory is not a complete selected partition")
    return {**payload, "merged_sha256": sha256(canonical_json(payload)).hexdigest()}


__all__ = [
    "AUTHORITY_FILENAME",
    "SCHEMA_VERSION",
    "audit_shard",
    "load_qualification_authority",
    "merge_shard_summaries",
    "shard_checkpoint_payload",
    "shard_entry",
    "validate_shard_checkpoint",
    "verify_shard_artifacts",
    "write_shard_checkpoint",
]
