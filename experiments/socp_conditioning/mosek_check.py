"""One bounded, default-tolerance MOSEK check; isolated experiment only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import patch

import numpy as np

from cvxopf import audit_socp_relaxation, extract_results
from experiments.socp_conditioning import diagnostic as d


def context():
    import mosek

    return dict(
        **d.context(),
        mosek_version=mosek.Env.getversion(),
        runner_sha256=d.sha(__file__),
        interpreter=sys.executable,
    )


def capture_task(task, inverse):
    """Capture the dualized task before CVXPY closes it during inversion."""
    import mosek
    from cvxpy import settings

    sol = mosek.soltype.itr
    return dict(
        problem_status=str(task.getprosta(sol)),
        solution_status=str(task.getsolsta(sol)),
        task_objective_sense=str(task.getobjsense()),
        dualized=inverse["dualized"],
        objective_offset=float(inverse[settings.OBJ_OFFSET]),
        task_primal_objective=task.getprimalobj(sol),
        task_dual_objective=task.getdualobj(sol),
        task_x=list(task.getxx(sol)),
        task_y=list(task.gety(sol)),
        iterations=task.getintinf(mosek.iinfitem.intpnt_iter),
        optimizer_seconds=task.getdouinf(mosek.dinfitem.optimizer_time),
        native_feasibility={
            key: task.getdouinf(getattr(mosek.dinfitem, key))
            for key in ("intpnt_primal_feas", "intpnt_dual_feas")
        },
        default_tolerances={
            key: task.getdouparam(getattr(mosek.dparam, key))
            for key in dir(mosek.dparam)
            if key.startswith("intpnt_co_tol_")
        },
        threads=task.getintparam(mosek.iparam.num_threads),
    )


def worker(directory, reference):
    from cvxpy.reductions.solvers.conic_solvers.mosek_conif import MOSEK

    started = time.monotonic()
    record = dict(schema=1, exception=None, audit=None, result=None, timings={})
    native = {}
    original_invert = MOSEK.invert

    def invert(self, output, inverse):
        if "task" in output:
            native.update(capture_task(output["task"], inverse))
        return original_invert(self, output, inverse)

    try:
        record["context"] = context()
        kwargs, record["historical_reference"] = d.load_case("surplus", reference)
        record["input_sha256"] = d.input_digest(kwargs)
        began = time.monotonic()
        build, original, divisor = d.build_variant(kwargs, "cones")
        assert divisor == 1.0
        record["timings"]["construction_seconds"] = time.monotonic() - began
        record["solver_options"] = {"MSK_IPAR_NUM_THREADS": 1}
        began = time.monotonic()
        try:
            with patch.object(MOSEK, "invert", invert):
                build.solve(
                    solver="MOSEK",
                    verbose=True,
                    warm_start=False,
                    mosek_params=record["solver_options"],
                    save_file=str(directory / "task.ptf.gz"),
                )
        except Exception as exc:
            record["exception"] = f"{type(exc).__name__}: {exc}"
        record["timings"]["canonicalization_and_solve_seconds"] = (
            time.monotonic() - began
        )
        record["native"] = native
        record["status"] = build.prob.status
        record["cvxpy_objective"] = build.prob.value
        result = extract_results(build)
        record["result"] = result
        usable = original.value is not None and all(
            v.value is not None and np.isfinite(v.value).all()
            for v in build.prob.variables()
        )
        if usable:
            record["original_objective"] = float(original.value)
            named = {
                k: float(v.value)
                for k, v in build.expressions.items()
                if k.endswith("_cost")
            }
            record["named_costs"] = named
            record["audit"] = d.original_audit("surplus", build, result, kwargs, named)
            record["relaxation_audit"] = audit_socp_relaxation(
                build, result, tolerances=d.e3.RELAXATION_TOLERANCES
            )
            record["model_values"] = {v.name(): v.value for v in build.prob.variables()}
            # MOSEK solves the dualized maximization task. Its dual objective
            # corresponds to the original canonical minimization primal cost.
            record["canonical_primal_original_units"] = (
                native["task_dual_objective"] + native["objective_offset"]
            )
            record["canonical_dual_original_units"] = (
                native["task_primal_objective"] + native["objective_offset"]
            )
            record["native_vs_original_objective"] = abs(
                record["canonical_primal_original_units"] - float(original.value)
            )
        record["context_after"] = context()
        if record["context_after"] != record["context"]:
            raise ValueError("source/environment changed during diagnostic")
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["accepted"] = bool(
        record["exception"] is None
        and native.get("solution_status") == "solsta.optimal"
        and (record["audit"] or {}).get("passed") is True
        and record.get("native_vs_original_objective", float("inf"))
        <= 1e-4 + 1e-8 * abs(record.get("original_objective", 0))
    )
    record["worker_elapsed_before_archive_seconds"] = time.monotonic() - started
    task = directory / "task.ptf.gz"
    record["task_sha256"] = d.sha(task) if task.exists() else None
    d.publish(directory / "arm.json.gz", d.e3.encode(record))
    d.publish(
        directory / "completion.json",
        dict(
            accepted=record["accepted"],
            arm_sha256=d.sha(directory / "arm.json.gz"),
            worker_wall_seconds=time.monotonic() - started,
        ),
    )


def supervise(
    directory,
    reference,
    *,
    worker_module=None,
    context_factory=context,
    worker_arguments=(),
):
    directory.mkdir(parents=True, exist_ok=False)
    d.publish(directory / "context.json", context_factory())
    process = None
    started, peak = time.monotonic(), 0.0
    classification, error = "completed", None
    try:
        with (directory / "worker.log").open("xb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    worker_module or __spec__.name,
                    "--worker",
                    "--output",
                    str(directory),
                    "--reference",
                    str(reference),
                    *worker_arguments,
                ],
                cwd=d.ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            while process.poll() is None:
                try:
                    peak = max(peak, d.rss(process.pid))
                except subprocess.CalledProcessError:
                    if process.poll() is None:
                        raise
                if peak > d.RSS_MIB or time.monotonic() - started > d.WALL_SECONDS:
                    classification = "resource_limit"
                    d.stop(process)
                    break
                time.sleep(0.5)
            process.wait()
            if process.returncode and classification == "completed":
                classification = "worker_failure"
    except BaseException as exc:
        classification = "supervisor_failure"
        error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        d.stop(process)
        evidence = dict(
            classification=classification,
            exception=error,
            returncode=None if process is None else process.returncode,
            wall_seconds=time.monotonic() - started,
            peak_rss_mib=peak,
            limits=dict(wall_seconds=d.WALL_SECONDS, rss_mib=d.RSS_MIB),
            artifacts={p.name: d.sha(p) for p in directory.iterdir() if p.is_file()},
        )
        d.publish(directory / "supervision.json", evidence)
        print(json.dumps(evidence, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    (worker if args.worker else supervise)(
        args.output.resolve(), args.reference.resolve()
    )


if __name__ == "__main__":
    main()
