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
    monkeypatch.setattr(continuation, "require_descendant", lambda *a: None)
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
    # Repeating the bound command is harmless and never appends a duplicate.
    runner.run(
        root, current["commit"], resume=True, continue_from=continuation.ORIGINAL
    )
    assert len(continuation.record_paths(root)) == 1
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


def test_successive_transitions_failed_primary_bind_retry_and_next_trajectory(
    tmp_path,
    monkeypatch,
):
    """Exercise run/resume/analyze, stubbing only inputs, Git and solver work."""
    current = dict(commit=continuation.ORIGINAL, clean=True, policy="unchanged")
    study = tiny_study()
    study["trajectories"].append({**study["trajectories"][0], "id": 1})
    monkeypatch.setattr(model, "context", lambda: current.copy())
    monkeypatch.setattr(model, "specification", lambda: study)
    monkeypatch.setattr(runner, "verified_inputs", lambda: None)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1)
    monkeypatch.setattr(continuation, "source_check", lambda commit: None)
    monkeypatch.setattr(continuation, "require_descendant", lambda *a: None)
    calls = []

    def verify(root, directory, request, prepared):
        # Check era resolution on every reconstruction, not merely latest state.
        assert continuation.attempt_context(root, directory) == model.read(
            directory / "execution-context.json"
        )
        return fake_verify(directory, request)

    monkeypatch.setattr(runner, "verify_attempt", verify)

    def supervise(command, directory, root):
        request = model.read(directory / "request.json")
        calls.append(request)
        accepted = len(calls) != 2  # First post-transition primary hits its limit.
        outcome = dict(classification="exited", returncode=0, wall_seconds=1)
        atomic_immutable_json(directory / "supervision.json", outcome)
        atomic_immutable_json(directory / "start.json", dict(causal_start={}))
        atomic_immutable_json(directory / "completion.json", {})
        model.atomic_gzip_json(
            directory / "x0.json.gz", dict(iteration=request["global_hour"])
        )
        model.atomic_gzip_json(
            directory / "result.json.gz",
            dict(
                accepted=accepted,
                iteration=request["global_hour"],
                next_soc_mwh=[4.0] if accepted else None,
            ),
        )
        if len(calls) in (1, 2, 3):
            (root / "STOP").touch()
        return outcome

    monkeypatch.setattr(runner, "supervise", supervise)
    root = tmp_path / "study"
    with pytest.raises(KeyboardInterrupt):
        runner.run(root, current["commit"])
    current["commit"] = "second"
    with pytest.raises(KeyboardInterrupt):
        runner.run(root, "second", resume=True, continue_from=continuation.ORIGINAL)
    original_record = (root / continuation.RECORD).read_bytes()
    retained = {p: p.read_bytes() for p in root.rglob("*") if p.is_file()}
    current["commit"] = "third"
    current["clean"] = False
    with pytest.raises(ValueError, match="clean reviewed"):
        runner.run(root, "third", resume=True, continue_from="second", preflight=True)
    current["clean"] = True
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: None)
    with pytest.raises(RuntimeError, match="RSS monitoring unavailable"):
        runner.run(root, "third", resume=True, continue_from="second", preflight=True)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 1)
    preview = runner.run(
        root, "third", resume=True, continue_from="second", preflight=True
    )
    assert preview["transition_needed"] and preview["completed_hours"] == 1
    assert preview["next"]["request"]["role"] == "causal_1"
    assert {p: p.read_bytes() for p in root.rglob("*") if p.is_file()} == retained
    assert len(calls) == 2

    publish = runner.atomic_immutable_json

    def fail_after_binding(path, value):
        publish(path, value)
        if path.name == "audit-continuation-001.json":
            raise RuntimeError("synthetic stop after binding, before worker launch")

    monkeypatch.setattr(runner, "atomic_immutable_json", fail_after_binding)
    with pytest.raises(RuntimeError, match="after binding"):
        runner.run(root, "third", resume=True, continue_from="second")
    assert len(calls) == 2
    second_record = (root / "audit-continuation-001.json").read_bytes()
    monkeypatch.setattr(runner, "atomic_immutable_json", publish)
    # The identical launch command is retryable after binding was published.
    with pytest.raises(KeyboardInterrupt):
        runner.run(root, "third", resume=True, continue_from="second")
    assert calls[2]["role"] == "causal_1"
    assert calls[2]["hour"] == 1 and calls[2]["initial_soc_mwh"] == [4.0]
    # Ordinary same-source resume then completes both trajectories exactly once.
    result = runner.run(root, "third", resume=True)
    assert result["completed_hours"] == 12 and result["complete"]
    assert len(calls) == 13  # Twelve accepted calls, one rejected primary.
    assert calls[7]["trajectory"] == 1 and calls[7]["hour"] == 0
    assert (root / continuation.RECORD).read_bytes() == original_record
    assert (root / "audit-continuation-001.json").read_bytes() == second_record
    for path, content in retained.items():
        if "trajectory-" in str(path) or path.name == "binding.json":
            assert path.read_bytes() == content
    analysis = runner.analyze(root)
    assert analysis["completed_hours"] == 12
    assert [r["execution_context"]["commit"] for r in analysis["continuations"]] == [
        "second",
        "third",
    ]
    with pytest.raises(ValueError, match="previous execution"):
        runner.run(root, "third", resume=True, continue_from=continuation.ORIGINAL)


def test_chain_rejects_changed_predecessor_and_missing_record(tmp_path, monkeypatch):
    monkeypatch.setattr(continuation, "source_check", lambda commit: None)
    monkeypatch.setattr(continuation, "require_descendant", lambda *a: None)
    original = dict(commit=continuation.ORIGINAL, clean=True)
    atomic_immutable_json(tmp_path / "binding.json", dict(context=original))
    progress = dict(
        blocking_failure=None, active_attempt=None, completed_hours=0, next=None
    )
    first = continuation.prepare(tmp_path, {**original, "commit": "second"}, progress)
    atomic_immutable_json(tmp_path / continuation.RECORD, first)
    second = continuation.prepare(tmp_path, {**original, "commit": "third"}, progress)
    path = continuation.next_record_path(tmp_path)
    atomic_immutable_json(path, second)
    assert len(continuation.load_chain(tmp_path)) == 2
    second["previous_record"]["sha256"] = "wrong"
    model.atomic_json(path, second)
    with pytest.raises(ValueError, match="predecessor"):
        continuation.load(tmp_path)
    path.rename(tmp_path / "audit-continuation-002.json")
    with pytest.raises(ValueError, match="sequence"):
        continuation.load(tmp_path)


def test_continuation_requires_forward_ancestry(monkeypatch):
    monkeypatch.setattr(continuation, "git", lambda *a: "old")
    continuation.require_descendant("old", "new")
    for old, new in [("old", "old"), ("unrelated", "new")]:
        with pytest.raises(ValueError, match="advance"):
            continuation.require_descendant(old, new)


def test_interrupted_attempt_can_cross_a_reviewed_source_transition(
    tmp_path, monkeypatch
):
    from tests.test_tracy_stage_d import append_attempt

    old = dict(commit=continuation.ORIGINAL, clean=True)
    atomic_immutable_json(tmp_path / "binding.json", dict(context=old))
    study = tiny_study()
    first = runner.reconstruct(tmp_path, study, fake_verify)
    directory = append_attempt(tmp_path, first, outcome="interrupted")
    progress = runner.reconstruct(tmp_path, study, fake_verify)
    assert progress["completed_hours"] == 0
    assert progress["next"]["request"] == first["next"]["request"]
    monkeypatch.setattr(continuation, "source_check", lambda commit: None)
    record = continuation.prepare(tmp_path, {**old, "commit": "new"}, progress)
    atomic_immutable_json(tmp_path / continuation.RECORD, record)
    assert continuation.attempt_context(tmp_path, directory) == old
    assert runner.reconstruct(tmp_path, study, fake_verify) == progress


def test_retained_two_era_prefix_and_pending_start_rehearsal(tmp_path):
    """Real records, physics, context routing and start construction; no solve."""
    from experiments.case118_tracy_2021.prepare import SOURCE
    from cvxopf import build_opf_multistep

    source = model.HERE / "results/stage_d"
    failed = source / "trajectory-08/hour-00/attempt-000"
    if not SOURCE.exists() or not (failed / "completion.json").exists():
        pytest.skip("owner source or retained stopped prefix unavailable")
    for name in ("binding.json", continuation.RECORD):
        (tmp_path / name).symlink_to(source / name)
    for index in range(8):
        name = f"trajectory-{index:02d}"
        (tmp_path / name).symlink_to(source / name, target_is_directory=True)
    last = tmp_path / failed.relative_to(source)
    last.parent.mkdir(parents=True)
    last.symlink_to(failed, target_is_directory=True)
    prepared = runner.verified_inputs()
    study = model.read(tmp_path / "binding.json")["study"]

    def verify(d, q):
        return runner.verify_attempt(tmp_path, d, q, prepared)

    before = runner.reconstruct(tmp_path, study, verify, allow_partial=True)
    assert before["completed_hours"] == 48
    # A simulated reviewed context is used only in this disposable test journal.
    # Production preflight still refuses a dirty tree; no real binding is written.
    current = {**model.context(), "clean": True}
    previous = continuation.execution_context(tmp_path)["commit"]
    assert continuation.transition_needed(tmp_path, current, previous)
    record = continuation.prepare(tmp_path, current, before)
    atomic_immutable_json(continuation.next_record_path(tmp_path), record)
    after = runner.reconstruct(tmp_path, study, verify, allow_partial=True)
    assert after == before
    assert not continuation.transition_needed(tmp_path, current, previous)
    request = after["next"]["request"]
    assert request["role"] == "causal_1" and request["global_hour"] == 8581
    kwargs = model.request_kwargs(prepared, request)
    build = build_opf_multistep(**kwargs)
    _, assigned = model.prepare_start(build, kwargs, request, tmp_path)
    import numpy as np

    np.testing.assert_array_equal(assigned["soc"][:, 0], request["initial_soc_mwh"])
