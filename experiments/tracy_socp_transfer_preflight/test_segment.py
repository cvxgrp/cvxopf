"""No-solve scope, offset, preservation and selected-proof regressions."""

import copy
import json

import cvxpy as cp
import numpy as np
import pandas as pd
import pytest

from cvxopf import Load
from experiments.tracy_socp_transfer_preflight import segment as s
from experiments.tracy_socp_transfer_preflight.test_forced_subdivision import ring


def two_rings():
    kwargs, _ = ring(5)
    case = kwargs["case"]
    ids = list(range(201, 207))
    buses = np.repeat(case["bus"][:1], 6, axis=0)
    buses[:, 0] = ids
    branches = np.repeat(case["branch"][:1], 7, axis=0)
    branches[:, :2] = list(zip(ids, ids[1:] + ids[:1])) + [(111, 201)]
    case["bus"] = np.vstack([case["bus"], buses])
    case["branch"] = np.vstack([case["branch"], branches])
    return kwargs


def select(kwargs, **kwargs_select):
    return s.select_segment(kwargs, first_hour=2452, hours=[2452], **kwargs_select)


def test_neighborhood_union_hops_and_long_cycle_filter():
    kwargs = two_rings()
    selected = select(kwargs, buses=[101])
    assert selected["candidate_cycles"] == 2
    assert selected["selected_cycles"] == [[101, 111, 110, 902, 305]]
    assert selected["neighborhood_buses"] == [101]
    assert selected["dispatch_box_visit_cap"] == 64
    assert len(select(kwargs, buses=[101], hops=2)["selected_cycles"]) == 2
    assert len(select(kwargs, buses=[101], hops=1)["selected_cycles"]) == 1
    assert len(select(kwargs, buses=[101, 201])["selected_cycles"]) == 2
    # A selected original bridge row seeds both endpoints; no induced subgraph.
    branch = select(kwargs, branch_rows=[11])
    assert branch["seed_buses"] == [111, 201]
    assert len(branch["selected_cycles"]) == 2
    # A chord creates shorter cycles which must not enter escalation.
    chord = kwargs["case"]["branch"][0].copy()
    chord[:2] = [101, 902]
    kwargs["case"]["branch"] = np.vstack([kwargs["case"]["branch"], chord])
    assert all(len(c) in (5, 6) for c in select(kwargs, buses=[101])["selected_cycles"])


def test_original_row_indexing_with_inactive_parallel_reversed_transformer():
    kwargs = two_rings()
    inactive = kwargs["case"]["branch"][0].copy()
    inactive[10] = 0
    parallel = kwargs["case"]["branch"][0].copy()
    parallel[:2] = parallel[1::-1]
    parallel[8:10] = [0.99, 2]
    kwargs["case"]["branch"] = np.vstack([inactive, kwargs["case"]["branch"], parallel])
    scope = select(kwargs, branch_rows=[1, 13])
    assert scope["focus_branch_rows"] == [1, 13]
    assert scope["seed_buses"] == [101, 305]
    assert len(scope["selected_cycles"]) == 1
    with pytest.raises(ValueError, match="inactive"):
        select(kwargs, branch_rows=[0])


@pytest.mark.parametrize(
    "selection,message",
    [
        ({}, "focus"),
        ({"buses": [9999]}, "unknown"),
        ({"branch_rows": [-1]}, "outside"),
        ({"branch_rows": [999]}, "outside"),
        ({"buses": [101], "hops": -1}, "nonnegative"),
        ({"buses": [True]}, "integers"),
        ({"buses": [101.1]}, "integers"),
        ({"buses": [101], "hops": True}, "integers"),
    ],
)
def test_bad_spatial_selectors_fail_without_widening(selection, message):
    with pytest.raises(ValueError, match=message):
        select(two_rings(), **selection)


@pytest.mark.parametrize("hours", [[], [2451], [2455], [True], [2452.5]])
def test_invalid_hours_fail_instead_of_clipping(hours):
    kwargs = two_rings()
    kwargs["T"] = 3
    with pytest.raises(ValueError):
        s.select_segment(kwargs, first_hour=2452, hours=hours, buses=[101])


def test_preview_and_empty_selection_never_construct_proof_engine(monkeypatch):
    kwargs = two_rings()
    leaf = np.repeat(kwargs["case"]["bus"][:1], 1, axis=0)
    leaf[0, 0] = 999
    kwargs["case"]["bus"] = np.vstack([kwargs["case"]["bus"], leaf])
    branch = kwargs["case"]["branch"][0].copy()
    branch[:2] = [999, 101]
    kwargs["case"]["branch"] = np.vstack([kwargs["case"]["branch"], branch])

    def forbidden(*args, **kwargs):
        raise AssertionError("proof engine constructed for preview/empty scope")

    monkeypatch.setattr(s.f, "LoopScanner", forbidden)
    common = dict(first_hour=2452, hours=[2452], reason="synthetic selector test")
    preview = s.check_segment(kwargs, [[0]], buses=[101], **common)
    assert preview["disposition"] == "not_run_preview"
    assert preview["summary"]["checked_loop_hours"] == 0 and preview["hours"] == []
    empty = s.check_segment(kwargs, [[0]], buses=[999], run=True, **common)
    assert (
        empty["disposition"] == "no_loops_selected" and empty["verdict"] == "unresolved"
    )
    assert empty["selection"]["selected_loop_hours"] == 0
    assert s.select_segment(
        kwargs,
        buses=[999],
        hops=1,
        **{k: v for k, v in common.items() if k != "reason"},
    )["selected_cycles"]


def test_only_selected_offsets_pins_and_loops_are_checked_on_full_inputs(monkeypatch):
    kwargs = two_rings()
    kwargs["T"] = 3
    kwargs["loads"] = [Load(bus=101, p_load_mw=1, q_load_mvar=0, device_id="demand")]
    kwargs["df_load_p"] = pd.DataFrame({"demand": [4, 8, 12]}, index=[100, 300, 900])
    pins = np.array([[0.0], [1.0], [2.0]])
    original = copy.deepcopy(kwargs)
    visits = []

    def fake_scan(engine, selected_pins, offset):
        assert engine.kwargs is kwargs and kwargs["T"] == 3
        assert len(engine.rows) == 12 and len(engine.cycles) == 1
        assert all(len(c) == 5 for c in engine.cycles)
        assert np.array_equal(selected_pins, pins[offset])
        expected = selected_pins[0] - kwargs["df_load_p"]["demand"].iloc[offset]
        assert (
            float(s.f.m.scanner.v1.injections(kwargs, selected_pins, offset)[101][0].lo)
            == expected
        )
        with pytest.raises(RuntimeError, match="must not invoke an optimizer"):
            cp.Problem(cp.Minimize(0)).solve()
        visits.append(offset)
        engine.actual_box_visits += 64
        return dict(
            status="unresolved",
            loop_counts={"unresolved_budget": 1},
            loops=[
                dict(
                    cycle=list(engine.cycles[0]),
                    status="unresolved_budget",
                    tested_boxes=64,
                )
            ],
        )

    monkeypatch.setattr(s.f.LoopScanner, "scan_hour", fake_scan)
    report = s.check_segment(
        kwargs,
        pins,
        first_hour=2452,
        hours=[2454, 2453, 2453],
        buses=[101],
        reason="synthetic noncontiguous-hour test",
        run=True,
    )
    assert visits == [1, 2]
    assert [h["hour"] for h in report["hours"]] == [2453, 2454]
    assert report["selection"]["omitted_hours"] == 1
    assert report["summary"]["checked_loop_hours"] == 2
    assert report["computation"]["logical_box_visits"] == 128
    assert np.array_equal(kwargs["case"]["branch"], original["case"]["branch"])
    pd.testing.assert_frame_equal(kwargs["df_load_p"], original["df_load_p"])
    assert kwargs["storage"] == original["storage"]
    assert pins.tolist() == [[0], [1], [2]]


def test_selected_synthetic_proof_matches_frozen_full_network_engine():
    kwargs = two_rings()
    with s.p.no_optimization():
        original = s.f.LoopScanner(kwargs)
        raw = original.scan_hour([0])
        expected = [loop for loop in raw["loops"] if 101 in loop["cycle"]]
        original.clear_caches()
    report = s.check_segment(
        kwargs,
        [[0]],
        first_hour=2452,
        hours=[2452],
        buses=[101],
        reason="synthetic exact proof regression",
        run=True,
    )
    assert report["hours"][0]["loops"] == expected
    assert len(expected) == 1 and expected[0]["tested_boxes"] == 64
    assert report["selection"]["omitted_cycles"] == 1


def test_certificate_and_unsupported_dispositions_are_preserved():
    kwargs, pins = ring(5)
    kwargs["case"]["branch"][:, 5] = 20
    kwargs["case"]["branch"][:, 9] = 30
    common = dict(
        first_hour=2452,
        hours=[2452],
        buses=[101],
        reason="synthetic phase contradiction",
        run=True,
    )
    certified = s.check_segment(kwargs, [pins], **common)
    assert certified["verdict"] == "certified_incompatible"
    proof = certified["hours"][0]["loops"][0]
    assert proof["unresolved_boxes"] == 0 and proof["certificate_leaves"]
    kwargs["case"]["branch"][0, 8] = -1
    unsupported = s.check_segment(kwargs, [pins], **common)
    assert unsupported["verdict"] == "unresolved"
    assert unsupported["summary"]["loop_statuses"] == {"unsupported": 1}


@pytest.mark.parametrize("pins", [[[0], [0]], [[float("nan")]], [[]]])
def test_schedule_must_remain_finite_and_full_horizon(pins):
    kwargs, _ = ring(5)
    with pytest.raises(ValueError, match="full-horizon"):
        s.check_segment(
            kwargs, pins, first_hour=2452, hours=[2452], buses=[101], reason="test"
        )


def test_cli_defaults_to_preview_and_requires_explicit_focus_and_reason(
    monkeypatch, capsys
):
    captured = []
    monkeypatch.setattr(
        s,
        "retained_segment",
        lambda number, **opts: captured.append((number, opts)) or dict(mode="preview"),
    )
    args = [
        "--job",
        "13",
        "--hours",
        "2452",
        "--buses",
        "71",
        "--hops",
        "1",
        "--reason",
        "selector fixture",
    ]
    s.main(args)
    assert json.loads(capsys.readouterr().out)["mode"] == "preview"
    assert captured[-1][1]["run"] is False
    s.main(args + ["--run", "--schedule", "ac-control"])
    assert (
        captured[-1][1]["run"] is True
        and captured[-1][1]["schedule_kind"] == "ac-control"
    )
    for bad in (
        ["--job", "13", "--hours", "2452", "--reason", "test"],
        ["--job", "13", "--hours", "2452", "--buses", "71"],
    ):
        with pytest.raises(SystemExit):
            s.main(bad)


def test_iterables_and_duplicate_seeds_are_deterministic():
    kwargs = two_rings()
    expected = select(kwargs, buses=[101])
    actual = s.select_segment(
        kwargs, first_hour=2452, hours=iter([2452, 2452]), buses=iter([101, 101])
    )
    assert actual == expected


@pytest.fixture
def mocked_replay(monkeypatch):
    kwargs = two_rings()
    pins = np.array([[0.0]])
    outcome = dict(label="native_rejection", compatible=False)
    data = {"cases": {"fixture": {"ac": {"result": {"b": [[1.0]]}}}}}
    row = dict(start=2452, mathematical_input_sha256="fixture")
    jobs = [dict(id=1, donor="socp", scenario="fixture")]
    calls = []

    def replay(binding, data, prepared, number):
        calls.append(number)
        return (None, kwargs, pins, None, row, None), outcome

    monkeypatch.setattr(s.f.long, "existing_inventory", lambda: {})
    monkeypatch.setattr(s.p.transfer, "source_inventory", lambda: data)
    monkeypatch.setattr(s.p.transfer, "jobs", lambda _: jobs)
    monkeypatch.setattr(
        s.p.transfer, "source_reference", lambda *a: dict(path="fixture")
    )
    monkeypatch.setattr(s.p.e3_prepared, "verified_inputs", lambda: None)
    monkeypatch.setattr(s.p, "historical_binding", lambda _: (None, {}))
    monkeypatch.setattr(s.p.starts, "origin_hashes", lambda _: {})
    monkeypatch.setattr(s.p, "replay_transfer", replay)
    return calls, outcome


def test_cli_wrapper_replays_one_job_and_keeps_control_separate(mocked_replay):
    calls, outcome = mocked_replay
    opts = dict(hours=[2452], buses=[101], reason="synthetic retained-wrapper fixture")
    donor = s.retained_segment(1, **opts)
    control = s.retained_segment(1, schedule_kind="ac-control", **opts)
    assert calls == [1, 1]
    assert (
        donor["retained"]["historical_donor_outcome"]
        == control["retained"]["historical_donor_outcome"]
        == outcome
    )
    assert donor["source_schedule_sha256"] != control["source_schedule_sha256"]
    assert (
        donor["summary"]["checked_loop_hours"]
        == control["summary"]["checked_loop_hours"]
        == 0
    )
    assert control["retained"]["ac_control_source"] == dict(path="fixture")


@pytest.mark.parametrize("kind,compatible", [("donor", True), ("ac-control", False)])
def test_accepted_witness_conflict_is_fatal(
    mocked_replay, monkeypatch, kind, compatible
):
    _, outcome = mocked_replay
    outcome["compatible"] = compatible
    monkeypatch.setattr(
        s, "check_segment", lambda *a, **k: dict(verdict="certified_incompatible")
    )
    with pytest.raises(ValueError, match="contradicts retained accepted witness"):
        s.retained_segment(1, schedule_kind=kind)


def test_artifact_mismatch_is_fatal(mocked_replay, monkeypatch):
    monkeypatch.setattr(s.p.starts, "origin_hashes", lambda _: dict(changed="artifact"))
    with pytest.raises(ValueError, match="historical artifacts changed"):
        s.retained_segment(1, hours=[2452], buses=[101], reason="fixture")


def test_existing_file_mismatch_is_fatal(mocked_replay, monkeypatch):
    monkeypatch.setattr(s.f.long, "existing_inventory", lambda: {"fixture": "before"})
    monkeypatch.setattr(s.p.transfer, "sha", lambda _: "after")
    with pytest.raises(ValueError, match="existing source or study artifact changed"):
        s.retained_segment(1, hours=[2452], buses=[101], reason="fixture")
