"""Real worker/archive and subprocess seams without numerical optimization."""

import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from cvxopf import build_opf_multistep
from cvxopf.hierarchical import IPOPTStartEvidence
from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.case118_tracy_2021 import run_stage_d as runner, stage_d as model
from tests.test_tracy_stage_d import fixture, tiny_study, fake_verify


def test_accepted_worker_and_parent_reconstruction(tmp_path, monkeypatch):
    kwargs, build, _, _ = fixture(1)
    request = dict(
        global_hour=0,
        W=1,
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
    monkeypatch.setattr(model, "build_opf_multistep", lambda **kw: build)

    def fake_solve(build, config, *, start_observer):
        layout, vector = [], []
        for variable in build.prob.variables():
            values = variable.value.flatten(order="F").tolist()
            layout.append(
                dict(
                    name=variable.name(),
                    start=len(vector),
                    stop=len(vector) + len(values),
                    shape=variable.shape,
                    is_original_variable=True,
                )
            )
            vector.extend(values)
        evidence = IPOPTStartEvidence(
            complete_x0=np.array(vector),
            layout=tuple(layout),
            layout_signature="synthetic",
            model_coordinate_count=len(vector),
            auxiliary_coordinate_count=0,
            object_ids_before={},
            object_ids_after={},
        )
        start_observer(evidence)
        return SimpleNamespace(evidence=evidence, exception=None, elapsed_seconds=0.01)

    monkeypatch.setattr(model.starts, "_solve_ac_with_verified_x0", fake_solve)
    model.worker(tmp_path, tmp_path)
    payload = runner.verify_attempt(tmp_path, tmp_path, request, None)
    assert payload["accepted"] and payload["next_soc_mwh"] == [5.0]
    assert "archive" in model.read(tmp_path / "completion.json")["phase_seconds"]


def test_stop_file_reaps_worker(tmp_path):
    directory = tmp_path / "attempt"
    directory.mkdir()

    def request_stop(pid):
        (tmp_path / "STOP").touch()
        return 1

    outcome = runner.supervise(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        directory,
        tmp_path,
        rss_reader=request_stop,
        poll_seconds=0.01,
    )
    assert outcome["classification"] == "interrupted"
    assert outcome["returncode"] is not None


def test_partial_analysis_no_acceptance_before_supervision(tmp_path):
    study = tiny_study()
    progress = runner.reconstruct(tmp_path, study, fake_verify)
    upcoming = progress["next"]
    directory = tmp_path / upcoming["directory"]
    directory.mkdir(parents=True)
    atomic_immutable_json(directory / "request.json", upcoming["request"])
    partial = runner.reconstruct(tmp_path, study, fake_verify, allow_partial=True)
    assert partial["completed_hours"] == 0 and partial["active_attempt"]
    runner.reconcile_interruptions(tmp_path)
    resumed = runner.reconstruct(tmp_path, study, fake_verify)
    assert resumed["uncertain_interrupted_timings"] == 1
    assert resumed["next"]["request"] == upcoming["request"]


@pytest.mark.parametrize("clean,commit", [(False, "abc"), (True, "wrong")])
def test_no_output_for_dirty_or_wrong_commit(tmp_path, monkeypatch, clean, commit):
    monkeypatch.setattr(model, "context", lambda: dict(clean=clean, commit="abc"))
    root = tmp_path / "study"
    with pytest.raises(ValueError, match="clean reviewed"):
        runner.run(root, commit)
    assert not root.exists()


def test_root_stop_and_resume_exactly_once(tmp_path, monkeypatch):
    root = tmp_path / "study"
    monkeypatch.setattr(model, "context", lambda: dict(clean=True, commit="abc"))
    monkeypatch.setattr(model, "specification", tiny_study)
    monkeypatch.setattr(runner, "verified_inputs", lambda: None)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.0)
    monkeypatch.setattr(
        runner, "verify_attempt", lambda root, d, r, p: fake_verify(d, r)
    )
    calls = []

    def fake_supervise(command, directory, root):
        interrupted = not calls
        calls.append(directory)
        record = dict(
            classification="interrupted" if interrupted else "exited",
            returncode=0,
            wall_seconds=10,
        )
        atomic_immutable_json(directory / "supervision.json", record)
        if not interrupted:
            atomic_immutable_json(directory / "start.json", dict(causal_start={}))
            model.atomic_gzip_json(
                directory / "result.json.gz",
                dict(iteration=100, accepted=True, next_soc_mwh=[4.0]),
            )
        return record

    monkeypatch.setattr(runner, "supervise", fake_supervise)
    with pytest.raises(KeyboardInterrupt):
        runner.run(root, "abc")
    result = runner.run(root, "abc", resume=True)
    assert result["complete"] and result["completed_hours"] == 6
    assert len(calls) == 7 and result["observed_attempt_wall_seconds"] == 70
    runner.run(root, "abc", resume=True)
    assert len(calls) == 7


def test_actual_tracy_window_construction_without_solve():
    from experiments.case118_tracy_2021.prepare import SOURCE

    if (
        not SOURCE.exists()
        or not (model.HERE / "results/stage_c/analysis.json").exists()
    ):
        pytest.skip("owner source or accepted annual archives unavailable")
    study = model.specification()
    prepared = model.verified_inputs()
    assert len(study["trajectories"]) == 16
    for trajectory in study["trajectories"][:4]:
        req = runner.make_request(
            trajectory,
            0,
            "primary",
            trajectory["initial_soc_mwh"],
            None,
            None,
            None,
            study["storage_device_ids"],
        )
        kwargs = model.request_kwargs(prepared, req)
        build = build_opf_multistep(**kwargs)
        _, assigned = model.prepare_start(build, kwargs, req, model.HERE)
        np.testing.assert_array_equal(assigned["soc"][:, 0], req["initial_soc_mwh"])
        assert build.temporal_assembly == "vectorized"
        assert not build.automatic_sparse_dispatch
        assert len(kwargs["loads"]) == 99 and len(kwargs["storage"]) == 27
        np.testing.assert_array_equal(
            [s.terminal_soc for s in kwargs["storage"]], req["target_soc_mwh"]
        )


def test_invocation_and_action_timing_across_repeated_stops(tmp_path, monkeypatch):
    """Parent time is real effort; downtime and replay are not worker latency."""
    now = [0.0]
    epoch = datetime(2026, 9, 29, tzinfo=timezone.utc)
    monkeypatch.setattr(runner.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        runner, "utc", lambda: (epoch + timedelta(seconds=now[0])).isoformat()
    )
    monkeypatch.setattr(model, "context", lambda: dict(clean=True, commit="abc"))
    monkeypatch.setattr(model, "specification", tiny_study)
    monkeypatch.setattr(runner, "verified_inputs", lambda: None)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.0)
    original = runner.reconstruct

    def reconstruct(*args, **kwargs):
        now[0] += 3
        return original(*args, **kwargs)

    def verify(root, directory, request, prepared):
        now[0] += 2
        return fake_verify(directory, request)

    calls = []

    def supervise(command, directory, root):
        now[0] += 10
        calls.append(directory)
        stopped = len(calls) <= 2
        record = dict(
            classification="interrupted" if stopped else "exited",
            returncode=0,
            wall_seconds=10,
        )
        atomic_immutable_json(directory / "supervision.json", record)
        if not stopped:
            atomic_immutable_json(directory / "start.json", dict(causal_start={}))
            model.atomic_gzip_json(
                directory / "result.json.gz",
                dict(iteration=100, accepted=True, next_soc_mwh=[4.0]),
            )
        return record

    monkeypatch.setattr(runner, "reconstruct", reconstruct)
    monkeypatch.setattr(runner, "verify_attempt", verify)
    monkeypatch.setattr(runner, "supervise", supervise)
    root = tmp_path / "study"
    for resume, gap in ((False, 100), (True, 200)):
        with pytest.raises(KeyboardInterrupt):
            runner.run(root, "abc", resume=resume)
        now[0] += gap
    result = runner.run(root, "abc", resume=True)
    before = runner.timing_summary(root)
    now[0] += 50
    runner.run(root, "abc", resume=True)  # replay only; no double-counted action
    summary = runner.timing_summary(root)
    assert result["complete"] and result["observed_attempt_wall_seconds"] == 80
    assert len(calls) == 8
    assert [g["seconds"] for g in summary["gaps"]] == [100, 200, 50]
    assert all(g["classification"] == "downtime" for g in summary["gaps"])
    assert summary["observed_invocation_wall_seconds"] == 134
    assert sum(i["parent_seconds"] for i in summary["invocations"]) == 54
    assert summary["actions"] == before["actions"]
    first = summary["actions"]["trajectory-00/hour-00"]
    assert first["observed_active_seconds"] == 35
    assert first["parent_seconds"] == 5
    assert first["total_calendar_latency_seconds"] == 341
    assert not summary["timing_is_lower_bound"]
    assert len(list((root / "invocations").glob("*/finished.json"))) == 4
    assert all("KeyboardInterrupt" in i["outcome"] for i in summary["invocations"][:2])


def test_unfinished_invocation_marks_unknown_tail_not_downtime(tmp_path, monkeypatch):
    now = [0.0]
    epoch = datetime(2026, 9, 29, tzinfo=timezone.utc)
    monkeypatch.setattr(runner.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        runner, "utc", lambda: (epoch + timedelta(seconds=now[0])).isoformat()
    )
    timing = runner.InvocationTiming(tmp_path, 0, runner.utc(), False)
    now[0] = 4
    timing.record["active_span"] = dict(phase="worker_supervision", action="a")
    timing.save()  # Simulate abrupt termination: no finish and unknown tail.
    now[0] = 100
    resumed = runner.InvocationTiming(tmp_path, 100, runner.utc(), True)
    now[0] = 110
    resumed.finish("complete")
    summary = runner.timing_summary(tmp_path)
    assert summary["timing_is_lower_bound"]
    assert summary["observed_invocation_wall_seconds"] == 14
    assert summary["invocations"][0]["parent_seconds"] is None
    assert summary["gaps"][0]["seconds"] == 96
    assert summary["gaps"][0]["classification"] == "unknown_active_tail_or_downtime"
