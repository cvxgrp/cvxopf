"""Resolve preserved artifact paths without rewriting historical records."""

import argparse
import hashlib
import json
from pathlib import Path


def resolve_artifact(
    reference: str | Path,
    *,
    locations: Path = Path(__file__).with_name("RESULT_LOCATIONS.json"),
) -> Path:
    """Return a current artifact only after inventory and byte verification.

    Accept a recorded absolute path or a repository-relative current path.
    This is artifact inspection, not historical source/environment replay.
    """
    mapping = json.loads(locations.read_text())
    repository = (locations.parent / mapping["repository_root"]).resolve()
    inventory_bytes = (repository / mapping["inventory"]["path"]).read_bytes()
    if hashlib.sha256(inventory_bytes).hexdigest() != mapping["inventory"]["sha256"]:
        raise ValueError("evidence inventory hash mismatch")
    inventory = json.loads(inventory_bytes)
    requested = Path(reference)
    if ".." in requested.parts:
        raise ValueError("artifact path may not traverse parent directories")
    if not requested.is_absolute():
        requested = repository / requested
    for name, roots in mapping["roots"].items():
        current = repository / roots["current"]
        for prefix in (Path(roots["original"]), current):
            try:
                relative = requested.relative_to(prefix)
            except ValueError:
                continue
            entry = next(
                (
                    e
                    for e in inventory["entries"]
                    if e["root"] == name and e["path"] == relative.as_posix()
                ),
                None,
            )
            if entry is None:
                raise ValueError("artifact is not in the preserved inventory")
            target = current / relative
            content = target.read_bytes()
            if (
                len(content) != entry["bytes"]
                or hashlib.sha256(content).hexdigest() != entry["sha256"]
            ):
                raise ValueError("artifact size or hash mismatch")
            return target
    raise ValueError("artifact path is outside the preserved roots")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path)
    args = parser.parse_args()
    print(resolve_artifact(args.reference))
