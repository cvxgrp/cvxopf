"""Bind, run, stop, resume, and report the paired AC benchmark."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import gzip
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib, _terminate
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_gzip_json, atomic_immutable_json, atomic_json, sha256_path,
)


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE / "results"
PACKAGE = "experiments.ac_spatial_vectorization"
THREADS = dict.fromkeys(
    ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"), "1",
)
TERMINAL = {"accepted", "measured", "native_rejection", "audit_rejection", "wall_limit", "rss_limit"}
WORKER_REPORTS = TERMINAL - {"wall_limit", "rss_limit"}


def utc():
    return datetime.now(timezone.utc).isoformat()


def stop_requests(root):
    return sorted((root / "stops").glob("pending-*.json"))


def request_stop(root):
    atomic_immutable_json(root / "stops" / f"pending-{uuid4().hex}.json", dict(utc=utc()))


def read(path):
    if path.suffix == ".gz":
        with gzip.open(path, "rt") as stream:
            return json.load(stream)
    return json.loads(path.read_text())


def publish(directory, request, outcome, **payload):
    """Publish the payload first, then the immutable integrity receipt."""
    archive = directory / "result.json.gz"
    atomic_gzip_json(archive, dict(iteration=request["ordinal"], outcome=outcome,
                                  request_sha256=sha256_path(directory / "request.json"), **payload))
    starts = [directory / name for name in ("start.json.gz", "verified-start.json.gz")]
    atomic_immutable_json(directory / "completion.json", dict(
        utc=utc(), outcome=outcome, archive_sha256=sha256_path(archive),
        request_sha256=sha256_path(directory / "request.json"),
        starts={path.name: sha256_path(path) for path in starts if path.exists()},
    ))


def check_publication(directory, phase):
    completion = read(directory / "completion.json")
    archive = directory / "result.json.gz"
    request_sha = sha256_path(directory / "request.json")
    if (completion["request_sha256"] != request_sha or
            completion["archive_sha256"] != sha256_path(archive)):
        raise ValueError(f"Publication integrity mismatch: {directory}")
    starts = [directory / name for name in ("start.json.gz", "verified-start.json.gz")]
    if completion["starts"] != {path.name: sha256_path(path) for path in starts if path.exists()}:
        raise ValueError(f"Starting-point archive changed: {directory}")
    record = read(archive)
    allowed = {"measured"} if phase == "compile" else WORKER_REPORTS - {"measured"}
    if (record["request_sha256"] != request_sha or record["outcome"] != completion["outcome"] or
            record["outcome"] not in allowed):
        raise ValueError(f"Publication disposition mismatch: {directory}")
    if record["outcome"] in {"accepted", "audit_rejection"} and (
        record["audit"]["passed"] is not (record["outcome"] == "accepted") or
        record["metrics"]["native_status"] != 0
    ):
        raise ValueError(f"Audit and disposition disagree: {directory}")
    if record["outcome"] == "accepted":
        if len(completion["starts"]) != 2:
            raise ValueError(f"Accepted solve lacks actual and verified start receipts: {directory}")
        if read(directory / "verified-start.json.gz")["physical_start_sha256"] != record["metrics"]["start_sha256"]:
            raise ValueError(f"Verified start and result disagree: {directory}")
    return record


def native_log(path):
    """Final native statistics only; partial iteration lines are not a total."""
    output = {"iterations": None, "ipopt_seconds": None, "exit": None}
    if path.exists():
        with path.open(errors="replace") as stream:
            for line in stream:
                if found := re.match(r"Number of Iterations\.*:\s*(\d+)", line):
                    output["iterations"] = int(found[1])
                if found := re.match(r"Total seconds in IPOPT\s*=\s*([0-9.]+)", line):
                    output["ipopt_seconds"] = float(found[1])
                if line.startswith("EXIT:"):
                    output["exit"] = line.removeprefix("EXIT:").strip()
    return output


def read_rss(pid):
    if sys.platform.startswith("linux"):
        return _child_rss_mib(pid)
    sample = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)],
                            capture_output=True, text=True, timeout=2.)
    return float(sample.stdout) / 1024. if sample.returncode == 0 and sample.stdout.strip() else None


def supervise(command, directory, root, limits, env, lock_fd, *, rss_reader=read_rss):
    """Run one child. As in E3, log to disk and reap before publishing supervision."""
    began = time.monotonic()
    deadline = began + limits["wall_seconds"]
    record = dict(started_utc=utc(), disposition="launch_failure", peak_rss_mib=None,
                  samples=0, returncode=None)
    process = None
    read_fd, write_fd = os.pipe()
    try:
        with (directory / "worker.log").open("xb") as log:
            if stop_requests(root):
                raise KeyboardInterrupt
            process = subprocess.Popen(
                [*command, str(read_fd)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(lock_fd, read_fd),
            )
            atomic_immutable_json(directory / "launch.json", dict(pid=process.pid, utc=utc()))
            record["disposition"] = "supervisor_failure"
            with (directory / "resources.jsonl").open("x") as stream:
                while True:
                    exited = process.poll() is not None
                    observed = time.monotonic()
                    if observed >= deadline:
                        record["disposition"] = "wall_limit"
                        break
                    if exited:
                        record["disposition"] = "exited"
                        break
                    if stop_requests(root):
                        if process.poll() is not None:
                            continue  # classify the finished child before honoring the stop
                        raise KeyboardInterrupt
                    try:
                        rss = rss_reader(process.pid)
                    except (OSError, ValueError, subprocess.SubprocessError) as exc:
                        record["monitoring_error"] = str(exc)
                        rss = None
                    if time.monotonic() >= deadline:
                        record["disposition"] = "wall_limit"
                        break
                    if rss is None or not math.isfinite(rss) or rss <= 0:
                        if process.poll() is not None:
                            continue
                        record["disposition"] = "monitoring_failure"
                        break
                    print(json.dumps(dict(utc=utc(), elapsed=time.monotonic()-began, rss_mib=rss)),
                          file=stream, flush=True)
                    record["samples"] += 1
                    record["peak_rss_mib"] = max(record["peak_rss_mib"] or 0, rss)
                    if rss > limits["rss_mib"]:
                        record["disposition"] = "rss_limit"
                        break
                    time.sleep(min(limits["poll_seconds"], max(0, deadline - time.monotonic())))
    except KeyboardInterrupt:
        record["disposition"] = "operator_stop"
    except Exception as exc:
        record["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        os.close(read_fd)
        os.close(write_fd)
        if process is not None:
            if process.poll() is None:
                _terminate(process)
            record["returncode"] = process.wait()
        record.update(ended_utc=utc(), worker_seconds=time.monotonic() - began)
        atomic_immutable_json(directory / "supervision.json", record)
    return record


def directories(root):
    return sorted((root / "attempts").glob("attempt-*"))


def reserve(root, ordinal, request):
    parent = root / "attempts"
    parent.mkdir(exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".reserve-", dir=parent))
    atomic_immutable_json(staging / "request.json", request)
    directory = parent / f"attempt-{ordinal:06d}"
    staging.rename(directory)
    return directory


def status(root):
    """Project immutable receipts; progress.json is only a convenience copy."""
    binding = read(root / "binding.json")
    binding_sha = sha256_path(root / "binding.json")
    work = {u["id"]: u for u in binding["work"]}
    attempts, terminal = [], {}
    for directory in directories(root):
        request_path = directory / "request.json"
        if not request_path.exists():
            raise ValueError(f"Attempt lacks its reservation: {directory}")
        req = read(request_path)
        unit = work[req["id"]]
        ordinal = len(attempts) + 1
        if (req != {**unit, "binding_sha256": binding_sha, "ordinal": ordinal} or
                directory.name != f"attempt-{ordinal:06d}" or unit["id"] in terminal):
            raise ValueError(f"Attempt identity differs from the binding: {directory}")
        attempt = dict(**unit, ordinal=ordinal, path=str(directory.relative_to(root)), disposition="pending")
        supervision = directory / "supervision.json"
        if supervision.exists():
            sup = read(supervision)
            attempt.update(sup)
            if sup["disposition"] == "exited":
                if sup["returncode"] == 0 and sup["samples"] > 0 and (directory / "completion.json").exists():
                    result = check_publication(directory, unit["phase"])
                    attempt.update(disposition=result["outcome"], metrics=result["metrics"])
                else:
                    attempt["disposition"] = ("monitoring_failure" if not sup["samples"]
                                               else "worker_failure")
            if unit["phase"] == "solve":
                attempt["native_log"] = native_log(directory / "worker.log")
        else:
            for key, name in (("phase_status", "phase.json"), ("launch", "launch.json")):
                if (directory / name).exists():
                    attempt[key] = read(directory / name)
            samples = directory / "resources.jsonl"
            if samples.exists():
                rows = samples.read_text().splitlines(keepends=True)
                if rows and not rows[-1].endswith("\n"):
                    rows.pop()
                if rows:
                    attempt["sample"] = json.loads(rows[-1])
        attempts.append(attempt)
        if attempt["disposition"] in TERMINAL:
            terminal[unit["id"]] = attempt
    return dict(binding_sha256=binding_sha, attempts=attempts, terminal=terminal,
                pending=[u for u in work.values() if u["id"] not in terminal],
                accepted_solves=sum(a["disposition"] == "accepted" for a in terminal.values()),
                worker_seconds=sum(a.get("worker_seconds", 0.) for a in attempts),
                unknown_effort_attempts=sum(a.get("unknown_effort", False) for a in attempts),
                limits=binding["settings"]["limits"])


def reconcile(root, limits):
    """Called with the exclusive lock, inherited by live workers until exit."""
    for directory in directories(root):
        path = directory / "supervision.json"
        if not path.exists():
            # No final resource/exit receipt: retain partial evidence but do not
            # credit a worker-only result. Charge the ceiling as an estimate.
            atomic_immutable_json(path, dict(
                disposition="interrupted_supervisor", returncode=None, samples=0, peak_rss_mib=None,
                worker_seconds=limits["wall_seconds"], unknown_effort=True,
                accounting="charged_attempt_ceiling_not_measured", ended_utc=utc(),
            ))


def execute(root, expected, *, resume, command_for, validate, rss_reader=read_rss, after=None):
    """Run/reconcile under a single supervisor lock; command injection is test-only."""
    with (root / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        prior_stops = stop_requests(root) if resume else []
        binding = read(root / "binding.json")
        if expected != sha256_path(root / "binding.json"):
            raise ValueError("Execution authorization does not match binding SHA-256")
        if directories(root) and not resume:
            raise ValueError("Use explicit resume for a started run")
        validate(binding)
        limits = binding["settings"]["limits"]
        if resume:
            reconcile(root, limits)
            for pending in prior_stops:
                pending.rename(pending.with_name(pending.name.replace("pending-", "acknowledged-", 1)))
        invocation = root / "invocations" / str(time.time_ns())
        atomic_immutable_json(invocation / "start.json", dict(utc=utc(), binding_sha256=expected, resume=resume))
        outcome = "infrastructure_failure"
        diagnostic = {}
        try:
            while True:
                progress = status(root)
                atomic_json(root / "progress.json", progress)
                if not progress["pending"] or stop_requests(root):
                    outcome = "complete" if not progress["pending"] else "operator_stop"
                    break
                unit = progress["pending"][0]
                ordinal = len(progress["attempts"]) + 1
                directory = root / "attempts" / f"attempt-{ordinal:06d}"
                try:
                    command, env = command_for(root, directory, unit)
                except Exception as exc:
                    outcome = "prelaunch_failure"
                    diagnostic = dict(error=f"{type(exc).__name__}: {exc}", pending_unit=unit["id"])
                    break
                directory = reserve(root, ordinal, dict(
                    **unit, ordinal=ordinal, binding_sha256=expected,
                ))
                sup = supervise(command, directory, root, limits, env, lock.fileno(), rss_reader=rss_reader)
                if after is not None:
                    after(directory)
                progress = status(root)
                atomic_json(root / "progress.json", progress)
                if unit["id"] not in progress["terminal"]:
                    outcome = ("operator_stop" if sup["disposition"] == "operator_stop"
                               else progress["attempts"][-1]["disposition"])
                    break
        except KeyboardInterrupt:
            outcome = "operator_stop"
        finally:
            finish = dict(utc=utc(), outcome=outcome, **diagnostic)
            atomic_immutable_json(invocation / "finish.json", finish)
            atomic_json(root / "outcome.json", finish)
        return outcome


def supervise_signals(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def command_for(root, directory, unit):
    source = root / "sources" / unit["revision"] / "src"
    env = dict(os.environ, **THREADS, PYTHONPATH=str(source))
    return [sys.executable, "-m", f"{PACKAGE}.worker", str(root), directory.name], env


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bind", "start", "resume", "status", "report", "stop"))
    parser.add_argument("output", type=Path)
    parser.add_argument("--settings", type=Path)
    parser.add_argument("--case500", type=Path)
    parser.add_argument("--authorize-execution", metavar="BINDING_SHA256")
    args = parser.parse_args()
    root = args.output.resolve()
    if root.parent != RESULTS.resolve() or not root.name.startswith("benchmark_"):
        parser.error("use a benchmark_* directory under this experiment's results/")
    if args.action == "bind":
        from .binding import bind
        if args.settings is None:
            parser.error("bind requires --settings")
        print(bind(root, args.settings, args.case500))
    elif args.action == "stop":
        if not (root / "binding.json").exists():
            parser.error("unknown run")
        request_stop(root)
    elif args.action in {"start", "resume"}:
        from .binding import verify_context, verify_harness
        if args.authorize_execution is None:
            parser.error("start/resume require separate --authorize-execution BINDING_SHA256")

        def checked_command(root, directory, unit):
            verify_harness(read(root / "binding.json")["harness"])
            return command_for(root, directory, unit)

        signal.signal(signal.SIGTERM, supervise_signals)
        outcome = execute(root, args.authorize_execution, resume=args.action == "resume",
                          command_for=checked_command, validate=lambda b: verify_context(root, b))
        print(outcome)
        if outcome not in {"complete", "operator_stop"}:
            raise SystemExit(1)
    else:
        snapshot = status(root)
        if args.action == "report":
            from .report import compare
            snapshot = compare(root, snapshot)
        print(json.dumps(snapshot, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
