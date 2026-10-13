"""Portable construction and control-first decisions, with solves forbidden."""

from dataclasses import replace

import cvxpy as cp
import numpy as np
import pytest

from experiments.numerical_preparation import fixture
from experiments.case118_tracy_2021 import e3_prepared
from experiments.tracy_battery_transfer_slice import model as m, run as r


@pytest.fixture(autouse=True)
def no_optimizer(monkeypatch):
    monkeypatch.setattr(
        cp.Problem, "solve", lambda *_a, **_k: pytest.fail("test must not optimize")
    )


def inputs():
    wide = fixture.case9_kwargs(3, "ac")
    wide["options"] = replace(
        wide["options"],
        numerical_preparation=e3_prepared.policy("ac", "component_first"),
    )
    return wide, np.array([[3.0], [-2.0], [1.0]])


def test_hourly_inputs_and_both_energy_boundaries_are_schedule_consistent():
    wide, powers = inputs()
    kw, E, pre = m.slice_inputs(wide, powers, 1, [18.0])
    assert kw["T"] == 1 and pre["passed"]
    np.testing.assert_array_equal(E, [[17.0], [19.0]])
    assert kw["storage"][0].initial_soc == 17
    assert kw["storage"][0].terminal_soc is None
    assert kw["storage"][0].terminal_constraint is None
    assert (
        kw["storage"][0].apparent_power_rating
        == wide["storage"][0].apparent_power_rating
    )
    for key in m.FRAMES:
        if wide.get(key) is not None:
            assert kw[key].equals(wide[key].iloc[1:2])
    assert kw["options"] is wide["options"] and kw["case"] is wide["case"]
    assert wide["storage"][0].initial_soc == 20 and wide["T"] == 3


def test_start_slice_keeps_native_units_and_only_replaces_battery_boundaries():
    full = dict(
        Pg=np.array([[0.3, 0.4, 0.5]]),
        b=np.ones((1, 3)),
        soc=np.array([[100.0, 99.0, 98.0, 97.0]]),
        theta=np.zeros((2, 3)),
    )
    part = m.slice_start(full, 1, np.array([[-2.0]]), np.array([[97.0], [99.0]]))
    np.testing.assert_array_equal(part["Pg"], [[0.4]])
    np.testing.assert_array_equal(part["b"], [[-2.0]])
    np.testing.assert_array_equal(part["soc"], [[97.0, 99.0]])
    np.testing.assert_array_equal(full["b"], np.ones((1, 3)))


def test_t1_graph_preserves_economic_preparation_and_exact_pin():
    wide, powers = inputs()
    kw, E, _ = m.slice_inputs(wide, powers, 1, [18.0])
    build = m.build_opf_multistep(**kw)
    original = build.prob.objective
    build = replace(
        build,
        prob=cp.Problem(
            original, [*build.prob.constraints, build.variables["b"] == powers[1:2].T]
        ),
    )
    start = r.q.physical_start(build, kw)
    start["b"] = powers[1:2].T
    start["soc"] = E.T
    from cvxopf._hierarchical_solver import _assign_start

    _assign_start(build, start)
    captured = e3_prepared.prepared_start(build, 1)
    assert build.prob.objective is original and build.prob.status is None
    assert build.numerical_preparation.cost_coordinates
    assert build.numerical_preparation.objective_assembly == "component_first"
    assert np.max(build.prob.constraints[-1].violation()) == 0
    assert len(captured["reduced_x0"]) > 0 and captured["cost_maps"]
    # The prospective witness does not counterfeit an optimizer status.
    m.start_audit(
        (build, kw, powers[1:2], None, start, dict(original_terminal_soc_mwh=E[1]))
    )
    assert build.prob.status is None


def test_unexpected_temporal_coupling_is_not_silently_dropped():
    wide, p = inputs()
    wide["coupling_constraints"] = [object()]
    with pytest.raises(ValueError, match="coupling"):
        m.slice_inputs(wide, p, 1, [18.0])


@pytest.mark.parametrize(
    "classification",
    [
        "unfinished",
        "native_rejection",
        "wall_limit",
        "audit_failure",
        "infrastructure_failure",
        "rss_limit",
        "interrupted",
    ],
)
def test_control_must_pass_before_donors(classification):
    assert r.next_call([dict(classification=classification)]) is None


def test_exactly_four_cases_and_no_donor_retries():
    assert r.next_call([]) == 1
    assert r.next_call([dict(classification="accepted")]) == 2
    attempts = [
        dict(classification="accepted"),
        dict(classification="native_rejection"),
        dict(classification="wall_limit"),
    ]
    assert r.next_call(attempts) == 4
    assert r.next_call(attempts + [dict(classification="accepted")]) is None
    assert r.next_call(attempts + [dict(classification="audit_failure")]) is None


def test_native_success_still_requires_audit():
    assert (
        r.classify(
            dict(exception=None, native=dict(status=0), checks=dict(passed=False))
        )
        == "audit_failure"
    )
    assert (
        r.classify(
            dict(exception=None, native=dict(status=2), checks=dict(passed=True))
        )
        == "native_rejection"
    )


def test_slice_rejects_wrong_endpoint_and_offset():
    wide, p = inputs()
    with pytest.raises(ValueError):
        m.slice_inputs(wide, p, 3, [18.0])
    with pytest.raises(ValueError):
        m.slice_inputs(wide, p, 1, [19.0])


def completed_fixture(
    tmp_path, monkeypatch, *, supervised=True, resources=True, elapsed=1.0
):
    root = tmp_path / "run"
    root.mkdir()
    directory = root / "call-001"
    directory.mkdir()
    binding = dict(rows=[{}] * 4)
    r.q.atomic_immutable_json(root / "binding.json", binding)
    r.q.atomic_immutable_json(root / "protocol.json", r.protocol())
    req = r.make_request(root, 1, 300.0)
    capture = dict(iteration=1)
    checks = dict(passed=True)
    summary = dict(test=True)
    view = (None, None, None, {}, {}, {})
    native = dict(status=0)
    ev = dict(native=native)
    record = dict(
        iteration=1,
        request=req,
        row={},
        exception=None,
        optimizer_calls=1,
        classification="accepted",
        native=native,
        preparation_evidence=ev,
        result={},
        checks=checks,
        summary=summary,
        binding_sha256=r.q.digest(root / "binding.json"),
    )
    values = {
        "request.json": req,
        "monitoring.json": {},
        "raw-start.json": dict(raw_start={}),
        "start.json": dict(assigned_start={}),
        "x0.json.gz": capture,
        "native.json.gz": dict(iteration=1, data=native),
        "preparation.json.gz": dict(iteration=1, data=ev),
        "result.json.gz": record,
    }
    for name, value in values.items():
        (r.q.atomic_gzip_json if name.endswith(".gz") else r.q.atomic_immutable_json)(
            directory / name, value
        )
    r.q.atomic_immutable_json(
        directory / "completion.json",
        dict(
            classification="accepted",
            artifacts={name: r.q.digest(directory / name) for name in r.ARTIFACTS},
        ),
    )
    if supervised:
        r.q.atomic_immutable_json(
            directory / "supervision.json",
            dict(
                classification="exited",
                returncode=0,
                wall_seconds=elapsed,
                peak_sampled_rss_mib=100.0,
                samples=1,
            ),
        )
    monkeypatch.setattr(r, "verify", lambda *_a: binding)
    monkeypatch.setattr(r, "checked", lambda *_a: (view, capture))
    monkeypatch.setattr(r.transfer, "assess", lambda *_a: checks)
    monkeypatch.setattr(r.m, "summarize", lambda *_a: summary)
    monkeypatch.setattr(r.q, "resource_evidence", lambda *_a: resources)
    return root, directory


@pytest.mark.parametrize(
    "supervised,resources,elapsed,classification",
    [
        (True, True, 1.0, "accepted"),
        (False, True, 1.0, "unfinished"),
        (True, False, 1.0, "infrastructure_failure"),
        (True, True, 301.0, "wall_limit"),
    ],
)
def test_supervised_audited_control_required_for_launch(
    tmp_path, monkeypatch, supervised, resources, elapsed, classification
):
    root, _ = completed_fixture(
        tmp_path,
        monkeypatch,
        supervised=supervised,
        resources=resources,
        elapsed=elapsed,
    )
    progress = r.status(root)
    assert progress["attempts"][0]["classification"] == classification
    assert progress["accepted"] == int(classification == "accepted")
    assert progress["next_call"] == (2 if classification == "accepted" else None)


def test_replay_refuses_hash_and_audit_disagreement(tmp_path, monkeypatch):
    root, directory = completed_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(r.transfer, "assess", lambda *_a: dict(passed=False))
    with pytest.raises(ValueError, match="independent audit"):
        r.status(root)
    (directory / "start.json").write_text("{}")
    with pytest.raises(ValueError, match="hash mismatch"):
        r.status(root)


def test_summary_accepts_archived_lists():
    bus = np.zeros((4, 13))
    bus[:, 0] = [70, 71, 72, 73]
    branch = np.zeros((1, 13))
    branch[0, [0, 1, 5, 10]] = [71, 73, 188, 1]
    result = dict(
        Vm=[[1.0, 0.99, 1.01, 0.94]],
        branch_s_from=[[170.0]],
        branch_s_to=[[169.0]],
        objective=12.0,
        energy_not_served=0.0,
        b_q=[[2.0]],
        b=[[3.0]],
    )
    summary = m.summarize(result, dict(case=dict(bus=bus, branch=branch)))
    assert summary["voltage_pu"]["73"] == 0.94
    assert summary["tightest_branch_margins"]["from"]["margin_mva"] == 18.0


def test_legacy_rejection_compatibility_cannot_accept_or_mutate():
    record = dict(
        iteration=2,
        native=dict(status=2),
        classification="native_rejection",
        checks=dict(passed=False, reason="native numerical rejection"),
    )
    assert r.archived_checks(record, legacy=True) == dict(
        record["checks"], native_status=2
    )
    assert "native_status" not in record["checks"]
    assert r.archived_checks(record, legacy=False) == record["checks"]
    for update in (
        dict(iteration=3),
        dict(native=dict(status=0)),
        dict(classification="accepted"),
        dict(checks=dict(passed=True)),
        dict(checks=dict(passed=False, reason="different rejection")),
    ):
        changed = dict(record, **update)
        assert r.archived_checks(changed, legacy=True) == changed["checks"]


def inherited_fixture(tmp_path, monkeypatch):
    origin, _ = completed_fixture(tmp_path, monkeypatch)
    directory = origin / "call-002"
    directory.mkdir()
    request = r.make_request(origin, 2, 300.0)
    record = dict(
        r.q.read(origin / "call-001/result.json.gz"),
        iteration=2,
        request=request,
        classification="native_rejection",
        native=dict(status=2),
        preparation_evidence=dict(native=dict(status=2)),
        checks=dict(passed=False, reason="native numerical rejection"),
    )
    for name in r.ARTIFACTS:
        value = r.q.read(origin / "call-001" / name)
        if name == "request.json":
            value = request
        elif name == "x0.json.gz":
            value = dict(iteration=2)
        elif name == "result.json.gz":
            value = record
        elif name == "native.json.gz":
            value = dict(iteration=2, data=record["native"])
        elif name == "preparation.json.gz":
            value = dict(iteration=2, data=record["preparation_evidence"])
        (r.q.atomic_gzip_json if name.endswith(".gz") else r.q.atomic_immutable_json)(
            directory / name, value
        )
    r.q.atomic_immutable_json(
        directory / "completion.json",
        dict(
            classification="native_rejection",
            artifacts={name: r.q.digest(directory / name) for name in r.ARTIFACTS},
        ),
    )
    r.q.atomic_immutable_json(
        directory / "supervision.json",
        dict(
            classification="exited",
            returncode=0,
            wall_seconds=40.0,
            peak_sampled_rss_mib=100.0,
            samples=1,
        ),
    )
    view = (None, None, None, {}, {}, {})
    monkeypatch.setattr(r, "checked", lambda _b, n: (view, dict(iteration=n)))
    monkeypatch.setattr(
        r.transfer,
        "assess",
        lambda *a: (
            dict(passed=True)
            if a[5]["native"]["status"] == 0
            else dict(
                passed=False, reason="native numerical rejection", native_status=2
            )
        ),
    )
    current = tmp_path / "continuation"
    current.mkdir()
    r.q.atomic_immutable_json(current / "protocol.json", r.protocol())
    binding = dict(predecessor=dict(path=str(origin)), rows=[{}] * 4)
    return origin, current, binding


def test_continuation_carries_prefix_and_starts_at_three_without_writes(
    tmp_path, monkeypatch
):
    origin, current, binding = inherited_fixture(tmp_path, monkeypatch)
    before = {
        str(p.relative_to(origin)): r.q.digest(p)
        for p in origin.rglob("*")
        if p.is_file()
    }
    progress = r.replay(current, binding)
    assert progress["launches"] == progress["inherited_launches"] == 2
    assert progress["local_launches"] == 0 and progress["worker_seconds"] == 41.0
    assert progress["accepted"] == 1 and progress["next_call"] == 3
    assert [a["classification"] for a in progress["attempts"]] == [
        "accepted",
        "native_rejection",
    ]
    assert before == {
        str(p.relative_to(origin)): r.q.digest(p)
        for p in origin.rglob("*")
        if p.is_file()
    }
    # Even a plausible duplicate control request must not restart call 1.
    duplicate = current / "call-001"
    duplicate.mkdir()
    r.q.atomic_immutable_json(
        duplicate / "request.json", r.make_request(current, 1, 300.0)
    )
    with pytest.raises(ValueError, match="sequence"):
        r.replay(current, binding)


def test_predecessor_hash_check_refuses_artifact_changes(tmp_path):
    origin = tmp_path / "origin"
    origin.mkdir()
    r.q.atomic_immutable_json(origin / "request.json", dict(call_id=1))
    predecessor = dict(
        path=str(origin),
        files={"request.json": r.q.digest(origin / "request.json")},
        source_snapshot_files={},
    )
    r.check_predecessor_files(predecessor)
    (origin / "request.json").write_text("{}")
    with pytest.raises(ValueError, match="predecessor"):
        r.check_predecessor_files(predecessor)
