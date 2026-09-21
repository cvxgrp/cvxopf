"""Run the frozen independent windows through the unchanged S5 race supervisor."""

from __future__ import annotations

from experiments.retained_paths import MANIFESTS, retained_operation

from dataclasses import asdict
from datetime import datetime, timezone
from importlib.metadata import version
import platform
import signal
import sys
import time

from experiments.case118_annual_hierarchy.run_s4b import _outer
from experiments.case118_annual_hierarchy.run_s0 import _software_versions
from experiments.case118_annual_hierarchy.s4_fixture import load_s4_fixture
from experiments.case118_annual_hierarchy.s5_speculative_archive import audit_candidate
from experiments.case118_annual_hierarchy.s5_speculative_policy import WindowKey
from experiments.case118_annual_hierarchy.s5_speculative_process import (
    SubprocessBackend,
)
from experiments.case118_annual_hierarchy.s5_speculative_supervisor import (
    SpeculativeSupervisor,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
)
from .sample import ROOT, OUT, read, checked, sha, ref


def validate_versions(current, historical, allowed_changes=None):
    """Permit only explicitly named historical -> current version pairs."""
    allowed_changes = allowed_changes or {}
    if current.keys() != historical.keys():
        raise ValueError("Software version fields differ from the historical study")
    for name, value in current.items():
        if name in allowed_changes:
            if (historical[name], value) != tuple(allowed_changes[name]):
                raise ValueError(f"Unapproved software version transition: {name}")
        elif value != historical[name]:
            raise ValueError(f"Software versions differ from the historical study: {name}")


@retained_operation()
def run(output=None, *, allowed_version_changes=None, provenance=None,
        additional_sources=()):
    output = OUT if output is None else output
    manifest = read(output / "sample.json")
    root = output / "run"
    source_paths = [
        p
        for base in (ROOT / "src", ROOT / "experiments/case118_annual_hierarchy")
        for p in base.rglob("*.py")
    ]
    source_paths.extend(
        ROOT / "experiments/case118_vectorization_replay" / name
        for name in ("__init__.py", "sample.py", "worker.py", "run.py")
    )
    source_paths.append(ROOT / "experiments/retained_paths.py")
    source_paths.extend(ROOT / manifest for manifest in MANIFESTS)
    source_paths.extend(additional_sources)
    sources = {str(p): sha(p) for p in source_paths}
    versions = _software_versions()
    for s in manifest["selected"]:
        historical_versions = checked(s["references"]["primary_request.json"])[
            "execution_context"
        ]["software_versions"]
        validate_versions(versions, historical_versions, allowed_version_changes)
    root.mkdir(exist_ok=False)
    atomic_immutable_json(
        root / "environment.json",
        dict(
            started_utc=datetime.now(timezone.utc).isoformat(),
            sample=ref(output / "sample.json"),
            python=platform.python_version(),
            platform=platform.platform(),
            execution_sources=sources,
            software_versions=versions,
            sparsediffpy_version=version("sparsediffpy"),
            allowed_version_changes=allowed_version_changes or {},
            provenance=provenance,
            policy="Unmodified SpeculativeSupervisor/WindowRace; two independent primary windows, one shared helper.",
            initialization="Frozen historical initial SoC, terminal target, and preceding controller; never another replay result.",
        ),
    )
    fixture, outer = load_s4_fixture(), _outer()
    selected = {
        WindowKey(s["shard_id"], s["iteration"]): s for s in manifest["selected"]
    }
    pending = list(selected)
    directories, audited, completed = {}, {}, []

    def command(spec, directory):
        free = [
            a
            for a in audited.values()
            if a.spec.window == spec.window
            and a.spec.source_slot == 1
            and a.completion.outcome == "accepted"
        ]
        free_dir = (
            directories[max(free, key=lambda a: a.spec.order).spec.attempt_id]
            if free
            else None
        )
        replay = directories.get(spec.replay_of)
        atomic_immutable_json(
            directory / "request.json",
            dict(
                selected=selected[spec.window],
                invocation=asdict(spec),
                execution_sources=sources,
                target_free_directory=str(free_dir)
                if free_dir is not None and spec.source_slot in (2, 3, 4, 5)
                else None,
                replay_start=None if replay is None else str(replay / "start.json"),
            ),
        )
        directories[spec.attempt_id] = directory
        return [
            sys.executable,
            "-m",
            "experiments.case118_vectorization_replay.worker",
            str(directory),
        ]

    def audit(spec, directory):
        s = selected[spec.window]
        expected = checked(s["references"]["primary_start.json"])["request_sha256"]
        candidate = audit_candidate(
            directory,
            spec,
            inputs=fixture.inputs,
            policy=fixture.policy,
            outer=outer,
            initial=s["initial_soc_mwh"],
            stop=s["iteration"] + 3,
            expected_request_sha256=expected,
        )
        audited[spec.attempt_id] = candidate
        return candidate.completion

    def publish(winner, attempts, paths):
        atomic_immutable_json(
            root / f"winner-{winner.window.iteration:06d}.json",
            dict(
                selected=selected[winner.window],
                winner=asdict(winner),
                completions=[asdict(a) for a in attempts],
                directories={k: str(v) for k, v in paths.items()},
            ),
        )

    def advance(winner, paths):
        # Completion bookkeeping only: there is no evolving physical checkpoint.
        completed.append(winner.window.iteration)
        atomic_json(root / "completed.json", dict(iterations=completed))
        print(
            f"{len(completed)}/{len(selected)}: hour {winner.window.iteration}, winner order {winner.order}",
            flush=True,
        )

    backend = SubprocessBackend(
        root, cwd=ROOT, command=command, audit=audit, publish=publish, advance=advance
    )
    supervisor = SpeculativeSupervisor(backend)

    def interrupted(signum, frame):
        raise KeyboardInterrupt(f"Signal {signum}")

    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        last_progress = time.monotonic()
        while pending or set(supervisor.races) - supervisor.finished:
            active = set(supervisor.races) - supervisor.finished
            while len(active) < 2:
                available = next(
                    (
                        k
                        for k in pending
                        if k.shard_id not in {a.shard_id for a in active}
                    ),
                    None,
                )
                if available is None:
                    break
                original = checked(
                    selected[available]["references"]["primary_request.json"]
                )
                supervisor.add_window(
                    available,
                    has_preceding=original["preceding_source"] is not None,
                    now=time.monotonic(),
                )
                pending.remove(available)
                active.add(available)
            supervisor.tick(time.monotonic())
            if time.monotonic() - last_progress >= 60:
                print(
                    f"Progress: {len(completed)}/{len(selected)}; active {[a.attempt_id for a in supervisor.active.values()]}",
                    flush=True,
                )
                last_progress = time.monotonic()
            time.sleep(1)
    except BaseException:
        if not supervisor.stopped:
            supervisor.stop("replay_interrupted", now=time.monotonic())
        raise
    atomic_immutable_json(
        root / "finished.json",
        dict(
            completed=len(completed),
            finished_utc=datetime.now(timezone.utc).isoformat(),
        ),
    )


if __name__ == "__main__":
    run()
