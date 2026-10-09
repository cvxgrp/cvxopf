"""Portable non-solving starts, canonical handoff and ladder lifecycle tests."""

from copy import deepcopy
from dataclasses import replace

import cvxpy as cp
import numpy as np
import pytest

from cvxopf._ac_start_mapping import variables_by_name
from cvxopf._hierarchical_solver import _assign_start
from experiments.case118_tracy_2021 import e3_prepared
from experiments.numerical_preparation import fixture
from experiments.numerical_preparation.audit import serializable
from experiments.tracy_battery_transfer import model as transfer
from experiments.tracy_battery_transfer_starts import model as m, run as r


@pytest.fixture(autouse=True)
def no_optimizer(monkeypatch):
    monkeypatch.setattr(
        cp.Problem, "solve", lambda *_a, **_k: pytest.fail("optimizer not authorized")
    )


def data():
    cases = {}
    for index in range(6):
        cases[f"scenario-{index}"] = dict(
            ac=dict(
                arm=dict(
                    start=2439 if index == 4 else 0,
                    stop=2463 if index == 4 else 24,
                    leg="energy_neutral",
                )
            )
        )
    return dict(cases=cases)


def setup():
    kwargs = fixture.case9_kwargs(3, "ac")
    kwargs["options"] = replace(
        kwargs["options"],
        numerical_preparation=e3_prepared.policy("ac", "component_first"),
    )
    schedule = np.array([[3.0], [-2.0], [1.0 + 2e-8]])
    build, kwargs, boundaries, _ = transfer.transfer_build(kwargs, schedule)
    start = r.q.physical_start(build, kwargs)
    start["v"][:] = 1.01
    start["b"], start["soc"] = schedule.T.copy(), boundaries.T.copy()
    _assign_start(build, start)
    return build, kwargs, start


def test_frozen_registry_scope_order_seeds_and_budget():
    trials = m.registry(data())
    assert len(trials) == m.LIMITS["max_launches"] == 21
    assert [trial["id"] for trial in trials] == list(range(1, 22))
    assert [trial["slot"] for trial in trials[:7]] == list(range(7))
    assert [trial["center"] for trial in trials[:7]] == [
        "flat",
        *(["matched_ac"] * 3),
        *(["flat"] * 3),
    ]
    assert [trial["scale"] for trial in trials[:7]] == [
        None,
        1e-4,
        1e-3,
        1e-2,
        1e-4,
        1e-3,
        1e-2,
    ]
    assert len({trial["seed"] for trial in trials if trial["seed"] is not None}) == 18
    assert trials[1]["seed"] == 17001311
    assert m.LIMITS["total_worker_seconds"] == 21 * m.LIMITS["wall_seconds"]
    wrong = data()
    wrong["cases"]["scenario-4"]["ac"]["arm"]["start"] += 1
    with pytest.raises(ValueError, match="restricted"):
        m.registry(wrong)


@pytest.mark.parametrize("slot", range(7))
def test_deterministic_complete_starts_preserve_all_pins_and_boundary(slot):
    build, kwargs, matched = setup()
    before = deepcopy(matched)
    trial = m.registry(data())[slot]
    raw, assigned = m.alternative_start(build, kwargs, matched, trial)
    raw2, assigned2 = m.alternative_start(build, kwargs, matched, trial)
    assert set(raw) == set(assigned) == set(variables_by_name(build))
    for name in matched:
        np.testing.assert_array_equal(matched[name], before[name])
        np.testing.assert_array_equal(raw[name], raw2[name])
        np.testing.assert_array_equal(assigned[name], assigned2[name])
        assert np.isfinite(raw[name]).all() and np.isfinite(assigned[name]).all()
    for name in ("b", "soc"):
        np.testing.assert_array_equal(raw[name], matched[name])
        np.testing.assert_array_equal(assigned[name], matched[name])
    np.testing.assert_array_equal(assigned["theta"][build.data["ref"]], 0)
    masks = r.q.structural_inputs(kwargs)
    assert np.all(assigned["p_nd"][np.asarray(masks["nd_fixed"]).T] == 0)
    if slot == 0:
        assert not np.array_equal(assigned["v"], matched["v"])
    captured = e3_prepared.prepared_start(build, slot + 1)
    assert captured["iteration"] == slot + 1
    # The exact pin must survive normalization/cycling coordinate transformation.
    assert np.max(build.prob.constraints[-1].violation()) == 0
    assert captured["cost_maps"] and captured["coordinates"]["values"]


def binding():
    return dict(
        rows=[dict(job=trial["job"], trial=trial) for trial in m.registry(data())]
    )


def outcome(
    trial_id,
    worker="native_rejection",
    classification="exited",
    compatible=False,
    returncode=0,
):
    return dict(
        trial=m.registry(data())[trial_id - 1],
        classification=classification,
        worker_classification=worker,
        compatible=compatible,
        returncode=returncode,
    )


def test_ladder_advances_failures_and_skips_only_successful_donor():
    frozen = binding()
    assert r.next_trial(frozen, []) == m.registry(data())[0]
    assert r.next_trial(frozen, [outcome(1)])["id"] == 2
    assert r.next_trial(frozen, [outcome(1, classification="wall_limit")])["id"] == 2
    assert (
        r.next_trial(frozen, [outcome(1, worker="compatible", compatible=True)])["id"]
        == 8
    )
    assert r.next_trial(frozen, [outcome(n) for n in range(1, 8)])["id"] == 8
    assert r.next_trial(frozen, [outcome(21)]) is None


@pytest.mark.parametrize(
    "changes",
    [
        dict(classification="unfinished"),
        dict(classification="interrupted"),
        dict(classification="rss_limit"),
        dict(worker="exception"),
        dict(worker="audit_failure"),
        dict(returncode=1),
        dict(worker="compatible", compatible=False),
    ],
)
def test_unfinished_failed_supervision_or_unaudited_convergence_cannot_advance(changes):
    assert r.next_trial(binding(), [outcome(1, **changes)]) is None


@pytest.mark.parametrize("wall", [True, 0, -1, float("nan"), float("inf"), 3601])
def test_requests_reject_invalid_or_excess_budget(tmp_path, wall):
    r.q.atomic_immutable_json(tmp_path / "protocol.json", r.protocol())
    trial = m.registry(data())[0]
    req = r.request(tmp_path, 1, trial, wall)
    with pytest.raises(ValueError):
        r.validate_request(tmp_path, 1, trial, req, 75600)


def journal(tmp_path, monkeypatch, native_status=2, supervised=True):
    frozen = binding()
    trial = frozen["rows"][0]["trial"]
    monkeypatch.setattr(r, "verify_binding", lambda root: (frozen, data()))
    monkeypatch.setattr(e3_prepared, "verified_inputs", lambda: {})
    monkeypatch.setattr(r.q, "resource_evidence", lambda *_: True)
    raw = dict(b=[[3.0]])
    assigned = deepcopy(raw)
    captured = dict(iteration=1)
    row = frozen["rows"][0]
    monkeypatch.setattr(
        r,
        "checked_construction",
        lambda *_: (None, None, None, raw, assigned, row, captured),
    )
    checks = dict(passed=native_status == 0, reason="test only")
    monkeypatch.setattr(transfer, "assess", lambda *_: checks)
    monkeypatch.setattr(
        transfer, "comparison", lambda *_: dict(interpretation="test only")
    )
    r.q.atomic_immutable_json(tmp_path / "binding.json", frozen)
    r.q.atomic_immutable_json(tmp_path / "protocol.json", r.protocol())
    directory = tmp_path / "call-001"
    directory.mkdir()
    req = r.request(tmp_path, 1, trial, 3600)
    record = dict(
        iteration=1,
        call_id=1,
        request=req,
        optimizer_calls=1,
        exception=None,
        binding_sha256=r.q.digest(tmp_path / "binding.json"),
        native=dict(status=native_status),
        preparation_evidence={},
        checks=checks,
        row=row,
        result={},
        named_costs={},
        classification="compatible" if native_status == 0 else "native_rejection",
    )
    r.q.atomic_immutable_json(directory / "request.json", req)
    r.q.atomic_immutable_json(directory / "monitoring.json", {})
    r.q.atomic_immutable_json(
        directory / "raw-start.json", dict(raw_start=serializable(raw))
    )
    r.q.atomic_immutable_json(
        directory / "start.json", dict(assigned_start=serializable(assigned))
    )
    for name, value in (
        ("x0.json.gz", captured),
        ("result.json.gz", record),
        ("native.json.gz", dict(iteration=1, data=record["native"])),
        ("preparation.json.gz", dict(iteration=1, data={})),
    ):
        r.q.atomic_gzip_json(directory / name, value)
    r.q.atomic_immutable_json(
        directory / "completion.json",
        dict(
            classification=record["classification"],
            artifacts={name: r.q.digest(directory / name) for name in r.ARTIFACTS},
        ),
    )
    if supervised:
        r.q.atomic_immutable_json(
            directory / "supervision.json",
            dict(
                classification="exited",
                returncode=0,
                wall_seconds=10.0,
                peak_sampled_rss_mib=100.0,
            ),
        )
    return directory


def test_status_replays_rejection_and_preserves_original_case_identity(
    tmp_path, monkeypatch
):
    journal(tmp_path, monkeypatch)
    report = r.status(tmp_path)
    assert report["finalized"] == report["native_rejections"] == 1
    assert report["compatible_cases"] == 0
    assert report["next_trial"]["id"] == 2
    assert report["worker_seconds"] == 10
    assert report["attempts"][0]["trial"]["job"]["id"] == 13


def test_status_does_not_accept_unsupervised_candidate(tmp_path, monkeypatch):
    journal(tmp_path, monkeypatch, native_status=0, supervised=False)
    report = r.status(tmp_path)
    assert report["compatible_cases"] == report["finalized"] == 0
    assert report["next_trial"] is None
    assert report["artifacts"]["completion.json"] == 1


def test_status_accepts_only_replayed_supervised_success_and_retains_skips(
    tmp_path, monkeypatch
):
    journal(tmp_path, monkeypatch, native_status=0)
    report = r.status(tmp_path)
    assert report["compatible_cases"] == 1
    assert report["next_trial"]["id"] == 8
    assert report["cases"][0]["accepted_slot"] == 0
    assert [row["disposition"] for row in report["ladder"][:8]] == [
        "finalized",
        *(["not_needed_after_acceptance"] * 6),
        "not_launched",
    ]


def test_independent_audit_disagreement_cannot_accept_candidate(tmp_path, monkeypatch):
    journal(tmp_path, monkeypatch, native_status=0)
    monkeypatch.setattr(transfer, "assess", lambda *_: dict(passed=False))
    with pytest.raises(ValueError, match="independent replay"):
        r.status(tmp_path)


def test_missing_resource_evidence_cannot_accept_candidate(tmp_path, monkeypatch):
    journal(tmp_path, monkeypatch, native_status=0)
    monkeypatch.setattr(r.q, "resource_evidence", lambda *_: False)
    with pytest.raises(ValueError, match="resource evidence"):
        r.status(tmp_path)


def test_over_wall_candidate_is_not_accepted(tmp_path, monkeypatch):
    directory = journal(tmp_path, monkeypatch, native_status=0)
    sup = r.q.read(directory / "supervision.json")
    sup["wall_seconds"] = 3601.0
    r.q.atomic_json(directory / "supervision.json", sup)
    report = r.status(tmp_path)
    assert report["compatible_cases"] == 0
    assert report["next_trial"] is None


def test_timeout_consumes_slot_without_accepting_partial_bundle(tmp_path, monkeypatch):
    directory = journal(tmp_path, monkeypatch, native_status=0, supervised=False)
    r.q.atomic_immutable_json(
        directory / "supervision.json",
        dict(
            classification="wall_limit",
            returncode=-15,
            wall_seconds=3600.1,
            peak_sampled_rss_mib=100.0,
        ),
    )
    report = r.status(tmp_path)
    assert report["timeouts"] == 1
    assert report["compatible_cases"] == report["infrastructure_failures"] == 0
    assert report["next_trial"]["id"] == 2


def test_manifest_tampering_is_detected(tmp_path, monkeypatch):
    directory = journal(tmp_path, monkeypatch)
    r.q.atomic_json(directory / "raw-start.json", dict(raw_start={}))
    with pytest.raises(ValueError, match="inconsistency"):
        r.status(tmp_path)


def test_later_launch_cannot_skip_a_failed_trial(tmp_path, monkeypatch):
    journal(tmp_path, monkeypatch)
    directory = tmp_path / "call-002"
    directory.mkdir()
    req = r.request(tmp_path, 2, m.registry(data())[2], 3600)
    r.q.atomic_immutable_json(directory / "request.json", req)
    with pytest.raises(ValueError, match="ladder request"):
        r.status(tmp_path)


def test_original_output_is_never_a_legal_target():
    with pytest.raises(ValueError, match="direct child"):
        r.validate_output(m.ORIGIN)


def test_dirty_execution_is_refused_without_creating_output(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "validate_output", lambda *_: None)
    monkeypatch.setattr(
        r, "frozen_binding", lambda: dict(context=dict(clean=False, commit="a" * 40))
    )
    target = tmp_path / "not-created"
    with pytest.raises(ValueError, match="clean full commit"):
        r.run(target, "a" * 40)
    assert not target.exists()
