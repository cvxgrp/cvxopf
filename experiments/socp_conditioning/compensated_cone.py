"""Native compensated determinant fixtures, then one bounded SuperLU replay."""

import argparse
from decimal import Decimal, localcontext
import gzip
import json
import os
from pathlib import Path
import re
import signal
import subprocess
from unittest.mock import patch

from experiments.socp_conditioning import cone_interiority as c
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import native_lu_probe as lu
from experiments.socp_conditioning import native_step_probe as n
from experiments.socp_conditioning import termination_probe as t
from experiments.socp_conditioning import termination_analysis as a

PRIOR = d.HERE / "results/cone_interiority_001/instrumented"
PRIOR_SHA = "2c3509ff4da97edc65a7a9c4061463120554fec676ca6d6851bc94250ff1652f"


def context():
    if d.sha(PRIOR / "arm.json.gz") != PRIOR_SHA:
        raise ValueError("comparison arm changed")
    base = lu.context()
    return base | dict(
        experiment="native_compensated_cone_fallback",
        prior_arm_sha256=PRIOR_SHA,
        additional_sources=base["additional_sources"]
        | {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (
                __file__,
                c.__file__,
                lu.__file__,
                d.HERE / "COMPENSATED_CONE_PROTOCOL.md",
            )
        },
        policy=dict(
            optimizer_calls=1,
            fallback="nonpositive finite sqrt determinant",
            algorithm="power-of-two scale, FMA product residuals, TwoSum",
            numerical_tolerance_changes=False,
        ),
    )


def precise_determinant(values):
    with localcontext() as ctx:
        ctx.prec = 90
        v = [Decimal.from_float(float(x)) for x in values]
        return v[0] * v[0] - sum(x * x for x in v[1:])


def validate_fixtures(stdout):
    records = [
        json.loads(line.split("CONE_FIXTURE ", 1)[1])
        for line in stdout.splitlines()
        if "CONE_FIXTURE " in line
    ]
    required = {
        "captured",
        "boundary",
        "outside",
        "interior",
        "negative_head",
        "zero",
        "next_lower",
        "next_higher",
        "small",
        "large",
    }
    if len(records) != len(required) or {r["label"] for r in records} != required:
        raise ValueError("missing native arithmetic fixtures")
    for r in records:
        exact = precise_determinant(r["z"])
        with localcontext() as ctx:
            ctx.prec = 90
            observed = (
                Decimal.from_float(r["scaled_det"])
                * Decimal.from_float(r["scale"]) ** 2
            )
            error = abs(observed - exact)
            if (not exact and observed) or (
                exact and error > abs(exact) * Decimal("1e-14")
            ):
                raise ValueError("compensated fixture accuracy mismatch")
        inside = r["z"][0] > 0 and exact > 0
        if (r["sqrt"] > 0) != inside:
            raise ValueError("compensated fixture interiority mismatch")
        r.update(exact_determinant=str(exact), absolute_error=str(error))
    return records


def native_fixture_gate():
    base = n.ENGINE.parent
    env = os.environ.copy() | dict(
        RUSTUP_HOME=str(base / "rustup"), CARGO_HOME=str(base / "cargo")
    )
    env.pop("CVXOPF_COMPENSATED_CONE", None)
    command = [
        str(base / "cargo/bin/cargo"),
        "test",
        "--release",
        "--lib",
        "--locked",
        "--jobs",
        "1",
        "compensated_cone_tests",
        "--",
        "--nocapture",
        "--test-threads=1",
    ]
    run = subprocess.run(
        command,
        cwd=n.ENGINE,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    return dict(
        command=command,
        stdout=run.stdout,
        stderr=run.stderr,
        fixtures=validate_fixtures(run.stdout),
        optimizer_calls=0,
    )


def fallbacks(log):
    result, iteration = [], -1
    for line in log.splitlines():
        if line.startswith("TRACE ITER"):
            iteration = int(re.search(r"iter=(\d+)", line)[1])
        if line.startswith("TRACE COMPENSATED"):
            vector = json.loads(re.search(r"z=(\[.*\])", line)[1])
            fields = {
                k: float(v)
                for k, v in re.findall(
                    r"(old|scaled_det|scale|recovered)=([^ ]+)", line
                )
            }
            exact = precise_determinant(vector)
            result.append(
                dict(
                    iteration=iteration,
                    z=vector,
                    exact_determinant=str(exact),
                    **fields,
                )
            )
    return result


def prefix_matches(current, prior, first_iteration):
    def strip(r):
        return {k: v for k, v in r.items() if k != "solve_time"}

    old = [strip(r) for r in prior if r["iterations"] <= first_iteration]
    new = [strip(r) for r in current if r["iterations"] <= first_iteration]
    return old == new


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
                patch.dict(
                    os.environ,
                    {"CVXOPF_COMPENSATED_CONE": "1", "CVXOPF_CONE_TRACE": "1"},
                ),
                patch.object(t, "instrumentation", instrument),
                patch.object(d, "rss", lu.process_rss),
            ):
                t.worker(output, reference, "CLARABEL")
            return
        binding = context()
        output.mkdir(parents=True, exist_ok=False)
        d.publish(output / "binding.json", binding)
        d.publish(output / "engine.json", n.engine_record())
        gate = native_fixture_gate()
        d.publish(output / "native-fixtures.json", gate)
        print(
            "Native compensated fixtures passed; launching one optimizer.", flush=True
        )
        folder = output / "compensated"
        lu.supervision.supervise(
            folder, reference, worker_module=__spec__.name, context_factory=context
        )
        sup = json.loads((folder / "supervision.json").read_text())
        if sup["classification"] != "completed" or sup["returncode"] != 0:
            raise RuntimeError("retained process/resource failure; no retry")
        for name, digest in sup["artifacts"].items():
            if d.sha(folder / name) != digest:
                raise ValueError("supervised artifact changed")
        r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        prior = json.load(gzip.open(PRIOR / "arm.json.gz", "rt"))
        lu.k.validate_arm(r, binding, prior, "superlu")
        log = (folder / "worker.log").read_text()
        events = fallbacks(log)
        same_prefix = bool(events) and prefix_matches(
            r["trace"], prior["trace"], events[0]["iteration"]
        )
        failures = c.parse_failure(log) if "TRACE CONE_FAILURE" in log else None
        if context() != binding:
            raise ValueError("context changed")
        result = dict(
            schema=1,
            promotional=False,
            context=binding,
            optimizer_calls=1,
            native=r["native"],
            accepted=r["accepted"],
            unchanged_before_fallback=same_prefix,
            fallbacks=events,
            cone_failure=failures,
            checks=a.numerical_checks(r["audit"]),
            physical_residuals=r["audit"]["residuals"],
            canonical_objective=r["canonical_objective"],
            objective_reconstruction_error=r["objective_reconstruction_error"],
            physical_objective=r["result"]["objective"],
            named_costs=r["named_costs"],
            common_kkt=r["common_kkt"],
            combined_peak_rss_mib=r["native_combined_peak_rss_mib"],
            worker_seconds=r["worker_seconds"],
            arm_sha256=d.sha(folder / "arm.json.gz"),
            supervision_sha256=d.sha(folder / "supervision.json"),
            native_fixtures_sha256=d.sha(output / "native-fixtures.json"),
            engine_sha256=d.sha(output / "engine.json"),
        )
        d.publish(output / "summary.json", result)
        print(
            json.dumps(
                {
                    k: result[k]
                    for k in (
                        "native",
                        "accepted",
                        "checks",
                        "unchanged_before_fallback",
                        "fallbacks",
                        "cone_failure",
                    )
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
