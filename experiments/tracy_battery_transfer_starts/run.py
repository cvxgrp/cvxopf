"""Bounded alternative-start ladder; preflight/status never optimize.

Execution requires separate owner approval and a reviewed clean full commit.
No resume, continuation, unpinning, or changes to historical results.
"""
# ruff: noqa: E402 -- set worker thread limits before numerical imports.

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
from experiments.tracy_battery_transfer import model as transfer, run as original
from . import model as m

MODULE = "experiments.tracy_battery_transfer_starts.run"
OUTPUT = m.HERE / "results/starts_001"
ARTIFACTS = (*original.ARTIFACTS, "raw-start.json")
COUNTED_ARTIFACTS = (*ARTIFACTS, "completion.json", "supervision.json")


def validate_output(root):
    if root.resolve().parent != (m.HERE / "results").resolve():
        raise ValueError("output must be a direct child of this experiment's results")


def context():
    value = q.context()
    value["alternative_start_sources"] = {
        str(path.relative_to(transfer.ROOT)): q.digest(path)
        for directory in (m.HERE, transfer.HERE)
        for path in sorted(directory.iterdir())
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


def checked_construction(binding, data, prepared, trial_id):
    if type(trial_id) is not int or not 1 <= trial_id <= m.LIMITS["max_launches"]:
        raise ValueError("invalid trial identity")
    trial = m.registry(data)[trial_id - 1]
    build, kwargs, schedule, raw, assigned, row = m.construct(trial, data, prepared)
    captured = e3_prepared.prepared_start(build, trial_id)
    row["canonical_start_sha256"] = transfer.object_hash(captured)
    if binding is not None and row != binding["rows"][trial_id - 1]:
        raise ValueError("problem/source/start/canonical handoff changed")
    return build, kwargs, schedule, raw, assigned, row, captured


def frozen_binding():
    data = transfer.source_inventory()
    sources = m.origin_hashes(data)
    current = context()
    historical = q.read(m.ORIGIN / "binding.json")["context"]
    for key in ("packages", "installed", "ipopt_library_version"):
        if current[key] != historical[key]:
            raise ValueError("historical numerical environment changed")
    if any(
        q.digest(transfer.ROOT / path) != digest
        for path, digest in historical["sources"].items()
    ):
        raise ValueError("historical production sources changed")
    prepared = e3_prepared.verified_inputs()
    rows = [
        checked_construction(None, data, prepared, trial["id"])[5]
        for trial in m.registry(data)
    ]
    return serializable(
        dict(
            context=current,
            sources=sources,
            rows=rows,
            limits=m.LIMITS,
            origin=str(m.ORIGIN),
        )
    )


def verify_binding(root):
    validate_output(root)
    binding = q.read(root / "binding.json")
    if (
        binding["context"] != context()
        or binding["limits"] != m.LIMITS
        or binding["origin"] != str(m.ORIGIN)
        or q.read(root / "protocol.json") != protocol()
    ):
        raise ValueError("source/environment/protocol binding changed")
    data = transfer.source_inventory()
    if binding["sources"] != m.origin_hashes(data) or [
        row["trial"] for row in binding["rows"]
    ] != m.registry(data):
        raise ValueError("historical evidence or ladder changed")
    return binding, data


def next_trial(binding, attempts):
    """Derived ordered ladder: finalized successes skip only their own donor."""
    if attempts:
        last = attempts[-1]
        if (
            last["classification"] == "unfinished"
            or last["classification"] not in {"exited", "wall_limit"}
            or (
                last["classification"] == "exited"
                and (
                    last.get("returncode") != 0
                    or last.get("worker_classification")
                    not in {"compatible", "native_rejection"}
                    or (
                        last.get("worker_classification") == "compatible"
                        and not last["compatible"]
                    )
                )
            )
        ):
            return None
    accepted = {row["trial"]["job"]["id"] for row in attempts if row["compatible"]}
    previous = attempts[-1]["trial"]["id"] if attempts else 0
    return next(
        (
            row["trial"]
            for row in binding["rows"]
            if row["trial"]["id"] > previous and row["job"]["id"] not in accepted
        ),
        None,
    )


def request(root, number, trial, wall):
    return dict(
        call_id=number,
        role="alternative_start",
        trial=trial,
        wall_seconds=wall,
        protocol=q.reference(root / "protocol.json", root),
    )


def validate_request(root, number, trial, req, remaining):
    wall = req.get("wall_seconds")
    if (
        isinstance(wall, bool)
        or not isinstance(wall, (int, float))
        or not math.isfinite(wall)
        or not 0 < wall <= min(m.LIMITS["wall_seconds"], remaining)
        or req != request(root, number, trial, wall)
    ):
        raise ValueError("invalid ladder request/resource budget")


def worker(root, number):
    validate_output(root)
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
            or (root / "invocation-finish.json").exists()
        ):
            raise ValueError("worker lacks current launch authority")
        progress = status(root)
        if (
            progress["launches"] != number
            or progress["attempts"][-1]["classification"] != "unfinished"
        ):
            raise ValueError("worker is not the current ordered launch")
        binding, data = verify_binding(root)
        phase("construct")
        build, kwargs, schedule, raw, assigned, row, captured = checked_construction(
            binding, data, e3_prepared.verified_inputs(), req["trial"]["id"]
        )
        record["row"] = row
        q.atomic_immutable_json(
            directory / "raw-start.json", dict(raw_start=serializable(raw))
        )
        q.atomic_immutable_json(
            directory / "start.json", dict(assigned_start=serializable(assigned))
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
        for name, field in (
            ("native.json.gz", "native"),
            ("preparation.json.gz", "preparation_evidence"),
        ):
            q.atomic_gzip_json(
                directory / name, dict(iteration=number, data=record[field])
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
            fresh = m.construct(req["trial"], data, e3_prepared.verified_inputs())[0]
            record["checks"] = transfer.assess(
                fresh, kwargs, schedule, assigned, row, record, captured
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
    record["classification"] = original.classify(record)
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


def status(root):
    """Read-only independent replay; unfinished/unsupervised candidates never count."""
    binding, data = verify_binding(root)
    directories = sorted(root.glob("call-*"))
    if len(directories) > m.LIMITS["max_launches"] or [
        path.name for path in directories
    ] != [f"call-{n:03d}" for n in range(1, len(directories) + 1)]:
        raise ValueError("excess or noncontiguous launches")
    rows, used = [], 0.0
    prepared = e3_prepared.verified_inputs() if directories else None
    counts = dict(
        finalized=0,
        compatible_attempts=0,
        native_rejections=0,
        audit_failures=0,
        timeouts=0,
        infrastructure_failures=0,
        cycling_warnings=0,
    )
    artifacts = {name: 0 for name in COUNTED_ARTIFACTS}
    for number, directory in enumerate(directories, 1):
        trial = next_trial(binding, rows)
        if trial is None:
            raise ValueError(
                "launch follows success, stop, unfinished tail or exhausted ladder"
            )
        row = dict(trial=trial, classification="unfinished", compatible=False)
        rows.append(row)
        for name in COUNTED_ARTIFACTS:
            artifacts[name] += int((directory / name).exists())
        if not (directory / "request.json").exists():
            if number != len(directories) or (directory / "supervision.json").exists():
                raise ValueError("missing request before finalized/later launch")
            continue
        req = q.read(directory / "request.json")
        validate_request(
            root, number, trial, req, m.LIMITS["total_worker_seconds"] - used
        )
        if not (directory / "supervision.json").exists():
            continue
        sup = q.read(directory / "supervision.json")
        if sup["classification"] not in {
            "exited",
            "wall_limit",
            "rss_limit",
            "interrupted",
            "supervisor_failure",
        }:
            raise ValueError("unknown supervision outcome")
        elapsed = sup["wall_seconds"]
        if isinstance(elapsed, bool) or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("invalid supervised duration")
        used += elapsed
        resources = q.resource_evidence(directory, sup)
        row.update(
            classification=sup["classification"],
            worker_seconds=elapsed,
            returncode=sup["returncode"],
            peak_sampled_rss_mib=sup["peak_sampled_rss_mib"],
            resource_evidence_verified=resources,
        )
        counts["finalized"] += 1
        counts["timeouts"] += int(sup["classification"] == "wall_limit")
        if not (directory / "completion.json").exists():
            if sup["classification"] == "exited" and sup["returncode"] == 0:
                raise ValueError("normal worker exit lacks completion")
            counts["infrastructure_failures"] += int(
                sup["classification"] != "wall_limit"
            )
            continue
        completion = q.read(directory / "completion.json")
        expected = {
            name: q.digest(directory / name)
            for name in ARTIFACTS
            if (directory / name).exists()
        }
        record = q.read(directory / "result.json.gz")
        if (
            completion["artifacts"] != expected
            or "result.json.gz" not in expected
            or record["call_id"] != number
            or record["iteration"] != number
            or record["request"] != req
            or type(record["optimizer_calls"]) is not int
            or record["optimizer_calls"] not in (0, 1)
            or record["binding_sha256"] != q.digest(root / "binding.json")
            or record["classification"] != original.classify(record)
            or completion["classification"] != record["classification"]
        ):
            raise ValueError("completion/archive/identity inconsistency")
        if record["classification"] != "exception":
            if not set(ARTIFACTS) <= set(expected) or record["optimizer_calls"] != 1:
                raise ValueError("numerical outcome lacks complete evidence")
            build, kwargs, schedule, raw, assigned, frozen, captured = (
                checked_construction(binding, data, prepared, trial["id"])
            )
            if (
                record["row"] != frozen
                or q.read(directory / "raw-start.json")
                != dict(raw_start=serializable(raw))
                or q.read(directory / "start.json")
                != dict(assigned_start=serializable(assigned))
                or q.read(directory / "x0.json.gz") != captured
                or q.read(directory / "native.json.gz")
                != dict(iteration=number, data=record["native"])
                or q.read(directory / "preparation.json.gz")
                != dict(iteration=number, data=record["preparation_evidence"])
            ):
                raise ValueError("problem/start/native/preparation evidence changed")
            if (
                transfer.assess(
                    build, kwargs, schedule, assigned, frozen, record, captured
                )
                != record["checks"]
            ):
                raise ValueError("independent replay audit differs")
            if (
                sup["classification"] == "exited"
                and sup["returncode"] == 0
                and not resources
            ):
                raise ValueError("numerical outcome lacks resource evidence")
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
        counts["compatible_attempts"] += int(eligible)
        counts["native_rejections"] += int(
            record["classification"] == "native_rejection"
        )
        counts["audit_failures"] += int(record["classification"] == "audit_failure")
        counts["infrastructure_failures"] += int(
            record["classification"] == "exception"
            or sup["classification"] not in {"exited", "wall_limit"}
            or (sup["classification"] == "exited" and sup["returncode"] != 0)
        )
        counts["cycling_warnings"] += int(
            record.get("checks", {}).get("economics", {}).get("cycling_warning", False)
        )
        if eligible:
            row["comparison"] = transfer.comparison(
                record["result"],
                record["named_costs"],
                data["cases"][trial["job"]["scenario"]]["ac"],
            )
    cases = []
    for job_id in m.CASE_IDS:
        attempts = [row for row in rows if row["trial"]["job"]["id"] == job_id]
        accepted = next(
            (row["trial"]["id"] for row in attempts if row["compatible"]), None
        )
        exhausted = (
            len(attempts) == 7 and attempts[-1]["classification"] != "unfinished"
        )
        cases.append(
            dict(
                original_case_id=job_id,
                donor=next(
                    row["job"]["donor"]
                    for row in binding["rows"]
                    if row["job"]["id"] == job_id
                ),
                accepted_trial=accepted,
                accepted_slot=None
                if accepted is None
                else binding["rows"][accepted - 1]["trial"]["slot"],
                classification="compatible"
                if accepted
                else "unresolved"
                if exhausted
                else "pending",
                launches=len(attempts),
            )
        )
    executed = {row["trial"]["id"]: row for row in rows}
    accepted_cases = {
        case["original_case_id"] for case in cases if case["accepted_trial"] is not None
    }
    ladder = [
        dict(
            trial=row["trial"],
            disposition=(
                "unfinished"
                if executed[row["trial"]["id"]]["classification"] == "unfinished"
                else "finalized"
            )
            if row["trial"]["id"] in executed
            else (
                "not_needed_after_acceptance"
                if row["job"]["id"] in accepted_cases
                else "not_launched"
            ),
        )
        for row in binding["rows"]
    ]
    return dict(
        expected_cases=3,
        launches=len(directories),
        **counts,
        compatible_cases=sum(case["accepted_trial"] is not None for case in cases),
        artifacts=artifacts,
        cases=cases,
        ladder=ladder,
        invocation_finish=q.read(root / "invocation-finish.json")
        if (root / "invocation-finish.json").exists()
        else None,
        attempts=rows,
        worker_seconds=used,
        remaining_worker_seconds=max(0.0, m.LIMITS["total_worker_seconds"] - used),
        next_trial=next_trial(binding, rows),
    )


def run(root, commit):
    validate_output(root)
    binding = frozen_binding()
    if (
        not isinstance(commit, str)
        or len(commit) != 40
        or any(c not in "0123456789abcdef" for c in commit)
        or binding["context"]["clean"] is not True
        or binding["context"]["commit"] != commit
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
        while True:
            progress = status(root)
            q.atomic_json(root / "report.json", progress)
            if (root / "STOP").exists():
                outcome = "operator_stop"
                break
            trial, remaining = (
                progress["next_trial"],
                progress["remaining_worker_seconds"],
            )
            if trial is None:
                outcome = (
                    "ladder_complete"
                    if all(
                        case["classification"] != "pending"
                        for case in progress["cases"]
                    )
                    else "stopped_failure"
                )
                break
            if remaining <= 0:
                outcome = "budget_exhausted"
                break
            number = progress["launches"] + 1
            telemetry = monitoring()
            directory = root / f"call-{number:03d}"
            directory.mkdir()
            q.atomic_immutable_json(directory / "monitoring.json", telemetry)
            req = request(root, number, trial, min(m.LIMITS["wall_seconds"], remaining))
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
            if sup["classification"] != "wall_limit" and (
                sup["classification"] != "exited"
                or sup["returncode"] != 0
                or progress["attempts"][-1].get("worker_classification")
                in {"exception", "audit_failure"}
            ):
                outcome = "stopped_failure"
                break
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
    group.add_argument("--status", action="store_true")
    group.add_argument("--authorize-execution", action="store_true")
    group.add_argument(
        "--worker", type=int, choices=range(1, 22), help=argparse.SUPPRESS
    )
    parser.add_argument("--commit")
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    root = args.output.resolve()
    validate_output(root)
    if args.authorize_execution and args.commit is None:
        parser.error("execution requires --commit and separate owner approval")
    if args.worker:
        worker(root, args.worker)
        return
    if args.preflight:
        binding = frozen_binding()
        result = dict(
            classification="preflight_only",
            cases=3,
            trials=len(binding["rows"]),
            limits=m.LIMITS,
            clean=binding["context"]["clean"],
            rows=binding["rows"],
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
