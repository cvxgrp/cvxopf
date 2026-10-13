"""Run the owner-approved four-case comparison; --status never optimizes."""

# ruff: noqa: E402 -- thread environment must precede numerical imports.
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
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
from experiments.case118_tracy_2021 import e3_prepared, run_e3
from experiments.numerical_preparation import run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable
from experiments.tracy_battery_transfer import model as transfer
from experiments.tracy_battery_transfer_starts import run as recovery
from . import model as m

MODULE = "experiments.tracy_battery_transfer_slice.run"
OUTPUT = m.HERE / "results/slice_001"
ARTIFACTS = (
    "request.json",
    "monitoring.json",
    "raw-start.json",
    "start.json",
    "x0.json.gz",
    "native.json.gz",
    "preparation.json.gz",
    "result.json.gz",
)
PREVIOUS = (
    "tracy_battery_transfer/results/transfer_001",
    "tracy_battery_transfer_starts/results/starts_001",
    "tracy_battery_transfer_starts/results/starts_002",
    "tracy_battery_transfer_feasibility/results/feasibility_001",
)


def context():
    value = q.context()
    value["slice_sources"] = {
        str(p.relative_to(transfer.ROOT)): q.digest(p)
        for p in sorted(m.HERE.iterdir())
        if p.suffix in (".py", ".md") and p.name != "REPORT.md"
    }
    return value


def historical_hashes():
    return {
        str(p.relative_to(transfer.ROOT)): q.digest(p)
        for name in PREVIOUS
        for p in sorted((transfer.ROOT / "experiments" / name).rglob("*"))
        if p.is_file()
    }


def verify(root, *, for_execution=False):
    """Replay may outlive a commit; execution retains exact frozen context."""
    from experiments.tracy_socp_transfer_preflight.historical_replay import (
        historical_context_matches,
    )

    binding = q.read(root / "binding.json")
    current = context()
    context_matches = (
        current == binding["context"]
        if for_execution
        else historical_context_matches(binding["context"], current)
    )
    if (
        not context_matches
        or historical_hashes() != binding["historical_hashes"]
        or binding["limits"] != m.LIMITS
        or q.read(root / "protocol.json") != protocol()
    ):
        raise ValueError(
            "bound source/environment/historical evidence/protocol changed"
        )
    if "predecessor" in binding:
        check_predecessor_files(binding["predecessor"])
    return binding


def protocol():
    return dict(
        protocol=m.LIMITS,
        ac_options=e3_prepared.DEFAULT_PROTOCOL["ac_options"],
        protocol_sha256=q.digest(m.HERE / "PROTOCOL.md"),
    )


def checked(binding, number):
    view = m.construct(
        number, transfer.source_inventory(), e3_prepared.verified_inputs()
    )
    captured = e3_prepared.prepared_start(view[0], number)
    if (
        dict(view[-1], canonical_start_sha256=transfer.object_hash(captured))
        != binding["rows"][number - 1]
    ):
        raise ValueError("slice/input/start/handoff differs from frozen case")
    return view, captured


def predecessor_binding(origin, current):
    """Bind only the authorized completed two-case prefix, without rewriting it."""
    origin = origin.resolve()
    if origin != OUTPUT.resolve():
        raise ValueError("only the finalized slice_001 prefix can be inherited")
    previous = q.read(origin / "binding.json")
    old_context = dict(previous["context"])
    new_context = dict(current["context"])
    old_sources = old_context.pop("slice_sources")
    new_sources = new_context.pop("slice_sources")
    if (
        old_context != new_context
        or previous["rows"] != current["rows"]
        or previous["limits"] != m.LIMITS
        or previous["historical_hashes"] != current["historical_hashes"]
    ):
        raise ValueError(
            "continuation would change numerical environment, inputs or budgets"
        )
    model_path = str((m.HERE / "model.py").relative_to(transfer.ROOT))
    if old_sources[model_path] != new_sources[model_path]:
        raise ValueError("slice model changed across bookkeeping correction")
    snapshot = origin.parent / "slice_001-source"
    source_files = {
        str((snapshot / Path(path).name).relative_to(transfer.ROOT)): digest
        for path, digest in old_sources.items()
    }
    if any(
        q.digest(transfer.ROOT / path) != digest
        for path, digest in source_files.items()
    ):
        raise ValueError("original diagnostic source snapshot differs")
    if q.read(origin / "invocation-finish.json") != dict(
        exception="ValueError: independent audit differs",
        outcome="failure",
        utc=q.read(origin / "invocation-finish.json")["utc"],
    ):
        raise ValueError("predecessor lacks expected finalized bookkeeping failure")
    if [p.name for p in sorted(origin.glob("call-*"))] != ["call-001", "call-002"]:
        raise ValueError("predecessor is not exactly the completed two-case prefix")
    old_protocol = q.read(origin / "protocol.json")
    if (
        old_protocol["protocol"] != m.LIMITS
        or old_protocol["ac_options"] != e3_prepared.DEFAULT_PROTOCOL["ac_options"]
        or old_protocol["protocol_sha256"]
        != old_sources[str((m.HERE / "PROTOCOL.md").relative_to(transfer.ROOT))]
    ):
        raise ValueError("predecessor numerical protocol differs")
    return dict(
        path=str(origin.relative_to(transfer.ROOT)),
        files={
            str(p.relative_to(origin)): q.digest(p)
            for p in sorted(origin.rglob("*"))
            if p.is_file()
        },
        source_snapshot_files=source_files,
        completed_calls=2,
        reader_compatibility="missing native_status on rejected predecessor call 2 only",
    )


def check_predecessor_files(predecessor):
    origin = transfer.ROOT / predecessor["path"]
    files = {
        str(p.relative_to(origin)): q.digest(p)
        for p in sorted(origin.rglob("*"))
        if p.is_file()
    }
    if files != predecessor["files"] or any(
        q.digest(transfer.ROOT / path) != digest
        for path, digest in predecessor["source_snapshot_files"].items()
    ):
        raise ValueError("immutable predecessor/source snapshot changed")


def freeze(origin=None):
    data, prepared = transfer.source_inventory(), e3_prepared.verified_inputs()
    rows = []
    witness = None
    for number in range(1, 5):
        view = m.construct(number, data, prepared)
        if number == 1:
            witness = m.start_audit(view)
            if not witness["passed"]:
                raise ValueError(
                    "retained AC control slice fails pre-solve physical audit"
                )
        captured = e3_prepared.prepared_start(view[0], number)
        rows.append(
            dict(view[-1], canonical_start_sha256=transfer.object_hash(captured))
        )
    value = dict(
        context=context(),
        limits=m.LIMITS,
        rows=rows,
        historical_hashes=historical_hashes(),
        retained_control_witness=witness,
        authorization="owner: Yes, I agree. I want to see that test",
    )
    if origin is not None:
        value["authorization"] = (
            "owner: fix bookkeeping and run remaining two cases without rerunning completed solves"
        )
        value["predecessor"] = predecessor_binding(origin, value)
        inherited = replay(origin, value, inherited_only=True)
        if (
            inherited["launches"] != 2
            or inherited["finalized"] != 2
            or inherited["next_call"] != 3
            or [a["classification"] for a in inherited["attempts"]]
            != ["accepted", "native_rejection"]
            or any(not a["resources_verified"] for a in inherited["attempts"])
        ):
            raise ValueError(
                "predecessor prefix does not independently qualify for remaining cases"
            )
        value["predecessor"]["audited_prefix"] = inherited
    json.dumps(value, allow_nan=False)
    return value


def classify(record):
    if record["exception"]:
        return "infrastructure_failure"
    if record["native"]["status"] != 0:
        return "native_rejection"
    return "accepted" if record["checks"]["passed"] else "audit_failure"


def worker(root, number):
    directory = root / f"call-{number:03d}"
    request = q.read(directory / "request.json")
    record = dict(
        iteration=number,
        request=request,
        exception=None,
        optimizer_calls=0,
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
            raise ValueError("worker lacks current supervised launch authority")
        binding = verify(root, for_execution=True)
        if request != make_request(root, number, request["wall_seconds"]):
            raise ValueError("worker request differs")
        phase("construct")
        view, captured = checked(binding, number)
        build, kwargs, powers, raw, assigned, row = view
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
                if (
                    build.preparation_evidence is None
                    or build.preparation_evidence.native.get("status") == 0
                ):
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
            raise ValueError("native/preparation evidence missing")
        record["native"] = record["preparation_evidence"]["native"]
        for name, key in (
            ("native.json.gz", "native"),
            ("preparation.json.gz", "preparation_evidence"),
        ):
            q.atomic_gzip_json(
                directory / name, dict(iteration=number, data=record[key])
            )
        e3_prepared.verify_prepared_start(build.preparation_evidence, captured)
        phase("audit")
        if record["native"]["status"] == 0:
            record["result"] = serializable(extract_results(build))
            record["named_costs"] = {
                k: float(v.value)
                for k, v in build.expressions.items()
                if k.endswith("_cost")
            }
            fresh, _ = checked(binding, number)
            record["checks"] = transfer.assess(
                fresh[0], kwargs, powers, assigned, row, record, captured
            )
            record["summary"] = m.summarize(record["result"], kwargs)
        else:
            record["checks"] = dict(
                passed=False,
                reason="native numerical rejection",
                native_status=record["native"]["status"],
            )
        verify(root, for_execution=True)
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


def make_request(root, number, wall):
    return dict(
        call_id=number,
        role="single_hour_battery_transfer",
        wall_seconds=wall,
        protocol=q.reference(root / "protocol.json", root),
    )


def next_call(attempts):
    if not attempts:
        return 1
    if attempts[0]["classification"] != "accepted":
        return None
    if attempts[-1]["classification"] not in (
        "accepted",
        "native_rejection",
        "wall_limit",
    ):
        return None
    return len(attempts) + 1 if len(attempts) < 4 else None


def status(root):
    binding = verify(root)
    return replay(root, binding)


def archived_checks(record, *, legacy=False):
    """Read compatibility for the known rejected prefix; never accept it."""
    checks = record["checks"]
    if (
        legacy
        and record["iteration"] == 2
        and record["native"]["status"] == 2
        and record["classification"] == "native_rejection"
        and checks == dict(passed=False, reason="native numerical rejection")
    ):
        return dict(checks, native_status=2)
    return checks


def replay(root, binding, *, inherited_only=False):
    attempts = []
    used = 0.0
    inherited = []
    if "predecessor" in binding:
        origin = transfer.ROOT / binding["predecessor"]["path"]
        inherited = sorted(origin.glob("call-*"))
    directories = inherited + ([] if inherited_only else sorted(root.glob("call-*")))
    if len(directories) > 4:
        raise ValueError("launch ceiling exceeded")
    for number, directory in enumerate(directories, 1):
        archive_root = directory.parent
        request = q.read(directory / "request.json")
        if (
            directory.name != f"call-{number:03d}"
            or next_call(attempts) != number
            or request != make_request(archive_root, number, request["wall_seconds"])
            or not 0 < request["wall_seconds"] <= min(300.0, 1200.0 - used)
        ):
            raise ValueError("launch sequence/request/budget differs")
        item = dict(
            call_id=number,
            donor=m.FORMS[number - 1],
            classification="unfinished",
            accepted=False,
        )
        if not (directory / "supervision.json").exists():
            if number != len(directories):
                raise ValueError("unfinished attempt precedes another launch")
            attempts.append(item)
            break
        sup = q.read(directory / "supervision.json")
        used += sup["wall_seconds"]
        resource = (
            q.resource_evidence(directory, sup)
            and sup["peak_sampled_rss_mib"] <= m.LIMITS["rss_mib"]
        )
        item.update(
            classification=sup["classification"],
            worker_seconds=sup["wall_seconds"],
            peak_sampled_rss_mib=sup["peak_sampled_rss_mib"],
            resources_verified=resource,
        )
        if (directory / "completion.json").exists():
            completion = q.read(directory / "completion.json")
            for name, digest in completion["artifacts"].items():
                if Path(name).name != name or q.digest(directory / name) != digest:
                    raise ValueError("completion archive/hash mismatch")
            record = q.read(directory / "result.json.gz")
            if (
                not {"request.json", "result.json.gz"} <= set(completion["artifacts"])
                or record["request"] != request
                or record["iteration"] != number
                or record["binding_sha256"] != q.digest(archive_root / "binding.json")
                or classify(record) != record["classification"]
                or completion["classification"] != record["classification"]
            ):
                raise ValueError("result/request/manifest identity differs")
            if record["classification"] != "infrastructure_failure":
                if (
                    not set(ARTIFACTS) <= set(completion["artifacts"])
                    or record["optimizer_calls"] != 1
                ):
                    raise ValueError("completed numerical attempt lacks evidence")
                view, captured = checked(binding, number)
                if (
                    record["row"] != view[-1]
                    or captured != q.read(directory / "x0.json.gz")
                    or q.read(directory / "start.json")["assigned_start"]
                    != serializable(view[4])
                    or q.read(directory / "raw-start.json")["raw_start"]
                    != serializable(view[3])
                    or record["native"] != record["preparation_evidence"]["native"]
                    or q.read(directory / "native.json.gz")
                    != dict(iteration=number, data=record["native"])
                    or q.read(directory / "preparation.json.gz")
                    != dict(iteration=number, data=record["preparation_evidence"])
                ):
                    raise ValueError("input/start/native/preparation archives disagree")
                replay = transfer.assess(
                    view[0], view[1], view[2], view[4], view[-1], record, captured
                )
                if replay != archived_checks(record, legacy=directory in inherited):
                    raise ValueError("independent audit differs")
                if record["native"]["status"] == 0:
                    summary = m.summarize(record["result"], view[1])
                    if summary != record["summary"]:
                        raise ValueError("summary differs")
                    item.update(summary=summary, checks=replay)
                item["native_status"] = record["native"]["status"]
            if sup["classification"] == "exited":
                item["classification"] = (
                    record["classification"]
                    if sup["returncode"] == 0 and resource
                    else "infrastructure_failure"
                )
                if sup["wall_seconds"] > request["wall_seconds"]:
                    item["classification"] = "wall_limit"
                item["accepted"] = item["classification"] == "accepted"
        elif sup["classification"] == "exited":
            item["classification"] = "infrastructure_failure"
        attempts.append(item)
    return dict(
        hour=m.HOUR,
        launches=len(directories),
        inherited_launches=len(inherited),
        local_launches=len(directories) - len(inherited),
        finalized=sum(a["classification"] != "unfinished" for a in attempts),
        accepted=sum(a["accepted"] for a in attempts),
        worker_seconds=used,
        attempts=attempts,
        next_call=next_call(attempts) if used < 1200 else None,
    )


def supervise(command, directory, root, request):
    """Existing supervision mechanics with this test's explicit 8-GiB ceiling."""
    began = time.monotonic()
    process = None
    record = dict(
        classification="supervisor_failure",
        started_utc=q.utc(),
        returncode=None,
        peak_sampled_rss_mib=0.0,
        samples=0,
    )
    try:
        if (root / "STOP").exists():
            raise KeyboardInterrupt("operator stop")
        with (directory / "worker.log").open("xb") as log:
            process = subprocess.Popen(
                command,
                cwd=transfer.ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            q.atomic_immutable_json(
                directory / "launch.json", dict(pid=process.pid, started_utc=q.utc())
            )
            with (directory / "resources.jsonl").open("x") as journal:
                while process.poll() is None:
                    if (root / "STOP").exists():
                        raise KeyboardInterrupt("operator stop")
                    elapsed = time.monotonic() - began
                    if elapsed >= request["wall_seconds"]:
                        record["classification"] = "wall_limit"
                        break
                    rss = q._child_rss_mib(process.pid)
                    if rss is None:
                        if process.poll() is not None:
                            break
                        raise RuntimeError("live RSS monitoring unavailable")
                    if not math.isfinite(rss) or rss < 0:
                        raise RuntimeError("invalid RSS sample")
                    journal.write(
                        json.dumps(
                            dict(utc=q.utc(), elapsed_seconds=elapsed, rss_mib=rss)
                        )
                        + "\n"
                    )
                    journal.flush()
                    record["samples"] += 1
                    record["peak_sampled_rss_mib"] = max(
                        record["peak_sampled_rss_mib"], rss
                    )
                    if rss > m.LIMITS["rss_mib"]:
                        record["classification"] = "rss_limit"
                        break
                    time.sleep(min(1.0, max(0.001, request["wall_seconds"] - elapsed)))
            if (
                process.poll() is not None
                and record["classification"] == "supervisor_failure"
            ):
                record["classification"] = "exited"
    except BaseException as exc:
        record.update(
            classification="interrupted"
            if isinstance(exc, KeyboardInterrupt)
            else "supervisor_failure",
            exception=f"{type(exc).__name__}: {exc}",
        )
    finally:
        if process is not None:
            if process.poll() is None:
                run_e3._terminate(process)
            record["returncode"] = process.wait()
        record.update(wall_seconds=time.monotonic() - began, ended_utc=q.utc())
        q.atomic_immutable_json(directory / "supervision.json", record)


def run(root, origin=None):
    if root.resolve().parent != (m.HERE / "results").resolve():
        raise ValueError("output must be a new child of this experiment results")
    if origin is not None and root.resolve() == origin.resolve():
        raise ValueError(
            "continuation must use a fresh output, not rewrite its predecessor"
        )
    binding = freeze(origin)
    telemetry = recovery.monitoring()
    root.mkdir(parents=True, exist_ok=False)
    q.atomic_immutable_json(root / "binding.json", binding)
    q.atomic_immutable_json(root / "protocol.json", protocol())
    q.atomic_immutable_json(
        root / "invocation-start.json", dict(utc=q.utc(), telemetry=telemetry)
    )
    outcome, exception = "failure", None
    try:
        while True:
            verify(root, for_execution=True)
            progress = status(root)
            q.atomic_json(root / "report.json", progress)
            number = progress["next_call"]
            if number is None:
                outcome = (
                    "four_case_complete" if progress["finalized"] == 4 else "stopped"
                )
                break
            telemetry = recovery.monitoring()
            directory = root / f"call-{number:03d}"
            directory.mkdir()
            request = make_request(
                root, number, min(300.0, 1200.0 - progress["worker_seconds"])
            )
            q.atomic_immutable_json(directory / "request.json", request)
            q.atomic_immutable_json(directory / "monitoring.json", telemetry)
            supervise(
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
                request,
            )
    except BaseException as exc:
        exception = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        q.atomic_immutable_json(
            root / "invocation-finish.json",
            dict(utc=q.utc(), outcome=outcome, exception=exception),
        )
    return progress


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--worker", type=int)
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--continue-from", type=Path)
    args = parser.parse_args()
    if args.worker is not None:
        worker(args.output, args.worker)
    elif args.status:
        print(json.dumps(status(args.output), indent=2))
    elif args.preflight:
        binding = freeze(args.continue_from)
        print(
            json.dumps(
                dict(
                    preflight="passed",
                    control_witness_passed=True,
                    rows=binding["rows"],
                    limits=m.LIMITS,
                    inherited_launches=binding.get("predecessor", {}).get(
                        "completed_calls", 0
                    ),
                    next_call=binding.get("predecessor", {})
                    .get("audited_prefix", {})
                    .get("next_call", 1),
                ),
                indent=2,
            )
        )
    elif args.execute:
        print(json.dumps(run(args.output, args.continue_from), indent=2))
    else:
        parser.error("choose --preflight, --status or --execute")


if __name__ == "__main__":
    main()
