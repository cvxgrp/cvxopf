"""Supervise one frozen Case118 S4b shard without scheduler assumptions."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import signal
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence, cast

from experiments.case118_annual_hierarchy.p0_fixture import (
    frozen_p0_policy,
    frozen_p0_solve_config,
)
from experiments.case118_annual_hierarchy.run_s0 import ROOT, _software_versions
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s4b_execution import (
    AUTHORITY_FILENAME,
    audit_shard,
    load_qualification_authority,
    merge_shard_summaries,
    qualification_shard_entry,
    shard_checkpoint_payload,
    shard_entry,
    validate_shard_checkpoint,
    verify_shard_artifacts,
    write_shard_checkpoint,
)
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    PRIMARY_ATTEMPT_BUDGET_SECONDS,
    S4_OUTER_ARCHIVE_PATH,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    causal_source_from_archive,
    load_verified_outer_plan_archive,
    window_archive_payload,
)
from experiments.case118_annual_hierarchy.streaming_runner import (
    CausalControllerSource,
    StreamingOuterPlan,
    execute_streaming_window,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    WindowIndexEntry,
    atomic_gzip_json,
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)


EXPERIMENT_DIR = ROOT / "experiments/case118_annual_hierarchy"
DEFAULT_AUTHORITY_PATH = EXPERIMENT_DIR / AUTHORITY_FILENAME
POLL_SECONDS = 1.0
ATTEMPT_TERMINATION_GRACE_SECONDS = 10.0
WORKER_TERMINATION_GRACE_SECONDS = 30.0
SCHEMA_VERSION = 1
QUALIFICATION_RUNS = {
    "ordinary": ("s4b-qualification-ordinary",),
    "partitioned_one_process": (
        "s4b-qualification-partition-a",
        "s4b-qualification-partition-b",
    ),
    "partitioned_fresh_sequential": (
        "s4b-qualification-partition-a",
        "s4b-qualification-partition-b",
    ),
    "partitioned_fresh_concurrent": (
        "s4b-qualification-partition-a",
        "s4b-qualification-partition-b",
    ),
}

SOURCE_FILES = (
    "experiments/case118_annual_hierarchy/FIVE_MINUTE_TIMEOUT_POLICY.md",
    "experiments/case118_annual_hierarchy/S4B_PROTOCOL.md",
    "experiments/case118_annual_hierarchy/audit.py",
    "experiments/case118_annual_hierarchy/p0_fixture.py",
    "experiments/case118_annual_hierarchy/run_s0.py",
    "experiments/case118_annual_hierarchy/run_s4b.py",
    "experiments/case118_annual_hierarchy/s2_analysis.py",
    "experiments/case118_annual_hierarchy/s4_fixture.py",
    "experiments/case118_annual_hierarchy/s4b_execution.py",
    "experiments/case118_annual_hierarchy/s4b_manifest.py",
    "experiments/case118_annual_hierarchy/streaming_archive.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
    "experiments/case118_annual_hierarchy/streaming_schema.py",
)


@dataclass(frozen=True)
class ProcessObservation:
    """One current process sample with a reuse-safe operating-system identity."""

    pid: int
    ppid: int
    create_time: str
    rss_mib: float
    cpu_seconds: float


def _cpu_seconds(value: str) -> float:
    days = 0
    clock_value = value
    if "-" in value:
        raw_days, clock_value = value.split("-", maxsplit=1)
        days = int(raw_days)
    fields = clock_value.split(":")
    if len(fields) == 2:
        hours = 0
        minutes = int(fields[0])
        seconds = float(fields[1])
    elif len(fields) == 3:
        hours = int(fields[0])
        minutes = int(fields[1])
        seconds = float(fields[2])
    else:
        raise ValueError("process CPU time has an unsupported format")
    return float(days * 86_400 + hours * 3_600 + minutes * 60 + seconds)


def process_observations() -> tuple[ProcessObservation, ...]:
    """Sample every process once; lstart distinguishes recycled PIDs."""
    output = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,rss=,time=,lstart="],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows: list[ProcessObservation] = []
    for line in output.splitlines():
        fields = line.split(None, maxsplit=4)
        if len(fields) != 5:
            continue
        rows.append(
            ProcessObservation(
                pid=int(fields[0]),
                ppid=int(fields[1]),
                rss_mib=int(fields[2]) / 1024.0,
                cpu_seconds=_cpu_seconds(fields[3]),
                create_time=fields[4],
            )
        )
    return tuple(rows)


def process_tree_usage(
    root_pids: Sequence[int], observations: Sequence[ProcessObservation]
) -> Mapping[str, object]:
    """Return deduplicated simultaneous resource usage for process trees."""
    by_pid = {row.pid: row for row in observations}
    children: dict[int, list[int]] = {}
    for row in observations:
        children.setdefault(row.ppid, []).append(row.pid)
    per_root: dict[int, set[tuple[int, str]]] = {}
    members: dict[tuple[int, str], ProcessObservation] = {}
    for root in root_pids:
        if root not in by_pid:
            raise ValueError(f"cannot sample live S4b worker process {root}")
        pending = [root]
        identities: set[tuple[int, str]] = set()
        while pending:
            pid = pending.pop()
            observed = by_pid.get(pid)
            if observed is None:
                raise ValueError("S4b process tree changed during one sample")
            identity = (observed.pid, observed.create_time)
            if identity in identities:
                continue
            identities.add(identity)
            members[identity] = observed
            pending.extend(children.get(pid, ()))
        per_root[root] = identities
    union = set().union(*per_root.values()) if per_root else set()
    return {
        "per_worker": {
            str(root): {
                "process_identities": [list(item) for item in sorted(identities)],
                "rss_mib": sum(members[item].rss_mib for item in identities),
                "cpu_seconds": sum(members[item].cpu_seconds for item in identities),
            }
            for root, identities in per_root.items()
        },
        "aggregate_process_identities": [list(item) for item in sorted(union)],
        "aggregate_rss_mib": sum(members[item].rss_mib for item in union),
        "aggregate_cpu_seconds": sum(members[item].cpu_seconds for item in union),
    }


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def source_paths() -> tuple[Path, ...]:
    paths = [ROOT / name for name in SOURCE_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    result = tuple(sorted(set(paths)))
    missing = [
        path.relative_to(ROOT).as_posix() for path in result if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(f"S4b source registry is incomplete: {missing}")
    return result


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def execution_context() -> Mapping[str, object]:
    fixture = load_s4_fixture()
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": source_fingerprint(),
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "scenario_sha256": fixture.scenario_hash,
        "policy_sha256": fixture.policy_sha256,
        "solve_config_sha256": fixture.solve_config_sha256,
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "software_versions": dict(_software_versions()),
    }


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def _outer() -> StreamingOuterPlan:
    fixture = load_s4_fixture()
    manifest, _ = shard_entry("s4b-shard-000")
    provenance = _mapping(
        _mapping(manifest["manifest"], "manifest")["global_provenance"],
        "global provenance",
    )
    return load_verified_outer_plan_archive(
        S4_OUTER_ARCHIVE_PATH,
        inputs=fixture.inputs,
        policy=frozen_p0_policy(),
        expected_solve_config_sha256=fixture.solve_config_sha256,
        expected_source_fingerprint=str(provenance["source_fingerprint"]),
        expected_scenario_hash=fixture.scenario_hash,
    )


def _terminate(process: subprocess.Popen[bytes], grace_seconds: float) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
    except ProcessLookupError:
        process.wait()


def _read_ready(path: Path) -> WindowIndexEntry:
    value = _mapping(json.loads(path.read_text()), "window-ready record")
    return WindowIndexEntry(
        iteration=int(cast(int, value["iteration"])),
        relative_path=str(value["relative_path"]),
        bytes=int(cast(int, value["bytes"])),
        sha256=str(value["sha256"]),
    )


def _last_source(
    directory: Path, checkpoint: Mapping[str, object]
) -> CausalControllerSource | None:
    windows = cast(Sequence[Mapping[str, object]], checkpoint["windows"])
    if not windows:
        return None
    path = directory / str(windows[-1]["relative_path"])
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        archive = _mapping(json.load(stream), "preceding window")
    executed = _mapping(archive["executed_interval"], "executed interval")
    controlling_id = executed["controlling_attempt_id"]
    attempts = cast(Sequence[Mapping[str, object]], archive["attempts"])
    matches = [
        attempt for attempt in attempts if attempt["attempt_id"] == controlling_id
    ]
    if len(matches) != 1:
        raise ValueError("preceding S4b controller is missing or duplicated")
    return causal_source_from_archive(matches[0])


def _write_phase(
    path: Path,
    events: list[Mapping[str, object]],
    phase: str,
    iteration: int,
    ordinal: int,
) -> None:
    events.append(
        {
            "phase": phase,
            "iteration": iteration,
            "attempt_ordinal": ordinal,
            "monotonic_seconds": time.monotonic(),
        }
    )
    atomic_json(path, {"schema_version": 1, "events": events})


def execute_one_window_child(
    directory: Path,
    *,
    shard_id: str,
    iteration: int,
    primary_timeout_seconds: float | None,
    authority_path: Path,
    expected_commit: str,
    expected_source_fingerprint: str,
) -> None:
    """Construct and archive one candidate window; never advance the checkpoint."""
    context = execution_context()
    if (
        context["git_clean"] is not True
        or context["git_commit"] != expected_commit
        or context["source_fingerprint"] != expected_source_fingerprint
    ):
        raise ValueError("S4b window child provenance mismatch")
    load_qualification_authority(
        authority_path,
        expected_execution_commit=expected_commit,
        expected_source_fingerprint=expected_source_fingerprint,
    )
    _, shard = qualification_shard_entry(shard_id, _outer())
    checkpoint = validate_shard_checkpoint(
        json.loads((directory / "checkpoint.json").read_text()), shard=shard
    )
    if checkpoint["next_global_iteration"] != iteration:
        raise ValueError("window child iteration differs from checkpoint")
    interval = _mapping(shard["interval"], "shard interval")
    storage = _mapping(shard["storage"], "shard storage")
    ids = tuple(cast(Sequence[str], storage["device_ids"]))
    realized = dict(
        zip(ids, cast(Sequence[float], checkpoint["realized_soc_mwh"]), strict=True)
    )
    initial = dict(
        zip(
            ids,
            cast(
                Sequence[float],
                _mapping(storage["initial_state"], "initial state")["soc_mwh"],
            ),
            strict=True,
        )
    )
    preceding = _last_source(directory, checkpoint)
    events: list[Mapping[str, object]] = []
    phase_kind = "recovery" if primary_timeout_seconds is not None else "primary"
    phase_path = directory / f"window-phase-{iteration:06d}-{phase_kind}.json"

    def observer(phase: str, phase_iteration: int, ordinal: int) -> None:
        _write_phase(phase_path, events, phase, phase_iteration, ordinal)

    fixture = load_s4_fixture()
    outer = _outer()
    result = execute_streaming_window(
        fixture.inputs,
        frozen_p0_policy(),
        frozen_p0_solve_config(),
        outer,
        iteration,
        realized,
        preceding,
        observer,
        trajectory_start=int(cast(int, interval["start"])),
        trajectory_stop=int(cast(int, interval["stop"])),
        trajectory_initial_soc_mwh=initial,
        primary_timeout_seconds=primary_timeout_seconds,
    )
    payload = window_archive_payload(
        result,
        inputs=fixture.inputs,
        policy=frozen_p0_policy(),
        outer=outer,
        preceding_controlling_attempt_id=(
            None if preceding is None else preceding.attempt_id
        ),
        trajectory_start=int(cast(int, interval["start"])),
        trajectory_stop=int(cast(int, interval["stop"])),
        primary_attempt_budget_seconds=PRIMARY_ATTEMPT_BUDGET_SECONDS,
    )
    if result.controlling_attempt is None:
        raise RuntimeError("S4b window exhausted recovery without an accepted action")
    encoded_hash = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    artifact_name = f"window-{iteration:06d}-{encoded_hash}.json.gz"
    entry = atomic_gzip_json(directory / artifact_name, payload)
    atomic_json(
        directory / f"window-ready-{iteration:06d}.json",
        entry.__dict__,
    )


def _primary_solve_started(phase_path: Path) -> tuple[float | None, bool]:
    if not phase_path.is_file():
        return None, False
    events = cast(
        Sequence[Mapping[str, object]],
        _mapping(json.loads(phase_path.read_text()), "phase record")["events"],
    )
    started = next(
        (
            float(cast(float, item["monotonic_seconds"]))
            for item in events
            if item["phase"] == "before_ac_solve" and item["attempt_ordinal"] == 0
        ),
        None,
    )
    completed = any(
        item["phase"] == "after_ac_solve" and item["attempt_ordinal"] == 0
        for item in events
    )
    return started, completed


def supervise_window_process(
    command: Sequence[str],
    *,
    directory: Path,
    iteration: int,
    budget_seconds: float = PRIMARY_ATTEMPT_BUDGET_SECONDS,
    poll_seconds: float = POLL_SECONDS,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    terminate_process: Callable[[subprocess.Popen[bytes], float], None] = _terminate,
) -> Mapping[str, object]:
    """Apply the primary-only wall budget and retain the complete transition."""
    if budget_seconds != PRIMARY_ATTEMPT_BUDGET_SECONDS:
        raise ValueError("S4b primary budget differs from the frozen manifest")
    phase_path = directory / f"window-phase-{iteration:06d}-primary.json"
    ready_path = directory / f"window-ready-{iteration:06d}.json"
    log_path = directory / f"window-process-{iteration:06d}-primary.log"
    started = clock()
    log = log_path.open("xb")
    try:
        process = popen(
            list(command),
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except BaseException:
        log.close()
        raise
    timed_out = False
    primary_started: float | None = None
    interruption: BaseException | None = None
    try:
        while process.poll() is None:
            marker_started, marker_completed = _primary_solve_started(phase_path)
            if marker_started is not None:
                primary_started = marker_started
            if (
                primary_started is not None
                and not marker_completed
                and clock() - primary_started >= budget_seconds
            ):
                timed_out = True
                terminate_process(process, ATTEMPT_TERMINATION_GRACE_SECONDS)
                break
            sleep(poll_seconds)
    except BaseException as exc:
        terminate_process(process, ATTEMPT_TERMINATION_GRACE_SECONDS)
        interruption = exc
    returncode = process.wait()
    log.close()
    record = {
        "schema_version": SCHEMA_VERSION,
        "iteration": iteration,
        "classification": "timeout"
        if timed_out
        else "supervisor_interrupted"
        if interruption is not None
        else "completed"
        if returncode == 0 and ready_path.is_file()
        else "worker_failure",
        "primary_budget_seconds": budget_seconds,
        "primary_solver_started_monotonic": primary_started,
        "primary_budget_consumed_seconds": (
            None
            if primary_started is None
            else min(clock() - primary_started, budget_seconds)
        ),
        "orchestration_wall_seconds": clock() - started,
        "returncode": returncode,
        "supervisor_exception": (
            None
            if interruption is None
            else f"{type(interruption).__name__}: {interruption}"
        ),
        "phase_record": phase_path.name if phase_path.is_file() else None,
        "phase_record_sha256": sha256_path(phase_path)
        if phase_path.is_file()
        else None,
        "ready_record": ready_path.name if ready_path.is_file() else None,
        "worker_log": log_path.name,
        "worker_log_sha256": sha256_path(log_path),
    }
    atomic_immutable_json(
        directory
        / (
            f"window-supervision-{iteration:06d}-"
            f"{'timeout' if timed_out else 'interrupted' if interruption else 'primary'}.json"
        ),
        record,
    )
    if interruption is not None:
        raise interruption
    return record


def _run_shard_worker_body(
    directory: Path,
    *,
    shard_id: str,
    authority_path: Path,
    reviewed_resume: bool = False,
    execution_mode: str = "ordinary",
) -> Mapping[str, object]:
    """Run one authorized shard through isolated window processes."""
    context = execution_context()
    child_usage_start = resource.getrusage(resource.RUSAGE_CHILDREN)
    if context["git_clean"] is not True:
        raise ValueError("S4b execution requires a clean committed worktree")
    load_qualification_authority(
        authority_path,
        expected_execution_commit=str(context["git_commit"]),
        expected_source_fingerprint=str(context["source_fingerprint"]),
    )
    _, shard = qualification_shard_entry(shard_id, _outer())
    outer = _outer()
    interval = _mapping(shard["interval"], "shard interval")
    storage = _mapping(shard["storage"], "shard storage")
    initial = cast(
        Sequence[float], _mapping(storage["initial_state"], "initial state")["soc_mwh"]
    )
    if directory.exists():
        if not reviewed_resume:
            raise FileExistsError("S4b partial shard requires explicit reviewed resume")
        checkpoint, _ = verify_shard_artifacts(directory, shard=shard, outer=outer)
        if (
            checkpoint["execution_source_fingerprint"] != context["source_fingerprint"]
            or checkpoint["outer_plan_sha256"] != sha256_path(S4_OUTER_ARCHIVE_PATH)
            or checkpoint["execution_mode"] != execution_mode
            or checkpoint["complete"] is True
        ):
            raise ValueError("S4b reviewed resume provenance or state mismatch")
        continuation_ordinal = len(list(directory.glob("reviewed-continuation-*.json")))
        atomic_immutable_json(
            directory / f"reviewed-continuation-{continuation_ordinal:03d}.json",
            {
                "schema_version": SCHEMA_VERSION,
                "classification": "explicit_reviewed_continuation",
                "shard_id": shard_id,
                "completed_intervals": checkpoint["completed_intervals"],
                "checkpoint_sha256": sha256_path(directory / "checkpoint.json"),
                "execution_context": context,
            },
        )
        windows = [
            WindowIndexEntry(**cast(dict[str, Any], item))
            for item in cast(Sequence[Mapping[str, object]], checkpoint["windows"])
        ]
    else:
        directory.mkdir(parents=True, exist_ok=False)
        checkpoint = shard_checkpoint_payload(
            shard=shard,
            execution_source_fingerprint=str(context["source_fingerprint"]),
            outer_plan_sha256=sha256_path(S4_OUTER_ARCHIVE_PATH),
            execution_mode=execution_mode,
            realized_soc_mwh=initial,
            preceding_controlling_attempt_id=None,
            windows=(),
        )
        write_shard_checkpoint(directory / "checkpoint.json", checkpoint)
        windows = []
    timing: list[Mapping[str, object]] = []
    start = int(cast(int, interval["start"]))
    stop = int(cast(int, interval["stop"]))
    for iteration in range(start + len(windows), stop):
        base_command = [
            sys.executable,
            "-m",
            "experiments.case118_annual_hierarchy.run_s4b",
            "--window-child",
            "--directory",
            str(directory),
            "--shard-id",
            shard_id,
            "--iteration",
            str(iteration),
            "--authority",
            str(authority_path),
            "--expected-commit",
            str(context["git_commit"]),
            "--expected-source-fingerprint",
            str(context["source_fingerprint"]),
        ]
        record = supervise_window_process(
            base_command, directory=directory, iteration=iteration
        )
        if record["classification"] == "timeout":
            recovery_command = [
                *base_command,
                "--primary-timeout",
                str(PRIMARY_ATTEMPT_BUDGET_SECONDS),
            ]
            recovery_started = time.monotonic()
            recovery = subprocess.run(recovery_command, cwd=ROOT, check=False)
            record = {
                **record,
                "recovery_returncode": recovery.returncode,
                "recovery_wall_seconds": time.monotonic() - recovery_started,
            }
            recovery_phase = directory / f"window-phase-{iteration:06d}-recovery.json"
            recovery_record = {
                "schema_version": SCHEMA_VERSION,
                "iteration": iteration,
                "timeout_supervision_sha256": sha256_path(
                    directory / f"window-supervision-{iteration:06d}-timeout.json"
                ),
                "returncode": recovery.returncode,
                "wall_seconds": record["recovery_wall_seconds"],
                "phase_record": recovery_phase.name,
                "phase_record_sha256": (
                    sha256_path(recovery_phase) if recovery_phase.is_file() else None
                ),
            }
            atomic_immutable_json(
                directory / f"window-recovery-{iteration:06d}.json",
                recovery_record,
            )
            if recovery.returncode != 0:
                raise RuntimeError("S4b timeout recovery process failed")
        elif record["classification"] != "completed":
            raise RuntimeError("S4b window process failed")
        ready = _read_ready(directory / f"window-ready-{iteration:06d}.json")
        if ready.iteration != iteration:
            raise ValueError("S4b ready record has the wrong iteration")
        windows.append(ready)
        with gzip.open(
            directory / ready.relative_path, "rt", encoding="utf-8"
        ) as stream:
            archive = _mapping(json.load(stream), "S4b window archive")
        executed = _mapping(archive["executed_interval"], "executed interval")
        checkpoint = shard_checkpoint_payload(
            shard=shard,
            execution_source_fingerprint=str(context["source_fingerprint"]),
            outer_plan_sha256=sha256_path(S4_OUTER_ARCHIVE_PATH),
            execution_mode=execution_mode,
            realized_soc_mwh=cast(Sequence[float], archive["post_step_soc_mwh"]),
            preceding_controlling_attempt_id=str(executed["controlling_attempt_id"]),
            windows=windows,
        )
        write_shard_checkpoint(directory / "checkpoint.json", checkpoint)
        verify_shard_artifacts(directory, shard=shard, outer=outer)
        timing.append(record)
    child_usage_stop = resource.getrusage(resource.RUSAGE_CHILDREN)
    prior_child_cpu = sum(
        float(
            cast(
                float,
                _mapping(json.loads(path.read_text()), "termination record").get(
                    "completed_child_cpu_seconds", 0.0
                ),
            )
        )
        for path in directory.glob("termination-*.json")
    )
    result = {
        **audit_shard(directory, shard=shard, outer=outer),
        "execution_context": context,
        "execution_mode": execution_mode,
        "worker_pid": os.getpid(),
        "window_supervision": timing,
        "completed_child_cpu_seconds": (
            prior_child_cpu
            + child_usage_stop.ru_utime
            + child_usage_stop.ru_stime
            - child_usage_start.ru_utime
            - child_usage_start.ru_stime
        ),
    }
    atomic_immutable_json(directory / "shard-result.json", result)
    return result


class _WorkerInterrupted(BaseException):
    pass


def run_shard_worker(
    directory: Path,
    *,
    shard_id: str,
    authority_path: Path = DEFAULT_AUTHORITY_PATH,
    reviewed_resume: bool = False,
    execution_mode: str = "ordinary",
) -> Mapping[str, object]:
    """Run a shard and retain any post-preflight abnormal termination."""
    previous_handler = signal.getsignal(signal.SIGTERM)
    child_usage_start = resource.getrusage(resource.RUSAGE_CHILDREN)

    def interrupt(_signum: int, _frame: object) -> None:
        raise _WorkerInterrupted("S4b worker received SIGTERM")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        return _run_shard_worker_body(
            directory,
            shard_id=shard_id,
            authority_path=authority_path,
            reviewed_resume=reviewed_resume,
            execution_mode=execution_mode,
        )
    except BaseException as exc:
        if directory.is_dir():
            checkpoint_path = directory / "checkpoint.json"
            completed = 0
            checkpoint_sha: str | None = None
            if checkpoint_path.is_file():
                try:
                    checkpoint = _mapping(
                        json.loads(checkpoint_path.read_text()), "partial checkpoint"
                    )
                    completed = int(cast(int, checkpoint["completed_intervals"]))
                    checkpoint_sha = sha256_path(checkpoint_path)
                except Exception:
                    pass
            child_usage_stop = resource.getrusage(resource.RUSAGE_CHILDREN)
            termination_ordinal = len(list(directory.glob("termination-*.json")))
            atomic_immutable_json(
                directory / f"termination-{termination_ordinal:03d}.json",
                {
                    "schema_version": SCHEMA_VERSION,
                    "classification": (
                        "supervisor_interrupted"
                        if isinstance(exc, _WorkerInterrupted)
                        else "worker_failure"
                    ),
                    "shard_id": shard_id,
                    "completed_intervals": completed,
                    "checkpoint_sha256": checkpoint_sha,
                    "completed_child_cpu_seconds": (
                        child_usage_stop.ru_utime
                        + child_usage_stop.ru_stime
                        - child_usage_start.ru_utime
                        - child_usage_start.ru_stime
                    ),
                    "exception": f"{type(exc).__name__}: {exc}",
                },
            )
        raise
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def _supervise_shards_impl(
    shard_ids: Sequence[str],
    *,
    authority_path: Path = DEFAULT_AUTHORITY_PATH,
    poll_seconds: float = POLL_SECONDS,
    observation_reader: Callable[
        [], Sequence[ProcessObservation]
    ] = process_observations,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    reviewed_resume: bool = False,
    run_label: str = "ordinary",
) -> Mapping[str, object]:
    """Launch one or two fresh shard workers under tree-aware resource limits."""
    if not 1 <= len(shard_ids) <= 2 or len(set(shard_ids)) != len(shard_ids):
        raise ValueError("S4b supervision requires one or two unique shards")
    valid_request = run_label in QUALIFICATION_RUNS and (
        tuple(shard_ids) == QUALIFICATION_RUNS[run_label]
        or (
            run_label == "partitioned_fresh_sequential"
            and len(shard_ids) == 1
            and shard_ids[0] in QUALIFICATION_RUNS[run_label]
        )
    )
    if not valid_request or run_label == "partitioned_one_process":
        raise ValueError("S4b shard request does not match a frozen qualification arm")
    context = execution_context()
    if context["git_clean"] is not True:
        raise ValueError("S4b supervision requires a clean committed worktree")
    authority = load_qualification_authority(
        authority_path,
        expected_execution_commit=str(context["git_commit"]),
        expected_source_fingerprint=str(context["source_fingerprint"]),
    )
    outer = _outer()
    selected = [qualification_shard_entry(shard_id, outer)[1] for shard_id in shard_ids]
    directories = [
        ROOT
        / str(
            _mapping(shard["run_locations"], "qualification run locations")[run_label]
        )
        for shard in selected
    ]
    if any(path.exists() for path in directories) and not reviewed_resume:
        raise FileExistsError("S4b shard output already exists")
    if reviewed_resume and any(not path.exists() for path in directories):
        raise FileNotFoundError("reviewed S4b resume requires every partial shard")
    supervision_root = (
        ROOT / "experiments/case118_annual_hierarchy/results/s4b_qualification"
    )
    supervision_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    processes: dict[str, subprocess.Popen[bytes]] = {}
    logs: dict[str, tuple[Path, object]] = {}
    samples: list[Mapping[str, object]] = []
    triggers: list[Mapping[str, object]] = []
    peak_per_worker = {shard_id: 0.0 for shard_id in shard_ids}
    peak_aggregate = 0.0
    maximum_concurrency = 0
    interruption: BaseException | None = None
    supervisor_error: Exception | None = None
    try:
        for shard_id, directory in zip(shard_ids, directories, strict=True):
            resume_ordinal = len(list(directory.glob("reviewed-continuation-*.json")))
            log_path = supervision_root / (
                f"{run_label}-{shard_id}-worker"
                f"{'-resume-' + format(resume_ordinal, '03d') if reviewed_resume else ''}.log"
            )
            log = log_path.open("xb")
            command = [
                sys.executable,
                "-m",
                "experiments.case118_annual_hierarchy.run_s4b",
                "--worker",
                "--directory",
                str(directory),
                "--shard-id",
                shard_id,
                "--authority",
                str(authority_path),
                "--execution-mode",
                run_label,
            ]
            if reviewed_resume:
                command.append("--reviewed-resume")
            process = popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            processes[shard_id] = process
            logs[shard_id] = (log_path, log)
        while any(process.poll() is None for process in processes.values()):
            active = {
                shard_id: process
                for shard_id, process in processes.items()
                if process.poll() is None
            }
            maximum_concurrency = max(maximum_concurrency, len(active))
            if active:
                observations = observation_reader()
                usage = process_tree_usage(
                    [process.pid for process in active.values()], observations
                )
                supervisor_observation = next(
                    (item for item in observations if item.pid == os.getpid()), None
                )
                if supervisor_observation is None:
                    raise ValueError("S4b supervisor current-RSS sample is unavailable")
                per_worker = cast(
                    Mapping[str, Mapping[str, object]], usage["per_worker"]
                )
                for shard_id, process in active.items():
                    rss = float(cast(float, per_worker[str(process.pid)]["rss_mib"]))
                    peak_per_worker[shard_id] = max(peak_per_worker[shard_id], rss)
                    if rss > 16_384.0:
                        triggers.append(
                            {
                                "kind": "per_worker_rss_limit",
                                "shard_id": shard_id,
                                "rss_mib": rss,
                            }
                        )
                aggregate = float(cast(float, usage["aggregate_rss_mib"]))
                peak_aggregate = max(peak_aggregate, aggregate)
                if aggregate > 24_576.0:
                    triggers.append(
                        {"kind": "aggregate_rss_limit", "rss_mib": aggregate}
                    )
                samples.append(
                    {
                        "elapsed_seconds": time.monotonic() - started,
                        "active_shards": sorted(active),
                        "supervisor_current_rss_mib": supervisor_observation.rss_mib,
                        "supervisor_cpu_seconds": supervisor_observation.cpu_seconds,
                        **usage,
                    }
                )
            if triggers:
                for process in active.values():
                    _terminate(process, WORKER_TERMINATION_GRACE_SECONDS)
                break
            time.sleep(poll_seconds)
    except Exception as exc:
        for process in processes.values():
            _terminate(process, WORKER_TERMINATION_GRACE_SECONDS)
        supervisor_error = exc
    except BaseException as exc:
        for process in processes.values():
            _terminate(process, WORKER_TERMINATION_GRACE_SECONDS)
        interruption = exc
    finally:
        for _path, handle in logs.values():
            cast(Any, handle).close()
    returncodes = {shard_id: process.wait() for shard_id, process in processes.items()}
    results: dict[str, Mapping[str, object]] = {}
    artifact_error: str | None = None
    if (
        interruption is None
        and supervisor_error is None
        and not triggers
        and not samples
    ):
        artifact_error = "S4b supervision retained no successful process-tree sample"
    if (
        interruption is None
        and supervisor_error is None
        and not triggers
        and all(value == 0 for value in returncodes.values())
        and artifact_error is None
    ):
        try:
            outer = _outer()
            for shard_id, shard, directory in zip(
                shard_ids, selected, directories, strict=True
            ):
                worker_result = _mapping(
                    json.loads((directory / "shard-result.json").read_text()),
                    "shard worker result",
                )
                reconstructed = audit_shard(directory, shard=shard, outer=outer)
                for name, value in reconstructed.items():
                    if worker_result.get(name) != value:
                        raise ValueError(
                            f"S4b worker result disagrees with independent audit: {name}"
                        )
                results[shard_id] = worker_result
        except Exception as exc:
            artifact_error = f"{type(exc).__name__}: {exc}"
    classification = (
        "resource_limit"
        if triggers
        else "supervisor_interrupted"
        if interruption is not None
        else "supervisor_failure"
        if supervisor_error is not None
        else "artifact_failure"
        if artifact_error is not None
        else "worker_failure"
        if any(value != 0 for value in returncodes.values())
        else "accepted"
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classification": classification,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "authority": authority,
        "execution_context": context,
        "requested_shards": list(shard_ids),
        "run_label": run_label,
        "requested_concurrency": len(shard_ids),
        "maximum_observed_concurrency": maximum_concurrency,
        "returncodes": returncodes,
        "worker_root_pids": {
            shard_id: process.pid for shard_id, process in processes.items()
        },
        "resource_triggers": triggers,
        "resource_samples": samples,
        "peak_worker_rss_mib": peak_per_worker,
        "peak_aggregate_rss_mib": peak_aggregate,
        "elapsed_critical_path_seconds": time.monotonic() - started,
        "artifact_error": artifact_error,
        "supervisor_exception": (
            f"{type(interruption).__name__}: {interruption}"
            if interruption is not None
            else f"{type(supervisor_error).__name__}: {supervisor_error}"
            if supervisor_error is not None
            else None
        ),
        "supervisor_exception_kind": (
            "interruption"
            if interruption is not None
            else "failure"
            if supervisor_error is not None
            else None
        ),
        "worker_results": results,
        "worker_logs": {
            shard_id: {
                "path": path.name,
                "sha256": sha256_path(path),
            }
            for shard_id, (path, _handle) in logs.items()
        },
    }
    resume_suffix = ""
    if reviewed_resume:
        resume_suffix = "-resume-" + format(
            max(
                len(list(path.glob("reviewed-continuation-*.json")))
                for path in directories
            ),
            "03d",
        )
    name = (
        "supervision-" + run_label + "-" + "-".join(shard_ids) + resume_suffix + ".json"
    )
    atomic_immutable_json(supervision_root / name, payload)
    if interruption is not None:
        raise interruption
    return payload


def supervise_shards(
    shard_ids: Sequence[str],
    *,
    authority_path: Path = DEFAULT_AUTHORITY_PATH,
    poll_seconds: float = POLL_SECONDS,
    observation_reader: Callable[
        [], Sequence[ProcessObservation]
    ] = process_observations,
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    reviewed_resume: bool = False,
    run_label: str = "ordinary",
) -> Mapping[str, object]:
    """Run tree-aware supervision with catchable external termination."""
    previous_handler = signal.getsignal(signal.SIGTERM)

    def interrupt(_signum: int, _frame: object) -> None:
        raise _WorkerInterrupted("S4b supervisor received SIGTERM")

    signal.signal(signal.SIGTERM, interrupt)
    try:
        return _supervise_shards_impl(
            shard_ids,
            authority_path=authority_path,
            poll_seconds=poll_seconds,
            observation_reader=observation_reader,
            popen=popen,
            reviewed_resume=reviewed_resume,
            run_label=run_label,
        )
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


def run_partitioned_one_process(
    *, authority_path: Path = DEFAULT_AUTHORITY_PATH
) -> Mapping[str, object]:
    """Execute both forced-boundary shards in one retained worker process."""
    context = execution_context()
    if context["git_clean"] is not True:
        raise ValueError("S4b qualification requires a clean committed worktree")
    authority = load_qualification_authority(
        authority_path,
        expected_execution_commit=str(context["git_commit"]),
        expected_source_fingerprint=str(context["source_fingerprint"]),
    )
    outer = _outer()
    root = (
        ROOT
        / "experiments/case118_annual_hierarchy/results/s4b_qualification/partitioned_one_process"
    )
    if root.exists():
        raise FileExistsError("S4b one-process qualification output exists")
    results = []
    for shard_id in QUALIFICATION_RUNS["partitioned_one_process"]:
        _, shard = qualification_shard_entry(shard_id, outer)
        directory = ROOT / str(
            _mapping(shard["run_locations"], "qualification run locations")[
                "partitioned_one_process"
            ]
        )
        results.append(
            _run_shard_worker_body(
                directory,
                shard_id=shard_id,
                authority_path=authority_path,
                execution_mode="partitioned_one_process",
            )
        )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "classification": "accepted",
        "run_label": "partitioned_one_process",
        "authority": authority,
        "execution_context": context,
        "worker_pid": os.getpid(),
        "worker_results": results,
    }
    atomic_immutable_json(root / "run-result.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-child", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--merge", action="store_true")
    parser.add_argument("--supervise", action="store_true")
    parser.add_argument("--one-process", action="store_true")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--shard-id")
    parser.add_argument("--iteration", type=int)
    parser.add_argument("--primary-timeout", type=float)
    parser.add_argument("--reviewed-resume", action="store_true")
    parser.add_argument("--execution-mode", default="ordinary")
    parser.add_argument("--run-label", default="ordinary")
    parser.add_argument("--summaries", type=Path, nargs="*")
    parser.add_argument("--shard-ids", nargs="*")
    parser.add_argument("--authority", type=Path, default=DEFAULT_AUTHORITY_PATH)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-source-fingerprint")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (
        sum(
            (
                args.window_child,
                args.worker,
                args.merge,
                args.supervise,
                args.one_process,
            )
        )
        != 1
    ):
        raise ValueError("select exactly one S4b execution mode")
    if args.window_child:
        if args.directory is None or args.shard_id is None or args.iteration is None:
            raise ValueError("window child requires directory, shard ID, and iteration")
        if args.expected_commit is None or args.expected_source_fingerprint is None:
            raise ValueError("window child requires frozen execution provenance")
        execute_one_window_child(
            args.directory,
            shard_id=args.shard_id,
            iteration=args.iteration,
            primary_timeout_seconds=args.primary_timeout,
            authority_path=args.authority,
            expected_commit=args.expected_commit,
            expected_source_fingerprint=args.expected_source_fingerprint,
        )
    elif args.worker:
        if args.directory is None or args.shard_id is None:
            raise ValueError("worker requires directory and shard ID")
        print(
            json.dumps(
                run_shard_worker(
                    args.directory,
                    shard_id=args.shard_id,
                    authority_path=args.authority,
                    reviewed_resume=args.reviewed_resume,
                    execution_mode=args.execution_mode,
                )
            )
        )
    elif args.merge:
        if not args.summaries or args.output is None:
            raise ValueError("merge requires shard summaries and output")
        summaries = [json.loads(path.read_text()) for path in args.summaries]
        merged = merge_shard_summaries(summaries)
        atomic_immutable_json(args.output, merged)
        print(json.dumps(merged))
    elif args.supervise:
        if not args.shard_ids:
            raise ValueError("supervision requires one or two shard IDs")
        print(
            json.dumps(
                supervise_shards(
                    args.shard_ids,
                    authority_path=args.authority,
                    reviewed_resume=args.reviewed_resume,
                    run_label=args.run_label,
                )
            )
        )
    else:
        print(json.dumps(run_partitioned_one_process(authority_path=args.authority)))


if __name__ == "__main__":
    main()
