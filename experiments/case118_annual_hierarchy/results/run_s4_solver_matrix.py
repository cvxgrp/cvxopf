"""Run a modest non-promotional Case118 solver timing/completion matrix."""

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
from typing import Mapping, cast

import cvxpy as cp
import numpy as np
import psutil

from cvxopf import extract_results
from experiments.case118_annual_hierarchy import streaming_runner
from experiments.case118_annual_hierarchy.audit import audit_probe
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.run_s4 import execution_context
from experiments.case118_annual_hierarchy.s4_analysis import analyze_s4
from experiments.case118_annual_hierarchy.s4_fixture import (
    S4_OUTPUT_DIRECTORY,
    load_s4_fixture,
)
from experiments.case118_annual_hierarchy.streaming_driver import process_rss_bytes
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)
from experiments.m14_time_vectorization.m14c_prefix_fixture import (
    load_prefix_fixture,
)
from experiments.m14_time_vectorization.m14c_tight_tolerance_diagnostic import (
    full_bounds_audit,
    objective_accounting,
)


SCRIPT = Path(__file__).resolve()
OUTPUT = SCRIPT.with_name("s4_solver_matrix_001")
PREFIX_RESULTS = ROOT / (
    "experiments/m14_time_vectorization/M14C_PREFIX_LADDER_RESULTS.json"
)
MOSEK_PREFIX_ROOT = ROOT / (
    "experiments/m14_time_vectorization/results/"
    "m14c_case118_mosek_comparison"
)
MOSEK_ANNUAL_ROOT = SCRIPT.with_name("s4_annual_outer_rated_mosek_timing_003")
SOLVERS = ("OSQP", "SCS", "HIGHS")
HORIZONS = (24, 168, 720, 8760)
RSS_LIMIT_MIB = 16_384.0
WALL_LIMIT_SECONDS = 300.0
POLL_SECONDS = 1.0
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


def _sample(phase: str, started: float) -> Mapping[str, object]:
    return {
        "phase": phase,
        "elapsed_seconds": time.perf_counter() - started,
        "rss_bytes": process_rss_bytes(),
    }


def _array_digest(result: Mapping[str, object]) -> str | None:
    arrays = {
        name: np.ascontiguousarray(value)
        for name, value in result.items()
        if isinstance(value, np.ndarray)
    }
    if not arrays:
        return None
    digest = hashlib.sha256()
    for name, values in sorted(arrays.items()):
        digest.update(name.encode())
        digest.update(b"\0")
        digest.update(str(values.dtype).encode())
        digest.update(b"\0")
        digest.update(str(values.shape).encode())
        digest.update(b"\0")
        digest.update(values.tobytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _extra_stats(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            if isinstance(item, (str, int, float, bool, np.generic)) or item is None:
                result[str(key)] = _json(item)
            elif str(key) == "info":
                result[str(key)] = _extra_stats(item)
        return result
    result = {}
    for name in dir(value):
        if name.startswith("_"):
            continue
        try:
            item = getattr(value, name)
        except Exception:
            continue
        if isinstance(item, (str, int, float, bool, np.generic)) or item is None:
            result[name] = _json(item)
    return result


def _fixture(horizon: int) -> tuple[object, object, object]:
    if horizon == 8760:
        annual = load_s4_fixture()
        return annual.inputs, annual.policy, annual
    prefix = load_prefix_fixture(horizon)
    return prefix.inputs, prefix.annual.policy, prefix.annual


def _worker(directory: Path, solver: str, horizon: int) -> int:
    started = time.perf_counter()
    context = execution_context()
    samples: list[Mapping[str, object]] = [_sample("worker_start", started)]
    exception = None
    build_seconds = None
    solve_seconds = None
    audit_seconds = None
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
    stats = None
    try:
        inputs_raw, policy_raw, _ = _fixture(horizon)
        inputs = cast(object, inputs_raw)
        policy = cast(object, policy_raw)
        storage = tuple(replace(unit) for unit in inputs.storage)
        samples.append(_sample("before_construction", started))
        phase_started = time.perf_counter()
        build = streaming_runner.build_window(
            inputs,
            "lossy_dc",
            0,
            horizon,
            storage,
            temporal_assembly="vectorized",
        )
        build_seconds = time.perf_counter() - phase_started
        samples.append(_sample("after_construction", started))
        samples.append(_sample("before_solve", started))
        phase_started = time.perf_counter()
        try:
            build.solve(solver=solver, nlp=False, verbose=True)
        finally:
            solve_seconds = time.perf_counter() - phase_started
        samples.append(_sample("after_solve", started))
        phase_started = time.perf_counter()
        result = extract_results(build)
        audit = audit_probe(
            inputs.case,
            build,
            result,
            generators=inputs.generators,
            loads=inputs.loads,
            nondispatchable=inputs.nondispatchable,
            storage=storage,
            delta=inputs.delta,
            branch_limit_sentinel=inputs.options.branch_limit_sentinel,
            tolerances=policy.tolerances,
        )
        usable = all(
            isinstance(result.get(name), np.ndarray)
            for name in ("Pg", "p_flows", "p_net", "p_nd", "b", "soc")
        )
        if usable:
            bounds = full_bounds_audit(build, result)
            accounting = objective_accounting(build, result, horizon)
        audit_seconds = time.perf_counter() - phase_started
        samples.append(_sample("after_audit", started))
        stats = build.prob.solver_stats
        status = result.get("status")
        accounting_residual = accounting.get("accounting_residual_abs")
        if status in {cp.INFEASIBLE, cp.INFEASIBLE_INACCURATE}:
            classification = "solver_certified_infeasible"
        elif not usable:
            classification = "unusable_primal"
        elif not audit.accepted_primal:
            classification = "residual_rejection"
        elif bounds.get("accepted") is not True:
            classification = "bounds_rejection"
        elif not isinstance(accounting_residual, (int, float)) or float(
            accounting_residual
        ) > 1e-5:
            classification = "accounting_rejection"
        else:
            classification = "accepted"
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
        classification = "solver_failure"
        if "build" in locals():
            stats = build.prob.solver_stats
    payload = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classification": classification,
        "exception": exception,
        "execution_context": context,
        "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "horizon_steps": horizon,
        "temporal_assembly": "vectorized",
        "canonicalization_backend": "SCIPY",
        "solver": solver,
        "solver_configuration": "defaults",
        "solver_options": {},
        "build_seconds": build_seconds,
        "solve_seconds": solve_seconds,
        "audit_seconds": audit_seconds,
        "worker_seconds": time.perf_counter() - started,
        "solver_stats": {
            "name": None if stats is None else stats.solver_name,
            "iterations": None if stats is None else stats.num_iters,
            "setup_time_seconds": None if stats is None else stats.setup_time,
            "solve_time_seconds": None if stats is None else stats.solve_time,
            "extra": None if stats is None else _extra_stats(stats.extra_stats),
        },
        "result_status": result.get("status"),
        "result_schema": {
            name: list(value.shape)
            for name, value in sorted(result.items())
            if isinstance(value, np.ndarray)
        },
        "result_array_sha256": _array_digest(result),
        "objective_accounting": accounting,
        "audit": None
        if audit is None
        else {
            "accepted_primal": audit.accepted_primal,
            "status": audit.status,
            "residuals": dict(audit.residuals),
            "missing_or_nonfinite_fields": list(audit.missing_or_nonfinite_fields),
            "identity_error": audit.identity_error,
        },
        "bounds_audit": bounds,
        "resource_samples": samples,
    }
    atomic_immutable_json(directory / "arm-result.json", _json(payload))
    return 0 if classification == "accepted" else 1


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


def _supervise(solver: str, horizon: int, directory: Path) -> Mapping[str, object]:
    directory.mkdir()
    log_path = directory / "worker.log"
    command = [
        sys.executable,
        "-m",
        "experiments.case118_annual_hierarchy.results.run_s4_solver_matrix",
        "--worker",
        "--solver",
        solver,
        "--horizon",
        str(horizon),
        "--directory",
        str(directory),
    ]
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
                    if rss > RSS_LIMIT_MIB and "rss_limit" not in triggers:
                        triggers.append("rss_limit")
                if (
                    time.perf_counter() - started > WALL_LIMIT_SECONDS
                    and "wall_limit" not in triggers
                ):
                    triggers.append("wall_limit")
                if triggers:
                    _terminate(process)
                    break
                time.sleep(POLL_SECONDS)
            returncode = process.wait()
        except BaseException:
            if process.poll() is None:
                _terminate(process)
            raise
    arm_path = directory / "arm-result.json"
    arm = json.loads(arm_path.read_text()) if arm_path.is_file() else None
    worker_classification = (
        arm.get("classification") if isinstance(arm, Mapping) else None
    )
    if triggers:
        classification = triggers[0]
    elif returncode == 0 and worker_classification == "accepted":
        classification = "accepted"
    elif worker_classification is not None:
        classification = str(worker_classification)
    else:
        classification = "worker_process_failure"
    supervision = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "solver": solver,
        "horizon_steps": horizon,
        "classification": classification,
        "returncode": returncode,
        "resource_triggers": triggers,
        "first_sampled_rss_mib": first_rss,
        "peak_sampled_rss_mib": peak_rss,
        "final_sampled_rss_mib": final_rss,
        "supervisor_wall_seconds": time.perf_counter() - started,
        "arm_result_sha256": sha256_path(arm_path) if arm_path.is_file() else None,
        "worker_log_sha256": sha256_path(log_path),
        "worker_result": arm,
    }
    atomic_immutable_json(directory / "supervision.json", _json(supervision))
    return supervision


def _reference_solvers() -> Mapping[str, object]:
    prefix = json.loads(PREFIX_RESULTS.read_text())
    clarabel = {
        str(item["horizon_steps"]): {
            "classification": item["classification"],
            "solve_seconds": item["solve_wall_seconds"],
            "supervisor_wall_seconds": item["supervisor_wall_seconds"],
            "peak_rss_mib": item["peak_supervisor_rss_mib"],
            "objective": item["objective"],
            "conditioned_input_match": True,
        }
        for item in prefix["prefixes"]
    }
    annual = analyze_s4(S4_OUTPUT_DIRECTORY)
    clarabel["8760"] = {
        "classification": annual["classification"],
        "solve_seconds": annual["solve_wall_seconds"],
        "supervisor_wall_seconds": annual["supervisor_wall_seconds"],
        "peak_rss_mib": annual["peak_supervisor_rss_mib"],
        "objective": annual["objective"],
        "conditioned_input_match": True,
    }
    mosek: dict[str, object] = {}
    for horizon in (24, 168, 720):
        path = MOSEK_PREFIX_ROOT / f"{horizon:04d}-vectorized-default/arm-result.json"
        item = json.loads(path.read_text())
        mosek[str(horizon)] = {
            "classification": item["classification"],
            "solve_seconds": item["solve_seconds"],
            "mosek_options": item["mosek_options"],
            "objective": item["objective_accounting"]["objective"],
            "conditioned_input_match": False,
            "qualification": "historical unconditioned input",
        }
    mosek_root = json.loads((MOSEK_ANNUAL_ROOT / "comparison-result.json").read_text())
    mosek_arm = json.loads((MOSEK_ANNUAL_ROOT / "arm-result.json").read_text())
    mosek["8760"] = {
        "classification": mosek_root["classification"],
        "solve_seconds": mosek_arm["solve_seconds"],
        "supervisor_wall_seconds": mosek_root["supervisor_wall_seconds"],
        "peak_rss_mib": mosek_root["peak_sampled_rss_mib"],
        "conditioned_input_match": True,
        "qualification": "native MOSEK UNKNOWN; no usable primal",
    }
    return {"CLARABEL": clarabel, "MOSEK": mosek}


def _parent() -> int:
    if _git("status", "--porcelain") != "":
        raise ValueError("solver matrix requires a clean tracked worktree")
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    context = execution_context()
    references = _reference_solvers()
    OUTPUT.mkdir(parents=True)
    protocol = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "non_promotional": True,
        "solvers": list(SOLVERS),
        "horizons": list(HORIZONS),
        "order": "horizon_then_solver",
        "temporal_assembly": "vectorized",
        "canonicalization_backend": "SCIPY",
        "solver_configuration": "defaults",
        "verbose": True,
        "rss_limit_mib": RSS_LIMIT_MIB,
        "wall_limit_seconds_per_arm": WALL_LIMIT_SECONDS,
        "automatic_retry": False,
        "execution_context": context,
        "script_sha256": hashlib.sha256(SCRIPT.read_bytes()).hexdigest(),
    }
    atomic_immutable_json(OUTPUT / "protocol.json", protocol)
    atomic_immutable_json(OUTPUT / "reference-solvers.json", references)
    records: list[Mapping[str, object]] = []
    for horizon in HORIZONS:
        for solver in SOLVERS:
            directory = OUTPUT / f"{horizon:04d}-{solver.lower()}"
            record = _supervise(solver, horizon, directory)
            records.append(
                {
                    "solver": solver,
                    "horizon_steps": horizon,
                    "classification": record["classification"],
                    "supervisor_wall_seconds": record["supervisor_wall_seconds"],
                    "peak_sampled_rss_mib": record["peak_sampled_rss_mib"],
                    "supervision_sha256": sha256_path(
                        directory / "supervision.json"
                    ),
                }
            )
            atomic_json(OUTPUT / "progress.json", {"records": records})
    result = {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "classification": "complete",
        "non_promotional": True,
        "protocol_sha256": sha256_path(OUTPUT / "protocol.json"),
        "reference_solvers_sha256": sha256_path(
            OUTPUT / "reference-solvers.json"
        ),
        "records": records,
    }
    atomic_immutable_json(OUTPUT / "matrix-result.json", result)
    print(json.dumps(result, indent=2))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--solver", choices=SOLVERS)
    parser.add_argument("--horizon", type=int, choices=HORIZONS)
    parser.add_argument("--directory", type=Path, default=OUTPUT)
    arguments = parser.parse_args()
    if arguments.worker:
        if arguments.solver is None or arguments.horizon is None:
            parser.error("worker requires solver and horizon")
        raise SystemExit(
            _worker(
                arguments.directory.resolve(), arguments.solver, arguments.horizon
            )
        )
    raise SystemExit(_parent())


if __name__ == "__main__":
    main()
