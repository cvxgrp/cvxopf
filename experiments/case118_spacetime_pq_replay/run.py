"""Preflight or launch the committed 120+6 replay; never resample windows."""

from __future__ import annotations

from experiments.retained_paths import retained_operation

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

from experiments.case118_annual_hierarchy.run_s0 import _software_versions
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.case118_vectorization_replay import run as replay
from experiments.case118_vectorization_replay.sample import ROOT, checked, read, ref, sha

PREVIOUS = ROOT / "experiments/case118_vectorization_replay/results/case118_vectorization_replay"
OUTPUT = ROOT / "experiments/case118_spacetime_pq_replay/results/case118_spacetime_pq_replay"
SAMPLE_SHA256 = "f950b14b061b60a582526d5a4d30ac02124ef2f1a62ddc41e6c74343f8750ddb"
VERSION_CHANGES = {"cvxpy": ("1.9.2", "1.9.3")}


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def validate_destination(output, previous):
    output, previous = output.resolve(), previous.resolve()
    if output == previous or previous in output.parents or output in previous.parents:
        raise ValueError("New output must be separate from the retained replay")
    if output.exists():
        raise FileExistsError(f"Replay output already exists: {output}")


@retained_operation()
def preflight(*, commit=None, require_clean=False):
    """Read-only checks; no output creation, model solves, or subprocess workers."""
    validate_destination(OUTPUT, PREVIOUS)
    head = git("rev-parse", "HEAD")
    if commit is not None and commit != head:
        raise ValueError(f"Commit binding mismatch: expected {commit}, found {head}")
    dirty = git("status", "--porcelain", "--untracked-files=normal")
    if require_clean and dirty:
        raise ValueError("Commit the reviewed runner changes before launch")
    sample_path = PREVIOUS / "sample.json"
    if sha(sample_path) != SAMPLE_SHA256:
        raise ValueError("Frozen sample hash changed")
    manifest = read(sample_path)
    selected = manifest["selected"]
    if (len({s["iteration"] for s in selected}) != 126
            or Counter(s["historical_group"] for s in selected)
            != {"primary": 120, "helper": 6}):
        raise ValueError("Expected the frozen 120 primary and six helper windows")
    refs = {r["path"]: r for s in selected for r in s["references"].values()}
    for reference in refs.values():
        checked(reference)
    versions = _software_versions()
    if version("sparsediffpy") != "0.6.1":
        raise ValueError("This replay requires sparsediffpy 0.6.1")
    for s in selected:
        historical = checked(s["references"]["primary_request.json"])
        replay.validate_versions(
            versions, historical["execution_context"]["software_versions"], VERSION_CHANGES,
        )
    previous_environment = read(PREVIOUS / "run/environment.json")
    if previous_environment["sample"]["sha256"] != SAMPLE_SHA256:
        raise ValueError("Previous replay used a different sample")
    checked(previous_environment["sample"])
    replay.validate_versions(versions, previous_environment["software_versions"], VERSION_CHANGES)
    if read(PREVIOUS / "run/finished.json")["completed"] != 126:
        raise ValueError("Previous replay is incomplete")
    with (PREVIOUS / "comparison.csv").open(newline="") as stream:
        previous_rows = list(csv.DictReader(stream))
    if (len(previous_rows) != 126 or {int(r["iteration"]) for r in previous_rows}
            != {s["iteration"] for s in selected}):
        raise ValueError("Previous comparison does not contain the frozen sample")
    evidence = {name: ref(PREVIOUS / name) for name in (
        "sample.json", "comparison.csv", "run/environment.json", "run/finished.json",
    )}
    return dict(
        checked_utc=datetime.now(timezone.utc).isoformat(),
        commit=head, working_tree_clean=not dirty, working_tree_status=dirty,
        python_executable=sys.executable,
        windows=len(selected), historical_references_verified=len(refs),
        sample=ref(sample_path), previous_replay=evidence,
        software_versions=versions, sparsediffpy_version=version("sparsediffpy"),
        dependency_transition={"cvxpy": ["1.9.2", "1.9.3"],
                               "sparsediffpy": ["0.3.0", "0.6.1"]},
        primary_workers=2, shared_helper_workers=1, output=str(OUTPUT),
    )


@retained_operation()
def launch(commit, *, fan_on):
    if not fan_on:
        raise ValueError("Confirm the fan is on before this replay")
    binding = preflight(commit=commit, require_clean=True)
    binding["cooling"] = "Owner confirmed external fan on before launch; keep on throughout."
    # Creating the destination is the start boundary. Never reuse a partial run.
    OUTPUT.mkdir(parents=True)
    (OUTPUT / "sample.json").write_bytes(Path(binding["sample"]["path"]).read_bytes())
    atomic_immutable_json(OUTPUT / "binding.json", binding)
    from .telemetry import TemperatureCollector

    sources = [*sorted(Path(__file__).parent.glob("*.py")),
               Path(__file__).with_name("PLAN.md"), ROOT / "pyproject.toml", ROOT / "uv.lock"]
    with TemperatureCollector(OUTPUT / "temperature_telemetry"):
        replay.run(OUTPUT, allowed_version_changes=VERSION_CHANGES,
                   provenance=binding, additional_sources=sources)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true", help="Read-only; never launch")
    parser.add_argument("--commit", help="Exact owner-reviewed HEAD SHA required for launch")
    parser.add_argument("--fan-on", action="store_true")
    args = parser.parse_args()
    if args.preflight:
        print(json.dumps(preflight(commit=args.commit), indent=2))
    elif args.commit is None:
        parser.error("Launch requires --commit and --fan-on")
    else:
        launch(args.commit, fan_on=args.fan_on)
