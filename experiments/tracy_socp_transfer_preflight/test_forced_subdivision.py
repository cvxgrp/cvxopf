"""Forced-trigger coverage, unchanged arithmetic, and lossless evidence tests."""

import copy
import json
from pathlib import Path

import numpy as np
import pytest

from experiments.tracy_socp_transfer_preflight import forced_subdivision as f
from experiments.tracy_socp_transfer_preflight import historical_replay as h
from experiments.tracy_socp_transfer_preflight.test_automatic import triangle
from experiments.tracy_socp_transfer_preflight.test_multigraph import witness_kwargs


def ring(size):
    kwargs, pins = triangle(0)
    ids = [101, 305, 902, 110, 111, 112][:size]
    buses = np.repeat(kwargs["case"]["bus"][:1], size, axis=0)
    buses[:, 0] = ids
    branches = np.repeat(kwargs["case"]["branch"][:1], size, axis=0)
    branches[:, :2] = list(zip(ids, ids[1:] + ids[:1]))
    kwargs["case"].update(bus=buses, branch=branches)
    return kwargs, pins


@pytest.mark.parametrize("size", [5, 6])
def test_no_export_is_forced_with_exact_parent_search(size):
    kwargs, pins = ring(size)
    old = f.m.scanner.LoopScanner(kwargs, max_length=6)
    scanner = f.LoopScanner(kwargs)
    with f.p.no_optimization():
        prior = old.scan_hour(pins)
        actual = scanner.scan_hour(pins)
        values = f.m.scanner.v1.injections(kwargs, pins, 0)
        losses = {
            i: f.m.scanner.I(
                (
                    old.base
                    * e["r"]
                    * f.m.scanner.v1.square(old.current_cap(i, values))
                ).hi
            )
            for i, e in old.geometry.items()
        }
        expected = old.prove(
            old.cycles[0], old.export_bounds(old.cycles[0], values, losses)
        )
    assert prior["loops"][0]["status"] == "unresolved_no_export_trigger"
    assert actual["loops"][0]["export_trigger_bypassed"]
    assert expected["status"] == "unresolved_budget" and expected["tested_boxes"] == 64
    assert all(actual["loops"][0][k] == v for k, v in expected.items())
    visits = scanner.actual_box_visits
    assert scanner.scan_hour(pins) == actual
    assert scanner.actual_box_visits == visits and scanner.reused_searches > 0
    scanner.clear_caches()


@pytest.mark.parametrize("size", [5, 6])
def test_accepted_parallel_transformer_witness_is_not_excluded(size):
    kwargs, _ = ring(size)
    kwargs["case"]["branch"][0, 8:10] = [1.01, 1]
    kwargs["case"]["branch"] = np.vstack(
        [kwargs["case"]["branch"], kwargs["case"]["branch"][0]]
    )
    voltage = np.exp(1j * np.linspace(0.025, 0, size))
    kwargs = witness_kwargs(kwargs, voltage)
    with f.p.no_optimization():
        scanner = f.LoopScanner(kwargs)
        report = scanner.scan_schedule(np.empty((1, 0)))
    assert report["discovered_cycles"] == 1 and not report["unsupported_cycles"]
    assert report["verdict"] == "unresolved"
    assert report["hours"][0]["loops"][0]["tested_boxes"] == 64
    scanner.clear_caches()


def test_short_cycles_are_not_forced_and_bad_geometries_stay_unsupported():
    kwargs, pins = triangle(0)
    assert f.LoopScanner(kwargs).cycles == []
    kwargs, pins = ring(5)
    kwargs["case"]["branch"][0, 8] = -1
    report = f.LoopScanner(kwargs).scan_hour(pins)
    assert report["loop_counts"] == {"unsupported": 1}


def test_budget_grouping_is_lossless():
    loops = [
        dict(
            cycle=[1, 2, 3, 4, 5],
            cycle_index=i,
            status="unresolved_budget",
            tested_boxes=64,
            unresolved_boxes=65,
            excluded_leaves=0,
            nonclosure_leaves=0,
        )
        for i in range(3)
    ]
    loops[1]["unresolved_boxes"] = 61
    report = dict(
        transfers=[dict(hours=[dict(loops=copy.deepcopy(loops))])],
        ac_controls=[],
        encoding="test",
    )
    f.compact_budgets(report)
    restored = [
        dict(
            **{k: v for k, v in g.items() if k != "cycle_indexes"},
            cycle_index=i,
            cycle=[1, 2, 3, 4, 5],
        )
        for g in report["transfers"][0]["hours"][0]["budget_groups"]
        for i in g["cycle_indexes"]
    ]
    assert sorted(restored, key=lambda r: r["cycle_index"]) == loops


def test_selected_forced_evidence():
    path = f.p.HERE / "FORCED_SUBDIVISION_EVIDENCE.json"
    if not path.exists():
        pytest.skip("forced checkpoint not yet saved")
    report = json.loads(path.read_text())
    assert report["policy"]["new_optimizer_calls"] == 0
    assert report["policy"]["forced_cycle_lengths"] == [5, 6]
    assert report["existing_results_unchanged"] and report["historical_bytes_unchanged"]
    assert report["sources"]["driver_sha256"] == h.digest(Path(f.__file__))
    for source, checksum in report["existing_files_verified"].items():
        assert h.digest(h.ROOT / source) == checksum
    catalog = report["topology"]["cycle_catalog"]
    assert len(catalog) == 57 and {len(c) for c in catalog} == {5, 6}
    for cohort, count in [("transfers", 18), ("ac_controls", 6)]:
        assert len(report[cohort]) == count
        for row in report[cohort]:
            assert len(row["hours"]) == 24
            logical = 0
            for hour in row["hours"]:
                codes = f.long.decode_status(hour["loop_status_runs"])
                assert len(codes) == 57 and "N" not in codes
                seen = set()
                for group in hour["budget_groups"]:
                    assert group["tested_boxes"] == 64
                    for i in group["cycle_indexes"]:
                        assert i not in seen and codes[i] == "B"
                        seen.add(i)
                        logical += group["tested_boxes"]
                for loop in hour["loops"]:
                    i = loop["cycle_index"]
                    assert i not in seen and codes[i] == "C"
                    assert loop["unresolved_boxes"] == 0 and loop["tested_boxes"] <= 64
                    seen.add(i)
                    logical += loop["tested_boxes"]
                assert len(seen) == 57
            assert logical == row["computation"]["logical_box_visits"]
            if cohort == "ac_controls" or row["historical_outcome"]["compatible"]:
                assert row["verdict"] == "unresolved"
