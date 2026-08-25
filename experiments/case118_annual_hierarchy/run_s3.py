"""Run the supervised one-month Case118 S3 hierarchy."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
from typing import Mapping, cast

from experiments.case118_annual_hierarchy.p0_fixture import (
    frozen_p0_policy,
    frozen_p0_solve_config,
    policy_sha256,
)
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.s3_fixture import (
    S3_HORIZON_STEPS,
    S3_RESTART_BOUNDARIES,
    load_s3_fixture,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    load_verified_outer_plan_archive,
    outer_boundaries,
    residual_tolerances,
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_driver import (
    SafeBoundaryState,
    run_streaming_trajectory,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
    load_verified_checkpoint,
    sha256_path,
)


EXPERIMENT_DIR = ROOT / "experiments/case118_annual_hierarchy"
DEFAULT_OUTPUT_DIRECTORY = EXPERIMENT_DIR / "results/s3_month_rated"
RSS_LIMIT_MIB = 24.0 * 1024.0
CHECKPOINT_STALL_LIMIT_SECONDS = 60.0 * 60.0
INVOCATION_WALL_LIMIT_SECONDS = 4.0 * 60.0 * 60.0
TOTAL_WALL_LIMIT_SECONDS = 72.0 * 60.0 * 60.0
POLL_SECONDS = 1.0
SCHEMA_VERSION = 1
REVIEWABLE_CLASSIFICATIONS = frozenset(
    {
        "rss_limit",
        "invocation_wall_limit",
        "total_wall_limit",
        "checkpoint_stall_limit",
        "worker_failure",
        "artifact_failure",
        "provenance_mismatch",
        "reviewed_interruption",
    }
)

S3_SOURCE_PATHS = (
    "experiments/case118_annual_hierarchy/S3_PROTOCOL.md",
    "experiments/case118_annual_hierarchy/audit.py",
    "experiments/case118_annual_hierarchy/p0_fixture.py",
    "experiments/case118_annual_hierarchy/pglib_case.py",
    "experiments/case118_annual_hierarchy/run_s0.py",
    "experiments/case118_annual_hierarchy/run_s3.py",
    "experiments/case118_annual_hierarchy/s2_analysis.py",
    "experiments/case118_annual_hierarchy/s2_fixture.py",
    "experiments/case118_annual_hierarchy/s3_analysis.py",
    "experiments/case118_annual_hierarchy/s3_fixture.py",
    "experiments/case118_annual_hierarchy/scenario.py",
    "experiments/case118_annual_hierarchy/streaming_archive.py",
    "experiments/case118_annual_hierarchy/streaming_driver.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
    "experiments/case118_annual_hierarchy/streaming_schema.py",
    "tests/test_case118_s3_fixture.py",
    "tests/test_case118_s3_analysis.py",
    "tests/test_case118_s3_runner.py",
)


@dataclass(frozen=True)
class ReviewedPriorOutcome:
    """The retained lifecycle outcome that authorizes one reviewed continuation."""

    prior_record_kind: str
    prior_invocation: int
    prior_classification: str
    completed_intervals: int
    checkpoint_sha256: str
    execution_context: Mapping[str, object]
    prior_record_path: str
    prior_record_sha256: str

    def authorization_identity(self, *, next_invocation: int) -> Mapping[str, object]:
        return {
            "next_invocation": next_invocation,
            "prior_record_kind": self.prior_record_kind,
            "prior_invocation": self.prior_invocation,
            "prior_classification": self.prior_classification,
            "completed_intervals": self.completed_intervals,
            "checkpoint_sha256": self.checkpoint_sha256,
            "execution_context": dict(self.execution_context),
            "prior_record_path": self.prior_record_path,
            "prior_record_sha256": self.prior_record_sha256,
        }


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _software_versions() -> Mapping[str, object]:
    values: dict[str, object] = {"python": platform.python_version()}
    for package in ("cvxopf", "cvxpy", "numpy", "pandas", "clarabel", "cyipopt"):
        try:
            values[package] = version(package)
        except PackageNotFoundError:
            values[package] = None
    try:
        import cyipopt

        values["ipopt"] = list(cyipopt.IPOPT_VERSION)
    except (ImportError, AttributeError):
        values["ipopt"] = None
    return values


def s3_source_paths() -> tuple[Path, ...]:
    """Return the complete deterministic S3 execution-source registry."""
    paths = [ROOT / name for name in S3_SOURCE_PATHS]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    resolved = tuple(sorted(set(paths)))
    missing = [path.relative_to(ROOT).as_posix() for path in resolved if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"S3 source registry is incomplete: {missing}")
    return resolved


def s3_source_fingerprint() -> str:
    """Hash every source used by the S3 scientific execution path."""
    digest = hashlib.sha256()
    for path in s3_source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def execution_context() -> Mapping[str, object]:
    """Return immutable source, scenario, policy, solver, and environment identity."""
    fixture = load_s3_fixture()
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": s3_source_fingerprint(),
        "scenario_hash": fixture.scenario_hash,
        "component_hashes": dict(fixture.hashes),
        "policy_sha256": fixture.policy_sha256,
        "solve_config_sha256": fixture.solve_config_sha256,
        "software_versions": dict(_software_versions()),
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }


def _safe_execution_context() -> Mapping[str, object]:
    try:
        return execution_context()
    except Exception as exc:
        return {"context_error": f"{type(exc).__name__}: {exc}"}


def observer_reason(completed_intervals: int, *, passed_boundary: int) -> str | None:
    """Return the frozen outcome at one global completed-interval boundary."""
    if not 0 <= passed_boundary <= completed_intervals <= S3_HORIZON_STEPS:
        raise ValueError("invalid S3 global boundary ordering")
    if completed_intervals == S3_HORIZON_STEPS:
        return "study_complete"
    if completed_intervals > passed_boundary and completed_intervals in S3_RESTART_BOUNDARIES:
        return "planned_recycle"
    return None


def _worker_result_path(directory: Path, invocation: int) -> Path:
    return directory / f"worker-result-{invocation:03d}.json"


def _supervision_path(directory: Path, invocation: int) -> Path:
    return directory / f"supervision-{invocation:03d}.json"


def _checkpoint_candidate(path: Path) -> tuple[int, str] | None:
    try:
        encoded = path.read_bytes()
        return int(json.loads(encoded)["completed_intervals"]), hashlib.sha256(encoded).hexdigest()
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def verify_checkpoint(path: Path) -> Mapping[str, object]:
    """Verify the S3 outer artifact and complete immutable checkpoint prefix."""
    fixture = load_s3_fixture()
    policy = frozen_p0_policy()
    outer_path = path.parent / "outer-plan.json.gz"
    outer_sha = sha256_path(outer_path)
    outer = load_verified_outer_plan_archive(
        outer_path,
        inputs=fixture.inputs,
        policy=policy,
        expected_solve_config_sha256=fixture.solve_config_sha256,
        expected_source_fingerprint=s3_source_fingerprint(),
        expected_scenario_hash=fixture.scenario_hash,
    )
    return load_verified_checkpoint(
        path,
        expected_source_fingerprint=s3_source_fingerprint(),
        expected_scenario_hash=fixture.scenario_hash,
        expected_outer_plan_sha256=outer_sha,
        expected_policy_hash=policy_sha256(policy),
        expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=residual_tolerances(policy),
        expected_inner_terminal_policy=policy.inner_terminal_policy,
        expected_horizon_steps=S3_HORIZON_STEPS,
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=result_dimensions(fixture.inputs),
        expected_delta_hours=fixture.inputs.delta,
        expected_outer_boundary_soc_mwh=outer_boundaries(outer),
    )


def _worker(
    directory: Path,
    *,
    invocation: int,
    passed_boundary: int,
    expected_commit: str,
    expected_source_fingerprint: str,
    expected_outer_sha256: str | None,
) -> int:
    started = time.monotonic()
    start_context = _safe_execution_context()
    provenance_matches = bool(
        start_context.get("git_commit") == expected_commit
        and start_context.get("git_clean") is True
        and start_context.get("source_fingerprint") == expected_source_fingerprint
    )
    if not provenance_matches:
        atomic_immutable_json(
            _worker_result_path(directory, invocation),
            {
                "schema_version": SCHEMA_VERSION,
                "classification": "provenance_mismatch",
                "start_context": start_context,
            },
        )
        return 2
    outer_path = directory / "trajectory/outer-plan.json.gz"
    if passed_boundary > 0:
        try:
            if expected_outer_sha256 is None:
                raise ValueError("resumed S3 worker requires pinned outer identity")
            if sha256_path(outer_path) != expected_outer_sha256:
                raise ValueError("resumed S3 worker outer-plan SHA-256 mismatch")
            verify_checkpoint(directory / "trajectory/checkpoint.json")
        except Exception as exc:
            atomic_immutable_json(
                _worker_result_path(directory, invocation),
                {
                    "schema_version": SCHEMA_VERSION,
                    "classification": "artifact_failure",
                    "completed_intervals": passed_boundary,
                    "exception": f"{type(exc).__name__}: {exc}",
                    "start_context": start_context,
                    "context_matches": True,
                },
            )
            return 1

    def observer(state: SafeBoundaryState) -> str | None:
        return observer_reason(state.completed_intervals, passed_boundary=passed_boundary)

    try:
        fixture = load_s3_fixture()
        trajectory = run_streaming_trajectory(
            directory / "trajectory",
            fixture.inputs,
            frozen_p0_policy(),
            frozen_p0_solve_config(),
            source_fingerprint=expected_source_fingerprint,
            scenario_hash=fixture.scenario_hash,
            resume=passed_boundary > 0,
            observer=observer,
        )
        exception = None
    except Exception as exc:
        trajectory = None
        exception = f"{type(exc).__name__}: {exc}"
    end_context = _safe_execution_context()
    context_matches = start_context == end_context
    status = None if trajectory is None else trajectory.status
    completed = passed_boundary if trajectory is None else trajectory.completed_intervals
    reason = exception if trajectory is None else trajectory.termination_reason
    classification = (
        "worker_failure"
        if trajectory is None
        else str(reason)
        if status == "observer_terminated" and reason in {"planned_recycle", "study_complete"}
        else str(status)
    )
    atomic_immutable_json(
        _worker_result_path(directory, invocation),
        {
            "schema_version": SCHEMA_VERSION,
            "invocation": invocation,
            "completed_intervals": completed,
            "classification": classification,
            "trajectory_status": status,
            "termination_reason": reason,
            "start_context": start_context,
            "end_context": end_context,
            "context_matches": context_matches,
            "wall_time_seconds": time.monotonic() - started,
        },
    )
    return 0 if trajectory is not None and context_matches else 1


def _child_rss_mib(pid: int) -> float | None:
    if sys.platform.startswith("linux"):
        try:
            fields = Path(f"/proc/{pid}/statm").read_text().split()
            return int(fields[1]) * os.sysconf("SC_PAGE_SIZE") / (1024.0**2)
        except (FileNotFoundError, IndexError, OSError, ValueError):
            return None
    completed = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    try:
        return float(completed.stdout.strip()) / 1024.0
    except ValueError:
        return None


def _terminate(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10.0)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _prior_wall_seconds(directory: Path) -> float:
    total = 0.0
    for pattern in ("supervision-*.json", "interrupted-invocation-*.json"):
        for path in directory.glob(pattern):
            payload = _mapping(json.loads(path.read_text()), "S3 wall record")
            total += float(cast(float, payload["wall_time_seconds"]))
    return total


def _next_invocation(directory: Path) -> int:
    """Return the next lifecycle identity, ignoring merely prepared artifacts."""
    indices: list[int] = []
    for pattern in (
        "supervision-*.json",
        "interrupted-invocation-*.json",
    ):
        for path in directory.glob(pattern):
            try:
                indices.append(int(path.stem.rsplit("-", maxsplit=1)[1]))
            except (IndexError, ValueError):
                continue
    return 0 if not indices else max(indices) + 1


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise ValueError(f"cannot verify S3 process PID {pid}") from exc
    return True


def _archive_stale_active_invocation(directory: Path) -> Mapping[str, object] | None:
    """Retain one interrupted invocation after explicit reviewed recovery."""
    active_path = directory / "active-invocation.json"
    if not active_path.is_file():
        return None
    active = _mapping(json.loads(active_path.read_text()), "active S3 invocation")
    try:
        invocation = int(cast(int, active["invocation"]))
        supervisor_pid = int(cast(int, active["supervisor_pid"]))
        worker_pid = int(cast(int, active["worker_pid"]))
        started_epoch = float(cast(float, active["started_epoch_seconds"]))
        completed_before = int(cast(int, active["completed_before"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("active S3 invocation record is malformed") from exc
    if _pid_is_alive(supervisor_pid) or _pid_is_alive(worker_pid):
        raise ValueError("another S3 supervisor or worker is still active")
    if _supervision_path(directory, invocation).is_file():
        active_path.unlink()
        return None
    reviewed_epoch = time.time()
    checkpoint = _checkpoint_candidate(directory / "trajectory/checkpoint.json")
    if checkpoint is None or checkpoint[0] < completed_before:
        raise ValueError("interrupted S3 invocation lacks a valid retained checkpoint")
    context_path = directory / f"run-context-{invocation:03d}.json"
    if not context_path.is_file():
        raise ValueError("interrupted S3 invocation lacks its run context")
    start_context = _mapping(
        json.loads(context_path.read_text()), "interrupted S3 run context"
    )
    end_context = _safe_execution_context()
    record: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "classification": "reviewed_interruption",
        "invocation": invocation,
        "supervisor_pid": supervisor_pid,
        "worker_pid": worker_pid,
        "started_epoch_seconds": started_epoch,
        "reviewed_epoch_seconds": reviewed_epoch,
        "wall_time_seconds": max(0.0, reviewed_epoch - started_epoch),
        "completed_before": completed_before,
        "completed_after": checkpoint[0],
        "checkpoint_sha256_before": active.get("checkpoint_sha256_before"),
        "checkpoint_sha256_after": checkpoint[1],
        "outer_plan_sha256": (
            sha256_path(directory / "trajectory/outer-plan.json.gz")
            if (directory / "trajectory/outer-plan.json.gz").is_file()
            else None
        ),
        "start_context": start_context,
        "end_context": end_context,
        "context_matches": start_context == end_context,
    }
    atomic_immutable_json(
        directory / f"interrupted-invocation-{invocation:03d}.json", record
    )
    active_path.unlink()
    return record


def supervise_invocation(directory: Path) -> Mapping[str, object]:
    """Launch and externally supervise one S3 worker invocation."""
    checkpoint_path = directory / "trajectory/checkpoint.json"
    before = _checkpoint_candidate(checkpoint_path)
    if checkpoint_path.exists() and before is None:
        raise ValueError("existing S3 checkpoint is unreadable")
    if before is not None:
        verify_checkpoint(checkpoint_path)
    outer_path = directory / "trajectory/outer-plan.json.gz"
    outer_sha_before = sha256_path(outer_path) if outer_path.is_file() else None
    passed_boundary = 0 if before is None else before[0]
    invocation = _next_invocation(directory)
    prior_wall = _prior_wall_seconds(directory)
    if prior_wall >= TOTAL_WALL_LIMIT_SECONDS:
        raise ValueError("S3 cumulative wall authorization is exhausted")
    context = execution_context()
    if context["git_clean"] is not True:
        raise ValueError("S3 execution requires a clean committed worktree")
    context_path = directory / f"run-context-{invocation:03d}.json"
    if context_path.is_file():
        prepared_context = _mapping(
            json.loads(context_path.read_text()), "prepared S3 run context"
        )
        if prepared_context != context:
            raise ValueError("prepared S3 invocation context mismatch")
        if (
            _worker_result_path(directory, invocation).exists()
            or (directory / "active-invocation.json").exists()
        ):
            raise ValueError("prepared S3 invocation has ambiguous execution evidence")
    else:
        atomic_immutable_json(context_path, context)
    command = [
        sys.executable,
        "-m",
        "experiments.case118_annual_hierarchy.run_s3",
        "--worker",
        "--output-directory",
        str(directory),
        "--invocation",
        str(invocation),
        "--passed-boundary",
        str(passed_boundary),
        "--expected-commit",
        str(context["git_commit"]),
        "--expected-source-fingerprint",
        str(context["source_fingerprint"]),
    ]
    if outer_sha_before is not None:
        command.extend(["--expected-outer-sha256", outer_sha_before])
    log_path = directory / f"worker-{invocation:03d}.log"
    active_path = directory / "active-invocation.json"
    started = time.monotonic()
    last_progress = started
    peak_rss = 0.0
    first_rss: float | None = None
    final_rss: float | None = None
    stop_reason: str | None = None
    artifact_error: str | None = None
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        atomic_json(
            active_path,
            {
                "invocation": invocation,
                "supervisor_pid": os.getpid(),
                "worker_pid": process.pid,
                "started_epoch_seconds": time.time(),
                "completed_before": passed_boundary,
                "checkpoint_sha256_before": None if before is None else before[1],
            },
        )
        last_completed = passed_boundary
        while process.poll() is None:
            now = time.monotonic()
            rss = _child_rss_mib(process.pid)
            if rss is not None:
                first_rss = rss if first_rss is None else first_rss
                final_rss = rss
                peak_rss = max(peak_rss, rss)
                if rss > RSS_LIMIT_MIB:
                    stop_reason = "rss_limit"
            candidate = _checkpoint_candidate(checkpoint_path)
            if checkpoint_path.exists() and candidate is None:
                artifact_error = "existing S3 checkpoint became unreadable"
            elif candidate is not None and candidate[0] > last_completed:
                try:
                    verify_checkpoint(checkpoint_path)
                except Exception as exc:
                    artifact_error = f"{type(exc).__name__}: {exc}"
                else:
                    if _checkpoint_candidate(checkpoint_path) == candidate:
                        last_completed = candidate[0]
                        last_progress = now
            elapsed = now - started
            if elapsed > INVOCATION_WALL_LIMIT_SECONDS:
                stop_reason = "invocation_wall_limit"
            elif prior_wall + elapsed > TOTAL_WALL_LIMIT_SECONDS:
                stop_reason = "total_wall_limit"
            elif now - last_progress > CHECKPOINT_STALL_LIMIT_SECONDS:
                stop_reason = "checkpoint_stall_limit"
            if artifact_error is not None or stop_reason is not None:
                _terminate(process)
                break
            time.sleep(POLL_SECONDS)
        returncode = process.wait()
    ended = time.monotonic()
    after = _checkpoint_candidate(checkpoint_path)
    if after is not None and artifact_error is None:
        try:
            verify_checkpoint(checkpoint_path)
        except Exception as exc:
            artifact_error = f"{type(exc).__name__}: {exc}"
    worker_path = _worker_result_path(directory, invocation)
    worker = (
        _mapping(json.loads(worker_path.read_text()), "S3 worker result")
        if worker_path.is_file()
        else None
    )
    end_context = _safe_execution_context()
    context_matches = context == end_context
    if artifact_error is not None:
        classification = "artifact_failure"
    elif stop_reason is not None:
        classification = stop_reason
    elif not context_matches:
        classification = "provenance_mismatch"
    elif worker is not None and worker.get("classification") == "provenance_mismatch":
        classification = "provenance_mismatch"
    elif worker is not None and worker.get("classification") == "artifact_failure":
        classification = "artifact_failure"
        artifact_error = str(worker.get("exception"))
    elif returncode != 0 or worker is None or worker.get("context_matches") is not True:
        classification = "worker_failure"
    else:
        classification = str(worker.get("classification"))
    if classification == "planned_recycle" and not (
        after is not None
        and after[0] > passed_boundary
        and after[0] in S3_RESTART_BOUNDARIES
        and worker is not None
        and worker.get("completed_intervals") == after[0]
    ):
        classification = "artifact_failure"
        artifact_error = "planned recycle lacks its exact scheduled checkpoint"
    if classification == "study_complete" and not (
        after is not None
        and after[0] == S3_HORIZON_STEPS
        and worker is not None
        and worker.get("completed_intervals") == S3_HORIZON_STEPS
    ):
        classification = "artifact_failure"
        artifact_error = "study completion lacks its boundary-720 checkpoint"
    record: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "invocation": invocation,
        "classification": classification,
        "artifact_error": artifact_error,
        "returncode": returncode,
        "completed_before": passed_boundary,
        "completed_after": None if after is None else after[0],
        "checkpoint_sha256_before": None if before is None else before[1],
        "checkpoint_sha256_after": None if after is None else after[1],
        "outer_plan_sha256": (
            sha256_path(directory / "trajectory/outer-plan.json.gz")
            if (directory / "trajectory/outer-plan.json.gz").is_file()
            else None
        ),
        "first_sampled_rss_mib": first_rss,
        "peak_sampled_rss_mib": peak_rss,
        "final_sampled_rss_mib": final_rss,
        "wall_time_seconds": ended - started,
        "prior_total_wall_seconds": prior_wall,
        "start_context": context,
        "end_context": end_context,
        "context_matches": context_matches,
        "worker_result": worker,
        "worker_log": log_path.name,
        "worker_log_sha256": sha256_path(log_path),
        "resource_policy": {
            "rss_limit_mib": RSS_LIMIT_MIB,
            "checkpoint_stall_limit_seconds": CHECKPOINT_STALL_LIMIT_SECONDS,
            "invocation_wall_limit_seconds": INVOCATION_WALL_LIMIT_SECONDS,
            "total_wall_limit_seconds": TOTAL_WALL_LIMIT_SECONDS,
            "poll_seconds": POLL_SECONDS,
        },
    }
    atomic_immutable_json(_supervision_path(directory, invocation), record)
    atomic_json(directory / "latest-supervision.json", record)
    active_path.unlink(missing_ok=True)
    return record


def _validate_reviewed_continuation(directory: Path) -> ReviewedPriorOutcome:
    """Validate and identify the exact retained outcome under review."""
    newly_interrupted = _archive_stale_active_invocation(directory)
    checkpoint_path = directory / "trajectory/checkpoint.json"
    if not checkpoint_path.is_file():
        raise ValueError("reviewed S3 continuation requires a verified checkpoint")
    verify_checkpoint(checkpoint_path)
    latest_path = directory / "latest-supervision.json"
    latest = (
        _mapping(json.loads(latest_path.read_text()), "latest S3 supervision")
        if latest_path.is_file()
        else None
    )
    interruption_paths = sorted(directory.glob("interrupted-invocation-*.json"))
    archived_interruption = (
        _mapping(
            json.loads(interruption_paths[-1].read_text()),
            "archived interrupted S3 invocation",
        )
        if interruption_paths
        else None
    )
    interrupted = newly_interrupted or archived_interruption
    candidates: tuple[tuple[str, Mapping[str, object] | None], ...] = (
        ("supervision", latest),
        ("interrupted_invocation", interrupted),
    )
    available: list[tuple[str, Mapping[str, object]]] = [
        (kind, record)
        for kind, record in candidates
        if record is not None
    ]
    if not available:
        raise ValueError("reviewed S3 continuation requires a retained outcome")
    try:
        def invocation_key(item: tuple[str, Mapping[str, object]]) -> int:
            value = item[1].get("invocation")
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError
            return value

        record_kind, retained = max(
            available,
            key=invocation_key,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("reviewed S3 prior outcome is incomplete") from exc
    if retained.get("classification") in {"planned_recycle", "study_complete"}:
        raise ValueError("normal S3 outcomes do not require reviewed continuation")
    current = execution_context()
    invocation = _next_invocation(directory)
    prior_context_path = directory / f"run-context-{invocation - 1:03d}.json"
    if not prior_context_path.is_file():
        raise ValueError("reviewed S3 continuation lacks prior run context")
    prior_context = _mapping(json.loads(prior_context_path.read_text()), "S3 run context")
    if current.get("git_clean") is not True or current != prior_context:
        raise ValueError("reviewed S3 continuation provenance mismatch")
    try:
        prior_invocation = int(cast(int, retained["invocation"]))
        classification = str(retained["classification"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("reviewed S3 prior outcome is incomplete") from exc
    retained_path = (
        directory / f"interrupted-invocation-{prior_invocation:03d}.json"
        if record_kind == "interrupted_invocation"
        else _supervision_path(directory, prior_invocation)
    )
    if not retained_path.is_file():
        raise ValueError("reviewed S3 prior outcome artifact is missing")
    immutable_retained = _mapping(
        json.loads(retained_path.read_text()), "reviewed S3 prior outcome artifact"
    )
    if immutable_retained != retained or classification not in REVIEWABLE_CLASSIFICATIONS:
        raise ValueError("reviewed S3 prior outcome identity mismatch")
    try:
        completed_intervals = int(cast(int, retained["completed_after"]))
        checkpoint_sha256 = str(retained["checkpoint_sha256_after"])
        retained_context = _mapping(
            retained["start_context"], "reviewed S3 prior execution context"
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("reviewed S3 prior outcome is incomplete") from exc
    checkpoint = _checkpoint_candidate(checkpoint_path)
    if (
        checkpoint is None
        or checkpoint != (completed_intervals, checkpoint_sha256)
        or prior_invocation != invocation - 1
        or retained_context != prior_context
    ):
        raise ValueError("reviewed S3 prior outcome identity mismatch")
    return ReviewedPriorOutcome(
        prior_record_kind=record_kind,
        prior_invocation=prior_invocation,
        prior_classification=classification,
        completed_intervals=completed_intervals,
        checkpoint_sha256=checkpoint_sha256,
        execution_context=retained_context,
        prior_record_path=retained_path.name,
        prior_record_sha256=sha256_path(retained_path),
    )


def run_s3(directory: Path = DEFAULT_OUTPUT_DIRECTORY, *, reviewed: bool = False) -> Mapping[str, object]:
    """Run fresh S3 or explicitly continue its verified partial trajectory."""
    if reviewed:
        if not directory.is_dir():
            raise ValueError("reviewed S3 continuation requires an existing directory")
        prior_outcome = _validate_reviewed_continuation(directory)
        checkpoint_path = directory / "trajectory/checkpoint.json"
        checkpoint = _checkpoint_candidate(checkpoint_path)
        if checkpoint is None:
            raise ValueError("reviewed S3 continuation lacks readable checkpoint")
        invocation = _next_invocation(directory)
        authorization_path = directory / f"reviewed-continuation-{invocation:03d}.json"
        authorization_identity = prior_outcome.authorization_identity(
            next_invocation=invocation
        )
        if (
            authorization_identity["completed_intervals"] != checkpoint[0]
            or authorization_identity["checkpoint_sha256"] != checkpoint[1]
        ):
            raise ValueError("reviewed S3 continuation checkpoint changed")
        if authorization_path.is_file():
            existing = _mapping(
                json.loads(authorization_path.read_text()),
                "reviewed S3 continuation authorization",
            )
            if any(existing.get(key) != value for key, value in authorization_identity.items()):
                raise ValueError("reviewed S3 continuation authorization mismatch")
        else:
            atomic_immutable_json(
                authorization_path,
                {
                    "schema_version": SCHEMA_VERSION,
                    "created_utc": datetime.now(timezone.utc).isoformat(),
                    **authorization_identity,
                },
            )
    else:
        if directory.exists():
            raise FileExistsError("fresh S3 execution requires an absent output directory")
        directory.mkdir(parents=True)
    records: list[Mapping[str, object]] = []
    while True:
        record = supervise_invocation(directory)
        records.append(record)
        if record["classification"] == "planned_recycle":
            continue
        break
    result: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "complete": records[-1]["classification"] == "study_complete",
        "records": records,
        "total_retained_wall_seconds": _prior_wall_seconds(directory),
    }
    atomic_json(directory / "provisional-s3-summary.json", result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY)
    parser.add_argument("--reviewed-continuation", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--invocation", type=int, default=0)
    parser.add_argument("--passed-boundary", type=int, default=0)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-source-fingerprint")
    parser.add_argument("--expected-outer-sha256")
    args = parser.parse_args()
    if args.worker:
        if args.expected_commit is None or args.expected_source_fingerprint is None:
            parser.error("S3 worker requires expected provenance")
        return _worker(
            args.output_directory,
            invocation=args.invocation,
            passed_boundary=args.passed_boundary,
            expected_commit=args.expected_commit,
            expected_source_fingerprint=args.expected_source_fingerprint,
            expected_outer_sha256=args.expected_outer_sha256,
        )
    result = run_s3(args.output_directory, reviewed=args.reviewed_continuation)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["complete"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
