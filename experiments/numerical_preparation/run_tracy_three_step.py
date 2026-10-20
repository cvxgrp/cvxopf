"""Three bounded matched convex solves; no retries, resume, or default changes.

``--preflight`` is non-solving/read-only. Execution requires
``--authorize-execution --commit <reviewed clean full SHA>``.
``--status`` independently checks archived variables/audits without solving.
"""
# ruff: noqa: E402 -- limits precede numerical imports.

import argparse
import json
import math
import os
import signal
import sys
import time

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

import cvxpy as cp
import numpy as np

from cvxopf import extract_results
from . import fixture as f, run_qualification as q
from .audit import audit_record, evidence_record, serializable
from .tracy_variables import (CALLS, common_inputs, historical_candidates,
                             capture_coordinates, canonical_layout, check_coordinates, expand_primal, compare)
from tests.socp_matched import digest as input_digest

OUTPUT = f.HERE / "results/tracy_three_step_001"
PROTOCOL = f.HERE / "TRACY_THREE_STEP_PROTOCOL.md"
MODULE = "experiments.numerical_preparation.run_tracy_three_step"
LIMITS = dict(max_launches=3, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=540., poll_seconds=1.)


def context():
    return q.context() | {"comparison_protocol_sha256": q.digest(PROTOCOL)}


def frozen_binding():
    historical, pins = historical_candidates()
    prepared = f.e3.verified_inputs()
    calls = [serializable(f.call_binding(c, f.kwargs_for_call(c, prepared))) for c in CALLS]
    identities = {input_digest(common_inputs(c["mathematical_inputs"])) for c in calls}
    identities.update(c["mathematical_input_sha256"] for c in historical.values())
    if len(identities) != 1:
        raise ValueError("all five candidates must have exactly matched non-formulation inputs")
    return dict(context=context(), calls=calls, limits=LIMITS, historical_pins=pins,
                common_input_sha256=identities.pop())


def request(root, number, wall):
    return dict(call_id=number, role="primary", wall_seconds=wall,
                protocol=q.reference(root / "protocol.json", root))


def worker(root, directory):
    binding, req = q.read(root / "binding.json"), q.read(directory / "request.json")
    call = CALLS[req["call_id"]-1]
    record = dict(iteration=call.id, request=req, optimizer_calls=0, exception=None,
        execution_context=binding["context"],
        mathematical_input_sha256=binding["calls"][call.id-1]["mathematical_input_sha256"])
    began = time.monotonic()

    def phase(name):
        q.atomic_json(directory / "phase.json", dict(phase=name, utc=q.utc(), elapsed_seconds=time.monotonic()-began))

    try:
        deadline = time.monotonic()+5
        while not (directory / "launch.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if (q.read(directory / "launch.json")["pid"] != os.getpid() or
            (directory / "supervision.json").exists() or (root / "STOP").exists() or
            req != request(root, call.id, req["wall_seconds"]) or context() != binding["context"]):
            raise ValueError("worker launch/request/source binding mismatch")
        phase("prepare")
        kwargs = f.kwargs_for_call(call)
        if serializable(f.call_binding(call, kwargs)) != binding["calls"][call.id-1]:
            raise ValueError("worker input/settings binding mismatch")
        build = f.build_for_call(call, kwargs)
        phase("solve")
        record["optimizer_calls"] = 1
        try:
            build.solve(warm_start=False, verbose=True, **f.solver_options(call.formulation))
        except cp.error.SolverError as exc:
            record["exception"] = f"{type(exc).__name__}: {exc}"
        record["preparation_evidence"] = evidence_record(build.preparation_evidence)
        record["native"] = (record["preparation_evidence"] or {}).get("native", {})
        phase("extract_and_audit")
        record["result"] = extract_results(build)
        record["boundary_soc_mwh"] = (None if record["result"].get("soc") is None else
            np.vstack(([s.initial_soc for s in kwargs["storage"]], record["result"]["soc"])))
        record["named_costs"] = {k: None if v.value is None else float(v.value)
                                 for k, v in build.expressions.items() if k.endswith("_cost")}
        record["audit"] = audit_record(call, kwargs, build, serializable(record))
        if record["native"].get("status") == "Solved":
            record["coordinates"] = capture_coordinates(build, record)
            record["coordinate_checks"] = check_coordinates(record["coordinates"], record["result"], kwargs)
        else:
            record["coordinate_checks"] = dict(passed=False, reason="no full native convergence")
        if context() != binding["context"]:
            raise ValueError("source/environment changed during worker")
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
        record["audit"] = dict(accepted=False, reason=record["exception"])
        record["coordinate_checks"] = dict(passed=False, reason=record["exception"])
    accepted = record["audit"]["accepted"] and record["coordinate_checks"]["passed"] and record["exception"] is None
    native_rejection = record.get("native", {}).get("status") not in (None, "Solved")
    record["classification"] = ("rejected" if native_rejection else "exception") if record["exception"] else ("accepted" if accepted else "rejected")
    phase("archive")
    q.atomic_gzip_json(directory / "result.json.gz", serializable(record))
    q.atomic_immutable_json(directory / "completion.json", dict(classification=record["classification"],
        artifacts={name: q.digest(directory / name) for name in ("request.json", "result.json.gz")}))
    phase("complete")


def status(root):
    binding = q.read(root / "binding.json")
    if binding != frozen_binding() or q.read(root / "protocol.json") != dict(protocol=LIMITS):
        raise ValueError("status requires bound inputs/source/environment/protocol")
    historical, _ = historical_candidates()
    prepared = f.e3.verified_inputs()
    attempts, candidates, used = [], dict(historical), 0.
    directories = sorted(root.glob("call-*"))
    if len(directories) > 3:
        raise ValueError("launch ceiling exceeded")
    for call, directory in zip(CALLS, directories):
        if directory.name != f"call-{call.id:03d}":
            raise ValueError("noncontiguous calls; no retries")
        if not (directory / "supervision.json").exists():
            if directory != directories[-1]:
                raise ValueError("unsupervised attempt precedes later call")
            attempts.append(dict(call_id=call.id, classification="unfinished", accepted=False))
            break
        req, sup = q.read(directory / "request.json"), q.read(directory / "supervision.json")
        wall = req["wall_seconds"]
        if (isinstance(wall, bool) or not math.isfinite(wall) or
            not 0 < wall <= min(180., 540.-used) or req != request(root, call.id, wall)):
            raise ValueError("request/budget mismatch")
        elapsed = sup["wall_seconds"]
        if isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid supervision effort")
        used += elapsed
        if sup["classification"] not in {"exited", "wall_limit", "rss_limit", "interrupted", "supervisor_failure"}:
            raise ValueError("unknown supervision disposition")
        resources = q.resource_evidence(directory, sup)
        accepted = False
        if (directory / "completion.json").exists():
            # Reuse the manifest, original-unit gate, and storage boundary checks;
            # coordinate checks are an additional gate for this new diagnostic.
            completion = q.read(directory / "completion.json")
            record = q.read(directory / "result.json.gz")
            # independent_record expects physical-audit classification only.
            # Verify independently below rather than weakening that contract.
            if set(completion["artifacts"]) != {"request.json", "result.json.gz"}:
                raise ValueError("incomplete comparison manifest")
            for name, sha in completion["artifacts"].items():
                if q.digest(directory / name) != sha:
                    raise ValueError("completion/archive mismatch")
            if (record["request"] != req or record["execution_context"] != binding["context"] or
                record["mathematical_input_sha256"] != binding["calls"][call.id-1]["mathematical_input_sha256"] or
                record["classification"] != completion["classification"] or record["optimizer_calls"] not in (0, 1)):
                raise ValueError("record/binding mismatch")
            if record["exception"] is None:
                kwargs = f.kwargs_for_call(call, prepared)
                build = f.build_for_call(call, kwargs)
                audit = serializable(audit_record(call, kwargs, build, record))
                if q.replayable_audit(audit) != q.replayable_audit(record["audit"]):
                    raise ValueError("independent audit/archive mismatch")
                if record["native"].get("status") == "Solved" and "coordinates" not in record:
                    raise ValueError("full native convergence lacks canonical coordinate evidence")
                if "coordinates" not in record and record["coordinate_checks"] != dict(passed=False, reason="no full native convergence"):
                    raise ValueError("unavailable primal cannot pass coordinate checks")
                if "coordinates" in record:
                    _, expected_layout = canonical_layout(build)
                    if record["coordinates"]["layout"] != expected_layout:
                        raise ValueError("original leaf layout differs from fresh build")
                    np.testing.assert_array_equal(expand_primal(record["native"], record["preparation_evidence"]), record["coordinates"]["full_x"])
                    checks = check_coordinates(record["coordinates"], record["result"], kwargs)
                    if checks != record["coordinate_checks"]:
                        raise ValueError("independent coordinate/archive mismatch")
                numerical = audit["accepted"] and record["coordinate_checks"]["passed"]
                if numerical != (record["classification"] == "accepted") or record["optimizer_calls"] != 1:
                    raise ValueError("classification/optimizer count mismatch")
                accepted = bool(numerical and sup["classification"] == "exited" and sup["returncode"] == 0 and resources and elapsed <= wall)
                expected_soc = (None if record["result"].get("soc") is None else
                    np.vstack(([s.initial_soc for s in kwargs["storage"]], record["result"]["soc"])))
                np.testing.assert_array_equal(record["boundary_soc_mwh"], expected_soc)
                candidates[call.formulation] = dict(result=record["result"], common=audit["common"],
                    coordinate_checks=record["coordinate_checks"], supervised_accepted=accepted,
                    native={k: v for k, v in record["native"].items() if k not in {"x", "s", "z"}})
            elif (record["audit"]["accepted"] or record["coordinate_checks"]["passed"] or
                  record["classification"] not in {"exception", "rejected"} or
                  (record["classification"] == "rejected" and record.get("native", {}).get("status") in (None, "Solved"))):
                raise ValueError("exception cannot be accepted; rejection requires native evidence")
        elif sup["classification"] == "exited" and sup["returncode"] == 0:
            raise ValueError("successful worker lacks completion")
        attempts.append(dict(call_id=call.id, classification=sup["classification"], accepted=accepted,
            wall_seconds=elapsed, peak_sampled_rss_mib=sup["peak_sampled_rss_mib"]))
    disposed = sum(a["classification"] != "unfinished" for a in attempts)
    progress = dict(launches=len(directories), disposed=disposed, accepted=sum(a["accepted"] for a in attempts),
        unresolved=disposed-sum(a["accepted"] for a in attempts), worker_seconds=used, attempts=attempts)
    comparison = compare(candidates, f.kwargs_for_call(CALLS[0], prepared))
    return serializable(dict(progress=progress, candidates=candidates, comparison=comparison))


def run(commit):
    binding = frozen_binding()
    if (len(commit or "") != 40 or not binding["context"]["clean"] or binding["context"]["commit"] != commit or
        any(binding["context"]["thread_environment"][k] != "1" for k in THREAD_KEYS)):
        raise ValueError("reviewed clean full commit and single-thread environment required")
    telemetry = q.monitoring_preflight()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    q.atomic_immutable_json(OUTPUT / "binding.json", binding)
    q.atomic_immutable_json(OUTPUT / "protocol.json", dict(protocol=LIMITS))
    invocation = OUTPUT / "invocations" / f"invocation-{time.time_ns()}"
    q.atomic_immutable_json(invocation / "start.json", dict(utc=q.utc(), telemetry=telemetry, execution_context=binding["context"]))
    outcome = "failure"
    try:
        for call in CALLS:
            report = status(OUTPUT)
            q.atomic_json(OUTPUT / "progress.json", report["progress"])
            if (OUTPUT / "STOP").exists():
                outcome = "operator_stop"
                break
            remaining = 540.-report["progress"]["worker_seconds"]
            if remaining <= 0:
                outcome = "budget_exhausted"
                break
            q.monitoring_preflight()
            directory = OUTPUT / f"call-{call.id:03d}"
            directory.mkdir()
            req = request(OUTPUT, call.id, min(180., remaining))
            q.atomic_immutable_json(directory / "request.json", req)
            sup = q.supervise([sys.executable, "-m", MODULE, "--worker", directory.name], directory, OUTPUT, req)
            report = status(OUTPUT)
            q.atomic_json(OUTPUT / "progress.json", report["progress"])
            q.atomic_json(OUTPUT / "report.json", report)
            if sup["classification"] not in {"exited", "wall_limit"} or (sup["classification"] == "exited" and sup["returncode"] != 0):
                outcome = sup["classification"]
                break
            if (directory / "completion.json").exists() and q.read(directory / "completion.json")["classification"] == "exception":
                outcome = "worker_exception"
                break
        else:
            outcome = "matrix_complete"
    finally:
        q.atomic_immutable_json(invocation / "finish.json", dict(utc=q.utc(), outcome=outcome))
    return dict(outcome=outcome, output=str(OUTPUT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--authorize-execution", action="store_true")
    mode.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--commit")
    args = parser.parse_args()
    if args.worker:
        if args.worker not in {f"call-{c.id:03d}" for c in CALLS}:
            parser.error("invalid worker directory")
        worker(OUTPUT, OUTPUT / args.worker)
        return
    if args.preflight:
        result = frozen_binding()
    elif args.status:
        result = status(OUTPUT)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, q.interrupted)
        result = run(args.commit)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
