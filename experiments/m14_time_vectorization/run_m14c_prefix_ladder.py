"""Run the frozen supervised 24/168/720-hour M14c Case118 prefix ladder."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import gc
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

import numpy as np

from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s4 import (
    _child_rss_mib,
    _problem_dimensions,
    _software_versions,
    _terminate,
    outer_equivalence_gate,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    write_verified_outer_plan_archive,
)
from experiments.case118_annual_hierarchy.streaming_driver import process_rss_bytes
from experiments.case118_annual_hierarchy.streaming_runner import solve_frozen_outer
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
PREFIX_SOURCE_FILES = (
    "experiments/m14_time_vectorization/M14C_INTEGRATION.json",
    "experiments/m14_time_vectorization/M14C_PROTOCOL.md",
    "experiments/m14_time_vectorization/m14c_prefix_fixture.py",
    "experiments/m14_time_vectorization/run_m14c_prefix_ladder.py",
    "experiments/case118_annual_hierarchy/S4_PROTOCOL.md",
    "experiments/case118_annual_hierarchy/audit.py",
    "experiments/case118_annual_hierarchy/p0_fixture.py",
    "experiments/case118_annual_hierarchy/pglib_case.py",
    "experiments/case118_annual_hierarchy/run_s0.py",
    "experiments/case118_annual_hierarchy/run_s4.py",
    "experiments/case118_annual_hierarchy/s4_equivalence.py",
    "experiments/case118_annual_hierarchy/s4_fixture.py",
    "experiments/case118_annual_hierarchy/scenario.py",
    "experiments/case118_annual_hierarchy/streaming_archive.py",
    "experiments/case118_annual_hierarchy/streaming_driver.py",
    "experiments/case118_annual_hierarchy/streaming_runner.py",
    "experiments/case118_annual_hierarchy/streaming_schema.py",
)


class SupervisorInterrupted(BaseException):
    """Catchable SIGTERM marker used to retain supervision evidence."""


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


def prefix_source_paths() -> tuple[Path, ...]:
    """Return the complete tracked prefix-execution source registry."""
    paths = [ROOT / name for name in PREFIX_SOURCE_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    result = tuple(sorted(set(paths)))
    missing = [
        path.relative_to(ROOT).as_posix() for path in result if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(f"M14c prefix source registry is incomplete: {missing}")
    return result


def prefix_source_fingerprint(commit: str | None = None) -> str:
    """Hash the prefix source at the working tree or a retained execution commit."""
    digest = hashlib.sha256()
    for path in prefix_source_paths():
        relative = path.relative_to(ROOT).as_posix()
        payload = (
            path.read_bytes()
            if commit is None
            else subprocess.run(
                ["git", "show", f"{commit}:{relative}"],
                cwd=ROOT,
                check=True,
                capture_output=True,
            ).stdout
        )
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


def prefix_execution_context(horizon_steps: int) -> Mapping[str, object]:
    """Capture source, frozen-input, integration, solver, and machine identity."""
    fixture = load_prefix_fixture(horizon_steps)
    annual = fixture.annual
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": prefix_source_fingerprint(),
        "m14c_integration_commit": M14C_INTEGRATION_COMMIT,
        "m14c_integration_sha256": annual.m14c_integration_sha256,
        "m14c_integration_checkpoint": annual.m14c_integration_checkpoint,
        "m14c_source_commit": annual.m14c_source_commit,
        "big_experiment_parent_commit": annual.big_experiment_parent_commit,
        "m14c_merge_base_commit": annual.m14c_merge_base_commit,
        "prefix_ladder_executed": annual.prefix_ladder_executed,
        "annual_execution_authorized": annual.annual_execution_authorized,
        "annual_scenario_sha256": annual.scenario_hash,
        "annual_component_hashes": dict(annual.hashes),
        "prefix_scenario_sha256": fixture.scenario_sha256,
        "prefix_input_sha256": fixture.input_sha256,
        "horizon_steps": horizon_steps,
        "delta_hours": fixture.inputs.delta,
        "policy_sha256": annual.policy_sha256,
        "solve_config_sha256": annual.solve_config_sha256,
        "temporal_assembly": annual.temporal_assembly,
        "canonicalization_backend": annual.canonicalization_backend,
        "generator_quadratic_cost": annual.generator_quadratic_cost,
        "generator_conditioning_evidence_sha256": (
            annual.generator_conditioning_evidence_sha256
        ),
        "resource_policy": _limit_payload(fixture.limits),
        "software_versions": dict(_software_versions()),
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }


def _safe_prefix_execution_context(horizon_steps: int) -> Mapping[str, object]:
    try:
        return prefix_execution_context(horizon_steps)
    except Exception as exc:
        return {"context_error": f"{type(exc).__name__}: {exc}"}


def ladder_execution_context() -> Mapping[str, object]:
    """Capture the common identity and every deterministic prefix fingerprint."""
    contexts = {
        str(horizon): prefix_execution_context(horizon)
        for horizon in PREFIX_LADDER_HORIZONS
    }
    first = contexts[str(PREFIX_LADDER_HORIZONS[0])]
    common_keys = (
        "git_commit",
        "git_clean",
        "source_fingerprint",
        "m14c_integration_commit",
        "m14c_integration_sha256",
        "m14c_integration_checkpoint",
        "m14c_source_commit",
        "big_experiment_parent_commit",
        "m14c_merge_base_commit",
        "prefix_ladder_executed",
        "annual_execution_authorized",
        "annual_scenario_sha256",
        "annual_component_hashes",
        "policy_sha256",
        "solve_config_sha256",
        "temporal_assembly",
        "canonicalization_backend",
        "generator_quadratic_cost",
        "generator_conditioning_evidence_sha256",
        "software_versions",
        "platform",
        "architecture",
    )
    return {
        "schema_version": SCHEMA_VERSION,
        **{name: first[name] for name in common_keys},
        "prefixes": {
            horizon: {
                "horizon_steps": context["horizon_steps"],
                "delta_hours": context["delta_hours"],
                "prefix_scenario_sha256": context["prefix_scenario_sha256"],
                "prefix_input_sha256": context["prefix_input_sha256"],
                "resource_policy": context["resource_policy"],
            }
            for horizon, context in contexts.items()
        },
    }


def _phase_sample(phase: str, started: float) -> Mapping[str, object]:
    return {
        "phase": phase,
        "elapsed_seconds": time.monotonic() - started,
        "rss_bytes": process_rss_bytes(),
    }


def _outer_outcome_classification(outer: object) -> str:
    """Apply the frozen, mutually exclusive outer-solve outcome registry."""
    audit = getattr(outer, "audit")
    if getattr(outer, "exception") is not None:
        return "solver_failure"
    if audit.status in {"infeasible", "infeasible_inaccurate"}:
        return "solver_certified_infeasible"
    if audit.status not in {"optimal", "optimal_inaccurate"} or (
        audit.missing_or_nonfinite_fields
    ):
        return "unusable_primal"
    if audit.identity_error is not None or not getattr(outer, "accepted_primal"):
        return "residual_rejection"
    return "accepted"


def _worker(
    directory: Path,
    *,
    horizon_steps: int,
    expected_commit: str,
    expected_source_fingerprint: str,
    expected_prefix_input_sha256: str,
) -> int:
    """Build, solve, audit, archive, and release exactly one prefix."""
    started = time.monotonic()
    start_context = _safe_prefix_execution_context(horizon_steps)
    if not (
        start_context.get("git_commit") == expected_commit
        and start_context.get("git_clean") is True
        and start_context.get("source_fingerprint") == expected_source_fingerprint
        and start_context.get("prefix_input_sha256") == expected_prefix_input_sha256
    ):
        atomic_immutable_json(
            directory / "worker-result.json",
            {
                "schema_version": SCHEMA_VERSION,
                "horizon_steps": horizon_steps,
                "classification": "provenance_mismatch",
                "start_context": start_context,
            },
        )
        return 2
    samples: list[Mapping[str, object]] = [_phase_sample("worker_start", started)]
    last_phase = "worker_start"
    outer = None
    archive = None
    dimensions = None
    exception = None
    classification = "worker_failure"
    try:
        fixture = load_prefix_fixture(horizon_steps)

        def observe(phase: str) -> None:
            nonlocal last_phase
            last_phase = phase
            samples.append(_phase_sample(phase, started))

        outer = solve_frozen_outer(
            fixture.inputs,
            fixture.annual.policy,
            fixture.annual.solve_config,
            phase_observer=observe,
            temporal_assembly=fixture.annual.temporal_assembly,
        )
        if outer.build is not None:
            dimensions = _problem_dimensions(outer.build)
        classification = _outer_outcome_classification(outer)
        if classification == "accepted":
            try:
                archive = write_verified_outer_plan_archive(
                    directory / "outer-plan.json.gz",
                    outer,
                    inputs=fixture.inputs,
                    source_fingerprint=expected_source_fingerprint,
                    scenario_hash=fixture.scenario_sha256,
                )
            except Exception:
                classification = "artifact_failure"
                raise
            samples.append(_phase_sample("after_archive", started))
            classification = "accepted"
        outer = replace(outer, build=None)
        gc.collect()
        samples.append(_phase_sample("after_release", started))
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
        if classification != "artifact_failure":
            classification = (
                "construction_error"
                if last_phase == "before_construction"
                else "worker_failure"
            )
    end_context = _safe_prefix_execution_context(horizon_steps)
    context_matches = start_context == end_context
    if not context_matches:
        classification = "provenance_mismatch"
    atomic_immutable_json(
        directory / "worker-result.json",
        {
            "schema_version": SCHEMA_VERSION,
            "horizon_steps": horizon_steps,
            "classification": classification,
            "exception": exception,
            "dimensions": dimensions,
            "outer_plan": None
            if outer is None
            else {
                "accepted_primal": outer.accepted_primal,
                "status": outer.audit.status,
                "missing_or_nonfinite_fields": list(
                    outer.audit.missing_or_nonfinite_fields
                ),
                "identity_error": outer.audit.identity_error,
                "audit_residuals": dict(outer.audit.residuals),
                "exception": outer.exception,
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
            "resource_samples": samples,
            "start_context": start_context,
            "end_context": end_context,
            "context_matches": context_matches,
            "wall_time_seconds": time.monotonic() - started,
        },
    )
    return 0 if classification == "accepted" else 1


def _supervise_prefix(
    directory: Path,
    *,
    horizon_steps: int,
    context: Mapping[str, object],
    limits: PrefixExecutionLimits,
) -> Mapping[str, object]:
    """Run one fresh prefix worker under the frozen resource envelope."""
    supervisor_started = time.monotonic()
    directory.mkdir()
    atomic_immutable_json(directory / "execution-context.json", context)
    command = [
        sys.executable,
        "-m",
        "experiments.m14_time_vectorization.run_m14c_prefix_ladder",
        "--worker",
        "--output-directory",
        str(directory),
        "--horizon-steps",
        str(horizon_steps),
        "--expected-commit",
        str(context["git_commit"]),
        "--expected-source-fingerprint",
        str(context["source_fingerprint"]),
        "--expected-prefix-input-sha256",
        str(context["prefix_input_sha256"]),
    ]
    worker_started = time.monotonic()
    first_rss = None
    peak_rss = 0.0
    final_rss = None
    stop_reason = None
    resource_triggers: list[str] = []
    launch_error = None
    supervisor_error = None
    supervisor_interruption = None
    pending_interrupt: BaseException | None = None
    returncode: int | None = None
    process: subprocess.Popen[bytes] | None = None
    log_path = directory / "worker.log"
    active_path = directory / "active-worker.json"

    def retain_supervisor_exception(exc: BaseException) -> None:
        nonlocal supervisor_error, supervisor_interruption, pending_interrupt
        message = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, Exception):
            if supervisor_error is None:
                supervisor_error = message
        elif pending_interrupt is None:
            pending_interrupt = exc
            supervisor_interruption = message

    with log_path.open("wb") as log:
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
                supervisor_interruption = f"{type(exc).__name__}: {exc}"
        else:
            try:
                atomic_json(
                    active_path,
                    {
                        "supervisor_pid": os.getpid(),
                        "worker_pid": process.pid,
                        "horizon_steps": horizon_steps,
                        "started_epoch_seconds": time.time(),
                    },
                )
                while process.poll() is None:
                    rss = _child_rss_mib(process.pid)
                    if rss is not None:
                        first_rss = rss if first_rss is None else first_rss
                        final_rss = rss
                        peak_rss = max(peak_rss, rss)
                        if (
                            rss > limits.child_rss_mib
                            and "rss_limit" not in resource_triggers
                        ):
                            resource_triggers.append("rss_limit")
                    worker_elapsed = time.monotonic() - worker_started
                    total_elapsed = time.monotonic() - supervisor_started
                    if (
                        worker_elapsed > limits.worker_wall_seconds
                        and "worker_wall_limit" not in resource_triggers
                    ):
                        resource_triggers.append("worker_wall_limit")
                    if (
                        total_elapsed > limits.supervisor_wall_seconds
                        and "total_wall_limit" not in resource_triggers
                    ):
                        resource_triggers.append("total_wall_limit")
                    # Frozen priority: RSS, worker wall, total wall. All observed
                    # simultaneous triggers remain retained.
                    stop_reason = resource_triggers[0] if resource_triggers else None
                    if stop_reason is not None:
                        _terminate(process)
                        break
                    time.sleep(limits.poll_seconds)
                returncode = process.wait()
            except BaseException as exc:
                retain_supervisor_exception(exc)
            finally:
                try:
                    if process.poll() is None:
                        _terminate(process)
                except BaseException as exc:
                    retain_supervisor_exception(exc)
                if returncode is None:
                    try:
                        returncode = process.wait()
                    except BaseException as exc:
                        retain_supervisor_exception(exc)
    worker_path = directory / "worker-result.json"
    worker = (
        _mapping(json.loads(worker_path.read_text()), "prefix worker result")
        if worker_path.is_file()
        else None
    )
    end_context = _safe_prefix_execution_context(horizon_steps)
    context_matches = context == end_context
    if launch_error is not None:
        classification = "worker_launch_failure"
    elif stop_reason is not None:
        classification = stop_reason
    elif supervisor_interruption is not None:
        classification = "supervisor_interrupted"
    elif supervisor_error is not None:
        classification = "supervisor_failure"
    elif not context_matches or (
        worker is not None and worker.get("classification") == "provenance_mismatch"
    ):
        classification = "provenance_mismatch"
    elif returncode != 0:
        worker_classification = (
            None if worker is None else str(worker.get("classification"))
        )
        classification = (
            worker_classification
            if worker_classification not in {None, "accepted"}
            else "worker_process_failure"
        )
    elif worker is None:
        classification = "worker_failure"
    else:
        classification = str(worker.get("classification"))
    outer_path = directory / "outer-plan.json.gz"
    if classification == "accepted":
        worker_outer_raw = None if worker is None else worker.get("outer_plan")
        worker_outer = (
            cast(Mapping[str, object], worker_outer_raw)
            if isinstance(worker_outer_raw, Mapping)
            else {}
        )
        artifact_raw = worker_outer.get("artifact")
        artifact = (
            cast(Mapping[str, object], artifact_raw)
            if isinstance(artifact_raw, Mapping)
            else {}
        )
        if (
            not outer_path.is_file()
            or artifact.get("sha256") != sha256_path(outer_path)
            or artifact.get("bytes") != outer_path.stat().st_size
        ):
            classification = "artifact_failure"
        elif first_rss is None or not np.isfinite(peak_rss) or peak_rss <= 0.0:
            classification = "resource_measurement_failure"
    supervision: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "horizon_steps": horizon_steps,
        "classification": classification,
        "returncode": returncode,
        "launch_error": launch_error,
        "supervisor_error": supervisor_error,
        "supervisor_interruption": supervisor_interruption,
        "resource_triggers": resource_triggers,
        "first_sampled_rss_mib": first_rss,
        "peak_sampled_rss_mib": peak_rss,
        "final_sampled_rss_mib": final_rss,
        "worker_wall_time_seconds": time.monotonic() - worker_started,
        "wall_time_seconds": time.monotonic() - supervisor_started,
        "start_context": context,
        "end_context": end_context,
        "context_matches": context_matches,
        "worker_result": worker,
        "worker_log": log_path.name,
        "worker_log_sha256": sha256_path(log_path),
        "outer_plan_sha256": sha256_path(outer_path) if outer_path.is_file() else None,
        "resource_policy": _limit_payload(limits),
    }
    atomic_immutable_json(directory / "supervision.json", supervision)
    active_path.unlink(missing_ok=True)
    if pending_interrupt is not None:
        raise pending_interrupt
    return supervision


def run_prefix_ladder(
    directory: Path = PREFIX_LADDER_OUTPUT_DIRECTORY,
) -> Mapping[str, object]:
    """Execute the ordered ladder once, stopping after the first nonacceptance."""
    directory = directory.expanduser().resolve()
    if directory.exists():
        raise FileExistsError("M14c prefix-ladder output directory already exists")
    context = ladder_execution_context()
    if context.get("git_clean") is not True:
        raise ValueError("M14c prefix execution requires a clean committed worktree")
    if _git("merge-base", M14C_INTEGRATION_COMMIT, "HEAD") != M14C_INTEGRATION_COMMIT:
        raise ValueError("M14c integration commit is not an ancestor of execution HEAD")
    if (
        context.get("prefix_ladder_executed") is not False
        or context.get("annual_execution_authorized") is not False
    ):
        raise ValueError("M14c prefix ladder lacks pre-ladder execution authority")
    equivalence = outer_equivalence_gate()
    if equivalence.get("equivalent") is not True:
        raise ValueError("M14c post-integration S4 seam equivalence failed")
    directory.mkdir(parents=True)
    atomic_immutable_json(directory / "execution-context.json", context)
    atomic_immutable_json(directory / "outer-equivalence.json", equivalence)
    records: list[Mapping[str, object]] = []
    accepted: list[int] = []
    stopped_horizon = None
    progress_path = directory / "ladder-progress.json"

    def publish_progress(classification: str, interrupted_horizon: int | None) -> None:
        atomic_json(
            progress_path,
            {
                "schema_version": SCHEMA_VERSION,
                "classification": classification,
                "attempted_horizons": [
                    int(cast(int, record["horizon_steps"])) for record in records
                ],
                "accepted_horizons": accepted,
                "stopped_horizon": stopped_horizon,
                "interrupted_horizon": interrupted_horizon,
                "records": records,
                "execution_context_sha256": sha256_path(
                    directory / "execution-context.json"
                ),
                "outer_equivalence_sha256": sha256_path(
                    directory / "outer-equivalence.json"
                ),
                "annual_execution_authorized": False,
            },
        )

    def retain_point(horizon_steps: int, point_directory: Path) -> None:
        supervision_path = point_directory / "supervision.json"
        supervision = _mapping(
            json.loads(supervision_path.read_text()), "retained prefix supervision"
        )
        record = {
            "horizon_steps": horizon_steps,
            "classification": supervision["classification"],
            "directory": point_directory.name,
            "supervision_sha256": sha256_path(supervision_path),
        }
        if not records or records[-1] != record:
            records.append(record)
        if supervision.get("classification") == "accepted":
            if horizon_steps not in accepted:
                accepted.append(horizon_steps)

    publish_progress("running", None)
    interrupted_horizon: int | None = None
    pending_interrupt: BaseException | None = None
    try:
        for horizon_steps in PREFIX_LADDER_HORIZONS:
            interrupted_horizon = horizon_steps
            point_context = prefix_execution_context(horizon_steps)
            if point_context.get("git_commit") != context.get("git_commit") or (
                point_context.get("source_fingerprint")
                != context.get("source_fingerprint")
            ):
                raise ValueError("M14c source changed during ordered prefix execution")
            point_directory = directory / f"prefix-{horizon_steps:04d}"
            try:
                supervision = _supervise_prefix(
                    point_directory,
                    horizon_steps=horizon_steps,
                    context=point_context,
                    limits=PREFIX_EXECUTION_LIMITS[horizon_steps],
                )
            except BaseException as exc:
                if isinstance(exc, Exception):
                    raise
                pending_interrupt = exc
                interrupted_horizon = horizon_steps
                if (point_directory / "supervision.json").is_file():
                    retain_point(horizon_steps, point_directory)
                stopped_horizon = horizon_steps
                publish_progress("supervisor_interrupted", interrupted_horizon)
                break
            interrupted_horizon = None
            retain_point(horizon_steps, point_directory)
            if supervision.get("classification") != "accepted":
                stopped_horizon = horizon_steps
                publish_progress("stopped", None)
                break
            publish_progress("running", None)
    except BaseException as exc:
        if isinstance(exc, Exception):
            raise
        pending_interrupt = exc
        stopped_horizon = interrupted_horizon
        publish_progress("supervisor_interrupted", interrupted_horizon)
    complete = tuple(accepted) == PREFIX_LADDER_HORIZONS
    if complete:
        publish_progress("accepted", None)
    result: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "classification": (
            "accepted"
            if complete
            else "supervisor_interrupted"
            if pending_interrupt is not None
            else "stopped"
        ),
        "execution_complete": complete,
        "attempted_horizons": [
            int(cast(int, record["horizon_steps"])) for record in records
        ],
        "accepted_horizons": accepted,
        "stopped_horizon": stopped_horizon,
        "interrupted_horizon": interrupted_horizon,
        "records": records,
        "execution_context_sha256": sha256_path(directory / "execution-context.json"),
        "outer_equivalence_sha256": sha256_path(directory / "outer-equivalence.json"),
        "ladder_progress_sha256": sha256_path(progress_path),
        "annual_execution_authorized": False,
    }
    atomic_immutable_json(directory / "ladder-result.json", result)
    if pending_interrupt is not None:
        raise pending_interrupt
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-directory", type=Path, default=PREFIX_LADDER_OUTPUT_DIRECTORY
    )
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--horizon-steps", type=int)
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-source-fingerprint")
    parser.add_argument("--expected-prefix-input-sha256")
    arguments = parser.parse_args()
    if arguments.worker:
        if (
            arguments.horizon_steps is None
            or arguments.expected_commit is None
            or arguments.expected_source_fingerprint is None
            or arguments.expected_prefix_input_sha256 is None
        ):
            parser.error("prefix worker requires complete expected provenance")
        raise SystemExit(
            _worker(
                arguments.output_directory.expanduser().resolve(),
                horizon_steps=arguments.horizon_steps,
                expected_commit=arguments.expected_commit,
                expected_source_fingerprint=arguments.expected_source_fingerprint,
                expected_prefix_input_sha256=arguments.expected_prefix_input_sha256,
            )
        )
    previous_handler = signal.signal(signal.SIGTERM, _sigterm_handler)
    try:
        print(json.dumps(run_prefix_ladder(arguments.output_directory), indent=2))
    finally:
        signal.signal(signal.SIGTERM, previous_handler)


if __name__ == "__main__":
    main()
