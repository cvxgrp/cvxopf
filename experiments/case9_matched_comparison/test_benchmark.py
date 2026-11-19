"""Rehearse the real supervisor and cross-audit with no numerical solve."""

import json
import os
import subprocess
import sys
from pathlib import Path

import cvxpy as cp
import numpy as np
import pytest
from cvxpy.reductions.solvers.nlp_solvers.ipopt_nlpif import IPOPT

from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json, sha256_path

from . import benchmark, preflight
from .benchmark_audit import assess, pair_summary
from .benchmark_worker import build_model, solve_cvx
from .contract import encode_case, generator_cost, point_suite, selected_links, solution_point
from .cvx_side import branch_data, make_case

CELL = "pwl_links"
FIXTURE_NATIVE = {"fixture": {"id": 0, "signed": 0., "units": "mock units", "tolerance": 1e-8}}


def archived_point():
    """Real retained coordinates, replayed without touching the archive."""
    case = make_case(CELL)
    p = preflight.historical_inputs()["pstar_full.json"]
    fx = [dc[15] for dc in selected_links(case).values()]
    point = solution_point(
        case, np.deg2rad(p["Va_deg"]), p["Vm"], p["real_Pg"], p["real_Qg"],
        zip(p["from_dummy_Pg"], np.asarray(p["to_dummy_Pg"]) + fx, strict=True),
    )
    cost = sum(generator_cost(row, pg) for row, pg in zip(case["gencost"], point["pg_mw"], strict=True))
    return case, point, cost


def cell_record(cell):
    case = make_case(cell)
    return {"cell": cell, "case": encode_case(case), "ratings": branch_data(case)[1],
            "start": point_suite(case)[0]}


def make_binding(root, arms, pyp=sys.executable, *, wall=2.):
    binding = {
        "arms": arms, "settings": {"wall_seconds_per_worker": wall, "poll_seconds": .05,
                                   "material_cost_threshold": 1.},
        "environment": {"cvxopf_executable": sys.executable, "pypower": {"executable": str(pyp)}},
        "cells": {cell: cell_record(cell) for cell, _ in arms},
    }
    atomic_immutable_json(root / "binding.json", binding)
    atomic_immutable_json(root / "protocol.json", {"protocol": {"poll_seconds": .05}})
    return binding, sha256_path(root / "binding.json")


def no_verify(root, binding):
    """Fixture bindings exercise the lifecycle, not production source admission."""


def dummy_supervisor(payloads, *, rss_reader=None):
    """Keep the real process, RSS, deadlines, STOP and reaping paths."""
    script = """
import hashlib
import json
import sys
import time
from pathlib import Path
root, directory, message = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
mode = message.pop("mode", "return")
if mode == "stop":
    (root / "STOP").touch()
    time.sleep(10)
if mode == "timeout":
    time.sleep(10)
if mode == "exit":
    sys.exit(21)
request = json.loads((directory / "request.json").read_text())
files = {}
for name in ("native_start.json", "native_return.json"):
    path = directory / name
    path.write_text("{}")
    files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
message.update(cell=request["cell"], solver=request["solver"], native_artifacts=files,
               binding_sha256=request["binding_sha256"], solver_call_seconds=0.1)
(directory / "return.json").write_text(json.dumps(message))
"""
    calls = []
    if rss_reader is None:
        rss_reader = benchmark._child_rss_mib if os.getenv("CASE9_REHEARSE_REAL_RSS") else lambda pid: 12.

    def supervise(command, directory, root, request, **kwargs):
        arm = (request["cell"], request["solver"])
        calls.append(arm)
        payload = payloads[arm]
        command = ([str(directory / "missing-executable")] if payload.get("mode") == "unlaunched"
                   else [sys.executable, "-c", script, str(root), str(directory), json.dumps(payload)])
        return benchmark.supervise(command, directory, root, request, rss_reader=rss_reader, **kwargs)

    return supervise, calls


@pytest.fixture
def pinned_python():
    path = os.environ.get("CASE9_PINNED_PYPOWER_PYTHON")
    if path is None:
        pytest.skip("set CASE9_PINNED_PYPOWER_PYTHON for the real PYPOWER cross-audit")
    return Path(path).absolute()


def test_binding_rehearsal_uses_real_preflight_and_does_not_authorize(tmp_path, pinned_python, monkeypatch):
    # The rehearsal has its own temp root and synthetic git identity. It runs
    # real preflight publication and bind/status, but never calls launch.
    sources = benchmark.source_paths()
    monkeypatch.setattr(benchmark, "ROOT", Path("/"))
    monkeypatch.setattr(benchmark, "source_paths", lambda: sources)
    monkeypatch.setattr(benchmark, "git", lambda cmd, *args: "" if cmd == "status" else "rehearsal")
    prior, root = tmp_path / "preflight", tmp_path / "benchmark"
    preflight.run(prior, pinned_python, resume=False)
    digest = benchmark.bind(root, prior, benchmark.HERE / "BENCHMARK_SETTINGS.json", pinned_python)
    binding = benchmark.read(root / "binding.json")
    assert digest == sha256_path(root / "binding.json")
    assert len(binding["arms"]) == 8
    assert all(v["start"]["name"] == "accounting" for v in binding["cells"].values())
    assert benchmark.progress(root, binding)["terminal"] == 0
    assert not (root / "authority.json").exists()
    assert not (root / "attempts").exists()
    with pytest.raises(ValueError, match="exact binding SHA-256"):
        benchmark.execute(root, "wrong digest", verify=no_verify)


def test_real_cross_audit_and_resumption_without_repeating_solve(tmp_path, pinned_python, monkeypatch):
    case, point, cost = archived_point()
    arms = [[CELL, "cvxopf"], [CELL, "pypower"]]
    binding, digest = make_binding(tmp_path, arms, pinned_python)
    raw = {"native_success": True, "point": point, "objective": cost, "api_objective": cost,
           "native_checks": FIXTURE_NATIVE}
    supervise, calls = dummy_supervisor({
        (CELL, "cvxopf"): raw,
        (CELL, "pypower"): dict(raw, dummy_q_mvar=[0.] * 6),
    })
    with monkeypatch.context() as patcher:
        def stop_after_publish(*args, **kwargs):
            raise KeyboardInterrupt
        patcher.setattr(benchmark, "audit_return", stop_after_publish)
        assert benchmark.execute(tmp_path, digest, supervisor=supervise, verify=no_verify) == "operator_stop"
    before = benchmark.progress(tmp_path, binding)
    assert [a["state"] for a in before["arms"]] == ["awaiting_audit", "pending"]
    resume = """
import json
import sys
from pathlib import Path
from experiments.case9_matched_comparison.test_benchmark import benchmark, dummy_supervisor, no_verify
root = Path(sys.argv[1])
raw = json.loads(sys.argv[3])
supervise, calls = dummy_supervisor({("pwl_links", "pypower"): dict(raw, dummy_q_mvar=[0.] * 6)})
assert benchmark.execute(root, sys.argv[2], resume=True, supervisor=supervise,
                         verify=no_verify) == "matrix_complete"
assert calls == [("pwl_links", "pypower")]
"""
    subprocess.run([sys.executable, "-c", resume, str(tmp_path), digest, json.dumps(raw)],
                   cwd=benchmark.ROOT, env=preflight.child_env(), check=True, timeout=30)
    summary = benchmark.progress(tmp_path, binding)
    assert summary["accepted"] == 2
    assert summary["pairs"][CELL]["verdict"] == "costs_agree_within_threshold"
    assert summary["pairs"][CELL]["total_converter_loss_mw"]["cvxopf"] == pytest.approx(1.51, abs=1e-5)
    assert benchmark.execute(tmp_path, digest, resume=True, supervisor=supervise,
                             verify=no_verify) == "matrix_complete"
    assert calls == [(CELL, "cvxopf")]
    directory = tmp_path / summary["arms"][1]["attempt"]
    audit = benchmark.read(directory / "audit.json")["verdict"]
    invalid = dict(raw, solver="pypower", dummy_q_mvar=[1., 0., 0., 0., 0., 0.])
    verdict = assess(case, invalid, audit["evaluations"])
    assert not verdict["accepted"] and verdict["failures"] == ["PYPOWER dummy terminal 0 Q: 1 MVAr"]
    assert pair_summary({"cvxopf": {"audit": audit}, "pypower": {"audit": verdict}}, 1.) == {
        "verdict": "not_comparable", "reason": "both native returns must pass both model audits",
    }


def test_launched_stop_and_failures_remain_and_unlaunched_can_resume(tmp_path):
    arms = [[CELL, "cvxopf"], [CELL, "pypower"]]
    binding, digest = make_binding(tmp_path, arms)
    payloads = {(CELL, "cvxopf"): {"mode": "unlaunched"}, (CELL, "pypower"): {"mode": "stop"}}
    supervise, calls = dummy_supervisor(payloads)
    with pytest.raises(ValueError, match="benchmark stopped"):
        benchmark.execute(tmp_path, digest, supervisor=supervise, verify=no_verify)
    assert benchmark.progress(tmp_path, binding)["terminal"] == 0
    payloads[CELL, "cvxopf"] = {"mode": "exit"}
    with pytest.raises(ValueError, match="benchmark stopped"):
        benchmark.execute(tmp_path, digest, resume=True, supervisor=supervise, verify=no_verify)
    assert benchmark.progress(tmp_path, binding)["arms"][0]["state"] == "worker_failed"
    assert benchmark.execute(tmp_path, digest, resume=True, supervisor=supervise,
                             verify=no_verify) == "operator_stop"
    assert benchmark.progress(tmp_path, binding)["arms"][1]["state"] == "interrupted"
    assert benchmark.execute(tmp_path, digest, resume=True, acknowledge_stop=True,
                             supervisor=supervise, verify=no_verify) == "matrix_complete"
    assert calls == [(CELL, "cvxopf"), (CELL, "cvxopf"), (CELL, "pypower")]
    assert benchmark.progress(tmp_path, binding)["pairs"][CELL]["verdict"] == "not_comparable"


def audit_fixture(tmp_path, pinned_python, point):
    root = tmp_path / "reconstructed"
    request = root / "request.json"
    atomic_immutable_json(request, {"cell": cell_record(CELL), "point": point})
    reports = {}
    for side, executable in (("cvxopf", sys.executable), ("pypower", str(pinned_python))):
        path = root / f"{side}.json"
        subprocess.run([executable, "-m", benchmark.WORKER, "audit", side, str(request), str(path)],
                       cwd=benchmark.ROOT, env=preflight.child_env(), check=True, timeout=30)
        reports[side] = benchmark.read(path)["evaluation"]
    return reports


@pytest.mark.parametrize("fault", ["none", "objective", "native_constraint", "nonconverged"])
def test_cvx_public_native_return_and_diagnostics(tmp_path, monkeypatch, pinned_python, fault):
    _, point, cost = archived_point()
    model = build_model("cvxopf", cell_record(CELL))
    calls = []

    def fake_native(self, data, warm_start, verbose, solver_opts, solver_cache=None):
        calls.append(1)
        x = data["x0"].copy()
        if fault == "native_constraint":
            offset = 0
            for var in data["_bounds"].main_var:
                if var.name() == "P_vec":
                    x[offset] += 1.
                    break
                offset += var.size
            else:
                pytest.fail("missing native P_vec")
        return {"status": -2 if fault == "nonconverged" else 0, "x": x,
                "obj_val": cost + (1000. if fault == "objective" else 0.),
                "num_iters": 0, "status_msg": b"mock"}

    monkeypatch.setattr(IPOPT, "solve_via_data", fake_native)
    raw = solve_cvx(model, point, benchmark.settings_from(benchmark.HERE / "BENCHMARK_SETTINGS.json")["cvxopf"],
                    tmp_path)
    assert calls == [1]
    if fault != "nonconverged":
        assert raw["api_status"] == cp.OPTIMAL
    assert raw["point"]["pg_mw"] == pytest.approx(point["pg_mw"])
    assert raw["point"]["qg_mvar"] == pytest.approx(point["qg_mvar"])
    assert raw["point"]["links"] == point["links"]
    assert not benchmark.read(tmp_path / "native_start.json")["warm_start"]
    verdict = assess(model.case, dict(raw, solver="cvxopf"),
                     audit_fixture(tmp_path, pinned_python, raw["point"]))
    if fault == "none":
        assert verdict["accepted"]
    elif fault == "objective":
        assert raw["objective"] == pytest.approx(cost + 1000)
        assert raw["api_objective"] == pytest.approx(cost)
        assert verdict["failures"] == ["native objective differs from the independent engineering-unit cost"]
    elif fault == "native_constraint":
        assert raw["native_checks"]["constraint_rows"]["signed"] == pytest.approx(1.)
        assert any(f.startswith("native constraint_rows") for f in verdict["failures"])
        assert not verdict["accepted"]
    else:
        assert verdict["classification"] == "nonconverged" and set(verdict["evaluations"]) == set(benchmark.SOLVERS)
        assert not verdict["accepted"]


@pytest.mark.parametrize(("reading", "state"), [(None, "supervisor_failure"), (17000., "rss_limit")])
def test_required_monitoring_unavailable_or_over_limit_halts(tmp_path, reading, state):
    arm = [CELL, "cvxopf"]
    binding, digest = make_binding(tmp_path, [arm])
    supervise, calls = dummy_supervisor({tuple(arm): {"mode": "timeout"}}, rss_reader=lambda _: reading)
    with pytest.raises(ValueError, match=state):
        benchmark.execute(tmp_path, digest, supervisor=supervise, verify=no_verify)
    assert calls == [tuple(arm)]
    assert benchmark.progress(tmp_path, binding)["arms"][0]["state"] == state


def test_time_limit_is_a_terminal_censored_arm(tmp_path):
    arm = [CELL, "cvxopf"]
    binding, digest = make_binding(tmp_path, [arm], wall=.15)
    supervise, calls = dummy_supervisor({tuple(arm): {"mode": "timeout"}})
    assert benchmark.execute(tmp_path, digest, supervisor=supervise, verify=no_verify) == "matrix_complete"
    result = benchmark.progress(tmp_path, binding)
    assert result["accepted"] == 0 and result["terminal"] == 1
    assert result["arms"][0]["state"] == "wall_limit"
    assert benchmark.execute(tmp_path, digest, resume=True, supervisor=supervise,
                             verify=no_verify) == "matrix_complete"
    assert calls == [tuple(arm)]


@pytest.mark.parametrize("wrong_epigraph", [False, True])
def test_pips_path_audits_actual_epigraph_without_calling_optimizer(tmp_path, pinned_python, wrong_epigraph):
    _, point, cost = archived_point()
    request = tmp_path / "fixture.json"
    atomic_immutable_json(request, {
        "model": cell_record(CELL), "point": point, "cost": cost, "wrong_epigraph": wrong_epigraph,
        "settings": benchmark.settings_from(benchmark.HERE / "BENCHMARK_SETTINGS.json")["pypower"],
    })
    script = """
import sys
from pathlib import Path
from unittest.mock import patch
from experiments.case9_matched_comparison.benchmark_worker import (
    atomic_immutable_json, build_model, read, solve_pyp,
)
path = Path(sys.argv[1])
fixture = read(path)
model = build_model("pypower", fixture["model"])
calls = []
def fake_pips(f, x0, A, lo, hi, xmin, xmax, gh, hess, options):
    calls.append(1)
    x = x0.copy()
    if fixture["wrong_epigraph"]:
        x[model.cols("y")] += [100., -100.]
    return {"x": x, "f": f(x)[0], "eflag": True, "output": {"iterations": 0}}
with patch("pypower.pips.pips", fake_pips):
    raw = solve_pyp(model, fixture["point"], fixture["settings"], path.parent)
assert calls == [1] and abs(raw["objective"] - fixture["cost"]) < 1e-8
assert raw["dummy_q_mvar"] == [0.] * 6
assert max(abs(a-b) for a, b in zip(raw["point"]["pg_mw"], fixture["point"]["pg_mw"])) < 1e-8
assert read(path.parent / "native_start.json")["options"] == fixture["settings"]
atomic_immutable_json(path.parent / "fixture_return.json", raw)
"""
    subprocess.run([str(pinned_python), "-c", script, str(request)], cwd=benchmark.ROOT,
                   env=preflight.child_env(), check=True, timeout=30)
    raw = dict(benchmark.read(tmp_path / "fixture_return.json"), solver="pypower")
    verdict = assess(make_case(CELL), raw, audit_fixture(tmp_path, pinned_python, raw["point"]))
    assert verdict["accepted"] == (not wrong_epigraph)
    if wrong_epigraph:
        assert raw["native_checks"]["linear:ycon"]["signed"] == pytest.approx(100.)
        assert len(verdict["failures"]) == 1 and verdict["failures"][0].startswith("native linear:ycon")


def test_resume_audits_published_return_after_supervisor_loss(tmp_path, pinned_python):
    _, point, cost = archived_point()
    arm = [CELL, "cvxopf"]
    binding, digest = make_binding(tmp_path, [arm], pinned_python)
    directory = tmp_path / "attempts" / "lost"
    atomic_immutable_json(directory / "request.json", {"cell": CELL, "solver": "cvxopf",
                          "binding_sha256": digest, "wall_seconds": 2.})
    atomic_immutable_json(directory / "worker_started.json", {"attempt": "lost"})
    atomic_immutable_json(tmp_path / "authority.json", {"binding_sha256": digest, "execution_authorized": True})
    artifacts = ("native_start.json", "native_return.json")
    for name in artifacts:
        atomic_immutable_json(directory / name, {})
    atomic_immutable_json(directory / "return.json", {
        "cell": CELL, "solver": "cvxopf", "binding_sha256": digest, "solver_call_seconds": .1,
        "native_success": True, "point": point, "objective": cost, "api_objective": cost,
        "native_checks": FIXTURE_NATIVE,
        "native_artifacts": {name: sha256_path(directory / name) for name in artifacts},
    })
    # No supervisor manifest: the complete raw return must still be audited.
    supervise, calls = dummy_supervisor({})
    assert benchmark.execute(tmp_path, digest, resume=True, supervisor=supervise,
                             verify=no_verify) == "matrix_complete"
    result = benchmark.progress(tmp_path, binding)
    assert calls == [] and result["accepted"] == 0
    assert result["finalized_worker_seconds"] == 2.
    assert result["arms"][0]["state"] == "supervisor_lost"
    verdict = benchmark.read(directory / "audit.json")["verdict"]
    assert set(verdict["evaluations"]) == set(benchmark.SOLVERS)
    assert verdict["failures"] == ["supervisor exit and resource status are unknown"]
