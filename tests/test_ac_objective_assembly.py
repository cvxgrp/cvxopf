"""Non-solving diagnostic checks with portable Case9 inputs, no ignored data."""

from dataclasses import replace
from types import SimpleNamespace
import os

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import extract_results
from cvxopf._numerical_preparation import PreparationEvidence, resolve_fixed_map
from experiments.ac_cost_coordinates import model as historical
from experiments.ac_cost_qualification import fixture as qualified
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.ac_objective_assembly import model as m, run as r
from experiments.ac_production_verification import fixture as production
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable


@pytest.fixture
def portable(monkeypatch):
    def kwargs(call, prepared=None):
        values = original.case9_kwargs(3, "ac")
        for name in ("df_load_p", "df_load_q", "df_nd"):
            values[name] = values[name].iloc[np.arange(call.T) % 3].reset_index(drop=True)
        values["T"] = call.T
        values["options"] = replace(values["options"], numerical_preparation=original.POLICIES[call.treatment])
        return values
    monkeypatch.setattr(original, "kwargs_for_call", kwargs)
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    monkeypatch.setattr(IPOPT, "solve_via_data", lambda *_a, **_k: pytest.fail("optimizer not authorized"))


def test_matched_two_arm_binding(portable):
    rows = [m.row_binding(arm) for arm in m.arms()]
    assert m.LIMITS["max_launches"] == len(rows) == 2
    assert m.LIMITS["total_worker_seconds"] == 2 * m.LIMITS["wall_seconds"]
    x, y = [row["fixture"] for row in rows]
    assert x["physical_start"] == y["physical_start"] and x["stress"] == y["stress"]
    assert x["frozen"]["mathematical_inputs"] == y["frozen"]["mathematical_inputs"]
    assert x["frozen"]["policy"] == y["frozen"]["policy"]
    assert x["frozen"]["policy"]["cost_coordinates"]
    assert x["stress"]["forced_shedding"]
    assert rows[0]["layout"] != rows[1]["layout"]


def test_control_is_normal_production_canonicalization(portable):
    _, _, control, _, _ = m.construct(m.arms()[0])
    _, _, ordinary, _, _ = production.construct(production.arms()[-1])
    x, y = m.canonical(control), m.canonical(ordinary)
    assert m.layout(*x) == m.layout(*y)
    assert np.array_equal(x[-2]["x0"], y[-2]["x0"])
    assert type(control.prob.objective) is cp.Minimize


def test_component_first_reproduces_historical_cost_graph_and_layout(portable):
    _, _, candidate, _, _ = m.construct(m.arms()[1])
    solver, bindings, auxiliary, data, inverse = m.canonical(candidate)
    _, _, view, _, _, _ = qualified.construct(qualified.arms()[-1])
    hdata, hinverse = historical.canonical_data(view.solver)
    hb = [SimpleNamespace(leaf=view.leaves[name], term=SimpleNamespace(kind=kind))
          for name, kind in (("b", "cycling"), ("load_shed_fraction", "shedding"))]
    original_ids = {v.id for v in view.solver.prob.variables()}
    haux = next(v for v in hdata["problem"].variables()
                if v.id not in original_ids and v.shape == view.leaves["b"].shape)
    assert m.layout(solver, bindings, auxiliary, data, inverse) == m.layout(view.solver, hb, haux, hdata, hinverse)
    assert np.array_equal(data["x0"], hdata["x0"])
    for key in ("lb", "ub", "cl", "cu"):
        assert np.array_equal(data[key], hdata[key])
    # A nonzero synthetic point tests algebra, not feasibility. Equivalent
    # canonical layouts let us assign the same complete vector directly.
    x = np.arange(len(data["x0"]), dtype=float) * .000123 + .1
    actual, _ = canonical_objective(data, inverse, x)
    expected, _ = canonical_objective(hdata, hinverse, x)
    assert actual == expected
    for c, d in zip(data["problem"].constraints, hdata["problem"].constraints, strict=True):
        assert np.array_equal(c.expr.value, d.expr.value)
    assert type(solver.prob.objective) is cp.Minimize


def test_adapter_rejects_unsupported_costs_and_nonunit_weights(portable):
    _, _, build, _, _ = m.construct(m.arms()[0])
    with pytest.raises(ValueError, match="frozen positive-cost"):
        m.ComponentFirstObjective(replace(build, _cost_coordinate_delta=.5))
    objective = m.ComponentFirstObjective(build)
    substitutions = {}
    for term in build._cost_coordinate_terms:
        leaf = cp.Variable(term.variable.shape)
        substitutions[id(term.rate)] = cp.sum(cp.multiply(np.full(leaf.shape, 2.), leaf), axis=0)
    with pytest.raises(ValueError, match="unit cost-valued"):
        objective.tree_copy(substitutions)
    with pytest.raises(ValueError, match="incomplete production"):
        objective.tree_copy({next(iter(substitutions)): next(iter(substitutions.values()))})
    assert type(objective.tree_copy()) is cp.Minimize


def setup_status(monkeypatch, root, number=1):
    q.atomic_immutable_json(root / "protocol.json", r.protocol())
    row = dict(layout=[], canonical_x0=[])
    binding = dict(rows=[row, row])
    monkeypatch.setattr(r, "verify_binding", lambda _: binding)
    monkeypatch.setattr(r, "checked_arm", lambda _, n: m.arms()[n-1])
    directory = root / f"call-{number:03d}"
    directory.mkdir()
    req = r.request(root, number, 180.)
    q.atomic_immutable_json(directory / "request.json", req)
    return directory, req


@pytest.mark.parametrize("number", [1, 2])
def test_worker_calls_public_solve_and_keeps_failure(monkeypatch, tmp_path, portable, number):
    directory, req = setup_status(monkeypatch, tmp_path, number)
    q.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    _, _, build, _, _ = m.construct(m.arms()[number-1])
    calls = []
    def solve(self, **options):
        calls.append(options)
        raise RuntimeError("synthetic public failure")
    monkeypatch.setattr(type(build), "solve", solve)
    r.worker(tmp_path, number)
    record = q.read(directory / "result.json.gz")
    assert len(calls) == record["optimizer_calls"] == 1
    assert "synthetic public failure" in record["exception"]
    assert record["request"] == req
    assert q.read(directory / "completion.json")["artifacts"]["canonical-start.json.gz"] == q.digest(directory / "canonical-start.json.gz")


@pytest.mark.parametrize("number", [1, 2])
def test_audit_restores_this_assembly_and_rejects_changed_start(monkeypatch, portable, number):
    arm = m.arms()[number-1]
    _, _, build, _, _ = m.construct(arm)
    sb, bindings, _, data, inverse = m.canonical(build)
    mapping = resolve_fixed_map(sb._exact_boxes, inverse[:-1], inverse[-1],
        data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    adjusted = np.asarray(data["x0"]).copy()
    adjusted[mapping.fixed] = mapping.values
    value, _ = canonical_objective(data, inverse, adjusted)
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value / binding.scale)
    build.prob._status, build.prob._value = cp.OPTIMAL, float(sb.prob.objective.value)
    published = serializable(extract_results(build))
    native = dict(status=0, obj_val=value, x=adjusted[mapping.free])
    evidence = evidence_record(PreparationEvidence(mapping, native, dict(restoration_available=True),
        np.ones(mapping.free.size), np.ones(mapping.kept.size), assigned_x0=data["x0"],
        adjusted_x0=adjusted, reduced_x0=adjusted[mapping.free], start_layout=tuple(
            (v.id, v.shape, inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
            for v in data["problem"].variables())))
    # A synthetic start is not asserted physically feasible or shedding. Mock
    # the independent physical oracle only to exercise restoration/accounting.
    monkeypatch.setattr(m, "physical_audit", lambda *args: (dict(passed=True,
        costs={k: float(v.value) for k, v in args[0].expressions.items() if k.endswith("_cost")}), None))
    check = m.assess(arm, serializable(native), evidence, published)
    assert check["publication"]["objective_difference"] <= check["publication"]["objective_limit"]
    assert check["economics"]["passed"] and check["transformation"]["passed"]
    evidence["assigned_x0"][0] += 1
    with pytest.raises(ValueError, match="layout/start"):
        m.assess(arm, serializable(native), evidence, published)


def test_unsupervised_archive_is_not_finalized(monkeypatch, tmp_path):
    directory, _ = setup_status(monkeypatch, tmp_path)
    q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    report = r.status(tmp_path)
    assert report["result_archives"] == 1
    assert report["accepted"] == report["finalized"] == 0


def test_manifest_mismatch_is_not_accepted(monkeypatch, tmp_path):
    directory, _ = setup_status(monkeypatch, tmp_path)
    q.atomic_immutable_json(directory / "supervision.json", dict(classification="exited", returncode=0,
        wall_seconds=1., peak_sampled_rss_mib=1.))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    q.atomic_immutable_json(directory / "completion.json", dict(artifacts={}))
    with pytest.raises(ValueError, match="manifest/archive mismatch"):
        r.status(tmp_path)


def test_dirty_source_gate_precedes_output_creation(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=False, commit="a"*40)))
    with pytest.raises(ValueError, match="clean full commit"):
        r.run(tmp_path / "absent", "a"*40)
    assert not (tmp_path / "absent").exists()


def test_finalized_timeout_does_not_censor_second_arm(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=True, commit="a"*40,
        thread_environment={key:"1" for key in r.THREAD_KEYS})))
    monkeypatch.setattr(r, "monitoring", lambda: {})
    state = dict(launches=0, finalized=0, remaining_worker_seconds=360., attempts=[])
    monkeypatch.setattr(r, "status", lambda _: state)
    launches = []
    def supervise(command, directory, root, req):
        launches.append(req["call_id"])
        state.update(launches=len(launches), finalized=len(launches),
                     remaining_worker_seconds=360.-180.*len(launches))
        return dict(classification="wall_limit", returncode=-15)
    monkeypatch.setattr(q, "supervise", supervise)
    assert r.run(tmp_path / "fresh", "a"*40)["outcome"] == "matrix_complete"
    assert launches == [1, 2]


@pytest.mark.parametrize("classification", ["rss_limit", "interrupted", "supervisor_failure"])
def test_safety_stop_does_not_launch_second_arm(monkeypatch, tmp_path, classification):
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=True, commit="a"*40,
        thread_environment={key:"1" for key in r.THREAD_KEYS})))
    monkeypatch.setattr(r, "monitoring", lambda: {})
    monkeypatch.setattr(r, "status", lambda _: dict(launches=0, finalized=0,
        remaining_worker_seconds=360., attempts=[]))
    launches = []
    def supervise(command, directory, root, req):
        launches.append(req["call_id"])
        return dict(classification=classification, returncode=-15)
    monkeypatch.setattr(q, "supervise", supervise)
    assert r.run(tmp_path / "fresh", "a"*40)["outcome"] == classification
    assert launches == [1]


def test_both_assemblies_worker_and_status_round_trip(monkeypatch, tmp_path, portable):
    directory, _ = setup_status(monkeypatch, tmp_path)
    rows = [m.row_binding(arm) for arm in m.arms()]
    monkeypatch.setattr(r, "verify_binding", lambda _: dict(rows=rows))

    def solve(build, **options):
        sb, bindings, auxiliary, data, inverse = m.canonical(build)
        mapping = resolve_fixed_map(sb._exact_boxes, inverse[:-1], inverse[-1],
            data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
        adjusted = np.asarray(data["x0"]).copy()
        adjusted[mapping.fixed] = mapping.values
        full = adjusted.copy()
        for binding in bindings:
            offset = inverse[-1].var_offsets[binding.leaf.id]
            physical = np.full(binding.leaf.shape, .9 if binding.term.kind == "shedding" else .123456789)
            full[offset:offset+binding.leaf.size] = (physical*binding.scale).ravel(order="F")
            if binding.term.kind == "cycling":
                aux_offset = inverse[-1].var_offsets[auxiliary.id]
                full[aux_offset:aux_offset+auxiliary.size] = abs(physical*binding.scale).ravel(order="F")
        value, _ = canonical_objective(data, inverse, full)
        for variable in sb.prob.variables():
            offset = inverse[-1].var_offsets[variable.id]
            variable.save_value(full[offset:offset+variable.size].reshape(variable.shape, order="F"))
        for binding in bindings:
            binding.term.variable.save_value(binding.leaf.value / binding.scale)
        build.prob._status, build.prob._value = cp.OPTIMAL, float(sb.prob.objective.value)
        build._preparation_evidence = PreparationEvidence(mapping,
            dict(status=0, obj_val=value, x=full[mapping.free]), dict(restoration_available=True),
            np.ones(mapping.free.size), np.ones(mapping.kept.size), assigned_x0=data["x0"],
            adjusted_x0=adjusted, reduced_x0=adjusted[mapping.free], start_layout=tuple(
                (v.id, v.shape, inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
                for v in data["problem"].variables()))

    _, _, build, _, _ = m.construct(m.arms()[0])
    monkeypatch.setattr(type(build), "solve", solve)
    # Synthetic primals exercise accounting/restoration only, not AC feasibility.
    monkeypatch.setattr(m, "physical_audit", lambda build, result, kwargs, named:
                        (dict(passed=True, costs=named), None))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    for number in (1, 2):
        directory = tmp_path / f"call-{number:03d}"
        if number == 2:
            directory.mkdir()
            q.atomic_immutable_json(directory / "request.json", r.request(tmp_path, number, 180.))
        q.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
        r.worker(tmp_path, number)
        record = q.read(directory / "result.json.gz")
        assert record["classification"] == "accepted", record.get("exception")
        q.atomic_immutable_json(directory / "supervision.json", dict(classification="exited",
            returncode=0, wall_seconds=1., peak_sampled_rss_mib=1.))
    report = r.status(tmp_path)
    assert report["accepted"] == report["finalized"] == report["result_archives"] == 2
    assert report["native_archives"] == report["completion_manifests"] == 2
    assert abs(report["pairs"][0]["component_first_minus_hourly_cost"]) < 1e-6
    start = q.read(directory / "canonical-start.json.gz")
    start["complete_x0"][0] += 1
    # This is an owned synthetic pytest artifact, deliberately corrupted to
    # verify semantic replay even after recomputing its manifest hash.
    (directory / "canonical-start.json.gz").unlink()
    q.atomic_gzip_json(directory / "canonical-start.json.gz", start)
    completion = q.read(directory / "completion.json")
    completion["artifacts"]["canonical-start.json.gz"] = q.digest(directory / "canonical-start.json.gz")
    q.atomic_json(directory / "completion.json", completion)
    with pytest.raises(ValueError, match="expected canonical start"):
        r.status(tmp_path)
