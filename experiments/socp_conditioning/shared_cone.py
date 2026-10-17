"""Native shared signed/shifted determinant gate and one supervised replay."""

import argparse
from decimal import Decimal, localcontext
import gzip
import json
import os
from pathlib import Path
import signal
import subprocess
from unittest.mock import patch

from experiments.socp_conditioning import compensated_cone as c
from experiments.socp_conditioning import diagnostic as d
from experiments.socp_conditioning import native_lu_probe as lu
from experiments.socp_conditioning import native_step_probe as n
from experiments.socp_conditioning import termination_probe as t
from experiments.socp_conditioning import termination_analysis as a

PRIOR = d.HERE / "results/compensated_cone_001/compensated"
PRIOR_SHA = "4a7b8896fd68ef1f8bae7beaf561067cd8281e55c58efe0cdd2f99ac10bab15c"


def context():
    if d.sha(PRIOR / "arm.json.gz") != PRIOR_SHA:
        raise ValueError("comparison changed")
    base = lu.context()
    return base | dict(
        experiment="shared_signed_compensated_SOC_determinant",
        prior_arm_sha256=PRIOR_SHA,
        additional_sources=base["additional_sources"]
        | {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (
                __file__,
                c.__file__,
                c.c.__file__,
                d.HERE / "SHARED_CONE_PROTOCOL.md",
            )
        },
        policy=dict(
            optimizer_calls=1,
            signed=True,
            shifted=True,
            application="all shared determinant calls",
            sqrt_only_fallback=False,
            tolerances_changed=False,
        ),
    )


def validate_fixtures(stdout):
    records = [
        json.loads(line.split("SHARED_FIXTURE ", 1)[1])
        for line in stdout.splitlines()
        if "SHARED_FIXTURE " in line
    ]
    required = {
        "captured",
        "boundary",
        "outside",
        "interior",
        "negative_head",
        "zero",
        "direction",
        "next_lower",
        "next_higher",
        "small",
        "large",
        "shifted",
    }
    if len(records) != len(required) or {r["label"] for r in records} != required:
        raise ValueError("incomplete native shared fixture records")
    for r in records:
        exact = c.precise_determinant(r["z"])
        with localcontext() as ctx:
            ctx.prec = 90
            error = abs(Decimal.from_float(r["det"]) - exact)
            if (not exact and error) or (
                exact and error > abs(exact) * Decimal("1e-14")
            ):
                raise ValueError("shared determinant accuracy mismatch")
        r.update(exact_determinant=str(exact), absolute_error=str(error))
    ops = [
        json.loads(line.split("SHARED_OPERATIONS ", 1)[1])
        for line in stdout.splitlines()
        if "SHARED_OPERATIONS " in line
    ]
    if len(ops) != 1 or not 0 < ops[0]["step"] <= 1:
        raise ValueError("shared operation check failed")
    return dict(records=records, operations=ops[0])


def fixture_gate():
    base = n.ENGINE.parent
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("CVXOPF_SHARED_CONE", "CVXOPF_COMPENSATED_CONE")
    }
    env.update(RUSTUP_HOME=str(base / "rustup"), CARGO_HOME=str(base / "cargo"))
    command = [
        str(base / "cargo/bin/cargo"),
        "test",
        "--release",
        "--lib",
        "--locked",
        "--jobs",
        "1",
        "shared_cone_tests",
        "--",
        "--nocapture",
        "--test-threads=1",
    ]
    result = subprocess.run(
        command,
        cwd=n.ENGINE,
        env=env,
        text=True,
        capture_output=True,
        check=True,
        timeout=120,
    )
    return dict(
        command=command,
        stdout=result.stdout,
        stderr=result.stderr,
        validation=validate_fixtures(result.stdout),
        optimizer_calls=0,
    )


def first_difference(current, prior):
    for left, right in zip(current, prior):
        keys = (set(left) | set(right)) - {"solve_time"}
        different = sorted(k for k in keys if left.get(k) != right.get(k))
        if different:
            return dict(iteration=left["iterations"], fields=different)
    return None


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

            with lu.environment("superlu"):
                env = dict(os.environ)
                env.pop("CVXOPF_COMPENSATED_CONE", None)
                env.update(CVXOPF_SHARED_CONE="1", CVXOPF_CONE_TRACE="1")
                with (
                    patch.dict(os.environ, env, clear=True),
                    patch.object(t, "instrumentation", instrument),
                    patch.object(d, "rss", lu.process_rss),
                ):
                    t.worker(output, reference, "CLARABEL")
            return
        binding = context()
        output.mkdir(parents=True, exist_ok=False)
        d.publish(output / "binding.json", binding)
        d.publish(output / "engine.json", n.engine_record())
        d.publish(output / "native-fixtures.json", fixture_gate())
        print("Shared arithmetic fixtures passed; launching one optimizer.", flush=True)
        folder = output / "shared"
        lu.supervision.supervise(
            folder, reference, worker_module=__spec__.name, context_factory=context
        )
        sup = json.loads((folder / "supervision.json").read_text())
        if sup["classification"] != "completed" or sup["returncode"] != 0:
            raise RuntimeError("retained supervised failure; no retry")
        for name, digest in sup["artifacts"].items():
            if d.sha(folder / name) != digest:
                raise ValueError("artifact changed")
        r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
        prior = json.load(gzip.open(PRIOR / "arm.json.gz", "rt"))
        lu.k.validate_arm(r, binding, prior, "superlu")
        log = (folder / "worker.log").read_text()
        if "TRACE SHARED_MODE enabled=true" not in log or "TRACE COMPENSATED" in log:
            raise ValueError("incorrect native mode")
        failure = c.c.parse_failure(log) if "TRACE CONE_FAILURE" in log else None
        if context() != binding:
            raise ValueError("context changed")
        result = dict(
            schema=1,
            promotional=False,
            context=binding,
            optimizer_calls=1,
            native=r["native"],
            accepted=r["accepted"],
            first_changed_iteration=first_difference(r["trace"], prior["trace"]),
            cone_failure=failure,
            bridge=lu.bridge_summary(log),
            checks=a.numerical_checks(r["audit"]) if r.get("audit") else None,
            physical_residuals=(r.get("audit") or {}).get("residuals"),
            physical_objective=(r.get("result") or {}).get("objective"),
            canonical_objective=r.get("canonical_objective"),
            objective_reconstruction_error=r.get("objective_reconstruction_error"),
            named_costs=r.get("named_costs"),
            common_kkt=r.get("common_kkt"),
            combined_peak_rss_mib=r["native_combined_peak_rss_mib"],
            worker_seconds=r["worker_seconds"],
            arm_sha256=d.sha(folder / "arm.json.gz"),
            supervision_sha256=d.sha(folder / "supervision.json"),
            engine_sha256=d.sha(output / "engine.json"),
            fixtures_sha256=d.sha(output / "native-fixtures.json"),
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
                        "first_changed_iteration",
                        "cone_failure",
                    )
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
