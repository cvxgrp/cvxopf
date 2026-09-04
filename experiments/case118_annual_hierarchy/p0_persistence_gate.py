"""Executable P0 gate for persistence, resume, and build release."""

from __future__ import annotations

from dataclasses import dataclass
import gzip
import json
from pathlib import Path
import shutil
from typing import Any
import weakref

from experiments.case118_annual_hierarchy import streaming_driver
from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy.streaming_archive import (
    causal_source_from_archive,
)
from experiments.case118_annual_hierarchy.streaming_driver import (
    SafeBoundaryObserver,
    StreamingTrajectoryResult,
    run_streaming_trajectory,
)
from experiments.case118_annual_hierarchy.streaming_runner import (
    CausalControllerSource,
)


SOURCE_FINGERPRINT = "p0-persistence-gate-v1"
SCENARIO_HASH = "p0-case9-6h-persistence-v1"


@dataclass(frozen=True)
class PersistenceGateReport:
    """Results of the predeclared P0 persistence gate."""

    stopped_intervals: int
    stopped_status: str
    stopped_reason: str | None
    resumed_intervals: int
    reconstructed_attempt_id: str
    reconstructed_variable_count: int
    outer_build_released: bool
    ac_builds_released: bool
    corruption_cases_rejected: tuple[str, ...]
    prior_checkpoint_preserved: bool
    retry_boundary_intervals: int
    zero_boundary_recovered: bool
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def _run(
    directory: Path,
    *,
    resume: bool = False,
    observer: SafeBoundaryObserver | None = None,
) -> StreamingTrajectoryResult:
    fixture = load_p0_fixture(6)
    return run_streaming_trajectory(
        directory,
        fixture.inputs,
        fixture.policy,
        fixture.solve_config,
        source_fingerprint=SOURCE_FINGERPRINT,
        scenario_hash=SCENARIO_HASH,
        resume=resume,
        observer=observer,
        rss_reader=lambda: 123,
    )


def _controlling_source(
    directory: Path, relative_path: str
) -> CausalControllerSource:
    with gzip.open(directory / relative_path, "rt", encoding="utf-8") as stream:
        window = json.load(stream)
    controlling_id = window["executed_interval"]["controlling_attempt_id"]
    attempt = next(
        item for item in window["attempts"] if item["attempt_id"] == controlling_id
    )
    return causal_source_from_archive(attempt)


def _expect_resume_rejection(directory: Path) -> bool:
    try:
        _run(directory, resume=True)
    except (ValueError, OSError, EOFError):
        return True
    return False


def run_persistence_gate(root: Path) -> PersistenceGateReport:
    """Execute the complete non-scientific persistence and release gate."""
    root.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    release_dir = root / "release-resume"
    outer_refs: list[weakref.ReferenceType[object]] = []
    ac_refs: list[weakref.ReferenceType[object]] = []
    original_outer: Any = getattr(streaming_driver, "solve_frozen_outer")
    original_window: Any = getattr(streaming_driver, "execute_streaming_window")

    def capture_outer(*args: Any, **kwargs: Any) -> Any:
        outer = original_outer(*args, **kwargs)
        if outer.build is not None:
            outer_refs.append(weakref.ref(outer.build))
        return outer

    def capture_window(*args: Any, **kwargs: Any) -> Any:
        window = original_window(*args, **kwargs)
        ac_refs.extend(
            weakref.ref(attempt.build)
            for attempt in window.attempts
            if attempt.build is not None
        )
        return window

    setattr(streaming_driver, "solve_frozen_outer", capture_outer)
    setattr(streaming_driver, "execute_streaming_window", capture_window)
    try:
        partial = _run(
            release_dir,
            observer=lambda state: (
                "p0 safe boundary" if state.completed_intervals == 2 else None
            ),
        )
    finally:
        setattr(streaming_driver, "solve_frozen_outer", original_outer)
        setattr(streaming_driver, "execute_streaming_window", original_window)
    if (
        partial.status != "observer_terminated"
        or partial.completed_intervals != 2
        or partial.termination_reason != "p0 safe boundary"
    ):
        failures.append("observer_stop")
    outer_released = bool(outer_refs) and all(item() is None for item in outer_refs)
    ac_released = bool(ac_refs) and all(item() is None for item in ac_refs)
    if not outer_released:
        failures.append("outer_build_release")
    if not ac_released:
        failures.append("ac_build_release")
    source = _controlling_source(
        release_dir, partial.completed_window_artifacts[-1].relative_path
    )
    if not source.solution_values or any(
        values.flags.writeable for values in source.solution_values.values()
    ):
        failures.append("build_free_reconstruction")
    resumed = _run(release_dir, resume=True)
    if resumed.status != "complete" or resumed.completed_intervals != 6:
        failures.append("resume_completion")

    corruption_cases: list[str] = []
    for name in ("window", "checkpoint", "resource"):
        destination = root / f"corrupt-{name}"
        shutil.copytree(release_dir, destination)
        checkpoint_path = destination / "checkpoint.json"
        checkpoint = json.loads(checkpoint_path.read_text())
        if name == "window":
            window_path = destination / checkpoint["windows"][0]["relative_path"]
            window_path.write_bytes(window_path.read_bytes() + b"corruption")
        elif name == "checkpoint":
            checkpoint["realized_soc_mwh"][0] += 10.0
            checkpoint_path.write_text(json.dumps(checkpoint))
        else:
            resource_path = (
                destination / checkpoint["resource_evidence"]["relative_path"]
            )
            resource = json.loads(resource_path.read_text())
            resource["samples"][0]["rss_bytes"] += 1
            resource_path.write_text(json.dumps(resource))
        if _expect_resume_rejection(destination):
            corruption_cases.append(name)
        else:
            failures.append(f"corruption_{name}")

    atomic_dir = root / "atomic-checkpoint"
    atomic_partial = _run(
        atomic_dir,
        observer=lambda state: "one" if state.completed_intervals == 1 else None,
    )
    checkpoint_path = atomic_dir / "checkpoint.json"
    checkpoint_before = checkpoint_path.read_bytes()
    original_atomic_json: Any = getattr(streaming_driver, "atomic_json")
    failed_once = False

    def fail_checkpoint(path: Path, value: object) -> None:
        nonlocal failed_once
        if path == checkpoint_path and not failed_once:
            failed_once = True
            raise OSError("injected P0 checkpoint failure")
        original_atomic_json(path, value)

    setattr(streaming_driver, "atomic_json", fail_checkpoint)
    try:
        failed_publication = _run(atomic_dir, resume=True)
    finally:
        setattr(streaming_driver, "atomic_json", original_atomic_json)
    checkpoint_preserved = (
        atomic_partial.completed_intervals == 1
        and failed_publication.status == "artifact_failure"
        and checkpoint_path.read_bytes() == checkpoint_before
    )
    if not checkpoint_preserved:
        failures.append("checkpoint_atomicity")
    retry = _run(
        atomic_dir,
        resume=True,
        observer=lambda state: "retry" if state.completed_intervals == 2 else None,
    )
    if retry.completed_intervals != 2:
        failures.append("checkpoint_retry")

    zero_dir = root / "zero-boundary"
    zero_checkpoint = zero_dir / "checkpoint.json"
    original_atomic_json = getattr(streaming_driver, "atomic_json")
    failed_once = False

    def fail_zero_checkpoint(path: Path, value: object) -> None:
        nonlocal failed_once
        if path == zero_checkpoint and not failed_once:
            failed_once = True
            raise OSError("injected P0 zero-boundary failure")
        original_atomic_json(path, value)

    setattr(streaming_driver, "atomic_json", fail_zero_checkpoint)
    try:
        zero_failed = _run(zero_dir)
    finally:
        setattr(streaming_driver, "atomic_json", original_atomic_json)
    zero_recovered = False
    if (
        zero_failed.status == "artifact_failure"
        and (zero_dir / "outer-plan.json.gz").is_file()
        and not zero_checkpoint.exists()
    ):
        recovered = _run(
            zero_dir,
            resume=True,
            observer=lambda state: (
                "zero recovered" if state.completed_intervals == 1 else None
            ),
        )
        zero_recovered = recovered.completed_intervals == 1
    if not zero_recovered:
        failures.append("zero_boundary_recovery")

    return PersistenceGateReport(
        stopped_intervals=partial.completed_intervals,
        stopped_status=partial.status,
        stopped_reason=partial.termination_reason,
        resumed_intervals=resumed.completed_intervals,
        reconstructed_attempt_id=source.attempt_id,
        reconstructed_variable_count=len(source.solution_values),
        outer_build_released=outer_released,
        ac_builds_released=ac_released,
        corruption_cases_rejected=tuple(corruption_cases),
        prior_checkpoint_preserved=checkpoint_preserved,
        retry_boundary_intervals=retry.completed_intervals,
        zero_boundary_recovered=zero_recovered,
        failures=tuple(failures),
    )


__all__ = ["PersistenceGateReport", "run_persistence_gate"]
