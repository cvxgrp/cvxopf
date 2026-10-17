"""Exactly one supervised prepared AC attempt. Never called by read-only modes."""
from dataclasses import asdict
from datetime import datetime, timezone
import os
import time


from cvxopf import extract_results
from cvxopf._prepared_rollout import starting_values, physical_solution
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json, atomic_json, atomic_gzip_json
from experiments.case118_annual_hierarchy.s5_speculative_policy import AttemptSpec, WindowKey
from experiments.case118_tracy_2021 import e3_prepared
from experiments.case118_tracy_2021.stage_d import read
from experiments.numerical_preparation.audit import serializable, evidence_record
from . import model, audit


def invocation(value):
    return AttemptSpec(WindowKey(**value["window"]), value["order"], value["source_slot"], value["replay_of"])


def referenced(root, ref):
    path = (root / ref["path"]).resolve()
    if root.resolve() not in path.parents or e3_prepared.digest(path) != ref["sha256"]:
        raise ValueError("changed or unsafe rollout artifact reference")
    return read(path)


def construct_attempt(row, cursor, data, prepared, spec, phase, sources):
    build, kwargs, snapshot, request = model.construct(row, cursor, data, prepared,
        hard_target=spec.hard_target, phase=phase)
    raw, assigned = starting_values(build, snapshot, request, source_slot=spec.source_slot,
        scale=spec.scale, seed=spec.seed, **sources)
    return build, kwargs, request, serializable(dict(raw_start=raw, assigned_start=assigned))


def run(root, directory):
    from .run import verify_binding
    binding, data, prepared = verify_binding(root, execution=True)
    req = read(directory / "request.json")
    spec = invocation(req["invocation"])
    row = next(row for row in binding["manifest"]["arms"] if row["arm_id"] == req["arm_id"])
    cursor = model.cursor_for(row, req["checkpoint"])
    if spec.window.iteration != cursor.completed_intervals or spec.window.shard_id != f'{row["arm_id"]}-{req["phase"]}':
        raise ValueError("invocation/arm/phase/cursor mismatch")
    if req["binding_sha256"] != e3_prepared.digest(root / "binding.json"):
        raise ValueError("worker request differs from frozen binding")
    deadline = time.monotonic() + 5
    while not (directory / "launch.json").exists() and time.monotonic() < deadline:
        time.sleep(.01)
    launch = read(directory / "launch.json")
    if launch["pid"] != os.getpid() or launch["invocation"] != asdict(spec) or (root / "STOP").exists():
        raise ValueError("worker lacks supervised launch authority")
    if (directory / "lifecycle.json").exists():
        raise ValueError("completed invocation cannot execute again")
    events = []

    def phase(name):
        events.append(dict(phase=name, monotonic_seconds=time.monotonic(), utc=datetime.now(timezone.utc).isoformat()))
        atomic_json(directory / "phase.json", dict(invocation=asdict(spec), events=events))

    phase("before_ac_build")
    sources = dict(preceding=None, target_free=None, replay=None)
    from .runtime import source_values
    for key, ref in req["sources"].items():
        if ref is not None:
            sources[key] = source_values(root, ref, req, key, row)
    # Parent selects sources only from independently audited same-arm returns.
    # Worker replays those artifacts too; timeout replay uses original start.
    build, kwargs, request, start = construct_attempt(row, cursor, data, prepared,
        spec, req["phase"], sources)
    atomic_immutable_json(directory / "start.json", dict(invocation=asdict(spec),
        request_sha256=model.transfer.object_hash(serializable(asdict(request))), **start))
    phase("after_ac_build")
    record = dict(iteration=spec.window.iteration, request=req, invocation=asdict(spec), hard_target=spec.hard_target,
        window_request=serializable(asdict(request)), classification="rejected", exception=None,
        mathematical_input_sha256=e3_prepared.physical_input_digest(kwargs),
        preparation_policy=e3_prepared.policy_record("ac", req["phase"]),
        result=None, audit=None, named_costs=None, logical_solution=None)
    phase("before_ac_solve")
    captured = e3_prepared.prepared_start(build, spec.window.iteration)
    if spec.order == 0 and spec.window.iteration == 0 and req["phase"] == "component_first":
        frozen = next(x for x in binding["first_windows"] if x["arm_id"] == row["arm_id"])
        if (frozen["window"] != serializable(asdict(request))
                or frozen["start_sha256"] != model.transfer.object_hash(start["assigned_start"])
                or frozen["canonical_start_sha256"] != model.transfer.object_hash(captured)):
            raise ValueError("first primary differs from frozen physical/canonical preflight")
    if sources["replay"] is not None and captured != sources["replay"]["captured"]:
        raise ValueError("retained replay x0 differs from freshly prepared canonical start")
    atomic_gzip_json(directory / "x0.json.gz", captured)
    atomic_immutable_json(directory / "start-ready.json", dict(
        start_sha256=e3_prepared.digest(directory / "start.json"),
        x0_sha256=e3_prepared.digest(directory / "x0.json.gz")))
    try:
        # Authoritative production path: cost graph -> verified actual IPOPT x0
        # -> native result -> inverse restoration. No alternate solve boundary.
        build.solve(solver=model.CONFIG.ac.solver, **dict(model.CONFIG.ac.options))
    except Exception as exc:
        record["exception"] = f"{type(exc).__name__}: {exc}"
    phase("after_ac_solve")
    evidence = evidence_record(build.preparation_evidence)
    if evidence is None:
        raise RuntimeError(f'missing native/preparation evidence: {record["exception"]}')
    e3_prepared.verify_prepared_start(build.preparation_evidence, captured)
    record.update(native=evidence["native"], preparation_evidence=evidence)
    atomic_gzip_json(directory / "native.json.gz", dict(iteration=spec.window.iteration, native=evidence["native"]))
    atomic_gzip_json(directory / "preparation.json.gz", dict(iteration=spec.window.iteration, evidence=evidence))
    if evidence["native"].get("status") == 0:
        result = serializable(extract_results(build))
        named = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
        common, _ = e3_prepared.audit(build, result, kwargs, named)
        record.update(result=result, named_costs=named, audit=common,
                      logical_solution=serializable(physical_solution(build)))
    checked = audit.assess(record, kwargs, start, captured)
    record.update(independent_audit=checked, classification="accepted" if checked["passed"] else "rejected")
    verify_binding(root, execution=True)
    archive = atomic_gzip_json(directory / "record.json.gz", serializable(record))
    atomic_immutable_json(directory / "result.json", dict(invocation=asdict(spec), record=asdict(archive)))
    names = ("request.json", "monitoring.json", "phase.json", "start.json", "start-ready.json", "x0.json.gz",
             "native.json.gz", "preparation.json.gz", "record.json.gz", "result.json")
    if (directory / "replay-source.json").exists():
        names += ("replay-source.json",)
    atomic_immutable_json(directory / "completion.json", dict(invocation=asdict(spec),
        classification=record["classification"], artifacts={name: e3_prepared.digest(directory / name) for name in names}))
