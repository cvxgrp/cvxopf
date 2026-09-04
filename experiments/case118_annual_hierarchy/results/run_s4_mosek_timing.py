"""Non-promotional annual MOSEK timing comparison for accepted S4."""

from __future__ import annotations

import argparse
from dataclasses import replace
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
from typing import Any, Mapping, cast
from unittest.mock import patch

import cvxpy as cp
from cvxpy.reductions.solvers.conic_solvers.mosek_conif import MOSEK
import mosek
import numpy as np
import psutil

from cvxopf import extract_results
from experiments.case118_annual_hierarchy import streaming_runner
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s4 import execution_context
from experiments.case118_annual_hierarchy.s4_analysis import analyze_s4
from experiments.case118_annual_hierarchy.s4_fixture import (
    S4_EXECUTION_LIMITS,
    S4_OUTPUT_DIRECTORY,
    load_s4_fixture,
)
from experiments.case118_annual_hierarchy.streaming_driver import process_rss_bytes
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)
from experiments.m14_time_vectorization.m14c_tight_tolerance_diagnostic import (
    full_bounds_audit,
    objective_accounting,
)


SCRIPT = Path(__file__).resolve()
OUTPUT = SCRIPT.with_name("s4_annual_outer_rated_mosek_timing_003")
SCHEMA_VERSION = 1


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()


def _json(value: object) -> object:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return _json(value.item())
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json(item) for item in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _capture_native(captured: dict[str, object]) -> Any:
    original = MOSEK.invert

    def wrapped(self: object, output: Mapping[str, object], inverse: object) -> object:
        task = output.get("task")
        if task is not None:
            handle = cast(Any, task)
            solution = mosek.soltype.itr
            primal = float(handle.getdouinf(mosek.dinfitem.intpnt_primal_obj))
            dual = float(handle.getdouinf(mosek.dinfitem.intpnt_dual_obj))
            captured.update(
                {
                    "solution_status": str(handle.getsolsta(solution)),
                    "problem_status": str(handle.getprosta(solution)),
                    "iterations": int(
                        handle.getintinf(mosek.iinfitem.intpnt_iter)
                    ),
                    "primal_objective": primal,
                    "dual_objective": dual,
                    "absolute_gap": abs(primal - dual),
                    "relative_gap": abs(primal - dual)
                    / max(1.0, abs(primal), abs(dual)),
                    "primal_feasibility": float(
                        handle.getdouinf(mosek.dinfitem.intpnt_primal_feas)
                    ),
                    "dual_feasibility": float(
                        handle.getdouinf(mosek.dinfitem.intpnt_dual_feas)
                    ),
                    "solution_primal_violation": float(
                        max(
                            handle.getdouinf(mosek.dinfitem.sol_itr_pviolcon),
                            handle.getdouinf(mosek.dinfitem.sol_itr_pviolvar),
                            handle.getdouinf(mosek.dinfitem.sol_itr_pviolcones),
                        )
                    ),
                    "solution_dual_violation": float(
                        max(
                            handle.getdouinf(mosek.dinfitem.sol_itr_dviolcon),
                            handle.getdouinf(mosek.dinfitem.sol_itr_dviolvar),
                            handle.getdouinf(mosek.dinfitem.sol_itr_dviolcones),
                        )
                    ),
                }
            )
        return original(self, output, inverse)

    return wrapped


def _sample(phase: str, started: float) -> Mapping[str, object]:
    return {
        "phase": phase,
        "elapsed_seconds": time.perf_counter() - started,
        "rss_bytes": process_rss_bytes(),
    }


def _array_digest(arrays: Mapping[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for name, values in sorted(arrays.items()):
        canonical = np.ascontiguousarray(values)
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(str(canonical.dtype).encode())
        digest.update(b"\0")
        digest.update(str(canonical.shape).encode())
        digest.update(b"\0")
        digest.update(canonical.tobytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _worker(directory: Path) -> int:
    started = time.perf_counter()
    context = execution_context()
    samples: list[Mapping[str, object]] = [_sample("worker_start", started)]
    exception = None
    build_seconds = None
    solve_seconds = None
    extraction_seconds = None
    captured: dict[str, object] = {}
    result: Mapping[str, object] = {}
    audit = None
    bounds: Mapping[str, object] = {
        "accepted": False,
        "classification": "unavailable_primal",
    }
    accounting: Mapping[str, object] = {
        "objective": None,
        "components": {},
        "accounting_residual_abs": None,
        "classification": "unavailable_primal",
    }
    arrays: dict[str, np.ndarray] = {}
    try:
        fixture = load_s4_fixture()
        storage = tuple(replace(unit) for unit in fixture.inputs.storage)
        samples.append(_sample("before_construction", started))
        phase_started = time.perf_counter()
        build = streaming_runner.build_window(
            fixture.inputs,
            "lossy_dc",
            0,
            fixture.inputs.horizon_steps,
            storage,
            temporal_assembly="vectorized",
        )
        build_seconds = time.perf_counter() - phase_started
        samples.append(_sample("after_construction", started))
        samples.append(_sample("before_solve", started))
        phase_started = time.perf_counter()
        try:
            with patch.object(MOSEK, "invert", _capture_native(captured)):
                build.solve(solver="MOSEK", nlp=False, verbose=True)
        finally:
            solve_seconds = time.perf_counter() - phase_started
        samples.append(_sample("after_solve", started))
        phase_started = time.perf_counter()
        result = extract_results(build)
        audit = audit_probe(
            fixture.inputs.case,
            build,
            result,
            generators=fixture.inputs.generators,
            loads=fixture.inputs.loads,
            nondispatchable=fixture.inputs.nondispatchable,
            storage=storage,
            delta=fixture.inputs.delta,
            branch_limit_sentinel=fixture.inputs.options.branch_limit_sentinel,
            tolerances=fixture.policy.tolerances,
        )
        usable = (
            result.get("status") == cp.OPTIMAL
            and all(
                isinstance(result.get(name), np.ndarray)
                for name in ("Pg", "p_flows", "p_net", "p_nd", "b", "soc")
            )
        )
        if usable:
            bounds = full_bounds_audit(build, result)
            accounting = objective_accounting(
                build, result, fixture.inputs.horizon_steps
            )
            arrays = {
                name: np.asarray(value)
                for name, value in result.items()
                if isinstance(value, np.ndarray)
            }
            np.savez_compressed(directory / "mosek-primal.npz", **arrays)
        extraction_seconds = time.perf_counter() - phase_started
        samples.append(_sample("after_extraction", started))
        stats = build.prob.solver_stats
        accounting_residual = accounting.get("accounting_residual_abs")
        accepted = bool(
            usable
            and audit.accepted_primal
            and bounds.get("accepted") is True
            and isinstance(accounting_residual, (int, float))
            and float(accounting_residual) <= 1e-5
            and captured.get("solution_status") == "solsta.optimal"
        )
        classification = "accepted" if accepted else "rejected"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "classification": classification,
            "exception": exception,
            "execution_context": context,
            "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "horizon_steps": fixture.inputs.horizon_steps,
            "temporal_assembly": "vectorized",
            "canonicalization_backend": build.canonicalization_backend,
            "solver": "MOSEK",
            "solver_configuration": "defaults",
            "mosek_params": {},
            "mosek_version": mosek.Env.getversion(),
            "build_seconds": build_seconds,
            "solve_seconds": solve_seconds,
            "extraction_and_audit_seconds": extraction_seconds,
            "worker_seconds": time.perf_counter() - started,
            "solver_stats": {
                "name": None if stats is None else stats.solver_name,
                "iterations": None if stats is None else stats.num_iters,
                "solve_time_seconds": None if stats is None else stats.solve_time,
                "native": captured,
            },
            "audit": {
                "accepted_primal": audit.accepted_primal,
                "status": audit.status,
                "residuals": dict(audit.residuals),
                "missing_or_nonfinite_fields": list(
                    audit.missing_or_nonfinite_fields
                ),
                "identity_error": audit.identity_error,
            },
            "bounds_audit": bounds,
            "objective_accounting": accounting,
            "result_status": result.get("status"),
            "result_schema": {
                name: list(value.shape) for name, value in sorted(arrays.items())
            },
            "result_array_sha256": _array_digest(arrays) if arrays else None,
            "primal_artifact": None
            if not arrays
            else {
                "path": "mosek-primal.npz",
                "bytes": (directory / "mosek-primal.npz").stat().st_size,
                "sha256": sha256_path(directory / "mosek-primal.npz"),
            },
            "resource_samples": samples,
        }
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
        payload = {
            "schema_version": SCHEMA_VERSION,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "classification": "worker_failure",
            "exception": exception,
            "execution_context": context,
            "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
            "mosek_version": mosek.Env.getversion(),
            "build_seconds": build_seconds,
            "solve_seconds": solve_seconds,
            "extraction_and_audit_seconds": extraction_seconds,
            "resource_samples": samples,
        }
    atomic_immutable_json(directory / "arm-result.json", _json(payload))
    return 0 if payload["classification"] == "accepted" else 1


def _rss_mib(pid: int) -> float | None:
    try:
        return float(psutil.Process(pid).memory_info().rss) / (1024.0**2)
    except (psutil.Error, OSError):
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


def _parent() -> int:
    if _git("status", "--porcelain") != "":
        raise ValueError("MOSEK timing comparison requires a clean tracked worktree")
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    baseline = analyze_s4(S4_OUTPUT_DIRECTORY)
    if baseline.get("accepted_for_s4b") is not True:
        raise ValueError("accepted S4 baseline did not pass independent analysis")
    OUTPUT.mkdir(parents=True)
    atomic_immutable_json(
        OUTPUT / "baseline.json",
        {
            "source_directory": str(S4_OUTPUT_DIRECTORY),
            "classification": baseline["classification"],
            "objective": baseline["objective"],
            "solve_wall_seconds": baseline["solve_wall_seconds"],
            "worker_wall_seconds": baseline["worker_wall_seconds"],
            "peak_supervisor_rss_mib": baseline["peak_supervisor_rss_mib"],
            "outer_plan_sha256": baseline["artifacts"]["outer-plan.json.gz"][
                "sha256"
            ],
        },
    )
    command = [
        sys.executable,
        "-m",
        "experiments.case118_annual_hierarchy.results.run_s4_mosek_timing",
        "--worker",
        "--directory",
        str(OUTPUT),
    ]
    log_path = OUTPUT / "worker.log"
    started = time.perf_counter()
    first_rss = None
    peak_rss = 0.0
    final_rss = None
    triggers: list[str] = []
    with log_path.open("wb") as log:
        process = subprocess.Popen(
            command,
            cwd=ROOT,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            while process.poll() is None:
                rss = _rss_mib(process.pid)
                if rss is not None:
                    first_rss = rss if first_rss is None else first_rss
                    final_rss = rss
                    peak_rss = max(peak_rss, rss)
                    if rss > S4_EXECUTION_LIMITS.child_rss_mib:
                        triggers.append("rss_limit")
                elapsed = time.perf_counter() - started
                if elapsed > S4_EXECUTION_LIMITS.worker_wall_seconds:
                    triggers.append("worker_wall_limit")
                if triggers:
                    _terminate(process)
                    break
                time.sleep(S4_EXECUTION_LIMITS.poll_seconds)
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                _terminate(process)
            raise
    arm_path = OUTPUT / "arm-result.json"
    arm = json.loads(arm_path.read_text()) if arm_path.is_file() else None
    accepted = bool(
        returncode == 0
        and not triggers
        and isinstance(arm, Mapping)
        and arm.get("classification") == "accepted"
    )
    comparison = None
    if isinstance(arm, Mapping) and isinstance(
        arm.get("objective_accounting"), Mapping
    ):
        accounting = cast(Mapping[str, object], arm["objective_accounting"])
        comparison = {
            "objective_absolute_difference": abs(
                float(accounting["objective"]) - float(baseline["objective"])
            ),
            "solve_seconds_difference": float(arm["solve_seconds"])
            - float(baseline["solve_wall_seconds"]),
            "solve_seconds_ratio_mosek_over_clarabel": float(arm["solve_seconds"])
            / float(baseline["solve_wall_seconds"]),
            "peak_rss_mib_difference": peak_rss
            - float(baseline["peak_supervisor_rss_mib"]),
            "peak_rss_ratio_mosek_over_clarabel": peak_rss
            / float(baseline["peak_supervisor_rss_mib"]),
        }
    root = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "accepted" if accepted else "failed",
        "non_promotional": True,
        "returncode": returncode,
        "resource_triggers": triggers,
        "first_sampled_rss_mib": first_rss,
        "peak_sampled_rss_mib": peak_rss,
        "final_sampled_rss_mib": final_rss,
        "supervisor_wall_seconds": time.perf_counter() - started,
        "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        "arm_result_sha256": sha256_path(arm_path) if arm_path.is_file() else None,
        "worker_log_sha256": sha256_path(log_path),
        "comparison_to_clarabel": comparison,
    }
    atomic_immutable_json(OUTPUT / "comparison-result.json", _json(root))
    print(json.dumps(_json(root), indent=2))
    return 0 if accepted else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--directory", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    if arguments.worker:
        raise SystemExit(_worker(arguments.directory.resolve()))
    raise SystemExit(_parent())


if __name__ == "__main__":
    main()
