"""Tests for the DC subset-study specification."""

from types import SimpleNamespace

import numpy as np

from experiments.battery_terminal.subset_study import (
    CROSSING_BOUNDARY_STATE,
    SUBSET_CASES,
    _trajectory_table,
)


def test_subset_cases_are_equal_length_and_differ_by_internal_boundary():
    crossing = SUBSET_CASES["crosses_boundary"]
    no_boundary = SUBSET_CASES["no_boundary"]

    assert crossing[1] - crossing[0] == no_boundary[1] - no_boundary[0]
    assert crossing[0] < CROSSING_BOUNDARY_STATE < crossing[1]
    assert not (
        no_boundary[0] < CROSSING_BOUNDARY_STATE < no_boundary[1]
    )


def test_trajectory_table_retains_each_battery_and_global_index():
    run = SimpleNamespace(
        start_state=10,
        results={
            "soc": np.array([[11.0, 21.0], [12.0, 22.0]]),
            "b": np.array([[1.0, -1.0], [2.0, -2.0]]),
        },
        build=SimpleNamespace(
            data={
                "storage_initial_soc": np.array([10.0, 20.0]),
                "storage_bus": np.array([0, 6]),
            }
        ),
    )

    table = _trajectory_table({("case", "ac"): run})

    assert len(table) == 4
    np.testing.assert_array_equal(table["battery_bus"].unique(), [1, 7])
    np.testing.assert_array_equal(table["global_step"].unique(), [10, 11])
    np.testing.assert_array_equal(
        table["global_post_step_state"].unique(),
        [11, 12],
    )
