"""Input-only screen checks; synthetic data suffice in a clean checkout."""

import json

import numpy as np
import pandas as pd
import pytest

from experiments.case118_tracy_2021.spatial_screen import (
    HERE,
    coverage,
    cut_branches,
    pressures,
    regions,
)
from experiments.case118_annual_hierarchy.pglib_case import load_pglib_case118


def branch_rows(edges):
    result = np.zeros((len(edges), 13))
    for row, (a, b, rating, active) in zip(result, edges, strict=True):
        row[[0, 1, 5, 10]] = [a, b, rating, active]
    return result


def test_topology_only_partition_and_ties():
    # Line 1--2--3--4--5; seeds 1,5,3; ties go to smallest seed ID.
    branches = branch_rows([(n, n + 1, 10, 1) for n in range(1, 5)])
    expected = {1: [1, 2], 3: [3, 4], 5: [5]}
    assert regions([5, 3, 1, 4, 2], branches, 3) == expected
    assert regions(range(1, 6), branches[::-1], 3) == expected
    branches[2, 10] = 0
    with pytest.raises(ValueError, match="connected"):
        regions(range(1, 6), branches, 3)


def test_cut_keeps_parallel_rows_excludes_inactive_and_internal():
    branches = branch_rows([(1, 2, 5, 1), (2, 3, 10, 1), (3, 2, 20, 1), (1, 3, 100, 0)])
    cuts = cut_branches([1, 2], branches)
    assert [r["source_row"] for r in cuts] == [1, 2]
    assert sum(r["rateA"] for r in cuts) == 30
    branches[1, 5] = 0
    with pytest.raises(ValueError, match="unrated"):
        cut_branches([1, 2], branches)


def test_signs_capacities_and_clipping():
    p = pressures(np.array([12.0, 2.0, 1.0]), np.array([1.0, 12.0, 1.0]), 4, 3)
    np.testing.assert_array_equal(p["import_before_flex"], [11, 0, 0])
    np.testing.assert_array_equal(p["import_after_generation"], [7, 0, 0])
    np.testing.assert_array_equal(p["import_after_generation_and_discharge"], [4, 0, 0])
    np.testing.assert_array_equal(p["export_before_flex"], [0, 10, 0])
    np.testing.assert_array_equal(p["export_after_charging"], [0, 7, 0])


def test_coverage_week_containment_ties_and_zero():
    index = pd.date_range("2021-01-01", periods=8760, freq="h", tz="Etc/GMT+8")
    values = np.zeros(8760)
    values[:168] = 2
    values[168] = 10  # Exclusive stop: not in selected window.
    windows = [("first_week", "2021-01-01", "2021-01-08")]
    record = coverage(values, index, windows)
    assert record["annual_peak_mw"] == 10
    assert not record["earliest_peak_covered"]
    assert record["selected_to_annual_peak"] == 0.2
    assert record["selected_weekly_mean_mw"] == 2
    expected = max(values[start : start + 168].mean() for start in range(0, 8593, 24))
    assert record["annual_weekly_mean_mw"] == pytest.approx(expected)
    assert record["positive_tail_hours"] == 169
    assert record["positive_tail_hours_covered"] == 168
    zero = coverage(np.zeros(8760), index, windows)
    assert zero["annual_peak_time"] is None
    assert zero["selected_to_annual_peak"] is None
    assert zero["positive_tail_hours"] == 0


def test_retained_network_partition_cuts_and_capacity_conservation():
    """Independent connectedness/cut/capacity checks need no owner CSV."""
    case = load_pglib_case118()
    retained = json.loads((HERE / "stage_b_selection/spatial_screen.json").read_text())
    parts = regions(np.asarray(case["bus"])[:, 0], case["branch"])
    assert sorted(b for members in parts.values() for b in members) == list(
        range(1, 119)
    )
    assert parts == {
        int(r["region"].split("_")[1]): r["buses"] for r in retained["regions"]
    }
    for region in retained["regions"]:
        members = set(region["buses"])
        reached = {min(members)}
        while True:
            previous = len(reached)
            for row in case["branch"]:
                a, b = int(row[0]), int(row[1])
                if row[10] > 0 and {a, b} <= members and ({a, b} & reached):
                    reached.update([a, b])
            if len(reached) == previous:
                break
        assert reached == members
        expected = [
            i
            for i, row in enumerate(case["branch"])
            if row[10] > 0 and sum(int(b) in members for b in row[:2]) == 1
        ]
        assert expected == [r["source_row"] for r in region["cut_branches"]]
        assert (
            sum(case["branch"][i, 5] for i in expected) == region["cut_rateA_sum_mva"]
        )
    assert sum(r["generator_pmax_mw"] for r in retained["regions"]) == pytest.approx(
        5000
    )
    assert sum(r["battery_capacity_mwh"] for r in retained["regions"]) == pytest.approx(
        14046.55784398981
    )


def test_owner_arrays_independently_reconstruct_retained_coverage():
    path = HERE / "results/stage_a_active_inputs/active_inputs.npz"
    if not path.exists():
        pytest.skip("owner-provided Stage A arrays unavailable")
    retained = json.loads((HERE / "stage_b_selection/spatial_screen.json").read_text())
    buses = pd.read_csv(HERE / "stage_a/buses.csv")
    nd = pd.read_csv(HERE / "stage_a/renewables.csv")
    load_buses = buses.loc[buses.load_share > 0, "bus"].to_numpy()
    with np.load(path) as arrays:
        for region in retained["regions"]:
            members = region["buses"]
            load = arrays["load_p_mw"][:, np.isin(load_buses, members)].sum(axis=1)
            renewable = arrays["nd_available_mw"][:, np.isin(nd.bus, members)].sum(
                axis=1
            )
            # Independently reconstruct the main import score, all 8,760 hours.
            score = np.clip(load - renewable - region["generator_pmax_mw"], 0, None)
            actual = region["coverage"]["import_after_generation"]
            assert actual["annual_peak_mw"] == pytest.approx(score.max())
            weekly = [score[i : i + 168].sum() / 168 for i in range(0, 8593, 24)]
            assert actual["annual_weekly_mean_mw"] == pytest.approx(max(weekly))
