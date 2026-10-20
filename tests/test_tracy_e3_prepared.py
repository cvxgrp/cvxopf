"""Prepared E3 journal and inverse-chain checks; optimizer calls are forbidden."""

from copy import deepcopy
from dataclasses import fields, replace
import json
import os

import clarabel
import cyipopt
import numpy as np
import pytest

from cvxopf import OPFOptions, build_opf_multistep
from cvxopf._numerical_preparation import PreparationEvidence
from cvxopf import _ac_preparation
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json, atomic_gzip_json
from experiments.case118_tracy_2021 import e3, e3_prepared as model, run_e3 as runner
from experiments.case118_tracy_2021.stage_d import read
from experiments.numerical_preparation.audit import serializable
from tests.test_tracy_e3_runner import selection
from tests.test_tracy_stage_d import fixture


@pytest.fixture(autouse=True)
def forbid_optimization(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("prepared E3 tests must not optimize")
    monkeypatch.setattr(clarabel, "DefaultSolver", forbidden)
    monkeypatch.setattr(cyipopt, "Problem", forbidden)
    monkeypatch.setattr(model.starts.IPOPT, "solve_via_data", forbidden)


def setup(root, monkeypatch, formulation="ac"):
    monkeypatch.setattr(model, "verify_context", lambda expected: None)
    study = dict(e3.make_study(selection()), cohort="prepared", schema=2)
    arm = next(a for a in study["arms"] if a["formulation"] == formulation)
    study["arms"] = [dict(arm, id=0)]
    atomic_immutable_json(root / "binding.json", dict(context=dict(clean=True, commit="a"*40), study=study))
    runner.queue_protocol(root, deepcopy(model.DEFAULT_PROTOCOL), "synthetic prepared protocol")


def append(root, *, accepted=False, outcome="exited", supervised=True):
    item = runner.replay(root, lambda a, r, v: v["classification"] == "accepted")["next"]
    arm, slot = item["arm"], item["slot"]
    parent = root / "arm-000"
    directory = parent / f"attempt-{len(list(parent.glob('attempt-*'))):03d}"
    request = runner.request_for(arm, slot, item["causal_source"], item["target_free_source"],
        read(root / "control.json")["protocol"], 10., cohort=model)
    atomic_immutable_json(directory / "request.json", request)
    if supervised:
        atomic_immutable_json(directory / "supervision.json", dict(classification=outcome, returncode=0, wall_seconds=2.))
    atomic_immutable_json(directory / "start.json", dict(causal_start={}, assigned_start={}))
    atomic_gzip_json(directory / "x0.json.gz", dict(iteration=0))
    atomic_gzip_json(directory / "native.json.gz", dict(iteration=0, data={}))
    atomic_gzip_json(directory / "preparation.json.gz", dict(iteration=0, data={}))
    atomic_gzip_json(directory / "result.json.gz", dict(iteration=0, classification="accepted" if accepted else "rejected",
        attempt=str(directory.relative_to(root)), request=request,
        execution_context=read(root / "binding.json")["context"], native={}, preparation_evidence={}))
    atomic_immutable_json(directory / "completion.json", dict(classification="accepted" if accepted else "rejected",
        artifacts={p.name: e3.digest(p) for p in directory.iterdir() if p.name != "supervision.json"}))
    return directory, request


def replay(root, **kwargs):
    return runner.replay(root, lambda a, r, v: v["classification"] == "accepted", **kwargs)


def test_complete_two_phase_ladder_sources_and_target_free_not_terminal(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    requests = []
    for slot in range(20):
        _, request = append(tmp_path, accepted=slot % 10 == 5)
        requests.append(request)
        if slot in (5, 15):
            assert replay(tmp_path)["accepted_arms"] == 0
    result = replay(tmp_path)
    assert result["finished"] and result["accepted_arms"] == 0
    assert result["launches"] == 20 and result["worker_seconds"] == 40
    assert [r["role"] for r in requests] == list(e3.ROLES)*2
    assert [r["objective_assembly"] for r in requests] == ["component_first"]*10+["hourly"]*10
    assert requests[10]["target_free_source"] is None
    assert requests[16]["target_free_source"] != requests[6]["target_free_source"]
    assert requests[11]["causal_source"] == requests[1]["causal_source"]


def test_failed_target_free_skips_only_its_phase(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    for _ in range(6):
        append(tmp_path)
    assert replay(tmp_path)["next"]["slot"] == 10
    append(tmp_path, accepted=True)
    assert replay(tmp_path)["accepted_arms"] == 1


def test_interruption_retries_slot_and_unsupervised_never_seeds(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    _, request = append(tmp_path, outcome="interrupted")
    assert replay(tmp_path)["next"]["slot"] == 0
    directory, repeated = append(tmp_path, accepted=True, supervised=False)
    assert repeated["phase"] == request["phase"]
    status = replay(tmp_path, partial=True)
    assert status["next"] is None and status["accepted_arms"] == 0
    assert status["worker_seconds"] == 2 and status["active_attempt"]["path"] == str(directory.relative_to(tmp_path))


def test_native_archives_required_and_hash_bound(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch, "socp")
    directory, _ = append(tmp_path, accepted=True)
    (directory / "native.json.gz").unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        replay(tmp_path)


@pytest.mark.parametrize("formulation", e3.FORMULATIONS)
def test_policy_and_settings_are_formulation_specific(formulation):
    value = model.policy(formulation, "component_first" if formulation == "ac" else "hourly")
    assert value.exact_fixed_boxes
    assert value.normalize_device_limits == (formulation in {"ac", "socp"})
    assert value.cost_coordinates == (formulation == "ac")
    assert value.canonical_scaling == ("none" if formulation == "ac" else "joint5")
    if formulation != "ac":
        options = model.solver_options(formulation, model.DEFAULT_PROTOCOL)
        assert options["tol_gap_rel"] == (1e-6 if formulation == "socp" else 1e-10)
        assert ("min_terminate_step_length" in options) == (formulation == "socp")


def test_fixed_settings_budget_validation_and_historical_output_guard():
    for key, value in (("max_launches", True), ("rss_mib", 8192), ("wall_seconds", float("nan"))):
        protocol = deepcopy(model.DEFAULT_PROTOCOL)
        protocol[key] = value
        with pytest.raises(ValueError):
            model.validate_protocol(protocol)
    protocol = deepcopy(model.DEFAULT_PROTOCOL)
    protocol["socp_options"]["tol_gap_rel"] = 1e-4
    with pytest.raises(ValueError):
        model.validate_protocol(protocol)
    for path in (model.HERE / "results/e3", model.HERE / "results/e3/nested", model.HERE / "results"):
        with pytest.raises(ValueError):
            model.validate_output(path)


def test_economic_warning_does_not_reject_but_unexplained_difference_does():
    costs = dict(generator_cost=10., storage_cost=.01, load_shedding_cost=100.)
    checked = model.economics(110.02, costs, np.array([.01]), formulation="ac")
    assert checked["cycling_warning"] and checked["passed"]
    assert not model.economics(111., costs, np.array([.01]), formulation="ac")["passed"]


@pytest.mark.parametrize("native_status", [1, 6, -1, "AlmostSolved", "MaxIterations"])
def test_full_native_convergence_required(native_status):
    formulation = "ac" if isinstance(native_status, int) else "socp"
    evidence = dict(native=dict(status=native_status))
    record = dict(native=evidence["native"], preparation_evidence=evidence,
                  preparation_policy=model.policy_record(formulation, "hourly"))
    kwargs = dict(options=OPFOptions(numerical_preparation=model.policy(formulation)))
    assert not model.assess(dict(formulation=formulation), "primary", record, kwargs)["passed"]


@pytest.mark.parametrize("assembly", ["component_first", "hourly"])
def test_analytic_ac_native_restoration_and_mapping_corruption(assembly):
    kwargs, original, _, _ = fixture(3)
    kwargs["storage"] = [replace(u, aging_weight=.01) for u in kwargs["storage"]]
    kwargs["options"] = OPFOptions(numerical_preparation=model.policy("ac", assembly))
    build = build_opf_multistep(**kwargs)
    assigned = model.starts._solution_values(original)
    model.starts._assign_start(build, assigned)
    _, solver_build, bindings, reduction, data, inverse, mapping = model.ac_canonical(build)
    full = data["x0"].copy()
    full[mapping.fixed] = mapping.values
    for binding, auxiliary in reduction.cycling_auxiliaries:
        offset = inverse[-1].var_offsets[auxiliary.id]
        full[offset:offset+auxiliary.size] = abs(binding.absolute.args[0].value).ravel(order="F")
    native_cost, _ = model.canonical_objective(data, inverse, full)
    layout = [(v.id, list(v.shape), inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
              for v in data["problem"].variables()]
    evidence = serializable(dict(native=dict(status=0, obj_val=native_cost, x=full[mapping.free]),
        coordinates={f.name: getattr(mapping, f.name) for f in fields(mapping) if not f.name.startswith("_")},
        start_layout=layout, assigned_x0=data["x0"], adjusted_x0=np.where(
            np.isin(np.arange(mapping.full_size), mapping.fixed), full, data["x0"]),
        reduced_x0=data["x0"][mapping.free], variable_scale=np.ones(mapping.free.size), row_scale=np.ones(mapping.kept.size),
        checks=dict(objective_assembly=assembly, cost_coordinate_maps=[dict(kind=b.term.kind,
            solver_variable_id=b.leaf.id, scale=b.scale, physical_start=b.physical_start) for b in bindings])))
    start = dict(assigned_start=serializable(assigned))
    captured = model.prepared_start(build, 0)
    fresh = build_opf_multistep(**kwargs)
    result, slack, error = model.restore_ac(fresh, evidence, start, captured)
    assert error <= 1e-7 and np.max(abs(slack), initial=0) == 0
    assert result["soc"][0] == [5.]
    np.testing.assert_allclose(result["Pg"][0], [7., 0., 0.], rtol=0, atol=1e-12)
    corrupt = deepcopy(evidence)
    corrupt["coordinates"]["full_size"] += 1
    with pytest.raises(ValueError):
        model.restore_ac(build_opf_multistep(**kwargs), corrupt, start, captured)


def test_existing_analytic_convex_native_inverse_is_reused_without_solving():
    from tests.test_e1_regression_runner import candidate, native_fixture
    from experiments.m11_socp import e1_compare
    from tests.socp_reference_comparison import construct
    row = e1_compare.row_binding(dict(id=1, case="two_bus", assembly="vectorized", treatment="prepared"))
    _, public = candidate("two_bus", "vectorized", "prepared")
    native, evidence = native_fixture(row, public)
    _, _, fresh = construct("two_bus", "vectorized", "prepared")
    restored, _ = model.restored_result(fresh, native, evidence)
    for key in ("Pg", "Qg", "w", "W_re", "W_im"):
        np.testing.assert_allclose(restored[key], public[key], rtol=0, atol=1e-10)
    changed_public = deepcopy(restored)
    changed_public["Pg"][0][0] += 1
    with pytest.raises(ValueError):
        model.publication_check(restored, changed_public)
    corrupted = deepcopy(evidence)
    corrupted["variable_scale"][0] *= 2
    _, _, fresh = construct("two_bus", "vectorized", "prepared")
    with pytest.raises(AssertionError):
        model.restored_result(fresh, native, corrupted)


def test_prepared_preflight_is_non_solving_and_creates_no_artifacts(tmp_path, monkeypatch):
    current = dict(clean=True, commit="a"*40, thread_environment={k: "1" for k in runner.THREAD_KEYS})
    monkeypatch.setattr(model, "context", lambda: current)
    monkeypatch.setattr(model, "specification", lambda: dict(cohort="prepared", arms=[]))
    monkeypatch.setattr(model, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "monitoring", lambda: {})
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.)
    output = tmp_path / "fresh"
    assert runner.run(output, "a"*40, prepared=True, preflight=True)["classification"] == "preflight_only"
    assert not output.exists()


def test_real_prepared_context_round_trips_strict_binding_json(tmp_path, monkeypatch):
    # Use installed CLARABEL settings and native provenance, but no owner data
    # or clean-checkout prerequisite for this non-solving serialization test.
    monkeypatch.setattr(e3, "context", lambda: dict(clean=True, commit="a"*40,
        code_sha256={}, thread_environment={k: "1" for k in runner.THREAD_KEYS}))
    current = model.context()
    for formulation in ("socp", "lossy_dc", "singlenode_dc"):
        settings = current["resolved_clarabel_settings"][formulation]
        assert settings["time_limit"] == {"nonfinite": "inf"}
        assert settings["tol_gap_rel"] == model.solver_options(
            formulation, model.DEFAULT_PROTOCOL)["tol_gap_rel"]
    # The archive representation must not alter the native unlimited setting.
    assert clarabel.DefaultSettings().time_limit == float("inf")
    binding = dict(context=current, study=dict(cohort="prepared", arms=[]))
    assert json.loads(json.dumps(binding, allow_nan=False)) == binding
    atomic_immutable_json(tmp_path / "binding.json", binding)
    retained = read(tmp_path / "binding.json")
    assert retained == binding
    model.verify_context(retained["context"])
    monkeypatch.setattr(model, "specification", lambda: binding["study"])
    monkeypatch.setattr(model, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "monitoring", lambda: {})
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.)
    output = tmp_path / "fresh"
    assert runner.run(output, "a"*40, prepared=True, preflight=True)["classification"] == "preflight_only"
    assert not output.exists()


@pytest.mark.parametrize("preflight", [False, True])
@pytest.mark.parametrize("location", ["context", "study"])
def test_binding_serialization_failure_precedes_run_artifacts(tmp_path, monkeypatch, preflight, location):
    current = dict(clean=True, commit="a"*40, thread_environment={k: "1" for k in runner.THREAD_KEYS})
    study = dict(cohort="prepared", arms=[])
    (current if location == "context" else study)["invalid"] = float("inf")
    monkeypatch.setattr(model, "context", lambda: current)
    monkeypatch.setattr(model, "specification", lambda: study)
    monkeypatch.setattr(model, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "monitoring", lambda: {})
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.)
    output = tmp_path / "fresh"
    with pytest.raises(ValueError, match="JSON compliant"):
        runner.run(output, "a"*40, prepared=True, preflight=preflight)
    assert not output.exists()


def test_prepared_monitoring_failure_precedes_run_artifacts(tmp_path, monkeypatch):
    current = dict(clean=True, commit="a"*40, thread_environment={k: "1" for k in runner.THREAD_KEYS})
    monkeypatch.setattr(model, "context", lambda: current)
    monkeypatch.setattr(model, "specification", lambda: dict(cohort="prepared", arms=[]))
    monkeypatch.setattr(model, "verified_inputs", lambda: None)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.)
    def failed():
        raise RuntimeError("AC power required")
    monkeypatch.setattr(model, "monitoring", failed)
    output = tmp_path / "fresh"
    with pytest.raises(RuntimeError, match="AC power"):
        runner.run(output, "a"*40, prepared=True)
    assert not output.exists()


def test_native_rejection_archives_real_worker_path_without_extracting(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    arm = read(tmp_path / "binding.json")["study"]["arms"][0]
    directory = tmp_path / "arm-000/attempt-000"
    request = model.request_for(arm, 0, None, None, read(tmp_path / "control.json")["protocol"], 10.)
    atomic_immutable_json(directory / "request.json", request)
    atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    kwargs, _, _, _ = fixture(3)
    kwargs["options"] = OPFOptions(numerical_preparation=model.policy("ac", "component_first"))
    monkeypatch.setattr(e3, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "kwargs_for_arm", lambda *args, **kw: kwargs)
    monkeypatch.setattr(e3, "prepare_start", lambda build, *args: (
        model.starts._complete_start(build), model.starts._complete_start(build)))
    def solve(build, **options):
        _, _, _, _, data, inverse, mapping = model.ac_canonical(build)
        adjusted = data["x0"].copy()
        adjusted[mapping.fixed] = mapping.values
        build._preparation_evidence = PreparationEvidence(mapping, dict(status=-1, obj_val=1.),
            dict(restoration_available=False), np.ones(mapping.free.size), np.ones(mapping.kept.size),
            assigned_x0=data["x0"], adjusted_x0=adjusted, reduced_x0=adjusted[mapping.free],
            start_layout=tuple((v.id, v.shape, inverse[-1].var_offsets[v.id],
                inverse[-1].var_offsets[v.id]+v.size) for v in data["problem"].variables()))
    from cvxopf import OPFBuild
    monkeypatch.setattr(OPFBuild, "solve", solve)
    monkeypatch.setattr(e3, "extract_results", lambda build: pytest.fail("native rejection must not publish primals"))
    assert model.worker(directory, tmp_path) == 0
    record = read(directory / "result.json.gz")
    assert record["classification"] == "rejected" and record["native"]["status"] == -1
    assert record["result"] is None
    manifest = read(directory / "completion.json")
    assert {"native.json.gz", "preparation.json.gz", "start.json", "x0.json.gz"} <= set(manifest["artifacts"])
    assert read(directory / "native.json.gz")["data"] == record["native"]


@pytest.mark.parametrize("assembly", ["component_first", "hourly"])
def test_worker_uses_public_cost_transformation_before_verified_boundary(tmp_path, monkeypatch, assembly):
    setup(tmp_path, monkeypatch)
    arm = read(tmp_path / "binding.json")["study"]["arms"][0]
    directory = tmp_path / "arm-000/attempt-000"
    request = model.request_for(arm, 0 if assembly == "component_first" else 10, None, None,
                                read(tmp_path / "control.json")["protocol"], 10.)
    atomic_immutable_json(directory / "request.json", request)
    atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    kwargs, _, _, _ = fixture(3)
    kwargs["storage"] = [replace(u, aging_weight=.01) for u in kwargs["storage"]]
    kwargs["options"] = OPFOptions(numerical_preparation=model.policy("ac", assembly))
    monkeypatch.setattr(e3, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "kwargs_for_arm", lambda *args, **kw: kwargs)
    monkeypatch.setattr(e3, "prepare_start", lambda build, *args: (
        model.starts._complete_start(build), model.starts._complete_start(build)))
    observed = []
    def boundary(solver_build, solver, data, inverse, verbose, solver_opts):
        fresh = build_opf_multistep(**kwargs)
        retained = read(directory / "start.json")["assigned_start"]
        model.starts._assign_start(fresh, {k: np.asarray(v) for k, v in retained.items()})
        _, _, bindings, _, expected, _, mapping = model.ac_canonical(fresh)
        assert bindings and solver_build.numerical_preparation.objective_assembly == assembly
        np.testing.assert_array_equal(data["x0"], expected["x0"])
        layout = tuple((v.id, v.shape, inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
                       for v in data["problem"].variables())
        native = dict(status=-1, obj_val=1., elapsed_seconds=.01)
        adjusted = data["x0"].copy()
        adjusted[mapping.fixed] = mapping.values
        solver_build._preparation_evidence = PreparationEvidence(mapping, native,
            dict(restoration_available=False, objective_assembly=assembly),
            np.ones(mapping.free.size), np.ones(mapping.kept.size),
            assigned_x0=data["x0"], adjusted_x0=adjusted, reduced_x0=adjusted[mapping.free], start_layout=layout)
        observed.append(True)
        return native
    monkeypatch.setattr(_ac_preparation, "solve_at_verified_boundary", boundary)
    assert model.worker(directory, tmp_path) == 0
    record = read(directory / "result.json.gz")
    assert observed and record["classification"] == "rejected", record.get("exception")
    assert record["preparation_evidence"]["checks"]["cost_coordinate_maps"]
    assert read(directory / "x0.json.gz")["complete_x0"] == record["preparation_evidence"]["assigned_x0"]


@pytest.mark.parametrize("assembly", ["component_first", "hourly"])
def test_public_worker_archival_and_independent_acceptance_with_analytic_native(tmp_path, monkeypatch, assembly):
    """Exercise the production wrapper with a supplied analytic primal, not IPOPT."""
    setup(tmp_path, monkeypatch)
    binding = read(tmp_path / "binding.json")
    binding["study"]["storage_device_ids"] = ["battery"]
    arm = binding["study"]["arms"][0]
    arm.update(initial_soc_mwh=[5.], terminal_soc_mwh=[5.])
    # Test-only mutable fixture binding, before worker artifacts exist.
    from experiments.case118_annual_hierarchy.streaming_schema import atomic_json
    atomic_json(tmp_path / "binding.json", binding)
    directory = tmp_path / "arm-000/attempt-000"
    request = model.request_for(arm, 0 if assembly == "component_first" else 10, None, None,
        read(tmp_path / "control.json")["protocol"], 10.)
    atomic_immutable_json(directory / "request.json", request)
    atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    kwargs, original, _, _ = fixture(3)
    kwargs["storage"] = [replace(u, aging_weight=.01) for u in kwargs["storage"]]
    kwargs["options"] = OPFOptions(numerical_preparation=model.policy("ac", assembly))
    assigned = model.starts._solution_values(original)
    monkeypatch.setattr(e3, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "kwargs_for_arm", lambda *args, **kw: kwargs)
    def prepare(build, *args):
        model.starts._assign_start(build, assigned)
        return assigned, assigned
    monkeypatch.setattr(e3, "prepare_start", prepare)
    def native(self, data, warm_start, verbose, solver_opts, solver_cache=None):
        # Retain stock reduction, fixed-coordinate removal and restoration.
        # Only the native optimizer call is replaced with this supplied point.
        oracle = solver_cache["oracles"]
        point = data["x0"].copy()
        return dict(status=0, status_msg=b"synthetic analytic primal", x=point,
            obj_val=oracle.objective(point), mult_g=np.zeros(len(data["cl"])),
            mult_x_L=np.zeros(len(point)), mult_x_U=np.zeros(len(point)))
    monkeypatch.setattr(model.starts.IPOPT, "solve_via_data", native)
    assert model.worker(directory, tmp_path) == 0
    record = read(directory / "result.json.gz")
    assert record["classification"] == "accepted", record.get("exception") or record.get("independent_audit")
    assert record["independent_audit"]["passed"] and record["logical_solution"]
    assert {m["kind"] for m in record["preparation_evidence"]["checks"]["cost_coordinate_maps"]} == {"cycling", "shedding"}
    checked = model.assess(arm, "primary", record, kwargs, read(directory / "start.json"), read(directory / "x0.json.gz"))
    assert checked == record["independent_audit"]


def test_expected_canonical_and_reduced_starts_survive_interrupted_public_solve(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    arm = read(tmp_path / "binding.json")["study"]["arms"][0]
    directory = tmp_path / "arm-000/attempt-000"
    request = model.request_for(arm, 0, None, None, read(tmp_path / "control.json")["protocol"], 10.)
    atomic_immutable_json(directory / "request.json", request)
    atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    kwargs, _, _, _ = fixture(3)
    kwargs["storage"] = [replace(u, aging_weight=.01) for u in kwargs["storage"]]
    kwargs["options"] = OPFOptions(numerical_preparation=model.policy("ac", "component_first"))
    monkeypatch.setattr(e3, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "kwargs_for_arm", lambda *args, **kw: kwargs)
    monkeypatch.setattr(e3, "prepare_start", lambda build, *args: (
        model.starts._complete_start(build), model.starts._complete_start(build)))
    def interrupted(build, **kwargs):
        retained = read(directory / "x0.json.gz")
        assert retained["evidence_kind"] == "prospective_canonical_start"
        assert retained["complete_x0"] and retained["reduced_x0"]
        assert {m["kind"] for m in retained["cost_maps"]} == {"cycling", "shedding"}
        raise KeyboardInterrupt("synthetic termination before optimizer")
    from cvxopf import OPFBuild
    monkeypatch.setattr(OPFBuild, "solve", interrupted)
    with pytest.raises(KeyboardInterrupt):
        model.worker(directory, tmp_path)
    assert (directory / "start.json").exists() and (directory / "x0.json.gz").exists()
    assert not (directory / "completion.json").exists() and not (directory / "result.json.gz").exists()
