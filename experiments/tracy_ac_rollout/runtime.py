"""E4 adapter for the existing two-primary/one-helper race and transaction.

No process is started on import, preflight, status, or by constructing adapters.
Numerical execution needs a separately supplied clean-commit authority record.
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import subprocess
import signal
import sys
import time

from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    Completion, POLICY_NAME, WindowKey, normalized_violation,
)
from experiments.case118_annual_hierarchy.s5_speculative_process import SubprocessBackend
from experiments.case118_annual_hierarchy.s5_speculative_supervisor import (
    SpeculativeSupervisor, UnresolvedWindow, ResourceStop,
)
from experiments.case118_annual_hierarchy.s5_speculative_transaction import (
    publish_winner_window, advance_from_winner,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_json, atomic_immutable_json,
)
from experiments.case118_annual_hierarchy.run_s4b import process_observations, process_tree_usage
from experiments.case118_tracy_2021 import e3_prepared
from experiments.case118_tracy_2021.stage_d import read
from experiments.numerical_preparation.audit import serializable
from . import model, audit
from .worker import invocation, referenced
from cvxopf._prepared_rollout import starting_values


@dataclass(frozen=True)
class ResourceBudgets:
    """Run-wide ceilings are explicit owner choices, never inherited AC budgets.

    Primary and final/secondary races have no solve-time deadline. Build and
    post-return stalls are infrastructure stops; they do not kill a slow primary
    solve. Every contender, including canceled losers and audit overhead, counts.
    """
    max_launches: int
    total_worker_seconds: float
    build_seconds: float
    post_solve_seconds: float
    poll_seconds: float = 1.

    def __post_init__(self):
        if type(self.max_launches) is not int or self.max_launches < 1:
            raise ValueError("launch ceiling must be a positive integer")
        for key in ("total_worker_seconds", "build_seconds", "post_solve_seconds", "poll_seconds"):
            value = getattr(self, key)
            if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid resource budget: {key}")


def reference(path, root):
    return dict(path=str(path.relative_to(root)), sha256=e3_prepared.digest(path))


def verify_manifest(directory, *, supervised):
    completion = read(directory / "completion.json")
    required = {"request.json", "monitoring.json", "phase.json", "start.json", "start-ready.json", "x0.json.gz",
                "native.json.gz", "preparation.json.gz", "record.json.gz", "result.json"}
    if not required <= completion["artifacts"].keys():
        raise ValueError("completion manifest missing required evidence")
    for name, expected in completion["artifacts"].items():
        if Path(name).name != name or e3_prepared.digest(directory / name) != expected:
            raise ValueError("completed attempt artifact/hash mismatch")
    if supervised:
        supervision = read(directory / "supervision.json")
        if (supervision["reason"] != "returned" or not supervision["reaped"]
                or supervision["returncode"] != 0 or not supervision["ended_utc"]
                or supervision["completion_sha256"] != e3_prepared.digest(directory / "completion.json")):
            raise ValueError("candidate lacks successful finalized supervision")
        resources = read(directory / "resources.json")
        if (e3_prepared.digest(directory / "resources.json") != supervision["resources_sha256"]
                or not resources["samples"] or resources["peak_rss_mib"] > 16384.):
            raise ValueError("candidate lacks eligible sampled resource evidence")
    return completion


def checked_record(directory, spec, row, data, prepared, *, supervised):
    completion = verify_manifest(directory, supervised=supervised)
    req, record = read(directory / "request.json"), read(directory / "record.json.gz")
    if (record["request"] != req or record["invocation"] != asdict(spec)
            or completion["invocation"] != asdict(spec) or req["arm_id"] != row["arm_id"]
            or spec.window.iteration != req["checkpoint"]["completed_intervals"]
            or spec.window.shard_id != f'{row["arm_id"]}-{req["phase"]}'
            or record["hard_target"] != spec.hard_target
            or completion["classification"] != record["classification"]):
        raise ValueError("candidate invocation/request/completion mismatch")
    cursor = model.cursor_for(row, req["checkpoint"])
    build, kwargs, snapshot, request = model.construct(row, cursor, data, prepared,
        hard_target=spec.hard_target, phase=req["phase"])
    if record["window_request"] != serializable(asdict(request)):
        raise ValueError("candidate window/realized state/target changed")
    root = next(p for p in directory.parents if (p / "binding.json").is_file())
    if req["binding_sha256"] != e3_prepared.digest(root / "binding.json"):
        raise ValueError("candidate belongs to a different run binding")
    if directory.is_relative_to(root / "continuations"):
        generation = directory.parents[2]
        if (req.get("continuation_generation") != generation.name
                or req.get("continuation_authority_sha256") != e3_prepared.digest(generation / "authority.json")):
            raise ValueError("candidate continuation authority changed")
    sources = {key: None if ref is None else source_values(root, ref, req, key, row)
               for key, ref in req["sources"].items()}
    raw, assigned = starting_values(build, snapshot, request, source_slot=spec.source_slot,
        scale=spec.scale, seed=spec.seed, **sources)
    start, captured = read(directory / "start.json"), read(directory / "x0.json.gz")
    if (start["invocation"] != asdict(spec) or start["raw_start"] != serializable(raw)
            or start["assigned_start"] != serializable(assigned)
            or captured != e3_prepared.prepared_start(build, spec.window.iteration)
            or read(directory / "native.json.gz")["native"] != record["native"]
            or read(directory / "preparation.json.gz")["evidence"] != record["preparation_evidence"]):
        raise ValueError("source-derived raw/assigned/canonical/native evidence differs")
    evidence = record["preparation_evidence"]
    if (evidence["assigned_x0"] != captured["complete_x0"] or evidence["adjusted_x0"] != captured["adjusted_x0"]
            or evidence["reduced_x0"] != captured["reduced_x0"] or evidence["coordinates"] != captured["coordinates"]
            or [[s, a, b] for _, s, a, b in evidence["start_layout"]] != captured["layout"]):
        raise ValueError("actual prepared handoff differs from reconstructed x0")
    checked = audit.assess(record, kwargs, start, captured)
    if checked != record["independent_audit"] or (record["classification"] == "accepted") != checked["passed"]:
        raise ValueError("worker and independent supervisor audit disagree")
    return record, kwargs, request


def source_values(root, ref, req, key, row):
    """Hash-checked supervised sources, scoped to this arm/window/phase."""
    record = referenced(root, ref)
    directory = (root / ref["path"]).parent
    spec = invocation(record["invocation"])
    if (record["request"]["arm_id"] != req["arm_id"]
            or record["request"]["binding_sha256"] != req["binding_sha256"]):
        raise ValueError("source belongs to another arm/run")
    if key == "replay":
        wanted = invocation(req["invocation"])
        if (spec.attempt_id != wanted.replay_of or spec.source_slot != wanted.source_slot
                or spec.window != wanted.window or spec.order == 0
                or record["window_request"] != serializable(asdict(model.window_for(
                    row, model.cursor_for(row, req["checkpoint"]))))):
            raise ValueError("replay start belongs to another invocation/window")
        supervision = referenced(root, record["supervision"])
        source_dir = (root / record["supervision"]["path"]).parent
        if not supervision["reaped"] or supervision["invocation"] != asdict(spec):
            raise ValueError("unfinished replay source")
        for name in ("start.json", "x0.json.gz", "request.json"):
            if e3_prepared.digest(source_dir / name) != supervision["artifacts"][name]["sha256"]:
                raise ValueError("retained replay source artifacts changed")
        start, captured = read(source_dir / "start.json"), read(source_dir / "x0.json.gz")
        if start != record["start"] or captured != record["captured"] or record["request"] != read(source_dir / "request.json"):
            raise ValueError("replay payload differs from finalized source")
        return dict(raw_start=start["raw_start"], assigned_start=start["assigned_start"],
                    captured=captured)
    verify_manifest(directory, supervised=True)
    if record["classification"] != "accepted" or not record["independent_audit"]["passed"]:
        raise ValueError("unaccepted source cannot seed a solve")
    current = invocation(req["invocation"])
    if key == "preceding":
        if not spec.hard_target or spec.window.iteration != current.window.iteration - 1:
            raise ValueError("preceding source is not the immediately previous controller")
        if spec.attempt_id != req["checkpoint"]["preceding_controlling_attempt_id"]:
            raise ValueError("preceding source was not selected by this arm")
    elif key == "target_free":
        if spec.hard_target or spec.window != current.window:
            raise ValueError("target-free source belongs to another window/phase")
    else:
        raise ValueError("unknown source role")
    return record["logical_solution"]


class RolloutSupervisor(SpeculativeSupervisor):
    """Retain exhausted windows for phase escalation without stopping a peer.

    All launch, admission, lease, audit, election and cleanup rules remain those
    of S5. Only E4's experiment-level exhaustion disposition differs: the main
    loop can move an exhausted window to its hourly phase, then stop that arm.
    Infrastructure/resource exceptions still use the inherited global cleanup.
    """
    def _tick(self, now):
        before = set(self.finished)
        try:
            super()._tick(now)
        except UnresolvedWindow:
            exhausted = [key for key, race in self.races.items()
                         if key not in self.finished and race.exhausted]
            if not exhausted:
                raise
            for key in exhausted:
                self.finished.add(key)
                self.no_more_helpers.add(key)
                self.queue.cancel_waiting(key)
                self.backend.event("recovery_exhausted", self.races[key].primary, now)
        return tuple(sorted(self.finished - before))


class RolloutBackend(SubprocessBackend):
    def __init__(self, root, budgets, **kwargs):
        super().__init__(root / "attempts", cwd=model.ROOT, **kwargs)
        self.output_root = root
        self.budgets = budgets
        self.finalized_effort = 0.
        self.resource_samples = {}
        self.thermal_at = None
        self.inherited_launches = 0
        self.inherited_children = 0

    def total_launches(self):
        return self.inherited_launches + len(self.children) - self.inherited_children

    def launch(self, spec):
        if self.total_launches() >= self.budgets.max_launches:
            raise ResourceStop("global launch ceiling reached; no new contender")
        if self.effort() >= self.budgets.total_worker_seconds:
            raise ResourceStop("cumulative worker budget exhausted")
        super().launch(spec)
        self.memory()  # sample a primary immediately, before preparing its peer

    def effort(self):
        return self.finalized_effort + sum(self.clock() - child.launched for child in self.children.values() if not child.reaped)

    def has_complete_start(self, spec):
        child = self.children[spec.attempt_id]
        ready = child.directory / "start-ready.json"
        if not ready.exists():
            return False
        value = read(ready)
        if (value["start_sha256"] != e3_prepared.digest(child.directory / "start.json")
                or value["x0_sha256"] != e3_prepared.digest(child.directory / "x0.json.gz")):
            raise ValueError("retained complete start hashes changed")
        return read(child.directory / "start.json")["invocation"] == asdict(spec)

    def poll_audited(self, spec):
        completed = super().poll_audited(spec)
        if completed is not None and completed.outcome == "construction_error":
            raise RuntimeError(f"worker infrastructure failure: {spec.attempt_id}")
        return completed

    def _finish(self, child, reason):
        if child.reaped:
            return
        super()._finish(child, reason)
        lifecycle = read(child.directory / "lifecycle.json")
        self.finalized_effort += lifecycle["worker_wall_seconds"]
        resource = self.resource_samples.get(child.spec.attempt_id, [])
        atomic_immutable_json(child.directory / "resources.json", dict(samples=resource,
            peak_rss_mib=max((x["rss_mib"] for x in resource), default=None)))
        atomic_immutable_json(child.directory / "supervision.json", dict(**lifecycle,
            ended_utc=datetime.now(timezone.utc).isoformat(),
            lifecycle_sha256=e3_prepared.digest(child.directory / "lifecycle.json"),
            resources_sha256=e3_prepared.digest(child.directory / "resources.json"),
            resource_log_sha256=e3_prepared.digest(child.directory / "resources.jsonl") if (child.directory / "resources.jsonl").exists() else None,
            completion_sha256=e3_prepared.digest(child.directory / "completion.json") if (child.directory / "completion.json").exists() else None))

    def memory(self):
        sample = super().memory()
        now = self.clock()
        if self.effort() >= self.budgets.total_worker_seconds:
            raise ResourceStop("cumulative worker-seconds ceiling reached")
        active = [child for child in self.children.values() if not child.reaped and child.process is not None and child.process.poll() is None]
        if active:
            observations = process_observations()
            usage = process_tree_usage([os.getpid(), *(child.process.pid for child in active)], observations)
            for child in active:
                value = usage["per_worker"].get(str(child.process.pid))
                if value is None:
                    raise ResourceStop("required worker resource evidence unavailable")
                self.resource_samples.setdefault(child.spec.attempt_id, []).append(dict(
                    monotonic_seconds=now, rss_mib=value["rss_mib"]))
                with (child.directory / "resources.jsonl").open("a") as stream:
                    stream.write(json.dumps(self.resource_samples[child.spec.attempt_id][-1], allow_nan=False) + "\n")
                started, returned = self.solve_started_at(child.spec), self.solve_returned_at(child.spec)
                if started is None and now - child.launched > self.budgets.build_seconds:
                    raise ResourceStop("build/preparation infrastructure time ceiling reached")
                if returned is not None and now - returned > self.budgets.post_solve_seconds:
                    raise ResourceStop("post-return infrastructure time ceiling reached")
        return sample


def telemetry():
    """Process evidence is required; thermal/battery telemetry is advisory."""
    process_observations()  # fail before any launch if ps permission is missing
    alerts, thermal, battery = [], None, None
    try:
        thermal = json.loads(subprocess.check_output(["macmon", "pipe", "--samples", "1"], text=True, timeout=10))
        if any(not math.isfinite(thermal["temp"][key]) or thermal["temp"][key] <= 0
               for key in ("cpu_temp_avg", "gpu_temp_avg")):
            raise ValueError("missing/nonpositive temperature sample")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as exc:
        thermal = None
        alerts.append(f"thermal telemetry unavailable (advisory): {type(exc).__name__}: {exc}")
    try:
        battery = subprocess.check_output(["pmset", "-g", "batt"], text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        alerts.append(f"battery context unavailable (advisory): {type(exc).__name__}: {exc}")
    return dict(utc=datetime.now(timezone.utc).isoformat(), thermal=thermal,
                thermal_status="available" if thermal is not None else "unavailable", battery=battery, alerts=alerts)


class StudyAdapter:
    def __init__(self, root, binding, data, prepared):
        self.root, self.binding, self.data, self.prepared = root, binding, data, prepared
        self.rows = {row["arm_id"]: row for row in binding["manifest"]["arms"]}
        self.entries = {}
        self.backend = None

    def row(self, spec):
        return self.rows[spec.window.shard_id.rsplit("-", 1)[0]] if spec.window.shard_id.endswith("-hourly") else self.rows[spec.window.shard_id.removesuffix("-component_first")]

    def arm_directory(self, row):
        return self.root / "arms" / row["arm_id"]

    def command(self, spec, directory):
        row = self.row(spec)
        cp = read(self.arm_directory(row) / "checkpoint.json")
        if cp["completed_intervals"] != spec.window.iteration:
            raise ValueError("attempt is not the current physical checkpoint")
        sources = dict(preceding=None, target_free=None, replay=None)
        previous = cp["preceding_controlling_attempt_id"]
        if previous is not None:
            sources["preceding"] = reference(self.backend.children[previous].directory / "record.json.gz", self.root)
        if spec.source_slot in (2, 3, 4, 5) and spec.replay_of is None:
            candidates = [child for child in self.backend.children.values()
                          if child.spec.window == spec.window and child.spec.source_slot == 1 and child.reaped
                          and child.completion is not None and child.completion.outcome == "accepted"]
            if not candidates:
                raise ValueError("accepted supervised target-free source unavailable")
            sources["target_free"] = reference(candidates[-1].directory / "record.json.gz", self.root)
        if spec.replay_of is not None:
            child = self.backend.children[spec.replay_of]
            # Timeouts have no returned record, but do retain their original start.
            replay = dict(invocation=asdict(child.spec), window_request=serializable(asdict(model.window_for(row, model.cursor_for(row, cp)))),
                request=read(child.directory / "request.json"),
                start=read(child.directory / "start.json"), captured=read(child.directory / "x0.json.gz"),
                supervision=reference(child.directory / "supervision.json", self.root))
            atomic_immutable_json(directory / "replay-source.json", replay)
            sources["replay"] = reference(directory / "replay-source.json", self.root)
        phase = "component_first" if spec.window.shard_id.endswith("-component_first") else "hourly"
        from .continuation import active
        resumed = active(self.root)
        atomic_immutable_json(directory / "request.json", dict(invocation=asdict(spec), arm_id=row["arm_id"],
            phase=phase, checkpoint=cp, sources=sources,
            binding_sha256=e3_prepared.digest(self.root / "binding.json"),
            **({} if resumed is None else dict(continuation_generation=resumed.name,
                continuation_authority_sha256=e3_prepared.digest(resumed / "authority.json")))))
        atomic_immutable_json(directory / "monitoring.json", telemetry())
        return [sys.executable, "-m", "experiments.tracy_ac_rollout.run", "--worker", str(directory), "--output", str(self.root)]

    def candidate(self, spec, directory):
        record, _, _ = checked_record(directory, spec, self.row(spec), self.data, self.prepared, supervised=False)
        common = record.get("audit") or {}
        rank = normalized_violation(common.get("residuals", {}), common["limits"]) if (
            common.get("limits") and record["classification"] != "accepted"
            and record["independent_audit"].get("first_action", {}).get("passed", True)) else None
        return Completion(spec, "accepted" if record["classification"] == "accepted" else "rejected",
            self.backend.has_complete_start(spec), rank,
            None if record["result"] is None else record["result"].get("objective"))

    def publish(self, spec, attempts, directories):
        if self.backend.effort() >= self.backend.budgets.total_worker_seconds:
            raise ResourceStop("worker effort ceiling reached before winner publication")
        row = self.row(spec)
        directory = self.arm_directory(row)
        cp = read(directory / "checkpoint.json")
        record, kwargs, request = checked_record(directories[spec.attempt_id], spec, row,
            self.data, self.prepared, supervised=True)
        next_cursor, action = audit.first_action(record, model.cursor_for(row, cp), request, kwargs, spec.attempt_id)
        window = dict(schema_version=2, policy=POLICY_NAME, iteration=request.local_start,
            shard_id=row["arm_id"], arm_id=row["arm_id"], phase=record["request"]["phase"],
            plan_id=row["plan"]["plan_id"], storage_device_ids=list(request.storage_device_ids),
            initial_soc_mwh=request.initial_soc_mwh.tolist(), post_step_soc_mwh=next_cursor.realized_soc_mwh.tolist(),
            preceding_controlling_attempt_id=cp["preceding_controlling_attempt_id"],
            executed_interval=action, window_request=serializable(asdict(request)),
            candidate_directories={key: str(path.relative_to(self.root)) for key, path in directories.items()},
            completions=[asdict(x) for x in attempts],
            selected_record=reference(directories[spec.attempt_id] / "record.json.gz", self.root),
            selected_supervision=reference(directories[spec.attempt_id] / "supervision.json", self.root))
        self.entries[spec.window] = publish_winner_window(directory, cp, window)

    def advance(self, spec, directories):
        row = self.row(spec)
        finalization = dict(winner=asdict(spec), window=asdict(self.entries[spec.window]),
            candidate_supervision={key: reference(path / "supervision.json", self.root) for key, path in directories.items()})
        for path in directories.values():
            if not read(path / "supervision.json")["reaped"]:
                raise ValueError("cannot finalize window before all contenders are reaped")
        path = self.arm_directory(row) / f"finalization-{spec.window.iteration:03d}.json"
        if path.exists():
            if read(path) != finalization:
                raise ValueError("different window finalization is already durable")
        else:
            atomic_immutable_json(path, finalization)
        advance_from_winner(self.arm_directory(self.row(spec)), self.entries[spec.window])


def execute(root, binding, data, prepared, *, continuation=None):
    """Two independent arm lanes, one fair shared helper; no automatic resume."""
    budgets = ResourceBudgets(**binding["budgets"])
    adapter = StudyAdapter(root, binding, data, prepared)
    backend = RolloutBackend(root, budgets, command=adapter.command, audit=adapter.candidate,
        publish=adapter.publish, advance=adapter.advance)
    adapter.backend = backend
    output = root
    if continuation is not None:
        from .continuation import restore_backend
        output = root / "continuations" / continuation["generation"]
        backend.root = output / "attempts"
        restore_backend(root, backend, continuation)
    supervisor = RolloutSupervisor(backend)
    queue = list(binding["manifest"]["arms"])
    active = {}
    for row in queue:
        directory = adapter.arm_directory(row)
        if continuation is not None:
            if not (directory / "checkpoint.json").is_file():
                raise ValueError("continuation lacks its preserved checkpoint")
            continue
        directory.mkdir(parents=True, exist_ok=False)
        atomic_json(directory / "checkpoint.json", dict(shard_id=row["arm_id"], plan_id=row["plan"]["plan_id"],
            binding_sha256=e3_prepared.digest(root / "binding.json"), interval=dict(start=0, stop=24),
            completed_intervals=0, next_global_iteration=0, complete=False, windows=[],
            storage_device_ids=row["plan"]["storage_device_ids"], realized_soc_mwh=row["initial_soc_mwh"],
            preceding_controlling_attempt_id=None))
    if continuation is not None:
        queue = [row for row in queue if not read(adapter.arm_directory(row) / "checkpoint.json")["complete"]
                 and not (adapter.arm_directory(row) / "outcome.json").exists()]

    def admit(row, phase="component_first"):
        cp = read(adapter.arm_directory(row) / "checkpoint.json")
        key = WindowKey(f'{row["arm_id"]}-{phase}', cp["completed_intervals"])
        active[key] = row
        supervisor.add_window(key, has_preceding=cp["preceding_controlling_attempt_id"] is not None, now=time.monotonic())

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"study interrupted by signal {signum}")

    handlers = {signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)}
    for signum in handlers:
        signal.signal(signum, interrupted)
    try:
        for _ in range(min(model.CONFIG.primary_workers, len(queue))):
            admit(queue.pop(0))
        last_thermal = time.monotonic()
        while active:
            if (root / "STOP").exists():
                raise ResourceStop("owner STOP marker")
            if time.monotonic() - last_thermal >= 60:
                atomic_json(output / "monitoring.json", telemetry())
                last_thermal = time.monotonic()
            for key in supervisor.tick(time.monotonic()):
                row = active.pop(key)
                race = supervisor.races[key]
                phase = "component_first" if key.shard_id.endswith("-component_first") else "hourly"
                atomic_immutable_json(adapter.arm_directory(row) / f"phase-{key.iteration:03d}-{phase}.json", dict(
                    winner=None if race.winner is None else asdict(race.winner),
                    launched=[asdict(x) for x in race.launched.values()], unavailable=race.unavailable,
                    completions=[asdict(x) for x in race.completed.values()]))
                if race.winner is None and phase == "component_first":
                    admit(row, "hourly")
                    continue
                cp = read(adapter.arm_directory(row) / "checkpoint.json")
                if race.winner is not None and not cp["complete"]:
                    admit(row)
                else:
                    atomic_immutable_json(adapter.arm_directory(row) / "outcome.json", dict(
                        classification="complete" if cp["complete"] else "numerically_unresolved",
                        completed_intervals=cp["completed_intervals"], first_unresolved=None if cp["complete"] else cp["next_global_iteration"]))
                    if queue:
                        admit(queue.pop(0))
            time.sleep(budgets.poll_seconds)
    except BaseException as exc:
        cleanup_error = None
        try:
            supervisor.stop("study_stopped", now=time.monotonic())
        except BaseException as cleanup:
            cleanup_error = f"{type(cleanup).__name__}: {cleanup}"
        atomic_immutable_json(output / "outcome.json", dict(classification="stopped", reason=f"{type(exc).__name__}: {exc}",
            cleanup_error=cleanup_error))
        raise
    finally:
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
    atomic_immutable_json(output / "outcome.json", dict(classification="finished", finalized_worker_seconds=backend.finalized_effort,
        launches=backend.total_launches() if hasattr(backend, "total_launches") else len(backend.children)))
