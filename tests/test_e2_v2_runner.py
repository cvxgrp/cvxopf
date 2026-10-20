"""Prepared E2 binding, restoration and supervision checks; never optimize."""

from copy import deepcopy
import json
import os
from types import SimpleNamespace

import cvxpy as cp
import numpy as np
import pytest
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT

from cvxopf import OPFBuild, extract_results
from cvxopf._numerical_preparation import PreparationEvidence
from experiments.m11_socp import e2_v2 as r, e2_v2_audit as a
from tests.socp_matched import PAIR_NAMES, fixture, digest


@pytest.fixture(autouse=True)
def no_optimization(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("optimization not authorized in runner tests")
    monkeypatch.setattr(OPFBuild, "solve", forbidden)
    monkeypatch.setattr(IPOPT, "solve_via_data", forbidden)


@pytest.fixture
def portable(monkeypatch):
    monkeypatch.setattr(r, "context", lambda: dict(head="a"*40, worktree_status="",
        thread_environment={k:"1" for k in r.THREAD_KEYS}))
    # Defaults introspection must still see the real adapter, not the sentinel.
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=r.context(),
        rows=[a.row_binding(n) for n in PAIR_NAMES]))


def test_matrix_preserves_original_inputs_and_selected_representations(portable):
    binding = r.frozen_binding()
    assert len(binding["rows"]) == 4
    assert json.loads(json.dumps(binding)) == binding
    for row in binding["rows"]:
        assert row["input_sha256"] == digest(fixture(row["name"]))
        assert digest(row["inputs"]) == row["input_sha256"]
        ac, socp = row["policies"]["ac"], row["policies"]["socp"]
        assert ac["normalize_device_limits"] and ac["exact_fixed_boxes"] and ac["cost_coordinates"]
        assert ac["objective_assembly"] == ("component_first" if row["name"] == "mixed_vectorized" else "hourly")
        assert socp == dict(normalize_device_limits=True, exact_fixed_boxes=True,
            cost_coordinates=False, canonical_scaling="joint5", objective_assembly="hourly")
        expected = int(row["name"].startswith("mixed"))
        assert len(row["ac_map"]["fixed"]) == len(row["socp_map"]["fixed"]) == expected
    assert r.LIMITS["max_optimizer_calls"] == 8 and r.LIMITS["max_launches"] == 4
    assert r.SETTINGS["ac"]["max_cpu_time"] == 60.
    assert r.SETTINGS["socp"]["time_limit"] == 20.


def synthetic_ac(name, slack=0.):
    """Encode a flat start, not a physically feasible or converged candidate.

    Mock native status only exercises restoration algebra. Independent network
    acceptance remains in assess(), and is intentionally not claimed here.
    """
    row = a.row_binding(name)
    _, build, _, _ = a.construct(name)
    _, solver_build, bindings, reduction, data, inverse, mapping = a.ac_canonical(build)
    assigned = data["x0"].copy()
    full = assigned.copy()
    full[mapping.fixed] = mapping.values
    for binding, auxiliary in reduction.cycling_auxiliaries:
        leaf_start = inverse[-1].var_offsets[binding.leaf.id]
        aux_start = inverse[-1].var_offsets[auxiliary.id]
        full[aux_start:aux_start+auxiliary.size] = abs(full[leaf_start:leaf_start+binding.leaf.size])+slack
    value, _ = a.canonical_objective(data, inverse, full)
    for variable in solver_build.prob.variables():
        start = inverse[-1].var_offsets[variable.id]
        variable.save_value(full[start:start+variable.size].reshape(variable.shape, order="F"))
    for binding in bindings:
        binding.term.variable.save_value(binding.leaf.value/binding.scale)
    build.prob._status, build.prob._value = cp.OPTIMAL, float(build.prob.objective.value)
    published = r.serializable(extract_results(build))
    native = dict(status=0, obj_val=value, x=full[mapping.free].tolist())
    layout = tuple((v.id, v.shape, inverse[-1].var_offsets[v.id], inverse[-1].var_offsets[v.id]+v.size)
                   for v in data["problem"].variables())
    checks = dict(restoration_available=True, objective_assembly=build.numerical_preparation.objective_assembly,
        cost_coordinate_maps=[dict(kind=b.term.kind, solver_variable_id=b.leaf.id,
                                  physical_variable_id=b.term.variable.id,
                                  scale=b.scale, physical_start=b.physical_start) for b in bindings])
    adjusted = assigned.copy()
    adjusted[mapping.fixed] = mapping.values
    evidence = r.evidence_record(PreparationEvidence(mapping, native, checks,
        np.ones(mapping.free.size), np.ones(mapping.kept.size), assigned_x0=assigned,
        adjusted_x0=adjusted, reduced_x0=adjusted[mapping.free], start_layout=layout))
    return row, evidence, published


@pytest.mark.parametrize("name", PAIR_NAMES)
@pytest.mark.parametrize("slack", (0., .01))
def test_ac_restoration_and_cycling_warning_without_optimization(name, slack):
    row, evidence, published = synthetic_ac(name, slack)
    _, build, _, _ = a.construct(name)
    economics = a.restore_ac(build, row, evidence, published)
    assert abs(economics["unexplained_difference"]) < 1e-7
    mixed = name.startswith("mixed")
    assert economics["cycling_warning"] is bool(mixed and slack)
    assert economics["cycling_epigraph_excess"] == pytest.approx(6*slack if mixed else 0.)


@pytest.mark.parametrize("field", ("assigned_x0", "reduced_x0", "variable_scale", "row_scale",
                                   "coordinates", "native", "maps", "assembly", "published"))
def test_ac_restoration_rejects_corrupted_representation(field):
    row, evidence, published = synthetic_ac("mixed_vectorized")
    evidence, published = deepcopy(evidence), deepcopy(published)
    if field in ("assigned_x0", "reduced_x0", "variable_scale", "row_scale"):
        evidence[field][0] += 1.
    elif field == "coordinates":
        evidence[field]["values"][0] += 1.
    elif field == "native":
        evidence["native"]["obj_val"] += 1.
    elif field == "maps":
        evidence["checks"]["cost_coordinate_maps"][0]["scale"][0][0] *= 2
    elif field == "assembly":
        evidence["checks"]["objective_assembly"] = "hourly"
    else:
        published["Pg"][0][0] += 1.
    _, build, _, _ = a.construct("mixed_vectorized")
    with pytest.raises((AssertionError, ValueError)):
        a.restore_ac(build, row, evidence, published)


def test_cli_requires_mode_and_preflight_creates_no_artifacts(portable, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["e2_v2"])
    with pytest.raises(SystemExit):
        r.main()
    root = tmp_path/"fresh"
    monkeypatch.setattr("sys.argv", ["e2_v2", "--preflight", "--output", str(root)])
    r.main()
    assert len(json.loads(capsys.readouterr().out)["rows"]) == 4
    assert not root.exists()


def bound_root(tmp_path):
    root = tmp_path/"e2"
    root.mkdir()
    r.q.atomic_immutable_json(root/"binding.json", r.frozen_binding())
    r.q.atomic_immutable_json(root/"protocol.json", r.protocol())
    return root


def supervised(root, number=1, classification="exited"):
    directory = root/f"call-{number:03d}"
    directory.mkdir()
    r.q.atomic_immutable_json(directory/"request.json", r.request(root, number, 180.))
    r.q.atomic_immutable_json(directory/"launch.json", dict(pid=os.getpid()))
    (directory/"worker.log").write_text("mock worker\n")
    (directory/"resources.jsonl").write_text(json.dumps(dict(elapsed_seconds=.1, rss_mib=10.))+"\n")
    r.q.atomic_immutable_json(directory/"supervision.json", dict(classification=classification, returncode=0,
        wall_seconds=.2, peak_sampled_rss_mib=10., samples=1))
    return directory


def archive(root, directory, both=True):
    row = r.q.read(root/"binding.json")["rows"][0]
    records = {"ac":dict(native=dict(status=0))}
    if both:
        records["socp"] = dict(native=dict(status="Solved"))
    record = dict(iteration=1, call_id=1, row=row, request=r.q.read(directory/"request.json"),
        optimizer_calls=len(records), records=records, audit=dict(passed=True), exception=None)
    record["classification"] = r.classify(record)
    r.q.atomic_gzip_json(directory/"result.json.gz", record)
    for f,arm in records.items():
        r.q.atomic_gzip_json(directory/(f+"-native.json.gz"),dict(iteration=1,native=arm["native"]))
    r.archive_ac(directory,record)
    names = ["request.json", "result.json.gz", "ac-arm.json.gz", "ac-completion.json"]+[f+"-native.json.gz" for f in records]
    r.q.atomic_immutable_json(directory/"completion.json",dict(artifacts={n:r.q.digest(directory/n) for n in names}))


def test_archives_supervision_and_acceptance_count_independently(portable, monkeypatch, tmp_path):
    # Physical assessment mocked only for this infrastructure test, not the runner.
    monkeypatch.setattr(r, "checked", lambda *_:dict(passed=True))
    root = bound_root(tmp_path)
    directory = supervised(root)
    archive(root, directory)
    progress = r.status(root)
    assert progress["accepted_pairs"] == progress["finalized"] == progress["completion_manifests"] == 1
    assert progress["native_archives"] == progress["optimizer_calls"] == 2
    assert progress["ac_checkpoint_manifests"] == 1
    (directory/"supervision.json").unlink()
    progress = r.status(root)
    assert progress["accepted_pairs"] == progress["finalized"] == 0
    assert progress["completion_manifests"] == 1
    assert progress["optimizer_call_count_censored_pairs"] == 1
    assert progress["attempts"][0]["classification"] == "unfinished"


def test_one_sided_pair_is_never_accepted(portable, monkeypatch, tmp_path):
    monkeypatch.setattr(r, "checked", lambda *_:dict(passed=True))
    root = bound_root(tmp_path)
    directory = supervised(root)
    archive(root,directory,both=False)
    assert r.status(root)["accepted_pairs"] == 0


def test_manifest_native_hash_and_audit_replay_guard(portable, monkeypatch, tmp_path):
    monkeypatch.setattr(r, "checked", lambda *_:dict(passed=True))
    root = bound_root(tmp_path)
    directory = supervised(root)
    archive(root,directory)
    monkeypatch.setattr(r, "checked", lambda *_:dict(passed=False))
    with pytest.raises(ValueError,match="audit replay"):
        r.status(root)
    (directory/"ac-native.json.gz").write_bytes(b"corruption")
    with pytest.raises(ValueError,match="manifest/archive"):
        r.status(root)


def test_interrupted_second_solve_retains_full_ac_checkpoint(portable,monkeypatch,tmp_path):
    root = bound_root(tmp_path)
    directory = root/"call-001"
    directory.mkdir()
    r.q.atomic_immutable_json(directory/"request.json",r.request(root,1,180.))
    r.q.atomic_immutable_json(directory/"launch.json",dict(pid=os.getpid()))
    (directory/"worker.log").write_text("mock second solve interrupted\n")
    (directory/"resources.jsonl").write_text(json.dumps(dict(elapsed_seconds=.1,rss_mib=10.))+"\n")
    ac = SimpleNamespace(kind="ac",preparation_evidence=dict(native=dict(status=0)))
    socp = SimpleNamespace(kind="socp")
    monkeypatch.setattr(r,"construct",lambda *_:(None,ac,socp,None))
    monkeypatch.setattr(r,"evidence_record",lambda evidence:evidence)
    monkeypatch.setattr(r,"extract_results",lambda _:dict(objective=10.,status="optimal"))
    monkeypatch.setattr(r,"checked",lambda *_:dict(passed=True,containment="mock"))
    calls = []
    def solve(build,_settings):
        calls.append(build.kind)
        if build.kind == "socp":
            raise KeyboardInterrupt("mock wall limit during second solve")
        return dict(exception=None,solve_wall_including_canonicalization_s=.05)
    monkeypatch.setattr(r,"timed_solve",solve)
    with pytest.raises(KeyboardInterrupt):
        r.worker(root,1)
    assert calls == ["ac","socp"]
    checkpoint = r.q.read(directory/"ac-arm.json.gz")
    assert checkpoint["record"]["result"]["objective"] == 10.
    assert checkpoint["record"]["preparation_evidence"]["native"]["status"] == 0
    assert "warnings" in checkpoint["record"] and checkpoint["audit"]["passed"]
    assert not (directory/"completion.json").exists()
    assert r.status(root)["ac_checkpoint_manifests"] == 1
    r.q.atomic_immutable_json(directory/"supervision.json",dict(classification="wall_limit",returncode=-15,
        wall_seconds=180.1,peak_sampled_rss_mib=10.,samples=1))
    progress = r.status(root)
    assert progress["finalized"] == 1 and progress["accepted_pairs"] == 0
    assert progress["ac_checkpoint_manifests"] == 1 and progress["result_archives"] == 0
    assert progress["attempts"][0]["completed_ac"]["audit"]["passed"]


def test_parent_stops_after_timeout_without_retry(portable, monkeypatch, tmp_path):
    monkeypatch.setattr(r, "monitoring",lambda:dict(utc="mock"))
    calls = []
    def supervise(command,directory,root,req):
        calls.append(command)
        r.q.atomic_immutable_json(directory/"supervision.json",dict(classification="wall_limit",returncode=-15,
            wall_seconds=180.1,peak_sampled_rss_mib=0.,samples=0))
    monkeypatch.setattr(r.q,"supervise",supervise)
    root = tmp_path/"fresh"
    assert r.run(root,"a"*40)["outcome"] == "stopped_on_outcome"
    assert len(calls) == 1
    progress = r.q.read(root/"report.json")
    assert progress["accepted_pairs"] == 0 and progress["optimizer_call_count_censored_pairs"] == 1
    assert progress["optimizer_calls_upper_bound"] == 2


def test_fresh_clean_commit_required(portable,monkeypatch,tmp_path):
    monkeypatch.setattr(r,"monitoring",lambda:pytest.fail("preflight should fail first"))
    root = tmp_path/"fresh"
    with pytest.raises(ValueError,match="clean full commit"):
        r.run(root,"wrong")
    assert not root.exists()
    root.mkdir()
    with pytest.raises(FileExistsError,match="fresh"):
        r.run(root,"a"*40)
