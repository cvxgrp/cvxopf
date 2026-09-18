"""Locate unchanged records moved from outputs into this experiment."""

import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

EXPERIMENT = Path(__file__).resolve().parent
ROOT = EXPERIMENT.parents[1]


def retained_path(saved):
    """Resolve a recorded old path without changing the hashed record."""
    locations = json.loads((EXPERIMENT / "RESULT_LOCATIONS.json").read_text())
    value = str(saved)
    for item in locations["files"]:
        if value == item["old"] or value.endswith("/" + item["old"]):
            return ROOT / item["new"]
    return Path(saved)


def verify_recorded_source(execution):
    """Check the recorded commit's runtime, not today's possibly edited source."""
    raw = subprocess.check_output(
        ["git", "archive", execution["commit"], "src", "experiments"], cwd=ROOT
    )
    digest = hashlib.sha256()
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        for member in sorted(archive.getmembers(), key=lambda item: item.name):
            if member.isfile() and member.name.endswith(".py"):
                content = archive.extractfile(member).read()
                digest.update(member.name.encode() + b"\0" + content)
    assert digest.hexdigest() == execution["python_source_sha256"]
