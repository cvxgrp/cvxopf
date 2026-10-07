"""Control plus one native QDLDL five-pass KKT-scaling trial, serial and bounded.

Same frozen fixed-load surplus problem and optimizer settings as native tracing.
At every refactor, apply the original static shift, then five symmetric Ruiz
passes. Solve D(K+E)D y=D rhs, map x=D y; refine against original unshifted K.
QDLDL dynamic pivot thresholds stay unchanged but act in scaled coordinates.
No installed solver replacement, tolerance relaxation, or production change.
"""

import argparse
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import signal
from unittest.mock import patch

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import minimum_step as m
from experiments.socp_conditioning import mosek_check as supervision
from experiments.socp_conditioning import native_step_probe as n
from experiments.socp_conditioning import native_step_analysis as trace_analysis
from experiments.socp_conditioning import termination_probe as t
from experiments.socp_conditioning import termination_analysis as a

INPUT_SHA = "b0ace5aeeeff35224f2b99d57847aba62eae9e019c7e2e4cf5630c7fc14d1184"
MODES = ("control", "five_pass")


def context():
    base = n.context()
    return base | dict(
        experiment="native_QDLDL_five_pass_KKT_scaling",
        native_input_sha256=INPUT_SHA,
        additional_sources=base["additional_sources"]
        | {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (__file__, n.__file__, trace_analysis.__file__)
        },
        policy=dict(
            passes=5,
            norm="symmetric infinity row maximum",
            reset_at_every_refactor=True,
            per_pass_clip=[1e-8, 1e8],
            cumulative_clip=[1e-16, 1e16],
            static_shift="original native shift before congruence",
            dynamic_shift="unchanged QDLDL thresholds in scaled coordinates",
            refinement="unchanged native rule, original unregularized KKT residual",
            linear_solver="qdldl",
            modes=list(MODES),
        ),
    )


@contextmanager
def native_environment(mode):
    if mode not in MODES:
        raise ValueError("unknown scaling arm")
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CVXOPF_TRACE", "CVXOPF_KKT_DIR", "CVXOPF_KKT_SCALING")
    }
    if mode == "five_pass":
        env["CVXOPF_KKT_SCALING"] = "5"
    with patch.dict(os.environ, env, clear=True):
        yield


def validate_arm(record, binding, prior, mode):
    if (
        record["exception"] is not None
        or record["context"] != binding
        or record["context_after"] != binding
        or record["optimizer_calls"] != 1
        or record.get("native_input_sha256") != INPUT_SHA
        or "raw_clarabel" not in record
    ):
        raise ValueError("replay identity or reconstruction failure")
    exact = n.same_solution(prior["raw_clarabel"], record["raw_clarabel"])
    if mode == "control" and not exact:
        raise ValueError("disabled control differs; do not run enabled arm")
    return exact


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=MODES)
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
                return n.instrumentation(solver, directory, record, mode="instrumented")

            with (
                native_environment(args.mode),
                patch.object(t, "instrumentation", instrument),
            ):
                t.worker(output, reference, "CLARABEL")
            return
        binding = context()
        output.mkdir(parents=True, exist_ok=False)
        d.publish(output / "binding.json", binding)
        d.publish(output / "engine.json", n.engine_record())
        prior = json.load(gzip.open(n.BASELINE / "arm.json.gz", "rt"))
        arms = []
        for mode in MODES:
            folder = output / mode
            supervision.supervise(
                folder,
                reference,
                worker_module=__spec__.name,
                context_factory=context,
                worker_arguments=("--mode", mode),
            )
            sup = json.loads((folder / "supervision.json").read_text())
            if sup["classification"] != "completed" or sup["returncode"] != 0:
                raise RuntimeError("stop after resource/process failure")
            for name, digest in sup["artifacts"].items():
                if d.sha(folder / name) != digest:
                    raise ValueError("supervised artifact changed")
            r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
            exact = validate_arm(r, binding, prior, mode)
            log = (folder / "worker.log").read_text()
            solves, steps, _, _ = trace_analysis.parse_trace(log)
            scaling_lines = [
                line for line in log.splitlines() if line.startswith("TRACE SCALING")
            ]
            if bool(scaling_lines) != (mode == "five_pass"):
                raise ValueError("native scaling activation evidence mismatch")
            arm = dict(
                mode=mode,
                exact_baseline_vectors=exact,
                native=r["native"],
                accepted=r["accepted"],
                solver_exception=r["solver_exception"],
                checks=a.numerical_checks(r["audit"]) if r["audit"] else None,
                physical_residuals=r["audit"]["residuals"] if r["audit"] else None,
                canonical_objective=r.get("canonical_objective"),
                objective_reconstruction_error=r.get("objective_reconstruction_error"),
                common_kkt=r.get("common_kkt"),
                worker_seconds=r["worker_seconds"],
                native_combined_peak_rss_mib=r["native_combined_peak_rss_mib"],
                scaling_refactors=len(scaling_lines),
                scaling_history=scaling_lines,
                linear_solves=len(solves),
                linear_solves_missing_tolerance=sum(
                    not s["meets_tolerance"] for s in solves
                ),
                final_linear_solves=solves[-3:],
                final_steps=steps[-2:],
                arm_sha256=d.sha(folder / "arm.json.gz"),
                supervision_sha256=d.sha(folder / "supervision.json"),
            )
            arms.append(arm)
            d.publish(output / f"{mode}-comparison.json", arm)
            print(
                json.dumps(
                    {k: arm[k] for k in ("mode", "native", "accepted", "checks")}
                ),
                flush=True,
            )
        if context() != binding:
            raise ValueError("study context changed")
        d.publish(
            output / "summary.json", dict(context=binding, arms=arms, promotional=False)
        )


if __name__ == "__main__":
    main()
