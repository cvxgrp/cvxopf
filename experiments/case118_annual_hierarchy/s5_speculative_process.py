"""Real process backend for the speculative coordinator, without launch authority.

Domain callbacks prepare a command, independently audit a returned candidate,
and publish/advance a scientific window. This backend owns process groups,
phase clocks, immutable lifecycle receipts, and deduplicated memory sampling.
No callback may launch another solver: one command is one contender.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import BinaryIO, Callable, Mapping, Sequence, cast

from experiments.case118_annual_hierarchy.run_s4b import (
    process_observations,
    process_tree_usage,
)
from experiments.case118_annual_hierarchy.s5_speculative_attempt import (
    load_retained_start,
)
from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    POLICY_NAME,
)
from experiments.case118_annual_hierarchy.s5_speculative_supervisor import MemorySample
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    atomic_json,
    sha256_path,
)


def artifact_ref(path: Path) -> dict[str, object]:
    return {
        "relative_path": path.name,
        "bytes": path.stat().st_size,
        "sha256": sha256_path(path),
    }


@dataclass
class Child:
    spec: AttemptSpec
    directory: Path
    launched: float
    process: subprocess.Popen[bytes] | None = None
    log: BinaryIO | None = None
    completion: Completion | DirectCompletion | None = None
    reaped: bool = False


@dataclass(frozen=True)
class DirectCompletion:
    """Independent non-speculative audit; no AC replay/x0 contract is claimed."""

    attempt: AttemptSpec
    outcome: str

    def __post_init__(self):
        if self.outcome not in ("accepted", "rejected"):
            raise ValueError("direct completion must follow an independent audit")


class SubprocessBackend:
    """Concrete RaceBackend; never confuses worker exit with primal acceptance.

    Commands run in fresh process groups with file-backed stdout/stderr, avoiding
    full-pipe deadlocks. The supplied root must be a new continuation directory;
    existing attempts are never overwritten or silently retried.
    """

    def __init__(
        self,
        root: Path,
        *,
        cwd: Path,
        command: Callable[[AttemptSpec, Path], Sequence[str]],
        audit: Callable[[AttemptSpec, Path], Completion | DirectCompletion],
        publish: Callable[
            [AttemptSpec, Sequence[Completion], Mapping[str, Path]], None
        ],
        advance: Callable[[AttemptSpec, Mapping[str, Path]], None],
        clock: Callable[[], float] = time.monotonic,
        phase_prefix: str = "ac",
    ) -> None:
        if phase_prefix not in ("ac", "dc"):
            raise ValueError("phase prefix must be ac or dc")
        self.phase_prefix = phase_prefix
        self.root, self.cwd = root, cwd
        self.command, self.audit = command, audit
        self.publish, self.advance = publish, advance
        self.clock = clock
        # A shared monotonic epoch is used by this wave's subprocesses. This
        # paired anchor also locates every phase on the UTC wall-clock timeline.
        self.clock_anchor = {
            "monotonic_seconds": clock(),
            "utc": datetime.now(timezone.utc).isoformat(),
        }
        self.children: dict[str, Child] = {}
        self.events: list[dict[str, object]] = []
        self.samples: list[dict[str, object]] = []

    def launch(self, attempt: AttemptSpec) -> None:
        if attempt.attempt_id in self.children:
            raise ValueError("attempt already launched")
        # IDs are identities, not arbitrary output paths.
        directory = (
            self.root
            / attempt.window.shard_id
            / (
                f"{self.phase_prefix}-{attempt.window.iteration:06d}-spec-{attempt.order:02d}"
            )
        )
        if not directory.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("invalid shard directory")
        directory.mkdir(parents=True, exist_ok=False)
        child = Child(attempt, directory, self.clock())
        self.children[attempt.attempt_id] = child
        try:
            argv = list(self.command(attempt, directory))
            if not argv or any(not isinstance(item, str) or not item for item in argv):
                raise ValueError("worker command must be a nonempty argument vector")
            child.log = (directory / "worker.log").open("xb")
            child.launched = self.clock()
            child.process = subprocess.Popen(
                argv,
                cwd=self.cwd,
                stdin=subprocess.DEVNULL,
                stdout=child.log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            atomic_immutable_json(
                directory / "launch.json",
                {
                    "policy": POLICY_NAME,
                    "invocation": asdict(attempt),
                    "command": argv,
                    "pid": child.process.pid,
                    "monotonic_seconds": child.launched,
                    "clock_anchor": self.clock_anchor,
                },
            )
        except BaseException:
            self.cancel_and_reap(attempt, "launch_failure")
            raise

    def _phases(self, child: Child) -> list[Mapping[str, object]]:
        path = child.directory / "phase.json"
        if not path.exists():
            return []
        value = json.loads(path.read_text())
        if value.get("invocation") != asdict(child.spec):
            raise ValueError("phase invocation mismatch")
        events = cast(list[Mapping[str, object]], value["events"])
        previous = child.launched
        for event in events:
            stamp = float(cast(float, event["monotonic_seconds"]))
            if not math.isfinite(stamp) or stamp < previous:
                raise ValueError("phase clock is not monotonic")
            previous = stamp
        return events

    def solve_started_at(self, attempt: AttemptSpec) -> float | None:
        child = self.children[attempt.attempt_id]
        starts = [
            float(cast(float, item["monotonic_seconds"]))
            for item in self._phases(child)
            if item["phase"] == f"before_{self.phase_prefix}_solve"
        ]
        if len(starts) > 1:
            raise ValueError("single-attempt worker reported multiple solves")
        return starts[0] if starts else None

    def solve_returned_at(self, attempt: AttemptSpec) -> float | None:
        phases = self._phases(self.children[attempt.attempt_id])
        returns = [
            float(cast(float, item["monotonic_seconds"]))
            for item in phases
            if item["phase"] == f"after_{self.phase_prefix}_solve"
        ]
        if len(returns) > 1 or (returns and self.solve_started_at(attempt) is None):
            raise ValueError("invalid single-attempt solve return")
        return returns[0] if returns else None

    def has_complete_start(self, attempt: AttemptSpec) -> bool:
        child = self.children[attempt.attempt_id]
        path = child.directory / "start.json"
        if not path.exists():
            return False
        return load_retained_start(path).invocation == attempt

    def poll_audited(
        self, attempt: AttemptSpec
    ) -> Completion | DirectCompletion | None:
        child = self.children[attempt.attempt_id]
        process = child.process
        if process is None or process.poll() is None:
            return None
        if child.completion is None:
            if (
                process.returncode != 0
                or not (child.directory / "result.json").is_file()
            ):
                child.completion = Completion(
                    attempt, "construction_error", self.has_complete_start(attempt)
                )
            else:
                phases = [item["phase"] for item in self._phases(child)]
                if phases != [
                    f"before_{self.phase_prefix}_build",
                    f"after_{self.phase_prefix}_build",
                    f"before_{self.phase_prefix}_solve",
                    f"after_{self.phase_prefix}_solve",
                ]:
                    raise ValueError("returned candidate lacks complete solve phases")
                child.completion = self.audit(attempt, child.directory)
                if child.completion.attempt != attempt:
                    raise ValueError("auditor returned another invocation")
        return child.completion

    def _finish(self, child: Child, reason: str) -> None:
        if child.reaped:
            return
        if child.process is not None:
            child.process.wait()
        if child.log is not None:
            child.log.close()
        ended = self.clock()
        phases = self._phases(child)
        artifacts = {
            name: artifact_ref(child.directory / name)
            for name in (
                "launch.json",
                "request.json",
                "phase.json",
                "start.json",
                "result.json",
                "worker.log",
            )
            if (child.directory / name).is_file()
        }
        atomic_immutable_json(
            child.directory / "lifecycle.json",
            {
                "schema_version": 1,
                "policy": POLICY_NAME,
                "invocation": asdict(child.spec),
                "reason": reason,
                "launched_monotonic": child.launched,
                "reaped_monotonic": ended,
                "worker_wall_seconds": ended - child.launched,
                "clock_anchor": self.clock_anchor,
                "post_solve_seconds": next(
                    (
                        ended - float(cast(float, item["monotonic_seconds"]))
                        for item in phases
                        if item["phase"] == f"after_{self.phase_prefix}_solve"
                    ),
                    None,
                ),
                "returncode": None
                if child.process is None
                else child.process.returncode,
                "reaped": True,
                "phases": phases,
                "artifacts": artifacts,
                "completion": None
                if child.completion is None
                else asdict(child.completion),
            },
        )
        child.reaped = True

    def reap(self, attempt: AttemptSpec) -> None:
        child = self.children[attempt.attempt_id]
        if child.process is None or child.process.poll() is None:
            raise ValueError("cannot reap a still-running contender as returned")
        self._finish(child, "returned")

    def cancel_and_reap(self, attempt: AttemptSpec, reason: str) -> None:
        child = self.children.get(attempt.attempt_id)
        if child is None or child.reaped:
            return
        process = child.process
        if process is not None:
            # Kill the group even if its leader just exited: no surviving solver
            # descendant may outlive cancellation and consume the shared lease.
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        self._finish(child, reason)

    def memory(self) -> MemorySample:
        observations = process_observations()
        observed = {row.pid for row in observations}
        active = []
        for child in self.children.values():
            process = child.process
            if child.reaped or process is None:
                continue
            if process.pid in observed:
                active.append(process.pid)
            elif process.poll() is None:
                # A second sample distinguishes an ordinary exit/sample race.
                observations = process_observations()
                observed = {row.pid for row in observations}
                if process.pid in observed:
                    active.append(process.pid)
                elif process.poll() is None:
                    raise ValueError("live contender missing from RSS sample")
        usage = process_tree_usage([os.getpid(), *active], observations)
        workers = cast(Mapping[str, Mapping[str, object]], usage["per_worker"])
        sample = MemorySample(
            float(cast(float, usage["aggregate_rss_mib"])),
            tuple(float(cast(float, workers[str(pid)]["rss_mib"])) for pid in active),
        )
        self.samples.append(
            {
                "monotonic_seconds": self.clock(),
                "aggregate_mib": sample.aggregate_mib,
                "worker_trees_mib": list(sample.worker_trees_mib),
                "supervisor_current_rss_mib": next(
                    row.rss_mib for row in observations if row.pid == os.getpid()
                ),
            }
        )
        return sample

    def _directories(self, winner: AttemptSpec) -> dict[str, Path]:
        return {
            key: child.directory
            for key, child in self.children.items()
            if child.spec.window == winner.window
        }

    def publish_winner(
        self, winner: AttemptSpec, attempts: Sequence[Completion]
    ) -> None:
        self.publish(winner, attempts, self._directories(winner))

    def advance_checkpoint(self, winner: AttemptSpec) -> None:
        if any(
            not child.reaped
            for child in self.children.values()
            if child.spec.window == winner.window
        ):
            raise ValueError("cannot advance before every contender is reaped")
        self.advance(winner, self._directories(winner))

    def event(self, kind: str, attempt: AttemptSpec | None, now: float) -> None:
        self.events.append(
            {
                "kind": kind,
                "invocation": None if attempt is None else asdict(attempt),
                "monotonic_seconds": now,
            }
        )
        atomic_json(
            self.root / "supervisor-progress.json",
            {
                "policy": POLICY_NAME,
                "clock_anchor": self.clock_anchor,
                "events": self.events,
                "memory_samples": self.samples,
            },
        )
