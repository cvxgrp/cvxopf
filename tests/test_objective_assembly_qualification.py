"""Portable, non-solving checks for the four-formulation comparison checkpoint."""

from dataclasses import replace
import os

import cvxpy as cp
import numpy as np
import pytest

from cvxopf import OPFBuild, build_opf_multistep, extract_results
from cvxopf._numerical_preparation import PreparationEvidence, resolve_fixed_map
from experiments.ac_cost_qualification.audit import canonical_objective
from experiments.ac_objective_assembly import model as prior
from experiments.convex_cost_qualification import model as convex, audit as convex_audit
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import evidence_record, serializable
from experiments.objective_assembly_qualification import model as m, run as r
from experiments.objective_assembly_qualification import analysis


@pytest.fixture
def portable(monkeypatch):
    def kwargs(call, prepared=None):
        values = original.case9_kwargs(3, call.formulation)
        for name in ("df_load_p", "df_load_q", "df_nd"):
            values[name] = values[name].iloc[np.arange(call.T) % 3].reset_index(drop=True)
        values["T"] = call.T
        values["options"] = replace(values["options"], numerical_preparation=original.POLICIES[call.treatment])
        return values
    monkeypatch.setattr(original, "kwargs_for_call", kwargs)
    from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
    from cvxpy.reductions.solvers.conic_solvers.clarabel_conif import CLARABEL
    for solver in (IPOPT, CLARABEL):
        monkeypatch.setattr(solver, "solve_via_data", lambda *_a, **_k: pytest.fail("optimizer not authorized"))


def test_declared_matrix_matches_confirmed_horizons():
    assert len(m.arms()) == m.LIMITS["max_launches"] == 32
    assert m.LIMITS["total_worker_seconds"] == 32*180
    assert {a.formulation for a in m.arms()} == set(m.FORMS)
    assert {a.T for a in m.arms()} == {3, 6, 24}
    for a, b in zip(m.arms()[::2], m.arms()[1::2], strict=True):
        assert a.group == b.group
        assert (a.assembly, b.assembly) == ("hourly", "component_first")


@pytest.mark.parametrize("form", m.FORMS)
@pytest.mark.parametrize("T", [3, 6, 24])
def test_matched_inputs_policies_algebra_and_canonical_replay(portable, form, T):
    pair = [a for a in m.arms() if a.formulation == form and a.T == T and a.forced_shedding]
    a, b = [m.row_binding(arm) for arm in pair]
    assert a["physical_start"] == b["physical_start"]
    assert a["stress"] == b["stress"] and a["stress"]["forced_shedding"]
    assert a["frozen"]["mathematical_inputs"] == b["frozen"]["mathematical_inputs"]
    assert a["frozen"]["policy"] == b["frozen"]["policy"]
    assert a["frozen"]["policy"]["cost_coordinates"] == (form == "ac")
    assert a["equivalence"]["passed"] and b["equivalence"]["passed"]
    assert a == m.row_binding(pair[0]) and b == m.row_binding(pair[1])
    _, _, control, _, _, _ = m.construct(pair[0])
    assert control.physical is control.solver and type(control.solver.prob.objective) is cp.Minimize


def test_ac_candidate_matches_prior_successful_graph(portable):
    arm = next(a for a in m.arms() if a.formulation == "ac" and a.T == 24 and a.assembly == "component_first")
    _, _, view, _, _, _ = m.construct(arm)
    actual = m.canonical(view)
    expected = prior.canonical(prior.construct(prior.arms()[1])[2])
    assert prior.layout(*actual) == prior.layout(*expected)
    for key in ("x0", "lb", "ub", "cl", "cu"):
        np.testing.assert_array_equal(actual[-2][key], expected[-2][key])
    x = np.arange(len(actual[-2]["x0"]))*0.0001+.1
    assert canonical_objective(actual[-2], actual[-1], x)[0] == canonical_objective(expected[-2], expected[-1], x)[0]


@pytest.mark.parametrize("form", m.FORMS[1:])
def test_convex_grouping_integrates_delta_once_and_preserves_constraints(portable, form):
    kwargs = original.case9_kwargs(3, form)
    kwargs["delta"] = .25
    build = build_opf_multistep(**kwargs)
    candidate = m.component_first(build)
    assert all(a is b for a, b in zip(build.prob.constraints, candidate.prob.constraints, strict=True))
    for offset in (.1, .3, 1.2):
        for v in build.prob.variables():
            v.save_value(np.full(v.shape, offset))
        np.testing.assert_allclose(candidate.prob.objective.value, build.prob.objective.value, rtol=1e-13)
    with pytest.raises(ValueError, match="unsupported"):
        m.component_first(replace(build, expressions=build.expressions | {"unknown_cost": cp.Constant(1.)}))
    with pytest.raises(ValueError, match="unsupported"):
        m.component_first(replace(build, expressions=build.expressions | {"storage_terminal_cost": cp.Constant(1.)}))


@pytest.mark.parametrize("assembly", ["hourly", "component_first"])
def test_ac_restoration_and_accounting_replay(portable, monkeypatch, assembly):
    arm = next(a for a in m.arms() if a.formulation == "ac" and a.T == 3
               and not a.forced_shedding and a.assembly == assembly)
    _, _, view, _, _, _ = m.construct(arm)
    solver, bindings, _, data, inverse = m.canonical(view)
    mapping = resolve_fixed_map(solver._exact_boxes, inverse[:-1], inverse[-1],
        data["_bounds"].problem.constraints, len(data["x0"]), len(data["cl"]))
    adjusted = np.asarray(data["x0"]).copy()
    adjusted[mapping.fixed] = mapping.values
    value, _ = canonical_objective(data, inverse, adjusted)
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value/binding.scale)
    view.solver.prob._status, view.solver.prob._value = cp.OPTIMAL, float(view.physical.prob.objective.value)
    published = serializable(extract_results(view.solver))
    native = serializable(dict(status=0, obj_val=value, x=adjusted[mapping.free]))
    evidence = evidence_record(PreparationEvidence(mapping, native, dict(restoration_available=True),
        np.ones(mapping.free.size), np.ones(mapping.kept.size), assigned_x0=data["x0"], adjusted_x0=adjusted,
        reduced_x0=adjusted[mapping.free], start_layout=tuple(
            (v.id, v.shape, inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
            for v in data["problem"].variables())))
    # Synthetic values are not a feasibility certificate. Only mock physics,
    # leaving native slicing, objective and exact publication checks live.
    monkeypatch.setattr(m, "physical_audit", lambda build, *_: (dict(passed=True,
        costs={k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}), None))
    checks = m.assess(arm, native, evidence, published)
    assert checks["passed"] and checks["economics"]["passed"] and checks["transformation"]["passed"]
    evidence["assigned_x0"][0] += 1.
    with pytest.raises(ValueError, match="layout/start"):
        m.assess(arm, native, evidence, published)


@pytest.mark.parametrize("form", m.FORMS[1:])
@pytest.mark.parametrize("assembly", ["hourly", "component_first"])
def test_convex_native_inverse_and_economics_replay(portable, monkeypatch, form, assembly):
    declared = original.kwargs_for_call
    def zero_fixed_generator(call, prepared=None):
        kwargs = declared(call, prepared)
        # Zero physical generation also makes the generated quadratic-cost
        # canonical auxiliaries zero; do not invent inconsistent native costs.
        kwargs["generators"] = [replace(g, p_min_mw=0., p_max_mw=0. if i == 1 else g.p_max_mw)
                                for i, g in enumerate(kwargs["generators"])]
        return kwargs
    monkeypatch.setattr(original, "kwargs_for_call", zero_fixed_generator)
    arm = next(a for a in m.arms() if a.formulation == form and a.T == 3
               and not a.forced_shedding and a.assembly == assembly)
    _, kwargs, view, _, _, _ = m.construct(arm)
    problem = m.canonical(view)
    full = np.zeros(problem.mapping.full_size)
    for item in problem.layout:
        if item["is_original_variable"]:
            variable = next(v for v in view.solver.prob.variables() if v.name() == item["name"])
            values = variable.project(np.zeros(variable.shape))
            if item["name"] == "soc":
                values[:, 0] = [s.initial_soc for s in kwargs["storage"]]
            full[item["start"]:item["stop"]] = values.ravel(order="F")
        elif (tuple(item["shape"]) == view.physical.variables["b"].shape
              and np.all(problem.data["c"][item["start"]:item["stop"]] > 0)):
            # Deliberately loose cycling abs auxiliary. This is a warning, not
            # a publication/acceptance failure caused by native vs physical cost.
            full[item["start"]:item["stop"]] = .01
    full[problem.mapping.fixed] = problem.mapping.values
    reduced = full[problem.mapping.free]/problem.D
    objective = float(.5*reduced@(problem.delivered["P"]@reduced)+problem.delivered["c"]@reduced)
    native = serializable(dict(status="Solved", x=reduced, s=np.zeros(len(problem.delivered["b"])),
        z=np.zeros(len(problem.delivered["b"])), obj_val=objective, obj_val_dual=objective,
        r_prim=0., r_dual=0., solve_time=0., iterations=0))
    evidence = evidence_record(PreparationEvidence(problem.mapping, native, {}, problem.D, problem.R,
                                                  objective_offset=problem.offset))
    raw, _ = convex.restored(problem, native)
    view.solver.prob.unpack_results(raw, problem.chain, problem.inverse)
    published = extract_results(view.solver)  # Actual public CVXPY inversion.
    # This is a nonfeasible algebraic point, not a solver success claim. Native
    # inversion, declared-box coordinate checks and component accounting are real.
    monkeypatch.setattr(convex_audit, "physical_audit", lambda build, *_: (dict(passed=True,
        costs={k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")}), None))
    checks = m.assess(arm, native, evidence, serializable(published))
    assert checks["coordinate_checks"]["passed"], checks["coordinate_checks"]
    assert checks["economics"]["passed"], checks["economics"]
    assert checks["passed"] and m.warning(checks)
    bad = serializable(published)
    bad["Pg"][0][0] += 1.
    with pytest.raises(ValueError, match="public results"):
        m.assess(arm, native, evidence, bad)


def setup_attempt(monkeypatch, root):
    q.atomic_immutable_json(root / "protocol.json", r.protocol())
    binding = dict(rows=[dict(canonical={}) for _ in m.arms()])
    monkeypatch.setattr(r, "verify_binding", lambda _: binding)
    directory = root / "call-001"
    directory.mkdir()
    req = r.request(root, 1, 180.)
    q.atomic_immutable_json(directory / "request.json", req)
    return directory, req


def test_protocol_request_reaches_real_supervisor_without_optimizer(monkeypatch, tmp_path):
    from experiments.case118_tracy_2021 import run_e3 as supervisor

    directory, req = setup_attempt(monkeypatch, tmp_path)
    command = ["synthetic-worker"]
    launches, sleeps, rss_pids = [], [], []

    class Process:
        pid = 12345

        def __init__(self):
            self.polls = 0

        def poll(self):
            self.polls += 1
            return None if self.polls == 1 else 0

        def wait(self):
            return 0

    def launch(actual_command, **kwargs):
        assert actual_command == command
        assert kwargs["cwd"] == original.ROOT
        assert kwargs["start_new_session"]
        assert kwargs["stdout"].name == str(directory / "worker.log")
        launches.append(actual_command)
        return Process()

    def rss(pid):
        rss_pids.append(pid)
        return 4.

    monkeypatch.setattr(supervisor.subprocess, "Popen", launch)
    monkeypatch.setattr(supervisor.time, "sleep", sleeps.append)
    result = q.supervise(command, directory, tmp_path, req, rss_reader=rss)
    assert launches == [command] and rss_pids == [Process.pid]
    assert sleeps == [m.LIMITS["poll_seconds"]]
    assert result["classification"] == "exited" and result["returncode"] == 0
    assert result["samples"] == 1 and result["peak_sampled_rss_mib"] == 4.
    assert q.read(directory / "launch.json")["pid"] == Process.pid
    assert q.read(directory / "supervision.json") == result
    assert q.resource_evidence(directory, result)
    assert not any((directory / name).exists() for name in
                   ("native.json.gz", "result.json.gz", "completion.json"))


def test_unsupervised_archive_is_unfinished(monkeypatch, tmp_path):
    directory, _ = setup_attempt(monkeypatch, tmp_path)
    q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    report = r.status(tmp_path)
    assert report["result_archives"] == 1
    assert report["finalized"] == report["accepted"] == 0


def test_manifest_mismatch_rejected(monkeypatch, tmp_path):
    directory, _ = setup_attempt(monkeypatch, tmp_path)
    q.atomic_immutable_json(directory / "supervision.json", dict(classification="exited", returncode=0,
        wall_seconds=1., peak_sampled_rss_mib=1.))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    q.atomic_immutable_json(directory / "completion.json", dict(artifacts={}))
    with pytest.raises(ValueError, match="manifest/archive"):
        r.status(tmp_path)


def test_supervised_acceptance_replays_checks_and_keeps_warning(monkeypatch, tmp_path):
    directory, req = setup_attempt(monkeypatch, tmp_path)
    q.atomic_immutable_json(directory / "supervision.json", dict(classification="exited", returncode=0,
        wall_seconds=1., peak_sampled_rss_mib=1.))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    monkeypatch.setattr(r, "checked_construction", lambda *args: (m.arms()[0], None, None, None, None, None))
    checks = dict(passed=True, common=dict(costs=dict(generator_cost=1.)), economics=dict(cycling_warning=True))
    monkeypatch.setattr(m, "assess", lambda *args: checks)
    native = dict(status=0)
    q.atomic_gzip_json(directory / "canonical.json.gz", dict(iteration=1, canonical={}))
    q.atomic_gzip_json(directory / "native.json.gz", dict(iteration=1, arm=1, native=native))
    q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=1, arm=1, request=req,
        optimizer_calls=1, exception=None, classification="accepted", native=native,
        preparation_evidence={}, published={}, checks=checks))
    q.atomic_immutable_json(directory / "completion.json", dict(artifacts={
        name: q.digest(directory / name) for name in r.ARTIFACTS}))
    report = r.status(tmp_path)
    assert report["accepted"] == report["cycling_warnings"] == 1
    assert report["result_archives"] == report["native_archives"] == report["completion_manifests"] == 1
    assert not report["pairs"][0]["both_accepted"]
    monkeypatch.setattr(m, "assess", lambda *args: checks | dict(passed=False))
    with pytest.raises(ValueError, match="independent replay"):
        r.status(tmp_path)


@pytest.mark.parametrize("number", [1, 2, 3, 4, 5, 6, 7, 8])
def test_worker_uses_public_solve_once_and_preserves_exception(portable, monkeypatch, tmp_path, number):
    q.atomic_immutable_json(tmp_path / "protocol.json", r.protocol())
    directory = tmp_path / f"call-{number:03d}"
    directory.mkdir()
    q.atomic_immutable_json(directory / "request.json", r.request(tmp_path, number, 180.))
    q.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    arm = m.arms()[number-1]
    row = m.row_binding(arm)
    binding = dict(rows=[row]*number)
    monkeypatch.setattr(r, "verify_binding", lambda _: binding)
    calls = []
    def solve(self, **options):
        calls.append(options)
        raise RuntimeError("synthetic public failure")
    monkeypatch.setattr(OPFBuild, "solve", solve)
    r.worker(tmp_path, number)
    record = q.read(directory / "result.json.gz")
    assert len(calls) == record["optimizer_calls"] == 1
    assert "synthetic public failure" in record["exception"]
    assert "canonical.json.gz" in q.read(directory / "completion.json")["artifacts"]


def test_dirty_source_gate_before_output(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=False, commit="a"*40)))
    with pytest.raises(ValueError, match="clean full commit"):
        r.run(tmp_path / "absent", "a"*40)
    assert not (tmp_path / "absent").exists()


def test_all_pairs_remain_visible_after_one_sided_stop(monkeypatch, tmp_path):
    directory, _ = setup_attempt(monkeypatch, tmp_path)
    q.atomic_immutable_json(directory / "supervision.json", dict(classification="wall_limit", returncode=-15,
        wall_seconds=180.1, peak_sampled_rss_mib=1.))
    monkeypatch.setattr(q, "resource_evidence", lambda *_: True)
    report = r.status(tmp_path)
    assert len(report["pairs"]) == 16
    assert report["pairs"][0]["outcomes"] == ["wall_limit", "not_launched"]
    assert report["per_formulation"]["ac"]["timeouts"] == 1
    assert all(not row["both_accepted"] for row in analysis.paired_summary(report))


def test_one_sided_analysis_preserves_timing_censoring_and_available_warning():
    report = dict(attempts=[dict(arm=dict(id=1), worker_seconds=180.2, classification="wall_limit", accepted=False),
        dict(arm=dict(id=2), worker_seconds=132.94, classification="exited", accepted=True,
             checks=dict(economics=dict(cycling_warning=True)))], pairs=[
        dict(arms=[1, 2], both_accepted=False, outcomes=["wall_limit", "accepted"])])
    row = analysis.paired_summary(report)[0]
    assert row["worker_seconds"] == [180.2, 132.94]
    assert row["supervision"] == ["wall_limit", "exited"] and row["accepted"] == [False, True]
    assert row["cycling_warnings"] == [None, True]
    assert "trajectories" not in row and "component_first_minus_hourly_cost" not in row


def test_analysis_records_device_differences_and_skips_partial_pairs(tmp_path):
    physical = dict(Pg=[[1., 2.], [3., 4.], [5., 6.]], b=[[1.], [0.], [-1.]],
                    soc=[[1.], [1.], [2.]], p_load_shed=[[0.], [0.], [0.]])
    changed = physical | dict(Pg=[[1., 2.], [3.1, 3.9], [5., 6.]])
    check = dict(result=physical, economics={})
    report = dict(attempts=[dict(arm=dict(id=1), checks=check, worker_seconds=1.),
        dict(arm=dict(id=2), checks=check | dict(result=changed), worker_seconds=2.)], pairs=[
        dict(arms=[1, 2], both_accepted=True, outcomes=["accepted", "accepted"]),
        dict(arms=[3, 4], both_accepted=False, outcomes=["rejected", "not_launched"])])
    summary = analysis.paired_summary(report)
    assert summary[0]["trajectories"]["Pg"]["maximum_absolute"] == pytest.approx(.1)
    assert "trajectories" not in summary[1]
    path = tmp_path / "pair.png"
    analysis.plot_pair(physical, changed, m.arms()[0], path)
    assert path.stat().st_size > 1000


def test_preflight_does_not_create_results_or_invoke_optimizer(portable, monkeypatch, tmp_path):
    import sys
    from pathlib import Path
    from experiments.case118_tracy_2021 import prepare
    from experiments.case118_annual_hierarchy import pglib_case
    raw = original.ROOT / "tests/synthetic-preflight-raw"  # Hashing is mocked; no file is created.
    monkeypatch.setattr(original.e3, "verified_inputs", lambda: {})
    monkeypatch.setattr(r, "context", lambda: dict(commit="a"*40, clean=False))
    monkeypatch.setattr(q, "digest", lambda _: "digest")
    monkeypatch.setattr(prepare, "SOURCE", raw)
    monkeypatch.setattr(pglib_case, "SOURCE_CASE_PATH", raw)
    # Restrict this CLI test to one matched pair; full matrix covered separately.
    selected = m.arms()[:2]
    monkeypatch.setattr(m, "arms", lambda: selected)
    monkeypatch.setattr(r, "HERE", tmp_path)
    monkeypatch.setattr(sys, "argv", ["run", "--preflight", "--output", str(tmp_path / "results/fresh")])
    r.main()
    assert not Path(tmp_path / "results").exists()


@pytest.mark.parametrize("outcome", ["wall_limit", "rss_limit", "interrupted", "supervisor_failure"])
def test_timeout_continues_but_safety_failure_stops(monkeypatch, tmp_path, outcome):
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=True, commit="a"*40,
        thread_environment={key: "1" for key in r.THREAD_KEYS})))
    monkeypatch.setattr(r, "monitoring", lambda: {})
    state = dict(launches=0, finalized=0, remaining_worker_seconds=5760., attempts=[])
    monkeypatch.setattr(r, "status", lambda _: state)
    launches = []
    def supervise(command, directory, root, req):
        launches.append(req["call_id"])
        state.update(launches=len(launches), finalized=len(launches),
                     remaining_worker_seconds=5760.-180.*len(launches))
        return dict(classification=outcome, returncode=-15)
    monkeypatch.setattr(q, "supervise", supervise)
    result = r.run(tmp_path / "fresh", "a"*40)
    assert launches == (list(range(1, 33)) if outcome == "wall_limit" else [1])
    assert result["outcome"] == ("matrix_complete" if outcome == "wall_limit" else outcome)
