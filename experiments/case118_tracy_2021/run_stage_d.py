"""Restartable sequential D1 runner. Execution requires a clean named commit.

The immutable attempt journal is authoritative; progress.json is only a cache.
Commands: run, resume, stop, status, analyze. No annual execution path exists.
"""

# ruff: noqa: E402 -- CLI thread environment must precede numerical imports.

import argparse
from contextlib import contextmanager
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

THREAD_KEYS = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
)
if __name__ == "__main__":
    for key in THREAD_KEYS:
        os.environ[key] = "1"

from experiments.case118_annual_hierarchy.run_s4 import _child_rss_mib, _terminate
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
)
from . import stage_d as model
from . import stage_d_continuation as continuation
from .prepare import HERE, ROOT, digest
from .run_stage_b import jsonable
from .stage_b import audit_result, verified_inputs

MODULE = "experiments.case118_tracy_2021.run_stage_d"
OUTPUT = HERE / "results/stage_d"


def utc():
    return datetime.now(timezone.utc).isoformat()


class InvocationTiming:
    """Nonoverlapping wall-time spans; unfinished snapshots are lower bounds."""

    def __init__(self, root, started, started_utc, resume):
        parent = root / "invocations"
        parent.mkdir(exist_ok=True)
        self.directory = parent / f"invocation-{len(list(parent.iterdir())):03d}"
        self.directory.mkdir()
        self.started = started
        self.record = dict(
            started_utc=started_utc, resume=resume, spans=[], action_finalizations={}
        )
        self.save()

    def save(self):
        self.record.update(
            observed_utc=utc(), wall_seconds=time.monotonic() - self.started
        )
        atomic_json(self.directory / "timing.json", self.record)

    @contextmanager
    def span(self, phase, action=None):
        started, started_utc = time.monotonic(), utc()
        self.record["active_span"] = dict(phase=phase, action=action)
        self.save()
        try:
            yield
        finally:
            self.record["spans"].append(
                dict(
                    phase=phase,
                    action=action,
                    started_utc=started_utc,
                    ended_utc=utc(),
                    wall_seconds=time.monotonic() - started,
                )
            )
            self.record["active_span"] = None
            self.save()

    def finish(self, outcome):
        self.record["outcome"] = outcome
        self.save()
        atomic_immutable_json(self.directory / "finished.json", self.record)


def timing_summary(root):
    """Separate invocation wall, action latency, worker effort, and offline gaps.

    A missing finish cannot distinguish an abrupt tail from downtime. Do not
    assign that unknown interval to either category or extrapolate monotonic
    clocks across invocations/reboots.
    """
    invocations, gaps, actions, finalizations = [], [], {}, {}
    for directory in sorted((root / "invocations").glob("invocation-*")):
        finished = (directory / "finished.json").exists()
        record = model.read(
            directory / ("finished.json" if finished else "timing.json")
        )
        finalizations.update(record.get("action_finalizations", {}))
        if invocations:
            previous = invocations[-1]
            gaps.append(
                dict(
                    started_utc=previous["observed_utc"],
                    ended_utc=record["started_utc"],
                    seconds=(
                        datetime.fromisoformat(record["started_utc"])
                        - datetime.fromisoformat(previous["observed_utc"])
                    ).total_seconds(),
                    classification="downtime"
                    if not previous["timing_is_lower_bound"]
                    else "unknown_active_tail_or_downtime",
                )
            )
        worker = sum(
            s["wall_seconds"]
            for s in record["spans"]
            if s["phase"] == "worker_supervision"
        )
        for span in record["spans"]:
            if span["action"] is None:
                continue
            item = actions.setdefault(
                span["action"],
                dict(
                    started_utc=span["started_utc"],
                    ended_utc=span["ended_utc"],
                    observed_active_seconds=0.0,
                    parent_seconds=0.0,
                ),
            )
            item["ended_utc"] = span["ended_utc"]
            item["observed_active_seconds"] += span["wall_seconds"]
            if span["phase"] != "worker_supervision":
                item["parent_seconds"] += span["wall_seconds"]
        invocations.append(
            dict(
                **record,
                timing_is_lower_bound=not finished,
                worker_supervision_seconds=worker,
                # An open worker span is not parent work.
                parent_seconds=(record["wall_seconds"] - worker) if finished else None,
            )
        )
    uncertain = any(i["timing_is_lower_bound"] for i in invocations)
    for item in actions.values():
        item["observed_calendar_span_seconds"] = (
            datetime.fromisoformat(item["ended_utc"])
            - datetime.fromisoformat(item["started_utc"])
        ).total_seconds()
        item["timing_is_lower_bound"] = uncertain
    for action, ended in finalizations.items():
        if action in actions:
            actions[action]["finalized_utc"] = ended
            actions[action]["total_calendar_latency_seconds"] = (
                datetime.fromisoformat(ended)
                - datetime.fromisoformat(actions[action]["started_utc"])
            ).total_seconds()
    return dict(
        invocations=invocations,
        gaps=gaps,
        actions=actions,
        observed_invocation_wall_seconds=sum(i["wall_seconds"] for i in invocations),
        timing_is_lower_bound=uncertain,
    )


def make_request(trajectory, hour, role, initial, previous, causal, target_free, ids):
    return dict(
        trajectory=trajectory["id"],
        hour=hour,
        global_hour=trajectory["start"] + hour,
        W=trajectory["W"],
        role=role,
        initial_soc_mwh=initial,
        target_soc_mwh=trajectory["targets"][hour],
        storage_device_ids=ids,
        previous=previous,
        causal_source=causal,
        target_free_source=target_free,
    )


def require_matching_audit(actual, retained):
    """Compare audit reconstructions, not bitwise floating-point reductions.

    These are roundoff allowances, not changes to scientific acceptance limits.
    Every residual must retain its individual pass/fail decision, even when the
    numerical difference is within the comparison allowance.
    """

    def mismatch(field):
        raise ValueError(f"independent parent audit differs from worker audit: {field}")

    if actual.keys() != retained.keys():
        mismatch("schema")
    numeric = {"residuals", "limits", "costs", "metrics"}
    for section, value in actual.items():
        saved = retained[section]
        if section not in numeric:
            if value != saved:
                mismatch(section)
            continue
        if value.keys() != saved.keys():
            mismatch(section + " schema")
        for name, number in value.items():
            # Limits are exact constants or cost-dependent scales: permit only
            # relative roundoff there, never a blanket absolute relaxation.
            atol = 0.0 if section == "limits" else 1e-12
            if section == "residuals":
                # Compare residual reports at a small fraction of their own
                # engineering-unit acceptance scale, not relative to a
                # cancellation residual that may be exactly zero. Historical
                # workers audited arrays before JSON normalized their layout.
                # This allowance is 0.001% of the tighter physical limit;
                # individual threshold decisions below still must agree.
                atol = max(
                    atol, 1e-5 * min(actual["limits"][name], retained["limits"][name])
                )
            if not math.isclose(number, saved[name], rel_tol=1e-12, abs_tol=atol):
                mismatch(f"{section}.{name}")
            if section == "residuals":
                current_pass = (
                    math.isfinite(number) and number <= actual["limits"][name]
                )
                saved_pass = (
                    math.isfinite(saved[name])
                    and saved[name] <= retained["limits"][name]
                )
                if current_pass != saved_pass:
                    mismatch(f"{section}.{name} acceptance")


def verify_attempt(root, directory, request, prepared):
    import numpy as np
    from cvxopf.hierarchical import IPOPTStartEvidence

    completion = model.read(directory / "completion.json")
    for name, key in (
        ("result.json.gz", "result_sha256"),
        ("x0.json.gz", "x0_sha256"),
        ("start.json", "start_sha256"),
    ):
        if digest(directory / name) != completion[key]:
            raise ValueError(f"attempt artifact mismatch: {directory / name}")
    payload = model.read(directory / "result.json.gz")
    record = continuation.load(root, verify_prefix=False)
    historical = (
        record is not None
        and str((directory / "request.json").relative_to(root))
        in record["historical_files"]
    )
    if (directory / "execution-context.json").exists():
        expected_context = model.read(directory / "execution-context.json")
        if payload.get("execution_context") != expected_context or expected_context != (
            record["original_context"]
            if historical
            else continuation.execution_context(root)
        ):
            raise ValueError("attempt execution provenance mismatch")
    elif record is not None and not historical:
        raise ValueError("new continued attempt lacks execution provenance")
    start = model.read(directory / "start.json")
    raw_x0 = model.read(directory / "x0.json.gz")
    raw_x0.pop("iteration")
    evidence = IPOPTStartEvidence(**raw_x0)
    seen = set()
    for item in evidence.layout:
        if item["is_original_variable"]:
            name = item["name"]
            seen.add(name)
            expected = np.asarray(start["assigned_start"][name]).flatten(order="F")
            np.testing.assert_array_equal(
                evidence.complete_x0[item["start"] : item["stop"]], expected
            )
    if seen != set(start["assigned_start"]):
        raise ValueError("retained IPOPT x0 omits model variables")
    if payload["request"] != request:
        raise ValueError("worker used a different request")
    audit = audit_result(
        payload["result"],
        model.request_kwargs(prepared, request),
        payload["named_costs"],
    )
    require_matching_audit(jsonable(audit), payload["audit"])
    accepted = payload["exception"] is None and audit["passed"]
    if accepted != payload["accepted"]:
        raise ValueError("acceptance label contradicts reconstructed audit")
    if accepted and payload["next_soc_mwh"] != payload["result"]["soc"][0]:
        raise ValueError("next state differs from accepted first action")
    if accepted:
        logical = payload["logical_solution"]
        if not logical or any(
            not np.isfinite(np.asarray(v)).all() for v in logical.values()
        ):
            raise ValueError("accepted source lacks finite logical solution")
        # Explicitly check the physical coordinates used for the next action's
        # state and warm start against the independently audited public result.
        aliases = {
            "Pg": "Pg",
            "Qg": "Qg",
            "b": "b",
            "b_q": "b_q",
            "soc": "soc",
            "p_nd": "p_nd",
            "q_nd": "q_nd",
            "load_shed_fraction": "load_shed_fraction",
            "v": "Vm",
            "theta": "Va_deg",
            "p": "p_net",
            "q": "q_net",
        }
        base = float(model.request_kwargs(prepared, request)["case"]["baseMVA"])
        for family, public in aliases.items():
            for t in range(request["W"]):
                values = np.asarray(logical[f"{family}_{t}"]).reshape(-1)
                if family in {"Pg", "Qg", "p", "q"}:
                    values = values * base
                elif family == "theta":
                    values = np.rad2deg(values)
                np.testing.assert_allclose(
                    values, payload["result"][public][t], rtol=1e-12, atol=1e-10
                )
    # The live cache keeps summaries, not all full-window/x0 arrays.
    return dict(
        accepted=accepted,
        next_soc_mwh=payload["next_soc_mwh"],
        result_reference=model.reference(directory / "result.json.gz", root),
        start_reference=model.reference(directory / "start.json", root),
    )


def reconstruct(root, study, verify, *, allow_partial=False):
    """Replay the finite ladder and exactly-once state transitions; no solves."""
    trajectories, attempts, next_request = [], [], None
    active = None
    blocking_failure = None
    for trajectory in study["trajectories"]:
        initial = trajectory["initial_soc_mwh"]
        previous = None
        executed = []
        exhausted = False
        for hour in range(6):
            causal = target_free = None
            slot = 0
            accepted = None
            action_dir = (
                root / f"trajectory-{trajectory['id']:02d}" / f"hour-{hour:02d}"
            )
            directories = sorted(action_dir.glob("attempt-*"))
            for number, directory in enumerate(directories):
                if directory.name != f"attempt-{number:03d}" or accepted is not None:
                    raise ValueError("nonconsecutive or post-acceptance attempt")
                if not (directory / "request.json").exists():
                    if any(
                        p.name != "supervision.json"
                        and not (p.name.startswith(".") and p.name.endswith(".tmp"))
                        for p in directory.iterdir()
                    ):
                        raise ValueError("attempt artifacts exist without a request")
                    # A stop between mkdir and atomic request publication did
                    # not launch or consume a policy slot; retain the directory.
                    continue
                if slot >= len(model.ROLES):
                    raise ValueError("attempt beyond frozen recovery sequence")
                expected = make_request(
                    trajectory,
                    hour,
                    model.ROLES[slot],
                    initial,
                    previous,
                    causal,
                    target_free,
                    study["storage_device_ids"],
                )
                if model.read(directory / "request.json") != expected:
                    raise ValueError(
                        "request/state/start lineage differs from retained prefix"
                    )
                if allow_partial and not (directory / "supervision.json").exists():
                    active = str(directory.relative_to(root))
                    break
                supervision = model.read(directory / "supervision.json")
                attempts.append(
                    dict(path=str(directory.relative_to(root)), **supervision)
                )
                outcome = supervision["classification"]
                interrupted_attempt = outcome in (
                    "interrupted",
                    "orphaned_interruption",
                )
                if interrupted_attempt and not (directory / "completion.json").exists():
                    # Same pending policy slot gets a new attempt identity on resume.
                    continue
                if not interrupted_attempt and (
                    outcome != "exited" or supervision["returncode"] != 0
                ):
                    if allow_partial:
                        blocking_failure = dict(
                            path=str(directory.relative_to(root)), **supervision
                        )
                        break
                    raise RuntimeError(f"study stopped by {outcome}: {directory}")
                payload = verify(directory, expected)
                if slot == 0:
                    causal = payload.get("start_reference") or model.reference(
                        directory / "start.json", root
                    )
                if payload["accepted"]:
                    ref = payload.get("result_reference") or model.reference(
                        directory / "result.json.gz", root
                    )
                    if model.ROLES[slot] == "target_free":
                        target_free = ref
                    else:
                        accepted = ref
                        initial = payload["next_soc_mwh"]
                        previous = ref
                        executed.append(
                            dict(
                                hour=hour,
                                global_hour=expected["global_hour"],
                                result=ref,
                                role=expected["role"],
                                next_soc_mwh=initial,
                            )
                        )
                completed_role = model.ROLES[slot]
                slot += 1
                if completed_role == "target_free" and target_free is None:
                    slot = len(model.ROLES)
            if accepted is not None:
                continue
            if slot == len(model.ROLES):
                exhausted = True
                # No later action may exist after exhausted recovery.
                break
            if next_request is None and active is None and blocking_failure is None:
                next_request = dict(
                    directory=str(
                        (action_dir / f"attempt-{len(directories):03d}").relative_to(
                            root
                        )
                    ),
                    request=make_request(
                        trajectory,
                        hour,
                        model.ROLES[slot],
                        initial,
                        previous,
                        causal,
                        target_free,
                        study["storage_device_ids"],
                    ),
                )
            break
        # Reject files that a later cursor would otherwise silently ignore.
        for action_dir in (root / f"trajectory-{trajectory['id']:02d}").glob("hour-*"):
            index = int(action_dir.name.split("-")[1])
            if index > len(executed) or index >= 6:
                raise ValueError("action exists beyond accepted prefix")
        trajectories.append(
            dict(
                id=trajectory["id"],
                completed_hours=len(executed),
                classification="complete"
                if len(executed) == 6
                else "incomplete"
                if exhausted
                else "pending",
                executed=executed,
            )
        )
    return dict(
        trajectories=trajectories,
        attempts=attempts,
        next=next_request,
        active_attempt=active,
        blocking_failure=blocking_failure,
        complete=all(t["classification"] == "complete" for t in trajectories),
        finished=all(t["classification"] != "pending" for t in trajectories),
        completed_hours=sum(t["completed_hours"] for t in trajectories),
        observed_attempt_wall_seconds=sum(a.get("wall_seconds", 0) for a in attempts),
        uncertain_interrupted_timings=sum(
            a["classification"] == "orphaned_interruption" for a in attempts
        ),
    )


def supervise(command, directory, root, *, rss_reader=_child_rss_mib, poll_seconds=1):
    """RSS-only supervision; no solve/wall/iteration/stall timeout."""
    started = time.monotonic()
    record = dict(
        classification="supervisor_failure",
        started_utc=utc(),
        returncode=None,
        peak_sampled_rss_mib=0.0,
        samples=0,
    )
    process = None
    try:
        if (root / "STOP").exists():
            raise KeyboardInterrupt("operator stop requested before launch")
        with (directory / "worker.log").open("xb") as log:
            process = subprocess.Popen(
                command,
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            atomic_immutable_json(
                directory / "launch.json",
                dict(pid=process.pid, started_utc=record["started_utc"]),
            )
            with (directory / "resources.jsonl").open("x") as samples:
                while process.poll() is None:
                    if (root / "STOP").exists():
                        raise KeyboardInterrupt("operator stop requested")
                    rss = rss_reader(process.pid)
                    if rss is None:
                        if process.poll() is not None:
                            break
                        raise RuntimeError("RSS monitoring failed for live worker")
                    samples.write(
                        json.dumps(
                            dict(
                                utc=utc(),
                                elapsed_seconds=time.monotonic() - started,
                                rss_mib=rss,
                            )
                        )
                        + "\n"
                    )
                    samples.flush()
                    record["samples"] += 1
                    record["peak_sampled_rss_mib"] = max(
                        record["peak_sampled_rss_mib"], rss
                    )
                    if rss > model.RSS_MIB:
                        record["classification"] = "rss_limit"
                        break
                    time.sleep(poll_seconds)
            if (
                record["classification"] == "supervisor_failure"
                and process.poll() is not None
            ):
                record["classification"] = "exited"
    except BaseException as exc:
        record["classification"] = (
            "interrupted"
            if isinstance(exc, KeyboardInterrupt)
            else "supervisor_failure"
        )
        record["exception"] = f"{type(exc).__name__}: {exc}"
    finally:
        if process is not None:
            if process.poll() is None:
                _terminate(process)
            record["returncode"] = process.wait()
        record.update(wall_seconds=time.monotonic() - started, ended_utc=utc())
        atomic_immutable_json(directory / "supervision.json", record)
    return record


def reconcile_interruptions(root):
    """After acquiring the parent lock, refuse live orphan children; retain gaps."""
    for directory in sorted(root.glob("trajectory-*/hour-*/attempt-*")):
        if (directory / "supervision.json").exists():
            continue
        launch = (
            model.read(directory / "launch.json")
            if (directory / "launch.json").exists()
            else None
        )
        if launch:
            try:
                os.kill(launch["pid"], 0)
            except ProcessLookupError:
                pass
            else:
                raise RuntimeError(
                    f"possible live orphan PID {launch['pid']}; inspect before resume"
                )
        # A complete worker archive can be independently audited on replay;
        # a result file without its completion record is not accepted.
        elapsed = 0.0
        samples = directory / "resources.jsonl"
        if samples.exists():
            for line in samples.read_text().splitlines():
                try:
                    elapsed = max(elapsed, json.loads(line)["elapsed_seconds"])
                except ValueError:
                    break
        atomic_immutable_json(
            directory / "supervision.json",
            dict(
                classification="orphaned_interruption",
                returncode=None,
                wall_seconds=elapsed,
                timing_is_lower_bound=True,
                reconciled_utc=utc(),
            ),
        )


def interrupted(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


def run(root, commit, *, resume=False, continue_from=None):
    invocation_started, invocation_utc = time.monotonic(), utc()
    for name in THREAD_KEYS:
        os.environ[name] = "1"
    current = model.context()
    if continue_from is not None and not resume:
        raise ValueError("audit continuation is only available with resume")
    if not current["clean"] or current["commit"] != commit:
        raise ValueError(
            "run/resume requires the exact clean reviewed execution commit"
        )
    # Monitoring permission must be established before any worker launch.
    if _child_rss_mib(os.getpid()) is None:
        raise RuntimeError("RSS monitoring unavailable; no worker launched")
    study = model.specification()
    prepared = verified_inputs()
    if not resume:
        root.mkdir(parents=True, exist_ok=False)
        atomic_immutable_json(
            root / "binding.json", dict(context=current, study=study, created_utc=utc())
        )
    binding = model.read(root / "binding.json")
    retained_continuation = continuation.load(root)
    expected_context = (
        retained_continuation["execution_context"]
        if retained_continuation
        else binding["context"]
    )
    if continue_from is not None:
        if retained_continuation or continue_from != binding["context"]["commit"]:
            raise ValueError(
                "continuation must name original commit and cannot replace a record"
            )
        continuation.compatible(binding["context"], current)
        continuation.source_check(commit)
    if (continue_from is None and expected_context != current) or binding[
        "study"
    ] != study:
        raise ValueError(
            "resume source/environment/study differs from original binding"
        )
    verified = {}

    def verify_once(directory, request):
        if directory not in verified:
            verified[directory] = (
                request,
                verify_attempt(root, directory, request, prepared),
            )
        original, summary = verified[directory]
        if original != request:
            raise ValueError("immutable attempt request changed during invocation")
        return summary

    with (root / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        timing = InvocationTiming(root, invocation_started, invocation_utc, resume)
        finalized = {
            key
            for key, value in timing_summary(root)["actions"].items()
            if "finalized_utc" in value
        }
        outcome_label = "complete"
        pending_action = None
        prior_sigterm = signal.signal(signal.SIGTERM, interrupted)
        try:
            with timing.span("resume_reconciliation"):
                reconcile_interruptions(root)
                if continue_from is not None:
                    prefix = reconstruct(root, study, verify_once)
                    record = continuation.prepare(root, current, prefix)
                    atomic_immutable_json(root / continuation.RECORD, record)
                if resume:
                    (root / "STOP").unlink(missing_ok=True)
            while True:
                # Startup replay is invocation overhead, not charged again to
                # previously completed actions. Later passes finalize the
                # immediately preceding attempt (including cursor publication).
                with timing.span(
                    "audit_and_cursor" if pending_action else "initial_reconstruction",
                    pending_action,
                ):
                    progress = reconstruct(root, study, verify_once)
                    atomic_json(root / "progress.json", progress)
                    for trajectory in progress["trajectories"]:
                        for action in trajectory["executed"]:
                            key = f"trajectory-{trajectory['id']:02d}/hour-{action['hour']:02d}"
                            if key not in finalized:
                                timing.record["action_finalizations"][key] = utc()
                                finalized.add(key)
                if progress["finished"]:
                    if not (root / "study-result.json").exists():
                        atomic_immutable_json(root / "study-result.json", progress)
                    return progress
                upcoming = progress["next"]
                req = upcoming["request"]
                directory = root / upcoming["directory"]
                pending_action = str(directory.parent.relative_to(root))
                with timing.span("attempt_preparation", pending_action):
                    if (root / "STOP").exists():
                        raise KeyboardInterrupt("operator stop requested")
                    if model.context() != current:
                        raise RuntimeError("execution source/environment changed")
                    print(
                        f"Next: trajectory {req['trajectory']:02d}, hour {req['hour'] + 1}/6 "
                        f"(global {req['global_hour']}), W={req['W']}, {req['role']}; "
                        f"{progress['completed_hours']}/96 actions accepted",
                        flush=True,
                    )
                    directory.mkdir(parents=True, exist_ok=False)
                    atomic_immutable_json(
                        directory / "request.json", upcoming["request"]
                    )
                    atomic_immutable_json(directory / "execution-context.json", current)
                with timing.span("worker_supervision", pending_action):
                    outcome = supervise(
                        [
                            sys.executable,
                            "-m",
                            MODULE,
                            "worker",
                            "--output",
                            str(root),
                            "--attempt",
                            str(directory),
                        ],
                        directory,
                        root,
                    )
                if outcome["classification"] == "interrupted":
                    raise KeyboardInterrupt("worker interrupted")
                if outcome["classification"] != "exited" or outcome["returncode"] != 0:
                    raise RuntimeError(f"study stopped: {outcome}")
        except BaseException as exc:
            outcome_label = f"{type(exc).__name__}: {exc}"
            atomic_json(
                root / "stopped.json",
                dict(reason=outcome_label, utc=utc()),
            )
            raise
        finally:
            signal.signal(signal.SIGTERM, prior_sigterm)
            timing.finish(outcome_label)


def analyze(root):
    binding = model.read(root / "binding.json")
    retained_continuation = continuation.load(root)
    # Analysis can run from later clean/dirty documentation states; bind its own
    # context separately and reconstruct all physics from the original requests.
    if binding["study"] != model.specification():
        raise ValueError("analysis specification differs from retained study")
    prepared = verified_inputs()
    progress = reconstruct(
        root,
        binding["study"],
        lambda d, r: verify_attempt(root, d, r, prepared),
        allow_partial=True,
    )
    if (root / "study-result.json").exists() and model.read(
        root / "study-result.json"
    ) != progress:
        raise ValueError("terminal summary differs from reconstructed attempt journal")
    return dict(
        analysis_context=model.context(),
        execution_context=binding["context"],
        continuation=retained_continuation,
        timing=timing_summary(root),
        **progress,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("run", "resume", "worker", "stop", "status", "analyze")
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--commit")
    parser.add_argument("--attempt", type=Path)
    parser.add_argument(
        "--continue-from",
        help="Explicit original commit for the reviewed audit-only continuation",
    )
    args = parser.parse_args()
    root = args.output.resolve()
    if args.command in {"run", "resume"}:
        if not args.commit:
            parser.error("--commit is required")
        run(
            root,
            args.commit,
            resume=args.command == "resume",
            continue_from=args.continue_from,
        )
    elif args.command == "worker":
        if args.attempt is None:
            parser.error("--attempt required for worker")
        model.worker(args.attempt, root)
    elif args.command == "stop":
        if not (root / "binding.json").exists():
            parser.error("no bound study at output")
        atomic_json(root / "STOP", dict(requested_utc=utc()))
    elif args.command == "status":
        progress = model.read(root / "progress.json")
        print(
            f"Accepted actions: {progress['completed_hours']}/96; "
            f"finished: {progress['finished']}; all trajectories complete: {progress['complete']}"
        )
        print(
            f"Retained attempts: {len(progress['attempts'])}; "
            f"observed attempt wall time: {progress['observed_attempt_wall_seconds'] / 60:.1f} min"
        )
        if progress["next"] is not None:
            req = progress["next"]["request"]
            print(
                f"Next/active request: trajectory {req['trajectory']:02d}, hour {req['hour'] + 1}/6, "
                f"global {req['global_hour']}, W={req['W']}, {req['role']}"
            )
            phase = root / progress["next"]["directory"] / "phase.json"
            if phase.exists():
                print("Latest worker phase:", model.read(phase))
        for name in ("stopped.json", "STOP"):
            if (root / name).exists():
                print(name, model.read(root / name))
    else:
        atomic_json(root / "analysis.json", analyze(root))


if __name__ == "__main__":
    main()
