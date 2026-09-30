"""Audit-only source continuation, without numerical execution."""

import pytest

from experiments.case118_annual_hierarchy.streaming_schema import atomic_immutable_json
from experiments.case118_tracy_2021 import run_stage_d as runner, stage_d as model
from experiments.case118_tracy_2021 import stage_d_continuation as continuation
from tests.test_tracy_stage_d import tiny_study, fake_verify


def test_audit_continuation_preserves_prefix_and_restarts_next_action(
    tmp_path, monkeypatch
):
    current = dict(commit=continuation.ORIGINAL, clean=True, policy="unchanged")
    monkeypatch.setattr(model, "context", lambda: current.copy())
    monkeypatch.setattr(model, "specification", tiny_study)
    monkeypatch.setattr(runner, "verified_inputs", lambda: None)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1.0)
    monkeypatch.setattr(
        runner, "verify_attempt", lambda root, d, r, p: fake_verify(d, r)
    )
    checked = []
    monkeypatch.setattr(
        continuation, "source_check", lambda commit: checked.append(commit)
    )
    calls = []

    def supervise(command, directory, root):
        calls.append(model.read(directory / "request.json"))
        outcome = dict(classification="exited", returncode=0, wall_seconds=1)
        atomic_immutable_json(directory / "supervision.json", outcome)
        atomic_immutable_json(directory / "start.json", dict(causal_start={}))
        atomic_immutable_json(directory / "completion.json", {})
        model.atomic_gzip_json(directory / "x0.json.gz", dict(iteration=100))
        model.atomic_gzip_json(
            directory / "result.json.gz",
            dict(iteration=100, accepted=True, next_soc_mwh=[4.0]),
        )
        if len(calls) == 1:
            (root / "STOP").touch()
        return outcome

    monkeypatch.setattr(runner, "supervise", supervise)
    root = tmp_path / "study"
    with pytest.raises(KeyboardInterrupt):
        runner.run(root, continuation.ORIGINAL)
    original_binding = (root / "binding.json").read_bytes()
    original_result = (
        root / "trajectory-00/hour-00/attempt-000/result.json.gz"
    ).read_bytes()
    current["commit"] = "new-reviewed-commit"
    with pytest.raises(ValueError, match="differs"):
        runner.run(root, current["commit"], resume=True)
    assert not (root / continuation.RECORD).exists()
    result = runner.run(
        root, current["commit"], resume=True, continue_from=continuation.ORIGINAL
    )
    assert result["completed_hours"] == 6
    assert [r["hour"] for r in calls] == list(range(6))
    assert calls[1]["initial_soc_mwh"] == [4.0]
    assert (root / "binding.json").read_bytes() == original_binding
    assert (
        root / "trajectory-00/hour-00/attempt-000/result.json.gz"
    ).read_bytes() == original_result
    record = continuation.load(root)
    assert (
        record["completed_hours"] == 1
        and record["next_request"]["request"]["hour"] == 1
    )
    assert record["execution_context"] == current
    assert (
        model.read(root / "trajectory-00/hour-01/attempt-000/execution-context.json")
        == current
    )
    runner.run(root, current["commit"], resume=True)
    assert len(calls) == 6
    analysis = runner.analyze(root)
    assert analysis["completed_hours"] == 6
    assert analysis["execution_context"]["commit"] == continuation.ORIGINAL
    assert analysis["continuation"]["execution_context"] == current
    assert checked and set(checked) == {current["commit"]}
    with pytest.raises(ValueError, match="cannot replace"):
        runner.run(
            root, current["commit"], resume=True, continue_from=continuation.ORIGINAL
        )
    path = root / "trajectory-00/hour-00/attempt-000/start.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="historical artifact"):
        continuation.load(root)


@pytest.mark.parametrize(
    "field,value",
    [("policy", "changed"), ("clean", False), ("packages", {"numpy": "other"})],
)
def test_continuation_rejects_noncommit_context_changes(field, value):
    old = dict(commit=continuation.ORIGINAL, clean=True, policy="same", packages={})
    new = {**old, "commit": "new", field: value}
    with pytest.raises(ValueError):
        continuation.compatible(old, new)


def test_source_gate_rejects_model_changes_and_wrong_ancestry(monkeypatch):
    monkeypatch.setattr(
        continuation,
        "git",
        lambda *a: (
            continuation.AUDIT_FIX if a[0] == "merge-base" else "src/cvxopf/storage.py"
        ),
    )
    with pytest.raises(ValueError, match="out-of-scope"):
        continuation.source_check("new")
    monkeypatch.setattr(continuation, "git", lambda *a: "wrong")
    with pytest.raises(ValueError, match="descend"):
        continuation.source_check("new")


def test_continued_worker_refuses_wrong_attempt_context(tmp_path, monkeypatch):
    atomic_immutable_json(tmp_path / "binding.json", dict(context={"commit": "old"}))
    atomic_immutable_json(tmp_path / "request.json", {})
    atomic_immutable_json(tmp_path / "execution-context.json", dict(commit="wrong"))
    monkeypatch.setattr(
        continuation, "execution_context", lambda root: {"commit": "new"}
    )
    with pytest.raises(ValueError, match="attempt execution context"):
        model.worker(tmp_path, tmp_path)
