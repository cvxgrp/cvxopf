"""D1 physics, initialization and journal tests; never run native IPOPT."""

from dataclasses import replace
import sys

import numpy as np
import pandas as pd
import pytest

from cvxopf import (
    Load,
    NondispatchableUnit,
    StorageUnitIdeal,
    build_opf_multistep,
    extract_results,
    gen_from_matpower,
)
from cvxopf.testcases import case9
from cvxopf._ac_start_mapping import stepwise_values
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.case118_tracy_2021 import run_stage_d as runner, stage_d as model
from experiments.case118_tracy_2021.stage_b import audit_result


def fixture(w=3):
    case = case9()
    case["branch"][:, 4] = 0  # No charging: flat voltages give exact zero flow.
    generators = [
        replace(g, p_min_mw=0, p_max_mw=100, cost_coeffs=(2, 1, 0.01))
        for g in gen_from_matpower(case["gen"], case["gencost"])
    ]
    kwargs = dict(
        case=case,
        T=w,
        delta=1.0,
        temporal_assembly="vectorized",
        formulation="ac",
        generators=generators,
        storage=[
            StorageUnitIdeal(
                bus=1,
                capacity=10,
                initial_soc=5,
                terminal_soc=5,
                terminal_constraint="equality",
                apparent_power_rating=4,
                device_id="battery",
            )
        ],
        loads=[
            Load(
                bus=1,
                p_load_mw=10,
                q_load_mvar=2,
                device_id="load",
                shedding_cost_per_mwh=100,
            )
        ],
        nondispatchable=[
            NondispatchableUnit(
                bus=1, device_id="solar", p_available=3, apparent_power_rating=4
            )
        ],
        df_load_p=pd.DataFrame({"load": [10.0] * w}),
        df_load_q=pd.DataFrame({"load": [2.0] * w}),
        df_nd=pd.DataFrame({"solar": [3.0] * w}),
    )
    build = build_opf_multistep(**kwargs)
    for variable in build.prob.variables():
        variable.value = variable.project(np.zeros(variable.shape))
    build.variables["v"].value = np.ones(build.variables["v"].shape)
    build.variables["Pg"].value = np.tile([[0.07], [0], [0]], (1, w))
    build.variables["Qg"].value = np.tile([[0.018], [0], [0]], (1, w))
    build.variables["soc"].value = np.full((1, w + 1), 5.0)
    build.variables["p_nd"].value = np.full((1, w), 2.0)
    build.variables["load_shed_fraction"].value = np.full((1, w), 0.1)
    build.prob._status = "optimal"
    build.prob._value = float(build.prob.objective.value)
    result = extract_results(build)
    named = {
        k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")
    }
    return kwargs, build, result, named


@pytest.mark.parametrize("w", [1, 3, 6, 12])
def test_analytic_ac_audit(w):
    kwargs, _, result, named = fixture(w)
    audit = audit_result(result, kwargs, named)
    assert audit["passed"], audit
    result["status"] = "optimal_inaccurate"
    assert audit_result(result, kwargs, named)["passed"]
    result["status"] = "user_limit"
    assert not audit_result(result, kwargs, named)["passed"]


@pytest.mark.parametrize(
    "key",
    [
        "Pg",
        "Qg",
        "b",
        "b_q",
        "soc",
        "p_nd",
        "q_nd",
        "Vm",
        "Va_deg",
        "p_net",
        "q_net",
        "branch_p_from",
        "branch_q_to",
        "branch_s_from",
        "q_load_shed",
        "q_load_served",
        "load_shed_fraction",
        "objective",
    ],
)
def test_ac_corruptions_rejected(key):
    kwargs, _, result, named = fixture()
    if key == "Va_deg":
        result[key][:, 0] += 1  # A uniform phase rotation is physically equivalent.
    else:
        result[key] = np.asarray(result[key]) + 1
    assert not audit_result(result, kwargs, named)["passed"]


@pytest.mark.parametrize("w", [1, 3, 6, 12])
def test_shift_perturb_and_fixed_initial_boundary(tmp_path, w):
    kwargs, build, _, _ = fixture(w)
    kwargs["options"] = __import__("cvxopf").OPFOptions()
    source = dict(
        logical_solution=model.jsonable(
            stepwise_values(build, model.starts._solution_values(build))
        )
    )
    path = tmp_path / "preceding.json"
    atomic_immutable_json(path, source)
    request = dict(
        role="primary",
        previous=model.reference(path, tmp_path),
        causal_source=None,
        storage_device_ids=["battery"],
        initial_soc_mwh=[5.0],
        global_hour=100,
    )
    causal, assigned = model.prepare_start(build, kwargs, request, tmp_path)
    assert assigned["soc"][0, 0] == 5
    assert assigned["b"][0, -1] == 0
    np.testing.assert_allclose(assigned["soc"], 5)
    path = tmp_path / "start.json"
    atomic_immutable_json(path, model.jsonable(dict(causal_start=causal)))
    request.update(role="causal_1", causal_source=model.reference(path, tmp_path))
    _, perturbed = model.prepare_start(build, kwargs, request, tmp_path)
    _, repeated = model.prepare_start(build, kwargs, request, tmp_path)
    for name in perturbed:
        np.testing.assert_array_equal(perturbed[name], repeated[name])
    assert perturbed["soc"][0, 0] == 5


def tiny_study():
    return dict(
        storage_device_ids=["battery"],
        trajectories=[
            dict(id=0, start=100, W=3, initial_soc_mwh=[5.0], targets=[[5.0]] * 6)
        ],
    )


def fake_verify(directory, request):
    return model.read(directory / "result.json.gz")


def append_attempt(root, progress, *, accepted=False, outcome="exited"):
    upcoming = progress["next"]
    directory = root / upcoming["directory"]
    directory.mkdir(parents=True)
    atomic_immutable_json(directory / "request.json", upcoming["request"])
    atomic_immutable_json(
        directory / "supervision.json",
        dict(classification=outcome, returncode=0, wall_seconds=10),
    )
    atomic_immutable_json(directory / "start.json", dict(causal_start={}))
    model.atomic_gzip_json(
        directory / "result.json.gz",
        dict(iteration=100, accepted=accepted, next_soc_mwh=[4.0]),
    )
    return directory


def test_restart_from_journal_without_progress_cursor(tmp_path):
    study = tiny_study()
    first = runner.reconstruct(tmp_path, study, fake_verify)
    append_attempt(tmp_path, first, accepted=True)
    resumed = runner.reconstruct(tmp_path, study, fake_verify)
    assert resumed["completed_hours"] == 1
    assert resumed["next"]["request"]["initial_soc_mwh"] == [4.0]
    assert resumed["next"]["request"]["previous"] is not None
    for _ in range(5):
        append_attempt(tmp_path, resumed, accepted=True)
        resumed = runner.reconstruct(tmp_path, study, fake_verify)
    assert resumed["complete"] and resumed["next"] is None
    assert resumed["observed_attempt_wall_seconds"] == 60


def test_interrupt_retries_same_slot_new_id(tmp_path):
    study = tiny_study()
    before = runner.reconstruct(tmp_path, study, fake_verify)
    append_attempt(tmp_path, before, outcome="interrupted")
    after = runner.reconstruct(tmp_path, study, fake_verify)
    assert before["next"]["request"] == after["next"]["request"]
    assert after["next"]["directory"].endswith("attempt-001")
    assert after["completed_hours"] == 0


def test_completed_archive_survives_interruption_before_cursor(tmp_path):
    study = tiny_study()
    before = runner.reconstruct(tmp_path, study, fake_verify)
    directory = append_attempt(tmp_path, before, accepted=True, outcome="interrupted")
    atomic_immutable_json(directory / "completion.json", {"synthetic": True})
    after = runner.reconstruct(tmp_path, study, fake_verify)
    assert after["completed_hours"] == 1
    assert after["next"]["request"]["initial_soc_mwh"] == [4.0]


def test_recovery_order_and_target_free_not_controlling(tmp_path):
    study = tiny_study()
    for role in ("primary", "causal_1", "causal_2", "causal_3", "flat", "target_free"):
        progress = runner.reconstruct(tmp_path, study, fake_verify)
        assert progress["next"]["request"]["role"] == role
        append_attempt(tmp_path, progress, accepted=role == "target_free")
    after = runner.reconstruct(tmp_path, study, fake_verify)
    assert after["completed_hours"] == 0
    assert after["next"]["request"]["role"] == "copied_target_free"
    assert after["next"]["request"]["target_free_source"] is not None


@pytest.mark.parametrize("w", [1, 3, 6, 12])
def test_flat_recovery_preserves_original_causal_source(tmp_path, w):
    kwargs, build, _, _ = fixture(w)
    expected = model.starts._complete_start(build)
    causal = {name: values.copy() for name, values in expected.items()}
    causal["theta"] = causal["theta"] + 0.02
    path = tmp_path / "causal.json"
    atomic_immutable_json(path, model.jsonable(dict(causal_start=causal)))
    request = dict(role="flat", causal_source=model.reference(path, tmp_path))
    retained, assigned = model.prepare_start(build, kwargs, request, tmp_path)
    for name in expected:
        np.testing.assert_array_equal(assigned[name], expected[name])
        np.testing.assert_array_equal(retained[name], causal[name])
    assert assigned["soc"][0, 0] == kwargs["storage"][0].initial_soc
    request["role"] = "target_free"
    _, subsequent = model.prepare_start(build, kwargs, request, tmp_path)
    np.testing.assert_array_equal(subsequent["theta"], causal["theta"])


def test_accepted_flat_recovery_advances_without_target_free(tmp_path):
    study = tiny_study()
    for role in ("primary", "causal_1", "causal_2", "causal_3", "flat"):
        progress = runner.reconstruct(tmp_path, study, fake_verify)
        assert progress["next"]["request"]["role"] == role
        append_attempt(tmp_path, progress, accepted=role == "flat")
    progress = runner.reconstruct(tmp_path, study, fake_verify)
    assert progress["completed_hours"] == 1
    assert progress["next"]["request"]["role"] == "primary"
    assert progress["next"]["request"]["initial_soc_mwh"] == [4.0]


def test_exhausted_recovery_continues_next_trajectory(tmp_path):
    study = tiny_study()
    study["trajectories"].append({**study["trajectories"][0], "id": 1})
    for _ in range(6):
        append_attempt(tmp_path, runner.reconstruct(tmp_path, study, fake_verify))
    after = runner.reconstruct(tmp_path, study, fake_verify)
    assert after["trajectories"][0]["classification"] == "incomplete"
    assert after["next"]["request"]["trajectory"] == 1


def test_supervisor_no_time_limit_and_rss_stop(tmp_path):
    normal = tmp_path / "normal"
    normal.mkdir()
    r = runner.supervise(
        [sys.executable, "-c", "import time; time.sleep(.1)"],
        normal,
        tmp_path,
        rss_reader=lambda pid: 1.0,
        poll_seconds=0.01,
    )
    assert r["classification"] == "exited" and r["returncode"] == 0
    limited = tmp_path / "limited"
    limited.mkdir()
    r = runner.supervise(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        limited,
        tmp_path,
        rss_reader=lambda pid: 20000.0,
        poll_seconds=0.01,
    )
    assert r["classification"] == "rss_limit" and r["returncode"] is not None


@pytest.mark.parametrize("w", [1, 3, 6, 12])
def test_worker_captures_actual_x0_before_mocked_solver_exception(
    tmp_path, monkeypatch, w
):
    kwargs, _, _, _ = fixture(w)
    request = dict(
        global_hour=0,
        W=w,
        role="primary",
        previous=None,
        causal_source=None,
        initial_soc_mwh=[5.0],
        storage_device_ids=["battery"],
    )
    atomic_immutable_json(tmp_path / "binding.json", dict(context={}))
    atomic_immutable_json(tmp_path / "request.json", request)
    monkeypatch.setattr(model, "context", lambda: {})
    monkeypatch.setattr(model, "verified_inputs", lambda: None)
    monkeypatch.setattr(model, "request_kwargs", lambda p, r: kwargs)
    called = []

    def fail(*args, **kw):
        called.append(True)
        raise RuntimeError("synthetic native failure; no IPOPT executed")

    monkeypatch.setattr(model.starts.IPOPT, "solve_via_data", fail)
    model.worker(tmp_path, tmp_path)
    assert called == [True]
    assert (tmp_path / "x0.json.gz").exists()
    assert not model.read(tmp_path / "result.json.gz")["accepted"]
    x0 = model.read(tmp_path / "x0.json.gz")
    assert (
        len(x0["complete_x0"])
        == x0["model_coordinate_count"] + x0["auxiliary_coordinate_count"]
    )
