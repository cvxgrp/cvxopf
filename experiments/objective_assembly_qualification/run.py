"""Prepare a four-formulation grouping comparison; no execution without approval.

Preflight is non-solving and creates no run directory. Fresh execution requires
a reviewed clean full commit, power/telemetry checks, and separate owner approval.
No resume, retry, production mutation, or historical evidence rewrite is offered.
"""
# ruff: noqa: E402 -- numerical imports follow CLI thread limits.

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

import cvxpy as cp
from cvxopf import extract_results
from experiments.ac_cost_coordinates.run import monitoring
from experiments.convex_cost_qualification import model as convex_model
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable
from . import model as f

HERE = Path(__file__).resolve().parent
MODULE = "experiments.objective_assembly_qualification.run"
OUTPUT = HERE / "results/qualification_001"
ARTIFACTS = ("request.json", "canonical.json.gz", "native.json.gz", "result.json.gz")


def context():
    value = q.context()
    # q binds all tracked Python. Include this not-yet-committed checkpoint too.
    value["objective_assembly_qualification_sources"] = {
        str(p.relative_to(original.ROOT)): q.digest(p) for p in sorted(HERE.iterdir())
        if p.suffix in {".py", ".md"}}
    value["coordinate_policy_sha256"] = q.digest(
        original.ROOT / "experiments/convex_cost_qualification/COORDINATE_REAUDIT_PROTOCOL.md")
    return value


def protocol():
    return dict(limits=f.LIMITS, protocol_sha256=q.digest(HERE / "PROTOCOL.md"))


def frozen_binding():
    prepared = original.e3.verified_inputs()
    rows = [f.row_binding(arm, prepared) for arm in f.arms()]
    for a, b in zip(rows[::2], rows[1::2], strict=True):
        for key in ("stress", "physical_start", "algebra_point"):
            if a[key] != b[key]:
                raise ValueError(f"unmatched input/start/policy pair: {key}")
        left, right = (r["frozen"] for r in (a, b))
        if ({k: v for k, v in left.items() if k != "call"} !=
                {k: v for k, v in right.items() if k != "call"} or
                {k: v for k, v in left["call"].items() if k != "id"} !=
                {k: v for k, v in right["call"].items() if k != "id"}):
            raise ValueError("unmatched physical input/solver/policy pair")
    from experiments.case118_tracy_2021.prepare import SOURCE
    from experiments.case118_annual_hierarchy.pglib_case import SOURCE_CASE_PATH
    paths = (SOURCE, SOURCE_CASE_PATH, original.ROOT / "experiments/case118_tracy_2021/stage_a/manifest.json")
    return dict(context=context(), rows=rows,
                raw_inputs={str(p.relative_to(original.ROOT)): q.digest(p) for p in paths})


def verify_binding(root):
    binding = q.read(root / "binding.json")
    if (binding["context"] != context() or q.read(root / "protocol.json") != protocol()
            or [r["arm"] for r in binding["rows"]] != [asdict(a) for a in f.arms()]):
        raise ValueError("source/environment/protocol/matrix binding changed")
    for name, digest in binding["raw_inputs"].items():
        if q.digest(original.ROOT / name) != digest:
            raise ValueError("raw input hash changed")
    return binding


def checked_construction(binding, number):
    arm = f.arms()[number-1]
    if f.row_binding(arm) != binding["rows"][number-1]:
        raise ValueError("input/start/policy/canonical data differs from frozen arm")
    call, kwargs, view, stress, _, _ = f.construct(arm)
    return arm, call, kwargs, view, stress, f.canonical(view)


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
                isinstance(req["wall_seconds"], bool) or
                not 0 < req["wall_seconds"] <= f.LIMITS["wall_seconds"]):
            raise ValueError("invalid worker launch/request")
        binding = verify_binding(root)
        phase("construct")
        arm, call, kwargs, view, stress, problem = checked_construction(binding, number)
        canonical = f.signature(view, problem)
        q.atomic_gzip_json(directory / "canonical.json.gz", dict(iteration=number, canonical=canonical))

        def observe(value):
            record["native"] = value
            q.atomic_gzip_json(directory / "native.json.gz", dict(iteration=number, arm=number, native=value))

        phase("solve")
        record["optimizer_calls"] = 1
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                if arm.formulation == "ac":
                    try:
                        view.solver.solve(**original.solver_options("ac"))
                    finally:
                        record["preparation_evidence"] = evidence_record(view.solver.preparation_evidence)
                        if record["preparation_evidence"] is not None:
                            observe(record["preparation_evidence"]["native"])
                else:
                    record["preparation_evidence"] = convex_model.solve_observed(view, problem,
                        original.solver_options(call.formulation), observe)
            except cp.error.SolverError as exc:
                # A recorded native numerical rejection is a result, not an
                # infrastructure exception and not proof of infeasibility.
                success = 0 if arm.formulation == "ac" else "Solved"
                if record.get("native", {}).get("status", success) == success:
                    raise
                record["solver_exception"] = str(exc)
                record["preparation_evidence"] = evidence_record(view.solver.preparation_evidence)
            finally:
                record["warnings"] = [dict(category=w.category.__name__, message=str(w.message)) for w in caught]
        if "native" not in record:
            raise ValueError("public solve lacks retained native evidence")
        phase("audit")
        if record["native"]["status"] == (0 if arm.formulation == "ac" else "Solved"):
            # Capture public output before independent replay overwrites leaves.
            published = serializable(extract_results(view.solver))
            record["published"] = published
            record["checks"] = f.assess(arm, record["native"], record["preparation_evidence"], published)
        verify_binding(root)
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["classification"] = ("exception" if record["exception"] else
                                "accepted" if record.get("checks", {}).get("passed") else "rejected")
    phase("archive")
    q.atomic_gzip_json(directory / "result.json.gz", serializable(record))
    q.atomic_immutable_json(directory / "completion.json", dict(utc=q.utc(), artifacts={
        name: q.digest(directory / name) for name in ARTIFACTS if (directory / name).exists()}))
    phase("complete")


def status(root):
    """Independent replay; archive counts never stand in for accepted cases."""
    binding = verify_binding(root)
    directories = sorted(root.glob("call-*"))
    if len(directories) > len(f.arms()) or [p.name for p in directories] != [
            f"call-{n:03d}" for n in range(1, len(directories)+1)]:
        raise ValueError("noncontiguous or excess launches")
    rows, used = [], 0.
    counts = dict(result_archives=0, native_archives=0, completion_manifests=0, finalized=0,
                  accepted=0, rejected=0, cycling_warnings=0)
    for number, directory in enumerate(directories, 1):
        for field, name in (("result_archives", "result.json.gz"), ("native_archives", "native.json.gz"),
                            ("completion_manifests", "completion.json")):
            counts[field] += int((directory / name).exists())
        req = q.read(directory / "request.json")
        if (req != request(root, number, req["wall_seconds"]) or isinstance(req["wall_seconds"], bool)
                or not 0 < req["wall_seconds"] <= min(f.LIMITS["wall_seconds"], f.LIMITS["total_worker_seconds"]-used)):
            raise ValueError("invalid attempt resource request")
        row = dict(arm=asdict(f.arms()[number-1]), classification="unfinished", accepted=False)
        rows.append(row)
        if not (directory / "supervision.json").exists():
            if number != len(directories):
                raise ValueError("cannot advance unfinished attempt")
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
        if not (directory / "completion.json").exists():
            if sup["classification"] == "exited" and sup["returncode"] == 0:
                raise ValueError("normal exit lacks completion manifest")
            continue
        completion = q.read(directory / "completion.json")
        expected = {name: q.digest(directory / name) for name in ARTIFACTS if (directory / name).exists()}
        if completion["artifacts"] != expected or "result.json.gz" not in expected:
            raise ValueError("completion manifest/archive mismatch")
        record = q.read(directory / "result.json.gz")
        if record["arm"] != number or record["request"] != req or record["optimizer_calls"] not in (0, 1):
            raise ValueError("worker attempt record mismatch")
        arm, _, _, _, _, _ = checked_construction(binding, number)
        if "canonical.json.gz" in expected:
            if q.read(directory / "canonical.json.gz") != dict(iteration=number,
                    canonical=binding["rows"][number-1]["canonical"]):
                raise ValueError("retained canonical data differs from binding")
        elif record["optimizer_calls"]:
            raise ValueError("optimizer call lacks canonical evidence")
        if "native.json.gz" in expected:
            if q.read(directory / "native.json.gz") != dict(iteration=number, arm=number, native=record["native"]):
                raise ValueError("native/result archive mismatch")
        elif record.get("checks") is not None:
            raise ValueError("audited result lacks native archive")
        if record.get("checks") is not None:
            checks = f.assess(arm, record["native"], record["preparation_evidence"], record["published"])
            if checks != record["checks"]:
                raise ValueError("retained audit differs from independent replay")
        expected_class = ("exception" if record["exception"] else
                          "accepted" if record.get("checks", {}).get("passed") else "rejected")
        if record["classification"] != expected_class:
            raise ValueError("worker classification disagrees with evidence")
        row["accepted"] = bool(expected_class == "accepted" and record["optimizer_calls"] == 1 and resources
            and sup["classification"] == "exited" and sup["returncode"] == 0
            and elapsed <= req["wall_seconds"] and used <= f.LIMITS["total_worker_seconds"])
        row.update(worker_classification=expected_class, exception=record["exception"], checks=record.get("checks"))
        counts["accepted"] += int(row["accepted"])
        counts["rejected"] += int(expected_class == "rejected")
        counts["cycling_warnings"] += int(f.warning(record.get("checks", {})))
    pairs = []
    by_id = {row["arm"]["id"]: row for row in rows}
    for left, right in zip(f.arms()[::2], f.arms()[1::2], strict=True):
        a, b = (by_id.get(arm.id, dict(arm=asdict(arm), accepted=False, classification="not_launched"))
                for arm in (left, right))
        pair = dict(arms=[a["arm"]["id"], b["arm"]["id"]], both_accepted=a["accepted"] and b["accepted"])
        pair["outcomes"] = [row.get("worker_classification", row["classification"]) for row in (a, b)]
        if pair["both_accepted"]:
            x, y = (f.physical_cost(row["checks"]) for row in (a, b))
            pair.update(physical_objectives=[x, y], component_first_minus_hourly_cost=y-x)
        pairs.append(pair)
    per_formulation = {}
    for form in f.FORMS:
        subset = [row for row in rows if row["arm"]["formulation"] == form]
        per_formulation[form] = dict(expected_cases=8,
            finalized=sum(row["classification"] != "unfinished" for row in subset),
            accepted=sum(row["accepted"] for row in subset),
            rejected=sum(row.get("worker_classification") == "rejected" for row in subset),
            timeouts=sum(row["classification"] == "wall_limit" for row in subset),
            cycling_warnings=sum(f.warning(row.get("checks", {})) for row in subset))
    return dict(expected_cases=len(f.arms()), launches=len(directories), **counts,
        timeouts=sum(row["classification"] == "wall_limit" for row in rows),
        exceptions=sum(row.get("worker_classification") == "exception" for row in rows),
        worker_seconds=used, remaining_worker_seconds=max(0., f.LIMITS["total_worker_seconds"]-used),
        attempts=rows, pairs=pairs, per_formulation=per_formulation)


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
            if sup["classification"] == "wall_limit":
                continue  # Prespecified outcome; continue comparison, never retry.
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
        print(json.dumps(dict(commit=value["context"]["commit"], clean=value["context"]["clean"],
            limits=f.LIMITS, rows=[dict(arm=r["arm"], policy=r["frozen"]["policy"],
            input_sha256=r["frozen"]["mathematical_input_sha256"]) for r in value["rows"]]), indent=2))


if __name__ == "__main__":
    main()
