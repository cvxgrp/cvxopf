"""Prepare/replay eight public-path AC calls; execution requires a reviewed clean commit.

Preflight never creates an output directory or calls an optimizer. A fresh run
has no retry/resume mode. Historical experiments, E3 and Stage D are untouched.
"""
# ruff: noqa: E402 -- set numerical thread limits before numerical imports.

import argparse
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
import warnings

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

from cvxopf import extract_results
from experiments.ac_cost_coordinates.run import monitoring
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable
from . import fixture as f
from .audit import assess, canonical

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "results/verification_001"
MODULE = "experiments.ac_production_verification.run"


def context():
    value = q.context()
    value["production_verification_sources"] = {
        str(p.relative_to(original.ROOT)): q.digest(p) for p in sorted(HERE.iterdir())
        if p.suffix in {".py", ".md"}}
    return value


def protocol():
    return dict(protocol=f.LIMITS, protocol_sha256=q.digest(HERE / "PROTOCOL.md"))


def frozen_binding():
    prepared = original.e3.verified_inputs()
    rows = [f.row_binding(arm, prepared) for arm in f.arms()]
    for a, b in zip(rows[::2], rows[1::2], strict=True):
        if (a["group"] != b["group"] or a["physical_start"] != b["physical_start"] or
                a["stress"] != b["stress"] or a["frozen"]["mathematical_input_sha256"] !=
                b["frozen"]["mathematical_input_sha256"]):
            raise ValueError("unmatched physical input/start pair")
    from experiments.case118_tracy_2021.prepare import SOURCE
    from experiments.case118_annual_hierarchy.pglib_case import SOURCE_CASE_PATH
    paths = (SOURCE, SOURCE_CASE_PATH, original.ROOT / "experiments/case118_tracy_2021/stage_a/manifest.json")
    return dict(context=context(), rows=rows, raw_inputs={
        str(p.relative_to(original.ROOT)): q.digest(p) for p in paths})


def verify_binding(root):
    binding = q.read(root / "binding.json")
    if binding["context"] != context() or q.read(root / "protocol.json") != protocol():
        raise ValueError("source/environment/protocol binding changed")
    for name, digest in binding["raw_inputs"].items():
        if q.digest(original.ROOT / name) != digest:
            raise ValueError("raw input hash changed")
    if len(binding["rows"]) != len(f.arms()):
        raise ValueError("matrix differs from reviewed protocol")
    return binding


def checked_arm(binding, number):
    arm = f.arms()[number-1]
    if f.row_binding(arm) != binding["rows"][number-1]:
        raise ValueError("input/start/policy differs from frozen arm")
    return arm


def request(root, number, wall):
    return dict(call_id=number, role="primary", wall_seconds=wall,
                protocol=q.reference(root / "protocol.json", root))


def worker(root, number):
    directory = root / f"call-{number:03d}"
    req = q.read(directory / "request.json")
    record = dict(iteration=number, arm=number, request=req, optimizer_calls=0, exception=None)
    began = time.monotonic()

    def phase(name):
        q.atomic_json(directory / "phase.json", dict(phase=name, utc=q.utc(),
                                                    elapsed_seconds=time.monotonic()-began))

    try:
        deadline = time.monotonic()+5
        while not (directory / "launch.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if (q.read(directory / "launch.json")["pid"] != os.getpid() or
                (directory / "supervision.json").exists() or (root / "STOP").exists() or
                req != request(root, number, req["wall_seconds"]) or
                not 0 < req["wall_seconds"] <= f.LIMITS["wall_seconds"]):
            raise ValueError("invalid worker launch/request")
        binding = verify_binding(root)
        phase("construct")
        arm = checked_arm(binding, number)
        _, _, build, _, _ = f.construct(arm)
        phase("solve")
        record["optimizer_calls"] = 1
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                build.solve(**original.solver_options("ac"))
        finally:
            record["warnings"] = [dict(category=w.category.__name__, message=str(w.message)) for w in caught]
            record["preparation_evidence"] = evidence_record(build.preparation_evidence)
            if record["preparation_evidence"] is not None:
                q.atomic_gzip_json(directory / "native.json.gz", dict(
                    iteration=number, native=record["preparation_evidence"]["native"]))
        if record["preparation_evidence"] is None:
            raise ValueError("public solve did not retain native preparation evidence")
        native = record["preparation_evidence"]["native"]
        phase("audit")
        if native["status"] == 0:
            record["result"] = serializable(extract_results(build))
            record["audit"] = assess(arm, native, record["preparation_evidence"], record["result"])
        verify_binding(root)
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["classification"] = ("exception" if record["exception"] else
                                "accepted" if record.get("audit", {}).get("passed") else "rejected")
    phase("archive")
    q.atomic_gzip_json(directory / "result.json.gz", record)
    artifacts = {name: q.digest(directory / name) for name in ("request.json", "result.json.gz", "native.json.gz")
                 if (directory / name).exists()}
    q.atomic_immutable_json(directory / "completion.json", dict(utc=q.utc(), artifacts=artifacts))
    phase("complete")


def status(root):
    binding = verify_binding(root)
    directories = sorted(root.glob("call-*"))
    if len(directories) > f.LIMITS["max_launches"] or [p.name for p in directories] != [
            f"call-{n:03d}" for n in range(1, len(directories)+1)]:
        raise ValueError("noncontiguous or excess launches")
    rows, used = [], 0.
    counts = dict(result_archives=0, native_archives=0, completion_manifests=0, finalized=0, accepted=0,
                  rejected=0, cycling_warnings=0)
    for number, directory in enumerate(directories, 1):
        for field, name in (("result_archives", "result.json.gz"), ("native_archives", "native.json.gz"),
                            ("completion_manifests", "completion.json")):
            counts[field] += int((directory / name).exists())
        req = q.read(directory / "request.json")
        if (req != request(root, number, req["wall_seconds"]) or
                not 0 < req["wall_seconds"] <= min(f.LIMITS["wall_seconds"], f.LIMITS["total_worker_seconds"]-used)):
            raise ValueError("invalid attempt resource request")
        row = dict(arm=asdict(f.arms()[number-1]), classification="unfinished", accepted=False)
        rows.append(row)
        if not (directory / "supervision.json").exists():
            if number != len(directories):
                raise ValueError("cannot advance an unfinished attempt")
            continue
        sup = q.read(directory / "supervision.json")
        if sup["classification"] not in {"exited", "wall_limit", "rss_limit", "interrupted", "supervisor_failure"}:
            raise ValueError("unknown supervision outcome")
        elapsed = sup["wall_seconds"]
        if not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid supervision elapsed time")
        used += elapsed
        counts["finalized"] += 1
        row.update(classification=sup["classification"], worker_seconds=elapsed,
                   peak_sampled_rss_mib=sup["peak_sampled_rss_mib"])
        resources = q.resource_evidence(directory, sup)
        manifest = directory / "completion.json"
        if not manifest.exists():
            if sup["classification"] == "exited" and sup["returncode"] == 0:
                raise ValueError("normal exit lacks completion manifest")
            continue
        completion = q.read(manifest)
        expected = {name: q.digest(directory / name) for name in
                    ("request.json", "result.json.gz", "native.json.gz") if (directory / name).exists()}
        if completion["artifacts"] != expected or "result.json.gz" not in expected:
            raise ValueError("completion manifest/archive mismatch")
        record = q.read(directory / "result.json.gz")
        if record["arm"] != number or record["request"] != req or record["optimizer_calls"] not in (0, 1):
            raise ValueError("worker attempt record mismatch")
        checked_arm(binding, number)
        if "native.json.gz" in expected:
            native_record = q.read(directory / "native.json.gz")
            if native_record["iteration"] != number:
                raise ValueError("native archive call mismatch")
            native = native_record["native"]
            if native != record["preparation_evidence"]["native"]:
                raise ValueError("native/result archive mismatch")
        elif record.get("audit") is not None:
            raise ValueError("audited result lacks native archive")
        if record.get("audit") is not None:
            audit = assess(f.arms()[number-1], native, record["preparation_evidence"], record["result"])
            if audit != record["audit"]:
                raise ValueError("retained audit differs from independent replay")
        expected_class = ("exception" if record["exception"] else
                          "accepted" if record.get("audit", {}).get("passed") else "rejected")
        if record["classification"] != expected_class:
            raise ValueError("worker classification disagrees with evidence")
        row["accepted"] = bool(expected_class == "accepted" and record["optimizer_calls"] == 1 and resources
                               and sup["classification"] == "exited" and sup["returncode"] == 0
                               and elapsed <= req["wall_seconds"] and used <= f.LIMITS["total_worker_seconds"])
        row.update(worker_classification=expected_class, audit=record.get("audit"), exception=record["exception"])
        counts["accepted"] += int(row["accepted"])
        counts["rejected"] += int(expected_class == "rejected")
        counts["cycling_warnings"] += int(record.get("audit", {}).get("economics", {}).get("cycling_warning", False))
    pairs = []
    for a, b in zip(rows[::2], rows[1::2]):
        if a["accepted"] and b["accepted"]:
            x, y = (row["audit"]["economics"]["physical_objective"] for row in (a, b))
            pairs.append(dict(arms=[a["arm"]["id"], b["arm"]["id"]], physical_objectives=[x, y],
                              coordinate_minus_original_cost=y-x))
    return dict(expected_cases=len(f.arms()), launches=len(directories), **counts,
                worker_seconds=used, remaining_worker_seconds=max(0., f.LIMITS["total_worker_seconds"]-used),
                attempts=rows, pairs=pairs)


def run(root, commit):
    binding = frozen_binding()
    if not binding["context"]["clean"] or binding["context"]["commit"] != commit or len(commit) != 40:
        raise ValueError("reviewed clean full commit required before execution")
    if any(binding["context"]["thread_environment"][k] != "1" for k in THREAD_KEYS):
        raise ValueError("single-thread numerical environment required")
    telemetry = monitoring()
    root.mkdir(parents=True, exist_ok=False)
    q.atomic_immutable_json(root / "binding.json", binding)
    q.atomic_immutable_json(root / "protocol.json", protocol())
    q.atomic_immutable_json(root / "invocation-start.json", dict(utc=q.utc(), telemetry=telemetry))
    outcome, exception = "failure", None
    try:
        for number in range(1, len(f.arms())+1):
            progress = status(root)
            remaining = progress["remaining_worker_seconds"]
            if progress["launches"] != progress["finalized"]:
                raise ValueError("unfinished attempt cannot advance")
            if (root / "STOP").exists() or remaining <= 0:
                outcome = "operator_stop" if (root / "STOP").exists() else "budget_exhausted"
                break
            telemetry = monitoring()
            directory = root / f"call-{number:03d}"
            directory.mkdir()
            q.atomic_immutable_json(directory / "telemetry-start.json", telemetry)
            req = request(root, number, min(f.LIMITS["wall_seconds"], remaining))
            q.atomic_immutable_json(directory / "request.json", req)
            sup = q.supervise([sys.executable, "-m", MODULE, "--worker", str(number), "--output", str(root)],
                              directory, root, req)
            progress = status(root)
            q.atomic_json(root / "report.json", progress)
            if sup["classification"] != "exited" or sup["returncode"] != 0:
                outcome = sup["classification"] if sup["classification"] != "exited" else "worker_failure"
                break
            if progress["attempts"][-1].get("worker_classification") == "exception":
                outcome = "worker_exception"
                break
        else:
            outcome = "matrix_complete"
    except KeyboardInterrupt:
        outcome = "operator_stop"
    except Exception as exc:
        exception = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        q.atomic_immutable_json(root / "invocation-finish.json", dict(utc=q.utc(), outcome=outcome, exception=exception))
    return dict(outcome=outcome, output=str(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--authorize-execution", action="store_true")
    group.add_argument("--status", action="store_true")
    group.add_argument("--worker", type=int, choices=range(1, len(f.arms())+1), help=argparse.SUPPRESS)
    parser.add_argument("--commit")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.parent != HERE / "results":
        parser.error("output must be a fresh direct child of this experiment's results directory")
    if args.authorize_execution and args.commit is None:
        parser.error("execution requires --commit and separate owner approval")
    if args.worker:
        worker(root, args.worker)
    elif args.authorize_execution:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, q.interrupted)
        value = run(root, args.commit)
        print(json.dumps(value, indent=2))
        if value["outcome"] != "matrix_complete":
            raise SystemExit(1)
    elif args.status:
        print(json.dumps(status(root), indent=2))
    else:
        value = frozen_binding()
        # Canonicalize every arm without solving, checking production maps/starts.
        for arm in f.arms():
            canonical(f.construct(arm)[2])
        print(json.dumps(dict(commit=value["context"]["commit"], clean=value["context"]["clean"],
                              limits=f.LIMITS, rows=[dict(arm=r["arm"], policy=r["frozen"]["policy"],
                                                       input_sha256=r["frozen"]["mathematical_input_sha256"],
                                                       stress=r["stress"]) for r in value["rows"]]), indent=2))


if __name__ == "__main__":
    main()
