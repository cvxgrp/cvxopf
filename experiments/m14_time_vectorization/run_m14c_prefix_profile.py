"""Run the non-promotional M14c stepwise/CPP prefix profile."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import gc
import hashlib
import json
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time
from typing import Callable, Mapping, cast
from unittest.mock import patch

import numpy as np

from cvxopf import extract_results
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy import streaming_runner
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s4 import (
    _child_rss_mib,
    _problem_dimensions,
    _software_versions,
    _terminate,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    load_verified_outer_plan_archive,
    write_verified_outer_plan_archive,
)
from experiments.case118_annual_hierarchy.streaming_driver import process_rss_bytes
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)
from experiments.m14_time_vectorization.m14c_prefix_fixture import (
    M14C_INTEGRATION_COMMIT,
    PREFIX_EXECUTION_LIMITS,
    PREFIX_LADDER_HORIZONS,
    PREFIX_LADDER_OUTPUT_DIRECTORY,
    PrefixExecutionLimits,
    load_prefix_fixture,
)


SCHEMA_VERSION = 1
PROFILE_OUTPUT_DIRECTORY = Path(
    "experiments/m14_time_vectorization/results/m14c_case118_prefix_profile"
)
REFERENCE_LADDER_RESULT_SHA256 = (
    "1c66941363d59cc29374eee2201eb2e2cf0a5393b4bfb76edfe3b99aef7cbca6"
)
REFERENCE_EXECUTION_COMMIT = "2f0f95288e5e50ff32c94c7667ea44121f719fa4"
EXPECTED_WORKER_PHASES = (
    "worker_start",
    "before_construction",
    "after_construction",
    "before_solve",
    "after_solve",
    "after_outer",
    "after_archive",
    "after_release",
)
PROFILE_SOURCE_FILES = (
    "experiments/m14_time_vectorization/M14C_INTEGRATION.json",
    "experiments/m14_time_vectorization/M14C_PROTOCOL.md",
    "experiments/m14_time_vectorization/M14C_PROFILING_PROTOCOL.md",
    "experiments/m14_time_vectorization/m14c_prefix_fixture.py",
    "experiments/m14_time_vectorization/run_m14c_prefix_profile.py",
    "experiments/case118_annual_hierarchy/audit.py",
    "experiments/case118_annual_hierarchy/p0_fixture.py",
    "experiments/case118_annual_hierarchy/pglib_case.py",
    "experiments/case118_annual_hierarchy/run_s0.py",
    "experiments/case118_annual_hierarchy/run_s4.py",
    "experiments/case118_annual_hierarchy/scenario.py",
    "experiments/case118_annual_hierarchy/s4_equivalence.py",
    "experiments/case118_annual_hierarchy/s4_fixture.py",
    "experiments/case118_annual_hierarchy/streaming_archive.py",
    "experiments/case118_annual_hierarchy/streaming_driver.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
    "experiments/case118_annual_hierarchy/streaming_schema.py",
)
SHARED_PRODUCTION_FILES = (
    "experiments/case118_annual_hierarchy/audit.py",
    "experiments/case118_annual_hierarchy/p0_fixture.py",
    "experiments/case118_annual_hierarchy/pglib_case.py",
    "experiments/case118_annual_hierarchy/scenario.py",
    "experiments/case118_annual_hierarchy/s4_fixture.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
)


class SupervisorInterrupted(BaseException):
    """Catchable SIGTERM marker used to retain profiling evidence."""


def _sigterm_handler(signum: int, frame: object) -> None:
    del signum, frame
    raise SupervisorInterrupted("SIGTERM")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def profile_source_paths() -> tuple[Path, ...]:
    paths = [ROOT / name for name in PROFILE_SOURCE_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    result = tuple(sorted(set(paths)))
    if any(not path.is_file() for path in result):
        raise FileNotFoundError("M14c profiling source registry is incomplete")
    return result


def profile_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in profile_source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def shared_production_fingerprint(commit: str | None = None) -> str:
    """Hash the common OPF/model surface independently of either harness."""
    paths = [ROOT / name for name in SHARED_PRODUCTION_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    digest = hashlib.sha256()
    for path in sorted(set(paths)):
        relative = path.relative_to(ROOT).as_posix()
        if commit is None:
            payload = path.read_bytes()
        else:
            payload = subprocess.run(
                ["git", "show", f"{commit}:{relative}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return digest.hexdigest()


def _limit_payload(limits: PrefixExecutionLimits) -> Mapping[str, float]:
    return {
        "rss_limit_mib": limits.child_rss_mib,
        "worker_wall_seconds": limits.worker_wall_seconds,
        "supervisor_wall_seconds": limits.supervisor_wall_seconds,
        "poll_seconds": limits.poll_seconds,
    }


def validate_reference_ladder(
    root: Path = PREFIX_LADDER_OUTPUT_DIRECTORY,
) -> Mapping[int, Mapping[str, object]]:
    """Verify the complete historical vectorized ladder before profiling."""
    root = (ROOT / root).resolve() if not root.is_absolute() else root.resolve()
    result_path = root / "ladder-result.json"
    progress_path = root / "ladder-progress.json"
    context_path = root / "execution-context.json"
    equivalence_path = root / "outer-equivalence.json"
    required = (result_path, progress_path, context_path, equivalence_path)
    if any(not path.is_file() for path in required):
        raise ValueError("accepted vectorized reference root is incomplete")
    if sha256_path(result_path) != REFERENCE_LADDER_RESULT_SHA256:
        raise ValueError("accepted vectorized reference root hash mismatch")
    result = _mapping(json.loads(result_path.read_text()), "reference result")
    progress = _mapping(json.loads(progress_path.read_text()), "reference progress")
    context = _mapping(json.loads(context_path.read_text()), "reference context")
    equivalence = _mapping(
        json.loads(equivalence_path.read_text()), "reference equivalence"
    )
    fixture = load_prefix_fixture(PREFIX_LADDER_HORIZONS[0]).annual
    if (
        result.get("classification") != "accepted"
        or result.get("execution_complete") is not True
        or result.get("accepted_horizons") != list(PREFIX_LADDER_HORIZONS)
        or result.get("attempted_horizons") != list(PREFIX_LADDER_HORIZONS)
        or result.get("annual_execution_authorized") is not False
        or result.get("ladder_progress_sha256") != sha256_path(progress_path)
        or result.get("execution_context_sha256") != sha256_path(context_path)
        or result.get("outer_equivalence_sha256") != sha256_path(equivalence_path)
        or equivalence.get("equivalent") is not True
        or context.get("git_commit") != REFERENCE_EXECUTION_COMMIT
        or context.get("git_clean") is not True
        or context.get("m14c_integration_commit") != M14C_INTEGRATION_COMMIT
        or context.get("m14c_integration_sha256") != fixture.m14c_integration_sha256
        or context.get("m14c_integration_checkpoint")
        != fixture.m14c_integration_checkpoint
        or context.get("m14c_source_commit") != fixture.m14c_source_commit
        or context.get("big_experiment_parent_commit")
        != fixture.big_experiment_parent_commit
        or context.get("m14c_merge_base_commit") != fixture.m14c_merge_base_commit
        or context.get("policy_sha256") != fixture.policy_sha256
        or context.get("solve_config_sha256") != fixture.solve_config_sha256
        or context.get("temporal_assembly") != "vectorized"
        or context.get("canonicalization_backend") != "SCIPY"
        or context.get("prefix_ladder_executed") is not False
        or context.get("annual_execution_authorized") is not False
    ):
        raise ValueError("accepted vectorized reference authority is inconsistent")
    for key in (
        "attempted_horizons",
        "accepted_horizons",
        "stopped_horizon",
        "interrupted_horizon",
        "records",
        "execution_context_sha256",
        "outer_equivalence_sha256",
        "annual_execution_authorized",
    ):
        if progress.get(key) != result.get(key):
            raise ValueError("accepted vectorized reference progress differs from root")
    if progress.get("classification") != "accepted":
        raise ValueError("accepted vectorized reference progress is not finalized")
    if (
        _git("merge-base", M14C_INTEGRATION_COMMIT, REFERENCE_EXECUTION_COMMIT)
        != M14C_INTEGRATION_COMMIT
    ):
        raise ValueError("vectorized reference lacks reviewed integration ancestry")
    records = cast(list[object], result.get("records"))
    if len(records) != len(PREFIX_LADDER_HORIZONS):
        raise ValueError("accepted vectorized reference registry is incomplete")
    registry: dict[int, Mapping[str, object]] = {}
    for horizon, item in zip(PREFIX_LADDER_HORIZONS, records, strict=True):
        record = _mapping(item, "reference record")
        point = root / str(record.get("directory"))
        supervision_path = point / "supervision.json"
        worker_path = point / "worker-result.json"
        point_context_path = point / "execution-context.json"
        log_path = point / "worker.log"
        outer_path = point / "outer-plan.json.gz"
        if any(
            not path.is_file()
            for path in (
                supervision_path,
                worker_path,
                point_context_path,
                log_path,
                outer_path,
            )
        ):
            raise ValueError("accepted vectorized reference point is incomplete")
        supervision = _mapping(
            json.loads(supervision_path.read_text()), "reference supervision"
        )
        worker = _mapping(json.loads(worker_path.read_text()), "reference worker")
        point_context = _mapping(
            json.loads(point_context_path.read_text()), "reference point context"
        )
        worker_outer = _mapping(worker.get("outer_plan"), "reference worker outer")
        artifact = _mapping(worker_outer.get("artifact"), "reference artifact")
        if (
            record.get("horizon_steps") != horizon
            or record.get("classification") != "accepted"
            or record.get("supervision_sha256") != sha256_path(supervision_path)
            or supervision.get("classification") != "accepted"
            or supervision.get("returncode") != 0
            or supervision.get("worker_result") != worker
            or supervision.get("start_context") != point_context
            or supervision.get("end_context") != point_context
            or supervision.get("context_matches") is not True
            or supervision.get("worker_log_sha256") != sha256_path(log_path)
            or supervision.get("outer_plan_sha256") != sha256_path(outer_path)
            or worker.get("classification") != "accepted"
            or worker.get("exception") is not None
            or worker.get("start_context") != point_context
            or worker.get("end_context") != point_context
            or worker.get("context_matches") is not True
            or worker_outer.get("accepted_primal") is not True
            or artifact.get("sha256") != sha256_path(outer_path)
            or artifact.get("bytes") != outer_path.stat().st_size
        ):
            raise ValueError("accepted vectorized reference chain is inconsistent")
        point_fixture = load_prefix_fixture(horizon)
        load_verified_outer_plan_archive(
            outer_path,
            inputs=point_fixture.inputs,
            policy=point_fixture.annual.policy,
            expected_solve_config_sha256=point_fixture.annual.solve_config_sha256,
            expected_source_fingerprint=str(point_context["source_fingerprint"]),
            expected_scenario_hash=point_fixture.scenario_sha256,
        )
        registry[horizon] = {
            "directory": point,
            "context": point_context,
            "supervision": supervision,
            "worker": worker,
        }
    return registry


def profile_execution_context(horizon_steps: int) -> Mapping[str, object]:
    fixture = load_prefix_fixture(horizon_steps)
    reference_result = ROOT / PREFIX_LADDER_OUTPUT_DIRECTORY / "ladder-result.json"
    if not reference_result.is_file() or sha256_path(reference_result) != (
        REFERENCE_LADDER_RESULT_SHA256
    ):
        raise ValueError(
            "accepted vectorized reference ladder is unavailable or changed"
        )
    production_fingerprint = shared_production_fingerprint()
    reference_production_fingerprint = shared_production_fingerprint(
        REFERENCE_EXECUTION_COMMIT
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "git_commit": _git("rev-parse", "HEAD"),
        "git_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": profile_source_fingerprint(),
        "shared_production_fingerprint": production_fingerprint,
        "reference_shared_production_fingerprint": (reference_production_fingerprint),
        "shared_production_matches_reference": (
            production_fingerprint == reference_production_fingerprint
        ),
        "horizon_steps": horizon_steps,
        "prefix_input_sha256": fixture.input_sha256,
        "prefix_scenario_sha256": fixture.scenario_sha256,
        "policy_sha256": fixture.annual.policy_sha256,
        "solve_config_sha256": fixture.annual.solve_config_sha256,
        "m14c_integration_commit": M14C_INTEGRATION_COMMIT,
        "m14c_integration_sha256": fixture.annual.m14c_integration_sha256,
        "m14c_integration_checkpoint": fixture.annual.m14c_integration_checkpoint,
        "m14c_source_commit": fixture.annual.m14c_source_commit,
        "big_experiment_parent_commit": fixture.annual.big_experiment_parent_commit,
        "m14c_merge_base_commit": fixture.annual.m14c_merge_base_commit,
        "annual_component_hashes": dict(fixture.annual.hashes),
        "annual_scenario_sha256": fixture.annual.scenario_hash,
        "prefix_ladder_executed": fixture.annual.prefix_ladder_executed,
        "annual_execution_authorized": fixture.annual.annual_execution_authorized,
        "temporal_assembly": "stepwise",
        "canonicalization_backend": "CPP",
        "generator_quadratic_cost": fixture.annual.generator_quadratic_cost,
        "generator_conditioning_evidence_sha256": (
            fixture.annual.generator_conditioning_evidence_sha256
        ),
        "reference_temporal_assembly": "vectorized",
        "reference_canonicalization_backend": "SCIPY",
        "reference_ladder_result_sha256": REFERENCE_LADDER_RESULT_SHA256,
        "resource_policy": _limit_payload(fixture.limits),
        "software_versions": dict(_software_versions()),
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }


def _phase_sample(phase: str, started: float) -> Mapping[str, object]:
    return {
        "phase": phase,
        "elapsed_seconds": time.monotonic() - started,
        "rss_bytes": process_rss_bytes(),
    }


def _timed_wrapper(
    original: Callable[..., object], name: str, timings: dict[str, float]
) -> Callable[..., object]:
    def wrapped(*args: object, **kwargs: object) -> object:
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            timings[name] = time.perf_counter() - started

    return wrapped


def _worker(directory: Path, context: Mapping[str, object]) -> int:
    started = time.monotonic()
    horizon = int(cast(int, context["horizon_steps"]))
    start_context = profile_execution_context(horizon)
    if start_context != context or context.get("git_clean") is not True:
        atomic_immutable_json(
            directory / "worker-result.json",
            {
                "schema_version": SCHEMA_VERSION,
                "horizon_steps": horizon,
                "classification": "provenance_mismatch",
                "start_context": start_context,
            },
        )
        return 2
    fixture = load_prefix_fixture(horizon)
    samples: list[Mapping[str, object]] = [_phase_sample("worker_start", started)]
    timings: dict[str, float] = {}
    archive = None
    outer = None
    exception = None
    classification = "worker_failure"

    def observe(phase: str) -> None:
        samples.append(_phase_sample(phase, started))

    try:
        with (
            patch.object(
                streaming_runner,
                "extract_results",
                _timed_wrapper(extract_results, "extraction_seconds", timings),
            ),
            patch.object(
                streaming_runner,
                "audit_probe",
                _timed_wrapper(audit_probe, "audit_seconds", timings),
            ),
        ):
            outer = streaming_runner.solve_frozen_outer(
                fixture.inputs,
                fixture.annual.policy,
                fixture.annual.solve_config,
                phase_observer=observe,
                temporal_assembly="stepwise",
            )
        samples.append(_phase_sample("after_outer", started))
        if outer.temporal_assembly != "stepwise" or (
            outer.canonicalization_backend != "CPP"
        ):
            raise ValueError(
                "stepwise profile used an unexpected representation/backend"
            )
        if outer.exception is not None:
            classification = "solver_failure"
        elif outer.audit.status in {"infeasible", "infeasible_inaccurate"}:
            classification = "solver_certified_infeasible"
        elif outer.audit.status not in {"optimal", "optimal_inaccurate"} or (
            outer.audit.missing_or_nonfinite_fields
        ):
            classification = "unusable_primal"
        elif not outer.accepted_primal:
            classification = "residual_rejection"
        else:
            classification = "accepted"
        archive_started = time.perf_counter()
        if classification == "accepted":
            try:
                archive = write_verified_outer_plan_archive(
                    directory / "outer-plan.json.gz",
                    outer,
                    inputs=fixture.inputs,
                    source_fingerprint=str(context["source_fingerprint"]),
                    scenario_hash=fixture.scenario_sha256,
                )
            except Exception:
                classification = "artifact_failure"
                raise
        timings["archive_seconds"] = time.perf_counter() - archive_started
        samples.append(_phase_sample("after_archive", started))
        dimensions = None if outer.build is None else _problem_dimensions(outer.build)
        outer = replace(outer, build=None)
        gc.collect()
        samples.append(_phase_sample("after_release", started))
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
        if classification == "worker_failure":
            classification = "construction_error"
        elif classification == "accepted":
            classification = "worker_failure"
        dimensions = None
    end_context = profile_execution_context(horizon)
    if end_context != start_context:
        classification = "provenance_mismatch"
    atomic_immutable_json(
        directory / "worker-result.json",
        {
            "schema_version": SCHEMA_VERSION,
            "horizon_steps": horizon,
            "classification": classification,
            "exception": exception,
            "dimensions": dimensions,
            "phase_timings": timings,
            "resource_samples": samples,
            "start_context": start_context,
            "end_context": end_context,
            "context_matches": start_context == end_context,
            "wall_time_seconds": time.monotonic() - started,
            "outer_plan": None
            if outer is None
            else {
                "accepted_primal": outer.accepted_primal,
                "status": outer.audit.status,
                "audit_residuals": dict(outer.audit.residuals),
                "solve_wall_seconds": outer.wall_time_seconds,
                "temporal_assembly": outer.temporal_assembly,
                "canonicalization_backend": outer.canonicalization_backend,
                "artifact": None
                if archive is None
                else {
                    "path": archive.relative_path,
                    "bytes": archive.bytes,
                    "sha256": archive.sha256,
                },
            },
        },
    )
    return 0 if classification == "accepted" else 1


def _accepted_worker_evidence(directory: Path, worker: Mapping[str, object]) -> bool:
    """Independently require complete accepted solve and archive evidence."""
    outer_path = directory / "outer-plan.json.gz"
    outer_raw = worker.get("outer_plan")
    if not isinstance(outer_raw, Mapping):
        return False
    outer = cast(Mapping[str, object], outer_raw)
    artifact_raw = outer.get("artifact")
    if not isinstance(artifact_raw, Mapping):
        return False
    artifact = cast(Mapping[str, object], artifact_raw)
    samples_raw = worker.get("resource_samples")
    if not isinstance(samples_raw, list):
        return False
    phases = [
        str(sample.get("phase"))
        for sample in samples_raw
        if isinstance(sample, Mapping)
    ]
    timings_raw = worker.get("phase_timings")
    if not isinstance(timings_raw, Mapping):
        return False
    timings = cast(Mapping[str, object], timings_raw)
    required_timings = ("extraction_seconds", "audit_seconds", "archive_seconds")
    timings_valid = all(
        isinstance(timings.get(name), (int, float))
        and not isinstance(timings.get(name), bool)
        and np.isfinite(float(cast(float, timings.get(name))))
        and float(cast(float, timings.get(name))) >= 0.0
        for name in required_timings
    )
    return (
        worker.get("classification") == "accepted"
        and worker.get("exception") is None
        and worker.get("context_matches") is True
        and isinstance(worker.get("dimensions"), Mapping)
        and outer.get("accepted_primal") is True
        and outer.get("status") in {"optimal", "optimal_inaccurate"}
        and outer.get("temporal_assembly") == "stepwise"
        and outer.get("canonicalization_backend") == "CPP"
        and phases == list(EXPECTED_WORKER_PHASES)
        and timings_valid
        and outer_path.is_file()
        and artifact.get("sha256") == sha256_path(outer_path)
        and artifact.get("bytes") == outer_path.stat().st_size
    )


def _supervise(
    directory: Path, context: Mapping[str, object], limits: PrefixExecutionLimits
) -> Mapping[str, object]:
    supervisor_started = time.monotonic()
    directory.mkdir()
    atomic_immutable_json(directory / "execution-context.json", context)
    context_path = directory / "execution-context.json"
    command = [
        sys.executable,
        "-m",
        "experiments.m14_time_vectorization.run_m14c_prefix_profile",
        "--worker",
        "--output-directory",
        str(directory),
        "--context-path",
        str(context_path),
    ]
    process: subprocess.Popen[bytes] | None = None
    first_rss = None
    peak_rss = 0.0
    final_rss = None
    triggers: list[str] = []
    launch_error = None
    error = None
    pending_interrupt: BaseException | None = None
    returncode = None
    log_path = directory / "worker.log"
    with log_path.open("wb") as log:
        worker_started = time.monotonic()
        try:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException as exc:
            if isinstance(exc, Exception):
                launch_error = f"{type(exc).__name__}: {exc}"
            else:
                pending_interrupt = exc
        else:
            try:
                atomic_json(directory / "active-worker.json", {"pid": process.pid})
                while process.poll() is None:
                    rss = _child_rss_mib(process.pid)
                    if rss is not None:
                        first_rss = rss if first_rss is None else first_rss
                        final_rss = rss
                        peak_rss = max(peak_rss, rss)
                        if rss > limits.child_rss_mib and "rss_limit" not in triggers:
                            triggers.append("rss_limit")
                    worker_elapsed = time.monotonic() - worker_started
                    total_elapsed = time.monotonic() - supervisor_started
                    if (
                        worker_elapsed > limits.worker_wall_seconds
                        and "worker_wall_limit" not in triggers
                    ):
                        triggers.append("worker_wall_limit")
                    if (
                        total_elapsed > limits.supervisor_wall_seconds
                        and "total_wall_limit" not in triggers
                    ):
                        triggers.append("total_wall_limit")
                    if triggers:
                        _terminate(process)
                        break
                    time.sleep(limits.poll_seconds)
                returncode = process.wait()
            except BaseException as exc:
                if isinstance(exc, Exception):
                    error = f"{type(exc).__name__}: {exc}"
                else:
                    pending_interrupt = exc
            finally:
                if process.poll() is None:
                    try:
                        _terminate(process)
                    except Exception as exc:
                        error = error or f"{type(exc).__name__}: {exc}"
                if returncode is None:
                    try:
                        returncode = process.wait()
                    except Exception as exc:
                        error = error or f"{type(exc).__name__}: {exc}"
    (directory / "active-worker.json").unlink(missing_ok=True)
    worker_path = directory / "worker-result.json"
    worker = json.loads(worker_path.read_text()) if worker_path.is_file() else None
    if launch_error is not None:
        classification = "worker_launch_failure"
    elif triggers:
        classification = triggers[0]
    elif pending_interrupt is not None:
        classification = "supervisor_interrupted"
    elif error is not None:
        classification = "supervisor_failure"
    elif returncode != 0:
        worker_classification = (
            str(worker.get("classification")) if isinstance(worker, Mapping) else None
        )
        classification = (
            worker_classification
            if worker_classification not in {None, "accepted"}
            else "worker_process_failure"
        )
    elif not isinstance(worker, Mapping):
        classification = "worker_failure"
    else:
        classification = str(worker.get("classification"))
    if classification == "accepted" and not _accepted_worker_evidence(
        directory, cast(Mapping[str, object], worker)
    ):
        classification = "artifact_failure"
    if classification == "accepted" and (
        first_rss is None or not np.isfinite(peak_rss) or peak_rss <= 0.0
    ):
        classification = "resource_measurement_failure"
    supervision = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "horizon_steps": context["horizon_steps"],
        "classification": classification,
        "returncode": returncode,
        "launch_error": launch_error,
        "supervisor_error": error,
        "supervisor_interruption": None
        if pending_interrupt is None
        else f"{type(pending_interrupt).__name__}: {pending_interrupt}",
        "resource_triggers": triggers,
        "first_sampled_rss_mib": first_rss,
        "peak_sampled_rss_mib": peak_rss,
        "final_sampled_rss_mib": final_rss,
        "worker_wall_time_seconds": time.monotonic() - worker_started,
        "wall_time_seconds": time.monotonic() - supervisor_started,
        "worker_result": worker,
        "worker_log_sha256": sha256_path(log_path),
        "outer_plan_sha256": sha256_path(directory / "outer-plan.json.gz")
        if (directory / "outer-plan.json.gz").is_file()
        else None,
        "resource_policy": _limit_payload(limits),
    }
    atomic_immutable_json(directory / "supervision.json", supervision)
    if pending_interrupt is not None:
        raise pending_interrupt
    return supervision


def run_profile(directory: Path = PROFILE_OUTPUT_DIRECTORY) -> Mapping[str, object]:
    directory = directory.expanduser().resolve()
    if directory.exists():
        raise FileExistsError("M14c prefix profile output already exists")
    if _git("status", "--porcelain") != "":
        raise ValueError("M14c prefix profiling requires a clean committed worktree")
    if _git("merge-base", M14C_INTEGRATION_COMMIT, "HEAD") != M14C_INTEGRATION_COMMIT:
        raise ValueError("profiling source lacks reviewed integration ancestry")
    validate_reference_ladder()
    contexts = {h: profile_execution_context(h) for h in PREFIX_LADDER_HORIZONS}
    directory.mkdir(parents=True)
    records = []
    pending_interrupt: BaseException | None = None
    try:
        for horizon in PREFIX_LADDER_HORIZONS:
            point = directory / f"stepwise-{horizon:04d}"
            try:
                supervision = _supervise(
                    point, contexts[horizon], PREFIX_EXECUTION_LIMITS[horizon]
                )
            except BaseException as exc:
                pending_interrupt = exc
                supervision_path = point / "supervision.json"
                if not supervision_path.is_file():
                    raise
                supervision = cast(
                    Mapping[str, object], json.loads(supervision_path.read_text())
                )
            record = {
                "horizon_steps": horizon,
                "classification": supervision["classification"],
                "directory": point.name,
                "supervision_sha256": sha256_path(point / "supervision.json"),
            }
            records.append(record)
            atomic_json(
                directory / "profile-progress.json",
                {"schema_version": SCHEMA_VERSION, "records": records},
            )
            if supervision["classification"] != "accepted":
                break
    except BaseException as exc:
        pending_interrupt = exc
    execution_complete = len(records) == len(PREFIX_LADDER_HORIZONS) and all(
        record["classification"] == "accepted" for record in records
    )
    result = {
        "schema_version": SCHEMA_VERSION,
        "classification": (
            "accepted"
            if execution_complete
            else "supervisor_failure"
            if isinstance(pending_interrupt, Exception)
            else "supervisor_interrupted"
            if pending_interrupt is not None
            else "stopped"
        ),
        "execution_complete": execution_complete,
        "records": records,
        "reference_ladder_result_sha256": REFERENCE_LADDER_RESULT_SHA256,
        "annual_execution_authorized": False,
        "supervisor_interruption": None
        if pending_interrupt is None or isinstance(pending_interrupt, Exception)
        else f"{type(pending_interrupt).__name__}: {pending_interrupt}",
        "root_error": None
        if pending_interrupt is None or not isinstance(pending_interrupt, Exception)
        else f"{type(pending_interrupt).__name__}: {pending_interrupt}",
    }
    atomic_immutable_json(directory / "profile-result.json", result)
    if pending_interrupt is not None:
        raise pending_interrupt
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument(
        "--output-directory", type=Path, default=PROFILE_OUTPUT_DIRECTORY
    )
    parser.add_argument("--context-path", type=Path)
    args = parser.parse_args()
    if args.worker:
        if args.context_path is None:
            parser.error("--worker requires --context-path")
        context = cast(Mapping[str, object], json.loads(args.context_path.read_text()))
        raise SystemExit(_worker(args.output_directory, context))
    previous_handler = signal.signal(signal.SIGTERM, _sigterm_handler)
    try:
        print(json.dumps(run_profile(args.output_directory), indent=2, sort_keys=True))
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    main()
