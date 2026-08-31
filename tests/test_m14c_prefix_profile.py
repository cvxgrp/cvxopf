from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import numpy as np
import pytest

from experiments.m14_time_vectorization import m14c_prefix_profile_analysis as analysis
from experiments.m14_time_vectorization import run_m14c_prefix_profile as runner


def test_profile_source_registry_binds_protocol_runner_and_recursive_package() -> None:
    relative = {
        path.relative_to(runner.ROOT).as_posix()
        for path in runner.profile_source_paths()
    }
    assert {
        "experiments/m14_time_vectorization/M14C_PROFILING_PROTOCOL.md",
        "experiments/m14_time_vectorization/run_m14c_prefix_profile.py",
        "experiments/case118_annual_hierarchy/pglib_case.py",
        "experiments/case118_annual_hierarchy/scenario.py",
        "src/cvxopf/testcases/__init__.py",
    } <= relative
    assert len(runner.profile_source_fingerprint()) == 64


def test_complete_historical_reference_chain_verifies() -> None:
    assert tuple(runner.validate_reference_ladder()) == (24, 168, 720)
    assert runner.shared_production_fingerprint() == (
        runner.shared_production_fingerprint(runner.REFERENCE_EXECUTION_COMMIT)
    )


def test_sigterm_handler_raises_catchable_supervisor_interruption() -> None:
    with pytest.raises(runner.SupervisorInterrupted, match="SIGTERM"):
        runner._sigterm_handler(15, object())


def test_profile_context_freezes_production_pair_and_reference() -> None:
    context = runner.profile_execution_context(24)
    assert context["temporal_assembly"] == "stepwise"
    assert context["canonicalization_backend"] == "CPP"
    assert context["reference_temporal_assembly"] == "vectorized"
    assert context["reference_canonicalization_backend"] == "SCIPY"
    assert context["prefix_ladder_executed"] is False
    assert context["annual_execution_authorized"] is False
    assert context["shared_production_matches_reference"] is True
    assert len(str(context["m14c_integration_sha256"])) == 64
    assert (
        context["reference_ladder_result_sha256"]
        == runner.REFERENCE_LADDER_RESULT_SHA256
    )


def test_analyzer_validates_complete_stepwise_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = dict(runner.profile_execution_context(24))
    context["git_clean"] = True
    monkeypatch.setattr(
        analysis,
        "profile_source_fingerprint",
        lambda: str(context["source_fingerprint"]),
    )
    analysis._validate_stepwise_context(context, 24)
    context["annual_execution_authorized"] = True
    with pytest.raises(ValueError, match="context"):
        analysis._validate_stepwise_context(context, 24)


def test_profile_refuses_dirty_source_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "profile"
    monkeypatch.setattr(runner, "_git", lambda *args: "dirty")
    with pytest.raises(ValueError, match="clean committed"):
        runner.run_profile(directory)
    assert not directory.exists()


def test_profile_validates_reference_chain_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "profile"
    monkeypatch.setattr(
        runner,
        "_git",
        lambda *args: (
            runner.M14C_INTEGRATION_COMMIT if args and args[0] == "merge-base" else ""
        ),
    )
    monkeypatch.setattr(
        runner,
        "validate_reference_ladder",
        lambda: (_ for _ in ()).throw(ValueError("reference drift")),
    )
    with pytest.raises(ValueError, match="reference drift"):
        runner.run_profile(directory)
    assert not directory.exists()


def test_phase_times_preserve_matching_boundaries() -> None:
    worker = {
        "resource_samples": [
            {"phase": "before_construction", "elapsed_seconds": 1.0},
            {"phase": "after_construction", "elapsed_seconds": 3.0},
            {"phase": "before_solve", "elapsed_seconds": 4.0},
            {"phase": "after_solve", "elapsed_seconds": 9.0},
            {"phase": "after_archive", "elapsed_seconds": 12.0},
            {"phase": "after_release", "elapsed_seconds": 13.0},
        ]
    }
    assert analysis._phase_times(worker) == {
        "construction_seconds": 2.0,
        "canonicalization_solve_seconds": 5.0,
        "postsolve_to_archive_seconds": 3.0,
        "release_seconds": 1.0,
    }


def test_result_comparison_detects_mathematical_drift() -> None:
    left: Mapping[str, object] = {
        "objective": 1.0,
        "soc": np.array([[1.0, 2.0]]),
        "status": "optimal",
    }
    right = dict(left)
    right["soc"] = np.array([[1.0, 3.0]])
    assert analysis._result_mismatches(left, right) == ["soc"]


def test_branch_flows_are_residual_gated_but_schema_bound() -> None:
    left = {"p_flows": np.zeros((2, 3)), "objective": 1.0}
    right = {"p_flows": np.ones((2, 3)) * 99.0, "objective": 1.0}
    assert analysis._result_mismatches(left, right) == []
    right["p_flows"] = np.ones((3, 3))
    assert analysis._result_mismatches(left, right) == ["p_flows.schema"]


def test_declared_component_costs_are_named_and_finite() -> None:
    reference = runner.validate_reference_ladder()[24]
    outer, _, _, _ = analysis._load_point(reference["directory"], 24, reference=True)
    costs = analysis._declared_component_costs(outer, 24)
    assert {"generation_cost", "storage_cost"} <= costs.keys()
    assert all(np.isfinite(value) for value in costs.values())


def test_context_comparability_retains_source_and_environment_mismatches() -> None:
    vector = {
        "git_commit": "vector",
        "source_fingerprint": "old",
        "shared_production_fingerprint": "production",
        "platform": "same",
        "architecture": "arm64",
        "software_versions": {"cvxpy": "1"},
    }
    stepwise = {**vector, "git_commit": "stepwise", "source_fingerprint": "new"}
    result = analysis._context_comparability(vector, stepwise)
    assert result["classification"] == "matched_execution_context"
    assert result["mismatched_fields"] == []
    assert result["git_commit"]["match"] is False
    assert result["source_fingerprint"]["match"] is False
    stepwise["platform"] = "different"
    result = analysis._context_comparability(vector, stepwise)
    assert result["classification"] == "descriptive_mismatched_execution_context"
    assert result["mismatched_fields"] == ["platform"]


def test_accepted_worker_requires_complete_exception_free_archive(
    tmp_path: Path,
) -> None:
    outer_path = tmp_path / "outer-plan.json.gz"
    outer_path.write_bytes(b"outer")
    worker = {
        "classification": "accepted",
        "exception": None,
        "context_matches": True,
        "dimensions": {"scalar_variables": 1},
        "phase_timings": {
            "extraction_seconds": 0.1,
            "audit_seconds": 0.1,
            "archive_seconds": 0.1,
        },
        "resource_samples": [
            {"phase": phase, "elapsed_seconds": index, "rss_bytes": 1}
            for index, phase in enumerate(runner.EXPECTED_WORKER_PHASES)
        ],
        "outer_plan": {
            "accepted_primal": True,
            "status": "optimal",
            "temporal_assembly": "stepwise",
            "canonicalization_backend": "CPP",
            "artifact": {
                "sha256": runner.sha256_path(outer_path),
                "bytes": outer_path.stat().st_size,
            },
        },
    }
    assert runner._accepted_worker_evidence(tmp_path, worker)
    worker["exception"] = "late failure"
    assert not runner._accepted_worker_evidence(tmp_path, worker)


def test_supervision_clock_includes_setup_but_worker_clock_starts_at_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class CompletedProcess:
        pid = 123

        def __init__(self) -> None:
            self.poll_count = 0

        def poll(self) -> int | None:
            self.poll_count += 1
            return None if self.poll_count == 1 else 0

        def wait(self) -> int:
            return 0

    clock = iter((100.0, 110.0, 111.0, 112.0, 120.0, 125.0))
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(runner.time, "sleep", lambda _: None)
    monkeypatch.setattr(runner, "_child_rss_mib", lambda _: None)
    monkeypatch.setattr(
        runner.subprocess, "Popen", lambda *args, **kwargs: CompletedProcess()
    )

    supervision = runner._supervise(
        tmp_path / "point",
        {"horizon_steps": 24},
        runner.PrefixExecutionLimits(1_000.0, 1_000.0, 1_000.0),
    )

    assert supervision["worker_wall_time_seconds"] == 10.0
    assert supervision["wall_time_seconds"] == 25.0


def test_incomplete_profile_is_not_analyzable(tmp_path: Path) -> None:
    directory = tmp_path / "profile"
    directory.mkdir()
    (directory / "profile-result.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "execution_complete": False,
                "records": [],
                "annual_execution_authorized": False,
                "reference_ladder_result_sha256": (
                    runner.REFERENCE_LADDER_RESULT_SHA256
                ),
            }
        )
    )
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(analysis, "_git", lambda *args: "")
        with pytest.raises(ValueError, match="incomplete"):
            analysis.analyze_profile(directory, tmp_path / "reference")


def test_analysis_refuses_dirty_checkout_before_reading_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(analysis, "_git", lambda *args: "dirty")
    with pytest.raises(ValueError, match="clean worktree"):
        analysis.analyze_profile(tmp_path / "missing", tmp_path / "reference")


def test_profile_result_never_authorizes_annual_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "profile"
    monkeypatch.setattr(
        runner,
        "_git",
        lambda *args: (
            runner.M14C_INTEGRATION_COMMIT if args and args[0] == "merge-base" else ""
        ),
    )
    monkeypatch.setattr(runner, "validate_reference_ladder", lambda: {})
    monkeypatch.setattr(
        runner,
        "profile_execution_context",
        lambda horizon: {"horizon_steps": horizon, "git_clean": True},
    )

    def supervise(
        point: Path, context: Mapping[str, object], limits: object
    ) -> Mapping[str, object]:
        del limits
        point.mkdir()
        supervision = {
            "classification": "accepted",
            "horizon_steps": context["horizon_steps"],
        }
        (point / "supervision.json").write_text(json.dumps(supervision))
        return supervision

    monkeypatch.setattr(runner, "_supervise", supervise)
    result = runner.run_profile(directory)
    assert result["execution_complete"] is True
    assert result["annual_execution_authorized"] is False


def test_profile_retains_root_record_after_supervisor_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "profile"
    monkeypatch.setattr(
        runner,
        "_git",
        lambda *args: (
            runner.M14C_INTEGRATION_COMMIT if args and args[0] == "merge-base" else ""
        ),
    )
    monkeypatch.setattr(runner, "validate_reference_ladder", lambda: {})
    monkeypatch.setattr(
        runner,
        "profile_execution_context",
        lambda horizon: {"horizon_steps": horizon, "git_clean": True},
    )

    def interrupt(
        point: Path, context: Mapping[str, object], limits: object
    ) -> Mapping[str, object]:
        del limits
        point.mkdir()
        supervision = {
            "classification": "supervisor_interrupted",
            "horizon_steps": context["horizon_steps"],
        }
        (point / "supervision.json").write_text(json.dumps(supervision))
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "_supervise", interrupt)
    with pytest.raises(KeyboardInterrupt):
        runner.run_profile(directory)
    result = json.loads((directory / "profile-result.json").read_text())
    assert result["classification"] == "supervisor_interrupted"
    assert result["execution_complete"] is False
    assert result["records"][0]["classification"] == "supervisor_interrupted"
    assert result["annual_execution_authorized"] is False
