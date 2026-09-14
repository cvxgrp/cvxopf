"""Pure scheduling rules for the proposed S5 speculative continuation.

No processes, archives, or physical states are changed here. The runtime must
independently audit completions, archive a winner before advancing state, and
reap a process before releasing its helper lease. Keeping those effects outside
this module lets scheduling be tested with synthetic clocks and outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, Mapping, Sequence

from experiments.case118_annual_hierarchy.streaming_schema import (
    PERTURBATION_SCALES,
    perturbation_seed,
)

POLICY_NAME = "causal_first_speculative_v1"
SOLVE_BUDGET_SECONDS = 300.0
HELPER_SOURCE_SLOTS = (6, 7, 8, 1, 2, 3, 4, 5)
Outcome = Literal["accepted", "rejected", "timeout", "canceled", "construction_error"]


def _nonnegative(value: float, label: str) -> None:
    if isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and nonnegative")


@dataclass(frozen=True, order=True)
class WindowKey:
    shard_id: str
    iteration: int

    def __post_init__(self) -> None:
        if not self.shard_id.strip():
            raise ValueError("shard_id must not be empty")
        if (
            isinstance(self.iteration, bool)
            or not isinstance(self.iteration, int)
            or self.iteration < 0
        ):
            raise ValueError("iteration must be a nonnegative integer")


@dataclass(frozen=True)
class AttemptSpec:
    """Invocation order is deliberately separate from the legacy source slot."""

    window: WindowKey
    order: int  # primary=0, bounded helpers=1..8, uncapped replay=9
    source_slot: int
    replay_of: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.order, bool) or not isinstance(self.order, int):
            raise ValueError("invalid invocation order")
        if isinstance(self.source_slot, bool) or not isinstance(self.source_slot, int):
            raise ValueError("invalid source slot")
        if self.order == 0:
            valid = self.source_slot == 0 and self.replay_of is None
        elif 1 <= self.order <= 8:
            valid = (
                self.source_slot == HELPER_SOURCE_SLOTS[self.order - 1]
                and self.replay_of is None
            )
        elif self.order == 9:
            valid = self.source_slot in (2, 3, 4, 5, 6, 7, 8) and bool(self.replay_of)
        else:
            valid = False
        if not valid:
            raise ValueError("invocation order/source/replay mismatch")

    @property
    def attempt_id(self) -> str:
        return (
            f"{self.window.shard_id}/ac-{self.window.iteration:06d}"
            f"/spec-v1-{self.order:02d}"
        )

    @property
    def hard_target(self) -> bool:
        return self.source_slot != 1

    @property
    def budget_seconds(self) -> float | None:
        return SOLVE_BUDGET_SECONDS if 1 <= self.order <= 8 else None

    @property
    def scale(self) -> float | None:
        return (
            PERTURBATION_SCALES[(self.source_slot - 3) % 3]
            if self.source_slot >= 3
            else None
        )

    @property
    def seed(self) -> int | None:
        return (
            perturbation_seed(self.window.iteration, self.source_slot)
            if self.source_slot >= 3
            else None
        )


def normalized_violation(
    residuals: Mapping[str, float], limits: Mapping[str, float]
) -> float | None:
    """Rank only complete residual evidence; never invent missing zeros.

    The production adapter must supply the full frozen box/network/coupling
    limit mapping. Extra reporting residuals may be present. A zero tolerance
    permits only an exactly zero residual; positive violation ranks as infinity.
    This score is for start selection, not solver acceptance.
    """
    if not limits:
        raise ValueError("ranking requires a nonempty complete limit mapping")
    for name, limit in limits.items():
        _nonnegative(limit, f"limit {name}")
    if not limits.keys() <= residuals.keys():
        return None
    ratios = []
    for name, limit in limits.items():
        residual = residuals[name]
        if isinstance(residual, bool) or not math.isfinite(residual) or residual < 0:
            return None
        ratios.append(
            residual / limit if limit > 0 else (0.0 if residual == 0 else math.inf)
        )
    return max(ratios)


@dataclass(frozen=True)
class Completion:
    """Outcome supplied after independent audit, not an IPOPT status shortcut."""

    attempt: AttemptSpec
    outcome: Outcome
    complete_x0_retained: bool = False
    normalized_residual: float | None = None
    objective: float | None = None

    def __post_init__(self) -> None:
        if self.outcome not in {
            "accepted",
            "rejected",
            "timeout",
            "canceled",
            "construction_error",
        }:
            raise ValueError("unknown completion outcome")
        score = self.normalized_residual
        if score is not None and (
            isinstance(score, bool) or math.isnan(score) or score < 0
        ):
            raise ValueError("normalized residual must be nonnegative or unavailable")
        if self.outcome != "rejected" and score is not None:
            raise ValueError("ranking residuals belong to returned rejected candidates")
        if self.outcome == "accepted" and not self.complete_x0_retained:
            raise ValueError("accepted candidate requires retained complete x0")


def select_uncapped_replay(completions: Sequence[Completion]) -> AttemptSpec | None:
    """Select a bounded hard-target start; replay its original x0, not an iterate."""
    if len({item.attempt.attempt_id for item in completions}) != len(completions):
        raise ValueError("duplicate attempt in replay candidates")
    if len({item.attempt.window for item in completions}) > 1:
        raise ValueError("replay candidates must belong to one window")
    candidates = [
        item
        for item in completions
        if 1 <= item.attempt.order <= 8
        and item.attempt.hard_target
        and item.complete_x0_retained
    ]
    ranked = [
        item
        for item in candidates
        if item.outcome == "rejected" and item.normalized_residual is not None
    ]

    def rank(item: Completion) -> tuple[float, float, int]:
        objective = item.objective
        return (
            item.normalized_residual
            if item.normalized_residual is not None
            else math.inf,
            objective
            if objective is not None and math.isfinite(objective)
            else math.inf,
            item.attempt.order,
        )

    if ranked:
        selected = min(ranked, key=rank)
    else:
        timed_out = [item for item in candidates if item.outcome == "timeout"]
        if not timed_out:
            return None
        selected = min(timed_out, key=lambda item: item.attempt.order)
    return AttemptSpec(
        selected.attempt.window,
        9,
        selected.attempt.source_slot,
        replay_of=selected.attempt.attempt_id,
    )


class WindowRace:
    """One window's scheduling state; no filesystem or physical-state ownership.

    `complete_batch` selects a candidate for publication. The caller must not
    launch the next window until publication, exact-once checkpoint advancement,
    and loser cleanup have all succeeded. Recovery of that transaction belongs
    to the archive/runtime integration, not this transient scheduling object.
    """

    def __init__(self, window: WindowKey, *, has_preceding: bool) -> None:
        self.window = window
        self.has_preceding = has_preceding
        self.primary = AttemptSpec(window, 0, 0)
        self.launched: dict[str, AttemptSpec] = {self.primary.attempt_id: self.primary}
        self.completed: dict[str, Completion] = {}
        self.unavailable: dict[int, str] = {}
        self.winner: AttemptSpec | None = None
        self._next_order = 1
        self._replay_considered = False

    def needs_help(self, primary_solve_elapsed: float) -> bool:
        _nonnegative(primary_solve_elapsed, "primary solve elapsed")
        return self.winner is None and (
            self.primary.attempt_id in self.completed
            or primary_solve_elapsed >= SOLVE_BUDGET_SECONDS
        )

    @property
    def active_attempts(self) -> tuple[AttemptSpec, ...]:
        return tuple(
            spec for key, spec in self.launched.items() if key not in self.completed
        )

    def next_helper(self) -> AttemptSpec | None:
        """Call only after eligibility, memory admission, and helper lease grant."""
        if self.winner is not None:
            return None
        if any(spec.order > 0 for spec in self.active_attempts):
            raise ValueError("a helper invocation is still active")
        target_free_accepted = any(
            item.attempt.source_slot == 1 and item.outcome == "accepted"
            for item in self.completed.values()
        )
        while self._next_order <= 8:
            order = self._next_order
            self._next_order += 1
            spec = AttemptSpec(self.window, order, HELPER_SOURCE_SLOTS[order - 1])
            if spec.source_slot >= 6 and not self.has_preceding:
                self.unavailable[order] = "preceding_controller_unavailable"
                continue
            if spec.source_slot in (2, 3, 4, 5) and not target_free_accepted:
                self.unavailable[order] = "accepted_target_free_unavailable"
                continue
            self.launched[spec.attempt_id] = spec
            return spec
        if not self._replay_considered:
            self._replay_considered = True
            replay = select_uncapped_replay(tuple(self.completed.values()))
            if replay is not None:
                self.launched[replay.attempt_id] = replay
                return replay
        return None

    def complete_batch(self, completions: Sequence[Completion]) -> AttemptSpec | None:
        """First audited batch wins; primary precedes helper in a simultaneous batch."""
        ids = [item.attempt.attempt_id for item in completions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate completion in batch")
        for item in completions:
            key = item.attempt.attempt_id
            if self.launched.get(key) != item.attempt:
                raise ValueError("completion does not match a launched invocation")
            if key in self.completed:
                raise ValueError("invocation already completed")
        # Validation above is atomic: malformed batches leave state unchanged.
        for item in sorted(completions, key=lambda value: value.attempt.order):
            self.completed[item.attempt.attempt_id] = item
            if (
                self.winner is None
                and item.outcome == "accepted"
                and item.attempt.hard_target
            ):
                self.winner = item.attempt
        return self.winner

    @property
    def cancel_after_winner(self) -> tuple[AttemptSpec, ...]:
        return self.active_attempts if self.winner is not None else ()

    @property
    def exhausted(self) -> bool:
        return (
            self.winner is None
            and not self.active_attempts
            and self._next_order > 8
            and self._replay_considered
        )


class HelperQueue:
    """A global single-helper lease, released only after process reaping.

    Requeue after each bounded turn with the current monotonic time. Existing
    requests retain their original position. Process cancellation alone does
    not release the lease; the runtime must confirm cleanup separately.
    """

    def __init__(self) -> None:
        self._waiting: dict[WindowKey, float] = {}
        self.active: WindowKey | None = None

    def request(self, key: WindowKey, *, eligible_at: float) -> bool:
        _nonnegative(eligible_at, "eligibility time")
        if self.active == key:
            raise ValueError("active helper must be reaped before requeue")
        fresh = key not in self._waiting
        self._waiting.setdefault(key, eligible_at)
        return fresh

    def cancel_waiting(self, key: WindowKey) -> None:
        self._waiting.pop(key, None)

    def acquire(self, *, memory_admitted: bool) -> WindowKey | None:
        if self.active is not None or not memory_admitted or not self._waiting:
            return None
        self.active = min(self._waiting, key=lambda key: (self._waiting[key], key))
        del self._waiting[self.active]
        return self.active

    def release_after_reap(self, key: WindowKey) -> None:
        if self.active != key:
            raise ValueError("helper lease does not belong to this window")
        self.active = None
