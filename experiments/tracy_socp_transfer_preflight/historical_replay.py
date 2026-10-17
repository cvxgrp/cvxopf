"""Read-only historical compatibility, never permission to launch a worker.

Keep every bound mathematical/environment value. Only commit/clean metadata,
new analysis-package files, and exact recorded reader-only source transitions
may differ. Original reader bytes remain hash-verified in a source checkpoint.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCE_CHANGES = HERE / "REPLAY_SOURCE_CHANGES.json"
ANALYSIS_DIRECTORIES = tuple(
    f"experiments/{name}/"
    for name in (
        "tracy_socp_transfer_preflight",
        "tracy_battery_transfer_loop",
        "tracy_battery_transfer_slice",
        "tracy_battery_transfer_feasibility",
    )
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_matches(path, recorded, current=None):
    """Exact source equality or this checkpoint's explicit reader-only patch.

    Not a path ignore list: any later source change fails until independently
    reviewed and recorded. The original snapshot must still match its digest.
    """
    relative = (
        str(Path(path).relative_to(ROOT)) if Path(path).is_absolute() else str(path)
    )
    current = digest(ROOT / relative) if current is None else current
    if recorded == current:
        return True
    transitions = json.loads(SOURCE_CHANGES.read_text())["changes"]
    transition = transitions.get(relative)
    if transition is None:
        return False
    return (
        recorded == transition["before_sha256"]
        and current == transition["after_sha256"]
        and digest(ROOT / transition["snapshot_path"]) == recorded
    )


def inventory_matches(recorded, current):
    return set(recorded) == set(current) and all(
        source_matches(path, checksum, current[path])
        for path, checksum in recorded.items()
    )


def historical_context_matches(saved, current, ignored=()):
    """The preflight comparator, extended to local diagnostic source snapshots.

    Existing recovery-only ignore dispositions remain caller-supplied. Newly
    committed analysis files cannot replace/delete a bound mathematical source.
    Package/native-library versions, threads, inputs, and protocol remain exact.
    """
    saved, current = deepcopy(saved), deepcopy(current)
    for value in (saved, current):
        value.pop("commit", None)
        value.pop("clean", None)
    for key in ("sources", "slice_sources", "diagnostic_snapshot"):
        if key not in saved and key not in current:
            continue
        if key not in saved or key not in current:
            return False
        before, after = saved.pop(key), current.pop(key)
        excluded = set(ignored) if key == "sources" else set()
        if any(
            not path.startswith(ANALYSIS_DIRECTORIES)
            for path in (set(after) - set(before)) - excluded
        ):
            return False
        for path, checksum in before.items():
            if path not in excluded and (
                path not in after or not source_matches(path, checksum, after[path])
            ):
                return False
    return saved == current
