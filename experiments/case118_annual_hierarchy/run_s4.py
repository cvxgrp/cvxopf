"""Run the supervised outer-only Case118 S4 annual solve."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import gc
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

import cvxpy as cp
import numpy as np

from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.s4_fixture import (
    S4_EXECUTION_LIMITS,
    S4_OUTPUT_DIRECTORY,
    load_s4_fixture,
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


SCHEMA_VERSION = 1
S4_SOURCE_FILES = (
    "experiments/m14_time_vectorization/M14C_INTEGRATION.json",
    "experiments/m14_time_vectorization/M14C_REPRESENTATION_DISPOSITION.json",
    "experiments/m14_time_vectorization/M14C_PREFIX_LADDER_RESULTS.json",
    "experiments/m14_time_vectorization/M14C_PROTOCOL.md",
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


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True
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


def s4_source_paths() -> tuple[Path, ...]:
    """Return every tracked Python/document source used by S4 execution."""
    paths = [ROOT / name for name in S4_SOURCE_FILES]
    paths.extend((ROOT / "src/cvxopf").rglob("*.py"))
    result = tuple(sorted(set(paths)))
    missing = [
        path.relative_to(ROOT).as_posix() for path in result if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError(f"S4 source registry is incomplete: {missing}")
    return result


def s4_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in s4_source_paths():
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def execution_context() -> Mapping[str, object]:
    """Capture the complete source, scenario, solver, and machine identity."""
    fixture = load_s4_fixture()
    return {
        "git_commit": _git("rev-parse", "HEAD"),
        "git_clean": _git("status", "--porcelain") == "",
        "source_fingerprint": s4_source_fingerprint(),
        "scenario_hash": fixture.scenario_hash,
        "component_hashes": dict(fixture.hashes),
        "policy_sha256": fixture.policy_sha256,
        "solve_config_sha256": fixture.solve_config_sha256,
        "temporal_assembly": fixture.temporal_assembly,
        "canonicalization_backend": fixture.canonicalization_backend,
        "generator_quadratic_cost": fixture.generator_quadratic_cost,
        "generator_conditioning_evidence_sha256": (
            fixture.generator_conditioning_evidence_sha256
        ),
        "m14c_integration_checkpoint": fixture.m14c_integration_checkpoint,
        "m14c_source_commit": fixture.m14c_source_commit,
        "big_experiment_parent_commit": fixture.big_experiment_parent_commit,
        "m14c_merge_base_commit": fixture.m14c_merge_base_commit,
        "prefix_ladder_executed": fixture.prefix_ladder_executed,
        "annual_execution_authorized": fixture.annual_execution_authorized,
        "m14c_representation_disposition_sha256": (
            fixture.m14c_representation_disposition_sha256
        ),
        "m14c_prefix_ladder_results_sha256": (
            fixture.m14c_prefix_ladder_results_sha256
        ),
        "m14c_integration_sha256": fixture.m14c_integration_sha256,
        "software_versions": dict(_software_versions()),
        "platform": platform.platform(),
        "architecture": platform.machine(),
    }


def _safe_execution_context() -> Mapping[str, object]:
    try:
        return execution_context()
    except Exception as exc:
        return {"context_error": f"{type(exc).__name__}: {exc}"}


def outer_equivalence_gate() -> Mapping[str, object]:
    """Run the bounded public-versus-streaming gate before annual execution."""
    from experiments.case118_annual_hierarchy.s4_equivalence import (
        run_s4_outer_equivalence,
    )

    return run_s4_outer_equivalence()


def _problem_dimensions(build: object) -> Mapping[str, int]:
    problem = cast(object, getattr(build, "prob"))
    equalities = 0
    inequalities = 0
    other = 0
    for constraint in getattr(problem, "constraints"):
        if isinstance(constraint, cp.constraints.Equality):
            equalities += int(constraint.size)
        elif isinstance(constraint, cp.constraints.Inequality):
            inequalities += int(constraint.size)
        else:
            other += int(constraint.size)
    return {
        "scalar_variables": sum(
            int(variable.size) for variable in getattr(problem, "variables")()
        ),
        "scalar_equalities": equalities,
        "explicit_scalar_inequalities": inequalities,
        "other_scalar_constraints": other,
        "constraint_objects": len(getattr(problem, "constraints")),
    }


def _phase_sample(phase: str, started: float) -> Mapping[str, object]:
    return {
        "phase": phase,
        "elapsed_seconds": time.monotonic() - started,
        "rss_bytes": process_rss_bytes(),
    }


def _worker(
    directory: Path, *, expected_commit: str, expected_source_fingerprint: str
) -> int:
    started = time.monotonic()
    start_context = _safe_execution_context()
    if not (
        start_context.get("git_commit") == expected_commit
        and start_context.get("git_clean") is True
        and start_context.get("source_fingerprint") == expected_source_fingerprint
    ):
        atomic_immutable_json(
            directory / "worker-result.json",
            {
                "schema_version": SCHEMA_VERSION,
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
        fixture = load_s4_fixture()

        def observe(phase: str) -> None:
            nonlocal last_phase
            last_phase = phase
            samples.append(_phase_sample(phase, started))

        outer = solve_frozen_outer(
            fixture.inputs,
            fixture.policy,
            fixture.solve_config,
            phase_observer=observe,
            temporal_assembly=fixture.temporal_assembly,
        )
        if outer.build is not None:
            dimensions = _problem_dimensions(outer.build)
        if outer.exception is not None:
            classification = "solver_failure"
        elif outer.audit.status in {"infeasible", "infeasible_inaccurate"}:
            classification = "solver_certified_infeasible"
        elif not outer.accepted_primal:
            classification = "unusable_primal"
        else:
            try:
                archive = write_verified_outer_plan_archive(
                    directory / "outer-plan.json.gz",
                    outer,
                    inputs=fixture.inputs,
                    source_fingerprint=expected_source_fingerprint,
                    scenario_hash=fixture.scenario_hash,
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
    end_context = _safe_execution_context()
    context_matches = start_context == end_context
    if not context_matches:
        classification = "provenance_mismatch"
    atomic_immutable_json(
        directory / "worker-result.json",
        {
            "schema_version": SCHEMA_VERSION,
            "classification": classification,
            "exception": exception,
            "dimensions": dimensions,
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
            "resource_samples": samples,
            "start_context": start_context,
            "end_context": end_context,
            "context_matches": context_matches,
            "wall_time_seconds": time.monotonic() - started,
        },
    )
    return 0 if classification == "accepted" else 1


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


def run_s4(directory: Path = S4_OUTPUT_DIRECTORY) -> Mapping[str, object]:
    """Launch exactly one fresh, resource-supervised annual outer worker."""
    supervisor_started = time.monotonic()
    directory = directory.expanduser().resolve()
    if directory.exists():
        raise FileExistsError("S4 output directory already exists")
    fixture = load_s4_fixture()
    if not fixture.annual_execution_authorized:
        raise ValueError(
            "S4 annual execution is not authorized; complete and review the frozen "
            "24/168/720-hour prefix ladder first"
        )
    context = execution_context()
    if context.get("git_clean") is not True:
        raise ValueError("S4 execution requires a clean committed worktree")
    equivalence = outer_equivalence_gate()
    if equivalence.get("equivalent") is not True:
        raise ValueError("S4 outer equivalence preflight failed")
    directory.mkdir(parents=True)
    atomic_immutable_json(directory / "execution-context.json", context)
    atomic_immutable_json(directory / "outer-equivalence.json", equivalence)
    command = [
        sys.executable,
        "-m",
        "experiments.case118_annual_hierarchy.run_s4",
        "--worker",
        "--output-directory",
        str(directory),
        "--expected-commit",
        str(context["git_commit"]),
        "--expected-source-fingerprint",
        str(context["source_fingerprint"]),
    ]
    worker_started = time.monotonic()
    first_rss = None
    peak_rss = 0.0
    final_rss = None
    stop_reason = None
    resource_triggers: list[str] = []
    launch_error = None
    returncode: int | None = None
    process: subprocess.Popen[bytes] | None = None
    log_path = directory / "worker.log"
    active_path = directory / "active-worker.json"
    with log_path.open("wb") as log:
        try:
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
                    "supervisor_pid": os.getpid(),
                    "worker_pid": process.pid,
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
                        rss > S4_EXECUTION_LIMITS.child_rss_mib
                        and "rss_limit" not in resource_triggers
                    ):
                        resource_triggers.append("rss_limit")
                worker_elapsed = time.monotonic() - worker_started
                total_elapsed = time.monotonic() - supervisor_started
                if (
                    worker_elapsed > S4_EXECUTION_LIMITS.worker_wall_seconds
                    and "worker_wall_limit" not in resource_triggers
                ):
                    resource_triggers.append("worker_wall_limit")
                if (
                    total_elapsed > S4_EXECUTION_LIMITS.supervisor_wall_seconds
                    and "total_wall_limit" not in resource_triggers
                ):
                    resource_triggers.append("total_wall_limit")
                # Frozen priority for simultaneous observations: RSS, worker
                # wall, then total wall. All triggers remain retained below.
                stop_reason = resource_triggers[0] if resource_triggers else None
                if stop_reason is not None:
                    _terminate(process)
                    break
                time.sleep(S4_EXECUTION_LIMITS.poll_seconds)
            returncode = process.wait()
        except Exception as exc:
            launch_error = f"{type(exc).__name__}: {exc}"
            if process is not None and process.poll() is None:
                _terminate(process)
                returncode = process.wait()
    worker_path = directory / "worker-result.json"
    worker = (
        _mapping(json.loads(worker_path.read_text()), "S4 worker result")
        if worker_path.is_file()
        else None
    )
    end_context = _safe_execution_context()
    context_matches = context == end_context
    if launch_error is not None:
        classification = "worker_launch_failure"
    elif stop_reason is not None:
        classification = stop_reason
    elif not context_matches or (
        worker is not None and worker.get("classification") == "provenance_mismatch"
    ):
        classification = "provenance_mismatch"
    elif returncode != 0 or worker is None:
        classification = (
            str(worker.get("classification"))
            if worker is not None
            else "worker_failure"
        )
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
        worker_artifact_raw = worker_outer.get("artifact")
        worker_artifact = (
            cast(Mapping[str, object], worker_artifact_raw)
            if isinstance(worker_artifact_raw, Mapping)
            else {}
        )
        if (
            not outer_path.is_file()
            or worker_artifact.get("sha256") != sha256_path(outer_path)
            or worker_artifact.get("bytes") != outer_path.stat().st_size
        ):
            classification = "artifact_failure"
        elif first_rss is None or not np.isfinite(peak_rss) or peak_rss <= 0.0:
            classification = "resource_measurement_failure"
    supervision: Mapping[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classification": classification,
        "returncode": returncode,
        "launch_error": launch_error,
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
        "resource_policy": {
            "rss_limit_mib": S4_EXECUTION_LIMITS.child_rss_mib,
            "worker_wall_seconds": S4_EXECUTION_LIMITS.worker_wall_seconds,
            "supervisor_wall_seconds": S4_EXECUTION_LIMITS.supervisor_wall_seconds,
            "poll_seconds": S4_EXECUTION_LIMITS.poll_seconds,
        },
    }
    atomic_immutable_json(directory / "supervision.json", supervision)
    atomic_json(directory / "latest-supervision.json", supervision)
    active_path.unlink(missing_ok=True)
    return supervision


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-directory", type=Path, default=S4_OUTPUT_DIRECTORY)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--expected-commit")
    parser.add_argument("--expected-source-fingerprint")
    arguments = parser.parse_args()
    if arguments.worker:
        if (
            arguments.expected_commit is None
            or arguments.expected_source_fingerprint is None
        ):
            parser.error("S4 worker requires expected provenance")
        raise SystemExit(
            _worker(
                arguments.output_directory.expanduser().resolve(),
                expected_commit=arguments.expected_commit,
                expected_source_fingerprint=arguments.expected_source_fingerprint,
            )
        )
    print(json.dumps(run_s4(arguments.output_directory), indent=2))


if __name__ == "__main__":
    main()
