"""One exact QDLDL control and one serial native SuperLU-path optimization.

Frozen original static shift, native refinement and all optimization settings.
No external KKT scaling. SuperLU replaces QDLDL ordering, factorization and
dynamic pivot treatment; this is not a pivot-only ablation. Both arms are
bounded by 180 seconds and 4096 MiB including the persistent bridge process.
"""

import argparse
from contextlib import contextmanager
import gzip
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from unittest.mock import patch

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import lu_bridge as lu
from experiments.socp_conditioning import minimum_step as m
from experiments.socp_conditioning import mosek_check as supervision
from experiments.socp_conditioning import native_kkt_scaling as k
from experiments.socp_conditioning import native_step_probe as n
from experiments.socp_conditioning import native_step_analysis as trace_analysis
from experiments.socp_conditioning import termination_probe as t
from experiments.socp_conditioning import termination_analysis as a

MODES = ("control", "superlu")


def context():
    base = n.context()
    return base | dict(
        experiment="native_CLARABEL_pivoted_LU_path",
        native_input_sha256=k.INPUT_SHA,
        additional_sources=base["additional_sources"]
        | {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (
                __file__,
                d.HERE / "NATIVE_LU_PROTOCOL.md",
                lu.__file__,
                k.__file__,
                n.__file__,
                trace_analysis.__file__,
            )
        },
        policy=dict(
            modes=list(MODES),
            factor_options=lu.OPTIONS,
            static_shift="native original-coordinate shift unchanged",
            dynamic_shift="QDLDL-specific pivot treatment replaced by SuperLU",
            kkt_scaling=False,
            refinement="unchanged native original-KKT residual rule",
            bridge="persistent serial process in supervised worker process group",
            memory="Python worker plus native process and all native descendants",
        ),
    )


def process_rss(pid):
    """Avoid double counting: native subtree plus worker's own RSS are sampled."""
    rows = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss="], text=True)
    records = {
        int(p): (int(parent), int(rss))
        for p, parent, rss in (line.split() for line in rows.splitlines())
    }
    selected = {pid}
    if pid != os.getpid():
        while True:
            children = {p for p, (parent, _) in records.items() if parent in selected}
            if children <= selected:
                break
            selected |= children
    return sum(records[p][1] for p in selected if p in records) / 1024


@contextmanager
def environment(mode):
    if mode not in MODES:
        raise ValueError("unknown LU arm")
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in (
            "CVXOPF_TRACE",
            "CVXOPF_KKT_DIR",
            "CVXOPF_KKT_SCALING",
            "CVXOPF_LU_PYTHON",
        )
    }
    if mode == "superlu":
        env["CVXOPF_LU_PYTHON"] = sys.executable
    with patch.dict(os.environ, env, clear=True):
        yield


def bridge_summary(log):
    records = [
        json.loads(line.removeprefix("TRACE SUPERLU "))
        for line in log.splitlines()
        if line.startswith("TRACE SUPERLU ")
    ]
    factors = [r for r in records if r.get("event") == "factor"]
    solves = [r for r in records if r.get("event") == "solve"]
    elapsed = sum(
        float(v)
        for v in re.findall(
            r"TRACE LU_BRIDGE event=\w+ elapsed_seconds=([\deE.+-]+)", log
        )
    )
    numerical = sum(r["seconds"] for r in factors + solves)
    assembly = sum(r["assembly_seconds"] for r in factors)
    return dict(
        factors=len(factors),
        solves=len(solves),
        factor_seconds=sum(r["seconds"] for r in factors),
        triangular_solve_seconds=sum(r["seconds"] for r in solves),
        assembly_seconds=assembly,
        bridge_request_seconds=elapsed,
        serialization_transport_and_other_seconds=elapsed - numerical - assembly,
        errors=[r for r in records if not r.get("ok")],
        peak_factor_nnz=max((r["L_nnz"] + r["U_nnz"] for r in factors), default=0),
    )


@contextmanager
def execution_configuration():
    with (
        patch.object(t, "context", context),
        patch.object(t, "options", m.options),
        patch.object(t, "SOLVERS", ("CLARABEL",)),
    ):
        yield


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
    with execution_configuration():
        if args.worker:

            def instrument(solver, directory, record):
                return n.instrumentation(solver, directory, record, mode="instrumented")

            with (
                environment(args.mode),
                patch.object(t, "instrumentation", instrument),
                patch.object(d, "rss", process_rss),
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
                raise RuntimeError(
                    "stop after process/resource failure; retain partial root"
                )
            for name, digest in sup["artifacts"].items():
                if d.sha(folder / name) != digest:
                    raise ValueError("supervised artifact mismatch")
            r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
            exact = k.validate_arm(r, binding, prior, mode)
            log = (folder / "worker.log").read_text()
            bridge = bridge_summary(log)
            if "TRACE SCALING" in log or bool(bridge["factors"]) != (mode == "superlu"):
                raise ValueError("backend activation mismatch")
            solves, steps, _, _ = trace_analysis.parse_trace(log)
            arm = dict(
                mode=mode,
                exact_baseline_vectors=exact,
                native=r["native"],
                accepted=r["accepted"],
                solver_exception=r["solver_exception"],
                checks=a.numerical_checks(r["audit"]) if r["audit"] else None,
                physical_residuals=r["audit"]["residuals"] if r["audit"] else None,
                physical_objective=(r.get("result") or {}).get("objective"),
                named_costs=r.get("named_costs"),
                canonical_objective=r.get("canonical_objective"),
                objective_reconstruction_error=r.get("objective_reconstruction_error"),
                common_kkt=r.get("common_kkt"),
                bridge=bridge,
                worker_seconds=r["worker_seconds"],
                combined_peak_rss_mib=r["native_combined_peak_rss_mib"],
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
                    {
                        key: arm[key]
                        for key in ("mode", "native", "accepted", "checks", "bridge")
                    }
                ),
                flush=True,
            )
        if context() != binding:
            raise ValueError("context changed")
        d.publish(
            output / "summary.json", dict(context=binding, arms=arms, promotional=False)
        )


if __name__ == "__main__":
    main()
