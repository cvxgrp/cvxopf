"""Run the supervised pre-S3 worker-recycling comparison."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import time
from typing import Mapping, Sequence, cast

from experiments.case118_annual_hierarchy.p0_fixture import (
    frozen_p0_policy,
    frozen_p0_solve_config,
    policy_sha256,
)
from experiments.case118_annual_hierarchy.reference.extract_s2_reference import (
    EXPECTED_OUTER_BYTES,
    EXPECTED_OUTER_SHA256,
    HISTORICAL_SOURCE_FINGERPRINT,
    TRACKED_OUTER_PATH,
    verify_tracked_reference,
)
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s2 import s2_source_fingerprint
from experiments.case118_annual_hierarchy.s2_fixture import (
    S2_HORIZON_STEPS,
    load_s2_fixture,
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
DEFAULT_OUTPUT_DIRECTORY = EXPERIMENT_DIR / "results/recycle_comparison"
ARM_BOUNDARIES: Mapping[str, tuple[int, ...]] = {
    "never": (),
    "recycle_32": (32,),
    "recycle_16": (16, 32, 48),
}
ARM_ORDER = tuple(ARM_BOUNDARIES)
STUDY_STOP = 64
RSS_LIMIT_MIB = 24.0 * 1024.0
CHECKPOINT_STALL_LIMIT_SECONDS = 60.0 * 60.0
ARM_WALL_LIMIT_SECONDS = 4.0 * 60.0 * 60.0
TOTAL_WALL_LIMIT_SECONDS = 12.0 * 60.0 * 60.0
POLL_SECONDS = 1.0
SCHEMA_VERSION = 1

# This is the single operational owner of the comparison implementation registry.
COMPARISON_SOURCE_PATHS = (
    "experiments/case118_annual_hierarchy/RECYCLE_COMPARISON_PROTOCOL.md",
    "experiments/case118_annual_hierarchy/recycle_analysis.py",
    "experiments/case118_annual_hierarchy/reference/extract_s2_reference.py",
    "experiments/case118_annual_hierarchy/run_recycle_comparison.py",
    "tests/test_case118_recycle_analysis.py",
    "tests/test_case118_recycle_comparison.py",
    "tests/test_case118_recycle_reference.py",
)


def comparison_source_paths() -> tuple[Path, ...]:
    """Return the complete ordered comparison-only provenance registry."""
    paths = tuple(ROOT / name for name in COMPARISON_SOURCE_PATHS)
    missing = [
        path.relative_to(ROOT).as_posix() for path in paths if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(f"comparison source registry is incomplete: {missing}")
    if tuple(sorted(set(paths))) != paths:
        raise ValueError("comparison source registry must be sorted and unique")
    frozen = set(s2_source_paths_resolved())
    overlap = [path for path in paths if path in frozen]
    if overlap:
        raise ValueError("comparison registry duplicates frozen S2 sources")
    return paths


def s2_source_paths_resolved() -> tuple[Path, ...]:
    """Resolve the frozen S2 registry without making private helper imports."""
    from experiments.case118_annual_hierarchy.run_s2 import s2_source_paths

    return tuple(path.resolve() for path in s2_source_paths())


def comparison_source_fingerprint() -> str:
    """Hash the ordered comparison-only registry using the S2 procedure."""
    digest = hashlib.sha256()
    for path in comparison_source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _git(*arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _software_versions() -> Mapping[str, object]:
    versions: dict[str, object] = {"python": platform.python_version()}
    for package in (
        "cvxopf",
        "cvxpy",
        "numpy",
        "pandas",
        "clarabel",
        "cyipopt",
    ):
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = None
    try:
        import cyipopt

        versions["ipopt"] = list(cyipopt.IPOPT_VERSION)
    except (ImportError, AttributeError):
        versions["ipopt"] = None
    return versions


def execution_context() -> Mapping[str, object]:
    """Return the complete immutable identity required by comparison workers."""
    fixture = load_s2_fixture()
    model_fingerprint = s2_source_fingerprint()
    if model_fingerprint != HISTORICAL_SOURCE_FINGERPRINT:
        raise ValueError("frozen model/streaming fingerprint mismatch")
    verify_tracked_reference()
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_clean": _git("status", "--porcelain") == "",
        "model_source_fingerprint": model_fingerprint,
        "comparison_source_fingerprint": comparison_source_fingerprint(),
        "scenario_hash": fixture.scenario_hash,
        "component_hashes": dict(fixture.hashes),
        "policy_sha256": fixture.policy_sha256,
        "solve_config_sha256": fixture.solve_config_sha256,
        "outer_plan_sha256": EXPECTED_OUTER_SHA256,
        "software_versions": dict(_software_versions()),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "cpu_model": platform.processor(),
        "physical_memory_bytes": _physical_memory_bytes(),
    }


def _safe_execution_context() -> Mapping[str, object]:
    try:
        return execution_context()
    except Exception as exc:
        return {"context_error": f"{type(exc).__name__}: {exc}"}


def _physical_memory_bytes() -> int | None:
    if hasattr(os, "sysconf"):
        try:
            return int(os.sysconf("SC_PHYS_PAGES")) * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError):
            pass
    if sys.platform == "darwin":
        completed = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            return int(completed.stdout.strip())
        except ValueError:
            return None
    return None


def observer_reason(
    arm: str, completed_intervals: int, *, already_passed: int = 0
) -> str | None:
    """Return the planned safe-boundary outcome for one global boundary."""
    if arm not in ARM_BOUNDARIES:
        raise ValueError(f"unknown comparison arm: {arm}")
    if completed_intervals == STUDY_STOP:
        return "study_complete"
    if (
        completed_intervals > already_passed
        and completed_intervals in ARM_BOUNDARIES[arm]
    ):
        return "planned_recycle"
    return None


def _verify_outer(path: Path) -> None:
    if path.stat().st_size != EXPECTED_OUTER_BYTES:
        raise ValueError("seeded outer-plan byte count mismatch")
    if sha256_path(path) != EXPECTED_OUTER_SHA256:
        raise ValueError("seeded outer-plan SHA-256 mismatch")
    fixture = load_s2_fixture()
    load_verified_outer_plan_archive(
        path,
        inputs=fixture.inputs,
        policy=frozen_p0_policy(),
        expected_solve_config_sha256=fixture.solve_config_sha256,
        expected_source_fingerprint=HISTORICAL_SOURCE_FINGERPRINT,
        expected_scenario_hash=fixture.scenario_hash,
    )


def seed_fresh_arm(directory: Path) -> Path:
    """Create one fresh arm and copy its shared outer artifact exactly once."""
    if directory.exists():
        raise FileExistsError("fresh comparison arm requires an absent directory")
    trajectory = directory / "trajectory"
    trajectory.mkdir(parents=True)
    outer_path = trajectory / "outer-plan.json.gz"
    shutil.copyfile(TRACKED_OUTER_PATH, outer_path)
    _verify_outer(outer_path)
    return outer_path


def verify_checkpoint(path: Path) -> Mapping[str, object]:
    """Fully verify one comparison checkpoint and its immutable prefix."""
    fixture = load_s2_fixture()
    policy = frozen_p0_policy()
    outer_path = path.parent / "outer-plan.json.gz"
    _verify_outer(outer_path)
    outer = load_verified_outer_plan_archive(
        outer_path,
        inputs=fixture.inputs,
        policy=policy,
        expected_solve_config_sha256=fixture.solve_config_sha256,
        expected_source_fingerprint=HISTORICAL_SOURCE_FINGERPRINT,
        expected_scenario_hash=fixture.scenario_hash,
    )
    return load_verified_checkpoint(
        path,
        expected_source_fingerprint=HISTORICAL_SOURCE_FINGERPRINT,
        expected_scenario_hash=fixture.scenario_hash,
        expected_outer_plan_sha256=EXPECTED_OUTER_SHA256,
        expected_policy_hash=policy_sha256(policy),
        expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=residual_tolerances(policy),
        expected_inner_terminal_policy=policy.inner_terminal_policy,
        expected_horizon_steps=S2_HORIZON_STEPS,
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=result_dimensions(fixture.inputs),
        expected_delta_hours=fixture.inputs.delta,
        expected_outer_boundary_soc_mwh=outer_boundaries(outer),
    )


def _worker_result_path(directory: Path, invocation: int) -> Path:
    return directory / f"worker-result-{invocation:03d}.json"


def _supervision_path(directory: Path, invocation: int) -> Path:
    return directory / f"supervision-{invocation:03d}.json"


def _worker(
    directory: Path,
    *,
    arm: str,
    invocation: int,
    passed_boundary: int,
    expected_commit: str,
    expected_comparison_fingerprint: str,
) -> int:
    started = time.monotonic()
    try:
        start_context = execution_context()
    except Exception as exc:
        start_context = {"context_error": f"{type(exc).__name__}: {exc}"}
    provenance_matches = bool(
        start_context.get("git_commit") == expected_commit
        and start_context.get("git_clean") is True
        and start_context.get("comparison_source_fingerprint")
        == expected_comparison_fingerprint
        and start_context.get("model_source_fingerprint")
        == HISTORICAL_SOURCE_FINGERPRINT
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

    def observer(state: SafeBoundaryState) -> str | None:
        return observer_reason(
            arm,
            state.completed_intervals,
            already_passed=passed_boundary,
        )

    try:
        fixture = load_s2_fixture()
        trajectory = run_streaming_trajectory(
            directory / "trajectory",
            fixture.inputs,
            frozen_p0_policy(),
            frozen_p0_solve_config(),
            source_fingerprint=HISTORICAL_SOURCE_FINGERPRINT,
            scenario_hash=fixture.scenario_hash,
            resume=True,
            observer=observer,
        )
        exception = None
    except Exception as exc:
        trajectory = None
        exception = f"{type(exc).__name__}: {exc}"
    end_context = _safe_execution_context()
    context_matches = start_context == end_context
    status = None if trajectory is None else trajectory.status
    completed = 0 if trajectory is None else trajectory.completed_intervals
    reason = exception if trajectory is None else trajectory.termination_reason
    classification = (
        "worker_failure"
        if trajectory is None
        else str(reason)
        if status == "observer_terminated"
        and reason in {"planned_recycle", "study_complete"}
        else str(status)
    )
    atomic_immutable_json(
        _worker_result_path(directory, invocation),
        {
            "schema_version": SCHEMA_VERSION,
            "arm": arm,
            "invocation": invocation,
            "resume": True,
            "classification": classification,
            "trajectory_status": status,
            "completed_intervals": completed,
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


def _checkpoint_candidate(path: Path) -> tuple[int, str] | None:
    try:
        encoded = path.read_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        payload = json.loads(encoded)
        return int(payload["completed_intervals"]), digest
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def is_restart_endpoint_candidate(
    before: tuple[int, str] | None,
    candidate: tuple[int, str] | None,
) -> bool:
    """Return whether an observed checkpoint is the first post-restart boundary."""
    return bool(
        before is not None
        and candidate is not None
        and candidate[0] == before[0] + 1
        and candidate[1] != before[1]
    )


def _prior_wall_seconds(directory: Path) -> float:
    total = 0.0
    for pattern in ("supervision-*.json", "interrupted-invocation-*.json"):
        for path in directory.glob(pattern):
            payload = _mapping(json.loads(path.read_text()), "wall-time record")
            total += float(cast(float, payload["wall_time_seconds"]))
    return total


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        raise ValueError(f"cannot verify comparison process PID {pid}") from exc
    return True


def _archive_stale_active_invocation(directory: Path) -> Mapping[str, object] | None:
    active_path = directory / "active-invocation.json"
    if not active_path.is_file():
        return None
    active = _mapping(json.loads(active_path.read_text()), "active invocation")
    try:
        invocation = int(cast(int, active["invocation"]))
        supervisor_pid = int(cast(int, active["supervisor_pid"]))
        worker_pid = (
            None
            if active.get("worker_pid") is None
            else int(cast(int, active["worker_pid"]))
        )
        started_epoch = float(cast(float, active["started_epoch_seconds"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("active invocation record is malformed") from exc
    pids = [supervisor_pid] + ([] if worker_pid is None else [worker_pid])
    if any(_pid_is_alive(pid) for pid in pids):
        raise ValueError("another comparison supervisor or worker is still active")
    supervision_path = _supervision_path(directory, invocation)
    if supervision_path.is_file():
        _mapping(json.loads(supervision_path.read_text()), "supervision record")
        active_path.unlink()
        return None
    reviewed_epoch = time.time()
    record: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "classification": "reviewed_interruption",
        "invocation": invocation,
        "supervisor_pid": supervisor_pid,
        "worker_pid": worker_pid,
        "started_epoch_seconds": started_epoch,
        "reviewed_epoch_seconds": reviewed_epoch,
        "wall_time_seconds": max(0.0, reviewed_epoch - started_epoch),
    }
    atomic_immutable_json(
        directory / f"interrupted-invocation-{invocation:03d}.json", record
    )
    active_path.unlink()
    return record


def _next_invocation(directory: Path) -> int:
    indices: list[int] = []
    for pattern in (
        "run-context-*.json",
        "worker-result-*.json",
        "supervision-*.json",
        "interrupted-invocation-*.json",
    ):
        for path in directory.glob(pattern):
            try:
                indices.append(int(path.stem.rsplit("-", maxsplit=1)[1]))
            except (IndexError, ValueError):
                continue
    return 0 if not indices else max(indices) + 1


def _total_retained_wall_seconds(root: Path) -> float:
    return sum(
        _prior_wall_seconds(root / arm) for arm in ARM_ORDER if (root / arm).is_dir()
    )


def _validate_reviewed_resume(directory: Path) -> Mapping[str, object]:
    checkpoint_path = directory / "trajectory/checkpoint.json"
    if not checkpoint_path.is_file():
        raise ValueError("reviewed resume requires an existing checkpoint")
    verify_checkpoint(checkpoint_path)
    interrupted = _archive_stale_active_invocation(directory)
    invocation = _next_invocation(directory)
    if invocation == 0:
        raise ValueError("reviewed resume requires a prior supervision record")
    previous_context_path = directory / f"run-context-{invocation - 1:03d}.json"
    if not previous_context_path.is_file():
        raise ValueError("reviewed resume lacks its prior immutable run context")
    previous = _mapping(json.loads(previous_context_path.read_text()), "run context")
    current = execution_context()
    if current.get("git_clean") is not True:
        raise ValueError("reviewed resume requires a clean committed worktree")
    identity_fields = (
        "git_commit",
        "model_source_fingerprint",
        "comparison_source_fingerprint",
        "scenario_hash",
        "policy_sha256",
        "solve_config_sha256",
        "outer_plan_sha256",
    )
    mismatches = [
        name for name in identity_fields if previous.get(name) != current.get(name)
    ]
    if mismatches:
        raise ValueError(f"reviewed resume provenance mismatch: {mismatches}")
    latest_path = directory / "latest-supervision.json"
    if latest_path.is_file():
        latest = _mapping(json.loads(latest_path.read_text()), "latest supervision")
        if latest.get("classification") == "study_complete":
            raise ValueError("a completed arm cannot be resumed")
    elif interrupted is None:
        raise ValueError("reviewed resume lacks a durable interruption outcome")
    return current


def supervise_invocation(
    directory: Path,
    *,
    arm: str,
    total_prior_wall_seconds: float,
) -> Mapping[str, object]:
    """Launch and externally supervise one immutable comparison invocation."""
    if arm not in ARM_BOUNDARIES or not directory.is_dir():
        raise ValueError("comparison invocation requires a known seeded arm")
    outer_path = directory / "trajectory/outer-plan.json.gz"
    _verify_outer(outer_path)
    checkpoint_path = directory / "trajectory/checkpoint.json"
    before = _checkpoint_candidate(checkpoint_path)
    if checkpoint_path.exists() and before is None:
        raise ValueError("existing checkpoint is unreadable or malformed")
    passed_boundary = 0 if before is None else before[0]
    if before is not None:
        verify_checkpoint(checkpoint_path)
    invocation = _next_invocation(directory)
    arm_prior_wall = _prior_wall_seconds(directory)
    if arm_prior_wall >= ARM_WALL_LIMIT_SECONDS:
        raise ValueError("comparison arm wall authorization is exhausted")
    if total_prior_wall_seconds >= TOTAL_WALL_LIMIT_SECONDS:
        raise ValueError("comparison total wall authorization is exhausted")
    context = execution_context()
    if context["git_clean"] is not True:
        raise ValueError("comparison execution requires a clean committed worktree")
    atomic_immutable_json(directory / f"run-context-{invocation:03d}.json", context)
    command = [
        sys.executable,
        "-m",
        "experiments.case118_annual_hierarchy.run_recycle_comparison",
        "--worker",
        "--output-directory",
        str(directory),
        "--arm",
        arm,
        "--invocation",
        str(invocation),
        "--passed-boundary",
        str(passed_boundary),
        "--expected-commit",
        str(context["git_commit"]),
        "--expected-comparison-fingerprint",
        str(context["comparison_source_fingerprint"]),
    ]
    log_path = directory / f"worker-{invocation:03d}.log"
    active_path = directory / "active-invocation.json"
    started = time.monotonic()
    last_progress = started
    first_rss: float | None = None
    final_rss: float | None = None
    peak_rss = 0.0
    resource_reason: str | None = None
    artifact_error: str | None = None
    restart_endpoint: float | None = None
    restart_checkpoint_sha256: str | None = None
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
                    resource_reason = "rss_limit"
            candidate = _checkpoint_candidate(checkpoint_path)
            if checkpoint_path.exists() and candidate is None:
                artifact_error = "existing checkpoint is unreadable or malformed"
                _terminate(process)
                break
            if candidate is not None and candidate[0] > last_completed:
                observed = now
                try:
                    verify_checkpoint(checkpoint_path)
                except Exception as exc:
                    artifact_error = f"{type(exc).__name__}: {exc}"
                    _terminate(process)
                    break
                if _checkpoint_candidate(checkpoint_path) == candidate:
                    last_completed = candidate[0]
                    last_progress = observed
                    if restart_endpoint is None and is_restart_endpoint_candidate(
                        before, candidate
                    ):
                        restart_endpoint = observed
                        restart_checkpoint_sha256 = candidate[1]
            elapsed = now - started
            if (
                resource_reason is None
                and arm_prior_wall + elapsed > ARM_WALL_LIMIT_SECONDS
            ):
                resource_reason = "total_wall_limit"
            if (
                resource_reason is None
                and total_prior_wall_seconds + elapsed > TOTAL_WALL_LIMIT_SECONDS
            ):
                resource_reason = "total_wall_limit"
            elif (
                resource_reason is None
                and now - last_progress > CHECKPOINT_STALL_LIMIT_SECONDS
            ):
                resource_reason = "checkpoint_stall_limit"
            if resource_reason is not None:
                _terminate(process)
                break
            time.sleep(POLL_SECONDS)
        returncode = process.wait()
    ended = time.monotonic()
    worker_path = _worker_result_path(directory, invocation)
    worker = (
        _mapping(json.loads(worker_path.read_text()), "worker result")
        if worker_path.is_file()
        else None
    )
    end_context = _safe_execution_context()
    context_matches = context == end_context
    after = _checkpoint_candidate(checkpoint_path)
    if checkpoint_path.exists() and after is None and artifact_error is None:
        artifact_error = "existing checkpoint is unreadable or malformed"
    if after is not None and artifact_error is None:
        try:
            verify_checkpoint(checkpoint_path)
        except Exception as exc:
            artifact_error = f"{type(exc).__name__}: {exc}"
    if (
        artifact_error is None
        and resource_reason is None
        and context_matches
        and worker is not None
        and worker.get("context_matches") is True
        and returncode == 0
    ):
        claim = worker.get("classification")
        worker_completed = worker.get("completed_intervals")
        if claim == "planned_recycle" and not (
            after is not None
            and after[0] == worker_completed
            and after[0] > passed_boundary
            and after[0] in ARM_BOUNDARIES[arm]
            and after[0] < STUDY_STOP
        ):
            artifact_error = "planned recycle lacks its verified scheduled checkpoint"
        elif claim == "study_complete" and not (
            after is not None
            and after[0] == STUDY_STOP
            and worker_completed == STUDY_STOP
        ):
            artifact_error = (
                "study completion lacks its verified boundary-64 checkpoint"
            )
    if artifact_error is not None:
        classification = "artifact_failure"
    elif resource_reason is not None:
        classification = resource_reason
    elif (
        not context_matches
        or worker is None
        or worker.get("context_matches") is not True
    ):
        classification = (
            "provenance_mismatch" if not context_matches else "worker_failure"
        )
    elif returncode != 0:
        classification = "worker_failure"
    else:
        classification = str(worker.get("classification"))
    record: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "arm": arm,
        "invocation": invocation,
        "resume": True,
        "classification": classification,
        "artifact_error": artifact_error,
        "returncode": returncode,
        "completed_before": passed_boundary,
        "completed_after": None if after is None else after[0],
        "checkpoint_sha256_before": None if before is None else before[1],
        "checkpoint_sha256_after": None if after is None else after[1],
        "seeded_outer_sha256": EXPECTED_OUTER_SHA256,
        "first_sampled_rss_mib": first_rss,
        "peak_sampled_rss_mib": peak_rss,
        "final_sampled_rss_mib": final_rss,
        "restart_to_first_checkpoint_seconds": (
            None if restart_endpoint is None else restart_endpoint - started
        ),
        "restart_checkpoint_sha256": restart_checkpoint_sha256,
        "wall_time_seconds": ended - started,
        "prior_arm_wall_seconds": arm_prior_wall,
        "prior_total_wall_seconds": total_prior_wall_seconds,
        "start_context": context,
        "end_context": end_context,
        "context_matches": context_matches,
        "worker_result": worker,
        "worker_log": log_path.name,
        "worker_log_sha256": sha256_path(log_path),
        "resource_policy": {
            "rss_limit_mib": RSS_LIMIT_MIB,
            "checkpoint_stall_limit_seconds": CHECKPOINT_STALL_LIMIT_SECONDS,
            "arm_wall_limit_seconds": ARM_WALL_LIMIT_SECONDS,
            "total_wall_limit_seconds": TOTAL_WALL_LIMIT_SECONDS,
            "poll_seconds": POLL_SECONDS,
        },
    }
    atomic_immutable_json(_supervision_path(directory, invocation), record)
    atomic_json(directory / "latest-supervision.json", record)
    active_path.unlink(missing_ok=True)
    return record


def run_arm(
    root: Path,
    arm: str,
    *,
    total_prior_wall_seconds: float,
) -> tuple[Mapping[str, object], ...]:
    """Run one fresh arm through planned recycles or its first abnormal stop."""
    directory = root / arm
    seed_fresh_arm(directory)
    records: list[Mapping[str, object]] = []
    while True:
        record = supervise_invocation(
            directory,
            arm=arm,
            total_prior_wall_seconds=(
                total_prior_wall_seconds
                + sum(
                    [
                        float(cast(float, item["wall_time_seconds"]))
                        for item in records
                    ],
                    start=0.0,
                )
            ),
        )
        records.append(record)
        classification = record["classification"]
        if classification == "planned_recycle":
            continue
        break
    return tuple(records)


def resume_arm(root: Path, arm: str) -> tuple[Mapping[str, object], ...]:
    """Continue one existing arm after explicit reviewed authorization."""
    if arm not in ARM_BOUNDARIES:
        raise ValueError(f"unknown comparison arm: {arm}")
    directory = root / arm
    if not directory.is_dir():
        raise ValueError("reviewed resume requires an existing arm directory")
    _validate_reviewed_resume(directory)
    initial_total_wall = _total_retained_wall_seconds(root)
    records: list[Mapping[str, object]] = []
    while True:
        record = supervise_invocation(
            directory,
            arm=arm,
            total_prior_wall_seconds=(
                initial_total_wall
                + sum(
                    [
                        float(cast(float, item["wall_time_seconds"]))
                        for item in records
                    ],
                    start=0.0,
                )
            ),
        )
        records.append(record)
        if record["classification"] != "planned_recycle":
            break
    return tuple(records)


def run_comparison(root: Path = DEFAULT_OUTPUT_DIRECTORY) -> Mapping[str, object]:
    """Run all fresh arms serially in the frozen order."""
    if root.exists():
        raise FileExistsError("fresh comparison requires an absent output root")
    root.mkdir(parents=True)
    all_records: dict[str, Sequence[Mapping[str, object]]] = {}
    total_wall = 0.0
    for arm in ARM_ORDER:
        records = run_arm(root, arm, total_prior_wall_seconds=total_wall)
        all_records[arm] = records
        total_wall += sum(
            [
                float(cast(float, record["wall_time_seconds"]))
                for record in records
            ],
            start=0.0,
        )
        if records[-1]["classification"] != "study_complete":
            break
    complete = bool(
        tuple(all_records) == ARM_ORDER
        and all(
            records[-1]["classification"] == "study_complete"
            for records in all_records.values()
        )
    )
    provisional = {
        "schema_version": SCHEMA_VERSION,
        "complete": complete,
        "arm_order": list(ARM_ORDER),
        "records": all_records,
        "total_wall_time_seconds": total_wall,
    }
    atomic_json(root / "provisional-comparison-summary.json", provisional)
    return provisional


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY
    )
    parser.add_argument("--arm", choices=ARM_ORDER)
    parser.add_argument("--resume-arm", choices=ARM_ORDER)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--invocation", type=int, default=0)
    parser.add_argument("--passed-boundary", type=int, default=0)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-comparison-fingerprint")
    args = parser.parse_args()
    if args.worker and args.resume_arm is not None:
        parser.error("--worker and --resume-arm are mutually exclusive")
    if args.worker:
        if (
            args.arm is None
            or args.expected_commit is None
            or args.expected_comparison_fingerprint is None
        ):
            parser.error("worker requires arm and expected provenance")
        return _worker(
            args.output_directory,
            arm=args.arm,
            invocation=args.invocation,
            passed_boundary=args.passed_boundary,
            expected_commit=args.expected_commit,
            expected_comparison_fingerprint=args.expected_comparison_fingerprint,
        )
    if args.resume_arm is not None:
        records = resume_arm(args.output_directory, args.resume_arm)
        print(json.dumps(records, indent=2, sort_keys=True))
        return 0 if records[-1]["classification"] == "study_complete" else 1
    result = run_comparison(args.output_directory)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["complete"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
