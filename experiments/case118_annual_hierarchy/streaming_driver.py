"""Complete archive-first trajectory driver for the frozen P0 experiment."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
import ctypes
from dataclasses import asdict, dataclass
import gc
import gzip
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from typing import Literal, cast

from cvxopf import HierarchicalInputs, HierarchicalPolicy, HierarchicalSolveConfig

from experiments.case118_annual_hierarchy.p0_fixture import (
    policy_sha256,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    causal_source_from_archive,
    load_verified_outer_plan_archive,
    outer_boundaries,
    persist_window_transaction,
    residual_tolerances,
    result_dimensions,
    write_verified_outer_plan_archive,
)
from experiments.case118_annual_hierarchy.streaming_runner import (
    CausalControllerSource,
    causal_source_from_attempt,
    execute_streaming_window,
    snapshot_inputs,
    solve_frozen_outer,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    WindowIndexEntry,
    atomic_immutable_json,
    atomic_json,
    checkpoint_payload,
    load_verified_checkpoint,
    sha256_path,
)


TrajectoryStatus = Literal[
    "complete",
    "observer_terminated",
    "outer_failure",
    "recovery_exhausted",
    "artifact_failure",
]


@dataclass(frozen=True)
class ResourceSample:
    """One process-memory and elapsed-time observation."""

    phase: str
    invocation: int
    iteration: int | None
    attempt_ordinal: int | None
    elapsed_seconds: float
    rss_bytes: int


@dataclass(frozen=True)
class SafeBoundaryState:
    """State exposed to observers only after durable persistence and release."""

    completed_intervals: int
    realized_soc_mwh: Mapping[str, float]
    latest_artifact: WindowIndexEntry
    resource_samples: tuple[ResourceSample, ...]


@dataclass(frozen=True)
class StreamingTrajectoryResult:
    """Build-free outcome of one fresh or resumed trajectory invocation."""

    status: TrajectoryStatus
    completed_intervals: int
    realized_soc_mwh: Mapping[str, float]
    checkpoint_path: Path | None
    outer_plan_artifact: WindowIndexEntry | None
    completed_window_artifacts: tuple[WindowIndexEntry, ...]
    failed_window_artifact: WindowIndexEntry | None
    resource_samples: tuple[ResourceSample, ...]
    termination_reason: str | None


SafeBoundaryObserver = Callable[[SafeBoundaryState], str | None]
RSSReader = Callable[[], int]


class _DarwinProcTaskInfo(ctypes.Structure):
    _fields_ = [
        ("virtual_size", ctypes.c_uint64),
        ("resident_size", ctypes.c_uint64),
        ("total_user", ctypes.c_uint64),
        ("total_system", ctypes.c_uint64),
        ("threads_user", ctypes.c_uint64),
        ("threads_system", ctypes.c_uint64),
        ("policy", ctypes.c_int32),
        ("faults", ctypes.c_int32),
        ("pageins", ctypes.c_int32),
        ("cow_faults", ctypes.c_int32),
        ("messages_sent", ctypes.c_int32),
        ("messages_received", ctypes.c_int32),
        ("syscalls_mach", ctypes.c_int32),
        ("syscalls_unix", ctypes.c_int32),
        ("csw", ctypes.c_int32),
        ("threadnum", ctypes.c_int32),
        ("numrunning", ctypes.c_int32),
        ("priority", ctypes.c_int32),
    ]


def _darwin_current_rss_bytes() -> int:
    """Read current resident bytes through Darwin's in-process libproc API."""
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    proc_pidinfo = libproc.proc_pidinfo
    proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    proc_pidinfo.restype = ctypes.c_int
    info = _DarwinProcTaskInfo()
    size = ctypes.sizeof(info)
    returned = proc_pidinfo(
        os.getpid(),
        4,
        0,
        ctypes.byref(info),
        size,  # PROC_PIDTASKINFO
    )
    if returned != size:
        error = ctypes.get_errno()
        raise OSError(error, "proc_pidinfo(PROC_PIDTASKINFO) failed")
    return int(info.resident_size)


def process_rss_bytes() -> int:
    """Return current-process RSS without adding an experiment dependency."""
    if sys.platform == "darwin":
        return _darwin_current_rss_bytes()
    if Path("/proc/self/statm").is_file():
        resident_pages = int(Path("/proc/self/statm").read_text().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE")
    raise RuntimeError("current-process RSS measurement is unsupported")


def _sample(
    samples: list[ResourceSample],
    *,
    phase: str,
    invocation: int,
    iteration: int | None,
    attempt_ordinal: int | None = None,
    started: float,
    rss_reader: RSSReader,
) -> None:
    samples.append(
        ResourceSample(
            phase=phase,
            invocation=invocation,
            iteration=iteration,
            attempt_ordinal=attempt_ordinal,
            elapsed_seconds=time.monotonic() - started,
            rss_bytes=rss_reader(),
        )
    )


def _entry(payload: Mapping[str, object]) -> WindowIndexEntry:
    return WindowIndexEntry(
        iteration=int(cast(int, payload["iteration"])),
        relative_path=str(payload["relative_path"]),
        bytes=int(cast(int, payload["bytes"])),
        sha256=str(payload["sha256"]),
    )


def _last_causal_source(
    directory: Path, entry: WindowIndexEntry
) -> CausalControllerSource:
    with gzip.open(directory / entry.relative_path, "rt", encoding="utf-8") as stream:
        archive = cast(Mapping[str, object], json.load(stream))
    executed = cast(Mapping[str, object], archive["executed_interval"])
    controlling_id = str(executed["controlling_attempt_id"])
    attempts = cast(Sequence[Mapping[str, object]], archive["attempts"])
    matches = [item for item in attempts if item["attempt_id"] == controlling_id]
    if len(matches) != 1:
        raise ValueError("archived controlling attempt cannot be reconstructed")
    return causal_source_from_archive(matches[0])


def _termination(
    directory: Path,
    *,
    status: TrajectoryStatus,
    completed_intervals: int,
    reason: str,
) -> None:
    atomic_json(
        directory / "termination.json",
        {
            "schema_version": 1,
            "status": status,
            "completed_intervals": completed_intervals,
            "reason": reason,
        },
    )


def _persist_resource_samples(
    directory: Path,
    checkpoint_path: Path,
    *,
    new_samples: Sequence[ResourceSample],
    previous_evidence: Mapping[str, object] | None,
    total_sample_count: int,
    chunk_count: int,
    completed_intervals: int,
    source_fingerprint: str,
    scenario_hash: str,
    policy_hash: str,
    outer_plan_sha256: str,
    storage_device_ids: Sequence[str],
    initial_soc_mwh: Sequence[float],
    realized_soc_mwh: Sequence[float],
    entries: Sequence[WindowIndexEntry],
) -> Mapping[str, object]:
    """Publish one immutable sample chunk, then advance the checkpoint once."""
    payload = {
        "schema_version": 1,
        "source_fingerprint": source_fingerprint,
        "scenario_hash": scenario_hash,
        "policy_hash": policy_hash,
        "completed_intervals": completed_intervals,
        "previous": None if previous_evidence is None else dict(previous_evidence),
        "samples": [asdict(sample) for sample in new_samples],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    generation = hashlib.sha256(encoded).hexdigest()
    resource_path = directory / f"resource-samples-{generation}.json"
    if resource_path.exists():
        if resource_path.read_bytes() != encoded:
            raise ValueError("resource generation path contains different content")
    else:
        atomic_immutable_json(resource_path, payload)
    checkpoint = checkpoint_payload(
        source_fingerprint=source_fingerprint,
        scenario_hash=scenario_hash,
        outer_plan_sha256=outer_plan_sha256,
        policy_hash=policy_hash,
        storage_device_ids=storage_device_ids,
        initial_soc_mwh=initial_soc_mwh,
        realized_soc_mwh=realized_soc_mwh,
        entries=entries,
    )
    evidence: dict[str, object] = {
        "relative_path": resource_path.name,
        "bytes": resource_path.stat().st_size,
        "sha256": sha256_path(resource_path),
        "completed_intervals": completed_intervals,
        "sample_count": total_sample_count,
        "chunk_count": chunk_count,
    }
    checkpoint["resource_evidence"] = evidence
    atomic_json(checkpoint_path, checkpoint)
    return evidence


def _load_resource_samples(
    directory: Path,
    checkpoint: Mapping[str, object],
    *,
    source_fingerprint: str,
    scenario_hash: str,
    policy_hash: str,
) -> tuple[list[ResourceSample], int, Mapping[str, object]]:
    """Verify and reconstruct the checkpoint-bound immutable resource chain."""
    head = cast(Mapping[str, object], checkpoint.get("resource_evidence"))
    if not isinstance(head, Mapping):
        raise ValueError("checkpoint lacks bound resource evidence")
    if head.get("completed_intervals") != checkpoint["completed_intervals"]:
        raise ValueError("resource evidence interval mismatch")
    expected_chunks = head.get("chunk_count")
    expected_samples = head.get("sample_count")
    if not isinstance(expected_chunks, int) or expected_chunks <= 0:
        raise ValueError("resource evidence chunk count is invalid")
    if not isinstance(expected_samples, int) or expected_samples < 0:
        raise ValueError("resource evidence sample count is invalid")
    chunks: list[list[ResourceSample]] = []
    chunk_intervals: list[int] = []
    current: Mapping[str, object] | None = head
    seen: set[str] = set()
    while current is not None:
        relative_path = current.get("relative_path")
        if (
            not isinstance(relative_path, str)
            or not relative_path.startswith("resource-samples-")
            or not relative_path.endswith(".json")
            or Path(relative_path).name != relative_path
            or relative_path in seen
        ):
            raise ValueError("resource evidence path mismatch")
        seen.add(relative_path)
        path = (directory / relative_path).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file():
            raise ValueError("resource evidence artifact is missing")
        if path.stat().st_size != current.get("bytes") or sha256_path(
            path
        ) != current.get("sha256"):
            raise ValueError("resource evidence integrity check failed")
        payload = cast(Mapping[str, object], json.loads(path.read_text()))
        expected = {
            "schema_version": 1,
            "source_fingerprint": source_fingerprint,
            "scenario_hash": scenario_hash,
            "policy_hash": policy_hash,
        }
        if any(payload.get(name) != value for name, value in expected.items()):
            raise ValueError("resource evidence provenance mismatch")
        chunk_interval = payload.get("completed_intervals")
        if not isinstance(chunk_interval, int) or chunk_interval < 0:
            raise ValueError("resource evidence interval is invalid")
        chunk_intervals.append(chunk_interval)
        records = payload.get("samples")
        if not isinstance(records, list):
            raise ValueError("resource evidence samples must be a list")
        chunk: list[ResourceSample] = []
        for item in records:
            if not isinstance(item, Mapping):
                raise ValueError("resource sample must be a mapping")
            try:
                sample = ResourceSample(
                    phase=str(item["phase"]),
                    invocation=int(cast(int, item["invocation"])),
                    iteration=(
                        None
                        if item["iteration"] is None
                        else int(cast(int, item["iteration"]))
                    ),
                    attempt_ordinal=(
                        None
                        if item["attempt_ordinal"] is None
                        else int(cast(int, item["attempt_ordinal"]))
                    ),
                    elapsed_seconds=float(cast(float, item["elapsed_seconds"])),
                    rss_bytes=int(cast(int, item["rss_bytes"])),
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("resource sample is malformed") from exc
            if (
                not sample.phase
                or sample.invocation < 0
                or sample.elapsed_seconds < 0
                or sample.rss_bytes < 0
            ):
                raise ValueError("resource sample contains invalid values")
            chunk.append(sample)
        chunks.append(chunk)
        previous = payload.get("previous")
        if previous is not None and not isinstance(previous, Mapping):
            raise ValueError("resource evidence previous link is malformed")
        current = cast(Mapping[str, object] | None, previous)
    if len(chunks) != expected_chunks:
        raise ValueError("resource evidence chunk count mismatch")
    ordered_intervals = list(reversed(chunk_intervals))
    if (
        not ordered_intervals
        or ordered_intervals[0] != 0
        or ordered_intervals[-1] != checkpoint["completed_intervals"]
        or any(
            later - earlier not in {0, 1}
            for earlier, later in zip(
                ordered_intervals, ordered_intervals[1:], strict=False
            )
        )
    ):
        raise ValueError("resource evidence interval chain is discontinuous")
    samples = [sample for chunk in reversed(chunks) for sample in chunk]
    if len(samples) != expected_samples:
        raise ValueError("resource evidence sample count mismatch")
    invocation = 0 if not samples else max(item.invocation for item in samples) + 1
    return samples, invocation, head


def run_streaming_trajectory(
    directory: Path,
    inputs: HierarchicalInputs,
    policy: HierarchicalPolicy,
    solve_config: HierarchicalSolveConfig,
    *,
    source_fingerprint: str,
    scenario_hash: str,
    resume: bool = False,
    observer: SafeBoundaryObserver | None = None,
    rss_reader: RSSReader = process_rss_bytes,
) -> StreamingTrajectoryResult:
    """Execute, archive, release, and optionally resume the frozen hierarchy."""
    directory.mkdir(parents=True, exist_ok=True)
    snapshot = snapshot_inputs(inputs)
    ids = tuple(str(unit.device_id) for unit in snapshot.storage)
    initial = tuple(float(unit.initial_soc) for unit in snapshot.storage)
    policy_hash = policy_sha256(policy)
    solve_hash = solve_config_sha256(solve_config)
    started = time.monotonic()
    samples: list[ResourceSample] = []
    invocation = 0
    resource_head: Mapping[str, object] | None = None
    persisted_sample_count = 0
    outer_path = directory / "outer-plan.json.gz"
    checkpoint_path = directory / "checkpoint.json"

    if resume:
        if not outer_path.is_file():
            raise ValueError("resume requires an outer-plan artifact")
        outer = load_verified_outer_plan_archive(
            outer_path,
            inputs=snapshot,
            policy=policy,
            expected_solve_config_sha256=solve_hash,
            expected_source_fingerprint=source_fingerprint,
            expected_scenario_hash=scenario_hash,
        )
        outer_entry = WindowIndexEntry(
            iteration=0,
            relative_path=outer_path.name,
            bytes=outer_path.stat().st_size,
            sha256=sha256_path(outer_path),
        )
        if checkpoint_path.is_file():
            checkpoint = load_verified_checkpoint(
                checkpoint_path,
                expected_source_fingerprint=source_fingerprint,
                expected_scenario_hash=scenario_hash,
                expected_outer_plan_sha256=outer_entry.sha256,
                expected_policy_hash=policy_hash,
                expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
                expected_residual_tolerances=residual_tolerances(policy),
                expected_inner_terminal_policy=policy.inner_terminal_policy,
                expected_horizon_steps=snapshot.horizon_steps,
                expected_ac_window_steps=policy.ac_window_steps,
                expected_result_dimensions=result_dimensions(snapshot),
                expected_delta_hours=snapshot.delta,
                expected_outer_boundary_soc_mwh=outer_boundaries(outer),
            )
            samples, invocation, resource_head = _load_resource_samples(
                directory,
                checkpoint,
                source_fingerprint=source_fingerprint,
                scenario_hash=scenario_hash,
                policy_hash=policy_hash,
            )
            persisted_sample_count = len(samples)
            completed_entries = tuple(
                _entry(item)
                for item in cast(Sequence[Mapping[str, object]], checkpoint["windows"])
            )
            realized = dict(
                zip(
                    ids,
                    cast(Sequence[float], checkpoint["realized_soc_mwh"]),
                    strict=True,
                )
            )
            preceding = (
                _last_causal_source(directory, completed_entries[-1])
                if completed_entries
                else None
            )
        else:
            if not outer.accepted_primal:
                raise ValueError(
                    "checkpoint-free recovery requires an accepted outer plan"
                )
            _sample(
                samples,
                phase="recovered_outer_without_checkpoint",
                invocation=invocation,
                iteration=None,
                started=started,
                rss_reader=rss_reader,
            )
            realized = dict(zip(ids, initial, strict=True))
            completed_entries = ()
            preceding = None
    else:
        _sample(
            samples,
            phase="before_outer",
            invocation=invocation,
            iteration=None,
            started=started,
            rss_reader=rss_reader,
        )
        if outer_path.exists() or checkpoint_path.exists():
            raise FileExistsError(
                "fresh trajectory directory already contains run artifacts"
            )
        outer = solve_frozen_outer(snapshot, policy, solve_config)
        try:
            outer_entry = write_verified_outer_plan_archive(
                outer_path,
                outer,
                inputs=snapshot,
                source_fingerprint=source_fingerprint,
                scenario_hash=scenario_hash,
            )
        except Exception as exc:
            reason = f"outer artifact failure: {type(exc).__name__}: {exc}"
            _termination(
                directory,
                status="artifact_failure",
                completed_intervals=0,
                reason=reason,
            )
            return StreamingTrajectoryResult(
                "artifact_failure",
                0,
                dict(zip(ids, initial, strict=True)),
                None,
                None,
                (),
                None,
                tuple(samples),
                reason,
            )
        if not outer.accepted_primal:
            reason = "outer plan did not pass the accepted-primal gate"
            _termination(
                directory, status="outer_failure", completed_intervals=0, reason=reason
            )
            return StreamingTrajectoryResult(
                "outer_failure",
                0,
                dict(zip(ids, initial, strict=True)),
                None,
                outer_entry,
                (),
                None,
                tuple(samples),
                reason,
            )
        outer = load_verified_outer_plan_archive(
            outer_path,
            inputs=snapshot,
            policy=policy,
            expected_solve_config_sha256=solve_hash,
            expected_source_fingerprint=source_fingerprint,
            expected_scenario_hash=scenario_hash,
            expected_artifact=outer_entry,
        )
        gc.collect()
        realized = dict(zip(ids, initial, strict=True))
        completed_entries = ()
        preceding = None
    _sample(
        samples,
        phase="after_outer_release",
        invocation=invocation,
        iteration=None,
        started=started,
        rss_reader=rss_reader,
    )
    if resource_head is None:
        try:
            resource_head = _persist_resource_samples(
                directory,
                checkpoint_path,
                new_samples=samples,
                previous_evidence=None,
                total_sample_count=len(samples),
                chunk_count=1,
                completed_intervals=0,
                source_fingerprint=source_fingerprint,
                scenario_hash=scenario_hash,
                policy_hash=policy_hash,
                outer_plan_sha256=outer_entry.sha256,
                storage_device_ids=ids,
                initial_soc_mwh=initial,
                realized_soc_mwh=initial,
                entries=(),
            )
        except Exception as exc:
            reason = f"zero-boundary artifact failure: {type(exc).__name__}: {exc}"
            _termination(
                directory,
                status="artifact_failure",
                completed_intervals=0,
                reason=reason,
            )
            return StreamingTrajectoryResult(
                "artifact_failure",
                0,
                dict(zip(ids, initial, strict=True)),
                None,
                outer_entry,
                (),
                None,
                tuple(samples),
                reason,
            )
        persisted_sample_count = len(samples)

    failed_entry: WindowIndexEntry | None = None
    while len(completed_entries) < snapshot.horizon_steps:
        iteration = len(completed_entries)

        def record_attempt_phase(
            phase: str, phase_iteration: int, ordinal: int
        ) -> None:
            _sample(
                samples,
                phase=phase,
                invocation=invocation,
                iteration=phase_iteration,
                attempt_ordinal=ordinal,
                started=started,
                rss_reader=rss_reader,
            )

        _sample(
            samples,
            phase="before_window",
            invocation=invocation,
            iteration=iteration,
            started=started,
            rss_reader=rss_reader,
        )
        window = execute_streaming_window(
            snapshot,
            policy,
            solve_config,
            outer,
            iteration=iteration,
            realized_soc_mwh=realized,
            preceding_controlling_attempt=preceding,
            phase_observer=record_attempt_phase,
        )
        _sample(
            samples,
            phase="after_window",
            invocation=invocation,
            iteration=iteration,
            started=started,
            rss_reader=rss_reader,
        )
        _sample(
            samples,
            phase="before_archive",
            invocation=invocation,
            iteration=iteration,
            started=started,
            rss_reader=rss_reader,
        )
        try:
            persisted = persist_window_transaction(
                directory,
                window,
                inputs=snapshot,
                policy=policy,
                outer=outer,
                outer_plan_artifact=outer_entry,
                preceding_controlling_attempt_id=(
                    None if preceding is None else preceding.attempt_id
                ),
                source_fingerprint=source_fingerprint,
                scenario_hash=scenario_hash,
                policy_hash=policy_hash,
                initial_soc_mwh=initial,
                completed_entries=completed_entries,
                advance_checkpoint=False,
            )
        except Exception as exc:
            reason = f"window artifact failure: {type(exc).__name__}: {exc}"
            _termination(
                directory,
                status="artifact_failure",
                completed_intervals=len(completed_entries),
                reason=reason,
            )
            return StreamingTrajectoryResult(
                "artifact_failure",
                len(completed_entries),
                realized,
                checkpoint_path if checkpoint_path.exists() else None,
                outer_entry,
                completed_entries,
                None,
                tuple(samples),
                reason,
            )
        _sample(
            samples,
            phase="after_archive",
            invocation=invocation,
            iteration=iteration,
            started=started,
            rss_reader=rss_reader,
        )
        if window.controlling_attempt is None or window.post_step_soc_mwh is None:
            failed_entry = persisted.artifact
            terminal_executed = [
                record for record in window.attempts if record.slot_state == "executed"
            ]
            if terminal_executed:
                last_audit = terminal_executed[-1].audit
                outcome = None if last_audit is None else last_audit.outcome
                reason = f"ac_recovery_exhausted:{outcome}"
            else:
                reason = "ac_recovery_exhausted:no_solver_attempt"
            del window
            gc.collect()
            _sample(
                samples,
                phase="after_release",
                invocation=invocation,
                iteration=iteration,
                started=started,
                rss_reader=rss_reader,
            )
            if checkpoint_path.exists():
                _persist_resource_samples(
                    directory,
                    checkpoint_path,
                    new_samples=samples[persisted_sample_count:],
                    previous_evidence=resource_head,
                    total_sample_count=len(samples),
                    chunk_count=(
                        1
                        if resource_head is None
                        else int(cast(int, resource_head["chunk_count"])) + 1
                    ),
                    completed_intervals=len(completed_entries),
                    source_fingerprint=source_fingerprint,
                    scenario_hash=scenario_hash,
                    policy_hash=policy_hash,
                    outer_plan_sha256=outer_entry.sha256,
                    storage_device_ids=ids,
                    initial_soc_mwh=initial,
                    realized_soc_mwh=[realized[device_id] for device_id in ids],
                    entries=completed_entries,
                )
            _termination(
                directory,
                status="recovery_exhausted",
                completed_intervals=len(completed_entries),
                reason=reason,
            )
            return StreamingTrajectoryResult(
                "recovery_exhausted",
                len(completed_entries),
                realized,
                checkpoint_path if checkpoint_path.exists() else None,
                outer_entry,
                completed_entries,
                failed_entry,
                tuple(samples),
                reason,
            )
        next_preceding = causal_source_from_attempt(window.controlling_attempt)
        next_realized = dict(window.post_step_soc_mwh)
        next_entries = persisted.completed_entries
        del window
        gc.collect()
        _sample(
            samples,
            phase="after_release",
            invocation=invocation,
            iteration=iteration,
            started=started,
            rss_reader=rss_reader,
        )
        try:
            next_resource_head = _persist_resource_samples(
                directory,
                checkpoint_path,
                new_samples=samples[persisted_sample_count:],
                previous_evidence=resource_head,
                total_sample_count=len(samples),
                chunk_count=(
                    1
                    if resource_head is None
                    else int(cast(int, resource_head["chunk_count"])) + 1
                ),
                completed_intervals=len(next_entries),
                source_fingerprint=source_fingerprint,
                scenario_hash=scenario_hash,
                policy_hash=policy_hash,
                outer_plan_sha256=outer_entry.sha256,
                storage_device_ids=ids,
                initial_soc_mwh=initial,
                realized_soc_mwh=[next_realized[device_id] for device_id in ids],
                entries=next_entries,
            )
        except Exception as exc:
            reason = f"resource artifact failure: {type(exc).__name__}: {exc}"
            _termination(
                directory,
                status="artifact_failure",
                completed_intervals=len(completed_entries),
                reason=reason,
            )
            return StreamingTrajectoryResult(
                "artifact_failure",
                len(completed_entries),
                realized,
                checkpoint_path if checkpoint_path.exists() else None,
                outer_entry,
                completed_entries,
                None,
                tuple(samples),
                reason,
            )
        preceding = next_preceding
        realized = next_realized
        completed_entries = next_entries
        resource_head = next_resource_head
        persisted_sample_count = len(samples)
        if observer is not None:
            observer_reason = observer(
                SafeBoundaryState(
                    len(completed_entries),
                    dict(realized),
                    persisted.artifact,
                    tuple(samples),
                )
            )
            if observer_reason is not None:
                _termination(
                    directory,
                    status="observer_terminated",
                    completed_intervals=len(completed_entries),
                    reason=observer_reason,
                )
                return StreamingTrajectoryResult(
                    "observer_terminated",
                    len(completed_entries),
                    realized,
                    checkpoint_path,
                    outer_entry,
                    completed_entries,
                    None,
                    tuple(samples),
                    observer_reason,
                )
    _termination(
        directory,
        status="complete",
        completed_intervals=len(completed_entries),
        reason="global horizon completed",
    )
    return StreamingTrajectoryResult(
        "complete",
        len(completed_entries),
        realized,
        checkpoint_path,
        outer_entry,
        completed_entries,
        None,
        tuple(samples),
        None,
    )


__all__ = [
    "ResourceSample",
    "SafeBoundaryObserver",
    "SafeBoundaryState",
    "StreamingTrajectoryResult",
    "process_rss_bytes",
    "run_streaming_trajectory",
]
