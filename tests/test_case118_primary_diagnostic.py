"""Admission boundaries for the isolated primary diagnostic; no solver runs."""

from contextlib import nullcontext
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.case118_spacetime_pq_replay import diagnose_primary as diagnostic


@pytest.mark.parametrize("head,dirty", [
    ("different", ""), ("expected", " M runner.py"),
    ("expected", "?? uncommitted_runner.py"),
])
def test_pair_refuses_unreviewed_execution(tmp_path, monkeypatch, head, dirty):
    monkeypatch.setattr(diagnostic, "git",
                        lambda *args: head if args[0] == "rev-parse" else dirty)
    output = tmp_path / "new"
    with pytest.raises(ValueError, match="reviewed commit"):
        diagnostic.run_pair(output, commit="expected", fan_on=True)
    assert not output.exists()


def test_pair_requires_fan_before_creating_output(tmp_path):
    output = tmp_path / "new"
    with pytest.raises(ValueError, match="fan"):
        diagnostic.run_pair(output, commit="unused")
    assert not output.exists()


@pytest.mark.parametrize("mode", ["dense", "spatial_off", ""])
def test_preparation_cannot_select_an_unplanned_representation(tmp_path, mode):
    with pytest.raises(ValueError, match="temporal representation"):
        diagnostic.prepare(tmp_path, mode, None, None, {})


def test_dependency_drift_is_rejected(monkeypatch):
    monkeypatch.setattr(diagnostic, "_software_versions", lambda: {"cvxpy": "1.9.3"})
    monkeypatch.setattr(diagnostic, "version", lambda name: "0.6.1")
    with pytest.raises(ValueError, match="dependencies"):
        diagnostic.check_versions(dict(software_versions={"cvxpy": "1.9.2"},
                                       sparsediffpy_version="0.6.1"))


def test_worker_refuses_dirty_launch_before_preparation(tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostic, "read", lambda path: dict(
        prepare_only=False, commit="expected"))
    monkeypatch.setattr(diagnostic, "git", lambda *args:
                        "expected" if args[0] == "rev-parse" else " M source.py")
    with pytest.raises(ValueError, match="clean, unchanged commit"):
        diagnostic.worker(tmp_path / "stepwise")


@pytest.mark.parametrize("attempt,stops", [
    (dict(slot_state="construction_error", audit=None, result=None,
          reason="ac_construction_error:RuntimeError:broken"), True),
    (dict(slot_state="executed", audit=dict(exception="RuntimeError: broken"),
          result=dict(status="solver_error")), True),
    (dict(slot_state="executed", audit=dict(exception=None, status="user_limit",
                                           accepted_primal=False),
          result=dict(status="user_limit", objective=123)), False),
])
def test_pair_preserves_structured_errors_and_continues_numerical_rejections(
    tmp_path, monkeypatch, attempt, stops,
):
    output = tmp_path / "diagnostic"
    launched = []
    monkeypatch.setattr(diagnostic, "preflight", lambda *a, **k: {})
    monkeypatch.setattr(diagnostic, "TemperatureCollector", lambda p: nullcontext())
    monkeypatch.setattr(diagnostic.subprocess, "run", lambda *a, **k: None)

    def launch(command, **kwargs):
        directory = Path(command[-1])
        launched.append(directory.name)
        (directory / "result.json").write_text(json.dumps(dict(attempt=attempt)))
        return SimpleNamespace(pid=123, wait=lambda: 0)

    monkeypatch.setattr(diagnostic.subprocess, "Popen", launch)
    if stops:
        with pytest.raises(RuntimeError, match="stepwise worker failed"):
            diagnostic.run_pair(output, commit="expected", fan_on=True)
        assert launched == ["stepwise"]
        assert not (output / "finished.json").exists()
    else:
        diagnostic.run_pair(output, commit="expected", fan_on=True)
        assert launched == ["stepwise", "vectorized"]
        assert (output / "finished.json").exists()
    completion = json.loads((output / "stepwise/completion.json").read_text())
    assert completion["returncode"] == 0
    assert completion["wall_seconds"] >= 0
    assert bool(completion["execution_error"]) == stops
    assert completion["audit"] == attempt["audit"]


def test_logging_reaches_solver_through_real_frozen_config_and_x0_checks(monkeypatch):
    from cvxopf import build_opf
    from cvxopf.testcases import case9
    from experiments.case118_annual_hierarchy.p0_fixture import (
        frozen_p0_solve_config, solve_config_sha256,
    )

    streaming = diagnostic.streaming
    config = frozen_p0_solve_config()
    original_hash = solve_config_sha256(config)
    original_solver = streaming.IPOPT
    calls, starts = [], []

    def native_entry(self, data, warm_start, verbose, solver_opts, solver_cache=None):
        calls.append(dict(solver_opts))
        raise RuntimeError("TEST_STOP_BEFORE_NATIVE_SOLVE")

    monkeypatch.setattr(original_solver, "solve_via_data", native_entry)
    for logging in (False, True):
        build = build_opf(case9(), formulation="ac")
        streaming.assign_start(build, streaming.complete_flat_start(build))
        with diagnostic.ipopt_iteration_log() if logging else nullcontext():
            run = streaming.solve_ac_with_verified_x0(
                build, config, start_observer=starts.append,
            )
        assert run.exception == "RuntimeError: TEST_STOP_BEFORE_NATIVE_SOLVE"
        assert streaming.IPOPT is original_solver
    assert len(starts) == len(calls) == 2
    assert calls[0]["print_level"] == 0
    assert calls[1] == {**calls[0], "print_level": 5}
    assert solve_config_sha256(config) == original_hash


def test_logging_restores_solver_on_escaping_exception():
    original = diagnostic.streaming.IPOPT
    with pytest.raises(RuntimeError, match="test failure"):
        with diagnostic.ipopt_iteration_log():
            assert diagnostic.streaming.IPOPT is not original
            raise RuntimeError("test failure")
    assert diagnostic.streaming.IPOPT is original
