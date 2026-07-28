"""Tests for the DC subset-study specification."""

from experiments.battery_terminal.subset_study import (
    CROSSING_BOUNDARY_STATE,
    SUBSET_CASES,
)


def test_subset_cases_are_equal_length_and_differ_by_internal_boundary():
    crossing = SUBSET_CASES["crosses_boundary"]
    no_boundary = SUBSET_CASES["no_boundary"]

    assert crossing[1] - crossing[0] == no_boundary[1] - no_boundary[0]
    assert crossing[0] < CROSSING_BOUNDARY_STATE < crossing[1]
    assert not (
        no_boundary[0] < CROSSING_BOUNDARY_STATE < no_boundary[1]
    )
