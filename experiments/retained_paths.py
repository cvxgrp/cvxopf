"""Resolve replay evidence paths; preserve recorded paths and evidence bytes."""

from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFESTS = (
    Path("experiments/case118_vectorization_replay/RESULT_LOCATIONS_20260921.json"),
    Path("experiments/case118_spacetime_pq_replay/RESULT_LOCATIONS_20260921.json"),
)
_index = ContextVar("retained_path_index", default=None)


def _load_index():
    locations = {}
    for manifest in MANIFESTS:
        for record in json.loads((ROOT / manifest).read_text())["files"]:
            old, new = Path(record["old"]), ROOT / record["new"]
            if old in locations and locations[old] != new:
                raise ValueError(f"Conflicting relocation for {old}")
            locations[old] = new
    return locations


@contextmanager
def retained_operation():
    """Load once for an operation; nested operations reuse the same index.

    Use as a context manager or entry-point decorator. The index is discarded
    on exit (including exceptions), so a later operation reads fresh manifests.
    """
    if _index.get() is not None:
        yield
        return
    token = _index.set(_load_index())
    try:
        yield
    finally:
        _index.reset(token)


def retained_path(saved):
    """Translate an exact recorded file; leave other paths unchanged.

    Standalone lookups are single-lookup operations. Batch callers should use
    retained_operation so manifest reads and indexing happen only once.
    """
    path = Path(saved)
    try:
        relative = path.relative_to(ROOT) if path.is_absolute() else path
    except ValueError:
        return path
    if not relative.parts or relative.parts[0] != "outputs":
        return path
    locations = _index.get()
    if locations is None:
        with retained_operation():
            return _index.get().get(relative, path)
    return locations.get(relative, path)


def verify_relocations():
    """Verify every retained file against the pre-move inventory; read only."""
    files = aliases = 0
    for manifest in sorted((ROOT / "experiments").glob("*/RESULT_LOCATIONS_20260921.json")):
        for record in json.loads(manifest.read_text())["files"]:
            old, new = ROOT / record["old"], ROOT / record["new"]
            if old.exists() or old.is_symlink():
                raise ValueError(f"Obsolete root path exists: {old}")
            if record.get("kind"):
                if not new.exists():
                    raise FileNotFoundError(new)
                aliases += 1
                continue
            content = new.read_bytes()
            if len(content) != record["bytes"] or hashlib.sha256(content).hexdigest() != record["sha256"]:
                raise ValueError(f"Relocated file changed: {new}")
            files += 1
    return dict(verified_files=files, removed_aliases=aliases)


if __name__ == "__main__":
    print(json.dumps(verify_relocations(), indent=2))
