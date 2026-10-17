"""Non-solving algebra, input witnesses, lifecycle and economic-gate regressions."""

from dataclasses import asdict, replace
import os
from types import SimpleNamespace

import numpy as np
import pytest
from cvxpy.reductions.solvers.nlp_solvers.nlp_solver import Oracles

from experiments.ac_cost_coordinates import model as m
from experiments.numerical_preparation import fixture as original, run_qualification as q
from experiments.numerical_preparation.audit import serializable
from experiments.ac_cost_qualification import fixture as f, run as r, audit as a, analyze as analysis


def small_fixture(mode="both", delta=1.):
    call = original.Call(1, "ac", "combined_ac", "case9", 3)
    kwargs = original.kwargs_for_call(call)
    kwargs["delta"] = delta
    build = original.build_for_call(call, kwargs)
    start = q.physical_start(build, kwargs)
    components = f.MODES[mode]
    view = m.transform(build, delta, bool(components), components=components or f.MODES["both"])
    return call, kwargs, view, start


@pytest.mark.parametrize("mode", list(f.MODES))
@pytest.mark.parametrize("delta", [.5, 1., 2.])
def test_ablation_preserves_physical_objective_constraints_and_inverse(mode, delta):
    _, _, view, start = small_fixture(mode, delta)
    start["b"] = np.array([[-2., 0., 3.]])
    start["load_shed_fraction"] = np.arange(9).reshape(3, 3)/20
    assert m.equivalence(view, start)["passed"]
    assert set(view.scales) == set(f.MODES[mode])
    view.restore()
    for variable in view.physical.prob.variables():
        np.testing.assert_allclose(variable.value, start[variable.name()], atol=1e-13)


def test_declared_matrix_covers_ablations_starts_and_tracy_stress_horizons():
    arms = f.arms()
    assert [a.id for a in arms] == list(range(1, 23))
    assert [a.mode for a in arms[:4]] == list(f.MODES)
    assert {a.initialization for a in arms} == {"stock", "historical_unprepared", "historical_prepared"}
    assert {a.T for a in arms if a.forced_shedding} == {3, 6, 24}
    assert all(a.dataset == "tracy" for a in arms if a.forced_shedding)
    assert f.LIMITS["total_worker_seconds"] == 22*180


@pytest.mark.parametrize("T", [3, 6, 24])
def test_tracy_variant_has_input_only_shortage_proof_and_preserves_economics(monkeypatch, T):
    # Unit tests do not require ignored historical data. Supply a temporal
    # device-bearing fixture through the exact same Tracy assembly interface.
    base = original.case9_kwargs(3, "ac")
    for name in ("df_load_p", "df_load_q", "df_nd"):
        frame = base[name]
        base[name] = frame.iloc[np.arange(T) % 3].reset_index(drop=True)
    base["T"] = T
    monkeypatch.setattr(original, "kwargs_for_call", lambda *_: dict(base))
    arm = f.Arm(17, "tracy", T, 1165, "stock", "both", True)
    _, kwargs, stress = f.kwargs_for_arm(arm)
    assert stress["demand_multiplier"] > 1
    assert min(stress["modified"]["minimum_shed_mw"]) > 0
    assert stress["modified"]["minimum_ens_mwh"] > f.GATES["forced_shedding_energy_mwh"]
    for name in ("df_load_p", "df_load_q"):
        np.testing.assert_array_equal(kwargs[name], base[name] * stress["demand_multiplier"])
    for name in ("case", "storage", "generators", "nondispatchable", "loads", "df_nd", "options"):
        assert kwargs[name] is base[name]
    assert stress == f.kwargs_for_arm(replace(arm, mode="original"))[2]


def test_shortage_witness_rejects_nonpassive_network():
    kwargs = original.case9_kwargs(3, "ac")
    kwargs["case"]["branch"][0, 2] = -.01
    with pytest.raises(ValueError, match="shortage witness"):
        f.shortage(kwargs)


def test_historical_start_keeps_free_values_but_resets_exact_inputs(monkeypatch):
    call, kwargs, view, start = small_fixture()
    start["b"][:] = 2.
    start["soc"][:, 0] += .001
    start["Pg"][1, :] += .001
    monkeypatch.setattr(original, "kwargs_for_call", lambda *_: dict(kwargs))
    monkeypatch.setattr(f.diagnostic, "historical_point", lambda _: start)
    arm = f.Arm(5, "tracy", 3, 1165, "historical_unprepared", "both")
    _, _, other, assigned, _, _ = f.construct(arm)
    np.testing.assert_array_equal(assigned["b"], start["b"])
    np.testing.assert_array_equal(assigned["soc"][:, 0], [s.initial_soc for s in kwargs["storage"]])
    np.testing.assert_array_equal(assigned["Pg"][1, :], np.repeat(kwargs["generators"][1].p_min_mw/kwargs["case"]["baseMVA"], 3))
    assert m.equivalence(other, assigned)["passed"]


def canonical_record(view, start, slack):
    view.assign(start)
    data, inverse = m.canonical_data(view.solver)
    originals = {v.id for v in view.solver.prob.variables()}
    layout, x = [], np.asarray(data["x0"]).copy()
    for variable in data["problem"].variables():
        offset = inverse[-1].var_offsets[variable.id]
        item = dict(name=variable.name(), shape=list(variable.shape), start=offset, stop=offset+variable.size,
                    is_original_variable=variable.id in originals)
        layout.append(item)
        if not item["is_original_variable"] and variable.shape == view.physical.variables["b"].shape:
            weights = (np.ones(variable.shape) if "b" in view.scales else
                       np.broadcast_to(view.physical.data["storage_delta"] *
                                       np.asarray(view.physical.data["storage_aging_weight"])[:, None], variable.shape))
            actual = abs(view.leaves["b"].value if "b" in view.scales else view.physical.variables["b"].value)
            x[offset:offset+variable.size] = (actual + slack/weights).ravel(order="F")
    oracle = Oracles(data["_bounds"].new_problem, verbose=False, use_hessian=True)
    value = float(oracle.objective(x))
    return dict(status=0, x=x, obj_val=value), serializable(dict(iteration=1, complete_x0=data["x0"], layout=layout))


def mocked_physical_checks(monkeypatch, view, kwargs):
    def checks(*args):
        # Deliberately isolate economics from AC feasibility for this algebraic
        # test. No claim is made that the synthetic nonzero point is feasible.
        Pg = view.physical.variables["Pg"].value * kwargs["case"]["baseMVA"]
        generation = kwargs["delta"] * sum(np.sum(g.cost_coeffs[0]+g.cost_coeffs[1]*Pg[i]+g.cost_coeffs[2]*Pg[i]**2)
            for i, g in enumerate(kwargs["generators"]))
        battery = view.physical.variables["b"].value
        fraction = view.physical.variables["load_shed_fraction"].value
        shed = fraction.T * kwargs["df_load_p"].to_numpy()
        physical = dict(generator_cost=float(generation), storage_cost=float(kwargs["delta"]*np.sum(abs(battery)*.01)),
                        load_shedding_cost=float(kwargs["delta"]*np.sum(shed*10000.)))
        common = dict(passed=True, costs=physical)
        return dict(common=common, transformation=dict(passed=True),
                    accounting=m.accounting(view, args[3], args[4], args[5], common),
                    result=dict(energy_not_served=float(kwargs["delta"]*shed.sum()), p_load_shed=shed.tolist()))
    monkeypatch.setattr(a.diagnostic, "assess", checks)


@pytest.mark.parametrize("mode", list(f.MODES))
def test_component_economic_reconstruction_for_each_ablation(monkeypatch, mode):
    call, kwargs, view, start = small_fixture(mode)
    start["b"] = np.array([[-2., 0., 3.]])
    start["load_shed_fraction"] = np.ones((3, 3))*.1
    native, captured = canonical_record(view, start, np.zeros((1, 3)))
    mocked_physical_checks(monkeypatch, view, kwargs)
    stress = dict(forced_shedding=False, demand_multiplier=1., modified=f.shortage(kwargs))
    check = a.assess(view, call, kwargs, start, stress, native, None, captured)
    assert check["economics"]["passed"], check
    assert check["passed"]
    assert check["economics"]["reconstruction_error"] < 1e-8


@pytest.mark.parametrize("slack", [np.array([[.01, 0., 0.]]), np.array([[.01, -.01, 0.]])])
def test_large_total_cost_or_cancellation_cannot_hide_cycling_error(monkeypatch, slack):
    call, kwargs, view, start = small_fixture("shedding")
    start["b"] = np.array([[-2., 0., 3.]])
    start["load_shed_fraction"] = np.ones((3, 3))*.9
    native, captured = canonical_record(view, start, slack)
    mocked_physical_checks(monkeypatch, view, kwargs)
    stress = dict(forced_shedding=False, demand_multiplier=1., modified=f.shortage(kwargs))
    check = a.assess(view, call, kwargs, start, stress, native, None, captured)
    assert check["economics"]["total"]["passed"]
    assert not check["economics"]["cycling_slack_passed"]
    assert not check["passed"]


def test_fresh_canonical_start_mismatch_is_not_accepted(monkeypatch):
    call, kwargs, view, start = small_fixture()
    native, captured = canonical_record(view, start, np.zeros((1, 3)))
    captured["complete_x0"][0] += 1
    with pytest.raises(ValueError, match="verified x0"):
        a.assess(view, call, kwargs, start, {}, native, None, captured)


def test_forced_variant_requires_measured_shedding_even_when_multiplier_is_one(monkeypatch):
    call, kwargs, view, start = small_fixture()
    start["b"][:] = 0.
    start["load_shed_fraction"][:] = 0.
    native, captured = canonical_record(view, start, np.zeros((1, 3)))
    mocked_physical_checks(monkeypatch, view, kwargs)
    stress = dict(forced_shedding=True, demand_multiplier=1.,
                  modified=dict(minimum_ens_mwh=1., minimum_shed_mw=[1., 0., 0.]))
    check = a.assess(view, call, kwargs, start, stress, native, None, captured)
    assert check["economics"]["passed"]
    assert check["forced_shedding"]["required"]
    assert not check["forced_shedding"]["passed"] and not check["passed"]


def setup_status(monkeypatch, root):
    binding = dict(rows=[dict(arm=asdict(arm), group=arm.group) for arm in f.arms()])
    monkeypatch.setattr(r, "verify_binding", lambda _: binding)
    q.atomic_immutable_json(root / "protocol.json", r.protocol())
    return binding


def test_missing_supervision_never_accepts_or_advances(monkeypatch, tmp_path):
    setup_status(monkeypatch, tmp_path)
    directory = tmp_path / "call-001"
    directory.mkdir()
    q.atomic_immutable_json(directory / "completion.json", dict(classification="accepted"))
    status = r.status(tmp_path)
    assert status["accepted"] == status["disposed"] == status["completed_archives"] == 0
    assert status["attempts"][0]["classification"] == "unfinished"
    (tmp_path / "call-002").mkdir()
    with pytest.raises(ValueError, match="unsupervised"):
        r.status(tmp_path)


def finalized_timeout(monkeypatch, tmp_path):
    setup_status(monkeypatch, tmp_path)
    directory = tmp_path / "call-001"
    directory.mkdir()
    q.atomic_immutable_json(directory / "request.json", r.request(tmp_path, 1, 180.))
    q.atomic_immutable_json(directory / "supervision.json", dict(classification="wall_limit", wall_seconds=180.,
        samples=0, peak_sampled_rss_mib=0., returncode=-15))
    return directory


def test_timeout_is_disposed_not_accepted_or_infeasible(monkeypatch, tmp_path):
    finalized_timeout(monkeypatch, tmp_path)
    status = r.status(tmp_path)
    assert status["disposed"] == 1 and status["accepted"] == status["completed_archives"] == 0
    assert status["attempts"][0]["classification"] == "wall_limit"


def test_timeout_during_publication_retains_draft_without_acceptance(monkeypatch, tmp_path):
    directory = finalized_timeout(monkeypatch, tmp_path)
    q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    report = r.status(tmp_path)
    assert report["disposed"] == 1 and report["accepted"] == report["completed_archives"] == 0
    assert report["retained_result_archives"] == 1 and report["retained_completion_manifests"] == 0
    assert report["attempts"][0]["archive_state"] == "incomplete_publication"
    assert report["attempts"][0]["partial_archive_sha256"] == q.digest(directory / "result.json.gz")


def test_completed_manifest_missing_archive_remains_fatal(monkeypatch, tmp_path):
    directory = finalized_timeout(monkeypatch, tmp_path)
    q.atomic_immutable_json(directory / "completion.json", dict(classification="accepted"))
    with pytest.raises(ValueError, match="manifest/archive"):
        r.status(tmp_path)


def test_successful_worker_missing_completion_remains_fatal(monkeypatch, tmp_path):
    directory = finalized_timeout(monkeypatch, tmp_path)
    q.atomic_json(directory / "supervision.json", dict(classification="exited", wall_seconds=5.,
        samples=0, peak_sampled_rss_mib=0., returncode=0))
    q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=1))
    with pytest.raises(ValueError, match="successful worker missing completion"):
        r.status(tmp_path)


def test_parent_advances_after_timeout_during_publication(monkeypatch, tmp_path):
    commit, binding = "a"*40, dict(context=dict(clean=True, commit="a"*40),
        rows=[dict(arm=asdict(arm), group=arm.group) for arm in f.arms()])
    monkeypatch.setattr(r, "frozen_binding", lambda: binding)
    monkeypatch.setattr(r, "verify_binding", lambda _: binding)
    monkeypatch.setattr(r.diagnostic, "monitoring", lambda: {})
    for key in r.THREAD_KEYS:
        monkeypatch.setenv(key, "1")
    launches = []
    def no_optimizer(command, directory, root, request):
        launches.append(request["call_id"])
        q.atomic_gzip_json(directory / "result.json.gz", dict(iteration=request["call_id"]))
        supervision = dict(classification="wall_limit", wall_seconds=180., samples=0,
                           peak_sampled_rss_mib=0., returncode=-15)
        q.atomic_immutable_json(directory / "supervision.json", supervision)
        if len(launches) == 2:
            (root / "STOP").touch()
        return supervision
    monkeypatch.setattr(q, "supervise", no_optimizer)
    result = r.run(tmp_path / "fresh", commit)
    assert launches == [1, 2] and result["outcome"] == "operator_stop"
    report = r.status(tmp_path / "fresh")
    assert report["disposed"] == 2 and report["accepted"] == report["completed_archives"] == 0
    assert report["retained_result_archives"] == 2 and report["worker_seconds"] == 360.


@pytest.mark.parametrize("mode", list(f.MODES))
def test_objective_only_evaluation_matches_stock_oracle_without_network_derivatives(monkeypatch, mode):
    _, _, view, start = small_fixture(mode)
    start["b"] = np.array([[-2., 0., 3.]])
    start["load_shed_fraction"] = np.ones((3, 3))*.1
    view.assign(start)
    data, inverse = m.canonical_data(view.solver)
    point = np.asarray(data["x0"]).copy()
    oracle = Oracles(data["_bounds"].new_problem, verbose=False, use_hessian=False)
    expected_value = float(oracle.objective(point))
    expected_gradient = float(np.max(abs(oracle.gradient(point))))
    def forbidden(*args, **kwargs):
        raise AssertionError("economic replay must not instantiate network derivative machinery")
    import cvxpy.reductions.solvers.nlp_solvers.diff_engine as engine
    monkeypatch.setattr(engine, "C_problem", forbidden)
    actual, maximum = a.canonical_objective(data, inverse, point, gradient=True)
    assert actual == pytest.approx(expected_value, abs=1e-9, rel=1e-12)
    assert maximum == pytest.approx(expected_gradient, abs=1e-9, rel=1e-12)


@pytest.mark.parametrize("failure", ["dirty", "wrong_commit", "telemetry"])
def test_execution_gate_failure_never_creates_root(monkeypatch, tmp_path, failure):
    commit = "a"*40
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=failure != "dirty", commit=commit)))
    for key in r.THREAD_KEYS:
        monkeypatch.setenv(key, "1")
    def no_monitoring():
        raise RuntimeError("telemetry unavailable")
    monkeypatch.setattr(r.diagnostic, "monitoring", no_monitoring)
    with pytest.raises((RuntimeError, ValueError)):
        r.run(tmp_path / "absent", "b"*40 if failure == "wrong_commit" else commit)
    assert not (tmp_path / "absent").exists()


def test_worker_failure_archive_contract_without_optimizer(monkeypatch, tmp_path):
    call, kwargs, view, start = small_fixture()
    row = dict(arm=asdict(f.arms()[0]))
    monkeypatch.setattr(r, "verify_binding", lambda _: dict(context={}, rows=[row]))
    monkeypatch.setattr(r, "checked_construction", lambda _: (call, kwargs, view, start, {}))
    q.atomic_immutable_json(tmp_path / "protocol.json", r.protocol())
    directory = tmp_path / "call-001"
    directory.mkdir()
    q.atomic_immutable_json(directory / "request.json", r.request(tmp_path, 1, 180.))
    q.atomic_immutable_json(directory / "launch.json", dict(pid=os.getpid()))
    def no_solver(*args, start_observer, native_observer, **options):
        start_observer(SimpleNamespace(complete_x0=[1., 2.], layout=[]))
        native_observer(dict(status=2, obj_val=10.))
        return SimpleNamespace(exception=None)
    monkeypatch.setattr(r, "_solve_ac_with_verified_x0", no_solver)
    r.worker(tmp_path, 1)
    record = q.read(directory / "result.json.gz")
    assert record["classification"] == "rejected" and record["exception"] is None
    assert record["captured"] == q.read(directory / "x0.json.gz")
    for name, sha in q.read(directory / "completion.json")["artifacts"].items():
        assert q.digest(directory / name) == sha


def test_operator_interrupt_between_attempts_records_terminal_stop(monkeypatch, tmp_path):
    commit = "a"*40
    monkeypatch.setattr(r, "frozen_binding", lambda: dict(context=dict(clean=True, commit=commit)))
    for key in r.THREAD_KEYS:
        monkeypatch.setenv(key, "1")
    calls = 0
    def telemetry():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt("operator battery pause")
        return {}
    monkeypatch.setattr(r.diagnostic, "monitoring", telemetry)
    monkeypatch.setattr(r, "status", lambda _: dict(worker_seconds=0., launches=0, disposed=0))
    result = r.run(tmp_path / "fresh", commit)
    assert result["outcome"] == "operator_stop"
    finish = q.read(tmp_path / "fresh/invocation-finish.json")
    assert finish["outcome"] == "operator_stop" and "battery pause" in finish["exception"]
    assert not list((tmp_path / "fresh").glob("call-*"))


def test_disposition_distinguishes_incomplete_rejected_and_accepted():
    arm = f.arms()[0]
    assert analysis.disposition([arm], []) == "incomplete"
    assert analysis.disposition([arm], [dict(arm=asdict(arm), classification="wall_limit", accepted=False)]) == "not_qualified"
    assert analysis.disposition([arm], [dict(arm=asdict(arm), classification="exited", accepted=True)]) == "qualified_for_declared_matrix"
