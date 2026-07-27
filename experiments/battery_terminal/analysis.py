"""SoC-boundary geometry for the battery terminal-policy experiment."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np


BoundaryKind = Literal["empty", "full"]
ExcursionKind = Literal["charging", "discharging"]


@dataclass(frozen=True)
class BoundaryEvent:
    """One consecutive plateau at an SoC boundary."""

    kind: BoundaryKind
    first_state: int
    last_state: int


@dataclass(frozen=True)
class Excursion:
    """A completed alternating boundary-to-boundary SoC excursion."""

    kind: ExcursionKind
    start_state: int
    end_state: int

    @property
    def step_slice(self) -> slice:
        """Dispatch steps whose transitions comprise this excursion."""
        return slice(self.start_state, self.end_state)

    @property
    def duration_steps(self) -> int:
        return self.end_state - self.start_state


@dataclass(frozen=True)
class SoCDecomposition:
    """Boundary events, complete excursions, and terminal-segment geometry."""

    states: np.ndarray
    boundary_events: tuple[BoundaryEvent, ...]
    excursions: tuple[Excursion, ...]
    final_boundary_state: int | None
    final_excursion_steps: int
    classified_steps: int
    unclassified_steps: int


@dataclass(frozen=True)
class TrajectoryLocality:
    """Comparison of two SoC trajectories relative to common saturation."""

    first_divergent_state: int | None
    last_common_boundary_state: int | None
    divergence_precedes_last_common_boundary: bool


def _state_sequence(soc, initial_soc: float) -> np.ndarray:
    post_step = np.asarray(soc, dtype=float).reshape(-1)
    states = np.concatenate(([float(initial_soc)], post_step))
    if not np.isfinite(states).all():
        raise ValueError("SoC trajectory must be finite")
    return states


def _boundary_labels(
    states: np.ndarray,
    capacity: float,
    tolerance: float,
) -> np.ndarray:
    if not np.isfinite(capacity) or capacity <= 0:
        raise ValueError("capacity must be finite and positive")
    if not np.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and nonnegative")
    if np.any(states < -tolerance) or np.any(states > capacity + tolerance):
        raise ValueError("SoC trajectory lies outside the storage bounds")

    labels = np.zeros(len(states), dtype=np.int8)
    labels[np.abs(states) <= tolerance] = -1
    labels[np.abs(states - capacity) <= tolerance] = 1
    return labels


def _boundary_events(labels: np.ndarray) -> tuple[BoundaryEvent, ...]:
    events = []
    state = 0
    while state < len(labels):
        label = labels[state]
        if label == 0:
            state += 1
            continue
        end = state
        while end + 1 < len(labels) and labels[end + 1] == label:
            end += 1
        events.append(
            BoundaryEvent(
                kind="empty" if label == -1 else "full",
                first_state=state,
                last_state=end,
            )
        )
        state = end + 1
    return tuple(events)


def decompose_soc(
    soc,
    *,
    initial_soc: float,
    capacity: float,
    tolerance: float | None = None,
) -> SoCDecomposition:
    """Extract completed empty/full excursions from post-step SoC values."""
    if tolerance is None:
        tolerance = 1e-4 * capacity
    states = _state_sequence(soc, initial_soc)
    labels = _boundary_labels(states, capacity, tolerance)
    events = _boundary_events(labels)

    excursions = []
    previous = None
    for event in events:
        if previous is None or event.kind == previous.kind:
            previous = event
            continue
        excursions.append(
            Excursion(
                kind=(
                    "charging"
                    if previous.kind == "empty" and event.kind == "full"
                    else "discharging"
                ),
                start_state=previous.last_state,
                end_state=event.first_state,
            )
        )
        previous = event

    total_steps = len(states) - 1
    classified_steps = sum(
        excursion.duration_steps for excursion in excursions
    )
    final_boundary_state = events[-1].last_state if events else None
    final_excursion_steps = (
        total_steps
        if final_boundary_state is None
        else total_steps - final_boundary_state
    )
    return SoCDecomposition(
        states=states,
        boundary_events=events,
        excursions=tuple(excursions),
        final_boundary_state=final_boundary_state,
        final_excursion_steps=final_excursion_steps,
        classified_steps=classified_steps,
        unclassified_steps=total_steps - classified_steps,
    )


def compare_soc_trajectories(
    reference_soc,
    candidate_soc,
    *,
    initial_soc: float,
    capacity: float,
    tolerance: float | None = None,
) -> TrajectoryLocality:
    """Locate divergence and the last shared empty/full boundary state."""
    if tolerance is None:
        tolerance = 1e-4 * capacity
    reference = _state_sequence(reference_soc, initial_soc)
    candidate = _state_sequence(candidate_soc, initial_soc)
    if reference.shape != candidate.shape:
        raise ValueError("SoC trajectories must have the same length")

    different = np.flatnonzero(np.abs(reference - candidate) > tolerance)
    first_divergent = int(different[0]) if len(different) else None

    reference_labels = _boundary_labels(reference, capacity, tolerance)
    candidate_labels = _boundary_labels(candidate, capacity, tolerance)
    common = np.flatnonzero(
        (reference_labels != 0) & (reference_labels == candidate_labels)
    )
    last_common = int(common[-1]) if len(common) else None
    return TrajectoryLocality(
        first_divergent_state=first_divergent,
        last_common_boundary_state=last_common,
        divergence_precedes_last_common_boundary=(
            first_divergent is not None
            and last_common is not None
            and first_divergent < last_common
        ),
    )
