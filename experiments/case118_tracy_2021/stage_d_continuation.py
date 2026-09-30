"""One explicit audit-only continuation; original execution evidence stays intact."""

from .prepare import digest
from .run_stage_b import git
from . import stage_d as model

ORIGINAL = "f7aec7c562705749956adadd353216c780621a98"
AUDIT_FIX = "828002a84192762cd4538834a5e6d9acb8fc9857"
RECORD = "audit-continuation.json"
ALLOWED = {
    "experiments/case118_tracy_2021/run_stage_d.py",
    "experiments/case118_tracy_2021/stage_d.py",
    "experiments/case118_tracy_2021/stage_d_continuation.py",
    "experiments/case118_tracy_2021/STAGE_D_RUNNER.md",
    "tests/test_tracy_stage_d_runner.py",
    "tests/test_tracy_stage_d_continuation.py",
}
PREFIX_FILES = (
    "request.json",
    "start.json",
    "x0.json.gz",
    "result.json.gz",
    "completion.json",
    "supervision.json",
)


def compatible(old, new):
    if old["commit"] != ORIGINAL or not new["clean"]:
        raise ValueError(
            "continuation requires the original run and clean reviewed source"
        )
    if {k: v for k, v in old.items() if k != "commit"} != {
        k: v for k, v in new.items() if k != "commit"
    }:
        raise ValueError(
            "continuation changes environment, inputs or scientific policy"
        )


def source_check(commit):
    if git("merge-base", AUDIT_FIX, commit) != AUDIT_FIX:
        raise ValueError("continuation must descend from the reviewed audit correction")
    changed = set(git("diff", "--name-only", ORIGINAL, commit).splitlines())
    if changed - ALLOWED:
        raise ValueError(
            f"continuation changes out-of-scope source: {sorted(changed - ALLOWED)}"
        )


def prepare(root, current, progress):
    binding = model.read(root / "binding.json")
    compatible(binding["context"], current)
    source_check(current["commit"])
    if progress["blocking_failure"] or progress["active_attempt"]:
        raise ValueError("continuation requires a fully audited stopped prefix")
    hashes = {}
    for directory in sorted(root.glob("trajectory-*/hour-*/attempt-*")):
        for name in PREFIX_FILES:
            path = directory / name
            # This narrow continuation only supports complete, reauditable attempts.
            hashes[str(path.relative_to(root))] = digest(path)
    return dict(
        reason="Reviewed audit roundoff correction; no model, solver or policy change",
        original_binding_sha256=digest(root / "binding.json"),
        original_context=binding["context"],
        execution_context=current,
        historical_files=hashes,
        completed_hours=progress["completed_hours"],
        next_request=progress["next"],
    )


def load(root, *, verify_prefix=True):
    if not (root / RECORD).exists():
        return None
    record = model.read(root / RECORD)
    binding = model.read(root / "binding.json")
    if (
        record["original_binding_sha256"] != digest(root / "binding.json")
        or record["original_context"] != binding["context"]
    ):
        raise ValueError("continuation original binding mismatch")
    compatible(record["original_context"], record["execution_context"])
    source_check(record["execution_context"]["commit"])
    if verify_prefix:
        for name, expected in record["historical_files"].items():
            if digest(root / name) != expected:
                raise ValueError(f"continuation historical artifact changed: {name}")
    return record


def execution_context(root):
    record = load(root, verify_prefix=False)
    return (
        record["execution_context"]
        if record
        else model.read(root / "binding.json")["context"]
    )
