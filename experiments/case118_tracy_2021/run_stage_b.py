"""Sequential fresh-process Stage B runner. No retries or implicit resume.

Run only after review/commit, explicitly naming the approved execution commit.
The analyzer can reconstruct retained accepted results without a numerical solve.
"""

import argparse
from datetime import datetime, timezone
import gzip
from importlib.metadata import version
import json
import os
from pathlib import Path
import platform
import signal
import subprocess
import sys
import time

import cvxpy as cp
import clarabel
import numpy as np

from cvxopf import build_opf_multistep, extract_results
from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib, _terminate
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_gzip_json,
    atomic_immutable_json,
    atomic_json,
)
from .prepare import HERE, ROOT, SOURCE, digest
from .stage_b import (
    Arm,
    LIMITS,
    SOLVER_OPTIONS,
    audit_result,
    inputs_for_arm,
    study_spec,
    verified_inputs,
)

MODULE = "experiments.case118_tracy_2021.run_stage_b"
OUTPUT = HERE / "results/stage_b"


def jsonable(value):
    if isinstance(value, np.ndarray):
        return jsonable(value.tolist())
    if isinstance(value, np.generic):
        return jsonable(value.item())
    if isinstance(value, dict):
        return {k: jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def context():
    return dict(
        commit=git("rev-parse", "HEAD"),
        clean=not git("status", "--porcelain"),
        python=sys.version,
        executable=sys.executable,
        platform=platform.platform(),
        packages={
            n: version(n) for n in ("cvxpy", "clarabel", "numpy", "scipy", "pandas")
        },
        stage_a_manifest_sha256=digest(HERE / "stage_a/manifest.json"),
        source_sha256=digest(SOURCE),
        protocol_sha256=digest(HERE / "STAGE_B_PROTOCOL.md"),
        clarabel_default_settings=str(clarabel.DefaultSettings()),
        thread_environment={
            k: os.environ.get(k)
            for k in (
                "OMP_NUM_THREADS",
                "OPENBLAS_NUM_THREADS",
                "MKL_NUM_THREADS",
                "VECLIB_MAXIMUM_THREADS",
            )
        },
    )


def verify_context(expected):
    current = context()
    if not current["clean"] or current != expected:
        raise ValueError("execution context changed or working tree is not clean")


def convergence_diagnostics(build):
    """Read the installed CVXPY/CLARABEL cache without masking solve failures.

    CVXPY does not expose these native fields in SolverStats. Missing native
    state (for example, a failure before solver construction) is explicit.
    This diagnostic does not change the scientific acceptance gate.
    """
    diagnostics = dict(native_info=None, effective_native_settings=None)
    try:
        solver = build.prob._solver_cache.get("CLARABEL")
        if solver is not None:
            info = solver.get_info()
            diagnostics["native_info"] = {
                name: getattr(info, name)
                for name in (
                    "cost_primal",
                    "cost_dual",
                    "gap_abs",
                    "gap_rel",
                    "res_primal",
                    "res_dual",
                    "iterations",
                    "solve_time",
                )
            }
            diagnostics["native_info"]["status"] = str(info.status)
            diagnostics["effective_native_settings"] = str(solver.get_settings())
    except Exception as exc:
        diagnostics["diagnostic_exception"] = f"{type(exc).__name__}: {exc}"
    return diagnostics


def worker(directory: Path, number: int) -> int:
    started = time.monotonic()
    manifest = json.loads((directory / "binding.json").read_text())
    arm_dir = directory / f"arm-{number:03d}"
    timings = {}
    payload = dict(
        iteration=number,
        arm=manifest["study"]["arms"][number],
        classification="exception",
        result=None,
        audit=None,
        timings=timings,
    )
    phase_started = started
    phase_history = []

    def phase(name):
        nonlocal phase_started
        now = time.monotonic()
        atomic_json(
            arm_dir / "phase.json",
            dict(
                phase=name,
                utc=datetime.now(timezone.utc).isoformat(),
                worker_elapsed_seconds=now - started,
            ),
        )
        phase_started = now
        phase_history.append(dict(phase=name, elapsed_seconds=now - started))

    build = None
    try:
        if manifest["study"] != study_spec():
            raise ValueError("binding study specification differs")
        verify_context(manifest["context"])
        phase("prepare")
        p = verified_inputs()
        kwargs = inputs_for_arm(p, Arm(**payload["arm"]))
        timings["preparation_seconds"] = time.monotonic() - phase_started
        phase("construct")
        build = build_opf_multistep(**kwargs)
        timings["construction_seconds"] = time.monotonic() - phase_started
        phase("solve")
        try:
            build.solve(
                solver=cp.CLARABEL,
                canon_backend=cp.SCIPY_CANON_BACKEND,
                warm_start=False,
                verbose=True,
                **SOLVER_OPTIONS,
            )
        finally:
            timings["canonicalization_and_solve_seconds"] = (
                time.monotonic() - phase_started
            )
            payload["convergence_diagnostics"] = convergence_diagnostics(build)
        phase("extract_and_audit")
        result = extract_results(build)
        payload["renewable_available_mw"] = kwargs["df_nd"].to_numpy()
        payload["boundary_soc_mwh"] = (
            np.vstack(([s.initial_soc for s in kwargs["storage"]], result["soc"]))
            if result["soc"] is not None
            else None
        )
        named = {
            k: float(expr.value) if expr.value is not None else None
            for k, expr in build.expressions.items()
            if k.endswith("_cost")
        }
        payload.update(
            result=result, named_costs=named, audit=audit_result(result, kwargs, named)
        )
        stats = build.prob.solver_stats
        payload["solver_stats"] = dict(
            solver_name=stats.solver_name,
            num_iters=stats.num_iters,
            solve_time=stats.solve_time,
            setup_time=stats.setup_time,
            compilation_time=build.prob.compilation_time,
        )
        payload["identities"] = dict(
            storage=[s.device_id for s in kwargs["storage"]],
            loads=[s.device_id for s in kwargs["loads"]],
            renewables=[s.device_id for s in kwargs["nondispatchable"]],
            generator_source_rows=list(range(len(kwargs["generators"]))),
        )
        timings["extraction_audit_seconds"] = time.monotonic() - phase_started
        payload["classification"] = (
            "accepted" if payload["audit"]["passed"] else "rejected"
        )
        verify_context(manifest["context"])
    except Exception as exc:
        payload["classification"] = "exception"
        payload["exception"] = f"{type(exc).__name__}: {exc}"
        if build is not None and payload["result"] is None:
            try:
                payload["result"] = extract_results(build)
            except Exception as extraction:
                payload["extraction_exception"] = str(extraction)
    phase("archive")
    payload["phase_history"] = phase_history
    atomic_gzip_json(arm_dir / "result.json.gz", jsonable(payload))
    archive_seconds = time.monotonic() - phase_started
    atomic_immutable_json(
        arm_dir / "completion.json",
        dict(
            classification=payload["classification"],
            archive_seconds=archive_seconds,
            worker_seconds=time.monotonic() - started,
            result_sha256=digest(arm_dir / "result.json.gz"),
        ),
    )
    return 0 if payload["classification"] == "accepted" else 1


def supervise(command, directory: Path, limits=LIMITS) -> dict:
    """Bound entire child lifetime, including extraction and archive writing."""
    started = time.monotonic()
    record = dict(
        classification="supervisor_failure",
        started_utc=datetime.now(timezone.utc).isoformat(),
        peak_sampled_rss_mib=0.0,
        samples=0,
        returncode=None,
    )
    process = None
    try:
        with (directory / "worker.log").open("wb") as log:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with (directory / "resources.jsonl").open("w") as samples:
                while process.poll() is None:
                    elapsed = time.monotonic() - started
                    rss = _child_rss_mib(process.pid)
                    if rss is None:
                        if process.poll() is not None:
                            break
                        raise RuntimeError("RSS monitoring failed for live worker")
                    samples.write(
                        json.dumps(dict(elapsed_seconds=elapsed, rss_mib=rss)) + "\n"
                    )
                    samples.flush()
                    record["samples"] += 1
                    record["peak_sampled_rss_mib"] = max(
                        record["peak_sampled_rss_mib"], rss
                    )
                    if rss > limits["rss_mib"]:
                        record["classification"] = "rss_limit"
                        break
                    if elapsed > limits["wall_seconds"]:
                        record["classification"] = "wall_limit"
                        break
                    time.sleep(limits["poll_seconds"])
            if (
                process.poll() is not None
                and record["classification"] == "supervisor_failure"
            ):
                record["classification"] = "exited"
                if time.monotonic() - started > limits["wall_seconds"]:
                    record["classification"] = "wall_limit"
    except BaseException as exc:
        record["classification"] = (
            "interrupted"
            if isinstance(exc, KeyboardInterrupt)
            else "supervisor_failure"
        )
        record["exception"] = f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None:
            if process.poll() is None:
                _terminate(process)
            record["returncode"] = process.wait()
        record["wall_seconds"] = time.monotonic() - started
        record["ended_utc"] = datetime.now(timezone.utc).isoformat()
        atomic_immutable_json(directory / "supervision.json", record)
    return record


def reconstruct_arm(directory, number, manifest, p):
    arm_dir = directory / f"arm-{number:03d}"
    supervision = json.loads((arm_dir / "supervision.json").read_text())
    if supervision["classification"] != "exited" or supervision["returncode"] != 0:
        raise ValueError("worker not normally completed")
    limits = manifest["study"]["limits"]
    if (
        supervision["peak_sampled_rss_mib"] > limits["rss_mib"]
        or supervision["wall_seconds"] > limits["wall_seconds"]
        or supervision["samples"] < 1
    ):
        raise ValueError("resource evidence does not meet limits")
    completion = json.loads((arm_dir / "completion.json").read_text())
    path = arm_dir / "result.json.gz"
    if digest(path) != completion["result_sha256"]:
        raise ValueError("result archive hash mismatch")
    with gzip.open(path, "rt") as stream:
        payload = json.load(stream)
    spec = manifest["study"]["arms"][number]
    if payload["arm"] != spec or payload["iteration"] != number:
        raise ValueError("arm identity mismatch")
    if (
        completion["classification"] != "accepted"
        or payload["classification"] != "accepted"
    ):
        raise ValueError("worker rejected result")
    kwargs = inputs_for_arm(p, Arm(**spec))
    np.testing.assert_array_equal(
        payload["renewable_available_mw"], kwargs["df_nd"].to_numpy()
    )
    np.testing.assert_array_equal(
        payload["boundary_soc_mwh"],
        np.vstack(
            ([s.initial_soc for s in kwargs["storage"]], payload["result"]["soc"])
        ),
    )
    audit = audit_result(payload["result"], kwargs, payload["named_costs"])
    if not audit["passed"]:
        raise ValueError("independent reconstruction rejected result")
    return dict(
        number=number,
        arm=spec,
        audit=audit,
        supervision=supervision,
        solver_stats=payload["solver_stats"],
        timings=payload["timings"],
        completion=completion,
    )


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"received signal {signum}")


def run(directory: Path, commit: str) -> dict:
    ctx = context()
    if not ctx["clean"] or commit != ctx["commit"]:
        raise ValueError("requires clean tree and exact full approved execution commit")
    if directory.exists():
        raise FileExistsError("output exists; no overwrite or automatic resume")
    p = verified_inputs()
    if _child_rss_mib(os.getpid()) is None:
        raise RuntimeError("RSS preflight failed; fix permissions before launch")
    manifest = dict(
        context=ctx,
        study=study_spec(),
        bound_utc=datetime.now(timezone.utc).isoformat(),
        thermal_telemetry="external/contextual; not collected by this runner",
    )
    directory.mkdir(parents=True)
    atomic_immutable_json(directory / "binding.json", manifest)
    record = dict(
        classification="running", accepted=[], attempts=[], annual_execution=False
    )
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        for number in range(len(manifest["study"]["arms"])):
            verify_context(ctx)
            arm_dir = directory / f"arm-{number:03d}"
            arm_dir.mkdir()
            supervision = supervise(
                [
                    sys.executable,
                    "-m",
                    MODULE,
                    "--worker",
                    str(number),
                    "--output",
                    str(directory),
                ],
                arm_dir,
            )
            record["attempts"].append(dict(number=number, supervision=supervision))
            try:
                accepted = reconstruct_arm(directory, number, manifest, p)
            except Exception as exc:
                record.update(classification="stopped", reason=str(exc))
                break
            verify_context(ctx)
            record["accepted"].append(accepted)
            atomic_json(directory / "progress.json", record)
        else:
            record["classification"] = "complete"
    except BaseException as exc:
        record.update(classification="stopped", reason=f"{type(exc).__name__}: {exc}")
    finally:
        signal.signal(signal.SIGTERM, previous)
        atomic_json(directory / "progress.json", jsonable(record))
        atomic_immutable_json(directory / "study-result.json", jsonable(record))
    return record


def analyze(directory: Path) -> dict:
    manifest = json.loads((directory / "binding.json").read_text())
    if manifest["study"] != study_spec():
        raise ValueError("study specification differs from this analyzer")
    p = verified_inputs()
    current = context()
    for key in ("stage_a_manifest_sha256", "source_sha256"):
        if current[key] != manifest["context"][key]:
            raise ValueError("analyzer inputs differ from execution binding")
    accepted, stopped = [], None
    for number in range(len(manifest["study"]["arms"])):
        if not (directory / f"arm-{number:03d}").exists():
            break
        try:
            accepted.append(reconstruct_arm(directory, number, manifest, p))
        except Exception as exc:
            stopped = dict(number=number, reason=str(exc))
            break
    root_path = directory / "study-result.json"
    finalized = (
        root_path.exists()
        and json.loads(root_path.read_text())["classification"] == "complete"
    )
    return dict(
        execution_context=manifest["context"],
        analyzer_context=current,
        complete=len(accepted) == 72 and finalized,
        accepted=accepted,
        stopped=stopped,
        annual_execution=False,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument(
        "--commit", help="full reviewed execution commit; required to launch"
    )
    parser.add_argument("--worker", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--analyze", action="store_true")
    args = parser.parse_args()
    directory = args.output.resolve()
    if args.worker is not None:
        raise SystemExit(worker(directory, args.worker))
    if args.analyze:
        atomic_immutable_json(directory / "analysis.json", jsonable(analyze(directory)))
    elif args.commit:
        outcome = run(directory, args.commit)
        raise SystemExit(0 if outcome["classification"] == "complete" else 1)
    else:
        parser.error(
            "--commit is required to launch; use --analyze for retained results"
        )


if __name__ == "__main__":
    main()
