"""Synthetic E3 replay, restart, supervision and physics; no OPF solves."""

import ast
from copy import deepcopy
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from cvxopf import build_opf_multistep, extract_results
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json, atomic_gzip_json
from experiments.case118_tracy_2021 import e3 as model, run_e3 as runner
from experiments.case118_tracy_2021.stage_d import read
from tests.test_tracy_stage_d import fixture


def selection():
    return dict(storage_device_ids=[f"battery-{i}" for i in range(27)],
        capacity_mwh=list(range(1, 28)), source_hashes={}, periods=[
        dict(regime=label, candidates=[dict(start=start, stop=start+24)])
        for label, start in zip(("Large surplus", "Large deficit", "Surplus to deficit",
                                "Deficit to surplus"), (3308, 1165, 8580, 2439), strict=True)])


def test_twenty_matched_arms_and_explicit_depletion_vectors():
    study = model.make_study(selection())
    assert len(study["arms"]) == 20
    assert sum(a["leg"] == "energy_neutral" for a in study["arms"]) == 16
    depletion = [a for a in study["arms"] if a["leg"] == "deficit_depletion"]
    assert [a["formulation"] for a in depletion] == list(model.FORMULATIONS)
    for a in depletion:
        np.testing.assert_allclose(a["initial_soc_mwh"], .6*np.arange(1, 28))
        np.testing.assert_allclose(a["terminal_soc_mwh"], .25*np.arange(1, 28))
        assert (a["start"], a["stop"]) == (1165, 1189)


@pytest.mark.parametrize("field,value", [("rss_mib", 8192), ("max_launches", True),
    ("max_launches", 2.5), ("wall_seconds", float("nan")), ("poll_seconds", 0)])
def test_invalid_protocol_rejected(field, value):
    protocol = deepcopy(model.DEFAULT_PROTOCOL)
    protocol[field] = value
    with pytest.raises(ValueError):
        model.validate_protocol(protocol)


def setup_root(root, monkeypatch, formulation="ac", *, arm_count=1):
    context = {"clean": True, "commit": "a"*40}
    monkeypatch.setattr(model, "verify_context", lambda expected: None)
    study = model.make_study(selection())
    arm = next(a for a in study["arms"] if a["formulation"] == formulation)
    study["arms"] = [{**arm, "id": i} for i in range(arm_count)]
    atomic_immutable_json(root / "binding.json", dict(context=context, study=study))
    protocol = deepcopy(model.DEFAULT_PROTOCOL)
    protocol["poll_seconds"] = .01
    runner.queue_protocol(root, protocol, "synthetic initial protocol")
    return study


def audited(arm, role, record):
    return True


def append(root, progress, *, accepted=False, outcome="exited", completion=True, supervised=True):
    item = progress["next"]
    arm = item["arm"]
    arm_dir = root / f"arm-{arm['id']:03d}"
    directory = arm_dir / f"attempt-{len(list(arm_dir.glob('attempt-*'))):03d}"
    request = runner.request_for(arm, item["slot"], item["causal_source"],
        item["target_free_source"], read(root / "control.json")["protocol"], 10.)
    atomic_immutable_json(directory / "request.json", request)
    if supervised:
        atomic_immutable_json(directory / "supervision.json", dict(classification=outcome,
            returncode=0, wall_seconds=2., peak_sampled_rss_mib=10.))
    if completion:
        atomic_immutable_json(directory / "start.json", dict(causal_start={}, assigned_start={"synthetic": [0.]}))
        evidence = dict(complete_x0=[0.], layout=[dict(name="synthetic", start=0,
            stop=1, is_original_variable=True)], layout_signature="synthetic",
            model_coordinate_count=1, auxiliary_coordinate_count=0,
            object_ids_before={}, object_ids_after={})
        atomic_gzip_json(directory / "x0.json.gz", dict(iteration=0, **evidence))
        classification = "accepted" if accepted else "rejected"
        atomic_gzip_json(directory / "result.json.gz", dict(iteration=0, classification=classification,
            request=request, execution_context=read(root / "binding.json")["context"],
            audit=dict(passed=accepted), logical_solution={}))
        artifacts = {name: model.digest(directory / name) for name in
            ("request.json", "start.json", "x0.json.gz", "result.json.gz")}
        atomic_immutable_json(directory / "completion.json", dict(classification=classification, artifacts=artifacts))
    return directory


def test_restart_uses_archives_not_progress_cache(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    append(tmp_path, runner.replay(tmp_path, audited), accepted=True)
    resumed = runner.replay(tmp_path, audited)
    assert resumed["finished"] and resumed["accepted_arms"] == 1
    assert resumed["launches"] == 1 and resumed["worker_seconds"] == 2
    assert runner.replay(tmp_path, audited, partial=True) == resumed
    assert not (tmp_path / "progress.json").exists()


@pytest.mark.parametrize("stage", ["prepared", "launched", "archived"])
def test_status_reports_live_tail_without_accepting_or_writing(tmp_path, monkeypatch, capsys, stage):
    setup_root(tmp_path, monkeypatch, "socp", arm_count=2)
    append(tmp_path, runner.replay(tmp_path, audited), accepted=True)
    directory = append(tmp_path, runner.replay(tmp_path, audited), accepted=True,
                       completion=stage == "archived", supervised=False)
    if stage != "prepared":
        atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid(), started_utc="synthetic"))
        atomic_immutable_json(directory / "phase.json", dict(phase="native_solve", elapsed_seconds=1.))
    audit_calls = []
    monkeypatch.setattr(runner, "independent_auditor", lambda *args:
                        lambda arm, role, record: audit_calls.append(arm["id"]) or True)
    for name in ("reconcile", "supervise"):
        monkeypatch.setattr(runner, name, lambda *args: pytest.fail("status mutated execution"))
    monkeypatch.setattr(model, "worker", lambda *args: pytest.fail("status launched worker"))
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    monkeypatch.setattr(sys, "argv", ["run_e3", "--output", str(tmp_path), "--status"])
    assert runner.main() == 0
    status = ast.literal_eval(capsys.readouterr().out)
    assert status["accepted_arms"] == 1 and not status["finished"]
    assert status["arms"][0]["classification"] == "accepted"
    assert status["arms"][1]["classification"] == "pending"
    assert status["arms"][1]["next_slot"] == 0
    assert status["worker_seconds"] == 2. and status["launches"] == 2
    assert status["next"] is None and audit_calls == [0]
    active = status["active_attempt"]
    assert active["path"] == "arm-001/attempt-000"
    assert active["request"] == read(directory / "request.json")
    assert active["classification"] == "awaiting_supervision" and not active["accepted"]
    assert active["wall_seconds"] is None
    assert (active["launch"] is not None) == (stage != "prepared")
    assert (active["phase"] is not None) == (stage != "prepared")
    after = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    with pytest.raises(ValueError, match="unreconciled"):
        runner.replay(tmp_path, audited)


def test_partial_target_free_archive_does_not_supply_seed(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    for _ in range(5):
        append(tmp_path, runner.replay(tmp_path, audited))
    directory = append(tmp_path, runner.replay(tmp_path, audited), accepted=True, supervised=False)
    status = runner.replay(tmp_path, audited, partial=True)
    assert status["active_attempt"]["role"] == "target_free"
    assert status["accepted_arms"] == 0 and status["arms"][0]["next_slot"] == 5
    assert status["next"] is None and status["worker_seconds"] == 10.
    atomic_immutable_json(directory / "supervision.json", dict(classification="exited",
                          returncode=0, wall_seconds=2.))
    resumed = runner.replay(tmp_path, audited)
    assert resumed["next"]["slot"] == 6 and resumed["next"]["target_free_source"] is not None
    assert resumed["accepted_arms"] == 0 and resumed["active_attempt"] is None


def test_partial_status_tolerates_directory_before_request(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    directory = tmp_path / "arm-000/attempt-000"
    directory.mkdir(parents=True)
    status = runner.replay(tmp_path, audited, partial=True)
    assert status["active_attempt"]["classification"] == "awaiting_request"
    assert status["active_attempt"]["request"] is None
    assert status["accepted_arms"] == 0 and status["arms"][0]["next_slot"] == 0
    assert status["next"] is None and status["worker_seconds"] == 0.
    with pytest.raises(FileNotFoundError):
        runner.replay(tmp_path, audited)


def test_partial_status_still_rejects_attempt_after_unfinished_tail(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    append(tmp_path, runner.replay(tmp_path, audited), supervised=False, completion=False)
    (tmp_path / "arm-000/attempt-001").mkdir()
    with pytest.raises(ValueError, match="unfinished attempt precedes"):
        runner.replay(tmp_path, audited, partial=True)


def test_exact_ladder_and_target_free_never_completes_arm(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    for role in model.ROLES:
        progress = runner.replay(tmp_path, audited)
        assert model.ROLES[progress["next"]["slot"]] == role
        append(tmp_path, progress, accepted=role == "target_free")
        if role == "target_free":
            progress = runner.replay(tmp_path, audited)
            assert progress["accepted_arms"] == 0
            assert progress["next"]["target_free_source"] is not None
    progress = runner.replay(tmp_path, audited)
    assert progress["finished"] and progress["accepted_arms"] == 0
    assert progress["launches"] == 10


def test_failed_target_free_skips_dependent_slots(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    for _ in range(6):
        append(tmp_path, runner.replay(tmp_path, audited))
    progress = runner.replay(tmp_path, audited)
    assert progress["finished"] and progress["arms"][0]["classification"] == "unresolved"


def test_interruption_retries_same_slot_without_overwrite(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    first = append(tmp_path, runner.replay(tmp_path, audited), accepted=True, outcome="interrupted")
    original = (first / "result.json.gz").read_bytes()
    progress = runner.replay(tmp_path, audited)
    assert progress["next"]["slot"] == 0 and progress["accepted_arms"] == 0
    assert progress["launches"] == 1
    second = append(tmp_path, progress, accepted=True)
    assert first != second and original == (first / "result.json.gz").read_bytes()
    assert runner.replay(tmp_path, audited)["accepted_arms"] == 1


def test_revision_mid_ladder_keeps_original_attempt_protocol(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    first = append(tmp_path, runner.replay(tmp_path, audited))
    original = read(first / "request.json")
    p = deepcopy(model.DEFAULT_PROTOCOL)
    p["wall_seconds"] = 2400.
    p["ac_options"]["max_iter"] = 4000
    runner.queue_protocol(tmp_path, p, "owner-reviewed synthetic budget change")
    progress = runner.replay(tmp_path, audited)
    second = append(tmp_path, progress, accepted=True)
    assert read(first / "request.json") == original
    assert read(second / "request.json")["protocol"] != original["protocol"]
    assert runner.replay(tmp_path, audited)["accepted_arms"] == 1


def test_protocol_requires_reason_and_fixed_rss(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="reason"):
        runner.queue_protocol(tmp_path, model.DEFAULT_PROTOCOL, "")


def test_corrupt_archive_or_audit_cannot_advance(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    directory = append(tmp_path, runner.replay(tmp_path, audited), accepted=True)
    with pytest.raises(ValueError, match="independent"):
        runner.replay(tmp_path, lambda *args: False)
    (directory / "result.json.gz").write_bytes(b"corrupt synthetic archive")
    with pytest.raises(ValueError, match="integrity"):
        runner.replay(tmp_path, audited)


def test_missing_completion_rejected_and_timeout_consumes_slot(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    append(tmp_path, runner.replay(tmp_path, audited), completion=False, outcome="wall_limit")
    progress = runner.replay(tmp_path, audited)
    assert progress["next"]["slot"] == 1 and progress["accepted_arms"] == 0


def test_orphan_refusal_and_conservative_budget(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    progress = runner.replay(tmp_path, audited)
    item = progress["next"]
    directory = tmp_path / "arm-000/attempt-000"
    atomic_immutable_json(directory / "request.json", runner.request_for(
        item["arm"], 0, None, None, read(tmp_path / "control.json")["protocol"], 10.))
    atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    with pytest.raises(RuntimeError, match="live orphan"):
        runner.reconcile(tmp_path)
    monkeypatch.setattr(runner.os, "kill", lambda *args: (_ for _ in ()).throw(ProcessLookupError()))
    runner.reconcile(tmp_path)
    progress = runner.replay(tmp_path, audited)
    assert progress["worker_seconds"] == 10 and progress["next"]["slot"] == 0


@pytest.mark.parametrize("mode,expected", [("normal", "exited"), ("rss", "rss_limit"),
    ("wall", "wall_limit"), ("missing", "supervisor_failure"), ("stop", "interrupted")])
def test_supervision_enforces_limits_without_solver(tmp_path, monkeypatch, mode, expected):
    setup_root(tmp_path, monkeypatch, "socp")
    directory = tmp_path / "arm-000/attempt-000"
    directory.mkdir(parents=True)
    request = dict(protocol=read(tmp_path / "control.json")["protocol"], wall_seconds=.12)
    if mode == "stop":
        atomic_immutable_json(tmp_path / "STOP", {})
    rss = None if mode == "missing" else 17000. if mode == "rss" else 10.
    clock = [0.]

    class Process:
        pid = 123456
        returncode = None

        def poll(self):
            if mode == "normal" and clock[0] >= .025:
                self.returncode = 0
            return self.returncode

        def wait(self):
            return self.returncode

    process = Process()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(runner, "_terminate", lambda p: setattr(p, "returncode", -15))
    monkeypatch.setattr(runner, "time", SimpleNamespace(monotonic=lambda: clock[0],
        sleep=lambda duration: clock.__setitem__(0, clock[0]+duration)))
    result = runner.supervise(["synthetic-worker"],
        directory, tmp_path, request, rss_reader=lambda pid: rss)
    assert result["classification"] == expected
    assert (directory / "supervision.json").exists()


def analytic_socp(w=24):
    kwargs, ac, _, _ = fixture(w)
    kwargs["formulation"] = "socp"
    build = build_opf_multistep(**kwargs)
    for variable in build.prob.variables():
        variable.value = variable.project(np.zeros(variable.shape))
    for name, variable in build.variables.items():
        if name in ac.variables:
            variable.value = ac.variables[name].value
    build.variables["w"].value = np.ones(build.variables["w"].shape)
    build.variables["W_re"].value = np.ones(build.variables["W_re"].shape)
    build.prob._status = "optimal"
    build.prob._value = float(build.prob.objective.value)
    result = model.jsonable(extract_results(build))
    named = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
    return kwargs, build, result, named


def test_socp_tracy_accounting_and_lifted_physics_without_solve():
    kwargs, build, result, named = analytic_socp()
    common, relaxation = model.audit(build, result, kwargs, named)
    assert common["passed"], common
    assert relaxation["complete"] and relaxation["feasible"]


@pytest.mark.parametrize("key", ["W_re", "w", "Qg", "b_q", "soc", "p_net", "q_net",
    "branch_p_from", "q_load_served", "branch_s_to", "objective", "energy_not_served"])
def test_socp_corruptions_rejected(key):
    kwargs, build, result, named = analytic_socp(3)
    result[key] = np.asarray(result[key])+1
    common, _ = model.audit(build, result, kwargs, named)
    assert not common["passed"]


def test_24_hour_complete_ac_start_capture_with_mocked_native_solver(tmp_path, monkeypatch):
    setup_root(tmp_path, monkeypatch)
    kwargs, _, _, _ = fixture(24)
    request = runner.request_for(read(tmp_path / "binding.json")["study"]["arms"][0],
        0, None, None, read(tmp_path / "control.json")["protocol"], 10.)
    directory = tmp_path / "arm-000/attempt-000"
    atomic_immutable_json(directory / "request.json", request)
    atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    monkeypatch.setattr(model, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "kwargs_for_arm", lambda *args: kwargs)
    called = []

    def fail(*args, **kw):
        called.append(True)
        raise RuntimeError("synthetic native failure; no numerical solve")

    monkeypatch.setattr(model.starts.IPOPT, "solve_via_data", fail)
    model.worker(directory, tmp_path)
    assert called == [True]
    captured = read(directory / "x0.json.gz")
    assert len(captured["complete_x0"]) == captured["model_coordinate_count"]+captured["auxiliary_coordinate_count"]
    assert read(directory / "completion.json")["classification"] != "accepted"


def simulated_supervisor(command, directory, root, request):
    """Control-plane rehearsal only: no worker, graph, or numerical solve."""
    atomic_immutable_json(directory / "start.json", dict(causal_start={}, assigned_start={"synthetic": [0.]}))
    atomic_gzip_json(directory / "x0.json.gz", dict(iteration=request["arm_id"],
        complete_x0=[0.], layout=[dict(name="synthetic", start=0, stop=1,
        is_original_variable=True)], layout_signature="synthetic",
        model_coordinate_count=1, auxiliary_coordinate_count=0,
        object_ids_before={}, object_ids_after={}))
    atomic_gzip_json(directory / "result.json.gz", dict(iteration=request["arm_id"],
        classification="accepted", request=request,
        execution_context=read(root / "binding.json")["context"], audit=dict(passed=True)))
    atomic_immutable_json(directory / "completion.json", dict(classification="accepted",
        artifacts={name: model.digest(directory / name) for name in
                   ("request.json", "start.json", "x0.json.gz", "result.json.gz")}))
    supervision = dict(classification="exited", returncode=0, wall_seconds=2., peak_sampled_rss_mib=10.)
    atomic_immutable_json(directory / "supervision.json", supervision)
    return supervision


def simulate_run(monkeypatch):
    current = dict(clean=True, commit="a"*40, thread_environment={k: "1" for k in runner.THREAD_KEYS})
    monkeypatch.setattr(model, "context", lambda: current)
    monkeypatch.setattr(model, "verify_context", lambda expected: None)
    monkeypatch.setattr(model, "specification", lambda: model.make_study(selection()))
    monkeypatch.setattr(model, "verified_inputs", lambda: None)
    monkeypatch.setattr(runner, "independent_auditor", lambda *args: audited)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 10.)
    return current


def test_full_control_plane_stop_resume_acknowledges_marker(tmp_path, monkeypatch):
    current = simulate_run(monkeypatch)
    root = tmp_path / "run"
    calls = []

    def stop_once(command, directory, root, request):
        calls.append(request["arm_id"])
        record = simulated_supervisor(command, directory, root, request)
        atomic_immutable_json(root / "STOP", dict(reason="synthetic planned stop"))
        return record

    monkeypatch.setattr(runner, "supervise", stop_once)
    stopped = runner.run(root, current["commit"])
    assert stopped["classification"] == "operator_stop" and calls == [0]
    retained = (root / "arm-000/attempt-000/result.json.gz").read_bytes()
    monkeypatch.setattr(runner, "supervise", simulated_supervisor)
    resumed = runner.run(root, current["commit"], resume=True, acknowledge_stop=True)
    assert resumed["classification"] == "complete"
    assert resumed["progress"]["accepted_arms"] == 20
    assert retained == (root / "arm-000/attempt-000/result.json.gz").read_bytes()
    assert len(list((root / "stops").glob("stop-*.json"))) == 1
    assert len(list((root / "invocations").glob("invocation-*"))) == 2


def test_budget_update_applies_only_at_next_attempt(tmp_path, monkeypatch):
    current = simulate_run(monkeypatch)
    root = tmp_path / "run"
    requests = []

    def change_after_first(command, directory, root, request):
        requests.append(deepcopy(request))
        record = simulated_supervisor(command, directory, root, request)
        if len(requests) == 1:
            revised = deepcopy(model.DEFAULT_PROTOCOL)
            revised.update(max_launches=1, wall_seconds=60.)
            runner.queue_protocol(root, revised, "synthetic one-launch budget")
        return record

    monkeypatch.setattr(runner, "supervise", change_after_first)
    limited = runner.run(root, current["commit"])
    assert limited["classification"] == "budget_limit" and len(requests) == 1
    assert requests[0]["wall_seconds"] == 1800
    revised = deepcopy(model.DEFAULT_PROTOCOL)
    revised.update(max_launches=20, wall_seconds=60.)
    runner.queue_protocol(root, revised, "synthetic reviewed extension")
    monkeypatch.setattr(runner, "supervise", simulated_supervisor)
    resumed = runner.run(root, current["commit"], resume=True)
    assert resumed["classification"] == "complete"
    assert read(root / "arm-001/attempt-000/request.json")["wall_seconds"] == 60


def test_preflight_never_writes_or_launches(tmp_path, monkeypatch):
    current = simulate_run(monkeypatch)
    root = tmp_path / "not-created"
    monkeypatch.setattr(runner, "supervise", lambda *args: pytest.fail("worker launched"))
    assert runner.run(root, current["commit"], preflight=True)["classification"] == "preflight_only"
    assert not root.exists()


@pytest.mark.parametrize("classification,expected", [("rss_limit", "rss_limit"),
    ("interrupted", "operator_stop"), ("supervisor_failure", "supervisor_failure")])
def test_killed_worker_preserves_stop_reason(tmp_path, monkeypatch, classification, expected):
    current = simulate_run(monkeypatch)

    def stopped(command, directory, root, request):
        record = dict(classification=classification, returncode=-15,
                      wall_seconds=2., peak_sampled_rss_mib=17000.)
        atomic_immutable_json(directory / "supervision.json", record)
        return record

    monkeypatch.setattr(runner, "supervise", stopped)
    record = runner.run(tmp_path / "run", current["commit"])
    assert record["classification"] == expected


def test_killed_wall_limited_worker_advances_to_independent_arm(tmp_path, monkeypatch):
    current = simulate_run(monkeypatch)
    calls = []

    def limited_once(command, directory, root, request):
        calls.append(request["arm_id"])
        if len(calls) > 1:
            return simulated_supervisor(command, directory, root, request)
        record = dict(classification="wall_limit", returncode=-15,
                      wall_seconds=2., peak_sampled_rss_mib=10.)
        atomic_immutable_json(directory / "supervision.json", record)
        return record

    monkeypatch.setattr(runner, "supervise", limited_once)
    record = runner.run(tmp_path / "run", current["commit"])
    assert record["classification"] == "complete_with_unresolved_arms"
    assert record["progress"]["accepted_arms"] == 19 and calls == list(range(20))
