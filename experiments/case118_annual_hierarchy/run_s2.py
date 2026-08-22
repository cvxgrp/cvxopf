"""Run the resource-supervised one-week Case118 hierarchy."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
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
)
from experiments.case118_annual_hierarchy.run_s0 import (
    ROOT,
    _git_output,
    _software_versions,
)
from experiments.case118_annual_hierarchy.s2_fixture import (
    S2_HORIZON_STEPS,
    load_s2_fixture,
)
from experiments.case118_annual_hierarchy.s2_analysis import analyze_s2
from experiments.case118_annual_hierarchy.streaming_driver import (
    SafeBoundaryState,
    run_streaming_trajectory,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
)


DEFAULT_OUTPUT_DIRECTORY = Path(
    "experiments/case118_annual_hierarchy/results/s2_week_rated"
)
RSS_LIMIT_MIB = 16.0 * 1024.0
CHECKPOINT_STALL_LIMIT_SECONDS = 60.0 * 60.0
TOTAL_WALL_LIMIT_SECONDS = 48.0 * 60.0 * 60.0
POLL_SECONDS = 1.0
SCHEMA_VERSION = 1
SOURCE_PATHS = (
    "experiments/case118_annual_hierarchy/S2_PROTOCOL.md",
    "experiments/case118_annual_hierarchy/audit.py",
    "experiments/case118_annual_hierarchy/p0_fixture.py",
    "experiments/case118_annual_hierarchy/pglib_case.py",
    "experiments/case118_annual_hierarchy/run_s0.py",
    "experiments/case118_annual_hierarchy/run_s2.py",
    "experiments/case118_annual_hierarchy/s2_analysis.py",
    "experiments/case118_annual_hierarchy/s2_fixture.py",
    "experiments/case118_annual_hierarchy/scenario.py",
    "experiments/case118_annual_hierarchy/streaming_archive.py",
    "experiments/case118_annual_hierarchy/streaming_driver.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
    "experiments/case118_annual_hierarchy/streaming_schema.py",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def s2_source_paths() -> tuple[Path, ...]:
    """Return the complete deterministic source registry for S2."""
    paths = [ROOT / name for name in SOURCE_PATHS]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    return tuple(sorted(set(paths)))


def s2_source_fingerprint() -> str:
    """Bind all experiment and cvxopf sources used by the S2 worker."""
    digest = hashlib.sha256()
    for path in s2_source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def execution_context() -> Mapping[str, object]:
    """Return the clean-source and environment identity for one invocation."""
    fixture = load_s2_fixture()
    return {
        "git_commit": _git_output("rev-parse", "HEAD"),
        "git_clean": _git_output("status", "--porcelain") == "",
        "source_fingerprint": s2_source_fingerprint(),
        "scenario_hash": fixture.scenario_hash,
        "component_hashes": dict(fixture.hashes),
        "policy_sha256": fixture.policy_sha256,
        "solve_config_sha256": fixture.solve_config_sha256,
        "software_versions": _software_versions(),
        "platform": platform.platform(),
    }


def _worker_result_path(directory: Path, invocation: int) -> Path:
    return directory / f"worker-result-{invocation:03d}.json"


def _supervision_path(directory: Path, invocation: int) -> Path:
    return directory / f"supervision-{invocation:03d}.json"


def _invocation(directory: Path) -> int:
    indices: list[int] = []
    for pattern in (
        "run-context-*.json",
        "worker-result-*.json",
        "supervision-*.json",
    ):
        for path in directory.glob(pattern):
            try:
                indices.append(int(path.stem.rsplit("-", maxsplit=1)[1]))
            except (IndexError, ValueError):
                continue
    return 0 if not indices else max(indices) + 1


def _prior_supervised_wall_seconds(directory: Path) -> float:
    total = 0.0
    for path in directory.glob("supervision-*.json"):
        try:
            value = json.loads(path.read_text())["wall_time_seconds"]
            total += float(value)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Malformed prior supervision record: {path}") from exc
    return total


def _validate_resume_authorization(directory: Path) -> None:
    latest = directory / "latest-supervision.json"
    if not latest.is_file():
        return
    try:
        classification = json.loads(latest.read_text())["classification"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed latest S2 supervision record") from exc
    if classification != "worker_failure":
        raise ValueError(
            "S2 resume is authorized only after an unexplained worker failure"
        )


def _stale_invocation_wall_seconds(directory: Path) -> float:
    active = directory / "active-invocation.json"
    if not active.is_file():
        return 0.0
    try:
        payload = json.loads(active.read_text())
        pids = [int(payload["supervisor_pid"])]
        if payload.get("worker_pid") is not None:
            pids.append(int(payload["worker_pid"]))
        started_epoch = float(payload["started_epoch_seconds"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("Malformed active S2 invocation record") from exc
    for pid in pids:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            raise ValueError("Cannot verify active S2 process") from exc
        raise ValueError("Another S2 supervisor or worker is still active")
    return max(0.0, time.time() - started_epoch)


def _worker(
    directory: Path,
    *,
    invocation: int,
    resume: bool,
    expected_commit: str,
    expected_source_fingerprint: str,
) -> int:
    started = time.monotonic()
    start_context = execution_context()
    if (
        start_context["git_commit"] != expected_commit
        or start_context["source_fingerprint"] != expected_source_fingerprint
        or start_context["git_clean"] is not True
    ):
        atomic_immutable_json(
            _worker_result_path(directory, invocation),
            {
                "schema_version": SCHEMA_VERSION,
                "classification": "provenance_mismatch",
                "start_context": start_context,
                "eligible_for_advancement": False,
            },
        )
        return 2
    fixture = load_s2_fixture()
    policy = frozen_p0_policy()
    solve_config = frozen_p0_solve_config()

    def observer(state: SafeBoundaryState) -> str | None:
        current_rss = state.resource_samples[-1].rss_bytes / (1024.0**2)
        if current_rss > RSS_LIMIT_MIB:
            return "S2 safe-boundary RSS limit"
        if time.monotonic() - started > TOTAL_WALL_LIMIT_SECONDS:
            return "S2 safe-boundary wall-time limit"
        return None

    try:
        trajectory = run_streaming_trajectory(
            directory / "trajectory",
            fixture.inputs,
            policy,
            solve_config,
            source_fingerprint=expected_source_fingerprint,
            scenario_hash=fixture.scenario_hash,
            resume=resume,
            observer=observer,
        )
        exception = None
    except Exception as exc:
        trajectory = None
        exception = f"{type(exc).__name__}: {exc}"
    end_context = execution_context()
    provenance_matches = start_context == end_context
    status = None if trajectory is None else trajectory.status
    completed = 0 if trajectory is None else trajectory.completed_intervals
    classification = (
        "worker_failure"
        if trajectory is None
        else "resource_limit"
        if status == "observer_terminated"
        and trajectory.termination_reason
        in {
            "S2 safe-boundary RSS limit",
            "S2 safe-boundary wall-time limit",
        }
        else "completed"
        if status == "complete" and completed == S2_HORIZON_STEPS
        else "scientific_termination"
    )
    eligible = bool(classification == "completed" and provenance_matches)
    atomic_immutable_json(
        _worker_result_path(directory, invocation),
        {
            "schema_version": SCHEMA_VERSION,
            "classification": classification,
            "trajectory_status": status,
            "completed_intervals": completed,
            "termination_reason": (
                exception
                if trajectory is None
                else trajectory.termination_reason
            ),
            "start_context": start_context,
            "end_context": end_context,
            "provenance_matches": provenance_matches,
            "eligible_for_advancement": eligible,
            "wall_time_seconds": time.monotonic() - started,
        },
    )
    return 0 if trajectory is not None else 1


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


def _checkpoint_progress(path: Path) -> int | None:
    try:
        payload = json.loads(path.read_text())
        return int(payload["completed_intervals"])
    except (FileNotFoundError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _classification(
    *,
    resource_reason: str | None,
    returncode: int,
    worker: Mapping[str, object] | None,
    context_matches: bool,
) -> tuple[str, bool]:
    if resource_reason is not None:
        return "resource_limit", False
    if not context_matches or (
        worker is not None
        and (
            worker.get("classification") == "provenance_mismatch"
            or worker.get("provenance_matches") is False
        )
    ):
        return "provenance_mismatch", False
    if returncode != 0 or worker is None:
        return "worker_failure", False
    if worker.get("classification") == "resource_limit":
        return "resource_limit", False
    if (
        worker.get("classification") == "completed"
        and worker.get("trajectory_status") == "complete"
        and worker.get("completed_intervals") == S2_HORIZON_STEPS
        and worker.get("eligible_for_advancement") is True
    ):
        return "completed", True
    return "scientific_termination", False


def supervise(directory: Path, *, resume: bool = False) -> Mapping[str, object]:
    """Launch and externally supervise one immutable S2 worker invocation."""
    if resume:
        if not directory.is_dir():
            raise ValueError("S2 resume requires an existing output directory")
        if not (directory / "trajectory/checkpoint.json").is_file():
            raise ValueError("S2 resume requires a verified checkpoint candidate")
        _validate_resume_authorization(directory)
    else:
        if directory.exists():
            raise FileExistsError("S2 fresh execution requires a fresh directory")
        directory.mkdir(parents=True)
    context = execution_context()
    if context["git_clean"] is not True:
        raise ValueError("S2 execution requires a clean committed worktree")
    invocation = _invocation(directory)
    prior_wall_seconds = _prior_supervised_wall_seconds(
        directory
    ) + _stale_invocation_wall_seconds(directory)
    if prior_wall_seconds >= TOTAL_WALL_LIMIT_SECONDS:
        raise ValueError("S2 cumulative wall-time authorization is exhausted")
    atomic_immutable_json(
        directory / f"run-context-{invocation:03d}.json", context
    )
    active_path = directory / "active-invocation.json"
    atomic_json(
        active_path,
        {
            "invocation": invocation,
            "supervisor_pid": os.getpid(),
            "started_epoch_seconds": time.time(),
        },
    )
    command = [
        sys.executable,
        "-m",
        "experiments.case118_annual_hierarchy.run_s2",
        "--worker",
        "--output-directory",
        str(directory),
        "--invocation",
        str(invocation),
        "--expected-commit",
        str(context["git_commit"]),
        "--expected-source-fingerprint",
        str(context["source_fingerprint"]),
    ]
    if resume:
        command.append("--resume")
    log_path = directory / f"worker-{invocation:03d}.log"
    started = time.monotonic()
    last_progress_time = started
    last_completed = _checkpoint_progress(
        directory / "trajectory/checkpoint.json"
    )
    peak_rss = 0.0
    resource_reason: str | None = None
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
        while process.poll() is None:
            now = time.monotonic()
            rss = _child_rss_mib(process.pid)
            if rss is not None:
                peak_rss = max(peak_rss, rss)
                if rss > RSS_LIMIT_MIB:
                    resource_reason = "rss_limit"
            completed = _checkpoint_progress(
                directory / "trajectory/checkpoint.json"
            )
            if completed is not None and completed != last_completed:
                last_completed = completed
                last_progress_time = now
            if (
                prior_wall_seconds + now - started
                > TOTAL_WALL_LIMIT_SECONDS
            ):
                resource_reason = "total_wall_limit"
            elif now - last_progress_time > CHECKPOINT_STALL_LIMIT_SECONDS:
                resource_reason = "checkpoint_stall_limit"
            if resource_reason is not None:
                _terminate(process)
                break
            time.sleep(POLL_SECONDS)
        returncode = process.wait()
    worker_path = _worker_result_path(directory, invocation)
    worker = (
        cast(Mapping[str, object], json.loads(worker_path.read_text()))
        if worker_path.is_file()
        else None
    )
    end_context = execution_context()
    context_matches = context == end_context
    classification, eligible = _classification(
        resource_reason=resource_reason,
        returncode=returncode,
        worker=worker,
        context_matches=context_matches,
    )
    analysis: Mapping[str, object] | None = None
    analysis_error: str | None = None
    checkpoint_path = directory / "trajectory/checkpoint.json"
    if worker is not None and checkpoint_path.is_file():
        try:
            analysis = analyze_s2(
                directory,
                source_fingerprint=str(context["source_fingerprint"]),
                scenario_hash=str(context["scenario_hash"]),
                trajectory_status=str(worker.get("trajectory_status")),
            )
            atomic_immutable_json(
                directory / f"analysis-{invocation:03d}.json", analysis
            )
        except Exception as exc:
            analysis_error = f"{type(exc).__name__}: {exc}"
    if classification == "completed" and (
        analysis is None or analysis.get("accepted_for_s3") is not True
    ):
        classification = "artifact_validation_failure"
        eligible = False
    record: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "invocation": invocation,
        "resume": resume,
        "classification": classification,
        "eligible_for_advancement": eligible,
        "resource_reason": resource_reason,
        "returncode": returncode,
        "last_verified_completed_intervals": last_completed,
        "peak_sampled_rss_mib": peak_rss,
        "wall_time_seconds": time.monotonic() - started,
        "prior_supervised_wall_seconds": prior_wall_seconds,
        "cumulative_supervised_wall_seconds": (
            prior_wall_seconds + time.monotonic() - started
        ),
        "start_context": context,
        "end_context": end_context,
        "context_matches": context_matches,
        "worker_result": worker,
        "analysis": analysis,
        "analysis_error": analysis_error,
        "worker_log": log_path.name,
        "resource_policy": {
            "rss_limit_mib": RSS_LIMIT_MIB,
            "checkpoint_stall_limit_seconds": CHECKPOINT_STALL_LIMIT_SECONDS,
            "total_wall_limit_seconds": TOTAL_WALL_LIMIT_SECONDS,
            "poll_seconds": POLL_SECONDS,
        },
    }
    atomic_immutable_json(_supervision_path(directory, invocation), record)
    atomic_json(directory / "latest-supervision.json", record)
    active_path.unlink(missing_ok=True)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory", type=Path, default=DEFAULT_OUTPUT_DIRECTORY
    )
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--invocation", type=int, default=0)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-source-fingerprint")
    arguments = parser.parse_args()
    if arguments.worker:
        if (
            arguments.expected_commit is None
            or arguments.expected_source_fingerprint is None
        ):
            parser.error("worker requires expected provenance")
        return _worker(
            arguments.output_directory,
            invocation=arguments.invocation,
            resume=arguments.resume,
            expected_commit=arguments.expected_commit,
            expected_source_fingerprint=arguments.expected_source_fingerprint,
        )
    record = supervise(arguments.output_directory, resume=arguments.resume)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if record["eligible_for_advancement"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CHECKPOINT_STALL_LIMIT_SECONDS",
    "DEFAULT_OUTPUT_DIRECTORY",
    "POLL_SECONDS",
    "RSS_LIMIT_MIB",
    "TOTAL_WALL_LIMIT_SECONDS",
    "execution_context",
    "s2_source_fingerprint",
    "s2_source_paths",
    "supervise",
]
