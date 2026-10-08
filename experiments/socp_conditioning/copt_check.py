"""Single default-tolerance COPT comparison on the retained surplus model."""

from __future__ import annotations

import argparse
from importlib.metadata import version
from pathlib import Path
import signal
import time
from unittest.mock import patch

import numpy as np

from cvxopf import audit_socp_relaxation, extract_results
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import mosek_check as supervision


def context():
    return dict(
        **d.context(),
        coptpy_version=version("coptpy"),
        runner_sha256=d.sha(__file__),
        supervisor_sha256=d.sha(supervision.__file__),
    )


def accepted(record):
    return bool(
        record["exception"] is None
        and record.get("native_optimal") is True
        and (record["audit"] or {}).get("passed") is True
        and record.get("native_vs_original_objective", float("inf"))
        <= 1e-4 + 1e-8 * abs(record.get("original_objective", 0))
    )


def portable_relaxation_audit(audit):
    """The COPT adapter returns a live Model as extra_stats, not numerical data."""
    statistics = audit.get("solver_statistics")
    if statistics is not None:
        statistics["extra_stats"] = {
            "native_model_handle_omitted": True,
            "evidence": "arm.native_solution and native_status; worker.log",
        }
    return audit


def worker(directory, reference):
    import coptpy as copt
    from cvxpy import settings as s
    from cvxpy.reductions.solvers.conic_solvers.copt_conif import COPT

    started = time.monotonic()
    record = dict(schema=1, exception=None, audit=None, result=None, timings={})
    original_invert = COPT.invert

    def invert(self, solution, inverse):
        model = solution["model"]
        # This SOCP interface preserves the primal orientation (no PSD block).
        record["native_solution"] = {k: v for k, v in solution.items() if k != "model"}
        record["native_status"] = model.status
        record["native_optimal"] = model.status == copt.COPT.OPTIMAL
        record["objective_offset"] = float(inverse[s.OFFSET])
        record["native_parameters"] = {
            k: model.getParam(k)
            for k in ("Threads", "FeasTol", "DualTol", "BarIterLimit")
        }
        if s.VALUE in solution:
            record["canonical_primal_original_units"] = (
                float(solution[s.VALUE]) + record["objective_offset"]
            )
        return original_invert(self, solution, inverse)

    try:
        record["context"] = context()
        kwargs, record["historical_reference"] = d.load_case("surplus", reference)
        record["input_sha256"] = d.input_digest(kwargs)
        began = time.monotonic()
        build, original, divisor = d.build_variant(kwargs, "cones")
        assert divisor == 1.0
        record["timings"]["construction_seconds"] = time.monotonic() - began
        record["solver_options"] = dict(Threads=1, reoptimize=False)
        began = time.monotonic()
        try:
            with patch.object(COPT, "invert", invert):
                build.solve(
                    solver="COPT",
                    verbose=True,
                    warm_start=False,
                    save_file=str(directory / "task.mps"),
                    **record["solver_options"],
                )
        except Exception as exc:
            record["exception"] = f"{type(exc).__name__}: {exc}"
        record["timings"]["canonicalization_and_solve_seconds"] = (
            time.monotonic() - began
        )
        record["status"] = build.prob.status
        record["cvxpy_objective"] = build.prob.value
        result = extract_results(build)
        record["result"] = result
        usable = (
            original.value is not None
            and np.isfinite(original.value)
            and all(
                v.value is not None and np.isfinite(v.value).all()
                for v in build.prob.variables()
            )
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
            record["relaxation_audit"] = portable_relaxation_audit(
                audit_socp_relaxation(
                    build, result, tolerances=d.e3.RELAXATION_TOLERANCES
                )
            )
            record["model_values"] = {v.name(): v.value for v in build.prob.variables()}
            record["native_vs_original_objective"] = abs(
                record["canonical_primal_original_units"] - float(original.value)
            )
        record["context_after"] = context()
        if record["context_after"] != record["context"]:
            raise ValueError("source/environment changed during diagnostic")
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["accepted"] = accepted(record)
    task = directory / "task.mps"
    record["task_sha256"] = d.sha(task) if task.exists() else None
    record["worker_elapsed_before_archive_seconds"] = time.monotonic() - started
    d.publish(directory / "arm.json.gz", d.e3.encode(record))
    d.publish(
        directory / "completion.json",
        dict(
            accepted=record["accepted"],
            arm_sha256=d.sha(directory / "arm.json.gz"),
            worker_wall_seconds=time.monotonic() - started,
        ),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    if args.worker:
        worker(args.output.resolve(), args.reference.resolve())
    else:
        supervision.supervise(
            args.output.resolve(),
            args.reference.resolve(),
            worker_module=__spec__.name,
            context_factory=context,
        )


if __name__ == "__main__":
    main()
