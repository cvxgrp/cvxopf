"""Bounded four-call diagnostic on pinned working sources; no retries/resume.

Reuse existing fixture, verified IPOPT boundary, supervision and physical audit.
Unlike historical committed protocols, this owner-authorized quick diagnostic
explicitly records an uncommitted source snapshot. It never modifies old runs.
"""
# ruff: noqa: E402 -- worker thread limits precede numerical imports.

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

import numpy as np
import cvxpy as cp
from cvxopf import extract_results
from cvxopf._hierarchical_solver import _solve_ac_with_verified_x0
from experiments.numerical_preparation import fixture as f, run_qualification as q
from experiments.numerical_preparation.audit import (
    evidence_record, physical_audit, serializable, transformation_check,
)
from experiments.numerical_preparation.tracy_variables import historical_candidates, expand_primal
from .model import ARMS, construct, equivalence, restore_archive, accounting

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "results/comparison_001"
MODULE = "experiments.ac_cost_coordinates.run"
LIMITS = dict(max_launches=4, wall_seconds=180., rss_mib=16384.,
              total_worker_seconds=720., poll_seconds=1.)


def context():
    value = q.context()
    value["cost_coordinate_sources"] = {str(p.relative_to(f.ROOT)): q.digest(p)
        for p in sorted(HERE.iterdir()) if p.suffix in {".py", ".md"} and p.name != "REPORT.md"}
    return value


def historical_point(number):
    directory = f.OUTPUT / f"call-{number:03d}"
    record, captured = q.read(directory / "result.json.gz"), q.read(directory / "x0.json.gz")
    full = expand_primal(record["native"], record["preparation_evidence"])
    return {e["name"]: full[e["start"]:e["stop"]].reshape(e["shape"], order="F")
            for e in captured["layout"] if e["is_original_variable"]}


def frozen_binding():
    _, pins = historical_candidates()
    historical = q.read(f.OUTPUT / "binding.json")
    rows = []
    for i, (number, scaled) in enumerate(ARMS, 1):
        call, kwargs, view, start = construct(number, scaled)
        frozen = serializable(f.call_binding(call, kwargs))
        if frozen["mathematical_input_sha256"] != historical["calls"][number-1]["mathematical_input_sha256"]:
            raise ValueError("physical inputs differ from historical comparison")
        original_record = q.read(f.OUTPUT / f"call-{number:03d}" / "result.json.gz")
        if serializable(start) != original_record["physical_start"]:
            raise ValueError("physical initialization differs from historical comparison")
        checks = [equivalence(view, start)]
        if scaled:
            checks += [equivalence(view, historical_point(n)) for n in (24,25)]
        if not all(c["passed"] for c in checks):
            raise ValueError("non-solving representation equivalence failed")
        rows.append(dict(id=i, historical_call=number, scaled=scaled, call=asdict(call),
                         frozen=frozen, physical_start=serializable(start),
                         scales=serializable(view.scales), equivalence=checks))
    return dict(context=context(), limits=LIMITS, rows=rows, historical_pins=pins)


def monitoring():
    if q._child_rss_mib(os.getpid()) is None:
        raise RuntimeError("process/RSS permission unavailable")
    battery = subprocess.check_output(["pmset", "-g", "batt"], text=True)
    if "AC Power" not in battery:
        raise RuntimeError("AC power required before launching a new attempt")
    thermal = json.loads(subprocess.check_output(
        ["macmon", "pipe", "--samples", "1"], text=True, timeout=10))
    temperatures = thermal["temp"]
    if not all(np.isfinite(temperatures[k]) and temperatures[k] > 0 for k in ("cpu_temp_avg", "gpu_temp_avg")):
        raise RuntimeError("thermal sample unavailable")
    return dict(utc=q.utc(), battery=battery, thermal=thermal)


def request(root, number, wall):
    return dict(call_id=number, role="primary", wall_seconds=wall,
                protocol=q.reference(root / "protocol.json", root))


def assess(view, call, kwargs, native, evidence, captured):
    restored = view.restore()
    # Extraction's public scalar is the original symbolic objective, not the
    # retained canonical epigraph objective. Set it identically on live/replay
    # paths, without invoking an optimizer or disguising a nonzero native status.
    restored.prob._status = cp.OPTIMAL if native["status"] == 0 else cp.SOLVER_ERROR
    restored.prob._value = float(restored.prob.objective.value)
    result = serializable(extract_results(restored))
    named = {k: float(v.value) for k,v in restored.expressions.items() if k.endswith("_cost")}
    common, _ = physical_audit(restored, result, kwargs, named)
    costs = accounting(view, native, evidence, captured, common)
    preparation = transformation_check(call, kwargs, evidence, captured)
    return serializable(dict(result=result, named_costs=named, common=common, accounting=costs,
        transformation=preparation,
        passed=native["status"] == 0 and common["passed"] and costs["passed"] and preparation["passed"],
        physical_coordinates={v.name(): v.value for v in view.physical.prob.variables()},
        solver_coordinates={k: v.value for k,v in view.leaves.items()}))


def worker(root, number):
    directory = root / f"call-{number:03d}"
    binding, req = q.read(root / "binding.json"), q.read(directory / "request.json")
    row = binding["rows"][number-1]
    record = dict(iteration=number, arm=number, exception=None, optimizer_calls=0)
    began = time.monotonic()

    def phase(name):
        q.atomic_json(directory / "phase.json", dict(phase=name, utc=q.utc(), elapsed_seconds=time.monotonic()-began))

    try:
        deadline = time.monotonic()+5
        while not (directory / "launch.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if (q.read(directory / "launch.json")["pid"] != os.getpid() or
                (directory / "supervision.json").exists() or (root / "STOP").exists() or
                req != request(root, number, req["wall_seconds"]) or context() != binding["context"]):
            raise ValueError("launch/request/source binding mismatch")
        phase("construct")
        call, kwargs, view, start = construct(row["historical_call"], row["scaled"])
        if (serializable(start) != row["physical_start"] or serializable(view.scales) != row["scales"] or
                serializable(f.call_binding(call, kwargs)) != row["frozen"]):
            raise ValueError("input/start/coordinate map differs from frozen arm")
        record.update(request=req, physical_start=serializable(start), execution_context=binding["context"])

        def observe_start(value):
            record["captured"] = serializable(dict(iteration=number,
                complete_x0=value.complete_x0, layout=value.layout))
            q.atomic_gzip_json(directory / "x0.json.gz", record["captured"])

        def observe_native(value):
            record["native"] = serializable(value)

        phase("solve")
        record["optimizer_calls"] = 1
        outcome = _solve_ac_with_verified_x0(view.solver, None, start_observer=observe_start,
                        native_observer=observe_native, solver_options=f.solver_options("ac"))
        record["preparation_evidence"] = evidence_record(view.solver.preparation_evidence)
        if record["preparation_evidence"]:
            record["native"] = record["preparation_evidence"]["native"]
        record["exception"] = outcome.exception
        phase("audit")
        if outcome.exception is None and record["native"]["status"] == 0:
            record["checks"] = assess(view, call, kwargs, record["native"], record["preparation_evidence"], record["captured"])
        if context() != binding["context"]:
            raise ValueError("source/environment changed during attempt")
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["classification"] = ("exception" if record["exception"] else
        "accepted" if record.get("checks", {}).get("passed") else "rejected")
    phase("archive")
    q.atomic_gzip_json(directory / "result.json.gz", serializable(record))
    files = ["request.json", "result.json.gz"] + (["x0.json.gz"] if (directory / "x0.json.gz").exists() else [])
    q.atomic_immutable_json(directory / "completion.json", dict(classification=record["classification"],
                       artifacts={name: q.digest(directory / name) for name in files}))
    phase("complete")


def status(root):
    """Replay completion, resources, primals and physical audits without solving."""
    binding = q.read(root / "binding.json")
    if (context() != binding["context"] or binding["limits"] != LIMITS or
            [(r["historical_call"], r["scaled"]) for r in binding["rows"]] != list(ARMS) or
            q.read(root / "protocol.json") != dict(protocol=LIMITS)):
        raise ValueError("execution snapshot/protocol mismatch")
    for name, sha in binding["historical_pins"].items():
        if q.digest(f.OUTPUT/name) != sha:
            raise ValueError("historical evidence changed")
    attempts, used = [], 0.
    directories = sorted(root.glob("call-*"))
    if len(directories) > LIMITS["max_launches"]:
        raise ValueError("launch ceiling exceeded")
    for index, directory in enumerate(directories):
        number = len(attempts)+1
        if number > 4 or directory.name != f"call-{number:03d}":
            raise ValueError("noncontiguous attempts or launch ceiling exceeded")
        row = binding["rows"][number-1]
        if not (directory / "supervision.json").exists():
            if index != len(directories)-1:
                raise ValueError("unsupervised attempt precedes later calls")
            attempts.append(dict(arm=number, classification="unfinished", accepted=False))
            break
        sup, req = q.read(directory/"supervision.json"), q.read(directory/"request.json")
        if sup["classification"] not in {"exited", "wall_limit", "rss_limit", "interrupted", "supervisor_failure"}:
            raise ValueError("unknown supervision disposition")
        if (req != request(root, number, req["wall_seconds"]) or
                isinstance(req["wall_seconds"], bool) or isinstance(sup["wall_seconds"], bool) or
                not 0 < req["wall_seconds"] <= min(180.,720.-used) or
                not np.isfinite(sup["wall_seconds"]) or sup["wall_seconds"] < 0):
            raise ValueError("request/budget mismatch")
        used += sup["wall_seconds"]
        resources = q.resource_evidence(directory, sup)
        item = dict(arm=number, historical_call=row["historical_call"], scaled=row["scaled"],
                    classification=sup["classification"], accepted=False,
                    wall_seconds=sup["wall_seconds"], peak_sampled_rss_mib=sup["peak_sampled_rss_mib"])
        if (directory/"completion.json").exists():
            completion, record = q.read(directory/"completion.json"), q.read(directory/"result.json.gz")
            expected = {"request.json", "result.json.gz"} | ({"x0.json.gz"} if "captured" in record else set())
            if set(completion["artifacts"]) != expected:
                raise ValueError("incomplete completion manifest")
            for name, sha in completion["artifacts"].items():
                if q.digest(directory/name) != sha:
                    raise ValueError("manifest/archive mismatch")
            if (record["classification"] != completion["classification"] or record["optimizer_calls"] not in (0,1) or
                    record.get("request", req) != req or record["arm"] != number):
                raise ValueError("record/request mismatch")
            if record["classification"] not in {"accepted", "rejected", "exception"}:
                raise ValueError("unknown worker classification")
            if record["classification"] == "accepted" and "checks" not in record:
                raise ValueError("accepted archive lacks audits")
            if "checks" in record and (record["optimizer_calls"] != 1 or
                    record.get("execution_context") != binding["context"] or
                    record.get("physical_start") != row["physical_start"] or record["exception"] is not None):
                raise ValueError("audited archive lacks bound execution/start")
            item.update(worker_classification=record["classification"], exception=record["exception"])
            if "checks" in record:
                call, kwargs, view, _ = construct(row["historical_call"],row["scaled"])
                if record["captured"] != q.read(directory/"x0.json.gz"):
                    raise ValueError("x0/archive mismatch")
                restore_archive(view, record, record["captured"])
                replay = assess(view, call, kwargs, record["native"], record["preparation_evidence"], record["captured"])
                if replay != record["checks"]:
                    raise ValueError("independent physical/cost audit mismatch")
                if replay["passed"] != (record["classification"] == "accepted"):
                    raise ValueError("numerical classification mismatch")
                item["checks"] = replay
                item["accepted"] = bool(replay["passed"] and record["exception"] is None and resources and
                    sup["classification"] == "exited" and sup["returncode"] == 0 and sup["wall_seconds"] <= req["wall_seconds"])
        elif sup["classification"] == "exited" and sup["returncode"] == 0:
            raise ValueError("successful worker missing completion")
        attempts.append(item)
    return dict(launches=len(attempts), disposed=sum(a["classification"] != "unfinished" for a in attempts),
                accepted=sum(a["accepted"] for a in attempts), worker_seconds=used, attempts=attempts)


def run(root):
    binding = frozen_binding()
    if any(os.environ.get(k) != "1" for k in THREAD_KEYS):
        raise ValueError("single-thread environment required")
    telemetry = monitoring()
    root.mkdir(parents=True, exist_ok=False)
    q.atomic_immutable_json(root/"binding.json", binding)
    q.atomic_immutable_json(root/"protocol.json", dict(protocol=LIMITS))
    q.atomic_immutable_json(root/"invocation-start.json", dict(utc=q.utc(), telemetry=telemetry))
    outcome = "failure"
    try:
        for number in range(1,5):
            progress = status(root)
            remaining = 720.-progress["worker_seconds"]
            if (root/"STOP").exists() or remaining <= 0:
                outcome = "operator_stop" if (root/"STOP").exists() else "budget_exhausted"
                break
            telemetry = monitoring()
            directory = root/f"call-{number:03d}"
            directory.mkdir()
            q.atomic_immutable_json(directory/"telemetry-start.json", telemetry)
            req = request(root, number, min(180.,remaining))
            q.atomic_immutable_json(directory/"request.json", req)
            sup = q.supervise([sys.executable,"-m",MODULE,"--worker",str(number),"--output",str(root)], directory,root,req)
            progress = status(root)
            q.atomic_json(root/"report.json", progress)
            if sup["classification"] not in {"exited", "wall_limit"} or (sup["classification"] == "exited" and sup["returncode"] != 0):
                outcome = "worker_failure" if sup["classification"] == "exited" else sup["classification"]
                break
            if ((directory/"completion.json").exists() and
                    q.read(directory/"completion.json")["classification"] == "exception"):
                outcome = "worker_exception"
                break
        else:
            outcome = "matrix_complete"
    finally:
        q.atomic_immutable_json(root/"invocation-finish.json", dict(utc=q.utc(), outcome=outcome))
    return dict(outcome=outcome, output=str(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--execute", action="store_true")
    group.add_argument("--status", action="store_true")
    group.add_argument("--worker", type=int, choices=range(1,5), help=argparse.SUPPRESS)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.parent != HERE/"results":
        parser.error("output must be a direct child of this experiment's results directory")
    if args.worker:
        worker(root,args.worker)
    elif args.execute:
        for sig in (signal.SIGINT,signal.SIGTERM):
            signal.signal(sig,q.interrupted)
        result = run(root)
        print(json.dumps(result,indent=2))
        if result["outcome"] != "matrix_complete":
            raise SystemExit(1)
    elif args.status:
        print(json.dumps(status(root),indent=2))
    else:
        value = frozen_binding()
        print(json.dumps(dict(commit=value["context"]["commit"],clean=value["context"]["clean"],
                              limits=LIMITS, rows=[{k:v for k,v in r.items() if k in
                              {"id","historical_call","scaled","equivalence"}} for r in value["rows"]]),indent=2))


if __name__ == "__main__":
    main()
