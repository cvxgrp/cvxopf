"""Two serial native replays: control then observational cone/KKT tracing.

Identical CLARABEL 0.11.1 problem and min-step=1e-8 settings. Both run through
the existing OPFBuild.solve interface hook. Native build and patch are isolated;
no installed solver replacement. Require exact control/wheel vectors before
running the traced arm. Limits remain one worker/thread, 180 seconds, 4096 MiB.
"""

import argparse
from contextlib import contextmanager
import gzip
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import patch

import clarabel
import numpy as np
from scipy import sparse
from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import (
    CLARABEL,
)

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import minimum_step as m
from experiments.socp_conditioning import mosek_check as supervision
from experiments.socp_conditioning import termination_probe as t
from experiments.socp_conditioning import termination_analysis as a

ENGINE = Path("/tmp/cvxopf-clarabel-trace.qX1JkJ/Clarabel.rs")
BINARY = ENGINE / "target/release/examples/trace_probe"
BASE_CONTEXT = t.context
BASELINE = d.HERE / "results/minimum_step_001/clarabel"
BASELINE_SHA = "9dc63831645db3a65eeb0fa77faa1b2dc3bd4cd9fb1ea3eb21b99c897b9972ff"


def engine_record():
    return dict(
        source_commit=subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ENGINE, text=True
        ).strip(),
        source_patch=subprocess.check_output(["git", "diff"], cwd=ENGINE, text=True),
        entrypoint=(ENGINE / "examples/rust/trace_probe.rs").read_text(),
        cargo_lock=(ENGINE / "Cargo.lock").read_text(),
        binary_sha256=d.sha(BINARY),
        rust_version="rustc 1.90.0 (1159e78c4 2025-09-14)",
        build_command="cargo build --release --example trace_probe --jobs 1 --locked",
    )


def context():
    if d.sha(BASELINE / "arm.json.gz") != BASELINE_SHA:
        raise ValueError("baseline changed")
    base = BASE_CONTEXT()
    evidence = engine_record()
    return base | dict(
        experiment="native_cone_step_and_refinement_trace",
        baseline_sha256=BASELINE_SHA,
        native_engine_sha256=hashlib.sha256(
            json.dumps(evidence, sort_keys=True).encode()
        ).hexdigest(),
        additional_sources=base["additional_sources"]
        | {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (__file__, m.__file__, a.__file__)
        },
    )


def matrix_json(matrix):
    matrix = sparse.csc_matrix(matrix)
    return dict(
        m=matrix.shape[0],
        n=matrix.shape[1],
        colptr=matrix.indptr.tolist(),
        rowval=matrix.indices.tolist(),
        nzval=matrix.data.tolist(),
    )


def native_input(directory, data, verbose, opts):
    # Serialize exact supplied matrices, before any native presolve/equilibration.
    # Rust's tagged JSON reader restores MAX time_limit to infinity.
    settings = CLARABEL.parse_solver_opts(verbose, opts.copy())
    native_settings = {
        k: getattr(settings, k)
        for k in dir(settings)
        if not k.startswith("_") and not callable(getattr(settings, k))
    }
    if np.isinf(native_settings["time_limit"]):
        native_settings["time_limit"] = np.finfo(float).max
    dims = data["dims"]
    if dims.exp or dims.psd or dims.p3d:
        raise ValueError("diagnostic requires only zero, nonnegative and SOC cones")
    cones = []
    if dims.zero:
        cones.append({"ZeroConeT": dims.zero})
    if dims.nonneg:
        cones.append({"NonnegativeConeT": dims.nonneg})
    cones.extend({"SecondOrderConeT": size} for size in dims.soc)
    payload = dict(
        P=matrix_json(sparse.triu(data["P"]).tocsc()),
        A=matrix_json(data["A"]),
        q=data["c"].tolist(),
        b=data["b"].tolist(),
        cones=cones,
        settings=native_settings,
    )
    d.publish(directory / "native_input.json", payload)
    return payload


@contextmanager
def instrumentation(solver, directory, record, *, mode):
    if solver != "CLARABEL":
        raise ValueError("native tracing is CLARABEL only")

    def interface(self, data, warm_start, verbose, solver_opts, solver_cache=None):
        if warm_start or record["optimizer_calls"]:
            raise ValueError("one fresh solve required")
        native_input(directory, data, verbose, solver_opts)
        env = os.environ.copy()
        env.pop("CVXOPF_TRACE", None)
        if mode == "instrumented":
            env["CVXOPF_TRACE"] = "1"
        proc = None
        began, peak = time.monotonic(), 0.0
        try:
            # Same process group as supervised worker: parent group cleanup also
            # reaches the native child. Child RSS is sampled explicitly here.
            proc = subprocess.Popen(
                [
                    str(BINARY),
                    str(directory / "native_input.json"),
                    str(directory / "native_output.json"),
                ],
                env=env,
            )
            record["optimizer_calls"] += 1
            while proc.poll() is None:
                try:
                    peak = max(peak, d.rss(proc.pid) + d.rss(os.getpid()))
                except subprocess.CalledProcessError:
                    if proc.poll() is None:
                        raise
                if peak > d.RSS_MIB or time.monotonic() - began > d.WALL_SECONDS:
                    raise TimeoutError("native replay resource boundary")
                time.sleep(0.1)
            if proc.returncode:
                raise RuntimeError(f"native process exited {proc.returncode}")
        finally:
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            record["native_combined_peak_rss_mib"] = peak
        output = json.loads((directory / "native_output.json").read_text())
        record["raw_clarabel"] = output["solution"]
        record["native"], record["trace"] = output["native"], output["trace"]
        record["native_mode"] = mode
        record["native_input_sha256"] = d.sha(directory / "native_input.json")
        record["native_output_sha256"] = d.sha(directory / "native_output.json")
        raw = output["solution"]
        return SimpleNamespace(
            **(raw | dict(status=getattr(clarabel.SolverStatus, raw["status"])))
        )

    with patch.object(CLARABEL, "solve_via_data", interface):
        yield


def same_solution(a0, b0):
    return all(np.array_equal(a0[k], b0[k]) for k in ("x", "s", "z"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=("control", "instrumented"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    output, reference = args.output.resolve(), args.reference.resolve()
    with (
        patch.object(t, "context", context),
        patch.object(t, "options", m.options),
        patch.object(t, "SOLVERS", ("CLARABEL",)),
    ):
        if args.worker:

            def instrument(solver, directory, record):
                return instrumentation(solver, directory, record, mode=args.mode)

            with patch.object(t, "instrumentation", instrument):
                t.worker(output, reference, "CLARABEL")
            return
        output.mkdir(parents=True, exist_ok=False)
        d.publish(output / "binding.json", context())
        d.publish(output / "engine.json", engine_record())
        prior = json.load(gzip.open(BASELINE / "arm.json.gz", "rt"))
        arms = []
        for mode in ("control", "instrumented"):
            folder = output / mode
            supervision.supervise(
                folder,
                reference,
                worker_module=__spec__.name,
                context_factory=context,
                worker_arguments=("--mode", mode),
            )
            sup = json.loads((folder / "supervision.json").read_text())
            if sup["classification"] != "completed":
                raise RuntimeError("stop after native process/resource failure")
            r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
            if (
                r["exception"]
                or "raw_clarabel" not in r
                or r["context"] != context()
                or r["context_after"] != context()
            ):
                raise ValueError(
                    f"worker reconstruction failure: {r['exception']}; {r['solver_exception']}"
                )
            exact = same_solution(prior["raw_clarabel"], r["raw_clarabel"])
            arms.append(
                dict(
                    mode=mode,
                    exact_baseline_vectors=exact,
                    native=r["native"],
                    checks=a.numerical_checks(r["audit"]),
                    arm_sha256=d.sha(folder / "arm.json.gz"),
                    supervision_sha256=d.sha(folder / "supervision.json"),
                )
            )
            d.publish(output / (mode + "-comparison.json"), arms[-1])
            if not exact:
                raise RuntimeError(
                    "native rebuild does not exactly reproduce wheel; stop before attributing diagnostics"
                )
        d.publish(
            output / "summary.json",
            dict(arms=arms, promotional=False, context=context()),
        )
        print(json.dumps(arms, indent=2))


if __name__ == "__main__":
    main()
