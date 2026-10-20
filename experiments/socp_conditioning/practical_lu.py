"""Two bounded serial SuperLU checks at a prospective 1e-9 relative gap."""

import argparse
from contextlib import ExitStack, contextmanager
import gzip
import json
import os
from pathlib import Path
import signal
from unittest.mock import patch

from experiments.socp_conditioning import native_lu_probe as lu

d, t, n = lu.d, lu.t, lu.n
BASE_LOAD = d.load_case
BASE_AUDIT = t.n.audit
CASES = {
    "surplus": (
        "fixed_surplus_scaled",
        "7f33b6dda454586be49036c3fa7059bb23fc1512d6e5a391fd3a4073878c0fe4",
    ),
    "deficit": (
        "deficit_shedding_scaled",
        "44783b733fa3c934d9919a71f02076107a55a134107dba28d94d8ec7382f4aea",
    ),
}
OLD_INPUT = d.HERE / "results/native_lu_probe_001/superlu/native_input.json"


def options(solver):
    return lu.m.options(solver) | dict(tol_gap_rel=1e-9)


def context():
    for folder, digest in CASES.values():
        if (
            d.sha(d.HERE / "results/fixed_substitution_001" / folder / "arm.json.gz")
            != digest
        ):
            raise ValueError("historical case changed")
    if d.sha(OLD_INPUT) != lu.k.INPUT_SHA:
        raise ValueError("historical native input changed")
    base = lu.context()
    base.pop("native_input_sha256")
    return base | dict(
        experiment="practical_superlu_relative_gap_1e-9",
        # The inherited base context is case-independent even inside the worker.
        prior_arm_sha256=CASES["surplus"][1],
        arms={k: list(v) for k, v in CASES.items()},
        options={"CLARABEL": options("CLARABEL")},
        additional_sources=base["additional_sources"]
        | {
            str(Path(p).relative_to(d.ROOT)): d.sha(p)
            for p in (__file__, d.HERE / "PRACTICAL_LU_PROTOCOL.md")
        },
        policy=dict(
            relative_gap=1e-9,
            absolute_gap=1e-10,
            feasibility=1e-10,
            compensated_arithmetic=False,
            max_optimizer_calls=2,
            shedding="absent in surplus; retained in deficit",
            refinement="unchanged",
            factor_options=lu.lu.OPTIONS,
        ),
    )


@contextmanager
def native_environment():
    # New arithmetic switches must not leak into this SuperLU-only check.
    env = {k: v for k, v in os.environ.items() if not k.startswith("CVXOPF_")}
    with patch.dict(os.environ, env, clear=True), lu.environment("superlu"):
        yield


def compare_input(payload, old):
    expected = old | dict(settings=old["settings"] | dict(tol_gap_rel=1e-9))
    if payload != expected:
        raise ValueError("more than relative-gap tolerance changed")


def worker(folder, reference, case):
    prior_folder, prior_sha = CASES[case]

    def load_case(_name, reference):
        return BASE_LOAD(case, reference)

    def audit(_name, build, result, kwargs, named):
        if case == "surplus":
            return BASE_AUDIT(case, build, result, kwargs, named)
        return t.n.ORIGINAL_AUDIT(case, build, result, kwargs, named)

    def instrument(solver, directory, record):
        return n.instrumentation(solver, directory, record, mode="instrumented")

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(
                t, "PRIOR", d.HERE / "results/fixed_substitution_001" / prior_folder
            )
        )
        stack.enter_context(patch.object(t, "PRIOR_SHA", prior_sha))
        stack.enter_context(patch.object(d, "load_case", load_case))
        stack.enter_context(patch.object(t.n, "audit", audit))
        if case == "deficit":
            stack.enter_context(
                patch.object(t.n, "fixed_inputs", lambda kwargs: kwargs)
            )
        stack.enter_context(patch.object(t, "instrumentation", instrument))
        stack.enter_context(patch.object(d, "rss", lu.process_rss))
        stack.enter_context(native_environment())
        t.worker(folder, reference, "CLARABEL")


def analyze_arm(root, case, binding):
    folder = root / case
    sup = json.loads((folder / "supervision.json").read_text())
    if sup["classification"] != "completed" or sup["returncode"] != 0:
        raise ValueError("process/resource failure; stop without retry")
    for name, digest in sup["artifacts"].items():
        if d.sha(folder / name) != digest:
            raise ValueError("artifact changed")
    r = json.load(gzip.open(folder / "arm.json.gz", "rt"))
    if (
        r["exception"] is not None
        or r["context"] != binding
        or r["context_after"] != binding
        or r["optimizer_calls"] != 1
    ):
        raise ValueError(f"worker reconstruction/provenance failure: {r['exception']}")
    if r["solver_options"] != options("CLARABEL"):
        raise ValueError("solver options changed")
    payload = json.loads((folder / "native_input.json").read_text())
    if case == "surplus":
        compare_input(payload, json.loads(OLD_INPUT.read_text()))
    log = (folder / "worker.log").read_text()
    bridge = lu.bridge_summary(log)
    if (
        not bridge["factors"]
        or "TRACE SHARED_MODE enabled=false" not in log
        or "TRACE COMPENSATED" in log
        or "TRACE SCALING" in log
    ):
        raise ValueError("incorrect numerical path")
    prior = d.HERE / "results/fixed_substitution_001" / CASES[case][0]
    if r["common_problem"]["transformed_sha256"] != d.sha(prior / "transformed.npz"):
        raise ValueError("prepared model mismatch")
    result = {
        k: r.get(k)
        for k in (
            "native",
            "accepted",
            "solver_exception",
            "common_kkt",
            "named_costs",
            "canonical_objective",
            "objective_reconstruction_error",
            "worker_seconds",
            "native_combined_peak_rss_mib",
            "input_sha256",
            "common_problem",
        )
    }
    return result | dict(
        case=case,
        interval=r["historical_reference"]["arm"],
        checks=lu.a.numerical_checks(r["audit"]) if r["audit"] else None,
        physical_residuals=(r["audit"] or {}).get("residuals"),
        physical_objective=(r.get("result") or {}).get("objective"),
        bridge=bridge,
        supervisor_seconds=sup["wall_seconds"],
        arm_sha256=d.sha(folder / "arm.json.gz"),
        supervision_sha256=d.sha(folder / "supervision.json"),
        native_input_sha256=d.sha(folder / "native_input.json"),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    args = parser.parse_args()

    def interrupt(signum, frame):
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, interrupt)
    root, reference = args.output.resolve(), args.reference.resolve()
    with (
        patch.object(t, "context", context),
        patch.object(t, "options", options),
        patch.object(t, "SOLVERS", ("CLARABEL",)),
    ):
        if args.worker:
            worker(root, reference, args.case)
            return
        binding = context()
        root.mkdir(parents=True, exist_ok=False)
        d.publish(root / "binding.json", binding)
        d.publish(root / "engine.json", n.engine_record())
        arms = []
        for case in CASES:
            lu.supervision.supervise(
                root / case,
                reference,
                worker_module=__spec__.name,
                context_factory=context,
                worker_arguments=("--case", case),
            )
            result = analyze_arm(root, case, binding)
            arms.append(result)
            d.publish(root / f"{case}-comparison.json", result)
            print(
                json.dumps(
                    {k: result[k] for k in ("case", "native", "accepted", "checks")}
                ),
                flush=True,
            )
        if context() != binding:
            raise ValueError("context changed")
        d.publish(
            root / "summary.json",
            dict(
                context=binding,
                arms=arms,
                promotional=False,
                engine_sha256=d.sha(root / "engine.json"),
                optimizer_calls=2,
            ),
        )


if __name__ == "__main__":
    main()
