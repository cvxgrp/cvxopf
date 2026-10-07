"""Bounded convex qualification: explicit preflight, clean-commit launch, read-only replay.

No resume, retries or scientific execution on import. The stock CLARABEL boundary, atomic writers, monitoring and supervisor are reused.
"""
# ruff: noqa: E402 -- numerical imports follow CLI thread limits.

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import signal
import sys
import time

THREAD_KEYS = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

import numpy as np
import cvxpy as cp
from experiments.ac_cost_coordinates import run as diagnostic
from experiments.numerical_preparation import fixture as old, run_qualification as q
from experiments.numerical_preparation.audit import serializable
from . import fixture as f, model
from .audit import assess

HERE = Path(__file__).resolve().parent
OUTPUT = HERE / "results/qualification_001"
MODULE = "experiments.convex_cost_qualification.run"


def context():
    value = q.context()
    value["convex_cost_qualification_sources"] = {str(p.relative_to(old.ROOT)): q.digest(p)
        for p in sorted(HERE.iterdir()) if p.suffix in {".py", ".md"}}
    return value


def frozen_binding():
    prepared = old.e3.verified_inputs()
    rows = [f.row_binding(arm, prepared) for arm in f.arms()]
    groups = {}
    for row in rows:
        hashes = row["mathematical_input_sha256"]
        if groups.setdefault(row["group"], hashes) != hashes:
            raise ValueError("matched group physical input mismatch")
    from experiments.case118_tracy_2021.prepare import SOURCE
    from experiments.case118_annual_hierarchy.pglib_case import SOURCE_CASE_PATH
    raw = {str(p.relative_to(old.ROOT)): q.digest(p) for p in
           (SOURCE, SOURCE_CASE_PATH, old.ROOT / "experiments/case118_tracy_2021/stage_a/manifest.json")}
    return dict(context=context(), limits=f.LIMITS, gates=f.GATES, rows=rows,
                raw_inputs=raw)


def protocol():
    return dict(protocol=f.LIMITS, gates=f.GATES, protocol_sha256=q.digest(HERE / "PROTOCOL.md"))


def request(root, number, wall):
    return dict(call_id=number, role="primary", wall_seconds=wall,
                protocol=q.reference(root / "protocol.json", root))


def verify_binding(root):
    binding = q.read(root / "binding.json")
    if (binding["context"] != context() or binding["limits"] != f.LIMITS or binding["gates"] != f.GATES or
            q.read(root / "protocol.json") != protocol() or
            [row["arm"] for row in binding["rows"]] != [asdict(arm) for arm in f.arms()]):
        raise ValueError("execution source/environment/matrix/protocol mismatch")
    for paths, base in ((binding["raw_inputs"], old.ROOT),):
        for name, sha in paths.items():
            if q.digest(base / name) != sha:
                raise ValueError("historical/raw input evidence changed")
    return binding


def checked_construction(row):
    arm = f.Arm(**row["arm"])
    call, kwargs, view, stress, check = f.construct(arm)
    if (serializable(old.call_binding(call, kwargs)) != row["frozen"] or
            serializable(view.scales) != row["scales"] or stress != row["stress"] or check != row["equivalence"]):
        raise ValueError("resolved inputs/maps differ from bound arm")
    problem = model.canonical(view.solver)
    if problem.signature != row["canonical"]:
        raise ValueError("resolved canonical data differs from bound arm")
    return call, kwargs, view, stress, problem


def worker(root, number):
    binding = verify_binding(root)
    row = binding["rows"][number-1]
    directory = root / f"call-{number:03d}"
    req = q.read(directory / "request.json")
    record = dict(iteration=number, arm=number, request=req, execution_context=binding["context"],
                  exception=None, optimizer_calls=0)
    began = time.monotonic()

    def phase(name):
        q.atomic_json(directory / "phase.json", dict(phase=name, utc=q.utc(), elapsed_seconds=time.monotonic()-began))

    try:
        deadline = time.monotonic()+5
        while not (directory / "launch.json").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        if (q.read(directory / "launch.json")["pid"] != os.getpid() or
                (directory / "supervision.json").exists() or (root / "STOP").exists() or
                req != request(root, number, req["wall_seconds"]) or
                isinstance(req["wall_seconds"], bool) or not 0 < req["wall_seconds"] <= f.LIMITS["wall_seconds"]):
            raise ValueError("worker launch/request mismatch or operator stop")
        phase("construct")
        call, kwargs, view, stress, problem = checked_construction(row)

        def observe_native(value):
            record["native"] = value
            q.atomic_gzip_json(directory / "native.json.gz", dict(iteration=number, native=value,
                                                                canonical=problem.signature))

        phase("solve")
        record["optimizer_calls"] = 1
        try:
            record["preparation_evidence"] = model.solve_observed(view, problem,
                old.solver_options(call.formulation), observe_native)
        except cp.error.SolverError as exc:
            # Preserve native rejection without retrying or inventing dispatch.
            if record.get("native", {}).get("status") in (None, "Solved"):
                raise
            record["solver_exception"] = str(exc)
            from experiments.numerical_preparation.audit import evidence_record
            record["preparation_evidence"] = evidence_record(view.solver.preparation_evidence)
        phase("audit")
        if record["native"]["status"] == "Solved":
            record["checks"] = assess(view, kwargs, stress, record["native"],
                                      record["preparation_evidence"], problem.signature)
        verify_binding(root)
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["classification"] = ("exception" if record["exception"] else
                                "accepted" if record.get("checks", {}).get("passed") else "rejected")
    phase("archive")
    q.atomic_gzip_json(directory / "result.json.gz", serializable(record))
    names = ["request.json", "result.json.gz"] + (["native.json.gz"] if "native" in record else [])
    q.atomic_immutable_json(directory / "completion.json", dict(classification=record["classification"],
                           artifacts={name: q.digest(directory / name) for name in names}))
    phase("complete")


def status(root):
    """Independent replay: unfinished attempts can never be accepted/advanced."""
    binding = verify_binding(root)
    attempts, used, archives = [], 0., 0
    directories = sorted(root.glob("call-*"))
    if len(directories) > f.LIMITS["max_launches"]:
        raise ValueError("launch ceiling exceeded")
    for index, directory in enumerate(directories):
        number = index+1
        if directory.name != f"call-{number:03d}":
            raise ValueError("noncontiguous attempts")
        row = binding["rows"][index]
        item = dict(arm=row["arm"], group=row["group"], accepted=False)
        if not (directory / "supervision.json").exists():
            if index != len(directories)-1:
                raise ValueError("unsupervised attempt precedes later calls")
            attempts.append(item | dict(classification="unfinished", active_evidence={
                name: q.read(directory / name) if (directory / name).exists() else None
                for name in ("request.json", "launch.json", "phase.json")}))
            break
        sup, req = q.read(directory / "supervision.json"), q.read(directory / "request.json")
        if sup["classification"] not in {"exited", "wall_limit", "rss_limit", "interrupted", "supervisor_failure"}:
            raise ValueError("unknown supervision disposition")
        if (req != request(root, number, req["wall_seconds"]) or isinstance(req["wall_seconds"], bool) or
                not 0 < req["wall_seconds"] <= min(f.LIMITS["wall_seconds"], f.LIMITS["total_worker_seconds"]-used) or
                isinstance(sup["wall_seconds"], bool) or not np.isfinite(sup["wall_seconds"]) or sup["wall_seconds"] < 0):
            raise ValueError("attempt request/budget mismatch")
        used += sup["wall_seconds"]
        resources = q.resource_evidence(directory, sup)  # same fixed 16 GiB ceiling
        item.update(classification=sup["classification"], wall_seconds=sup["wall_seconds"],
                    peak_sampled_rss_mib=sup["peak_sampled_rss_mib"])
        completion_path, result_path = directory / "completion.json", directory / "result.json.gz"
        if completion_path.exists() and not result_path.exists():
            raise ValueError("completed manifest/archive disagreement")
        if result_path.exists() and not completion_path.exists():
            if sup["classification"] == "exited" and sup["returncode"] == 0:
                raise ValueError("successful worker missing completion")
            # An atomic result can precede its completion by a few milliseconds.
            # Supervised interruption/limits may land in that publication gap.
            # Preserve the draft without replaying or accepting it. A finalized
            # timeout still consumes its arm; other dispositions stop the parent.
            item.update(archive_state="incomplete_publication", partial_archive_sha256=q.digest(result_path))
        if completion_path.exists():
            archives += 1
            completion, record = q.read(completion_path), q.read(result_path)
            expected = {"request.json", "result.json.gz"} | ({"native.json.gz"} if "native" in record else set())
            if set(completion["artifacts"]) != expected:
                raise ValueError("incomplete completion manifest")
            for name, sha in completion["artifacts"].items():
                if q.digest(directory / name) != sha:
                    raise ValueError("completed archive/hash mismatch")
            if (record["classification"] != completion["classification"] or record["arm"] != number or
                    record["iteration"] != number or record["request"] != req or
                    record["execution_context"] != binding["context"] or record["optimizer_calls"] not in (0, 1)):
                raise ValueError("record/completion/execution mismatch")
            if record["classification"] not in {"accepted", "rejected", "exception"}:
                raise ValueError("unknown worker classification")
            if record["classification"] == "accepted" and "checks" not in record:
                raise ValueError("accepted record lacks audits")
            item.update(worker_classification=record["classification"], exception=record["exception"])
            if "native" in record:
                captured = q.read(directory / "native.json.gz")
                if (record["optimizer_calls"] != 1 or captured["iteration"] != number or
                        captured["native"] != record["native"] or captured["canonical"] != row["canonical"]):
                    raise ValueError("record lacks bound native evidence")
            if record["classification"] == "rejected" and (record["exception"] is not None or
                    "native" not in record or record["native"]["status"] == "Solved" and "checks" not in record):
                raise ValueError("rejected record lacks solver rejection or audits")
            if "checks" in record:
                captured = q.read(directory / "native.json.gz")
                if (record["optimizer_calls"] != 1 or record["exception"] is not None or
                        record["native"]["status"] != "Solved" or captured["iteration"] != number or
                        captured["native"] != record["native"] or captured["canonical"] != row["canonical"]):
                    raise ValueError("audited record lacks bound native evidence")
                call, kwargs, view, stress, problem = checked_construction(row)
                replay = assess(view, kwargs, stress, record["native"],
                                record["preparation_evidence"], problem.signature)
                if replay != record["checks"] or replay["passed"] != (record["classification"] == "accepted"):
                    raise ValueError("independent physical/economic audit mismatch")
                item["checks"] = replay
                item["accepted"] = bool(replay["passed"] and resources and sup["classification"] == "exited"
                                        and sup["returncode"] == 0 and sup["wall_seconds"] <= req["wall_seconds"])
        elif sup["classification"] == "exited" and sup["returncode"] == 0:
            raise ValueError("successful worker missing completed archive")
        attempts.append(item)
    return dict(expected_arms=len(f.arms()), launches=len(attempts), completed_archives=archives,
                retained_result_archives=sum((d / "result.json.gz").exists() for d in directories),
                retained_completion_manifests=sum((d / "completion.json").exists() for d in directories),
                disposed=sum(a["classification"] != "unfinished" for a in attempts),
                accepted=sum(a["accepted"] for a in attempts), worker_seconds=used,
                accepted_with_cycling_warnings=sum(a["accepted"] and a["checks"]["economics"]["cycling_gap_warning"]
                                                   for a in attempts),
                budget_exhausted=used >= f.LIMITS["total_worker_seconds"], attempts=attempts)


def run(root, commit):
    binding = frozen_binding()
    if not binding["context"]["clean"] or binding["context"]["commit"] != commit or len(commit) != 40:
        raise ValueError("a reviewed clean full execution commit is required")
    if any(os.environ.get(key) != "1" for key in THREAD_KEYS):
        raise ValueError("single-thread environment required")
    telemetry = diagnostic.monitoring()
    root.mkdir(parents=True, exist_ok=False)
    q.atomic_immutable_json(root / "binding.json", binding)
    q.atomic_immutable_json(root / "protocol.json", protocol())
    q.atomic_immutable_json(root / "invocation-start.json", dict(utc=q.utc(), telemetry=telemetry))
    outcome, exception = "failure", None
    try:
        for number in range(1, len(f.arms())+1):
            progress = status(root)
            remaining = f.LIMITS["total_worker_seconds"]-progress["worker_seconds"]
            if progress["launches"] != progress["disposed"]:
                raise ValueError("unfinished attempt cannot advance")
            if (root / "STOP").exists() or remaining <= 0:
                outcome = "operator_stop" if (root / "STOP").exists() else "budget_exhausted"
                break
            telemetry = diagnostic.monitoring()
            directory = root / f"call-{number:03d}"
            directory.mkdir()
            q.atomic_immutable_json(directory / "telemetry-start.json", telemetry)
            req = request(root, number, min(f.LIMITS["wall_seconds"], remaining))
            q.atomic_immutable_json(directory / "request.json", req)
            sup = q.supervise([sys.executable, "-m", MODULE, "--worker", str(number), "--output", str(root)], directory, root, req)
            progress = status(root)
            q.atomic_json(root / "report.json", progress)
            if sup["classification"] not in {"exited", "wall_limit"} or (sup["classification"] == "exited" and sup["returncode"] != 0):
                outcome = "worker_failure" if sup["classification"] == "exited" else sup["classification"]
                break
            if progress["attempts"][-1].get("worker_classification") == "exception":
                outcome = "worker_exception"
                break
        else:
            outcome = "matrix_complete"
    except KeyboardInterrupt as exc:
        outcome, exception = "operator_stop", str(exc)
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
    group.add_argument("--analyze", action="store_true")
    group.add_argument("--worker", type=int, choices=range(1, len(f.arms())+1), help=argparse.SUPPRESS)
    parser.add_argument("--commit")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.parent != HERE / "results":
        parser.error("output must be a direct child of this experiment's results directory")
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
    elif args.analyze:
        from .analyze import analyze
        print(json.dumps(analyze(root), indent=2))
    else:
        value = frozen_binding()
        print(json.dumps(dict(commit=value["context"]["commit"], clean=value["context"]["clean"],
                              limits=f.LIMITS, gates=f.GATES, rows=[{k: row[k] for k in
                              ("arm", "group", "treatment", "stress", "equivalence")} for row in value["rows"]]), indent=2))


if __name__ == "__main__":
    main()
