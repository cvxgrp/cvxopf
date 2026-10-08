"""Restartable sequential E3 runner; review/commit and authorize before launch.

The immutable attempt journal is authoritative. Runtime protocol revisions
are explicit, hash-bound, and applied only before a new attempt. No live
source edits, cross-version execution, or continuation of IPOPT internals.
"""
# ruff: noqa: E402 -- CLI thread limits must precede numerical imports.

import argparse
from datetime import datetime, timezone
import fcntl
import json
import math
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

from cvxopf import build_opf_multistep
from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib, _terminate
from experiments.case118_annual_hierarchy.streaming_schema import atomic_json, atomic_immutable_json
from . import e3 as model
from .prepare import ROOT, HERE, digest
from .stage_d import read, reference
from .run_stage_d import require_matching_audit
from .ac_archive import verify_x0, verify_logical

referenced = model.referenced

MODULE = "experiments.case118_tracy_2021.run_e3"


def cohort_for(root):
    """Select the declared cohort from immutable binding, never a global patch."""
    cohort = read(root / "binding.json")["study"].get("cohort")
    if cohort == "prepared":
        from . import e3_prepared
        return e3_prepared
    if cohort == "prepared_plus":
        from . import e3_plus
        return e3_plus
    if cohort is not None:
        raise ValueError("unknown E3 cohort")
    return model


def utc():
    return datetime.now(timezone.utc).isoformat()


def queue_protocol(root, protocol, reason):
    """Separate control lock permits queuing while the run lock is held."""
    if not reason or not reason.strip():
        raise ValueError("protocol revision requires an operator reason")
    model = cohort_for(root)
    model.validate_protocol(protocol)
    with (root / "control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        binding = read(root / "binding.json")
        model.verify_context(binding["context"])
        control = read(root / "control.json") if (root / "control.json").exists() else None
        number = 0 if control is None else referenced(root, control["protocol"])["revision"]+1
        record = dict(revision=number, protocol=protocol, reason=reason, utc=utc(),
                      previous=None if control is None else control["protocol"],
                      execution_context=binding["context"])
        path = root / "protocols" / f"revision-{number:03d}.json"
        atomic_immutable_json(path, record)
        ref = reference(path, root)
        atomic_json(root / "control.json", dict(protocol=ref))
    return ref


def protocol_record(root, ref, expected_context):
    """Verify the complete revision lineage, not just the mutable pointer."""
    model = cohort_for(root)
    current = referenced(root, ref)
    model.validate_protocol(current["protocol"])
    tail = current
    while True:
        if tail["execution_context"] != expected_context:
            raise ValueError("protocol revision source/environment mismatch")
        previous = tail["previous"]
        if previous is None:
            if tail["revision"] != 0:
                raise ValueError("broken protocol ancestry")
            break
        older = referenced(root, previous)
        model.validate_protocol(older["protocol"])
        if older["revision"] != tail["revision"]-1:
            raise ValueError("broken protocol ancestry")
        tail = older
    return current


def request_for(arm, slot, causal, target_free, protocol, wall, *, cohort=None):
    if cohort is not None and hasattr(cohort, "request_for"):
        return cohort.request_for(arm, slot, causal, target_free, protocol, wall)
    return dict(arm_id=arm["id"], slot=slot,
        role=model.ROLES[slot] if arm["formulation"] == "ac" else "primary",
        causal_source=causal, target_free_source=target_free,
        protocol=protocol, wall_seconds=wall)


def independent_auditor(root, study):
    model = cohort_for(root)
    if hasattr(model, "independent_auditor"):
        return model.independent_auditor(root, study)
    prepared = model.verified_inputs()

    def check(arm, role, record):
        kwargs = model.kwargs_for_arm(prepared, arm, study["storage_device_ids"], role)
        build = build_opf_multistep(**kwargs) if arm["formulation"] == "socp" else None
        audit, _ = model.audit(build, record["result"], kwargs, record["named_costs"])
        require_matching_audit(audit, record["audit"])
        if arm["formulation"] == "ac" and audit["passed"]:
            verify_logical(record["logical_solution"], record["result"],
                           kwargs["case"]["baseMVA"], kwargs["T"])
        return audit["passed"] and record.get("exception") is None

    return check


def replay(root, auditor=None, *, partial=False):
    """Replay completed evidence; partial status never adopts unfinished work.

    Resume uses the strict default after reconciliation. Partial replay reports
    an unfinished tail without accepting it, deriving seeds, or offering a next
    launch. Its worker_seconds contains only finalized supervision effort.
    """
    binding = read(root / "binding.json")
    model = cohort_for(root)
    prepared_cohort = binding["study"].get("cohort") in {"prepared", "prepared_plus"}
    study = binding["study"]
    # Snapshot directory identities before potentially slow independent audits:
    # status must not combine an earlier pending arm with newly launched arms.
    directories_by_arm = {}
    for directory in sorted(root.glob("arm-*/attempt-*")):
        directories_by_arm.setdefault(directory.parent.name, []).append(directory)
    if prepared_cohort and set(directories_by_arm) - {f"arm-{a['id']:03d}" for a in study["arms"]}:
        raise ValueError("attempt journal contains an undeclared arm")
    auditor = independent_auditor(root, study) if auditor is None else auditor
    active = read(root / "control.json")["protocol"]
    protocol_record(root, active, binding["context"])
    arms, attempts, used, launches, next_item, last_revision = [], [], 0., 0, None, -1
    active_attempt = None

    def unfinished(directory, arm, slot, request):
        if not partial:
            raise ValueError("unreconciled attempt; inspect live worker before resume")
        return dict(path=str(directory.relative_to(root)), arm_id=arm["id"], slot=slot,
            role=model.ROLES[slot % 10] if arm["formulation"] == "ac" else "primary",
            protocol=None if request is None else request["protocol"], request=request,
            classification="awaiting_request" if request is None else "awaiting_supervision",
            accepted=False, wall_seconds=None,
            launch=read(directory / "launch.json") if (directory / "launch.json").exists() else None,
            phase=read(directory / "phase.json") if (directory / "phase.json").exists() else None)

    for arm in study["arms"]:
        slot, causal, target_free, accepted = 0, None, None, None
        count = (20 if prepared_cohort else 10) if arm["formulation"] == "ac" else 1
        directories = directories_by_arm.get(f"arm-{arm['id']:03d}", [])
        if next_item is not None and directories:
            raise ValueError("attempt after an earlier pending arm")
        for number, directory in enumerate(directories):
            if directory.name != f"attempt-{number:03d}" or accepted is not None or slot >= count:
                raise ValueError("attempts are noncontiguous or beyond a terminal arm")
            if partial and not (directory / "request.json").exists():
                if number != len(directories)-1:
                    raise ValueError("unfinished attempt precedes later attempts")
                active_attempt = unfinished(directory, arm, slot, None)
                attempts.append(active_attempt)
                launches += 1
                break
            request = read(directory / "request.json")
            revision = protocol_record(root, request["protocol"], binding["context"])
            if revision["revision"] < last_revision:
                raise ValueError("attempt protocol revision moved backwards")
            last_revision = revision["revision"]
            wall = request["wall_seconds"]
            if isinstance(wall, bool) or not 0 < wall <= revision["protocol"]["wall_seconds"]:
                raise ValueError("invalid effective wall limit")
            limits = revision["protocol"]
            if launches >= limits["max_launches"] or wall > limits["total_worker_seconds"]-used:
                raise ValueError("attempt launched beyond its protocol budget")
            if request != request_for(arm, slot, causal, target_free, request["protocol"], wall, cohort=model):
                raise ValueError("request/ladder/source mismatch")
            if not (directory / "supervision.json").exists():
                if number != len(directories)-1:
                    raise ValueError("unfinished attempt precedes later attempts")
                active_attempt = unfinished(directory, arm, slot, request)
                attempts.append(active_attempt)
                launches += 1
                break
            supervision = read(directory / "supervision.json")
            elapsed = supervision["wall_seconds"]
            if not 0 <= elapsed or not math.isfinite(elapsed):
                raise ValueError("invalid supervision effort")
            launches += 1
            used += elapsed
            classification = supervision["classification"]
            completion = read(directory / "completion.json") if (directory / "completion.json").exists() else None
            if completion:
                required = {"request.json", "result.json.gz"}
                if prepared_cohort and completion["classification"] != "exception":
                    required |= {"native.json.gz", "preparation.json.gz"}
                if arm["formulation"] == "ac" and completion["classification"] != "exception":
                    required |= {"start.json", "x0.json.gz"}
                if not required <= set(completion["artifacts"]):
                    raise ValueError("incomplete worker artifact manifest")
                for name, expected in completion["artifacts"].items():
                    if Path(name).name != name or digest(directory / name) != expected:
                        raise ValueError("worker artifact integrity mismatch")
                record = read(directory / "result.json.gz")
                if record["request"] != request or record["classification"] != completion["classification"]:
                    raise ValueError("archive/completion/request mismatch")
                if record["execution_context"] != binding["context"]:
                    raise ValueError("worker archive execution context mismatch")
                if prepared_cohort and record.get("attempt") != str(directory.relative_to(root)):
                    raise ValueError("prepared archive attempt identity mismatch")
                if prepared_cohort and record["classification"] != "exception":
                    if (read(directory / "native.json.gz") != dict(iteration=arm["id"], data=record["native"]) or
                            read(directory / "preparation.json.gz") != dict(iteration=arm["id"], data=record["preparation_evidence"])):
                        raise ValueError("native/preparation archive disagreement")
                if arm["formulation"] == "ac" and record["classification"] != "exception" and not prepared_cohort:
                    verify_x0(read(directory / "start.json"), read(directory / "x0.json.gz"))
                usable = (classification == "exited" and supervision["returncode"] == 0 and
                          record["classification"] == "accepted")
                if prepared_cohort and record["classification"] != "exception":
                    checked = auditor(arm, request["role"], record)
                    if checked != (record["classification"] == "accepted"):
                        raise ValueError("prepared archive classification differs from replay")
                if usable and not prepared_cohort and not auditor(arm, request["role"], record):
                    raise ValueError("accepted archive failed independent replay audit")
            else:
                record, usable = None, False
                if classification == "exited" and supervision["returncode"] == 0:
                    raise ValueError("successful worker exit lacks completion archive")
            attempts.append(dict(path=str(directory.relative_to(root)), arm_id=arm["id"],
                role=request["role"], protocol=request["protocol"], classification=classification,
                accepted=usable, wall_seconds=elapsed,
                **({"objective_assembly": request["objective_assembly"], "slot": slot,
                    "worker_classification": None if record is None else record["classification"],
                    "cycling_warning": bool(record and record.get("independent_audit", {}).get("economics", {}).get("cycling_warning")),
                    "target_free_diagnostic": request["role"] == "target_free"} if prepared_cohort else {})))
            # The original causal source is captured once, even after an interrupted solve.
            if arm["formulation"] == "ac" and causal is None and (directory / "start.json").exists():
                causal = reference(directory / "start.json", root)
            if usable:
                if request["role"] == "target_free":
                    target_free = reference(directory / "result.json.gz", root)
                else:
                    accepted = reference(directory / "result.json.gz", root)
            # Explicit interruptions retry the same slot in a fresh attempt; numerical
            # rejection/timeout/RSS exhaustion consumes a slot. Every launch costs budget.
            if classification not in ("interrupted", "orphaned_interruption"):
                slot += 1
                if slot % 10 == 6 and target_free is None:
                    slot = (slot // 10 + 1) * 10 if prepared_cohort else count
                if prepared_cohort and slot == 10:
                    target_free = None
        status = "accepted" if accepted else "unresolved" if slot >= count else "pending"
        arms.append(dict(arm=arm, classification=status, accepted=accepted,
                         next_slot=slot, attempts=len(directories)))
        if next_item is None and status == "pending":
            next_item = dict(arm=arm, slot=slot, causal_source=causal, target_free_source=target_free)
    counts = {}
    if prepared_cohort:
        directories = [d for group in directories_by_arm.values() for d in group]
        counts = {key: sum((d / name).exists() for d in directories) for key, name in (
            ("result_archives", "result.json.gz"), ("native_archives", "native.json.gz"),
            ("preparation_archives", "preparation.json.gz"), ("completion_manifests", "completion.json"),
            ("finalized_supervision", "supervision.json"))}
        counts.update(disposed_arms=sum(a["classification"] != "pending" for a in arms),
                      unresolved_arms=sum(a["classification"] == "unresolved" for a in arms))
    return dict(arms=arms, attempts=attempts, launches=launches,
                worker_seconds=used, next=None if active_attempt else next_item,
                active_attempt=active_attempt,
                finished=all(a["classification"] != "pending" for a in arms),
                accepted_arms=sum(a["classification"] == "accepted" for a in arms), **counts)


def reconcile(root):
    """Under parent lock, refuse live orphans and conservatively charge gaps."""
    for directory in sorted(root.glob("arm-*/attempt-*")):
        if (directory / "supervision.json").exists():
            continue
        if (directory / "launch.json").exists():
            pid = read(directory / "launch.json")["pid"]
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(f"possible live orphan PID {pid}; inspect before resuming")
        request = read(directory / "request.json")
        atomic_immutable_json(directory / "supervision.json", dict(
            classification="orphaned_interruption", returncode=None,
            wall_seconds=request["wall_seconds"], accounting="charged_full_attempt_ceiling",
            peak_sampled_rss_mib=None, reconciled_utc=utc()))


def supervise(command, directory, root, request, *, rss_reader=_child_rss_mib):
    began, process = time.monotonic(), None
    record = dict(classification="supervisor_failure", started_utc=utc(),
                  returncode=None, peak_sampled_rss_mib=0., samples=0)
    protocol = referenced(root, request["protocol"])["protocol"]
    try:
        if (root / "STOP").exists():
            raise KeyboardInterrupt("operator stop before launch")
        with (directory / "worker.log").open("xb") as log:
            process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            atomic_immutable_json(directory / "launch.json", dict(pid=process.pid, started_utc=utc()))
            with (directory / "resources.jsonl").open("x") as samples:
                while process.poll() is None:
                    if (root / "STOP").exists():
                        raise KeyboardInterrupt("operator stop requested")
                    elapsed = time.monotonic()-began
                    if elapsed >= request["wall_seconds"]:
                        record["classification"] = "wall_limit"
                        break
                    rss = rss_reader(process.pid)
                    if rss is None:
                        if process.poll() is not None:
                            break
                        raise RuntimeError("RSS monitoring unavailable for live worker")
                    if not math.isfinite(rss) or rss < 0:
                        raise RuntimeError("invalid RSS sample")
                    samples.write(json.dumps(dict(utc=utc(), elapsed_seconds=elapsed, rss_mib=rss))+"\n")
                    samples.flush()
                    record["samples"] += 1
                    record["peak_sampled_rss_mib"] = max(record["peak_sampled_rss_mib"], rss)
                    if rss > 16384:
                        record["classification"] = "rss_limit"
                        break
                    time.sleep(min(protocol["poll_seconds"], max(.001, request["wall_seconds"]-elapsed)))
            if process.poll() is not None and record["classification"] == "supervisor_failure":
                record["classification"] = "exited"
    except BaseException as exc:
        record.update(classification="interrupted" if isinstance(exc, KeyboardInterrupt) else "supervisor_failure",
                      exception=f"{type(exc).__name__}: {exc}")
    finally:
        if process is not None:
            if process.poll() is None:
                _terminate(process)
            record["returncode"] = process.wait()
        record.update(wall_seconds=time.monotonic()-began, ended_utc=utc())
        atomic_immutable_json(directory / "supervision.json", record)
    return record


def run(root, commit, *, resume=False, preflight=False, acknowledge_stop=False, prepared=False, plus=False):
    model = globals()["model"]
    if prepared and plus:
        raise ValueError("select only one E3 cohort")
    if prepared:
        from . import e3_prepared as model
    if plus:
        from . import e3_plus as model
    prepared_cohort = prepared or plus
    if prepared_cohort:
        model.validate_output(root)
    current = model.context()
    if not current["clean"] or current["commit"] != commit or len(commit) != 40:
        raise ValueError("a reviewed clean full execution commit is required")
    if any(current["thread_environment"][k] != "1" for k in THREAD_KEYS):
        raise ValueError("single-thread numerical environment required")
    if _child_rss_mib(os.getpid()) is None:
        raise RuntimeError("preflight RSS/ps access unavailable")
    study = model.specification()
    model.verified_inputs()  # pinned owner data; no silent fallback
    if prepared_cohort:
        model.monitoring()
    expected_binding = dict(context=current, study=study)
    # Exercise the archive's strict JSON contract even in non-writing preflight,
    # before mkdir or any durable run artifacts can leave a partial launch.
    json.dumps(expected_binding, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if preflight:
        if resume:
            binding = read(root / "binding.json")
            if binding != expected_binding:
                raise ValueError("resume binding differs")
        return dict(classification="preflight_only", study=study, context=current)
    if not resume:
        root.mkdir(parents=True, exist_ok=False)
        atomic_immutable_json(root / "binding.json", expected_binding)
        queue_protocol(root, model.DEFAULT_PROTOCOL, "initial reviewed protocol")
    with (root / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if read(root / "binding.json") != expected_binding:
            raise ValueError("resume binding differs; no implicit source transition")
        reconcile(root)
        if acknowledge_stop:
            if not resume:
                raise ValueError("STOP acknowledgement requires explicit resume")
            if (root / "STOP").exists():
                stops = root / "stops"
                stops.mkdir(exist_ok=True)
                target = stops / f"stop-{len(list(stops.glob('stop-*'))):03d}.json"
                if target.exists():
                    raise ValueError("STOP archive collision")
                (root / "STOP").rename(target)
        auditor = independent_auditor(root, study)
        invocations = root / "invocations"
        invocations.mkdir(exist_ok=True)
        invocation = invocations / f"invocation-{len(list(invocations.glob('invocation-*'))):03d}"
        invocation.mkdir()
        atomic_immutable_json(invocation / "start.json", dict(utc=utc(), resume=resume, context=current))
        outcome = "exception"
        try:
            while True:
                model.verify_context(current)
                progress = replay(root, auditor)
                atomic_json(root / "progress.json", progress)
                ref = read(root / "control.json")["protocol"]
                protocol = protocol_record(root, ref, current)["protocol"]
                if progress["finished"]:
                    outcome = "complete" if progress["accepted_arms"] == len(study["arms"]) else "complete_with_unresolved_arms"
                    break
                if (root / "STOP").exists():
                    outcome = "operator_stop"
                    break
                remaining = protocol["total_worker_seconds"]-progress["worker_seconds"]
                if remaining <= 0 or progress["launches"] >= protocol["max_launches"]:
                    outcome = "budget_limit"
                    break
                item = progress["next"]
                request = request_for(item["arm"], item["slot"], item["causal_source"],
                                      item["target_free_source"], ref, min(protocol["wall_seconds"], remaining), cohort=model)
                telemetry = model.monitoring() if prepared_cohort else None
                arm_dir = root / f"arm-{item['arm']['id']:03d}"
                arm_dir.mkdir(exist_ok=True)
                directory = arm_dir / f"attempt-{len(list(arm_dir.glob('attempt-*'))):03d}"
                directory.mkdir()
                atomic_immutable_json(directory / "request.json", request)
                if telemetry is not None:
                    atomic_immutable_json(directory / "monitoring.json", telemetry)
                supervision = supervise([sys.executable, "-m", MODULE, "--output", str(root),
                    "--worker", str(directory.relative_to(root))], directory, root, request)
                if supervision["classification"] in {"interrupted", "supervisor_failure", "rss_limit"}:
                    outcome = "operator_stop" if supervision["classification"] == "interrupted" else supervision["classification"]
                    break
                if supervision["classification"] == "exited" and supervision["returncode"] != 0:
                    outcome = "worker_failure"
                    break
                if (directory / "completion.json").exists() and read(directory / "completion.json")["classification"] == "exception":
                    outcome = "worker_exception"
                    break
        except KeyboardInterrupt:
            outcome = "operator_stop"
        finally:
            atomic_immutable_json(invocation / "finish.json", dict(utc=utc(), outcome=outcome))
        progress = replay(root, auditor)
        atomic_json(root / "progress.json", progress)
        return dict(classification=outcome, progress=progress)


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    cohorts = parser.add_mutually_exclusive_group()
    cohorts.add_argument("--prepared", action="store_true", help="fresh prepared E3 cohort; never resumes historical E3")
    cohorts.add_argument("--plus", action="store_true", help="separate four-arm forced-shedding E3+ cohort")
    parser.add_argument("--commit")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--acknowledge-stop", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--authorize-execution", action="store_true")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    parser.add_argument("--queue-protocol", type=Path)
    parser.add_argument("--reason")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--show-protocol", action="store_true")
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    default_output = "results/e3_plus" if args.plus else "results/e3_prepared" if args.prepared else "results/e3"
    root = (args.output or HERE / default_output).resolve()
    selected = model
    if args.prepared:
        from . import e3_prepared as selected
    if args.plus:
        from . import e3_plus as selected
    if args.prepared or args.plus:
        selected.validate_output(root)
    if args.show_protocol:
        print(json.dumps(selected.DEFAULT_PROTOCOL, indent=2))
        return 0
    if args.worker:
        directory = (root / args.worker).resolve()
        if root not in directory.parents:
            parser.error("worker path outside run")
        return cohort_for(root).worker(directory, root)
    if args.status:
        print(model.encode(replay(root, partial=True)))
        return 0
    if args.stop:
        atomic_immutable_json(root / "STOP", dict(utc=utc(), reason=args.reason or "operator stop"))
        return 0
    if args.queue_protocol:
        print(queue_protocol(root, read(args.queue_protocol), args.reason))
        return 0
    if not args.commit or not (args.preflight or args.authorize_execution):
        parser.error("reviewed --commit and --authorize-execution required; --preflight never solves")
    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    if args.acknowledge_stop and not args.resume:
        parser.error("--acknowledge-stop requires --resume")
    record = run(root, args.commit, resume=args.resume, preflight=args.preflight,
                 acknowledge_stop=args.acknowledge_stop,
                 **({"prepared": True} if args.prepared else {"plus": True} if args.plus else {}))
    print(model.encode(record))
    return 0 if record["classification"] in ("complete", "complete_with_unresolved_arms", "preflight_only") else 1


if __name__ == "__main__":
    raise SystemExit(main())
