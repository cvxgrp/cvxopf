"""Scientific invariants for the fresh factorial replay; no native solves."""

from dataclasses import replace
import json
from types import SimpleNamespace

import numpy as np
import pytest

from experiments.case118_annual_hierarchy import streaming_runner as streaming
from experiments.case118_annual_hierarchy.p0_fixture import load_p0_fixture
from experiments.case118_annual_hierarchy.s5_speculative_attempt import PreparedAttempt, TargetFreeSource
from experiments.case118_annual_hierarchy.s5_speculative_policy import AttemptSpec, WindowKey
from experiments.case118_vectorization_replay import worker, run as replay
from experiments.case118_spacetime_pq_replay import study
from experiments.case118_spacetime_pq_replay.analyze_study import paired, summarize, attempt_diagnostics


@pytest.mark.parametrize("mode", study.CONFIGURATIONS)
@pytest.mark.parametrize("replaying", [False, True])
def test_helper_start_mapping_and_replay_preserve_every_coordinate(tmp_path, monkeypatch, mode, replaying):
    fixture = load_p0_fixture(6)
    initial = {str(s.device_id): 500.0 for s in fixture.inputs.storage}
    target = {key: 400.0 for key in initial}
    storage = streaming._inner_storage(fixture.inputs, initial, target)
    step = streaming.build_window(fixture.inputs, "ac", 0, 3, storage)
    physical = streaming.complete_flat_start(step)
    rng = np.random.default_rng(349)
    physical = {name: variable.project(physical[name] + rng.normal(0, .01, variable.shape))
                for name, variable in streaming.variables_by_name(step).items()}
    config = study.CONFIGURATIONS[mode]
    build = streaming.build_window(
        replace(fixture.inputs, options=replace(fixture.inputs.options, vectorize_pq=config["vectorize_pq"])),
        "ac", 0, 3, storage, temporal_assembly=config["temporal_assembly"])
    vectorized = config["temporal_assembly"] == "vectorized"
    values = worker.pack_start(physical, build, list(initial.values())) if vectorized else physical
    key = WindowKey("s4b-shard-001", 0)
    prior = AttemptSpec(key, 5, 2)
    spec = AttemptSpec(key, 9, 2, prior.attempt_id) if replaying else prior
    evidence = object()
    retained = SimpleNamespace(assigned=physical, request_sha256="frozen")
    restart = SimpleNamespace(invocation=prior, request_sha256="frozen", raw=values,
                              assigned=values, source_kind="attempt", source_attempt_id="source",
                              evidence=evidence)
    monkeypatch.setattr(worker, "retained_path", lambda p: p)
    monkeypatch.setattr(worker, "load_retained_start", lambda p: restart if p == "restart" else retained)
    historical = dict(preceding_source=None)
    monkeypatch.setattr(worker, "checked", lambda ref: historical)
    monkeypatch.setattr(worker, "restore_source", lambda value: value)
    free = TargetFreeSource(AttemptSpec(key, 4, 1), "frozen", values)
    monkeypatch.setattr(worker, "read", lambda path: dict(invocation=dict(
        window=dict(shard_id=key.shard_id, iteration=0), order=4, source_slot=1)))
    monkeypatch.setattr(worker, "audit_candidate", lambda *a, **kw: SimpleNamespace(target_free_source=lambda: free))
    captured = []

    def prepare(*args, **kwargs):
        normalized = kwargs["target_free"].target_free_values("frozen")
        assert set(normalized) == set(physical)
        for name in physical:
            np.testing.assert_array_equal(normalized[name], physical[name])
        captured.append(normalized)
        return PreparedAttempt(spec, "frozen", step, initial, target, 3, physical,
                               physical, "attempt", "source", None)

    monkeypatch.setattr(worker, "prepare_attempt", prepare)
    request = dict(
        selected=dict(references={"primary_request.json": {}, "primary_start.json": {"path": "primary"}},
                      initial_soc_mwh=initial, target_soc_mwh=target, trajectory_start=0,
                      trajectory_stop=6, trajectory_initial_soc_mwh=initial),
        invocation=dict(window=dict(shard_id=key.shard_id, iteration=0), order=spec.order,
                        source_slot=2, replay_of=spec.replay_of),
        replay_start="restart" if replaying else None,
        target_free_directory="free", execution_configuration=config,
    )
    prepared = worker.prepare(tmp_path, fixture, None, request)
    assert bool(captured) is not replaying
    assert prepared.replay_start is (evidence if replaying else None)
    assert prepared.request_sha256 == "frozen"
    for name, expected in values.items():
        np.testing.assert_array_equal(prepared.raw[name], expected)
        np.testing.assert_array_equal(prepared.assigned[name], expected)
        np.testing.assert_array_equal(streaming.variables_by_name(prepared.build)[name].value, expected)


@pytest.mark.parametrize("mode", study.CONFIGURATIONS)
def test_configuration_reaches_fresh_subprocess_request(tmp_path, monkeypatch, mode):
    config = study.CONFIGURATIONS[mode]
    sample = dict(selected=[dict(shard_id="s4b-shard-001", iteration=0,
                                 references={"primary_request.json": {}})])
    (tmp_path / "sample.json").write_text(json.dumps(sample))
    monkeypatch.setattr(replay, "checked", lambda ref: dict(execution_context=dict(software_versions={})))
    monkeypatch.setattr(replay, "_software_versions", lambda: {})
    monkeypatch.setattr(replay, "load_s4_fixture", lambda: None)
    monkeypatch.setattr(replay, "_outer", lambda: None)

    class Captured(BaseException):
        pass

    def backend(root, **kwargs):
        folder = root / "attempt"
        folder.mkdir()
        command = kwargs["command"](AttemptSpec(WindowKey("s4b-shard-001", 0), 0, 0), folder)
        assert command[-2] == "experiments.case118_vectorization_replay.worker"
        request = json.loads((folder / "request.json").read_text())
        assert request["execution_configuration"] == config
        environment = json.loads((root / "environment.json").read_text())
        assert environment["execution_configuration"] == config
        raise Captured()

    monkeypatch.setattr(replay, "SubprocessBackend", backend)
    with pytest.raises(Captured):
        replay.run(tmp_path, execution_configuration=config)


def observation(hour, seconds, *, group="primary", weight=1, winner=0, accepted=True):
    return dict(iteration=hour, historical_group=group, stratum="stratum", population_weight=weight,
                historical_objective=100., historical_solve_seconds=100., historical_window_seconds=110.,
                new_solve_seconds=seconds, new_window_seconds=seconds * 2, new_build_seconds=1.,
                new_objective=100., new_winner_order=winner, accepted=accepted, new_status="optimal")


def test_weighted_fresh_comparison_preserves_helpers_partial_pairs_and_changed_winners():
    a = [observation(1, 40, weight=3), observation(2, 20), observation(3, 100, group="helper"), observation(4, 1)]
    b = [observation(1, 10, weight=3), observation(2, 10, winner=2), observation(3, 50, group="helper")]
    result = summarize(paired(a, b))
    assert result["matched"] == 3
    assert result["primary_matched"] == 2
    assert result["weighted_primary_means"]["first_solve_seconds"] == 35
    assert result["geometric_speedups"]["solve_seconds"] == pytest.approx(4 ** .75 * 2 ** .25)
    assert result["changed_winners"] == [2]
    assert [r["iteration"] for r in result["helper_cohort"]] == [3]


def test_rejected_pair_is_retained_but_not_a_speedup():
    rows = paired([observation(1, 100)], [observation(1, 5, accepted=False)])
    assert rows[0]["solve_seconds_speedup"] is None
    assert summarize(rows)["rejected_pairs"] == [1]
    assert summarize(rows)["geometric_speedups"] == {}


def test_comparison_rejects_changed_population_and_invalid_duration():
    with pytest.raises(ValueError, match="identity"):
        paired([observation(1, 100)], [observation(1, 10, weight=2)])
    with pytest.raises(ValueError, match="duration"):
        paired([observation(1, 100)], [observation(1, 0)])
    with pytest.raises(ValueError, match="Duplicate"):
        paired([observation(1, 100)] * 2, [])


def test_study_output_is_isolated_and_requires_fan(tmp_path):
    with pytest.raises(ValueError, match="fresh direct child"):
        study.preflight(tmp_path)
    with pytest.raises(ValueError, match="fan"):
        study.launch("unused", fan_on=False)


@pytest.mark.parametrize("value", [True, .05, float("nan"), "0"])
def test_configuration_rejects_unapproved_dispatch_policy(value):
    with pytest.raises(ValueError):
        worker.execution_configuration(dict(study.CONFIGURATIONS["both"], sparse_density_threshold=value))


def test_partial_attempt_accounting_and_effective_configuration_checks(tmp_path):
    config = study.CONFIGURATIONS["both"]
    directory = tmp_path / "run/s4b-shard-001/ac-000000-spec-00"
    directory.mkdir(parents=True)
    request = dict(execution_configuration=config,
                   invocation=dict(window=dict(iteration=0), order=0))
    (directory / "request.json").write_text(json.dumps(request))
    assert attempt_diagnostics(tmp_path, config)["attempt_outcomes"] == {"active_or_unreaped": 1}
    (directory / "lifecycle.json").write_text(json.dumps(dict(
        completion=dict(outcome="construction_error"), reason="worker_exited", artifacts={})))
    assert attempt_diagnostics(tmp_path, config)["attempt_outcomes"] == {"construction_error": 1}
    (directory / "execution_configuration.json").write_text(json.dumps(dict(
        **config, effective_sparse_density_threshold=.05)))
    with pytest.raises(ValueError, match="Effective dispatch"):
        attempt_diagnostics(tmp_path, config)
    (directory / "execution_configuration.json").write_text(json.dumps(dict(
        **config, effective_sparse_density_threshold=0.0)))
    (directory / "replica.json").write_text(json.dumps(dict(execution_configuration=study.CONFIGURATIONS["none"])))
    with pytest.raises(ValueError, match="Prepared representation"):
        attempt_diagnostics(tmp_path, config)
    (directory / "replica.json").unlink()
    (directory / "request.json").write_text(json.dumps(dict(request, execution_configuration=study.CONFIGURATIONS["none"])))
    with pytest.raises(ValueError, match="Attempt configuration"):
        attempt_diagnostics(tmp_path, config)


def test_no_winner_report_retains_failed_attempts(tmp_path):
    from experiments.case118_spacetime_pq_replay.analyze_study import run
    (tmp_path / "binding.json").write_text(json.dumps(dict(conditions=study.CONFIGURATIONS)))
    directory = tmp_path / "none/run/s4b-shard-001/ac-000000-spec-00"
    directory.mkdir(parents=True)
    (directory / "request.json").write_text(json.dumps(dict(
        execution_configuration=study.CONFIGURATIONS["none"],
        invocation=dict(window=dict(iteration=0), order=0))))
    (directory / "lifecycle.json").write_text(json.dumps(dict(
        completion=dict(outcome="construction_error"), reason="worker_exited", artifacts={})))
    result = run(tmp_path)
    assert not result["complete"]
    assert result["diagnostics"]["none"]["attempt_count"] == 1
    assert result["diagnostics"]["none"]["attempt_outcomes"] == {"construction_error": 1}
    assert "PARTIAL" in (tmp_path / "REPORT.md").read_text()
