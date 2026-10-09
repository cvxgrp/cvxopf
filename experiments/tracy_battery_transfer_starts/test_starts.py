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


def journal(
    tmp_path, monkeypatch, native_status=2, supervised=True, complete_binding=False
):
    frozen = binding()
    if complete_binding:
        frozen.update(limits=m.LIMITS, origin=str(m.ORIGIN), sources={})
    trial = frozen["rows"][0]["trial"]
    monkeypatch.setattr(
        r,
        "verify_binding",
        lambda root, **_k: (r.q.read(root / "binding.json"), data()),
    )
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


@pytest.mark.parametrize(
    "power", ["Now drawing from 'Battery Power'", "Now drawing from 'AC Power'"]
)
def test_battery_power_is_informational_not_a_launch_gate(monkeypatch, power):
    monkeypatch.setattr(r.q, "_child_rss_mib", lambda *_: 100.0)
    monkeypatch.setattr(
        r.subprocess,
        "check_output",
        lambda command, **_k: (
            power
            if command[0] == "pmset"
            else '{"temp": {"cpu_temp_avg": 55, "gpu_temp_avg": 45}}'
        ),
    )
    sample = r.monitoring()
    assert sample["battery"] == power
    assert bool(sample["power_warning"]) == ("AC Power" not in power)


def test_restart_context_accepts_new_commit_only_for_scoped_runner_changes():
    previous = dict(
        clean=True,
        commit="a" * 40,
        packages={"numpy": "same"},
        sources={
            "src/cvxopf/storage.py": "physical",
            **{p: "old" for p in r.RECOVERY_EDIT_PATHS},
        },
        alternative_start_sources={p: "old" for p in r.RECOVERY_EDIT_PATHS},
    )
    current = deepcopy(previous)
    current["commit"] = "b" * 40
    current["sources"].update({p: "new" for p in r.RECOVERY_EDIT_PATHS})
    current["alternative_start_sources"].update(
        {p: "new" for p in r.RECOVERY_EDIT_PATHS}
    )
    assert r.historical_context_matches(previous, current)
    current["sources"]["src/cvxopf/storage.py"] = "changed"
    assert not r.historical_context_matches(previous, current)


def continued_journal(tmp_path, monkeypatch):
    prior = tmp_path / "prior"
    prior.mkdir()
    journal(prior, monkeypatch, complete_binding=True)
    frozen = r.q.read(prior / "binding.json")
    r.q.atomic_immutable_json(
        prior / "invocation-finish.json",
        dict(
            outcome="failure",
            exception="RuntimeError: AC power required before launching a new attempt",
        ),
    )
    snapshot = dict(root=str(prior), files=r.retained_files(prior))
    frozen["predecessor"] = snapshot
    monkeypatch.setattr(r, "validate_output", lambda *_: None)
    return prior, frozen


def test_continuation_replays_completed_call_and_never_relaunches_it(
    tmp_path, monkeypatch
):
    prior, frozen = continued_journal(tmp_path, monkeypatch)
    before = r.retained_files(prior)
    frozen["context"] = dict(
        clean=True,
        commit="b" * 40,
        thread_environment={key: "1" for key in r.THREAD_KEYS},
    )
    monkeypatch.setattr(r, "frozen_binding", lambda predecessor: deepcopy(frozen))
    monkeypatch.setattr(
        r, "monitoring", lambda: dict(power_warning="running on battery")
    )
    launches = []

    def supervised(command, directory, root, req):
        launches.append(req["trial"]["id"])
        assert req["call_id"] == 2 and req["wall_seconds"] == 3600
        sup = dict(
            classification="wall_limit",
            returncode=-15,
            wall_seconds=3600.1,
            peak_sampled_rss_mib=100.0,
        )
        r.q.atomic_immutable_json(directory / "supervision.json", sup)
        (root / "STOP").write_text("synthetic lifecycle stopping rule")
        return sup

    monkeypatch.setattr(r.q, "supervise", supervised)
    root = tmp_path / "continued"
    result = r.run(root, "b" * 40, prior)
    assert result["outcome"] == "operator_stop"
    report = r.status(root)
    assert launches == [2]  # cp.Problem.solve is forbidden throughout this test.
    assert report["launches"] == report["finalized"] == 2
    assert report["inherited_launches"] == report["local_launches"] == 1
    assert report["native_rejections"] == report["timeouts"] == 1
    assert report["worker_seconds"] == 3610.1
    assert report["remaining_worker_seconds"] == 75600 - 3610.1
    assert report["next_trial"]["id"] == 3
    assert r.retained_files(prior) == before


def test_continuation_refuses_changed_predecessor_bytes(tmp_path, monkeypatch):
    prior, frozen = continued_journal(tmp_path, monkeypatch)
    (prior / "call-001" / "worker.log").write_text("changed")
    with pytest.raises(ValueError, match="evidence changed"):
        r.predecessor_progress(frozen, frozen["predecessor"])


def test_continuation_refuses_unfinished_predecessor(tmp_path, monkeypatch):
    prior, frozen = continued_journal(tmp_path, monkeypatch)
    directory = prior / "call-002"
    directory.mkdir()
    r.q.atomic_immutable_json(
        directory / "request.json", r.request(prior, 2, m.registry(data())[1], 3600)
    )
    frozen["predecessor"]["files"] = r.retained_files(prior)
    with pytest.raises(ValueError, match="unfinished"):
        r.predecessor_progress(frozen, frozen["predecessor"])


def test_binding_accepts_new_execution_commit_but_preserves_historical_identity(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(r, "validate_output", lambda *_: None)
    protocol_path = str((m.HERE / "PROTOCOL.md").relative_to(transfer.ROOT))
    run_path = str((m.HERE / "run.py").relative_to(transfer.ROOT))
    old = dict(
        clean=True,
        commit="a" * 40,
        sources={"src/cvxopf/storage.py": "same", run_path: "old"},
        alternative_start_sources={protocol_path: "old-protocol", run_path: "old"},
    )
    current = deepcopy(old)
    current["commit"] = "b" * 40
    current["sources"][run_path] = "new"
    current["alternative_start_sources"] = {
        protocol_path: r.protocol()["protocol_sha256"],
        run_path: "new",
    }
    monkeypatch.setattr(r, "context", lambda: current)
    monkeypatch.setattr(transfer, "source_inventory", data)
    monkeypatch.setattr(m, "origin_hashes", lambda *_: {})
    frozen = dict(
        binding(), context=old, limits=m.LIMITS, origin=str(m.ORIGIN), sources={}
    )
    historical_protocol = deepcopy(r.protocol())
    historical_protocol["protocol"].pop("power_policy")
    historical_protocol["protocol_sha256"] = "old-protocol"
    r.q.atomic_immutable_json(tmp_path / "binding.json", frozen)
    r.q.atomic_immutable_json(tmp_path / "protocol.json", historical_protocol)
    restored, _ = r.verify_binding(tmp_path, historical=True)
    assert restored["context"]["commit"] == "a" * 40
    with pytest.raises(ValueError, match="binding changed"):
        r.verify_binding(
            tmp_path
        )  # Old execution authority cannot launch a new worker.
    frozen["context"] = deepcopy(current)
    r.q.atomic_json(tmp_path / "binding.json", frozen)
    r.q.atomic_json(tmp_path / "protocol.json", r.protocol())
    restored, _ = r.verify_binding(tmp_path)
    assert restored["context"]["commit"] == "b" * 40
    current["sources"]["src/cvxopf/storage.py"] = "changed"
    with pytest.raises(ValueError, match="binding changed"):
        r.verify_binding(tmp_path, historical=True)
