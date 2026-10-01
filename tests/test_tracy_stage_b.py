"""Runner qualification without launching study solves or calling a solver."""

from copy import deepcopy
from dataclasses import replace
import json
import gzip
import sys
from types import SimpleNamespace

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
from experiments.case118_tracy_2021 import run_stage_b as runner
from experiments.case118_tracy_2021.stage_b import (
    arms,
    audit_result,
    inputs_for_arm,
    verified_inputs,
    study_spec,
)
from experiments.case118_tracy_2021.prepare import SOURCE


def fixture(formulation):
    case = case9()
    generators = [
        replace(g, p_min_mw=0, p_max_mw=100, cost_coeffs=(2.0, 1.0, 0.01))
        for g in gen_from_matpower(case["gen"], case["gencost"])
    ]
    kwargs = dict(
        case=case,
        T=2,
        delta=1.0,
        formulation=formulation,
        generators=generators,
        storage=[
            StorageUnitIdeal(
                bus=5,
                capacity=10,
                initial_soc=5,
                terminal_soc=5,
                terminal_constraint="equality",
                apparent_power_rating=4,
                device_id="battery",
            )
        ],
        loads=[Load(bus=5, p_load_mw=10, device_id="load", shedding_cost_per_mwh=100)],
        nondispatchable=[
            NondispatchableUnit(
                bus=5, device_id="solar", p_available=3, apparent_power_rating=4
            )
        ],
        df_load_p=pd.DataFrame({"load": [10.0, 10.0]}),
        df_load_q=pd.DataFrame({"load": [0.0, 0.0]}),
        df_nd=pd.DataFrame({"solar": [3.0, 3.0]}),
    )
    build = build_opf_multistep(**kwargs)
    # An analytic feasible point, assigned without canonicalization or solving.
    build.variables["Pg"].value = np.array([[8.0, 6.0], [0.0, 0.0], [0.0, 0.0]]) / 100
    build.variables["b"].value = np.array([[-1.0, 1.0]])
    build.variables["soc"].value = np.array([[5.0, 6.0, 5.0]])
    build.variables["p_nd"].value = np.array([[2.0, 2.0]])
    build.variables["load_shed_fraction"].value = np.array([[0.1, 0.1]])
    if formulation == "lossy_dc":
        flows = np.zeros((9, 2))
        flows[0] = [8.0, 6.0]
        flows[1] = [8.0, 6.0]
        build.variables["p_flows"].value = flows / 100
    build.prob._status = "optimal"
    build.prob._value = float(build.prob.objective.value)
    named = {
        k: float(v.value) for k, v in build.expressions.items() if k.endswith("_cost")
    }
    return kwargs, extract_results(build), named


def test_grid_is_exact_and_ordered():
    grid = arms()
    assert len(grid) == len(set(grid)) == 72
    assert [a.stop - a.start for a in grid] == sorted(a.stop - a.start for a in grid)
    assert grid[0].window == "Ordinary control"
    assert {a.formulation for a in grid} == {"lossy_dc", "singlenode_dc"}
    assert {a.rho for a in grid} == {1 / 3, 0.001}
    assert {a.throughput for a in grid} == {0.0001, 0.01, 1.0}
    assert len({(a.start, a.stop) for a in grid}) == 6
    assert study_spec()["annual_execution"] is False


@pytest.mark.parametrize("formulation", ["lossy_dc", "singlenode_dc"])
def test_independent_audit_matches_public_projection_without_solve(formulation):
    kwargs, result, named = fixture(formulation)
    audit = audit_result(result, kwargs, named)
    assert audit["passed"], audit
    assert audit["metrics"] == dict(
        energy_not_served_mwh=2.0, curtailed_mwh=2.0, throughput_mwh=2.0
    )
    assert audit["costs"]["generator_cost"] == pytest.approx(27.0)
    assert audit["costs"]["storage_cost"] == pytest.approx(0.02)
    assert audit["costs"]["load_shedding_cost"] == pytest.approx(200.0)


@pytest.mark.parametrize(
    "key",
    [
        "Pg",
        "b",
        "soc",
        "p_nd",
        "p_flows",
        "p_load_served",
        "p_load_shed",
        "p_load",
        "q_load",
        "load_shed_fraction",
        "p_net",
        "energy_not_served",
        "objective",
        "storage_cost",
    ],
)
def test_each_result_corruption_rejected(key):
    kwargs, result, named = fixture("lossy_dc")
    result[key] = np.asarray(result[key]) + 1
    assert not audit_result(result, kwargs, named)["passed"]


@pytest.mark.parametrize("bad", [None, np.nan, np.zeros((2, 2))])
def test_unusable_primal_rejected(bad):
    kwargs, result, named = fixture("lossy_dc")
    result["Pg"] = bad
    assert not audit_result(result, kwargs, named)["passed"]


def test_identity_status_named_cost_and_terminal_rejection():
    kwargs, result, named = fixture("lossy_dc")
    for change in ({"storage_device_ids": ["wrong"]}, {"status": "optimal_inaccurate"}):
        assert not audit_result({**result, **change}, kwargs, named)["passed"]
    assert not audit_result(result, kwargs, {**named, "dc_loss_cost": 10})["passed"]
    different = deepcopy(kwargs)
    different["storage"] = [replace(kwargs["storage"][0], terminal_soc=4)]
    assert not audit_result(result, different, named)["passed"]


def test_owner_input_verification_and_economic_settings():
    if not SOURCE.exists():
        pytest.skip("owner Tracy CSV unavailable")
    p = verified_inputs()
    for a in (arms()[0], arms()[-1]):
        k = inputs_for_arm(p, a)
        assert all(s.aging_weight == a.throughput for s in k["storage"])
        assert all(
            s.initial_soc == s.terminal_soc == s.capacity / 2 for s in k["storage"]
        )
        assert all(d.shedding_cost_per_mwh == 20763.594 for d in k["loads"])
        for g in k["generators"]:
            if g.p_max_mw > 0:
                assert g.cost_coeffs[2] * g.p_max_mw == pytest.approx(
                    a.rho * g.cost_coeffs[1]
                )


@pytest.mark.parametrize(
    "condition,expected",
    [
        ("ok", "exited"),
        ("timeout", "wall_limit"),
        ("rss", "rss_limit"),
        ("missing", "supervisor_failure"),
    ],
)
def test_real_subprocess_supervision_without_numerics(
    tmp_path, monkeypatch, condition, expected
):
    monkeypatch.setattr(
        runner, "_child_rss_mib", lambda pid: None if condition == "missing" else 10.0
    )
    limits = dict(
        wall_seconds=0.1 if condition == "timeout" else 5.0,
        rss_mib=1 if condition == "rss" else 100.0,
        poll_seconds=0.01,
    )
    seconds = 0.02 if condition == "ok" else 10
    record = runner.supervise(
        [sys.executable, "-c", f"import time; time.sleep({seconds})"], tmp_path, limits
    )
    assert record["classification"] == expected
    assert record["returncode"] is not None
    assert (tmp_path / "supervision.json").exists()


def test_interrupt_reaps_worker_and_launch_error_retained(tmp_path, monkeypatch):
    def interrupt(pid):
        raise KeyboardInterrupt()

    monkeypatch.setattr(runner, "_child_rss_mib", interrupt)
    result = runner.supervise(
        [sys.executable, "-c", "import time; time.sleep(10)"], tmp_path
    )
    assert result["classification"] == "interrupted"
    assert result["returncode"] is not None
    other = tmp_path / "launch"
    other.mkdir()
    result = runner.supervise(["/nonexistent/tracy-worker"], other)
    assert result["classification"] == "supervisor_failure"


def test_preflight_refuses_dirty_or_wrong_commit_without_output(tmp_path, monkeypatch):
    for ctx, commit in (
        (dict(clean=False, commit="abc"), "abc"),
        (dict(clean=True, commit="abc"), "wrong"),
    ):
        monkeypatch.setattr(runner, "context", lambda: ctx)
        with pytest.raises(ValueError):
            runner.run(tmp_path / "study", commit)
        assert not (tmp_path / "study").exists()


def test_parent_stops_at_first_failed_arm(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "context", lambda: dict(clean=True, commit="abc"))
    monkeypatch.setattr(runner, "verify_context", lambda ctx: None)
    monkeypatch.setattr(runner, "verified_inputs", lambda: None)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 10.0)
    calls = []

    def supervise(command, directory, limits):
        calls.append(directory)
        return dict(classification="wall_limit")

    monkeypatch.setattr(runner, "supervise", supervise)
    record = runner.run(tmp_path / "study", "abc")
    assert record["classification"] == "stopped"
    assert len(calls) == 1
    assert not record["accepted"]
    assert (
        json.loads((tmp_path / "study/study-result.json").read_text())["classification"]
        == "stopped"
    )


@pytest.mark.parametrize("solver_error", [False, True])
@pytest.mark.parametrize("native_available", [False, True])
def test_worker_archive_and_offline_reconstruction(
    tmp_path, monkeypatch, solver_error, native_available
):
    kwargs, result, named = fixture("singlenode_dc")
    ctx = dict(
        clean=True,
        commit="synthetic",
        stage_a_manifest_sha256="input",
        source_sha256="csv",
    )
    manifest = dict(context=ctx, study=study_spec())
    runner.atomic_json(tmp_path / "binding.json", manifest)
    arm_dir = tmp_path / "arm-000"
    arm_dir.mkdir()
    calls = []

    def solve(**options):
        calls.append(options)
        if solver_error:
            raise RuntimeError("synthetic solver exception")

    native_info = dict(
        cost_primal=10.0,
        cost_dual=9.999,
        gap_abs=0.001,
        gap_rel=0.0001,
        res_primal=1e-12,
        res_dual=2e-12,
        iterations=3,
        solve_time=0.2,
        status="Solved" if not solver_error else "NumericalError",
    )
    native_solver = SimpleNamespace(
        get_info=lambda: SimpleNamespace(**native_info),
        get_settings=lambda: "retained effective settings",
    )
    build = SimpleNamespace(
        solve=solve,
        expressions={k: SimpleNamespace(value=v) for k, v in named.items()},
        prob=SimpleNamespace(
            _solver_cache={"CLARABEL": native_solver} if native_available else {},
            compilation_time=0.1,
            solver_stats=SimpleNamespace(
                solver_name="CLARABEL", num_iters=3, solve_time=0.2, setup_time=None
            ),
        ),
    )
    monkeypatch.setattr(runner, "verify_context", lambda ctx: None)
    monkeypatch.setattr(runner, "context", lambda: ctx)
    monkeypatch.setattr(runner, "verified_inputs", lambda: None)
    monkeypatch.setattr(runner, "inputs_for_arm", lambda p, a: kwargs)
    monkeypatch.setattr(runner, "build_opf_multistep", lambda **kw: build)
    monkeypatch.setattr(runner, "extract_results", lambda b: result)
    assert runner.worker(tmp_path, 0) == int(solver_error)
    assert calls[0]["warm_start"] is False
    assert calls[0]["tol_feas"] == 1e-10
    assert calls[0]["tol_gap_abs"] == calls[0]["tol_gap_rel"] == 1e-10
    assert calls[0]["max_iter"] == 5000
    assert calls[0]["max_threads"] == 1
    assert calls[0]["verbose"] is True
    assert (arm_dir / "completion.json").exists()
    with gzip.open(arm_dir / "result.json.gz", "rt") as stream:
        archived = json.load(stream)
    diagnostics = archived["convergence_diagnostics"]
    assert diagnostics["native_info"] == (native_info if native_available else None)
    assert diagnostics["effective_native_settings"] == (
        "retained effective settings" if native_available else None
    )
    assert archived["timings"]["canonicalization_and_solve_seconds"] >= 0
    if solver_error:
        assert "synthetic solver exception" in archived["exception"]
    runner.atomic_json(
        arm_dir / "supervision.json",
        dict(
            classification="exited",
            returncode=int(solver_error),
            samples=1,
            wall_seconds=1.0,
            peak_sampled_rss_mib=100.0,
        ),
    )
    analysis = runner.analyze(tmp_path)
    assert not analysis["complete"]
    assert len(analysis["accepted"]) == (0 if solver_error else 1)
    if not solver_error:
        supervision = json.loads((arm_dir / "supervision.json").read_text())
        supervision["wall_seconds"] = 1801
        runner.atomic_json(arm_dir / "supervision.json", supervision)
        with pytest.raises(ValueError, match="resource"):
            runner.reconstruct_arm(tmp_path, 0, manifest, None)
        supervision["wall_seconds"] = 1
        runner.atomic_json(arm_dir / "supervision.json", supervision)
        completion = json.loads((arm_dir / "completion.json").read_text())
        completion["result_sha256"] = "changed"
        runner.atomic_json(arm_dir / "completion.json", completion)
        with pytest.raises(ValueError, match="hash"):
            runner.reconstruct_arm(tmp_path, 0, manifest, None)


def test_native_diagnostic_error_is_retained_not_raised():
    def broken_info():
        raise RuntimeError("native statistics unavailable")

    build = SimpleNamespace(
        prob=SimpleNamespace(
            _solver_cache={"CLARABEL": SimpleNamespace(get_info=broken_info)}
        )
    )
    diagnostics = runner.convergence_diagnostics(build)
    assert diagnostics["native_info"] is None
    assert "native statistics unavailable" in diagnostics["diagnostic_exception"]
