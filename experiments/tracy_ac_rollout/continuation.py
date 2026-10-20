"""Explicit operational restart: immutable predecessor, unchanged physics/budget.

Only the reviewed thermal-telemetry stop is admitted. This is not automatic
resume, final-iterate reuse, or permission to retry completed controlling solves.
"""
import os
import subprocess

from experiments.case118_tracy_2021.stage_d import read
from experiments.case118_tracy_2021 import e3_prepared
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.case118_annual_hierarchy.s5_speculative_process import Child
from experiments.case118_annual_hierarchy.s5_speculative_policy import Completion
from . import model

OPERATIONAL_EDIT_PATHS = frozenset({
    "experiments/tracy_ac_rollout/run.py",
    "experiments/tracy_ac_rollout/runtime.py",
    "experiments/tracy_ac_rollout/README.md",
    "experiments/tracy_ac_rollout/PROTOCOL.md",
    "experiments/tracy_ac_rollout/INTEGRATION_DESIGN.md",
})


def attempts(root):
    directories = list((root / "attempts").glob("*/ac-*-spec-*"))
    directories += list((root / "continuations").glob("*/attempts/*/ac-*-spec-*"))
    return sorted(directories)


def active(root):
    records = sorted((root / "continuations").glob("*/authority.json"))
    return None if not records else records[-1].parent


def outcome(root):
    directory = active(root) or root
    path = directory / "outcome.json"
    return read(path) if path.exists() else None


def ensure_stopped_processes(root):
    output = subprocess.check_output(["ps", "-axo", "pid=,command="], text=True)
    names = (str(root), str(root.relative_to(model.ROOT)))
    for line in output.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or int(fields[0]) in {os.getpid(), os.getppid()}:
            continue
        command = fields[1]
        if ("-m experiments.tracy_ac_rollout.run" in command
                and any(f"--{mode}" in command for mode in ("worker", "run", "resume"))
                and any(name in command for name in names)):
            raise ValueError("another rollout supervisor/worker is still live")


def retained_files(root):
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if relative.parts[0] == "continuations":
            continue
        if path.is_symlink():
            raise ValueError("retained evidence must not contain symlinks")
        if path.is_file() and not (path.name == "checkpoint.json" and relative.parts[0] == "arms"):
            result[str(relative)] = e3_prepared.digest(path)
    return result


def verify_retained(root, binding):
    for name, digest in binding["retained_files"].items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or e3_prepared.digest(path) != digest:
            raise ValueError("continuation predecessor evidence changed")
    if binding["run_binding_sha256"] != e3_prepared.digest(root / "binding.json"):
        raise ValueError("continuation original binding changed")


def expected_authority(binding, path):
    return dict(schema=1, experiment="tracy_ac_rollout_continuation", execution_authorized=True,
        continuation_binding_sha256=e3_prepared.digest(path), execution_commit=binding["context"]["commit"],
        primary_workers=2, helper_workers=1, maximum_solver_processes=3,
        recovery_policy="causal_first_speculative_v1", recovery_revision=2,
        thermal_policy="advisory", budgets=binding["budgets"])


def verify_authority(root, directory, *, execution=False):
    from .run import context, validate_context
    binding = read(directory / "continuation-binding.json")
    if directory.name != binding["generation"] or binding["budgets"] != read(root / "binding.json")["budgets"]:
        raise ValueError("continuation generation/budget changed")
    if read(directory / "authority.json") != expected_authority(binding, directory / "continuation-binding.json"):
        raise ValueError("continuation authority differs")
    validate_context(binding["context"], context(), execution=execution)
    verify_retained(root, binding)
    return binding


def prepare(root):
    from .run import context, status, no_optimization, validate_output
    validate_output(root)
    ensure_stopped_processes(root)
    if active(root) is not None:
        raise ValueError("this explicit restart supports only the original telemetry stop")
    if read(root / "outcome.json") != dict(classification="stopped", cleanup_error=None,
                                         reason="ResourceStop: required thermal telemetry unavailable"):
        raise ValueError("continuation requires the finalized original telemetry stop")
    with no_optimization():
        progress = status(root)
    if any(a["pending_durable_winner"] is not None for a in progress["arms"]):
        raise ValueError("durable pending winner requires reconciliation, not a new solve")
    directories = attempts(root)
    if progress["active_partial_attempts"] or any(not read(d / "supervision.json")["reaped"] for d in directories):
        raise ValueError("predecessor supervision is unfinished")
    if any(a["classification"] not in {"accepted", "launch_failure"} for a in progress["attempts"]):
        raise ValueError("completed unsuccessful attempts require a reviewed recovery reconciliation")
    if any(read(d / "request.json")["invocation"]["order"] != 0
           or read(d / "request.json")["phase"] != "component_first" for d in directories):
        raise ValueError("this continuation admits only the observed primary-only telemetry stop")
    current = context()
    if not current["clean"]:
        raise ValueError("continuation requires an owner-reviewed clean commit")
    old = read(root / "binding.json")
    if len(directories) >= old["budgets"]["max_launches"] or progress["finalized_worker_seconds"] >= old["budgets"]["total_worker_seconds"]:
        raise ValueError("predecessor exhausted the approved cumulative budget")
    generation = "continuation_001"
    binding = dict(schema=1, generation=generation, context=current,
        run_binding_sha256=e3_prepared.digest(root / "binding.json"), budgets=old["budgets"],
        inherited_launches=len(directories), inherited_worker_seconds=progress["finalized_worker_seconds"],
        retained_files=retained_files(root), initial_checkpoints={
            row["arm_id"]: read(root / "arms" / row["arm_id"] / "checkpoint.json") for row in old["manifest"]["arms"]})
    directory = root / "continuations" / generation
    directory.mkdir(parents=True, exist_ok=False)
    atomic_immutable_json(directory / "continuation-binding.json", binding)
    return directory


def restore_backend(root, backend, binding):
    """Restore finalized controller lookup only; never replay a consumed solve."""
    from .worker import invocation
    for arm, checkpoint in binding["initial_checkpoints"].items():
        for entry in checkpoint["windows"]:
            window = read(root / "arms" / arm / entry["relative_path"])
            record_path = root / window["selected_record"]["path"]
            record = read(record_path)
            spec = invocation(record["invocation"])
            backend.children[spec.attempt_id] = Child(spec, record_path.parent, 0.,
                completion=Completion(spec, "accepted", True), reaped=True)
    backend.inherited_children = len(backend.children)
    backend.inherited_launches = binding["inherited_launches"]
    backend.finalized_effort = binding["inherited_worker_seconds"]


def resume(root, authority):
    from .run import verify_binding
    from . import runtime
    directory = authority.resolve().parent
    if directory != root / "continuations" / "continuation_001" or authority.name != "authority.json":
        raise ValueError("resume requires its explicit continuation authority")
    if (directory / "attempts").exists() or (directory / "outcome.json").exists():
        raise ValueError("continuation already attempted; no automatic resume")
    ensure_stopped_processes(root)
    receipt = verify_authority(root, directory, execution=True)
    for arm, checkpoint in receipt["initial_checkpoints"].items():
        if read(root / "arms" / arm / "checkpoint.json") != checkpoint:
            raise ValueError("checkpoint changed after continuation preparation")
    binding, data, prepared = verify_binding(root, execution=True)
    atomic_immutable_json(directory / "monitoring.json", runtime.telemetry())
    runtime.execute(root, binding, data, prepared, continuation=receipt)
