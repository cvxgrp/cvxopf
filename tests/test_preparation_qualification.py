"""Frozen execution infrastructure tests; no qualification/numerical solves."""

from copy import deepcopy
from dataclasses import replace
import json
import os

import numpy as np
import pytest

from cvxopf._numerical_preparation import FixedCoordinateMap, PreparationEvidence
from cvxopf._hierarchical_solver import _solve_ac_with_verified_x0
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json, atomic_gzip_json
from experiments.case118_tracy_2021.stage_b import audit_result
from experiments.case118_tracy_2021.stage_d import reference
from experiments.case118_tracy_2021.prepare import digest
from experiments.numerical_preparation import fixture as f, audit as a, run_qualification as r
from tests.test_tracy_stage_d import fixture as ac_fixture
from tests.test_tracy_stage_b import fixture as dc_fixture


def test_frozen_call_order_policies_settings():
    calls = f.calls()
    assert len(calls) == 25 and [c.id for c in calls] == list(range(1, 26))
    assert [(c.start, c.stop, c.leg) for c in calls[:5]] == [
        (3308, 3332, "energy_neutral"), (1165, 1189, "energy_neutral"),
        (8580, 8604, "energy_neutral"), (2439, 2463, "energy_neutral"),
        (1165, 1189, "deficit_depletion")]
    assert [(c.id, c.formulation, c.source, c.T, c.treatment) for c in calls[5:]] == [
        (i, form, source, T, treatment) for i, (form, source, T, treatment) in enumerate([
            (form, source, T, treatment) for form in ("lossy_dc", "singlenode_dc")
            for source, T in (("case9", 1), ("case9", 3), ("tracy", 24))
            for treatment in ("baseline", "prepared_dc")] + [
            ("ac", "case9", T, treatment) for T in (1, 3)
            for treatment in ("baseline", "normalized", "combined_ac")] + [
            ("ac", "tracy", 3, treatment) for treatment in ("baseline", "combined_ac")], start=6)]
    assert f.solver_options("socp")["tol_gap_rel"] == 1e-6
    assert f.solver_options("lossy_dc")["tol_gap_rel"] == 1e-10
    assert "min_terminate_step_length" not in f.solver_options("lossy_dc")
    assert "max_iter" not in f.solver_options("ac")
    assert not f.POLICIES["prepared_dc"].normalize_device_limits
    assert not f.POLICIES["baseline"].enabled
    assert f.POLICIES["combined_ac"].canonical_scaling == "none"
    json.dumps(a.serializable(f.resolved_settings("socp")), allow_nan=False)


@pytest.mark.parametrize("T", [1, 3])
@pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc", "ac"])
def test_case9_fixed_near_fixed_and_matched_inputs(T, formulation):
    call = next(c for c in f.calls() if c.T == T and c.formulation == formulation and c.source == "case9")
    kwargs = f.kwargs_for_call(call)
    structure = f.structural_inputs(kwargs)
    assert structure["pg_fixed"] == [False, True, False]
    assert structure["nd_fixed"] == ([[True, False]] if T == 1 else [[True, False], [False, False], [True, False]])
    assert structure["expected_fixed_count"] == (2 if T == 1 else 5)
    build = f.build_for_call(call, kwargs)
    assert ("T" in build.data) == (T == 3)
    other = replace(call, treatment="combined_ac" if formulation == "ac" else "prepared_dc")
    assert f.mathematical_inputs(kwargs) == f.mathematical_inputs(f.kwargs_for_call(other))
    if formulation == "ac":
        first = r.physical_start(build, kwargs)
        second = r.physical_start(f.build_for_call(other, f.kwargs_for_call(other)), f.kwargs_for_call(other))
        assert a.serializable(first) == a.serializable(second)
        assert np.all(first["Pg"][1] == .5)
        assert np.all(first["p_nd"][0] == 0)
        if T == 3:
            assert first["soc"][0, 0] == 20


def test_pin_changes_fail_without_data_fallback(monkeypatch):
    monkeypatch.setattr(f, "digest", lambda path: "wrong")
    with pytest.raises(ValueError, match="pinned historical"):
        f.verify_pins()


def test_prospective_total_gate_does_not_relax_components():
    kwargs, _, result, named = ac_fixture(1)
    kwargs["formulation"] = "socp"
    def callback(*args):  # Separate network tests cover the numeric audit.
        pass
    result["objective"] += 2e-4
    old = audit_result(result, kwargs, named, network_audit=callback)
    new = audit_result(result, kwargs, named, network_audit=callback, total_cost_relative_tolerance=1e-6)
    assert not old["passed"] and new["passed"]
    result["storage_cost"] += 2e-4
    assert not audit_result(result, kwargs, named, network_audit=callback, total_cost_relative_tolerance=1e-6)["passed"]
    kwargs["formulation"] = "ac"
    with pytest.raises(ValueError, match="SOCP"):
        audit_result(result, kwargs, named, total_cost_relative_tolerance=1e-6)


@pytest.mark.parametrize("status,accepted", [(0, True), (1, False), (6, False), (-1, False)])
def test_native_ac_gate_and_observer_snapshot(monkeypatch, status, accepted):
    kwargs, build, result, named = ac_fixture(1)
    call = replace(f.calls()[17], T=1)
    record = dict(result=result, named_costs=named, native=dict(status=status), exception=None, preparation_evidence=None)
    assert a.audit_record(call, kwargs, build, record)["accepted"] == accepted
    original = {}
    def native(self, data, *args, **kwargs):
        original.update(status=status, x=data["x0"].copy(), obj_val=1., num_iters=0)
        return original
    monkeypatch.setattr(IPOPT, "solve_via_data", native)
    observed = []
    def observe(value):
        observed.append(value["status"])
        value["status"] = 99
        value["x"][:] = 123
    _solve_ac_with_verified_x0(build, None, solver_options=dict(verbose=False), native_observer=observe)
    assert observed == [status] and original["status"] == status
    assert not np.all(original["x"] == 123)


def test_serialized_preparation_map_and_unchanged_free_start():
    kwargs = f.kwargs_for_call(f.calls()[19])
    mapping = FixedCoordinateMap(5, 5, np.array([1, 3]), np.array([.5, 0]), np.array([0, 1]))
    assigned = np.array([1., .5, 2., 0., 3.])
    evidence = PreparationEvidence(mapping, dict(status=0), dict(restoration_available=True),
                                   np.ones(3), np.ones(3), assigned_x0=assigned, adjusted_x0=assigned,
                                   reduced_x0=assigned[mapping.free], start_layout=((42, (5,), 0, 5),))
    retained = a.evidence_record(evidence)
    captured = dict(complete_x0=assigned.tolist(), layout=[dict(shape=[5], start=0, stop=5)])
    json.dumps(retained, allow_nan=False)
    assert a.transformation_check(f.calls()[19], kwargs, retained, captured)["passed"]
    retained["reduced_x0"][0] += 1
    assert not a.transformation_check(f.calls()[19], kwargs, retained, captured)["passed"]


def test_prepared_start_must_match_capture_not_just_its_own_roundtrip():
    kwargs = f.kwargs_for_call(f.calls()[19])
    mapping = FixedCoordinateMap(5, 5, np.array([1, 3]), np.array([.5, 0]), np.array([0, 1]))
    assigned = np.array([1., .5, 2., 0., 3.])
    evidence = PreparationEvidence(mapping, dict(status=0), dict(restoration_available=True),
        np.ones(3), np.ones(3), assigned_x0=assigned, adjusted_x0=assigned,
        reduced_x0=assigned[mapping.free], start_layout=((42, (5,), 0, 5),))
    retained = a.evidence_record(evidence)
    captured = dict(complete_x0=assigned.tolist(), layout=[dict(shape=[5], start=0, stop=5)])
    assert a.transformation_check(f.calls()[19], kwargs, retained, captured)["passed"]
    # Internally consistent, but not the matched physical/canonical start.
    for name in ("assigned_x0", "adjusted_x0", "reduced_x0"):
        retained[name][0] += 1
    assert not a.transformation_check(f.calls()[19], kwargs, retained, captured)["passed"]
    retained = a.evidence_record(evidence)
    retained["start_layout"][0][1] = [1, 5]
    assert not a.transformation_check(f.calls()[19], kwargs, retained, captured)["passed"]


@pytest.mark.parametrize("solver_raises", [False, True])
@pytest.mark.parametrize("use_source_transition", [False, True])
def test_no_primal_native_failure_is_rejected_and_replayable(tmp_path, monkeypatch, solver_raises, use_source_transition):
    call = f.calls()[7]  # Multistep DC baseline; use two storage units below.
    kwargs = f.kwargs_for_call(call)
    kwargs["storage"].append(replace(kwargs["storage"][0], bus=5, device_id="battery5"))
    build = f.build_for_call(call, kwargs)
    def solve(**options):
        build.prob._status = "infeasible"
        build.prob._value = None
        if solver_raises:
            from cvxpy.error import SolverError
            raise SolverError("retained native rejection")
    monkeypatch.setattr(build, "solve", solve)
    context = dict(clean=True, commit="a"*40)
    frozen = a.serializable(f.call_binding(call, kwargs))
    binding = dict(context=context, calls=[{} for _ in f.calls()])
    binding["calls"][call.id-1] = frozen
    atomic_immutable_json(tmp_path / "binding.json", binding)
    directory = tmp_path / f"call-{call.id:03d}"
    transition = None
    if use_source_transition:
        continued = dict(context, commit="b"*40)
        transition = dict(first_call=2, context=continued)
        atomic_immutable_json(tmp_path / "replay-fix-transition.json", transition)
        monkeypatch.setattr(r, "replay_fix_transition", lambda *args: transition)
    atomic_immutable_json(tmp_path / "protocol.json", dict(protocol=f.LIMITS))
    atomic_immutable_json(directory / "request.json", r.request_for_call(tmp_path, call.id, 180., transition))
    atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    monkeypatch.setattr(r, "context", lambda: r.binding_for_call(binding, transition, call.id)["context"])
    monkeypatch.setattr(r, "kwargs_for_call", lambda *args: kwargs)
    monkeypatch.setattr(r, "build_for_call", lambda *args: build)
    monkeypatch.setattr(r, "convergence_diagnostics", lambda build: dict(native_info=dict(status="PrimalInfeasible")))
    assert r.worker(tmp_path, directory) == "rejected"
    record = r.independent_record(call, r.binding_for_call(binding, transition, call.id), directory, None)
    assert record["execution_context"]["commit"] == ("b"*40 if use_source_transition else "a"*40)
    assert r.read(tmp_path / "binding.json") == binding
    assert record["result"]["soc"] is None and record["boundary_soc_mwh"] is None
    assert not record["audit"]["accepted"] and record["native"]["status"] == "PrimalInfeasible"


def pair_records():
    kwargs, result, named = dc_fixture("singlenode_dc")
    common = audit_result(result, kwargs, named)
    record = dict(result=a.serializable(result), audit=dict(accepted=True, common=common),
                  mathematical_input_sha256="same", boundary_soc_mwh=[[5], [6], [5]])
    return record, deepcopy(record)


def test_pair_uses_independent_objective_ens_inputs_and_supervision():
    left, right = pair_records()
    assert a.pair_check(left, right)["passed"]
    assert a.pair_check(left, right)["storage_endpoints"] == [[[5], [5]], [[5], [5]]]
    right["supervised_accepted"] = False
    assert not a.pair_check(left, right)["available"]
    right.pop("supervised_accepted")
    right["mathematical_input_sha256"] = "different"
    assert not a.pair_check(left, right)["passed"]
    right["mathematical_input_sha256"] = "same"
    right["audit"]["common"]["metrics"]["energy_not_served_mwh"] += .001
    assert not a.pair_check(left, right)["passed"]


def setup_root(root):
    binding = dict(context=dict(commit="a"*40), limits=f.LIMITS, calls=[{} for _ in f.calls()])
    atomic_immutable_json(root / "binding.json", binding)
    atomic_immutable_json(root / "protocol.json", dict(protocol=f.LIMITS))
    return binding


def append(root, call_id=1, *, classification="exited", completed=True, supervised=True, elapsed=2., rss=10.):
    directory = root / f"call-{call_id:03d}"
    request = dict(call_id=call_id, role="primary", protocol=reference(root / "protocol.json", root), wall_seconds=180.)
    if not (directory / "request.json").exists():
        atomic_immutable_json(directory / "request.json", request)
    if completed:
        atomic_immutable_json(directory / "completion.json", dict(classification="accepted"))
    if supervised:
        atomic_immutable_json(directory / "launch.json", dict(pid=42))
        (directory / "worker.log").touch()
        # Atomic JSON plus newline is also one valid sampler JSONL record.
        atomic_immutable_json(directory / "resources.jsonl", dict(elapsed_seconds=1., rss_mib=rss))
        atomic_immutable_json(directory / "supervision.json", dict(classification=classification, returncode=0,
            wall_seconds=elapsed, peak_sampled_rss_mib=rss, samples=1))
    return directory


def synthetic_auditor(call, directory):
    return dict(audit=dict(accepted=True), classification="accepted")


@pytest.mark.parametrize("stage", ["prepared", "archived"])
def test_unsupervised_tail_never_accepted_advanceable_or_mutated(tmp_path, stage):
    setup_root(tmp_path)
    append(tmp_path, completed=stage == "archived", supervised=False)
    before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    progress, records = r.replay(tmp_path, auditor=synthetic_auditor)
    assert progress["launches"] == 1 and progress["accepted"] == progress["disposed"] == 0
    assert progress["next_call"] is None and not records
    assert before == {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}


@pytest.mark.parametrize("classification,elapsed,rss", [("wall_limit", 181., 10.), ("interrupted", 2., 10.),
    ("rss_limit", 2., 17000.), ("exited", 181., 10.)])
def test_stopped_archive_not_accepted_or_comparable(tmp_path, classification, elapsed, rss):
    setup_root(tmp_path)
    append(tmp_path, classification=classification, elapsed=elapsed, rss=rss)
    progress, records = r.replay(tmp_path, auditor=synthetic_auditor)
    assert progress["disposed"] == progress["unresolved"] == 1 and progress["accepted"] == 0
    assert progress["worker_seconds"] == elapsed and progress["next_call"] == 2
    assert not records[1]["supervised_accepted"]


def test_noncontiguous_and_unsupervised_before_later_fail(tmp_path):
    setup_root(tmp_path)
    append(tmp_path, supervised=False)
    append(tmp_path, call_id=2)
    with pytest.raises(ValueError, match="unsupervised"):
        r.replay(tmp_path, auditor=synthetic_auditor)


def test_sampler_and_successful_missing_archive_fail(tmp_path):
    setup_root(tmp_path)
    directory = append(tmp_path, completed=False)
    with pytest.raises(ValueError, match="lacks completion"):
        r.replay(tmp_path, auditor=synthetic_auditor)
    (directory / "resources.jsonl").write_text('{"elapsed_seconds":1,"rss_mib":20}')
    with pytest.raises(ValueError, match="journal/supervision"):
        r.replay(tmp_path, auditor=synthetic_auditor)


def test_completed_manifest_tampering_rejected_before_audit(tmp_path):
    binding = setup_root(tmp_path)
    directory = append(tmp_path)
    # Use a fresh subfolder so the immutable synthetic marker is not overwritten.
    directory = tmp_path / "archive"
    atomic_immutable_json(directory / "request.json", {})
    atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    atomic_immutable_json(directory / "completion.json", dict(artifacts={
        "request.json": digest(directory / "request.json"), "result.json.gz": "wrong"}))
    with pytest.raises(ValueError, match="manifest/archive"):
        r.independent_record(f.calls()[0], binding, directory, None)


@pytest.mark.parametrize("difference", [None, "residual", "solver_status", "native", "transformation", "accepted"])
def test_solved_socp_archive_replays_without_solver_statistics_only(tmp_path, monkeypatch, difference):
    kwargs, result, named = dc_fixture("singlenode_dc")
    audit = dict(accepted=True, native_full_convergence=True, common=dict(passed=True),
        transformation=dict(passed=True), relaxation=dict(solver_status="optimal",
            residuals=dict(p_balance=dict(maximum=0., passed=True)),
            solver_statistics=dict(solver_name="CLARABEL", num_iters=32,
                                   solve_time=.75, extra_stats=None)))
    replayed = deepcopy(audit)
    replayed["relaxation"]["solver_statistics"] = None
    if difference == "residual":
        replayed["relaxation"]["residuals"]["p_balance"]["maximum"] = 1e-12
    elif difference == "solver_status":
        replayed["relaxation"]["solver_status"] = "optimal_inaccurate"
    elif difference == "native":
        replayed["native_full_convergence"] = False
    elif difference == "transformation":
        replayed["transformation"]["passed"] = False
    elif difference == "accepted":
        replayed["accepted"] = False
    context = dict(clean=True, commit="a"*40)
    frozen = dict(mathematical_input_sha256="same")
    binding = dict(context=context, calls=[frozen] + [{} for _ in f.calls()[1:]])
    directory = tmp_path / "call-001"
    request = dict(call_id=1)
    record = a.serializable(dict(iteration=1, request=request, classification="accepted", exception=None,
        execution_context=context, mathematical_input_sha256="same", optimizer_calls=1,
        result=result, named_costs=named, native=dict(status="Solved"), audit=audit,
        boundary_soc_mwh=np.vstack(([s.initial_soc for s in kwargs["storage"]], result["soc"]))))
    atomic_immutable_json(directory / "request.json", request)
    atomic_gzip_json(directory / "result.json.gz", record)
    atomic_immutable_json(directory / "completion.json", dict(classification="accepted", artifacts={
        n: digest(directory / n) for n in ("request.json", "result.json.gz")}))
    monkeypatch.setattr(r, "kwargs_for_call", lambda *args: kwargs)
    monkeypatch.setattr(r, "call_binding", lambda *args: frozen)
    monkeypatch.setattr(r, "build_for_call", lambda *args: None)
    monkeypatch.setattr(r, "audit_record", lambda *args: replayed)
    before = {p: p.read_bytes() for p in directory.iterdir()}
    if difference is None:
        retained = r.independent_record(f.calls()[0], binding, directory, None)
        assert retained == record
        assert retained["audit"]["relaxation"]["solver_statistics"] == audit["relaxation"]["solver_statistics"]
    else:
        with pytest.raises(ValueError, match="independent audit/archive mismatch"):
            r.independent_record(f.calls()[0], binding, directory, None)
    assert before == {p: p.read_bytes() for p in directory.iterdir()}
    assert replayed["relaxation"]["solver_statistics"] is None


def test_replay_comparison_keeps_non_socp_audits_and_other_diagnostics():
    assert r.replayable_audit(dict(accepted=True, relaxation=None)) == dict(accepted=True, relaxation=None)
    retained = dict(relaxation=dict(solver_statistics=dict(num_iters=32), other_diagnostic=1))
    assert r.replayable_audit(retained) == dict(relaxation=dict(other_diagnostic=1))
    assert retained["relaxation"]["solver_statistics"] == dict(num_iters=32)


def test_run_refuses_dirty_wrong_commit_and_missing_monitoring(monkeypatch):
    binding = dict(context=dict(clean=False, commit="a"*40))
    monkeypatch.setattr(r, "frozen_binding", lambda: binding)
    monkeypatch.setattr(r, "supervise", lambda *args: pytest.fail("launched"))
    with pytest.raises(ValueError, match="clean"):
        r.run("a"*40)
    monkeypatch.setattr(r, "_child_rss_mib", lambda pid: None)
    with pytest.raises(RuntimeError, match="permission"):
        r.monitoring_preflight()


def test_singlestep_projection_preserves_values():
    source = dict(Pg=np.arange(3), Vm=np.ones(9), soc=np.array([18.]), p_net=0., objective=42.)
    projected = a.single_step_result(source)
    assert projected["Pg"].shape == (1, 3) and projected["soc"].shape == (1, 1)
    assert projected["p_net"].shape == (1,) and projected["objective"] == 42.
    assert source["Pg"].shape == (3,)


def synthetic_runner(monkeypatch, root, outcomes):
    context = dict(clean=True, commit="a"*40, thread_environment={k: "1" for k in r.THREAD_KEYS})
    binding = dict(context=context, limits=f.LIMITS, calls=[{} for _ in f.calls()])
    monkeypatch.setattr(r, "OUTPUT", root)
    monkeypatch.setattr(r, "context", lambda: context)
    monkeypatch.setattr(r, "frozen_binding", lambda: binding)
    monkeypatch.setattr(r, "monitoring_preflight", lambda: {})
    replay = r.replay
    monkeypatch.setattr(r, "replay", lambda root, **kwargs: replay(root, auditor=synthetic_auditor, **kwargs))
    monkeypatch.setattr(r, "qualification_report", lambda *args: {})
    launched = []
    def supervise(command, directory, root, request):
        launched.append(request["call_id"])
        outcome = outcomes.get(request["call_id"], "exited")
        append(root, request["call_id"], classification=outcome)
        return dict(classification=outcome, returncode=0)
    monkeypatch.setattr(r, "supervise", supervise)
    return launched


def test_serial_frozen_matrix_stops_after_25_without_replacement(tmp_path, monkeypatch):
    root = tmp_path / "qualification"
    launched = synthetic_runner(monkeypatch, root, {1: "wall_limit"})
    assert r.run("a"*40)["outcome"] == "matrix_complete"
    assert launched == list(range(1, 26))
    progress, _ = r.replay(root)
    assert progress["accepted"] == 24 and progress["unresolved"] == 1
    assert progress["finished"] and progress["next_call"] is None
    assert r.run("a"*40, resume=True)["outcome"] == "matrix_complete"
    assert launched == list(range(1, 26))


def test_explicit_resume_consumes_interrupted_call_not_retry(tmp_path, monkeypatch):
    root = tmp_path / "qualification"
    launched = synthetic_runner(monkeypatch, root, {1: "interrupted"})
    assert r.run("a"*40)["outcome"] == "interrupted"
    assert launched == [1]
    assert r.run("a"*40, resume=True)["outcome"] == "matrix_complete"
    assert launched == list(range(1, 26))


def replay_fix_fixture(monkeypatch, root):
    """Synthetic original/continued contexts and evidence; never solve."""
    launched = synthetic_runner(monkeypatch, root, {})
    continued = r.frozen_binding()
    continued["context"].update(sources={p: "new" for p in r.REPLAY_FIX_SOURCES}, installed={"native": "same"})
    continued["context"]["sources"]["src/cvxopf/problem.py"] = "unchanged"
    original = deepcopy(continued)
    original["context"]["commit"] = r.REPLAY_FIX_ORIGIN
    for p in r.REPLAY_FIX_SOURCES:
        original["context"]["sources"][p] = "old"
    atomic_immutable_json(root / "binding.json", original)
    atomic_immutable_json(root / "protocol.json", dict(protocol=f.LIMITS))
    directory = append(root)
    atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    atomic_immutable_json(directory / "phase.json", dict(phase="complete"))
    invocation = root / "invocations" / r.REPLAY_FIX_INVOCATION
    atomic_immutable_json(invocation / "start.json", dict(resume=False))
    atomic_immutable_json(invocation / "finish.json", dict(outcome="failure"))
    monkeypatch.setattr(r, "REPLAY_FIX_BINDING_SHA256", digest(root / "binding.json"))
    return original, continued, launched


def test_replay_fix_adoption_is_additive_and_remaining_calls_only(tmp_path, monkeypatch):
    root = tmp_path / "qualification"
    original, continued, launched = replay_fix_fixture(monkeypatch, root)
    before = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    candidate = r.prepare_replay_fix_transition(root, original, continued)
    assert not (root / "replay-fix-transition.json").exists()
    assert before == {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert r.run("a"*40, resume=True, adopt_replay_fix=True)["outcome"] == "matrix_complete"
    assert launched == list(range(2, 26))
    assert all(p.read_bytes() == content for p, content in before.items())
    transition = r.replay_fix_transition(root, original)
    assert transition["preserved"] == candidate["preserved"]
    assert transition["source_changes"] == candidate["source_changes"]
    assert r.binding_for_call(original, transition, 1) == original
    assert r.binding_for_call(original, transition, 2) == continued
    request = r.read(root / "call-002/request.json")
    assert request["source_transition"] == reference(root / "replay-fix-transition.json", root)
    assert "source_transition" not in r.read(root / "call-001/request.json")
    progress, _ = r.replay(root)
    assert progress["worker_seconds"] == 50. and progress["accepted"] == 25
    assert progress["attempts"][0]["execution_commit"] == r.REPLAY_FIX_ORIGIN
    assert all(a["execution_commit"] == "a"*40 for a in progress["attempts"][1:])
    transition_bytes = (root / "replay-fix-transition.json").read_bytes()
    assert r.run("a"*40, resume=True)["outcome"] == "matrix_complete"
    assert launched == list(range(2, 26))
    assert (root / "replay-fix-transition.json").read_bytes() == transition_bytes
    continued["context"]["commit"] = "b"*40
    with pytest.raises(ValueError, match="adoption required"):
        r.run("b"*40, resume=True, adopt_replay_fix=True)
    assert launched == list(range(2, 26))
    assert (root / "replay-fix-transition.json").read_bytes() == transition_bytes


@pytest.mark.parametrize("change", ["production", "added_source", "removed_source", "installed", "protocol", "dirty", "same_commit", "wrong_origin", "no_changes"])
def test_replay_fix_context_rejects_unrelated_changes(tmp_path, monkeypatch, change):
    original, continued, _ = replay_fix_fixture(monkeypatch, tmp_path)
    current = deepcopy(continued["context"])
    if change == "production":
        current["sources"]["src/cvxopf/problem.py"] = "changed"
    elif change == "added_source":
        current["sources"]["new.py"] = "added"
    elif change == "removed_source":
        current["sources"].pop("src/cvxopf/problem.py")
    elif change == "installed":
        current["installed"]["native"] = "changed"
    elif change == "protocol":
        current["protocol_sha256"] = "changed"
    elif change == "dirty":
        current["clean"] = False
    elif change == "same_commit":
        current["commit"] = r.REPLAY_FIX_ORIGIN
    elif change == "wrong_origin":
        original["context"]["commit"] = "b"*40
    else:
        current["sources"] = deepcopy(original["context"]["sources"])
    with pytest.raises(ValueError, match="replay-fix"):
        r.replay_fix_changes(original["context"], current)


@pytest.mark.parametrize("field", ["calls", "limits", "raw_inputs"])
def test_replay_fix_rejects_input_settings_and_budget_changes(tmp_path, monkeypatch, field):
    original, continued, _ = replay_fix_fixture(monkeypatch, tmp_path)
    changed = deepcopy(continued)
    changed[field] = {"changed": True}
    with pytest.raises(ValueError, match="frozen inputs/settings/budgets"):
        r.prepare_replay_fix_transition(tmp_path, original, changed)


@pytest.mark.parametrize("failure", ["unadopted", "unfinished", "extra_call", "tampered", "wrong_binding", "wrong_transition_hash"])
def test_replay_fix_refuses_missing_or_changed_evidence(tmp_path, monkeypatch, failure):
    original, continued, launched = replay_fix_fixture(monkeypatch, tmp_path)
    if failure == "unadopted":
        with pytest.raises(ValueError, match="adoption required"):
            r.run("a"*40, resume=True)
    elif failure == "unfinished":
        (tmp_path / "call-001/supervision.json").rename(tmp_path / "saved-supervision.json")
        with pytest.raises(ValueError, match="evidence set"):
            r.prepare_replay_fix_transition(tmp_path, original, continued)
    elif failure == "extra_call":
        append(tmp_path, call_id=2)
        with pytest.raises(ValueError, match="exactly"):
            r.prepare_replay_fix_transition(tmp_path, original, continued)
    elif failure == "wrong_binding":
        monkeypatch.setattr(r, "REPLAY_FIX_BINDING_SHA256", "wrong")
        with pytest.raises(ValueError, match="origin/protocol"):
            r.prepare_replay_fix_transition(tmp_path, original, continued)
    else:
        candidate = r.prepare_replay_fix_transition(tmp_path, original, continued)
        atomic_immutable_json(tmp_path / "replay-fix-transition.json", candidate)
        if failure == "tampered":
            (tmp_path / "call-001/worker.log").write_text("changed")
            with pytest.raises(ValueError, match="preserved evidence"):
                r.replay(tmp_path)
        else:
            append(tmp_path, call_id=2)  # Missing mandatory transition reference.
            with pytest.raises(ValueError, match="request/call/budget"):
                r.replay(tmp_path)
    assert not launched


def test_terminal_dispositions_do_not_count_unavailable_pairs(monkeypatch):
    monkeypatch.setattr(r.e3, "verified_inputs", lambda: None)
    monkeypatch.setattr(r, "kwargs_for_call", lambda *args: {})
    monkeypatch.setattr(r, "build_for_call", lambda *args: None)
    def historical(*args):
        raise ValueError("retained comparator unavailable")
    monkeypatch.setattr(r, "historical_control", historical)
    attempts = [dict(call_id=c.id, classification="exited", accepted=True) for c in f.calls()]
    progress = dict(attempts=attempts)
    report = r.qualification_report(dict(context=dict(commit="a"*40)), progress, {})
    assert set(report["qualification"].values()) == {"incomplete"}
    attempts[5]["accepted"] = False
    report = r.qualification_report(dict(context=dict(commit="a"*40)), progress, {})
    assert report["qualification"]["lossy_dc"] == "not_qualified"
