"""Shared-helper orchestration with injectable process/artifact effects.

The coordinator is the only owner of helper leases and winner transactions.
The backend must audit returned results and implement the existing process-tree
termination/reaping rules. No annual entry point selects this policy yet.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Callable, Protocol, Sequence

from experiments.case118_annual_hierarchy.s5_speculative_policy import (
    AttemptSpec,
    Completion,
    FINAL_SOURCE_SLOTS,
    HelperQueue,
    WindowKey,
    WindowRace,
    HELPER_SOURCE_SLOTS,
)


@dataclass(frozen=True)
class MemorySample:
    """Deduplicated current RSS, including all solver descendants, in MiB."""

    aggregate_mib: float
    worker_trees_mib: tuple[float, ...]

    def __post_init__(self) -> None:
        for value in (self.aggregate_mib, *self.worker_trees_mib):
            if not math.isfinite(value) or value < 0:
                raise ValueError("RSS evidence must be finite and nonnegative")


@dataclass(frozen=True)
class MemoryPolicy:
    worker_limit_mib: float = 16_384.0
    aggregate_limit_mib: float = 24_576.0
    helper_reserve_mib: float = 8_192.0
    pressure_mib: float = 22_528.0

    def __post_init__(self) -> None:
        if (self.worker_limit_mib, self.aggregate_limit_mib) != (16_384.0, 24_576.0):
            raise ValueError(
                "speculative policy must preserve existing hard RSS limits"
            )
        if (self.helper_reserve_mib, self.pressure_mib) != (8_192.0, 22_528.0):
            raise ValueError("speculative admission settings differ from frozen v1")

    def hard_crossing(self, sample: MemorySample) -> bool:
        return sample.aggregate_mib > self.aggregate_limit_mib or any(
            value > self.worker_limit_mib for value in sample.worker_trees_mib
        )

    def admits_helper(self, sample: MemorySample) -> bool:
        return not self.hard_crossing(sample) and (
            sample.aggregate_mib + self.helper_reserve_mib <= self.pressure_mib
        )


class RaceBackend(Protocol):
    """Process/artifact boundary; all times use the supervisor's monotonic clock."""

    def launch(self, attempt: AttemptSpec) -> None: ...
    def solve_started_at(self, attempt: AttemptSpec) -> float | None: ...
    def solve_returned_at(self, attempt: AttemptSpec) -> float | None: ...
    def poll_audited(self, attempt: AttemptSpec) -> Completion | None: ...
    def reap(self, attempt: AttemptSpec) -> None: ...
    def cancel_and_reap(self, attempt: AttemptSpec, reason: str) -> None: ...
    def has_complete_start(self, attempt: AttemptSpec) -> bool: ...
    def memory(self) -> MemorySample: ...
    def publish_winner(
        self, winner: AttemptSpec, attempts: Sequence[Completion]
    ) -> None: ...
    def advance_checkpoint(self, winner: AttemptSpec) -> None: ...
    def event(self, kind: str, attempt: AttemptSpec | None, now: float) -> None: ...


class UnresolvedWindow(RuntimeError):
    pass


class ResourceStop(RuntimeError):
    pass


class SpeculativeSupervisor:
    """Drive up to two shard windows and one shared helper one tick at a time.

    A production root supplies real process/artifact effects. Tests use the
    same coordinator with simulated workers. A completed window is not returned
    until winner publication, loser reaping, and checkpoint advancement succeed.
    Caller-owned shard/wave audits must gate admission of the next wave.
    """

    def __init__(self, backend: RaceBackend, *, race_factory: Callable = WindowRace) -> None:
        self.backend = backend
        self.race_factory = race_factory
        self.memory_policy = MemoryPolicy()
        self.queue = HelperQueue()
        self.races: dict[WindowKey, WindowRace] = {}
        self.active: dict[str, AttemptSpec] = {}
        self.leased_helpers: set[str] = set()
        self.replacement_helpers: set[str] = set()
        self.finished: set[WindowKey] = set()
        self.no_more_helpers: set[WindowKey] = set()
        self.last_time: float | None = None
        self.stopped = False

    def add_window(self, window: WindowKey, *, has_preceding: bool, now: float) -> None:
        self._time(now)
        if self.stopped or window in self.races:
            raise ValueError("cannot admit window into stopped/duplicate race")
        pending = set(self.races) - self.finished
        if len(pending) >= 2 or any(key.shard_id == window.shard_id for key in pending):
            raise ValueError("only one window per shard and two shards may be active")
        race = self.race_factory(window, has_preceding=has_preceding)
        self.races[window] = race
        try:
            self._launch(race.primary, now)
        except BaseException:
            self.stop("launch_failure", now=now)
            raise

    def _time(self, now: float) -> None:
        if (
            not math.isfinite(now)
            or now < 0
            or (self.last_time is not None and now < self.last_time)
        ):
            raise ValueError("supervisor clock must be finite and monotonic")
        self.last_time = now

    def _launch(
        self, attempt: AttemptSpec, now: float, *, replacement_lane: bool = False
    ) -> None:
        if len(self.active) >= 3:
            raise RuntimeError("three-solver concurrency ceiling exceeded")
        # Register first so cleanup also handles a partially failed launch.
        self.active[attempt.attempt_id] = attempt
        self.backend.launch(attempt)
        self.backend.event(
            "replacement_launched" if replacement_lane else "launched", attempt, now
        )

    def _reaped(self, attempt: AttemptSpec) -> None:
        self.active.pop(attempt.attempt_id)
        self.replacement_helpers.discard(attempt.attempt_id)
        if attempt.attempt_id in self.leased_helpers:
            self.leased_helpers.remove(attempt.attempt_id)
            self.queue.release_after_reap(attempt.window)

    def _cancel(self, attempt: AttemptSpec, reason: str, now: float) -> None:
        retained = self.backend.has_complete_start(attempt)
        self.backend.cancel_and_reap(attempt, reason)
        self._reaped(attempt)
        completion = Completion(
            attempt, "timeout" if reason == "solve_budget" else "canceled", retained
        )
        self.races[attempt.window].complete_batch([completion])
        self.backend.event(reason, attempt, now)

    def stop(self, reason: str, *, now: float) -> None:
        """Best-effort cleanup of every contender, even if one cleanup fails."""
        self.stopped = True
        errors: list[BaseException] = []
        for attempt in tuple(self.active.values()):
            try:
                self.backend.cancel_and_reap(attempt, reason)
                self._reaped(attempt)
            except BaseException as exc:
                errors.append(exc)
        self.backend.event(reason, None, now)
        if errors:
            raise RuntimeError(
                "one or more contender processes could not be reaped"
            ) from errors[0]

    def tick(self, now: float) -> tuple[WindowKey, ...]:
        self._time(now)
        if self.stopped:
            raise ValueError("stopped supervisor cannot resume without reconstruction")
        try:
            return self._tick(now)
        except BaseException:
            self.stop("supervisor_stopped", now=now)
            raise

    def _tick(self, now: float) -> tuple[WindowKey, ...]:
        sample = self.backend.memory()
        if self.memory_policy.hard_crossing(sample):
            self.backend.event("hard_rss_crossing", None, now)
            raise ResourceStop("hard RSS limit crossed")

        # Collect before deadline checks: an already returned candidate is audited
        # even if the polling tick is after its nominal deadline.
        batches: dict[WindowKey, list[Completion]] = {}
        for attempt in tuple(self.active.values()):
            completion = self.backend.poll_audited(attempt)
            if completion is not None:
                if completion.attempt != attempt:
                    raise ValueError("backend returned a different invocation")
                self.backend.reap(attempt)
                self._reaped(attempt)
                batches.setdefault(attempt.window, []).append(completion)
                self.backend.event("audited_return", attempt, now)
        for key, completions in batches.items():
            self.races[key].complete_batch(completions)

        finished = []
        for key, race in self.races.items():
            if key in self.finished or race.winner is None:
                continue
            # Durable decision first. Failure after this point is reconciled from
            # the retained decision, never by electing another winner on restart.
            self.backend.publish_winner(race.winner, tuple(race.completed.values()))
            self.queue.cancel_waiting(key)
            for loser in tuple(race.cancel_after_winner):
                self._cancel(loser, "lost_race", now)
            self.backend.advance_checkpoint(race.winner)
            self.finished.add(key)
            finished.append(key)
            self.backend.event("checkpoint_advanced", race.winner, now)

        sample = self.backend.memory()
        if self.memory_policy.hard_crossing(sample):
            self.backend.event("hard_rss_crossing", None, now)
            raise ResourceStop("hard RSS limit crossed")
        for attempt in tuple(self.active.values()):
            if attempt.order == 0:
                continue  # a primary never times out under this policy
            started = self.backend.solve_started_at(attempt)
            if started is not None and (not math.isfinite(started) or started < 0):
                raise ValueError("invalid helper solve-start clock")
            if sample.aggregate_mib >= self.memory_policy.pressure_mib:
                self._cancel(attempt, "memory_pressure", now)
            elif (
                started is not None
                and self.backend.solve_returned_at(attempt) is None
                and attempt.budget_seconds is not None
                and now - started >= attempt.budget_seconds
            ):
                self._cancel(attempt, "solve_budget", now)

        for key, race in self.races.items():
            if key in self.finished:
                continue
            if race.exhausted:
                raise UnresolvedWindow(f"no accepted controller for {key}")
            if key in self.no_more_helpers or self.queue.active == key:
                continue
            started = self.backend.solve_started_at(race.primary)
            if started is not None and (not math.isfinite(started) or started < 0):
                raise ValueError("invalid primary solve-start clock")
            # A child can publish its start after this tick's timestamp while
            # the parent is polling. Wait until the next tick; do not treat that
            # ordinary interleaving as a clock/provenance failure.
            returned = self.backend.solve_returned_at(race.primary)
            elapsed = (
                0.0
                if started is None
                else max(0.0, (now if returned is None else returned) - started)
            )
            if race.needs_help(elapsed):
                has_had_helper = any(spec.order > 0 for spec in race.launched.values())
                eligible_at = (
                    started + 300.0
                    if started is not None
                    and not has_had_helper
                    and race.primary.attempt_id not in race.completed
                    else now
                )
                if self.queue.request(key, eligible_at=eligible_at):
                    self.backend.event("helper_eligible", race.primary, eligible_at)

        # A returned primary contributes one replacement lane to its own window.
        # That lane can advance the final sweep while the shared helper remains
        # queued fairly across both shard windows.
        admitted = self.memory_policy.admits_helper(self.backend.memory())
        replacement_launched = False
        if admitted:
            for key, race in self.races.items():
                if key in self.finished or len(self.active) >= 3:
                    continue
                replacement_active = any(
                    attempt_id in self.replacement_helpers
                    for attempt_id in self.active
                    if self.active[attempt_id].window == key
                )
                if race.primary.attempt_id not in race.completed or replacement_active:
                    continue
                final = race.next_final()
                if final is not None:
                    self.replacement_helpers.add(final.attempt_id)
                    self._launch(final, now, replacement_lane=True)
                    replacement_launched = True

        # Use a fresh RSS sample after result processing/cancellation. Launch at
        # most one helper per tick, even when unavailable sources are skipped.
        admitted = not replacement_launched and self.memory_policy.admits_helper(
            self.backend.memory()
        )
        helper_window = self.queue.acquire(memory_admitted=admitted)
        if helper_window is not None:
            race = self.races[helper_window]
            prior_unavailable = set(race.unavailable)
            helper = race.next_helper()
            for order in sorted(set(race.unavailable) - prior_unavailable):
                source_slot = (
                    HELPER_SOURCE_SLOTS[order - 1]
                    if 1 <= order <= 8
                    else FINAL_SOURCE_SLOTS[order - 11]
                )
                self.backend.event(
                    race.unavailable[order],
                    AttemptSpec(helper_window, order, source_slot),
                    now,
                )
            if helper is None:
                self.queue.release_after_reap(helper_window)  # no process was launched
                if race.waiting_for_primary or race.waiting_for_final_source:
                    self.backend.event(
                        "final_recovery_waiting_for_source", race.primary, now
                    )
                else:
                    self.no_more_helpers.add(helper_window)
                    self.backend.event("helpers_exhausted", race.primary, now)
                if self.races[helper_window].exhausted:
                    raise UnresolvedWindow(
                        f"no accepted controller for {helper_window}"
                    )
            else:
                self.leased_helpers.add(helper.attempt_id)
                self._launch(helper, now)
        return tuple(finished)
