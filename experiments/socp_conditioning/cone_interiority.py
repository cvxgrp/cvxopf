"""One exact native SuperLU replay with observational cone failure capture."""

import argparse
from decimal import Decimal, localcontext
import gzip
import json
import os
from pathlib import Path
import re
import signal
from unittest.mock import patch

import numpy as np

from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import native_lu_probe as lu
from experiments.socp_conditioning import native_step_probe as n
from experiments.socp_conditioning import termination_probe as t
from experiments.socp_conditioning import termination_analysis as a

PRIOR = d.HERE / "results/native_lu_probe_001/superlu"
PRIOR_SHA = "756623ebc5199c2d8b632396b454ae66593084a63091e2cf981bf458d8995dbc"


def context():
    if d.sha(PRIOR / "arm.json.gz") != PRIOR_SHA:
        raise ValueError("SuperLU reference changed")
    base = lu.context()
    return base | dict(
        experiment="native_cone_interiority_trace",
        prior_arm_sha256=PRIOR_SHA,
        additional_sources=base["additional_sources"]
        | {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (__file__, lu.__file__, d.HERE / "CONE_INTERIORITY_PROTOCOL.md")
        },
        policy=dict(observational_only=True, optimizer_calls=1, precision=90),
    )


def soc_metrics(values):
    """Compute exact-input margins, not margins of rounded decimal approximations."""
    x = np.asarray(values, dtype=float)
    if x.ndim != 1 or len(x) < 2 or not np.isfinite(x).all():
        raise ValueError("invalid SOC vector")
    with localcontext() as ctx:
        ctx.prec = 90
        v = [Decimal.from_float(float(z)) for z in x]
        norm = sum(z * z for z in v[1:]).sqrt()
        margin = v[0] - norm
        return dict(
            margin=str(margin),
            determinant=str(v[0] * v[0] - sum(z * z for z in v[1:])),
            relative_margin=str(margin / max(abs(v[0]), norm, Decimal("1e-300"))),
            strictly_interior=margin > 0,
            leading_ulp=float(np.spacing(abs(x[0]))),
        )


def parse_failure(log):
    iteration, alpha, inverse, check = None, None, None, None
    failures = []
    for line in log.splitlines():
        if line.startswith("TRACE ITER"):
            iteration = int(re.search(r"iter=(\d+)", line)[1])
        elif line.startswith("TRACE CONE_ADD"):
            alpha = float(line.split("alpha=")[1])
        elif line.startswith("TRACE CONE_RESCALE"):
            inverse = float(line.split("inverse=")[1])
        elif line.startswith("TRACE CONE_CHECK"):
            check = line
        elif line.startswith("TRACE CONE_FAILURE"):
            row = re.search(r"rows=(\d+)\.\.(\d+)", line)
            failures.append(
                dict(
                    iteration=iteration,
                    rows=[int(row[1]), int(row[2])],
                    kind=re.search(r"kind=(\w+)", line)[1],
                    check=check,
                    preceding_applied_alpha=alpha,
                    following_rescale_inverse=inverse,
                    **{
                        k: json.loads(v)
                        for k, v in re.findall(r"(\w+)=(\[[^]]*\])", line)
                    },
                )
            )
        elif line.startswith("TRACE CONE_PREVIOUS"):
            failures[-1]["previous"] = {
                k: json.loads(v) for k, v in re.findall(r"(\w+)=(\[[^]]*\])", line)
            }
    if len(failures) != 1 or failures[0].get("previous") is None or check is None:
        raise ValueError("missing or ambiguous cone failure evidence")
    result = failures[0]
    for name in ("s", "z"):
        result[name + "_metrics"] = soc_metrics(result[name])
        result["previous"][name + "_metrics"] = soc_metrics(result["previous"][name])
    return result


def verify_replay(record, binding, prior):
    lu.k.validate_arm(record, binding, prior, "superlu")
    if not n.same_solution(record["raw_clarabel"], prior["raw_clarabel"]):
        raise ValueError("observational replay changed vectors")
    for key in ("status", "iterations", "gap_abs", "gap_rel", "res_primal", "res_dual"):
        if record["native"][key] != prior["native"][key]:
            raise ValueError("observational replay changed termination")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    output, reference = args.output.resolve(), args.reference.resolve()
    with lu.execution_configuration(), patch.object(t, "context", context):
        if args.worker:

            def instrument(solver, directory, record):
                return n.instrumentation(solver, directory, record, mode="instrumented")

            with (
                lu.environment("superlu"),
                patch.dict(os.environ, {"CVXOPF_CONE_TRACE": "1"}),
                patch.object(t, "instrumentation", instrument),
                patch.object(d, "rss", lu.process_rss),
            ):
                t.worker(output, reference, "CLARABEL")
            return
        binding = context()
        output.mkdir(parents=True, exist_ok=False)
        d.publish(output / "binding.json", binding)
        d.publish(output / "engine.json", n.engine_record())
        folder = output / "instrumented"
        lu.supervision.supervise(
            folder, reference, worker_module=__spec__.name, context_factory=context
        )
        sup = json.loads((folder / "supervision.json").read_text())
        if sup["classification"] != "completed" or sup["returncode"] != 0:
            raise RuntimeError("stop after supervised process/resource failure")
        for name, digest in sup["artifacts"].items():
            if d.sha(folder / name) != digest:
                raise ValueError("supervised artifact changed")
        r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        prior = json.load(gzip.open(PRIOR / "arm.json.gz", "rt"))
        verify_replay(r, binding, prior)
        failure = parse_failure((folder / "worker.log").read_text())
        if context() != binding:
            raise ValueError("context changed")
        result = dict(
            schema=1,
            promotional=False,
            optimizer_calls=1,
            exact_replay_verified=True,
            context=binding,
            native=r["native"],
            accepted=r["accepted"],
            checks=a.numerical_checks(r["audit"]),
            physical_residuals=r["audit"]["residuals"],
            objective_reconstruction_error=r["objective_reconstruction_error"],
            failure=failure,
            worker_seconds=r["worker_seconds"],
            combined_peak_rss_mib=r["native_combined_peak_rss_mib"],
            arm_sha256=d.sha(folder / "arm.json.gz"),
            supervision_sha256=d.sha(folder / "supervision.json"),
            engine_sha256=d.sha(output / "engine.json"),
        )
        d.publish(output / "summary.json", result)
        print(json.dumps({k: result[k] for k in ("native", "checks", "failure")}))


if __name__ == "__main__":
    main()
