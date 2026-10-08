"""E1 runner infrastructure and independent checks; no optimization calls."""

from copy import deepcopy
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

from cvxopf import OPFBuild, extract_results
from cvxopf._numerical_preparation import PreparationEvidence
from experiments.m11_socp import e1_compare as r
from tests.socp_reference_comparison import construct, compare


def candidate(name="two_bus", assembly="single", treatment="baseline"):
    _, ref, build = construct(name, assembly, treatment)
    # Assign the frozen external state, then use the normal public projection.
    # Its branch powers can differ from the fixture by external rounding;
    # the original independent physics oracle still checks those projections.
    for key in ("w", "W_re", "W_im", "Pg", "Qg"):
        value = np.asarray(ref["primal"][key])
        if key in ("Pg", "Qg"):
            value = value/build.data["baseMVA"]
        variable = build.variables[key]
        if isinstance(variable, list):
            variable[0].save_value(value)
        else:
            variable.save_value(value[:, None] if assembly == "vectorized" else value)
    result = r.serializable(extract_results(build))
    result.update(status="optimal", objective=ref["primal_objective"])
    return build, result


@pytest.fixture
def portable(monkeypatch):
    monkeypatch.setattr(OPFBuild, "solve", lambda *_a, **_k: pytest.fail("optimizer not authorized"))
    monkeypatch.setattr(r, "context", lambda: dict(head="a"*40, worktree_status="",
                      thread_environment={k: "1" for k in r.THREAD_KEYS}))


def test_declared_order_policies_and_settings(portable):
    arms = r.arms()
    assert len(arms) == 18 and [a["id"] for a in arms] == list(range(1, 19))
    assert [(a["case"], a["assembly"], a["treatment"]) for a in arms] == [
        (n, a, t) for n in ("two_bus", "case9", "case14")
        for a in ("single", "vectorized", "stepwise") for t in ("baseline", "prepared")]
    binding = r.frozen_binding()
    for a, b in zip(binding["rows"][::2], binding["rows"][1::2], strict=True):
        for key in ("input_sha256", "physical_policy", "reference_sha256", "reference_objective"):
            assert a[key] == b[key]
        assert not a["numerical_preparation"]["exact_fixed_boxes"]
        assert b["numerical_preparation"] == dict(normalize_device_limits=True, exact_fixed_boxes=True,
            canonical_scaling="joint5", cost_coordinates=False, objective_assembly="hourly")
    assert r.SOLVER_SETTINGS["time_limit"] == 20
    assert r.SOLVER_SETTINGS["max_iter"] == 200
    assert binding["resolved_settings"]["max_threads"] == 1


@pytest.mark.parametrize("assembly", ("single", "vectorized", "stepwise"))
@pytest.mark.parametrize("treatment", ("baseline", "prepared"))
@pytest.mark.parametrize("name", ("two_bus", "case9", "case14"))
def test_existing_external_primals_pass_shared_checks_without_solving(portable, name, assembly, treatment):
    build, result = candidate(name, assembly, treatment)
    checked = compare(name, assembly, build, result)
    assert checked["objective"] == pytest.approx(result["objective"], rel=1e-10, abs=1e-7)
    if name == "two_bus":
        assert checked["exact_product_recovery"] and checked["recovered_ac_feasible"]


@pytest.mark.parametrize("field", ("Pg", "branch_p_to", "W_im", "objective"))
def test_shared_gate_rejects_corruption(portable, field):
    build, result = candidate()
    if field == "objective":
        result[field] += 1
    else:
        result[field][0] += 1
    with pytest.raises(AssertionError):
        compare("two_bus", "single", build, result)


def native_fixture(row, result):
    """Encode an external primal in canonical coordinates, without a solver.

    Fill public coordinates directly and recover canonical auxiliary copies
    from their defining affine equalities. Zero duals are deliberate mock
    diagnostics, not claimed optimizer evidence.
    """
    arm = row["arm"]
    _, _, build = construct(arm["case"], arm["assembly"], arm["treatment"])
    data, _, inverse, mapping, D, R, offset = r.canonical_problem(build)
    full = np.zeros(data["c"].size)
    known = []
    for key, variables in build.variables.items():
        values = np.asarray(result[key])
        variables = variables if isinstance(variables, list) else [variables]
        for t, variable in enumerate(variables):
            start = inverse[-2].var_offsets[variable.id]
            indices = np.arange(start, start+variable.size)
            physical = values if arm["assembly"] == "single" else (
                values.T if arm["assembly"] == "vectorized" else values[t])
            if key in ("Pg", "Qg"):
                physical = physical/build.data["baseMVA"]
            full[indices] = physical.ravel(order="F")
            known.extend(indices)
    unknown = np.setdiff1d(np.arange(full.size), known)
    eq = data["A"][:data["dims"].zero, :]
    if unknown.size:
        full[unknown] = np.linalg.lstsq(eq[:, unknown].toarray(), data["b"][:data["dims"].zero]-eq @ full, rcond=None)[0]
    raw = SimpleNamespace(status="Solved", x=full[mapping.free]/D,
        s=R*(data["b"]-data["A"] @ full)[mapping.kept], z=np.zeros(R.size),
        obj_val=float(.5*full @ (data["P"] @ full)+data["c"] @ full)-offset,
        obj_val_dual=float(.5*full @ (data["P"] @ full)+data["c"] @ full)-offset,
        iterations=1, solve_time=.1)
    native = r.serializable(vars(raw) | dict(cvxpy_objective_offset=float(inverse[-1]["offset"])))
    evidence = None
    if row["arm"]["treatment"] == "prepared":
        _, checks = r.restore(data, mapping, raw, D, R, offset)
        evidence = r.evidence_record(PreparationEvidence(mapping, native, checks, D, R, offset))
    return native, evidence


@pytest.mark.parametrize("treatment", ("baseline", "prepared"))
def test_native_cost_and_preparation_checks(portable, treatment):
    row = r.row_binding(dict(id=1, case="two_bus", assembly="single", treatment=treatment))
    _, result = candidate(treatment=treatment)
    native, evidence = native_fixture(row, result)
    assert r.assess(row, result, native, evidence)["passed"]
    bad = dict(native, obj_val=native["obj_val"]+1.)
    changed = None if evidence is None else dict(evidence, native=bad)
    assert not r.assess(row, result, bad, changed)["passed"]
    assert not r.assess(row, result, dict(native, status="AlmostSolved"), evidence)["passed"]
    if evidence:
        bad = dict(evidence, coordinates=dict(fixed=[0], dropped=[0]))
        assert not r.assess(row, result, native, bad)["passed"]
        assert not r.assess(row, result, native, None)["passed"]


@pytest.mark.parametrize("assembly", ("single", "vectorized", "stepwise"))
@pytest.mark.parametrize("treatment", ("baseline", "prepared"))
@pytest.mark.parametrize("name", ("two_bus", "case9", "case14"))
def test_native_coordinates_restore_external_primal(portable, name, assembly, treatment):
    row = r.row_binding(dict(id=1, case=name, assembly=assembly, treatment=treatment))
    _, result = candidate(name, assembly, treatment)
    native, evidence = native_fixture(row, result)
    audit = r.assess(row, result, native, evidence)
    assert audit["passed"], audit
    native = deepcopy(native)
    native["x"][0] += 1.
    changed = None if evidence is None else dict(evidence, native=native)
    assert not r.assess(row, result, native, changed)["passed"]


@pytest.mark.parametrize("field", ("variable_scale", "row_scale", "coordinates", "checks", "objective_offset"))
def test_replay_rejects_changed_preparation_maps_and_scales(portable, field):
    row = r.row_binding(dict(id=1, case="two_bus", assembly="single", treatment="prepared"))
    _, result = candidate(treatment="prepared")
    native, evidence = native_fixture(row, result)
    changed = deepcopy(evidence)
    if field in ("variable_scale", "row_scale"):
        changed[field][0] *= 2
    elif field == "coordinates":
        changed[field]["full_size"] += 1
    elif field == "checks":
        changed[field]["primal_residual"] += 1
    else:
        changed[field] += 1
    assert not r.assess(row, result, native, changed)["passed"]


def test_cli_requires_mode_and_preflight_creates_no_output(portable, monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("sys.argv", ["e1_compare"])
    with pytest.raises(SystemExit):
        r.main()
    root = tmp_path / "fresh"
    monkeypatch.setattr("sys.argv", ["e1_compare", "--preflight", "--output", str(root)])
    r.main()
    assert len(json.loads(capsys.readouterr().out)["rows"]) == 18
    assert not root.exists()


def test_run_requires_fresh_clean_full_commit(portable, monkeypatch, tmp_path):
    root = tmp_path / "fresh"
    monkeypatch.setattr(r, "monitoring", lambda: pytest.fail("must fail before monitoring"))
    with pytest.raises(ValueError, match="clean full commit"):
        r.run(root, "wrong")
    assert not root.exists()
    root.mkdir()
    with pytest.raises(FileExistsError, match="fresh"):
        r.run(root, "a"*40)


def bound_root(tmp_path):
    root = tmp_path / "e1"
    root.mkdir()
    r.q.atomic_immutable_json(root / "binding.json", r.frozen_binding())
    r.q.atomic_immutable_json(root / "protocol.json", r.protocol())
    return root


def finished_call(root, number=1, classification="exited"):
    directory = root / f"call-{number:03d}"
    directory.mkdir()
    req = r.request(root, number, 30.)
    r.q.atomic_immutable_json(directory / "request.json", req)
    r.q.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    (directory / "worker.log").write_text("mock worker\n")
    (directory / "resources.jsonl").write_text(json.dumps(dict(elapsed_seconds=.1, rss_mib=10.))+"\n")
    r.q.atomic_immutable_json(directory / "supervision.json", dict(classification=classification, returncode=0,
        wall_seconds=.2, peak_sampled_rss_mib=10., samples=1))
    return directory


def archive(root, directory, number=1):
    row = r.q.read(root / "binding.json")["rows"][number-1]
    _, result = candidate(row["arm"]["case"], row["arm"]["assembly"], row["arm"]["treatment"])
    native, evidence = native_fixture(row, result)
    record = dict(iteration=number, call_id=number, row=row, request=r.q.read(directory / "request.json"), optimizer_calls=1,
        exception=None, result=result, native=native, preparation_evidence=evidence,
        audit=r.assess(row, result, native, evidence), classification="accepted")
    r.q.atomic_gzip_json(directory / "result.json.gz", r.serializable(record))
    r.q.atomic_gzip_json(directory / "native.json.gz", dict(iteration=number, native=native))
    r.q.atomic_immutable_json(directory / "completion.json", dict(artifacts={n:r.q.digest(directory / n)
        for n in ("request.json", "result.json.gz", "native.json.gz")}))


def test_replay_counts_and_hashes_are_independent(portable, tmp_path):
    root = bound_root(tmp_path)
    directory = finished_call(root)
    archive(root, directory)
    report = r.status(root)
    assert report["accepted"] == report["finalized"] == report["result_archives"] == report["native_archives"] == 1
    assert report["attempts"][0]["audit"]["passed"]
    record = r.q.read(directory / "result.json.gz")
    record["audit"]["native_objective_error"] = 1.
    (directory / "result.json.gz").unlink()
    r.q.atomic_gzip_json(directory / "result.json.gz", record)
    completion = r.q.read(directory / "completion.json")
    completion["artifacts"]["result.json.gz"] = r.q.digest(directory / "result.json.gz")
    r.q.atomic_json(directory / "completion.json", completion)
    with pytest.raises(ValueError, match="audit replay"):
        r.status(root)


def test_no_acceptance_without_supervision(portable, tmp_path):
    root = bound_root(tmp_path)
    directory = root / "call-001"
    directory.mkdir()
    r.q.atomic_immutable_json(directory / "request.json", r.request(root, 1, 30.))
    assert r.status(root)["attempts"][0]["classification"] == "unfinished"
    other = root / "call-002"
    other.mkdir()
    r.q.atomic_immutable_json(other / "request.json", r.request(root, 2, 30.))
    with pytest.raises(ValueError, match="unfinished"):
        r.status(root)


def test_binding_and_manifest_corruption_fail(portable, tmp_path):
    root = bound_root(tmp_path)
    directory = finished_call(root)
    archive(root, directory)
    (directory / "native.json.gz").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="manifest/archive"):
        r.status(root)
    binding = r.q.read(root / "binding.json")
    binding["rows"][1]["input_sha256"] = "bad"
    r.q.atomic_json(root / "binding.json", binding)
    with pytest.raises(ValueError, match="binding changed"):
        r.status(root)


def test_parent_stops_after_timeout_without_retry(portable, monkeypatch, tmp_path):
    monkeypatch.setattr(r, "monitoring", lambda: dict(utc="mock"))
    calls = []
    def supervise(command, directory, root, req):
        calls.append(command)
        r.q.atomic_immutable_json(directory / "supervision.json", dict(classification="wall_limit", returncode=-15,
            wall_seconds=30.1, peak_sampled_rss_mib=0., samples=0))
    monkeypatch.setattr(r.q, "supervise", supervise)
    root = tmp_path / "fresh"
    assert r.run(root, "a"*40)["outcome"] == "stopped_on_outcome"
    assert len(calls) == 1
    assert r.status(root)["finalized"] == 1 and r.status(root)["accepted"] == 0
    assert r.q.read(root / "invocation-finish.json")["outcome"] == "stopped_on_outcome"


def test_native_collection_preserves_evidence_and_offsets(portable):
    row = r.row_binding(dict(id=1, case="two_bus", assembly="single", treatment="baseline"))
    raw = SimpleNamespace(**{k: [] for k in ("x", "s", "z")}, obj_val=1., obj_val_dual=1.,
                          r_prim=0., r_dual=0., iterations=1, solve_time=.1, status="Solved")
    build = SimpleNamespace(preparation_evidence=None,
        prob=SimpleNamespace(_solver_cache={"CLARABEL": SimpleNamespace(get_solution=lambda:raw)}))
    native, evidence = r.native_record(build, row)
    assert native["status"] == "Solved" and evidence is None
    assert native["cvxpy_objective_offset"] == row["canonical"]["objective_offset"]
    row = deepcopy(row)
    row["arm"]["treatment"] = "prepared"
    with pytest.raises(ValueError, match="lacks preparation"):
        r.native_record(build, row)


def test_worker_refuses_unsupervised_launch_before_solve(portable, tmp_path):
    root = bound_root(tmp_path)
    directory = root / "call-001"
    directory.mkdir()
    r.q.atomic_immutable_json(directory / "request.json", r.request(root, 1, 30.))
    r.q.atomic_immutable_json(directory / "launch.json", dict(pid=-1))
    r.worker(root, 1)
    record = r.q.read(directory / "result.json.gz")
    assert record["optimizer_calls"] == 0 and record["classification"] == "exception"
    assert "launch/request" in record["exception"]


@pytest.mark.parametrize("number", (1, 2))
def test_worker_archive_round_trip_without_optimization(portable, monkeypatch, tmp_path, number):
    root = bound_root(tmp_path)
    if number == 2:
        archive(root, finished_call(root))
    row = r.q.read(root / "binding.json")["rows"][number-1]
    _, result = candidate(treatment=row["arm"]["treatment"])
    calls = []
    monkeypatch.setattr(OPFBuild, "solve", lambda _b, **options: calls.append(options))
    monkeypatch.setattr(r, "extract_results", lambda _b: result)
    monkeypatch.setattr(r, "native_record", lambda _b, actual: native_fixture(actual, result))
    directory = root / f"call-{number:03d}"
    directory.mkdir()
    r.q.atomic_immutable_json(directory / "request.json", r.request(root, number, 30.))
    r.q.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    (directory / "worker.log").write_text("mock worker\n")
    (directory / "resources.jsonl").write_text(json.dumps(dict(elapsed_seconds=.1, rss_mib=10.))+"\n")
    r.worker(root, number)
    assert calls == [r.SOLVER_SETTINGS]
    r.q.atomic_immutable_json(directory / "supervision.json", dict(classification="exited", returncode=0,
        wall_seconds=.2, peak_sampled_rss_mib=10., samples=1))
    report = r.status(root)
    assert report["accepted"] == report["finalized"] == report["completion_manifests"] == number
    assert r.q.read(directory / "phase.json")["phase"] == "complete"
