from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, cast

import pytest

from experiments.case118_annual_hierarchy.p0_fixture import (
    load_p0_fixture,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.s4_fixture import S4Fixture
from experiments.case118_annual_hierarchy.streaming_schema import sha256_path
from experiments.m14_time_vectorization import m14c_prefix_analysis as analysis
from experiments.m14_time_vectorization import run_m14c_prefix_ladder as runner
from experiments.m14_time_vectorization.m14c_prefix_fixture import (
    M14C_INTEGRATION_COMMIT,
    PREFIX_EXECUTION_LIMITS,
    PREFIX_EXPECTED_HASHES,
    PREFIX_LADDER_HORIZONS,
    PrefixExecutionLimits,
    PrefixFixture,
    load_prefix_fixture,
)
from experiments.m14_time_vectorization import m14c_prefix_fixture as prefix_fixture


def test_prefix_fixture_freezes_exact_order_limits_and_s4_prefixes() -> None:
    assert PREFIX_LADDER_HORIZONS == (24, 168, 720)
    assert PREFIX_EXECUTION_LIMITS == {
        24: PrefixExecutionLimits(16_384.0, 600.0, 900.0),
        168: PrefixExecutionLimits(16_384.0, 1_800.0, 2_400.0),
        720: PrefixExecutionLimits(16_384.0, 3_600.0, 4_500.0),
    }
    for horizon in PREFIX_LADDER_HORIZONS:
        point = load_prefix_fixture(horizon)
        annual = point.annual
        assert point.inputs.horizon_steps == horizon
        assert point.inputs.delta == annual.inputs.delta == 1.0
        assert point.inputs.df_load_p.equals(annual.inputs.df_load_p.iloc[:horizon])
        assert point.inputs.df_load_q.equals(annual.inputs.df_load_q.iloc[:horizon])
        assert point.inputs.df_nd is not None and annual.inputs.df_nd is not None
        assert point.inputs.df_nd.equals(annual.inputs.df_nd.iloc[:horizon])
        assert point.inputs.storage == annual.inputs.storage
        assert annual.prefix_ladder_executed is True
        assert annual.annual_execution_authorized is True
        assert point.input_sha256 == PREFIX_EXPECTED_HASHES[horizon]["input"]
        assert point.scenario_sha256 == PREFIX_EXPECTED_HASHES[horizon]["scenario"]


def test_prefix_fixture_remains_reconstructable_after_authority_advances() -> None:
    point = prefix_fixture.load_prefix_fixture(24)
    assert point.annual.prefix_ladder_executed is True
    assert point.annual.annual_execution_authorized is True


def test_prefix_source_registry_binds_runner_analyzer_and_recursive_package() -> None:
    relative = {
        path.relative_to(runner.ROOT).as_posix()
        for path in runner.prefix_source_paths()
    }
    assert {
        "experiments/m14_time_vectorization/m14c_prefix_fixture.py",
        "experiments/m14_time_vectorization/run_m14c_prefix_ladder.py",
        "experiments/m14_time_vectorization/M14C_INTEGRATION.json",
    } <= relative
    assert "experiments/m14_time_vectorization/m14c_prefix_analysis.py" not in relative
    assert "src/cvxopf/testcases/__init__.py" in relative
    assert len(runner.prefix_source_fingerprint()) == 64
    analysis_relative = {
        path.relative_to(runner.ROOT).as_posix()
        for path in analysis.analysis_source_paths()
    }
    assert "experiments/m14_time_vectorization/m14c_prefix_analysis.py" in (
        analysis_relative
    )
    assert len(analysis.analysis_source_fingerprint()) == 64


def test_historical_execution_context_allows_new_analyzer_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = json.loads(
        (
            runner.ROOT
            / runner.PREFIX_LADDER_OUTPUT_DIRECTORY
            / "execution-context.json"
        ).read_text()
    )
    context.update(
        {
            "platform": "different-analysis-platform",
            "architecture": "different-analysis-architecture",
        }
    )
    monkeypatch.setattr(analysis, "_git", lambda *args: M14C_INTEGRATION_COMMIT)
    analysis._validate_execution_context(context)


def test_ladder_refuses_dirty_source_before_equivalence_or_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ladder"
    monkeypatch.setattr(
        runner, "ladder_execution_context", lambda: {"git_clean": False}
    )
    monkeypatch.setattr(
        runner,
        "outer_equivalence_gate",
        lambda: pytest.fail("equivalence must not run from dirty source"),
    )
    with pytest.raises(ValueError, match="clean committed"):
        runner.run_prefix_ladder(directory)
    assert not directory.exists()


def test_ladder_refuses_missing_integration_ancestry_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ladder"
    context = {
        "git_clean": True,
        "prefix_ladder_executed": False,
        "annual_execution_authorized": False,
    }
    monkeypatch.setattr(runner, "ladder_execution_context", lambda: context)
    monkeypatch.setattr(runner, "_git", lambda *args: "wrong-merge-base")
    monkeypatch.setattr(
        runner,
        "outer_equivalence_gate",
        lambda: pytest.fail("equivalence must not run without integration ancestry"),
    )
    with pytest.raises(ValueError, match="not an ancestor"):
        runner.run_prefix_ladder(directory)
    assert not directory.exists()


def _write_supervision(
    directory: Path, horizon: int, classification: str
) -> Mapping[str, object]:
    directory.mkdir()
    supervision = {
        "schema_version": 1,
        "horizon_steps": horizon,
        "classification": classification,
    }
    (directory / "supervision.json").write_text(json.dumps(supervision))
    return supervision


def test_ladder_stops_after_first_nonaccepted_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ladder"
    root_context = {
        "git_clean": True,
        "git_commit": "commit",
        "source_fingerprint": "source",
        "prefix_ladder_executed": False,
        "annual_execution_authorized": False,
    }
    monkeypatch.setattr(runner, "ladder_execution_context", lambda: root_context)
    monkeypatch.setattr(runner, "_git", lambda *args: M14C_INTEGRATION_COMMIT)
    monkeypatch.setattr(runner, "outer_equivalence_gate", lambda: {"equivalent": True})
    monkeypatch.setattr(
        runner,
        "prefix_execution_context",
        lambda horizon: {
            "git_commit": "commit",
            "source_fingerprint": "source",
            "horizon_steps": horizon,
        },
    )
    calls: list[int] = []

    def supervise(
        point_directory: Path,
        *,
        horizon_steps: int,
        context: Mapping[str, object],
        limits: PrefixExecutionLimits,
    ) -> Mapping[str, object]:
        del context, limits
        calls.append(horizon_steps)
        return _write_supervision(
            point_directory,
            horizon_steps,
            "accepted" if horizon_steps == 24 else "solver_failure",
        )

    monkeypatch.setattr(runner, "_supervise_prefix", supervise)
    result = runner.run_prefix_ladder(directory)
    assert calls == [24, 168]
    assert result["classification"] == "stopped"
    assert result["attempted_horizons"] == [24, 168]
    assert result["accepted_horizons"] == [24]
    assert result["stopped_horizon"] == 168
    assert result["annual_execution_authorized"] is False
    assert not (directory / "prefix-0720").exists()


def test_ladder_retains_nonpromotable_root_after_parent_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ladder"
    root_context = {
        "git_clean": True,
        "git_commit": "commit",
        "source_fingerprint": "source",
        "prefix_ladder_executed": False,
        "annual_execution_authorized": False,
    }
    monkeypatch.setattr(runner, "ladder_execution_context", lambda: root_context)
    monkeypatch.setattr(runner, "_git", lambda *args: M14C_INTEGRATION_COMMIT)
    monkeypatch.setattr(runner, "outer_equivalence_gate", lambda: {"equivalent": True})
    monkeypatch.setattr(
        runner,
        "prefix_execution_context",
        lambda horizon: {
            "git_commit": "commit",
            "source_fingerprint": "source",
            "horizon_steps": horizon,
        },
    )

    def interrupt(point_directory: Path, **kwargs: object) -> Mapping[str, object]:
        del kwargs
        _write_supervision(point_directory, 24, "supervisor_interrupted")
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "_supervise_prefix", interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.run_prefix_ladder(directory)
    result = json.loads((directory / "ladder-result.json").read_text())
    assert result["classification"] == "supervisor_interrupted"
    assert result["execution_complete"] is False
    assert result["attempted_horizons"] == [24]
    assert result["accepted_horizons"] == []
    assert (directory / "ladder-progress.json").is_file()


def test_ladder_retains_root_interruption_between_prefixes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ladder"
    root_context = {
        "git_clean": True,
        "git_commit": "commit",
        "source_fingerprint": "source",
        "prefix_ladder_executed": False,
        "annual_execution_authorized": False,
    }
    monkeypatch.setattr(runner, "ladder_execution_context", lambda: root_context)
    monkeypatch.setattr(runner, "_git", lambda *args: M14C_INTEGRATION_COMMIT)
    monkeypatch.setattr(runner, "outer_equivalence_gate", lambda: {"equivalent": True})

    def point_context(horizon: int) -> Mapping[str, object]:
        if horizon == 168:
            raise KeyboardInterrupt
        return {
            "git_commit": "commit",
            "source_fingerprint": "source",
            "horizon_steps": horizon,
        }

    monkeypatch.setattr(runner, "prefix_execution_context", point_context)
    monkeypatch.setattr(
        runner,
        "_supervise_prefix",
        lambda point_directory, **kwargs: _write_supervision(
            point_directory, 24, "accepted"
        ),
    )
    with pytest.raises(KeyboardInterrupt):
        runner.run_prefix_ladder(directory)
    result = json.loads((directory / "ladder-result.json").read_text())
    assert result["attempted_horizons"] == [24]
    assert result["accepted_horizons"] == [24]
    assert result["stopped_horizon"] == 168
    assert result["interrupted_horizon"] == 168


def test_prefix_supervisor_requires_successful_rss_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "prefix"
    context = {
        "schema_version": 1,
        "git_commit": "commit",
        "source_fingerprint": "source",
        "prefix_input_sha256": "input",
    }
    outer_path = directory / "outer-plan.json.gz"

    class FakeProcess:
        pid = 123

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    def launch(*args: object, **kwargs: object) -> FakeProcess:
        del args, kwargs
        outer_path.write_bytes(b"outer")
        worker = {
            "classification": "accepted",
            "outer_plan": {
                "artifact": {
                    "sha256": sha256_path(outer_path),
                    "bytes": outer_path.stat().st_size,
                }
            },
        }
        (directory / "worker-result.json").write_text(json.dumps(worker))
        return FakeProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: None)
    monkeypatch.setattr(
        runner, "_safe_prefix_execution_context", lambda horizon: context
    )
    result = runner._supervise_prefix(
        directory,
        horizon_steps=24,
        context=context,
        limits=PrefixExecutionLimits(1.0, 1.0, 1.0, 1e-6),
    )
    assert result["classification"] == "resource_measurement_failure"


def test_prefix_supervisor_terminates_and_retains_keyboard_interrupt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = {
        "git_commit": "commit",
        "source_fingerprint": "source",
        "prefix_input_sha256": "input",
    }
    terminated: list[int] = []

    class FakeProcess:
        pid = 321

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return -15

    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *args, **kwargs: FakeProcess()
    )
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 10.0)
    monkeypatch.setattr(
        runner.time, "sleep", lambda seconds: (_ for _ in ()).throw(KeyboardInterrupt())
    )
    monkeypatch.setattr(
        runner, "_terminate", lambda process: terminated.append(process.pid)
    )
    monkeypatch.setattr(
        runner, "_safe_prefix_execution_context", lambda horizon: context
    )
    directory = tmp_path / "prefix"
    with pytest.raises(KeyboardInterrupt):
        runner._supervise_prefix(
            directory,
            horizon_steps=24,
            context=context,
            limits=PrefixExecutionLimits(100.0, 10.0, 20.0, 0.01),
        )
    supervision = json.loads((directory / "supervision.json").read_text())
    assert terminated == [321]
    assert supervision["classification"] == "supervisor_interrupted"
    assert supervision["supervisor_interruption"].startswith("KeyboardInterrupt")
    assert not (directory / "active-worker.json").exists()


def test_resource_trigger_precedes_interrupt_during_termination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = {
        "git_commit": "commit",
        "source_fingerprint": "source",
        "prefix_input_sha256": "input",
    }
    terminate_calls = 0

    class FakeProcess:
        pid = 654

        def poll(self) -> int | None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return -15

    def terminate(process: FakeProcess) -> None:
        nonlocal terminate_calls
        del process
        terminate_calls += 1
        if terminate_calls == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *args, **kwargs: FakeProcess()
    )
    monkeypatch.setattr(runner, "_child_rss_mib", lambda pid: 101.0)
    monkeypatch.setattr(runner, "_terminate", terminate)
    monkeypatch.setattr(
        runner, "_safe_prefix_execution_context", lambda horizon: context
    )
    directory = tmp_path / "prefix"
    with pytest.raises(KeyboardInterrupt):
        runner._supervise_prefix(
            directory,
            horizon_steps=24,
            context=context,
            limits=PrefixExecutionLimits(100.0, 10.0, 20.0, 0.01),
        )
    supervision = json.loads((directory / "supervision.json").read_text())
    assert supervision["classification"] == "rss_limit"
    assert supervision["resource_triggers"] == ["rss_limit"]
    assert supervision["supervisor_interruption"].startswith("KeyboardInterrupt")


def test_nonzero_exit_cannot_inherit_worker_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "prefix"
    context = {
        "git_commit": "commit",
        "source_fingerprint": "source",
        "prefix_input_sha256": "input",
    }

    class FakeProcess:
        pid = 123

        def poll(self) -> int:
            return 9

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 9

    def launch(*args: object, **kwargs: object) -> FakeProcess:
        del args, kwargs
        (directory / "worker-result.json").write_text(
            json.dumps({"classification": "accepted"})
        )
        return FakeProcess()

    monkeypatch.setattr(runner.subprocess, "Popen", launch)
    monkeypatch.setattr(
        runner, "_safe_prefix_execution_context", lambda horizon: context
    )
    result = runner._supervise_prefix(
        directory,
        horizon_steps=24,
        context=context,
        limits=PrefixExecutionLimits(100.0, 10.0, 20.0, 0.01),
    )
    assert result["classification"] == "worker_process_failure"


def test_post_launch_supervisor_error_is_not_labeled_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = {
        "git_commit": "commit",
        "source_fingerprint": "source",
        "prefix_input_sha256": "input",
    }

    class FakeProcess:
        pid = 777

        def poll(self) -> int:
            return 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *args, **kwargs: FakeProcess()
    )
    monkeypatch.setattr(
        runner,
        "atomic_json",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("marker write")),
    )
    monkeypatch.setattr(
        runner, "_safe_prefix_execution_context", lambda horizon: context
    )
    result = runner._supervise_prefix(
        tmp_path / "prefix",
        horizon_steps=24,
        context=context,
        limits=PrefixExecutionLimits(100.0, 10.0, 20.0, 0.01),
    )
    assert result["classification"] == "supervisor_failure"
    assert result["launch_error"] is None
    assert str(result["supervisor_error"]).startswith("OSError: marker write")


@pytest.mark.parametrize(
    ("status", "missing", "identity_error", "accepted", "expected"),
    [
        ("optimal", ["objective"], None, False, "unusable_primal"),
        ("optimal", [], "storage identity", False, "residual_rejection"),
        ("optimal", [], None, False, "residual_rejection"),
    ],
)
def test_outer_outcomes_distinguish_unusable_and_residual_rejection(
    status: str,
    missing: list[str],
    identity_error: str | None,
    accepted: bool,
    expected: str,
) -> None:
    outer = SimpleNamespace(
        exception=None,
        accepted_primal=accepted,
        audit=SimpleNamespace(
            status=status,
            missing_or_nonfinite_fields=missing,
            identity_error=identity_error,
        ),
    )
    assert runner._outer_outcome_classification(outer) == expected


@pytest.mark.parametrize(
    ("declared", "status"),
    [
        ("solver_failure", "optimal"),
        ("accepted", "infeasible"),
        ("residual_rejection", "optimal"),
    ],
)
def test_analyzer_rejects_worker_labels_that_contradict_audit_evidence(
    declared: str, status: str
) -> None:
    context = {"git_commit": "commit", "source_fingerprint": "source"}
    worker = {
        "schema_version": 1,
        "horizon_steps": 24,
        "classification": declared,
        "exception": None,
        "start_context": context,
        "end_context": context,
        "context_matches": True,
        "resource_samples": [{"phase": phase} for phase in analysis.EXPECTED_PHASES],
        "outer_plan": {
            "accepted_primal": status == "optimal",
            "status": status,
            "missing_or_nonfinite_fields": [],
            "identity_error": None,
            "audit_residuals": {
                "soc_recurrence_mwh_abs": 0.0,
                "terminal_soc_mwh_abs": 0.0,
                "dc_injection_reporting_mw_abs": 0.0,
                "dc_nodal_balance_pu_abs": 0.0,
                "branch_mw_abs": 0.0,
            },
            "exception": None,
        },
    }
    with pytest.raises(ValueError, match="classification contradicts"):
        analysis._retained_worker_outcome(
            worker, horizon_steps=24, point_context=context
        )


def test_analyzer_accepts_exact_early_and_late_provenance_mismatch_shapes() -> None:
    context = {"git_commit": "commit", "source_fingerprint": "source"}
    early = {
        "schema_version": 1,
        "horizon_steps": 24,
        "classification": "provenance_mismatch",
        "start_context": {"source_fingerprint": "changed"},
    }
    late = {
        "schema_version": 1,
        "horizon_steps": 24,
        "classification": "provenance_mismatch",
        "start_context": context,
        "end_context": {"source_fingerprint": "changed"},
        "context_matches": False,
    }
    assert (
        analysis._retained_worker_outcome(
            early, horizon_steps=24, point_context=context
        )
        == "provenance_mismatch"
    )
    assert (
        analysis._retained_worker_outcome(late, horizon_steps=24, point_context=context)
        == "provenance_mismatch"
    )


def test_analyzer_rejects_matching_start_with_missing_provenance_tail() -> None:
    context = {"git_commit": "commit", "source_fingerprint": "source"}
    malformed = {
        "schema_version": 1,
        "horizon_steps": 24,
        "classification": "provenance_mismatch",
        "start_context": context,
    }
    with pytest.raises(ValueError, match="mismatch shape"):
        analysis._retained_worker_outcome(
            malformed, horizon_steps=24, point_context=context
        )


def test_accepted_prefix_is_independently_reconstructed_from_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compact = load_p0_fixture(24)
    annual = SimpleNamespace(
        inputs=compact.inputs,
        policy=compact.policy,
        solve_config=compact.solve_config,
        solve_config_sha256=solve_config_sha256(compact.solve_config),
        temporal_assembly="vectorized",
        canonicalization_backend="SCIPY",
    )
    point = PrefixFixture(
        annual=cast(S4Fixture, annual),
        inputs=compact.inputs,
        horizon_steps=24,
        input_sha256="input",
        scenario_sha256="scenario",
        limits=PREFIX_EXECUTION_LIMITS[24],
    )
    context = {
        "git_commit": "commit",
        "git_clean": True,
        "source_fingerprint": "source",
        "prefix_input_sha256": "input",
    }
    monkeypatch.setattr(runner, "load_prefix_fixture", lambda horizon: point)
    monkeypatch.setattr(
        runner, "_safe_prefix_execution_context", lambda horizon: context
    )
    assert (
        runner._worker(
            tmp_path,
            horizon_steps=24,
            expected_commit="commit",
            expected_source_fingerprint="source",
            expected_prefix_input_sha256="input",
        )
        == 0
    )
    worker = json.loads((tmp_path / "worker-result.json").read_text())
    (tmp_path / "execution-context.json").write_text(json.dumps(context))
    (tmp_path / "worker.log").write_text("ok")
    supervision = {
        "schema_version": 1,
        "horizon_steps": 24,
        "classification": "accepted",
        "returncode": 0,
        "launch_error": None,
        "supervisor_interruption": None,
        "resource_triggers": [],
        "context_matches": True,
        "start_context": context,
        "end_context": context,
        "worker_result": worker,
        "resource_policy": {
            "rss_limit_mib": 16_384.0,
            "worker_wall_seconds": 600.0,
            "supervisor_wall_seconds": 900.0,
            "poll_seconds": 1.0,
        },
        "first_sampled_rss_mib": 10.0,
        "peak_sampled_rss_mib": 20.0,
        "worker_wall_time_seconds": 1.0,
        "wall_time_seconds": 2.0,
        "outer_plan_sha256": sha256_path(tmp_path / "outer-plan.json.gz"),
        "worker_log_sha256": sha256_path(tmp_path / "worker.log"),
    }
    (tmp_path / "supervision.json").write_text(json.dumps(supervision))
    monkeypatch.setattr(analysis, "load_prefix_fixture", lambda horizon: point)
    monkeypatch.setattr(analysis, "_validate_point_context", lambda *args: None)
    summary = analysis._accepted_prefix_summary(
        tmp_path, horizon_steps=24, root_context=context
    )
    assert summary["classification"] == "accepted"
    assert summary["horizon_steps"] == 24
    assert cast(float, summary["terminal_soc_residual_mwh_abs"]) <= 1e-8


@pytest.mark.parametrize(
    (
        "classification",
        "triggers",
        "returncode",
        "worker_classification",
        "retain_outer",
    ),
    [
        ("rss_limit", ["rss_limit", "worker_wall_limit"], -15, None, False),
        ("rss_limit", ["rss_limit"], -16, None, False),
        ("worker_process_failure", [], 9, "accepted", True),
        ("worker_process_failure", [], 9, None, False),
        ("worker_failure", [], 0, None, False),
        ("artifact_failure", [], 0, "accepted", False),
        ("residual_rejection", [], 1, "residual_rejection", False),
        ("worker_launch_failure", [], None, None, False),
        ("supervisor_failure", [], 0, None, False),
        ("supervisor_interrupted", [], -15, None, False),
    ],
)
def test_failed_analysis_reconstructs_trigger_and_worker_classification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    classification: str,
    triggers: list[str],
    returncode: int | None,
    worker_classification: str | None,
    retain_outer: bool,
) -> None:
    context = {"git_commit": "commit", "source_fingerprint": "source"}
    (tmp_path / "execution-context.json").write_text(json.dumps(context))
    (tmp_path / "worker.log").write_text("retained")
    worker = None
    outer_sha = None
    if worker_classification is not None:
        accepted_worker = worker_classification == "accepted"
        worker = {
            "schema_version": 1,
            "horizon_steps": 24,
            "classification": worker_classification,
            "exception": None,
            "start_context": context,
            "end_context": context,
            "context_matches": True,
            "resource_samples": [
                {"phase": phase}
                for phase in (
                    analysis.EXPECTED_PHASES
                    if accepted_worker
                    else analysis.EXPECTED_PHASES[:5]
                )
            ],
            "outer_plan": {
                "accepted_primal": accepted_worker,
                "status": "optimal",
                "missing_or_nonfinite_fields": [],
                "identity_error": None if accepted_worker else "storage identity",
                "audit_residuals": {
                    "soc_recurrence_mwh_abs": 0.0,
                    "terminal_soc_mwh_abs": 0.0,
                    "dc_injection_reporting_mw_abs": 0.0,
                    "dc_nodal_balance_pu_abs": 0.0,
                    "branch_mw_abs": 0.0,
                },
                "exception": None,
            },
        }
        if accepted_worker:
            cast(dict[str, object], worker["outer_plan"])["artifact"] = {
                "sha256": "missing" * 9 + "m",
                "bytes": 5,
            }
        if accepted_worker and retain_outer:
            outer_path = tmp_path / "outer-plan.json.gz"
            outer_path.write_bytes(b"outer")
            outer_sha = sha256_path(outer_path)
            cast(dict[str, object], worker["outer_plan"])["artifact"] = {
                "sha256": outer_sha,
                "bytes": outer_path.stat().st_size,
            }
        (tmp_path / "worker-result.json").write_text(json.dumps(worker))
    limits = PREFIX_EXECUTION_LIMITS[24]
    supervision = {
        "schema_version": 1,
        "horizon_steps": 24,
        "classification": classification,
        "returncode": returncode,
        "launch_error": (
            "OSError: launch refused"
            if classification == "worker_launch_failure"
            else None
        ),
        "supervisor_error": (
            "OSError: process control"
            if classification == "supervisor_failure"
            else None
        ),
        "supervisor_interruption": (
            "KeyboardInterrupt: "
            if classification == "supervisor_interrupted" or returncode == -16
            else None
        ),
        "resource_triggers": triggers,
        "first_sampled_rss_mib": 10.0,
        "peak_sampled_rss_mib": limits.child_rss_mib + 1.0,
        "worker_wall_time_seconds": limits.worker_wall_seconds + 1.0,
        "wall_time_seconds": limits.supervisor_wall_seconds - 1.0,
        "start_context": context,
        "end_context": context,
        "context_matches": True,
        "worker_result": worker,
        "worker_log_sha256": sha256_path(tmp_path / "worker.log"),
        "outer_plan_sha256": outer_sha,
        "resource_policy": {
            "rss_limit_mib": limits.child_rss_mib,
            "worker_wall_seconds": limits.worker_wall_seconds,
            "supervisor_wall_seconds": limits.supervisor_wall_seconds,
            "poll_seconds": limits.poll_seconds,
        },
    }
    (tmp_path / "supervision.json").write_text(json.dumps(supervision))
    monkeypatch.setattr(analysis, "_validate_point_context", lambda *args: None)
    monkeypatch.setattr(
        analysis, "load_verified_outer_plan_archive", lambda *args, **kwargs: None
    )
    summary = analysis._failed_prefix_summary(
        tmp_path,
        horizon_steps=24,
        classification=classification,
        root_context=context,
    )
    assert summary["classification"] == classification
    if outer_sha is not None:
        assert "outer-plan.json.gz" in cast(Mapping[str, object], summary["artifacts"])


def test_failed_analysis_rejects_trigger_priority_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = {"git_commit": "commit", "source_fingerprint": "source"}
    (tmp_path / "execution-context.json").write_text(json.dumps(context))
    (tmp_path / "worker.log").write_text("retained")
    limits = PREFIX_EXECUTION_LIMITS[24]
    supervision = {
        "schema_version": 1,
        "horizon_steps": 24,
        "classification": "worker_wall_limit",
        "returncode": -15,
        "launch_error": None,
        "supervisor_interruption": None,
        "resource_triggers": ["worker_wall_limit", "rss_limit"],
        "first_sampled_rss_mib": 10.0,
        "peak_sampled_rss_mib": limits.child_rss_mib + 1.0,
        "worker_wall_time_seconds": limits.worker_wall_seconds + 1.0,
        "wall_time_seconds": 2.0,
        "start_context": context,
        "end_context": context,
        "context_matches": True,
        "worker_result": None,
        "worker_log_sha256": sha256_path(tmp_path / "worker.log"),
        "outer_plan_sha256": None,
        "resource_policy": {
            "rss_limit_mib": limits.child_rss_mib,
            "worker_wall_seconds": limits.worker_wall_seconds,
            "supervisor_wall_seconds": limits.supervisor_wall_seconds,
            "poll_seconds": limits.poll_seconds,
        },
    }
    (tmp_path / "supervision.json").write_text(json.dumps(supervision))
    monkeypatch.setattr(analysis, "_validate_point_context", lambda *args: None)
    with pytest.raises(ValueError, match="trigger priority"):
        analysis._failed_prefix_summary(
            tmp_path,
            horizon_steps=24,
            classification="worker_wall_limit",
            root_context=context,
        )


def _write_ladder_root(
    directory: Path, classifications: list[str]
) -> tuple[Mapping[str, object], list[int]]:
    directory.mkdir(parents=True)
    context = {
        "schema_version": 1,
        "git_commit": "commit",
        "git_clean": True,
        "source_fingerprint": "source",
        "prefix_ladder_executed": False,
        "annual_execution_authorized": False,
    }
    (directory / "execution-context.json").write_text(json.dumps(context))
    (directory / "outer-equivalence.json").write_text(json.dumps({"equivalent": True}))
    attempted = list(PREFIX_LADDER_HORIZONS[: len(classifications)])
    records = []
    accepted = []
    for horizon, classification in zip(attempted, classifications, strict=True):
        point = directory / f"prefix-{horizon:04d}"
        point.mkdir()
        (point / "execution-context.json").write_text(json.dumps(context))
        (point / "worker.log").write_text("retained")
        limits = PREFIX_EXECUTION_LIMITS[horizon]
        supervision = {
            "schema_version": 1,
            "horizon_steps": horizon,
            "classification": classification,
            "returncode": 0 if classification == "accepted" else 1,
            "resource_triggers": [],
            "first_sampled_rss_mib": 10.0,
            "peak_sampled_rss_mib": 20.0,
            "worker_wall_time_seconds": 1.0,
            "wall_time_seconds": 2.0,
            "start_context": context,
            "end_context": context,
            "context_matches": True,
            "worker_result": None,
            "worker_log_sha256": sha256_path(point / "worker.log"),
            "resource_policy": {
                "rss_limit_mib": limits.child_rss_mib,
                "worker_wall_seconds": limits.worker_wall_seconds,
                "supervisor_wall_seconds": limits.supervisor_wall_seconds,
                "poll_seconds": limits.poll_seconds,
            },
        }
        (point / "supervision.json").write_text(json.dumps(supervision))
        records.append(
            {
                "horizon_steps": horizon,
                "classification": classification,
                "directory": point.name,
                "supervision_sha256": sha256_path(point / "supervision.json"),
            }
        )
        if classification == "accepted":
            accepted.append(horizon)
    complete = classifications == ["accepted", "accepted", "accepted"]
    ladder = {
        "schema_version": 1,
        "classification": "accepted" if complete else "stopped",
        "execution_complete": complete,
        "attempted_horizons": attempted,
        "accepted_horizons": accepted,
        "stopped_horizon": None if complete else attempted[-1],
        "interrupted_horizon": None,
        "records": records,
        "execution_context_sha256": sha256_path(directory / "execution-context.json"),
        "outer_equivalence_sha256": sha256_path(directory / "outer-equivalence.json"),
        "annual_execution_authorized": False,
    }
    progress = {
        key: ladder[key]
        for key in (
            "schema_version",
            "classification",
            "attempted_horizons",
            "accepted_horizons",
            "stopped_horizon",
            "interrupted_horizon",
            "records",
            "execution_context_sha256",
            "outer_equivalence_sha256",
            "annual_execution_authorized",
        )
    }
    (directory / "ladder-progress.json").write_text(json.dumps(progress))
    ladder["ladder_progress_sha256"] = sha256_path(directory / "ladder-progress.json")
    (directory / "ladder-result.json").write_text(json.dumps(ladder))
    return context, attempted


@pytest.mark.parametrize(
    ("classifications", "complete"),
    [
        (["accepted", "accepted", "accepted"], True),
        (["accepted", "solver_failure"], False),
    ],
)
def test_analyzer_retains_complete_or_stopped_ordered_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    classifications: list[str],
    complete: bool,
) -> None:
    directory = tmp_path / "ladder"
    context, attempted = _write_ladder_root(directory, classifications)
    monkeypatch.setattr(analysis, "_validate_execution_context", lambda value: None)
    monkeypatch.setattr(analysis, "_validate_point_context", lambda *args: None)
    monkeypatch.setattr(analysis, "_validate_equivalence", lambda *args: None)
    monkeypatch.setattr(
        analysis,
        "_accepted_prefix_summary",
        lambda directory, *, horizon_steps, root_context: {
            "horizon_steps": horizon_steps,
            "classification": "accepted",
        },
    )
    monkeypatch.setattr(
        analysis,
        "_failed_prefix_summary",
        lambda directory, *, horizon_steps, classification, root_context: {
            "horizon_steps": horizon_steps,
            "classification": classification,
        },
    )
    monkeypatch.setattr(
        analysis,
        "analysis_context",
        lambda: {"git_commit": "commit", "git_clean": True},
    )
    result = analysis.analyze_prefix_ladder(directory)
    assert result["execution_complete"] is complete
    assert result["qualified_for_annual_review"] is complete
    assert result["annual_execution_authorized"] is False
    assert result["attempted_horizons"] == attempted
    if not complete:
        with pytest.raises(ValueError, match="only a complete"):
            analysis.promote_prefix_ladder(
                result, tmp_path / "result.json", source_directory=directory
            )


def test_analyzer_retains_unfinalized_progress_as_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "ladder"
    _context, _attempted = _write_ladder_root(
        directory, ["accepted", "accepted", "accepted"]
    )
    (directory / "ladder-result.json").unlink()
    monkeypatch.setattr(analysis, "_validate_execution_context", lambda value: None)
    monkeypatch.setattr(analysis, "_validate_equivalence", lambda *args: None)
    monkeypatch.setattr(
        analysis,
        "_accepted_prefix_summary",
        lambda directory, *, horizon_steps, root_context: {
            "horizon_steps": horizon_steps,
            "classification": "accepted",
        },
    )
    monkeypatch.setattr(
        analysis,
        "analysis_context",
        lambda: {"git_commit": "analysis", "git_clean": True},
    )
    result = analysis.analyze_prefix_ladder(directory)
    assert result["execution_complete"] is False
    assert result["lifecycle_finalized"] is False
    assert result["qualified_for_annual_review"] is False
    assert result["terminal_classification"] == "accepted"
    assert result["accepted_horizons"] == [24, 168, 720]


def test_complete_promotion_is_immutable_and_does_not_grant_annual_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "promoted.json"
    context = {"git_commit": "commit", "git_clean": True}
    minimal = {
        "execution_complete": True,
        "qualified_for_annual_review": True,
        "annual_execution_authorized": False,
        "analysis_context": context,
    }
    with pytest.raises(ValueError, match="only a complete"):
        analysis.promote_prefix_ladder(minimal, destination)
    result = {
        "schema_version": 1,
        "execution_complete": True,
        "classification": "accepted",
        "lifecycle_finalized": True,
        "terminal_classification": "accepted",
        "stopped_horizon": None,
        "interrupted_horizon": None,
        "qualified_for_annual_review": True,
        "annual_execution_authorized": False,
        "attempted_horizons": [24, 168, 720],
        "accepted_horizons": [24, 168, 720],
        "prefixes": [{}, {}, {}],
        "execution_context": {},
        "analysis_context": context,
        "outer_equivalence": {},
        "artifacts": {},
    }
    monkeypatch.setattr(analysis, "analysis_context", lambda: context)
    monkeypatch.setattr(analysis, "analyze_prefix_ladder", lambda directory: result)
    analysis.promote_prefix_ladder(
        result, destination, source_directory=tmp_path / "ladder"
    )
    assert json.loads(destination.read_text())["annual_execution_authorized"] is False
    with pytest.raises(FileExistsError):
        analysis.promote_prefix_ladder(
            result, destination, source_directory=tmp_path / "ladder"
        )
