"""Matched comparison science and lifecycle; native optimization is forbidden."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import clarabel
import cyipopt
import numpy as np
import pytest

from cvxopf import build_opf_multistep
from experiments.case118_tracy_2021 import e3_prepared
from experiments.case118_tracy_2021.stage_d import read, reference
from experiments.numerical_preparation import matched_model as m, matched_audit as a, run_matched as r
from tests.test_tracy_stage_d import fixture


@pytest.fixture(autouse=True)
def forbid_native_optimization(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("non-solving matched tests entered a native optimizer")
    monkeypatch.setattr(clarabel, "DefaultSolver", forbidden)
    monkeypatch.setattr(cyipopt, "Problem", forbidden)
    monkeypatch.setattr(m.IPOPT, "solve_via_data", forbidden)


def kwargs_for(treatment="baseline", T=3):
    kwargs, original, _, _ = fixture(T)
    arm = dict(m.arms()[0], T=T, treatment=treatment)
    kwargs["options"] = replace(kwargs.get("options", m.OPFOptions()), numerical_preparation=m.policy(arm))
    kwargs["automatic_sparse_dispatch"] = False
    return arm, kwargs, original


def analytic_record(treatment, *, excess=.01, delta=1.):
    arm, kwargs, original = kwargs_for(treatment)
    kwargs["delta"] = delta
    build = build_opf_multistep(**kwargs)
    assigned = e3_prepared.starts._solution_values(original)
    e3_prepared.starts._assign_start(build, assigned)
    start = m.ac_start(build, assigned)
    _, solver_build, bindings, reduction, data, inverse, mapping = m.ac_graph(build, assigned)
    full = data["x0"].copy()
    full[mapping.fixed] = mapping.values
    for owner, auxiliary in reduction.cycling_auxiliaries:
        offset = inverse[-1].var_offsets[auxiliary.id]
        actual = abs(owner.absolute.args[0].value) if bindings else abs(owner[1].value)
        weights = np.ones(actual.shape) if bindings else delta*np.broadcast_to(owner[0].weights.value, actual.shape)
        full[offset:offset+auxiliary.size] = (actual+excess/auxiliary.size/weights).ravel(order="F")
    cost, _ = a.canonical_objective(data, inverse, full)
    native = dict(status=0, obj_val=cost, x=full[mapping.free].tolist())
    preparation = None
    if treatment == "prepared":
        preparation = dict(native=native, assigned_x0=start["complete_x0"],
            adjusted_x0=start["adjusted_x0"], reduced_x0=start["reduced_x0"], coordinates=start["coordinates"],
            start_layout=[(v.id, list(v.shape), inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
                          for v in data["problem"].variables()],
            variable_scale=np.ones(mapping.free.size).tolist(), row_scale=np.ones(mapping.kept.size).tolist(),
            checks=dict(objective_assembly="component_first", cost_coordinate_maps=[
                dict(kind=b.term.kind, scale=b.scale.tolist(), physical_start=b.physical_start.tolist()) for b in bindings]))
    record = dict(start=start, native=native, preparation=preparation,
                  handoff=dict(complete_x0=start["complete_x0"], layout=start["layout"]), exception=None)
    restored, slack, error = a.restore_ac(build_opf_multistep(**kwargs), record)
    fresh = build_opf_multistep(**kwargs)
    a.restore_ac(fresh, record)
    record.update(result=restored, named_costs={k: float(v.value) for k, v in fresh.expressions.items() if k.endswith("_cost")})
    return kwargs, record, slack, error


def test_matrix_matches_design_and_alternates_pair_order():
    rows = m.arms()
    assert len(rows) == 16 and [a["id"] for a in rows] == list(range(1, 17))
    assert {(a["start"], a["stop"]) for a in rows} == {(1165,1168), (1165,1189), (2439,2442), (2439,2463)}
    for pair in range(1, 9):
        members = [a for a in rows if a["pair"] == pair]
        assert [a["treatment"] for a in members] == (["baseline", "prepared"] if pair % 2 else ["prepared", "baseline"])
        assert all(m.policy(a).objective_assembly == ("component_first" if a["formulation"] == "ac"
            and a["treatment"] == "prepared" else "hourly") for a in members)


@pytest.mark.parametrize("treatment", ["baseline", "prepared"])
def test_narrow_admitted_slice_round_trips(treatment):
    arm, kwargs, _ = kwargs_for(treatment)
    item = m.row(arm, kwargs)
    assert m.row(arm, m.thaw(item)) == item
    changed = deepcopy(item)
    changed["physical_inputs"]["storage"][0]["initial_soc"] = 4.
    with pytest.raises(ValueError, match="round-trip"):
        m.thaw(changed)


@pytest.mark.parametrize("delta", [1., .5])
def test_symmetric_ac_cycling_and_unexplained_accounting(delta):
    economics = []
    for treatment in ("baseline", "prepared"):
        kwargs, record, slack, error = analytic_record(treatment, delta=delta)
        assert error <= 1e-7
        np.testing.assert_allclose(slack.sum(), .01, atol=1e-12)
        checked = a.assess(kwargs, record)
        assert checked["passed"], checked
        assert checked["economics"]["cycling_warning"]
        economics.append(checked["economics"])
        corrupted = deepcopy(record)
        corrupted["native"]["obj_val"] += 1.
        if corrupted["preparation"]:
            corrupted["preparation"]["native"] = corrupted["native"]
        assert not a.assess(kwargs, corrupted)["passed"]
        corrupted = deepcopy(record)
        corrupted["start"]["complete_x0"][0] += 1
        assert not a.assess(kwargs, corrupted)["passed"]
    for key in ("physical_objective", "cycling_epigraph_excess", "cycling_slack_l1", "unexplained_difference"):
        assert economics[0][key] == pytest.approx(economics[1][key], abs=1e-9)


def test_baseline_capture_goes_through_public_solve_and_stock_adapter(tmp_path, monkeypatch):
    arm, kwargs, _ = kwargs_for()
    build = build_opf_multistep(**kwargs)
    assigned = m.physical_start(build, kwargs)
    start = m.ac_start(build, assigned)
    called = []
    def fake_native(self, data, *args, **kwargs):
        called.append(True)
        return dict(status=0, x=data["x0"].copy(), obj_val=1.)
    monkeypatch.setattr(m.IPOPT, "solve_via_data", fake_native)
    capture = r.CapturingIPOPT(start)
    r.baseline_public_solve(build, capture, dict(solver="IPOPT", verbose=False, warm_start=False))
    assert len(called) == 1 and capture.native["status"] == 0
    assert capture.handoff == {k: start[k] for k in ("complete_x0", "layout")}
    assert capture.elapsed >= 0


def synthetic_binding(count=3, allowance=0):
    rows = []
    for arm in m.arms()[:count]:
        rows.append(dict(arm=arm, marker="synthetic lifecycle only"))
    return dict(schema=1, context={"synthetic": True}, rows=rows,
                limits=dict(m.LIMITS, max_launches=count+allowance, poll_seconds=.01),
                replacement_allowance=allowance)


def setup(root, count=3, allowance=0):
    root.mkdir(parents=True, exist_ok=True)
    binding = synthetic_binding(count, allowance)
    r.atomic_immutable_json(root / "binding.json", binding)
    r.atomic_immutable_json(root / "protocol.json", dict(protocol=binding["limits"]))
    r.atomic_immutable_json(root / "authority.json", dict(execution_commit="a"*40,
        binding=reference(root / "binding.json", root), serial_workers=1,
        limits=binding["limits"], execution_authorized=True))
    return binding


def append(root, arm_id, number, *, outcome="exited", completed=True, accepted=True):
    directory = root / "attempts" / f"attempt-{number:03d}"
    request = dict(arm_id=arm_id, wall_seconds=10., binding=reference(root / "binding.json", root),
                   protocol=reference(root / "protocol.json", root))
    r.atomic_immutable_json(directory / "request.json", request)
    if outcome is not None:
        r.atomic_immutable_json(directory / "supervision.json", dict(classification=outcome,
            returncode=0, wall_seconds=2., peak_sampled_rss_mib=5.))
    if completed:
        binding = read(root / "binding.json")
        record = dict(iteration=arm_id, request=request, row_sha256=m.input_digest(binding["rows"][arm_id-1]),
            classification="accepted" if accepted else "native_rejected", native_call_seconds=.5,
            audit=dict(passed=accepted, economics=dict(physical_objective=10.)))
        r.atomic_gzip_json(directory / "result.json.gz", record)
        r.atomic_immutable_json(directory / "timing.json", dict(phases=dict(public_solve=1.)))
        r.atomic_immutable_json(directory / "completion.json", dict(utc=r.utc(), classification=record["classification"],
            artifacts={n: m.digest(directory / n) for n in ("request.json", "result.json.gz", "timing.json")}))
    return directory


def fake_run(monkeypatch, tmp_path, outcomes):
    monkeypatch.setattr(m, "HERE", tmp_path)
    monkeypatch.setattr(m, "context", lambda: dict(synthetic=True))
    monkeypatch.setattr(m, "execution_revision", lambda current, commit: commit)
    monkeypatch.setattr(r, "verify_sources", lambda binding: None)
    monkeypatch.setattr(r, "monitoring", lambda: dict(thermal={"unavailable": "missing", "advisory": True}))
    def supervisor(command, directory, root, request):
        # Exercise the real run, publication, status and resume paths, not a
        # replacement coordinator. Only the scientific worker is simulated.
        outcome = outcomes.pop(0)
        directory_request = read(directory / "request.json")
        arm_id = directory_request["arm_id"]
        binding = read(root / "binding.json")
        if outcome == "operator_stop":
            r.atomic_immutable_json(root / "STOP", dict(reason="rehearsal"))
        sup = dict(classification="interrupted" if outcome == "operator_stop" else outcome,
                   returncode=0, wall_seconds=2., peak_sampled_rss_mib=1.)
        r.atomic_immutable_json(directory / "supervision.json", sup)
        if outcome == "exited":
            rec = dict(iteration=arm_id, request=request, row_sha256=m.input_digest(binding["rows"][arm_id-1]),
                classification="accepted", native_call_seconds=.5,
                audit=dict(passed=True, economics=dict(physical_objective=10.)))
            r.atomic_gzip_json(directory / "result.json.gz", rec)
            r.atomic_immutable_json(directory / "timing.json", dict(phases=dict(public_solve=1.)))
            r.atomic_immutable_json(directory / "completion.json", dict(utc=r.utc(), classification="accepted",
                artifacts={n: m.digest(directory / n) for n in ("request.json", "result.json.gz", "timing.json")}))
        return sup
    return supervisor


def test_stop_resume_twice_reuses_publications_and_charges_effort(tmp_path, monkeypatch):
    outcomes = ["exited", "operator_stop", "operator_stop", "exited"]
    supervisor = fake_run(monkeypatch, tmp_path, outcomes)
    root = tmp_path / "results/matched_comparison_rehearsal"
    def factory(allowance):
        return synthetic_binding(4)
    first = r.run(root, "a"*40, binding_factory=factory, supervisor=supervisor)
    assert first["outcome"] == "operator_stop" and first["progress"]["accepted_arms"] == 1
    original = m.digest(root / "attempts/attempt-001/result.json.gz")
    second = r.run(root, "a"*40, resume=True, acknowledge_stop=True, supervisor=supervisor)
    assert second["outcome"] == "operator_stop" and second["progress"]["worker_seconds"] == 6.
    final = r.run(root, "a"*40, resume=True, acknowledge_stop=True, supervisor=supervisor)
    assert final["progress"]["matrix_complete"] and final["progress"]["accepted_arms"] == 2
    assert final["progress"]["disposed_arms"] == 4 and final["progress"]["worker_seconds"] == 8.
    assert m.digest(root / "attempts/attempt-001/result.json.gz") == original
    assert r.run(root, "a"*40, resume=True, supervisor=supervisor)["outcome"] == "matrix_complete"


def test_publication_before_checkpoint_is_reconciled_once(tmp_path):
    setup(tmp_path, 2)
    append(tmp_path, 1, 1)
    assert not (tmp_path / "progress.json").exists()
    for _ in range(2):
        result = r.status(tmp_path)
        assert result["accepted_arms"] == 1 and result["next_arm"] == 2
    append(tmp_path, 2, 2, outcome=None)
    assert r.status(tmp_path)["accepted_arms"] == 1
    r.reconcile(tmp_path, read(tmp_path / "binding.json"))
    assert r.status(tmp_path)["worker_seconds"] == 12.
    assert r.status(tmp_path)["pending_disposition"]
    assert not r.status(tmp_path)["matrix_complete"]


def test_interrupted_request_publication_has_a_same_policy_resume_path(tmp_path):
    binding = setup(tmp_path, 2)
    directory = tmp_path / "attempts/attempt-001"
    directory.mkdir(parents=True)
    assert r.status(tmp_path)["attempts"][0]["classification"] == "awaiting_request"
    r.reconcile(tmp_path, binding)
    assert r.status(tmp_path)["pending_disposition"] and r.status(tmp_path)["worker_seconds"] == 0.
    r.atomic_immutable_json(directory / "disposition.json", dict(action="dispose", reason="explicit resume"))
    assert r.status(tmp_path)["next_arm"] == 2


def test_manifest_corruption_missing_completion_and_order_fail(tmp_path):
    setup(tmp_path, 2)
    directory = append(tmp_path, 1, 1)
    (directory / "timing.json").write_text("{}")
    with pytest.raises(ValueError, match="artifact changed"):
        r.status(tmp_path)


def test_numerical_timeout_disposes_only_that_arm(tmp_path):
    setup(tmp_path, 2)
    append(tmp_path, 1, 1, outcome="wall_limit", completed=False)
    assert r.status(tmp_path)["next_arm"] == 2
    assert r.status(tmp_path)["accepted_arms"] == 0


def test_no_execution_permission_and_historical_output_paths_fail(tmp_path):
    for path in (m.HERE / "results/qualification_001", m.HERE / "results",
                 m.HERE / "results/tracy_three_step_001", tmp_path):
        with pytest.raises(ValueError):
            r.validate_output(path)
    completed = subprocess.run([sys.executable, "-m", r.MODULE, "--resume"],
                               capture_output=True, text=True)
    assert completed.returncode != 0 and "required" in completed.stderr


def test_optional_telemetry_absent_and_slow_is_advisory(tmp_path, monkeypatch):
    monkeypatch.setattr(r, "_child_rss_mib", lambda pid: 1.)
    for error in (FileNotFoundError("missing"), subprocess.TimeoutExpired("pmset", 1)):
        def fail(*args, **kwargs):
            assert kwargs["timeout"] == 1.
            raise error
        monkeypatch.setattr(r.subprocess, "check_output", fail)
        value = r.monitoring()
        assert value["thermal"]["advisory"] and value["battery"]["advisory"]
    monkeypatch.setattr(r, "_child_rss_mib", lambda pid: None)
    with pytest.raises(RuntimeError, match="RSS"):
        r.monitoring()


def test_real_supervisor_required_monitoring_and_worker_loss(tmp_path):
    setup(tmp_path, 1)
    directory = tmp_path / "attempts/attempt-001"
    request = dict(arm_id=1, wall_seconds=2., binding=reference(tmp_path / "binding.json", tmp_path),
                   protocol=reference(tmp_path / "protocol.json", tmp_path))
    r.atomic_immutable_json(directory / "request.json", request)
    sup = r.supervise([sys.executable, "-c", "import time; time.sleep(2)"],
                      directory, tmp_path, request, rss_reader=lambda pid: None)
    assert sup["classification"] == "supervisor_failure" and sup["returncode"] is not None
    assert r.status(tmp_path)["pending_disposition"]


def test_source_scope_and_changed_input_fail(monkeypatch, tmp_path):
    monkeypatch.setattr(m, "ROOT", tmp_path)
    source = tmp_path / "used.py"
    source.write_text("original")
    binding = dict(context=dict(sources={"used.py": m.digest(source)}))
    (tmp_path / "unrelated.md").write_text("unrelated edit")
    r.verify_sources(binding)
    source.write_text("changed")
    with pytest.raises(ValueError, match="dependency changed"):
        r.verify_sources(binding)


def test_worker_clock_phase_sum_does_not_double_count_native_timer(tmp_path):
    clock = r.WorkerClock(tmp_path)
    clock.phase("build")
    clock.phase("prospective_preparation")
    clock.phase("public_solve")
    clock.phase("independent_audit")
    done = clock.finish()
    assert sum(done["phases"].values()) <= done["worker_body_seconds"]
    assert "native_solve" not in done["phases"]


@pytest.mark.parametrize("treatment", ["baseline", "prepared"])
def test_real_worker_publication_and_full_replay_with_analytic_native(tmp_path, monkeypatch, treatment):
    kwargs, analytic, _, _ = analytic_record(treatment, excess=0.)
    arm = dict(m.arms()[0], treatment=treatment)
    item = m.row(arm, kwargs)
    binding = dict(synthetic_binding(1), rows=[item])
    r.atomic_immutable_json(tmp_path / "binding.json", binding)
    r.atomic_immutable_json(tmp_path / "protocol.json", dict(protocol=binding["limits"]))
    directory = tmp_path / "attempts/attempt-001"
    request = dict(arm_id=1, wall_seconds=10., binding=reference(tmp_path / "binding.json", tmp_path),
                   protocol=reference(tmp_path / "protocol.json", tmp_path))
    r.atomic_immutable_json(directory / "request.json", request)
    r.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    monkeypatch.setattr(r, "verify_sources", lambda binding: None)
    monkeypatch.setattr(m, "context", lambda: dict(synthetic=True))
    def native(self, data, *args, **kwargs):
        return dict(analytic["native"], mult_g=np.zeros(len(data["cl"])),
                    mult_x_L=np.zeros(len(data["x0"])), mult_x_U=np.zeros(len(data["x0"])),
                    num_iters=0, status_msg=b"synthetic analytic return; no optimizer")
    monkeypatch.setattr(m.IPOPT, "solve_via_data", native)
    r.worker(tmp_path, directory)
    result = read(directory / "result.json.gz")
    assert result["classification"] == "accepted", result
    assert result["optimizer_calls"] == 1  # One mocked boundary, zero native optimization.
    r.atomic_immutable_json(directory / "supervision.json", dict(classification="exited", returncode=0,
        wall_seconds=2., peak_sampled_rss_mib=2.))
    assert r.status(tmp_path, audit=True)["accepted_arms"] == 1
    assert all(phase in read(directory / "timing.json")["phases"] for phase in
               ("build", "prospective_preparation", "public_solve", "extraction", "independent_audit", "archive"))


def test_failed_launch_can_resume_without_implicit_retry(tmp_path, monkeypatch):
    outcomes = ["supervisor_failure", "exited"]
    supervisor = fake_run(monkeypatch, tmp_path, outcomes)
    root = tmp_path / "results/matched_comparison_launch_failure"
    first = r.run(root, "a"*40, binding_factory=lambda allowance: synthetic_binding(2), supervisor=supervisor)
    assert first["outcome"] == "infrastructure_failure"
    assert first["progress"]["pending_disposition"]
    final = r.run(root, "a"*40, resume=True, supervisor=supervisor)
    assert final["progress"]["accepted_arms"] == 1 and final["progress"]["disposed_arms"] == 2
    assert final["progress"]["worker_seconds"] == 4.


def test_explicit_replacement_uses_frozen_allowance_and_retains_old_attempt(tmp_path, monkeypatch):
    outcomes = ["operator_stop", "exited", "exited"]
    supervisor = fake_run(monkeypatch, tmp_path, outcomes)
    root = tmp_path / "results/matched_comparison_replacement"
    r.run(root, "a"*40, replacement_allowance=1,
          binding_factory=lambda allowance: synthetic_binding(2, allowance), supervisor=supervisor)
    final = r.run(root, "a"*40, resume=True, acknowledge_stop=True, retry_interrupted=True, supervisor=supervisor)
    assert final["progress"]["accepted_arms"] == 2 and final["progress"]["reservations"] == 3
    assert read(root / "attempts/attempt-001/disposition.json")["action"] == "retry"
    assert final["progress"]["worker_seconds"] == 6.


def test_undeclared_replacement_is_refused(tmp_path, monkeypatch):
    supervisor = fake_run(monkeypatch, tmp_path, ["operator_stop"])
    root = tmp_path / "results/matched_comparison_no_replacement"
    r.run(root, "a"*40, binding_factory=lambda allowance: synthetic_binding(2), supervisor=supervisor)
    with pytest.raises(ValueError, match="replacement allowance"):
        r.run(root, "a"*40, resume=True, acknowledge_stop=True, retry_interrupted=True, supervisor=supervisor)
    assert r.status(root)["worker_seconds"] == 2.


def test_fresh_process_cli_stop_resume_twice_with_real_supervisor(tmp_path):
    # Test-only bootstrap substitutes admission and the scientific worker, not
    # the CLI, lock, supervisor, disk journal, advancement or resume paths.
    scientific_worker = r'''
import sys, time
from pathlib import Path
from experiments.numerical_preparation import run_matched as r, matched_model as m
from experiments.case118_tracy_2021.stage_d import read
root, directory = map(Path, sys.argv[1:])
request = read(directory / "request.json")
arm = request["arm_id"]
if arm in (2, 3):
    r.atomic_immutable_json(root / "STOP", dict(reason="non-solving rehearsal"))
    time.sleep(5)
else:
    row = read(root / "binding.json")["rows"][arm-1]
    record = dict(iteration=arm, request=request, row_sha256=m.input_digest(row),
        classification="accepted", native_call_seconds=.01,
        audit=dict(passed=True, economics=dict(physical_objective=10.)))
    r.atomic_gzip_json(directory / "result.json.gz", record)
    r.atomic_immutable_json(directory / "timing.json", dict(phases=dict(public_solve=.01)))
    r.atomic_immutable_json(directory / "completion.json", dict(utc=r.utc(), classification="accepted",
        artifacts={n:m.digest(directory/n) for n in ("request.json", "result.json.gz", "timing.json")}))
'''
    bootstrap = r'''
import sys, functools
from pathlib import Path
import cvxpy as cp, cyipopt, clarabel
from experiments.numerical_preparation import run_matched as r, matched_model as m
from tests.test_matched_preparation import synthetic_binding
def forbidden(*args, **kwargs):
    raise RuntimeError("rehearsal optimizer forbidden")
cp.Problem.solve = cyipopt.Problem = clarabel.DefaultSolver = forbidden
m.HERE = Path(sys.argv[1])
m.context = lambda: dict(synthetic=True)
m.execution_revision = lambda current, commit: commit
r.verify_sources = lambda binding: None
r.monitoring = lambda: dict(thermal=dict(unavailable="rehearsal", advisory=True))
code = sys.argv[2]
def supervisor(command, directory, root, request):
    return r.supervise([sys.executable, "-c", code, str(root), str(directory)], directory,
        root, request, rss_reader=lambda pid: 1.)
real_run = r.run
r.run = functools.partial(real_run, binding_factory=lambda allowance: synthetic_binding(4), supervisor=supervisor)
sys.argv = ["run_matched", "--output", str(m.HERE/"results/matched_comparison_rehearsal"),
            "--authorize-execution", "--commit", "a"*40] + sys.argv[3:]
r.main()
'''
    def invoke(*args):
        proc = subprocess.run([sys.executable, "-c", bootstrap, str(tmp_path), scientific_worker, *args],
                              text=True, capture_output=True, timeout=30)
        assert proc.returncode == 0, proc.stderr
        return json.loads(proc.stdout)
    first = invoke()
    root = tmp_path / "results/matched_comparison_rehearsal"
    original = m.digest(root / "attempts/attempt-001/result.json.gz")
    assert first["progress"]["accepted_arms"] == 1 and first["outcome"] == "operator_stop"
    second = invoke("--resume", "--acknowledge-stop")
    assert second["outcome"] == "operator_stop"
    final = invoke("--resume", "--acknowledge-stop")
    assert final["progress"]["accepted_arms"] == 2 and final["progress"]["disposed_arms"] == 4
    assert final["progress"]["worker_seconds"] > second["progress"]["worker_seconds"] > first["progress"]["worker_seconds"]
    assert m.digest(root / "attempts/attempt-001/result.json.gz") == original
    assert invoke("--resume")["outcome"] == "matrix_complete"


@pytest.mark.parametrize("kind", ["wall", "rss", "exit"])
def test_real_supervisor_ceiling_and_failed_child_dispositions(tmp_path, kind):
    setup(tmp_path, 2)
    directory = tmp_path / "attempts/attempt-001"
    request = dict(arm_id=1, wall_seconds=.05 if kind == "wall" else 2.,
        binding=reference(tmp_path / "binding.json", tmp_path), protocol=reference(tmp_path / "protocol.json", tmp_path))
    r.atomic_immutable_json(directory / "request.json", request)
    code = "raise SystemExit(3)" if kind == "exit" else "import time; time.sleep(5)"
    sup = r.supervise([sys.executable, "-c", code], directory, tmp_path, request,
                      rss_reader=lambda pid: 17000. if kind == "rss" else 1.)
    assert sup["returncode"] is not None  # Owned child always joined.
    progress = r.status(tmp_path)
    if kind == "wall":
        assert sup["classification"] == "wall_limit" and progress["next_arm"] == 2
        assert progress["worker_seconds"] >= request["wall_seconds"]
    else:
        assert progress["pending_disposition"] and progress["next_arm"] is None
        assert sup["classification"] == ("rss_limit" if kind == "rss" else "exited")
    assert progress["accepted_arms"] == 0


def test_budget_is_clipped_and_not_reset_on_resume(tmp_path, monkeypatch):
    calls = []
    supervisor = fake_run(monkeypatch, tmp_path, ["exited", "exited"])
    def observed(*args):
        calls.append(args[-1]["wall_seconds"])
        return supervisor(*args)
    binding = synthetic_binding(3)
    binding["limits"]["total_worker_seconds"] = 3.
    root = tmp_path / "results/matched_comparison_budget"
    result = r.run(root, "a"*40, binding_factory=lambda allowance: binding, supervisor=observed)
    assert calls == [3., 1.]
    assert result["outcome"] == "budget_exhausted" and result["progress"]["worker_seconds"] == 4.
    assert r.run(root, "a"*40, resume=True, supervisor=observed)["outcome"] == "budget_exhausted"
    assert calls == [3., 1.]


def test_same_root_has_only_one_supervisor(tmp_path, monkeypatch):
    supervisor = fake_run(monkeypatch, tmp_path, [])
    root = tmp_path / "results/matched_comparison_lock"
    setup(root, 2)
    with (root / "supervisor.lock").open("a") as lock:
        r.fcntl.flock(lock, r.fcntl.LOCK_EX | r.fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            r.run(root, "a"*40, resume=True, supervisor=supervisor)
    assert not list(root.glob("invocations/*"))


def test_report_table_and_figure_reuse_only_retained_evidence(tmp_path):
    pytest.importorskip("matplotlib")
    from experiments.numerical_preparation import matched_report as report
    setup(tmp_path, 2)
    append(tmp_path, 1, 1)
    append(tmp_path, 2, 2, accepted=False)
    original = m.digest(tmp_path / "attempts/attempt-001/result.json.gz")
    progress = r.status(tmp_path)
    assert not progress["pairs"][0]["eligible"]
    rendered = report.render(tmp_path, progress)
    bundle = Path(rendered["report"])
    assert Path(rendered["figure"]).stat().st_size > 1000
    assert len(read(bundle / "summary.json")["all_arm_table"]) == 2
    assert m.digest(tmp_path / "attempts/attempt-001/result.json.gz") == original


@pytest.mark.parametrize("treatment", ["baseline", "prepared"])
def test_socp_worker_publication_and_full_inverse_with_analytic_native(tmp_path, monkeypatch, treatment):
    from types import SimpleNamespace
    from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL
    arm, kwargs, original = kwargs_for(treatment)
    arm = dict(arm, formulation="socp")
    kwargs.update(formulation="socp", options=replace(kwargs["options"], numerical_preparation=m.policy(arm)))
    build = build_opf_multistep(**kwargs)
    data, _, inverse, mapping, D, R, offset = m.canonical_problem(build)
    full = np.zeros(data["c"].size)
    known = []
    for key, variable in build.variables.items():
        if key in ("w", "W_re"):
            value = np.ones(variable.shape)
        elif key == "W_im":
            value = np.zeros(variable.shape)
        else:
            value = original.variables[key].value
        begin = inverse[-2].var_offsets[variable.id]
        indices = np.arange(begin, begin+variable.size)
        full[indices] = np.asarray(value).ravel(order="F")
        known.extend(indices)
    unknown = np.setdiff1d(np.arange(full.size), known)
    equality = data["A"][:data["dims"].zero, :]
    if unknown.size:
        full[unknown] = np.linalg.lstsq(equality[:, unknown].toarray(),
            data["b"][:data["dims"].zero]-equality@full, rcond=None)[0]
    cost = float(.5*full@(data["P"]@full)+data["c"]@full)-offset
    raw = SimpleNamespace(status="Solved", x=full[mapping.free]/D,
        s=R*(data["b"]-data["A"]@full)[mapping.kept], z=np.zeros(R.size), obj_val=cost,
        obj_val_dual=cost, iterations=1, solve_time=.01, r_prim=0., r_dual=0.)
    def native(self, data, warm_start, verbose, settings, solver_cache=None):
        if solver_cache is not None:
            solver_cache["CLARABEL"] = SimpleNamespace(get_solution=lambda: raw)
        return raw
    monkeypatch.setattr(CLARABEL, "solve_via_data", native)
    binding = dict(synthetic_binding(1), rows=[m.row(arm, kwargs)])
    r.atomic_immutable_json(tmp_path / "binding.json", binding)
    r.atomic_immutable_json(tmp_path / "protocol.json", dict(protocol=binding["limits"]))
    directory = tmp_path / "attempts/attempt-001"
    request = dict(arm_id=1, wall_seconds=10., binding=reference(tmp_path / "binding.json", tmp_path),
                   protocol=reference(tmp_path / "protocol.json", tmp_path))
    r.atomic_immutable_json(directory / "request.json", request)
    r.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    monkeypatch.setattr(r, "verify_sources", lambda binding: None)
    monkeypatch.setattr(m, "context", lambda: dict(synthetic=True))
    r.worker(tmp_path, directory)
    record = read(directory / "result.json.gz")
    assert record["classification"] == "accepted", record
    r.atomic_immutable_json(directory / "supervision.json", dict(classification="exited", returncode=0,
        wall_seconds=2., peak_sampled_rss_mib=2.))
    assert r.status(tmp_path, audit=True)["accepted_arms"] == 1
