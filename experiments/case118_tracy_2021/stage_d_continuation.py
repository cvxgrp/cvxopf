"""Append-only audit-only source continuations; execution evidence stays intact."""

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
            # Reconstruction has already classified completed versus interrupted
            # attempts. Preserve every existing evidence file, including partial
            # attempts whose policy slot remains pending.
            if path.exists():
                hashes[str(path.relative_to(root))] = digest(path)
        context_path = directory / "execution-context.json"
        if context_path.exists():
            hashes[str(context_path.relative_to(root))] = digest(context_path)
    previous = load(root)
    previous_path = record_paths(root)[-1] if previous else None
    record = dict(
        reason="Reviewed audit roundoff correction; no model, solver or policy change",
        original_binding_sha256=digest(root / "binding.json"),
        original_context=binding["context"],
        execution_context=current,
        historical_files=hashes,
        completed_hours=progress["completed_hours"],
        next_request=progress["next"],
    )
    if previous_path is not None:
        record.update(
            previous_context=previous["execution_context"],
            previous_record=model.reference(previous_path, root),
        )
    return record


def record_paths(root):
    """Keep the original filename; append numbered records without replacement."""
    paths = ([root / RECORD] if (root / RECORD).exists() else []) + sorted(
        root.glob("audit-continuation-[0-9]*.json")
    )
    expected = [root / RECORD] + [
        root / f"audit-continuation-{i:03d}.json" for i in range(1, len(paths))
    ]
    if paths and paths != expected:
        raise ValueError("audit continuation sequence is incomplete")
    return paths


def next_record_path(root):
    count = len(record_paths(root))
    return root / (RECORD if not count else f"audit-continuation-{count:03d}.json")


def load_chain(root, *, verify_prefix=True):
    binding = model.read(root / "binding.json")
    records = []
    paths = record_paths(root)
    for i, path in enumerate(paths):
        record = model.read(path)
        if (
            record["original_binding_sha256"] != digest(root / "binding.json")
            or record["original_context"] != binding["context"]
        ):
            raise ValueError("continuation original binding mismatch")
        compatible(record["original_context"], record["execution_context"])
        source_check(record["execution_context"]["commit"])
        if i:
            if (
                record.get("previous_record") != model.reference(paths[i - 1], root)
                or record.get("previous_context") != records[-1]["execution_context"]
            ):
                raise ValueError("continuation predecessor mismatch")
            require_descendant(
                record["previous_context"]["commit"],
                record["execution_context"]["commit"],
            )
            if any(
                record["historical_files"].get(name) != value
                for name, value in records[-1]["historical_files"].items()
            ):
                raise ValueError("continuation drops or changes historical evidence")
        if verify_prefix:
            for name, expected in record["historical_files"].items():
                if digest(root / name) != expected:
                    raise ValueError(
                        f"continuation historical artifact changed: {name}"
                    )
        records.append(record)
    return records


def load(root, *, verify_prefix=True):
    records = load_chain(root, verify_prefix=verify_prefix)
    return records[-1] if records else None


def require_descendant(previous, current):
    if previous == current or git("merge-base", previous, current) != previous:
        raise ValueError("continuation must advance from the previous execution commit")


def transition_needed(root, current, continue_from):
    """Validate an explicit transition; a retry after binding is idempotent."""
    previous = load(root)
    original = model.read(root / "binding.json")["context"]
    expected = previous["execution_context"] if previous else original
    if current == expected:
        predecessor = previous.get("previous_context", original) if previous else None
        if continue_from is None or (
            predecessor and predecessor["commit"] == continue_from
        ):
            return False
        raise ValueError("continuation must name the previous execution commit")
    if continue_from != expected["commit"]:
        raise ValueError("resume source differs; name the previous execution commit")
    compatible(original, current)
    source_check(current["commit"])
    require_descendant(expected["commit"], current["commit"])
    return True


def attempt_context(root, directory):
    """Resolve each retained attempt to its original execution era."""
    name = str((directory / "request.json").relative_to(root))
    records = load_chain(root, verify_prefix=False)
    for record in records:
        if name in record["historical_files"]:
            return record.get("previous_context", record["original_context"])
    return (
        records[-1]["execution_context"]
        if records
        else model.read(root / "binding.json")["context"]
    )


def execution_context(root):
    record = load(root, verify_prefix=False)
    return (
        record["execution_context"]
        if record
        else model.read(root / "binding.json")["context"]
    )
