"""Prepare or execute the reviewed 18-case battery schedule transfer experiment.

Preflight and status never optimize. Execution requires a clean full commit and
separate owner approval; no retries, recovery ladder, or resume are offered.
"""
# ruff: noqa: E402 -- CLI thread limits precede numerical imports.

import argparse
import json
import math
import os
from pathlib import Path
import signal
import sys
import time
import warnings

THREAD_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

import cvxpy as cp
from cvxopf import extract_results
from experiments.ac_cost_coordinates.run import monitoring
from experiments.case118_tracy_2021 import e3_prepared
from experiments.numerical_preparation import run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable
from . import model as m

MODULE = "experiments.tracy_battery_transfer.run"
OUTPUT = m.HERE / "results/transfer_001"
ARTIFACTS = (
    "request.json",
    "monitoring.json",
    "start.json",
    "x0.json.gz",
    "native.json.gz",
    "preparation.json.gz",
    "result.json.gz",
)


def context():
    value = q.context()
    value["battery_transfer_sources"] = {
        str(path.relative_to(m.ROOT)): q.digest(path)
        for path in sorted(m.HERE.iterdir())
        if path.suffix in {".py", ".md"}
    }
    return value


def protocol():
    return dict(
        protocol=dict(
            m.LIMITS, ac_options=dict(e3_prepared.DEFAULT_PROTOCOL["ac_options"])
        ),
        protocol_sha256=q.digest(m.HERE / "PROTOCOL.md"),
    )


def retained_hashes(data):
    base = m.ROOT / "experiments/case118_tracy_2021/results"
    values = {
        str((base / name).relative_to(m.ROOT)): digest
        for name, digest in data["hashes"].items()
    }
    for run in data["runs"]:
        for name in ("binding.json", "progress.json"):
            path = run["directory"] / name
            values[str(path.relative_to(m.ROOT))] = q.digest(path)
    return values


def frozen_binding():
    data, prepared = m.source_inventory(), e3_prepared.verified_inputs()
    rows = []
    for job in m.jobs(data):
        build, _, _, _, row = m.construct(job, data, prepared)
        # Exercise the actual prepared graph and canonical handoff, never solve.
        captured = e3_prepared.prepared_start(build, job["id"])
        row["canonical_start_sha256"] = m.object_hash(captured)
        rows.append(row)
    value = serializable(
        dict(
            context=context(), rows=rows, sources=retained_hashes(data), limits=m.LIMITS
        )
    )
    json.dumps(value, allow_nan=False)
    return value


def verify_binding(root):
    binding = q.read(root / "binding.json")
    if (
        binding["context"] != context()
        or q.read(root / "protocol.json") != protocol()
        or binding["limits"] != m.LIMITS
    ):
        raise ValueError("source/environment/protocol binding changed")
    data = m.source_inventory()
    if binding["sources"] != retained_hashes(data) or [
        row["job"] for row in binding["rows"]
    ] != m.jobs(data):
        raise ValueError("retained source evidence or transfer matrix changed")
    return binding, data


def checked_construction(binding, data, prepared, number):
    if type(number) is not int or not 1 <= number <= m.LIMITS["max_launches"]:
        raise ValueError("invalid transfer number")
    job = m.jobs(data)[number - 1]
    build, kwargs, schedule, start, row = m.construct(job, data, prepared)
    captured = e3_prepared.prepared_start(build, number)
    row["canonical_start_sha256"] = m.object_hash(captured)
    if row != binding["rows"][number - 1]:
        raise ValueError(
            "input/schedule/start/canonical graph differs from frozen case"
        )
    return build, kwargs, schedule, start, row, captured


def request(root, number, wall):
    return dict(
        call_id=number,
        role="battery_schedule_transfer",
        wall_seconds=wall,
        protocol=q.reference(root / "protocol.json", root),
    )


def validate_request(root, number, req, remaining):
    wall = req["wall_seconds"]
    if (
        isinstance(wall, bool)
        or not isinstance(wall, (int, float))
        or not math.isfinite(wall)
        or not 0 < wall <= min(m.LIMITS["wall_seconds"], remaining)
        or req != request(root, number, wall)
    ):
        raise ValueError("invalid request or launch resource budget")


def worker(root, number):
    directory = root / f"call-{number:03d}"
    req = q.read(directory / "request.json")
    record = dict(
        iteration=number,
        call_id=number,
        request=req,
        optimizer_calls=0,
        exception=None,
        binding_sha256=q.digest(root / "binding.json"),
    )
    began = time.monotonic()

    def phase(name):
        q.atomic_json(
            directory / "phase.json",
            dict(phase=name, utc=q.utc(), elapsed_seconds=time.monotonic() - began),
        )

    try:
        deadline = time.monotonic() + 5
        while not (directory / "launch.json").exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if (
            q.read(directory / "launch.json")["pid"] != os.getpid()
            or (directory / "supervision.json").exists()
            or (root / "STOP").exists()
        ):
            raise ValueError("worker lacks current launch authority")
        validate_request(root, number, req, m.LIMITS["total_worker_seconds"])
        binding, data = verify_binding(root)
        phase("construct")
        build, kwargs, schedule, start, row, captured = checked_construction(
            binding, data, e3_prepared.verified_inputs(), number
        )
        record["row"] = row
        q.atomic_immutable_json(
            directory / "start.json", dict(assigned_start=serializable(start))
        )
        q.atomic_gzip_json(directory / "x0.json.gz", captured)
        phase("solve")
        record["optimizer_calls"] = 1
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                build.solve(**e3_prepared.DEFAULT_PROTOCOL["ac_options"])
            except cp.error.SolverError as exc:
                evidence = build.preparation_evidence
                if evidence is None or evidence.native.get("status") == 0:
                    raise
                record["solver_exception"] = str(exc)
            finally:
                record["warnings"] = [
                    dict(category=w.category.__name__, message=str(w.message))
                    for w in caught
                ]
                record["preparation_evidence"] = evidence_record(
                    build.preparation_evidence
                )
        if record["preparation_evidence"] is None:
            raise ValueError("solve did not retain preparation/native evidence")
        record["native"] = record["preparation_evidence"]["native"]
        q.atomic_gzip_json(
            directory / "native.json.gz", dict(iteration=number, data=record["native"])
        )
        q.atomic_gzip_json(
            directory / "preparation.json.gz",
            dict(iteration=number, data=record["preparation_evidence"]),
        )
        e3_prepared.verify_prepared_start(build.preparation_evidence, captured)
        phase("audit")
        if record["native"]["status"] == 0:
            record["result"] = serializable(extract_results(build))
            record["named_costs"] = {
                key: float(value.value)
                for key, value in build.expressions.items()
                if key.endswith("_cost")
            }
            # Construct a separate fresh graph for the independent inverse replay.
            fresh = m.construct(row["job"], data, e3_prepared.verified_inputs())[0]
            record["checks"] = m.assess(
                fresh, kwargs, schedule, start, row, record, captured
            )
        else:
            record["checks"] = dict(
                passed=False,
                reason="native numerical rejection",
                native_status=record["native"]["status"],
            )
        verify_binding(root)
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    record["classification"] = classify(record)
    phase("archive")
    q.atomic_gzip_json(directory / "result.json.gz", serializable(record))
    q.atomic_immutable_json(
        directory / "completion.json",
        dict(
            utc=q.utc(),
            classification=record["classification"],
            artifacts={
                name: q.digest(directory / name)
                for name in ARTIFACTS
                if (directory / name).exists()
            },
        ),
    )
    phase("complete")


def classify(record):
    if record["exception"] is not None:
        return "exception"
    if record.get("native", {}).get("status") != 0:
        return "native_rejection"
    return "compatible" if record.get("checks", {}).get("passed") else "audit_failure"


def status(root):
    """Read-only graph reconstruction and native replay, including partial tails."""
    binding, data = verify_binding(root)
    directories = sorted(root.glob("call-*"))
    if len(directories) > m.LIMITS["max_launches"] or [
        path.name for path in directories
    ] != [f"call-{n:03d}" for n in range(1, len(directories) + 1)]:
        raise ValueError("excess or noncontiguous attempts")
    prepared = e3_prepared.verified_inputs() if directories else None
    rows, used = [], 0.0
    counts = {
        key: 0
        for key in (
            "finalized",
            "compatible",
            "native_rejections",
            "audit_failures",
            "timeouts",
            "cycling_warnings",
            "result_archives",
            "native_archives",
            "preparation_archives",
            "completion_manifests",
        )
    }
    for number, directory in enumerate(directories, 1):
        row = dict(
            job=binding["rows"][number - 1]["job"],
            classification="unfinished",
            compatible=False,
        )
        rows.append(row)
        for field, name in (
            ("result_archives", "result.json.gz"),
            ("native_archives", "native.json.gz"),
            ("preparation_archives", "preparation.json.gz"),
            ("completion_manifests", "completion.json"),
        ):
            counts[field] += int((directory / name).exists())
        if not (directory / "request.json").exists():
            if number != len(directories) or (directory / "supervision.json").exists():
                raise ValueError("missing request in a finalized or advanced attempt")
            continue
        req = q.read(directory / "request.json")
        validate_request(root, number, req, m.LIMITS["total_worker_seconds"] - used)
        if not (directory / "supervision.json").exists():
            if number != len(directories):
                raise ValueError("cannot advance unfinished attempt")
            continue
        sup = q.read(directory / "supervision.json")
        if sup["classification"] not in {
            "exited",
            "wall_limit",
            "rss_limit",
            "interrupted",
            "supervisor_failure",
        }:
            raise ValueError("unknown finalized supervision outcome")
        elapsed = sup["wall_seconds"]
        if isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid supervised worker duration")
        used += elapsed
        counts["finalized"] += 1
        counts["timeouts"] += int(sup["classification"] == "wall_limit")
        row.update(
            classification=sup["classification"],
            worker_seconds=elapsed,
            peak_sampled_rss_mib=sup["peak_sampled_rss_mib"],
        )
        resources = q.resource_evidence(directory, sup)
        row["resource_evidence_verified"] = resources
        if not (directory / "completion.json").exists():
            if sup["classification"] == "exited" and sup["returncode"] == 0:
                raise ValueError("normal worker exit lacks completion")
            continue
        completion = q.read(directory / "completion.json")
        expected = {
            name: q.digest(directory / name)
            for name in ARTIFACTS
            if (directory / name).exists()
        }
        if completion["artifacts"] != expected or "result.json.gz" not in expected:
            raise ValueError("completion manifest/archive inconsistency")
        record = q.read(directory / "result.json.gz")
        if (
            record["iteration"] != number
            or record["call_id"] != number
            or record["request"] != req
            or type(record["optimizer_calls"]) is not int
            or record["optimizer_calls"] not in (0, 1)
            or record.get("binding_sha256") != q.digest(root / "binding.json")
            or record["classification"] != classify(record)
            or completion["classification"] != record["classification"]
        ):
            raise ValueError(
                "worker identity/classification differs from retained evidence"
            )
        if record["classification"] != "exception":
            if (
                sup["classification"] == "exited"
                and sup["returncode"] == 0
                and not resources
            ):
                raise ValueError("numerical outcome lacks verified resource evidence")
            if not set(ARTIFACTS) <= set(expected) or record["optimizer_calls"] != 1:
                raise ValueError("numerical outcome lacks required evidence")
            build, kwargs, schedule, start, frozen, captured = checked_construction(
                binding, data, prepared, number
            )
            if (
                record["row"] != frozen
                or q.read(directory / "start.json")
                != dict(assigned_start=serializable(start))
                or q.read(directory / "x0.json.gz") != captured
                or q.read(directory / "native.json.gz")
                != dict(iteration=number, data=record["native"])
                or q.read(directory / "preparation.json.gz")
                != dict(iteration=number, data=record["preparation_evidence"])
            ):
                raise ValueError("source/start/native/preparation evidence differs")
            checks = m.assess(build, kwargs, schedule, start, frozen, record, captured)
            if checks != record["checks"]:
                raise ValueError("independent replay audit differs from archive")
        eligible = bool(
            record["classification"] == "compatible"
            and resources
            and sup["classification"] == "exited"
            and sup["returncode"] == 0
            and elapsed <= req["wall_seconds"]
            and used <= m.LIMITS["total_worker_seconds"]
        )
        row.update(
            worker_classification=record["classification"],
            compatible=eligible,
            exception=record["exception"],
            checks=record.get("checks"),
        )
        counts["compatible"] += int(eligible)
        counts["native_rejections"] += int(
            record["classification"] == "native_rejection"
        )
        counts["audit_failures"] += int(record["classification"] == "audit_failure")
        counts["cycling_warnings"] += int(
            record.get("checks", {}).get("economics", {}).get("cycling_warning", False)
        )
        if eligible:
            reference = data["cases"][row["job"]["scenario"]]["ac"]
            row["comparison"] = m.comparison(
                record["result"], record["named_costs"], reference
            )
    return dict(
        expected_cases=18,
        launches=len(directories),
        **counts,
        attempts=rows,
        worker_seconds=used,
        remaining_worker_seconds=max(0.0, m.LIMITS["total_worker_seconds"] - used),
        per_donor={
            donor: dict(
                finalized=sum(
                    r["classification"] != "unfinished"
                    for r in rows
                    if r["job"]["donor"] == donor
                ),
                compatible=sum(
                    r["compatible"] for r in rows if r["job"]["donor"] == donor
                ),
            )
            for donor in m.DONORS
        },
    )


def run(root, commit):
    binding = frozen_binding()
    if (
        binding["context"]["clean"] is not True
        or binding["context"]["commit"] != commit
        or not isinstance(commit, str)
        or len(commit) != 40
        or any(c not in "0123456789abcdef" for c in commit)
    ):
        raise ValueError("reviewed clean full commit required before execution")
    if any(binding["context"]["thread_environment"][key] != "1" for key in THREAD_KEYS):
        raise ValueError("single-thread numerical environment required")
    telemetry = monitoring()
    root.mkdir(parents=True, exist_ok=False)
    q.atomic_immutable_json(root / "binding.json", binding)
    q.atomic_immutable_json(root / "protocol.json", protocol())
    q.atomic_immutable_json(
        root / "invocation-start.json", dict(utc=q.utc(), telemetry=telemetry)
    )
    outcome, exception = "failure", None
    try:
        for number in range(1, m.LIMITS["max_launches"] + 1):
            progress = status(root)
            if progress["launches"] != progress["finalized"]:
                raise ValueError("unfinished attempt cannot advance")
            remaining = progress["remaining_worker_seconds"]
            if (root / "STOP").exists() or remaining <= 0:
                outcome = (
                    "operator_stop" if (root / "STOP").exists() else "budget_exhausted"
                )
                break
            telemetry = monitoring()
            directory = root / f"call-{number:03d}"
            directory.mkdir()
            q.atomic_immutable_json(directory / "monitoring.json", telemetry)
            req = request(root, number, min(m.LIMITS["wall_seconds"], remaining))
            q.atomic_immutable_json(directory / "request.json", req)
            sup = q.supervise(
                [
                    sys.executable,
                    "-m",
                    MODULE,
                    "--worker",
                    str(number),
                    "--output",
                    str(root),
                ],
                directory,
                root,
                req,
            )
            progress = status(root)
            q.atomic_json(root / "report.json", progress)
            if sup["classification"] == "wall_limit":
                continue
            if sup["classification"] != "exited" or sup["returncode"] != 0:
                outcome = "supervision_failure"
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
        q.atomic_immutable_json(
            root / "invocation-finish.json",
            dict(utc=q.utc(), outcome=outcome, exception=exception),
        )
    return dict(outcome=outcome, output=str(root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true")
    group.add_argument("--authorize-execution", action="store_true")
    group.add_argument("--status", action="store_true")
    group.add_argument(
        "--worker", type=int, choices=range(1, 19), help=argparse.SUPPRESS
    )
    parser.add_argument("--commit")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    root = args.output.resolve()
    if root.parent != (m.HERE / "results").resolve():
        parser.error(
            "output must be a fresh direct child of this experiment's results directory"
        )
    if args.authorize_execution and args.commit is None:
        parser.error("execution requires --commit and separate owner approval")
    if args.worker:
        worker(root, args.worker)
        return
    if args.preflight:
        value = frozen_binding()
        result = dict(
            classification="preflight_only",
            cases=len(value["rows"]),
            limits=m.LIMITS,
            clean=value["context"]["clean"],
            commit=value["context"]["commit"],
            rows=[
                dict(job=row["job"], source_precheck=row["source_precheck"])
                for row in value["rows"]
            ],
        )
    elif args.status:
        result = status(root)
    else:
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, q.interrupted)
        result = run(root, args.commit)
    print(json.dumps(serializable(result), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
