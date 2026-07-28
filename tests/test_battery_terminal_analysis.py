"""Tests for SoC-boundary decomposition and trajectory locality."""

import numpy as np
import pytest

from experiments.battery_terminal.analysis import (
    compare_soc_trajectories,
    decompose_soc,
)


def test_alternating_plateaus_produce_two_complete_excursions():
    decomposition = decompose_soc(
        [0.0, 2.0, 5.0, 5.0, 4.0, 0.0],
        initial_soc=0.0,
        capacity=5.0,
    )

    assert [
        (event.kind, event.first_state, event.last_state)
        for event in decomposition.boundary_events
    ] == [
        ("empty", 0, 1),
        ("full", 3, 4),
        ("empty", 6, 6),
    ]
    assert [
        (
            excursion.kind,
            excursion.start_state,
            excursion.end_state,
            excursion.duration_steps,
        )
        for excursion in decomposition.excursions
    ] == [
        ("charging", 1, 3, 2),
        ("discharging", 4, 6, 2),
    ]
    assert decomposition.final_boundary_state == 6
    assert decomposition.final_excursion_steps == 0
    assert decomposition.classified_steps == 4
    assert decomposition.unclassified_steps == 2


def test_latest_same_boundary_event_starts_next_excursion():
    decomposition = decompose_soc(
        [2.0, 0.0, 1.0, 5.0],
        initial_soc=0.0,
        capacity=5.0,
    )

    assert len(decomposition.excursions) == 1
    excursion = decomposition.excursions[0]
    assert excursion.kind == "charging"
    assert excursion.start_state == 2
    assert excursion.end_state == 4


def test_final_segment_begins_after_last_boundary_plateau():
    decomposition = decompose_soc(
        [0.0, 3.0, 5.0, 5.0, 4.0, 3.0],
        initial_soc=1.0,
        capacity=5.0,
    )

    assert decomposition.final_boundary_state == 4
    assert decomposition.final_excursion_steps == 2


def test_default_tolerance_detects_near_boundary_values():
    decomposition = decompose_soc(
        [0.05, 500.0, 999.95],
        initial_soc=0.0,
        capacity=1000.0,
    )

    assert decomposition.boundary_events[0].kind == "empty"
    assert decomposition.boundary_events[-1].kind == "full"


def test_trajectory_without_boundary_has_one_unclassified_final_segment():
    decomposition = decompose_soc(
        [4.0, 5.0, 6.0],
        initial_soc=3.0,
        capacity=10.0,
    )

    assert decomposition.boundary_events == ()
    assert decomposition.excursions == ()
    assert decomposition.final_boundary_state is None
    assert decomposition.final_excursion_steps == 3
    assert decomposition.unclassified_steps == 3


def test_excursion_step_slice_indexes_transition_actions():
    decomposition = decompose_soc(
        [0.0, 2.0, 5.0],
        initial_soc=0.0,
        capacity=5.0,
    )
    values = np.array([10.0, 20.0, 30.0])

    assert values[decomposition.excursions[0].step_slice].tolist() == [
        20.0,
        30.0,
    ]


def test_locality_reports_divergence_after_last_common_boundary():
    locality = compare_soc_trajectories(
        [0.0, 5.0, 10.0, 8.0, 6.0],
        [0.0, 5.0, 10.0, 9.0, 7.0],
        initial_soc=0.0,
        capacity=10.0,
    )

    assert locality.first_divergent_state == 4
    assert locality.last_common_boundary_state == 3
    assert not locality.divergence_precedes_last_common_boundary


def test_locality_flags_divergence_before_later_common_boundary():
    locality = compare_soc_trajectories(
        [1.0, 3.0, 5.0, 4.0],
        [2.0, 3.0, 5.0, 4.0],
        initial_soc=0.0,
        capacity=5.0,
    )

    assert locality.first_divergent_state == 1
    assert locality.last_common_boundary_state == 3
    assert locality.divergence_precedes_last_common_boundary


@pytest.mark.parametrize(
    "soc, initial_soc, capacity, match",
    [
        ([1.0, np.nan], 0.0, 5.0, "finite"),
        ([1.0], 0.0, 0.0, "positive"),
        ([6.0], 0.0, 5.0, "outside"),
    ],
)
def test_invalid_trajectory_is_rejected(soc, initial_soc, capacity, match):
    with pytest.raises(ValueError, match=match):
        decompose_soc(
            soc,
            initial_soc=initial_soc,
            capacity=capacity,
        )
