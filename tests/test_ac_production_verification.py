"""Non-solving public-path checkpoint tests; no ignored Tracy data required."""

from dataclasses import replace
import os

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import extract_results
from cvxopf._numerical_preparation import PreparationEvidence, resolve_fixed_map
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.ac_production_verification import fixture as f, audit as a, run as r
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable


@pytest.fixture
def portable(monkeypatch):
    def kwargs(call, prepared=None):
        values = original.case9_kwargs(3, "ac")
        for name in ("df_load_p", "df_load_q", "df_nd"):
            values[name] = values[name].iloc[np.arange(call.T) % 3].reset_index(drop=True)
        values["T"] = call.T
        return values
    monkeypatch.setattr(original, "kwargs_for_call", kwargs)
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    monkeypatch.setattr(IPOPT, "solve_via_data", lambda *_args, **_kw: pytest.fail("unexpected optimizer call"))


def test_small_matched_matrix(portable):
    arms = f.arms()
    assert len(arms) == f.LIMITS["max_launches"] == 8
    assert f.LIMITS["total_worker_seconds"] == 8 * f.LIMITS["wall_seconds"]
    for first, second in zip(arms[::2], arms[1::2], strict=True):
        x, y = f.row_binding(first), f.row_binding(second)
        assert x["group"] == y["group"] and x["physical_start"] == y["physical_start"]
        assert x["stress"] == y["stress"]
        assert x["frozen"]["mathematical_input_sha256"] == y["frozen"]["mathematical_input_sha256"]
        assert not x["frozen"]["policy"]["cost_coordinates"]
        assert y["frozen"]["policy"]["cost_coordinates"]
        assert x["stress"]["forced_shedding"] == first.forced_shedding
        if first.forced_shedding:
            assert min(x["stress"]["modified"]["minimum_shed_mw"]) > 0


@pytest.mark.parametrize("mode", ["original", "both"])
def test_canonical_replay_restores_public_variables_without_solve(portable, monkeypatch, mode):
    arm = replace(f.arms()[0], mode=mode)
    call, kwargs, build, _, _ = f.construct(arm)
    solver_build, bindings, auxiliary, data, inverse = a.canonical(build)
    mapping = resolve_fixed_map(solver_build._exact_boxes, inverse[:-1], inverse[-1],
                                data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    full = np.asarray(data["x0"]).copy()
    full[mapping.fixed] = mapping.values
    value, _ = canonical_objective(data, inverse, full)
    for var in solver_build.prob.variables():
        offset = inverse[-1].var_offsets[var.id]
        var.save_value(full[offset:offset+var.size].reshape(var.shape, order="F"))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value / binding.scale)
    build.prob._status, build.prob._value = cp.OPTIMAL, float(build.prob.objective.value)
    result = serializable(extract_results(build))
    native = dict(status=0, obj_val=value, x=full[mapping.free])
    evidence = evidence_record(PreparationEvidence(
        coordinates=mapping, native=native, checks=dict(restoration_available=True),
        variable_scale=np.ones(mapping.free.size), row_scale=np.ones(mapping.kept.size),
        assigned_x0=data["x0"], adjusted_x0=full, reduced_x0=full[mapping.free],
        start_layout=tuple((var.id, var.shape, inverse[-1].var_offsets[var.id],
                            inverse[-1].var_offsets[var.id]+var.size) for var in data["problem"].variables())))
    # This synthetic initial point is not claimed feasible. Isolate restoration
    # and economic checks from network physics in this algebraic unit test.
    costs = {k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}
    monkeypatch.setattr(a, "physical_audit", lambda *_: (dict(passed=True, costs=costs), None))
    check = a.assess(arm, serializable(native), evidence, result)
    assert check["transformation"]["passed"]
    assert check["economics"]["reconstruction_error"] < 1e-8
    assert check["passed"]
    bad = dict(result, objective=result["objective"]+1)
    with pytest.raises(ValueError, match="public results differ"):
        a.assess(arm, serializable(native), evidence, bad)
    evidence["assigned_x0"][0] += 1
    with pytest.raises(ValueError, match="verified production x0"):
        a.assess(arm, serializable(native), evidence, result)


@pytest.mark.parametrize("slack", [np.array([.02, 0.]), np.array([.02, -.02])])
def test_cycling_slack_warns_without_rejection(slack):
    costs = dict(generator_cost=2., storage_cost=.01, load_shedding_cost=3.)
    native = dict(obj_val=sum(costs.values())+float(slack.sum()))
    check = a.economic_checks(native, costs, slack, 0., 3.)
    assert check["cycling_warning"] and check["passed"]
    native["obj_val"] += .1
    assert not a.economic_checks(native, costs, slack, 0., 3.)["passed"]


def test_large_shedding_cost_does_not_mask_generator_or_canonical_error():
    costs = dict(generator_cost=2., storage_cost=.01, load_shedding_cost=1e6)
    native = dict(obj_val=sum(costs.values())+.1)
    check = a.economic_checks(native, costs, np.zeros(1), 0., 1e6)
    assert check["unexplained"]["passed"] and not check["passed"]
    native["obj_val"] -= .1
    assert not a.economic_checks(native, costs, np.zeros(1), .001, 1e6)["passed"]


def test_objective_roundoff_does_not_relax_trajectory_restoration():
    physical = dict(objective=3696167.141024389, b=[[.5]])
    published = dict(physical, objective=3696167.1410243884)
    assert a.publication_check(physical, published)["objective_difference"] > 0
    with pytest.raises(ValueError, match="public results differ"):
        a.publication_check(physical, dict(published, b=[[.5000000001]]))
    with pytest.raises(ValueError, match="public results differ"):
        a.publication_check(physical, dict(published, objective=physical["objective"]+.001))


def setup_status(monkeypatch, root):
    q.atomic_immutable_json(root / "protocol.json", r.protocol())
    binding = dict(rows=[])
    monkeypatch.setattr(r, "verify_binding", lambda _: binding)
    monkeypatch.setattr(r, "checked_arm", lambda *_: f.arms()[0])
    directory = root / "call-001"
    directory.mkdir()
    req = r.request(root, 1, 180.)
    q.atomic_immutable_json(directory / "request.json", req)
    return directory, req


def test_archived_unsupervised_attempt_is_unfinished(monkeypatch, tmp_path):
    directory, _ = setup_status(monkeypatch, tmp_path)
    for name in ("result.json.gz", "native.json.gz"):
        q.atomic_gzip_json(directory / name, dict(iteration=1))
    q.atomic_immutable_json(directory / "completion.json", {})
    report = r.status(tmp_path)
    assert report["result_archives"] == report["native_archives"] == report["completion_manifests"] == 1
    assert report["finalized"] == report["accepted"] == 0
    assert report["attempts"][0]["classification"] == "unfinished"


def test_manifest_archive_mismatch_is_not_accepted(monkeypatch, tmp_path):
    directory, _ = setup_status(monkeypatch, tmp_path)
    q.atomic_immutable_json(directory / "supervision.json", dict(
        classification="exited", returncode=0, wall_seconds=1., peak_sampled_rss_mib=1.))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    q.atomic_immutable_json(directory / "completion.json", dict(artifacts={}))
    with pytest.raises(ValueError, match="manifest/archive mismatch"):
        r.status(tmp_path)


def test_worker_uses_public_solve_and_retains_failure(monkeypatch, tmp_path, portable):
    directory, req = setup_status(monkeypatch, tmp_path)
    q.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    _, _, build, _, _ = f.construct(f.arms()[0])
    calls = []
    def solve(self, **options):
        calls.append(options)
        raise RuntimeError("synthetic public failure")
    monkeypatch.setattr(type(build), "solve", solve)
    r.worker(tmp_path, 1)
    record = q.read(directory / "result.json.gz")
    assert len(calls) == record["optimizer_calls"] == 1
    assert record["classification"] == "exception" and "synthetic public failure" in record["exception"]
    completion = q.read(directory / "completion.json")
    assert completion["artifacts"]["request.json"] == q.digest(directory / "request.json")
    assert record["request"] == req


def test_dirty_commit_gate_precedes_execution_or_output_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=False, commit="a"*40)))
    with pytest.raises(ValueError, match="clean full commit"):
        r.run(tmp_path / "absent", "a"*40)
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("mode", ["original", "both"])
def test_public_worker_archive_and_status_round_trip(portable, monkeypatch, tmp_path, mode):
    arm = replace(f.arms()[0], mode=mode)
    monkeypatch.setattr(f, "arms", lambda: (arm,))
    directory, _ = setup_status(monkeypatch, tmp_path)
    q.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))

    def solve(build, **options):
        solver_build, bindings, _, data, inverse = a.canonical(build)
        mapping = resolve_fixed_map(solver_build._exact_boxes, inverse[:-1], inverse[-1],
            data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
        full = np.asarray(data["x0"]).copy()
        full[mapping.fixed] = mapping.values
        adjusted_start = full.copy()
        for binding in bindings:
            # Nonzero cost-valued leaves exercise production's transformed
            # objective publication rather than our physical audit evaluation.
            offset = inverse[-1].var_offsets[binding.leaf.id]
            physical = np.arange(binding.leaf.size).reshape(binding.leaf.shape, order="F")*.000123456789 + .123456789
            full[offset:offset+binding.leaf.size] = (physical*binding.scale).ravel(order="F")
        value, _ = canonical_objective(data, inverse, full)
        for variable in solver_build.prob.variables():
            offset = inverse[-1].var_offsets[variable.id]
            variable.save_value(full[offset:offset+variable.size].reshape(variable.shape, order="F"))
        for binding in bindings:
            binding.term.variable.save_value(binding.leaf.value / binding.scale)
        build.prob._status, build.prob._value = cp.OPTIMAL, float(solver_build.prob.objective.value)
        build._preparation_evidence = PreparationEvidence(
            coordinates=mapping, native=dict(status=0, obj_val=value, x=full[mapping.free]),
            checks=dict(restoration_available=True), variable_scale=np.ones(mapping.free.size),
            row_scale=np.ones(mapping.kept.size), assigned_x0=data["x0"], adjusted_x0=adjusted_start,
            reduced_x0=adjusted_start[mapping.free], start_layout=tuple(
                (v.id, v.shape, inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
                for v in data["problem"].variables()))

    _, _, build, _, _ = f.construct(arm)
    monkeypatch.setattr(type(build), "solve", solve)
    monkeypatch.setattr(a, "physical_audit", lambda build, result, kwargs, named:
                        (dict(passed=True, costs=named), None))
    r.worker(tmp_path, 1)
    record = q.read(directory / "result.json.gz")
    assert record["classification"] == "accepted", record.get("exception")
    q.atomic_immutable_json(directory / "supervision.json", dict(
        classification="exited", returncode=0, wall_seconds=1., peak_sampled_rss_mib=1.))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    report = r.status(tmp_path)
    assert report["accepted"] == report["finalized"] == report["result_archives"] == report["native_archives"] == 1
