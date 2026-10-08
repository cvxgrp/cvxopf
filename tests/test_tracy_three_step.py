"""Non-solving tests for matched inputs, variable maps, and execution gates."""

from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from cvxopf import extract_results
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.numerical_preparation import fixture as f
from experiments.numerical_preparation import tracy_variables as v
from experiments.numerical_preparation import run_tracy_three_step as r


def synthetic(formulation):
    kwargs = f.case9_kwargs(3, formulation)
    call = replace(v.CALLS[0], formulation=formulation, source="case9")
    build = f.build_for_call(call, kwargs)
    data, layout = v.canonical_layout(build)
    full = np.zeros(data["c"].size)
    for item in layout:
        variable = build.variables[item["name"]]
        value = np.arange(variable.size, dtype=float).reshape(variable.shape, order="F") / 100
        if item["name"] == "soc":
            value[:, 0] = [u.initial_soc for u in kwargs["storage"]]
        variable.save_value(value)
        full[item["start"]:item["stop"]] = value.ravel(order="F")
    # Assigning a synthetic public status is not a solver call or feasibility
    # assertion. These values intentionally need not satisfy the model.
    build.prob._status, build.prob._value = "optimal", 0.
    record = dict(native=dict(x=full), preparation_evidence=None)
    coordinates = v.capture_coordinates(build, record)
    result = extract_results(build)
    return kwargs, coordinates, result


def test_exact_three_call_scope():
    assert [(c.formulation, c.treatment, c.T, c.start, c.stop, c.leg) for c in v.CALLS] == [
        (form, treatment, 3, 1165, 1168, "energy_neutral") for form, treatment in
        (("socp", "prepared_socp"), ("lossy_dc", "prepared_dc"), ("singlenode_dc", "prepared_dc"))]
    assert r.LIMITS == dict(max_launches=3, wall_seconds=180., rss_mib=16384., total_worker_seconds=540., poll_seconds=1.)
    a = dict(formulation="ac", T=3, terminal=1168, cost=0.01)
    assert v.common_inputs(a) == v.common_inputs(dict(a, formulation="socp"))
    assert v.common_inputs(a) != v.common_inputs(dict(a, terminal=1189))
    assert v.common_inputs(a) != v.common_inputs(dict(a, cost=0.02))


@pytest.mark.parametrize("formulation", ["socp", "lossy_dc", "singlenode_dc"])
def test_all_convex_original_leaves_and_engineering_projections(formulation):
    kwargs, coordinates, result = synthetic(formulation)
    checks = v.check_coordinates(coordinates, result, kwargs)
    assert checks["passed"], checks
    assert set(checks["canonical_to_restored"]) == {x["name"] for x in coordinates["layout"]}
    if formulation == "singlenode_dc":
        assert np.shape(result["p_net"]) == (3,)
    if formulation != "socp":
        assert result.get("Qg") is None and checks["canonical_to_public"]["absent_Qg"]["passed"]


@pytest.mark.parametrize("field,change", [
    ("Pg", "unit"), ("b", "sign"), ("p_nd", "transpose"),
    ("soc", "boundary"), ("Pg", "nonfinite"), ("load_shed_fraction", "missing"),
])
def test_projection_failures_are_not_hidden(field, change):
    kwargs, coordinates, result = synthetic("socp")
    if change == "unit":
        result[field] = result[field] / kwargs["case"]["baseMVA"]
    elif change == "sign":
        result[field] = -result[field]
    elif change == "transpose":
        result[field] = result[field].T
    elif change == "boundary":
        result[field] = np.vstack((result[field][0], result[field]))
    elif change == "nonfinite":
        result[field][0, 0] = np.nan
    else:
        del result[field]
    assert not v.check_coordinates(coordinates, result, kwargs)["passed"]


def test_raw_and_initial_boundary_corruption_fail():
    kwargs, coordinates, result = synthetic("socp")
    corrupted = deepcopy(coordinates)
    corrupted["raw"]["b"][0][0] += 1
    assert not v.check_coordinates(corrupted, result, kwargs)["passed"]
    corrupted = deepcopy(coordinates)
    item = next(x for x in corrupted["layout"] if x["name"] == "soc")
    corrupted["full_x"][item["start"]] += 1
    assert not v.check_coordinates(corrupted, result, kwargs)["passed"]


def test_fixed_and_scaled_canonical_restoration():
    evidence = dict(coordinates=dict(full_size=4, fixed=[1, 3], values=[10, 30]), variable_scale=[2, .5])
    np.testing.assert_array_equal(v.expand_primal(dict(x=[4, 6]), evidence), [8, 10, 3, 30])
    for corrupt in (dict(variable_scale=[0, .5]), dict(variable_scale=[2]),
                    dict(coordinates=dict(full_size=4, fixed=[1, 1], values=[10, 30]))):
        with pytest.raises(ValueError):
            v.expand_primal(dict(x=[4, 6]), evidence | corrupt)
    with pytest.raises(ValueError):
        v.expand_primal(dict(x=[np.inf]), None)


def test_complete_device_deltas_are_descriptive_and_absent_q_not_zero():
    kwargs, _, result = synthetic("socp")
    other = deepcopy(result)
    other["Pg"] += 5
    other["Qg"] = None
    report = v.compare({"a": dict(result=result), "b": dict(result=other)}, kwargs)
    pair = report["pairwise"]["a__b"]
    assert pair["Pg"]["maximum_absolute_delta"] == 5
    np.testing.assert_allclose(pair["Pg"]["left_minus_right"], -np.ones((3, 3))*5, atol=1e-14, rtol=0)
    assert "passed" not in pair["Pg"]
    assert not pair["Qg"]["available"]
    assert report["axes"]["storage"][0]["device_id"] == "battery9"
    assert report["axis_checks"]["a"]["Pg"]["passed"]


def setup_status(monkeypatch, tmp_path):
    binding = dict(calls=[dict(mathematical_input_sha256="inputs")]*3, context={}, limits=r.LIMITS)
    atomic_immutable_json(tmp_path / "binding.json", binding)
    atomic_immutable_json(tmp_path / "protocol.json", dict(protocol=r.LIMITS))
    kwargs = f.case9_kwargs(3, "singlenode_dc")
    monkeypatch.setattr(r, "frozen_binding", lambda: binding)
    monkeypatch.setattr(r, "historical_candidates", lambda: ({}, {}))
    monkeypatch.setattr(f.e3, "verified_inputs", lambda: None)
    monkeypatch.setattr(f, "kwargs_for_call", lambda *args: kwargs)
    return binding


def test_unsupervised_archive_is_unfinished_not_accepted(monkeypatch, tmp_path):
    setup_status(monkeypatch, tmp_path)
    directory = tmp_path / "call-001"
    directory.mkdir()
    atomic_immutable_json(directory / "completion.json", dict(classification="accepted"))
    report = r.status(tmp_path)
    assert report["progress"]["accepted"] == report["progress"]["disposed"] == 0
    assert report["progress"]["attempts"][0]["classification"] == "unfinished"
    (tmp_path / "call-002").mkdir()
    with pytest.raises(ValueError, match="unsupervised"):
        r.status(tmp_path)


def test_noncontiguous_and_launch_ceiling_fail(monkeypatch, tmp_path):
    setup_status(monkeypatch, tmp_path)
    (tmp_path / "call-002").mkdir()
    with pytest.raises(ValueError, match="noncontiguous"):
        r.status(tmp_path)
    for name in ("call-003", "call-004", "call-005"):
        (tmp_path / name).mkdir()
    with pytest.raises(ValueError, match="launch ceiling"):
        r.status(tmp_path)


@pytest.mark.parametrize("clean,commit", [(False, "a"*40), (True, "b"*40), (True, None)])
def test_no_execution_directory_before_clean_committed_gate(monkeypatch, tmp_path, clean, commit):
    monkeypatch.setattr(r, "OUTPUT", tmp_path / "absent")
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=clean, commit="a"*40,
        thread_environment={k: "1" for k in r.THREAD_KEYS})))
    with pytest.raises(ValueError, match="clean full commit"):
        r.run(commit)
    assert not r.OUTPUT.exists()


def test_power_permission_preflight_failure_creates_nothing(monkeypatch, tmp_path):
    monkeypatch.setattr(r, "OUTPUT", tmp_path / "absent")
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=True, commit="a"*40,
        thread_environment={k: "1" for k in r.THREAD_KEYS})))
    def unavailable():
        raise RuntimeError("battery")
    monkeypatch.setattr(r.q, "monitoring_preflight", unavailable)
    with pytest.raises(RuntimeError, match="battery"):
        r.run("a"*40)
    assert not r.OUTPUT.exists()


def test_historical_manifest_mismatch_fails_without_solver(monkeypatch, tmp_path):
    monkeypatch.setattr(f, "OUTPUT", tmp_path)
    atomic_immutable_json(tmp_path / "binding.json", dict(calls=[]))
    monkeypatch.setattr(v, "HISTORICAL_BINDING_SHA", r.q.digest(tmp_path / "binding.json"))
    directory = tmp_path / "call-024"
    atomic_immutable_json(directory / "completion.json", dict(artifacts={"result.json.gz": "wrong"}))
    atomic_immutable_json(directory / "supervision.json", dict(classification="exited", returncode=0))
    with pytest.raises(ValueError, match="pinned completed"):
        v.historical_candidates()
