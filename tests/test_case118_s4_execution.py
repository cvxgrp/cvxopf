from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping, cast

import pytest

from experiments.case118_annual_hierarchy import run_s4
from experiments.case118_annual_hierarchy import s4_analysis
from experiments.case118_annual_hierarchy.p0_fixture import (
    load_p0_fixture,
    solve_config_sha256,
)
from experiments.case118_annual_hierarchy.run_s4 import (
    S4_SOURCE_FILES,
    s4_source_fingerprint,
    s4_source_paths,
)
from experiments.case118_annual_hierarchy.s4_analysis import (
    _archived_dimensions,
    analysis_source_fingerprint,
    analysis_source_paths,
    promote_s4,
)
from experiments.case118_annual_hierarchy.s4_equivalence import (
    EQUIVALENCE_HORIZON_STEPS,
    run_s4_outer_equivalence,
)
from experiments.case118_annual_hierarchy.s4_fixture import (
    S4_CANONICALIZATION_BACKEND,
    S4_TEMPORAL_ASSEMBLY,
    S4ExecutionLimits,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    write_verified_outer_plan_archive,
)
from experiments.case118_annual_hierarchy.streaming_runner import solve_frozen_outer


def test_s4_source_registry_binds_direct_and_recursive_runtime_sources() -> None:
    relative = {path.as_posix() for path in s4_source_paths()}
    assert all(
        any(value.endswith(name) for value in relative) for name in S4_SOURCE_FILES
    )
    assert any(value.endswith("src/cvxopf/testcases/__init__.py") for value in relative)
    assert len(s4_source_fingerprint()) == 64
    assert any(path.name == "s4_analysis.py" for path in analysis_source_paths())
    analysis_names = {path.name for path in analysis_source_paths()}
    assert {"p0_fixture.py", "pglib_case.py", "scenario.py", "run_s0.py"} <= (
        analysis_names
    )
    assert len(analysis_source_fingerprint()) == 64


def test_s4_outer_seam_matches_public_controller_before_ac_construction() -> None:
    report = run_s4_outer_equivalence()

    assert report["horizon_steps"] == EQUIVALENCE_HORIZON_STEPS == 24
    assert report["equivalent"] is True
    assert report["mismatches"] == []
    assert report["formulation"] == "lossy_dc"
    assert report["temporal_assembly"] == S4_TEMPORAL_ASSEMBLY == "vectorized"
    assert report["canonicalization_backend"] == S4_CANONICALIZATION_BACKEND == "SCIPY"
    assert (
        report["public_dimensions"]
        == report["streaming_dimensions"]
        == {
            "scalar_variables": 6004,
            "scalar_equalities": 2936,
            "explicit_scalar_inequalities": 0,
            "other_scalar_constraints": 0,
            "constraint_objects": 7,
        }
    )
    assert report["public_status"] == report["streaming_status"] == "optimal"
    assert report["fingerprints_match"] is True
    assert report["public_fingerprints"] == report["streaming_fingerprints"]
    assert report["public_boundary_sha256"] == report["streaming_boundary_sha256"]
    assert report["public_summary"] == report["streaming_summary"]
    assert report["storage_device_ids"] == [
        "storage_bus_41",
        "storage_bus_65",
        "storage_bus_89",
        "storage_bus_105",
    ]
    assert report["audit_schema_projection"] == {
        "public_branch_mw_abs_present": False,
        "streaming_branch_mw_abs": 0.0,
        "projected_public_branch_mw_abs": 0.0,
    }


def test_s4_analysis_reconstructs_scalar_counts_from_outer_archive(
    tmp_path: Path,
) -> None:
    fixture = load_p0_fixture(6)
    outer = solve_frozen_outer(fixture.inputs, fixture.policy, fixture.solve_config)
    path = tmp_path / "outer-plan.json.gz"
    write_verified_outer_plan_archive(
        path,
        outer,
        inputs=fixture.inputs,
        source_fingerprint="test-source",
        scenario_hash="test-scenario",
    )

    dimensions = _archived_dimensions(path)

    assert dimensions["scalar_variables"] > 0
    assert dimensions["scalar_equalities"] > 0
    assert dimensions["explicit_scalar_inequalities"] > 0
    assert dimensions["other_scalar_constraints"] == 0
    assert dimensions["constraint_objects"] > 0


def test_s4_promotion_requires_complete_acceptance_and_is_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "S4_RESULTS.json"
    context = {
        "git_commit": "analysis-commit",
        "git_clean": True,
        "analysis_source_fingerprint": "analysis-source",
    }
    monkeypatch.setattr(s4_analysis, "analysis_context", lambda: context)
    with pytest.raises(ValueError, match="complete accepted"):
        promote_s4({"execution_complete": False}, destination)

    result = {
        "execution_complete": True,
        "accepted_for_s4b": True,
        "analysis_context": context,
    }
    with pytest.raises(ValueError, match="analyzer context"):
        promote_s4({**result, "analysis_context": {}}, destination)
    promote_s4(result, destination)
    with pytest.raises(FileExistsError):
        promote_s4({**result, "changed": True}, destination)


def test_s4_worker_executes_only_outer_lifecycle_and_archives_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compact = load_p0_fixture(6)
    fixture = SimpleNamespace(
        inputs=compact.inputs,
        policy=compact.policy,
        solve_config=compact.solve_config,
        scenario_hash="test-scenario",
        temporal_assembly=S4_TEMPORAL_ASSEMBLY,
        canonicalization_backend=S4_CANONICALIZATION_BACKEND,
        generator_quadratic_cost=1e-4,
        generator_conditioning_evidence_sha256="conditioning-evidence",
    )
    context = {
        "git_commit": "test-commit",
        "git_clean": True,
        "source_fingerprint": "test-source",
    }
    monkeypatch.setattr(run_s4, "load_s4_fixture", lambda: fixture)
    monkeypatch.setattr(run_s4, "_safe_execution_context", lambda: context)

    returncode = run_s4._worker(
        tmp_path,
        expected_commit="test-commit",
        expected_source_fingerprint="test-source",
    )

    assert returncode == 0
    worker = run_s4._mapping(
        json.loads((tmp_path / "worker-result.json").read_text()),
        "worker",
    )
    assert worker["classification"] == "accepted"
    assert worker["outer_plan"]["temporal_assembly"] == "vectorized"
    assert worker["outer_plan"]["canonicalization_backend"] == "SCIPY"
    assert (tmp_path / "outer-plan.json.gz").is_file()
    samples = cast(list[Mapping[str, object]], worker["resource_samples"])
    assert [sample["phase"] for sample in samples] == [
        "worker_start",
        "before_construction",
        "after_construction",
        "before_solve",
        "after_solve",
        "after_archive",
        "after_release",
    ]


def test_s4_analysis_reconstructs_worker_archive_without_trusting_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    compact = load_p0_fixture(6)
    fixture = SimpleNamespace(
        inputs=compact.inputs,
        policy=compact.policy,
        solve_config=compact.solve_config,
        policy_sha256=compact.policy_sha256,
        solve_config_sha256=solve_config_sha256(compact.solve_config),
        scenario_hash="test-scenario",
        storage_device_ids=compact.storage_device_ids,
        temporal_assembly=S4_TEMPORAL_ASSEMBLY,
        canonicalization_backend=S4_CANONICALIZATION_BACKEND,
        generator_quadratic_cost=1e-4,
        generator_conditioning_evidence_sha256="conditioning-evidence",
        m14c_integration_checkpoint="integration-checkpoint",
        m14c_source_commit="m14c-source",
        big_experiment_parent_commit="big-parent",
        m14c_merge_base_commit="merge-base",
        prefix_ladder_executed=True,
        annual_execution_authorized=True,
        m14c_integration_sha256="integration-hash",
    )
    context = {
        "git_commit": "test-commit",
        "git_clean": True,
        "source_fingerprint": "test-source",
        "temporal_assembly": S4_TEMPORAL_ASSEMBLY,
        "canonicalization_backend": S4_CANONICALIZATION_BACKEND,
        "generator_quadratic_cost": 1e-4,
        "generator_conditioning_evidence_sha256": "conditioning-evidence",
        "m14c_integration_checkpoint": "integration-checkpoint",
        "m14c_source_commit": "m14c-source",
        "big_experiment_parent_commit": "big-parent",
        "m14c_merge_base_commit": "merge-base",
        "prefix_ladder_executed": True,
        "annual_execution_authorized": True,
        "m14c_integration_sha256": "integration-hash",
    }
    monkeypatch.setattr(run_s4, "load_s4_fixture", lambda: fixture)
    monkeypatch.setattr(run_s4, "_safe_execution_context", lambda: context)
    assert (
        run_s4._worker(
            tmp_path,
            expected_commit="test-commit",
            expected_source_fingerprint="test-source",
        )
        == 0
    )
    worker = json.loads((tmp_path / "worker-result.json").read_text())
    outer_path = tmp_path / "outer-plan.json.gz"
    supervision = {
        "classification": "accepted",
        "returncode": 0,
        "context_matches": True,
        "start_context": context,
        "end_context": context,
        "worker_result": worker,
        "outer_plan_sha256": run_s4.sha256_path(outer_path),
        "first_sampled_rss_mib": 50.0,
        "peak_sampled_rss_mib": 100.0,
        "worker_wall_time_seconds": 0.9,
        "wall_time_seconds": 1.0,
        "resource_policy": {
            "rss_limit_mib": run_s4.S4_EXECUTION_LIMITS.child_rss_mib,
            "worker_wall_seconds": run_s4.S4_EXECUTION_LIMITS.worker_wall_seconds,
            "supervisor_wall_seconds": (
                run_s4.S4_EXECUTION_LIMITS.supervisor_wall_seconds
            ),
            "poll_seconds": run_s4.S4_EXECUTION_LIMITS.poll_seconds,
        },
    }
    equivalence_fingerprints = {
        "input_sha256": s4_analysis.EXPECTED_EQUIVALENCE_INPUT_SHA256,
        "policy_sha256": compact.policy_sha256,
        "solve_config_sha256": fixture.solve_config_sha256,
        "result_sha256": "1" * 64,
        "boundary_sha256": "2" * 64,
        "structure_sha256": "3" * 64,
    }
    equivalence_summary = {
        "objective": 1.0,
        "result_sha256": "1" * 64,
        "result_schema": {"objective": []},
    }
    (tmp_path / "execution-context.json").write_text(json.dumps(context))
    (tmp_path / "outer-equivalence.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "equivalent": True,
                "horizon_steps": 24,
                "mismatches": [],
                "formulation": "lossy_dc",
                "temporal_assembly": S4_TEMPORAL_ASSEMBLY,
                "canonicalization_backend": S4_CANONICALIZATION_BACKEND,
                "public_dimensions": s4_analysis.EXPECTED_EQUIVALENCE_DIMENSIONS,
                "streaming_dimensions": (s4_analysis.EXPECTED_EQUIVALENCE_DIMENSIONS),
                "public_status": "optimal",
                "streaming_status": "optimal",
                "storage_device_ids": list(compact.storage_device_ids),
                "global_boundary_indices_sha256": "4" * 64,
                "public_summary": equivalence_summary,
                "streaming_summary": equivalence_summary,
                "public_boundary_sha256": "2" * 64,
                "streaming_boundary_sha256": "2" * 64,
                "public_residuals": {},
                "streaming_residuals": {"branch_mw_abs": 0.0},
                "audit_schema_projection": {
                    "public_branch_mw_abs_present": False,
                    "streaming_branch_mw_abs": 0.0,
                    "projected_public_branch_mw_abs": 0.0,
                },
                "public_fingerprints": equivalence_fingerprints,
                "streaming_fingerprints": equivalence_fingerprints,
                "fingerprints_match": True,
            }
        )
    )
    (tmp_path / "supervision.json").write_text(json.dumps(supervision))
    monkeypatch.setattr(s4_analysis, "load_s4_fixture", lambda: fixture)
    monkeypatch.setattr(s4_analysis, "s4_source_fingerprint", lambda: "test-source")

    result = s4_analysis.analyze_s4(tmp_path)

    assert result["execution_complete"] is True
    assert result["accepted_for_s4b"] is True

    equivalence_payload = json.loads((tmp_path / "outer-equivalence.json").read_text())
    equivalence_payload["public_summary"]["result_sha256"] = "5" * 64
    equivalence_payload["streaming_summary"]["result_sha256"] = "5" * 64
    (tmp_path / "outer-equivalence.json").write_text(json.dumps(equivalence_payload))
    with pytest.raises(ValueError, match="equivalence evidence"):
        s4_analysis.analyze_s4(tmp_path)
    equivalence_payload["public_summary"]["result_sha256"] = "1" * 64
    equivalence_payload["streaming_summary"]["result_sha256"] = "1" * 64
    (tmp_path / "outer-equivalence.json").write_text(json.dumps(equivalence_payload))

    supervision["first_sampled_rss_mib"] = None
    supervision["peak_sampled_rss_mib"] = 0.0
    (tmp_path / "supervision.json").write_text(json.dumps(supervision))
    with pytest.raises(ValueError, match="first RSS"):
        s4_analysis.analyze_s4(tmp_path)


def test_s4_supervisor_retains_worker_launch_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    directory = Path("relative-s4")
    resolved = (tmp_path / directory).resolve()
    context = {
        "git_commit": "test-commit",
        "git_clean": True,
        "source_fingerprint": "test-source",
    }
    monkeypatch.setattr(run_s4, "execution_context", lambda: context)
    monkeypatch.setattr(run_s4, "_safe_execution_context", lambda: context)
    monkeypatch.setattr(
        run_s4,
        "load_s4_fixture",
        lambda: SimpleNamespace(annual_execution_authorized=True),
    )
    monkeypatch.setattr(run_s4, "outer_equivalence_gate", lambda: {"equivalent": True})

    captured_command: list[str] = []

    def fail_launch(*args: object, **kwargs: object) -> object:
        del kwargs
        captured_command.extend(cast(list[str], args[0]))
        raise OSError("synthetic launch failure")

    monkeypatch.setattr(run_s4.subprocess, "Popen", fail_launch)

    result = run_s4.run_s4(directory)

    assert result["classification"] == "worker_launch_failure"
    assert result["returncode"] is None
    assert "synthetic launch failure" in str(result["launch_error"])
    output_index = captured_command.index("--output-directory") + 1
    assert Path(captured_command[output_index]) == resolved
    assert (resolved / "supervision.json").is_file()
    assert not (resolved / "active-worker.json").exists()


def test_s4_supervisor_retains_rss_limit_without_promoting_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "s4"
    context = {
        "git_commit": "test-commit",
        "git_clean": True,
        "source_fingerprint": "test-source",
    }

    class FakeProcess:
        pid = 12345
        stopped = False

        def poll(self) -> int | None:
            return -15 if self.stopped else None

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return -15

    process = FakeProcess()
    limits = S4ExecutionLimits(
        child_rss_mib=1.0,
        worker_wall_seconds=1e-12,
        supervisor_wall_seconds=1e-12,
        poll_seconds=1e-12,
    )
    monkeypatch.setattr(run_s4, "S4_EXECUTION_LIMITS", limits)
    monkeypatch.setattr(run_s4, "execution_context", lambda: context)
    monkeypatch.setattr(run_s4, "_safe_execution_context", lambda: context)
    monkeypatch.setattr(
        run_s4,
        "load_s4_fixture",
        lambda: SimpleNamespace(annual_execution_authorized=True),
    )
    monkeypatch.setattr(run_s4, "outer_equivalence_gate", lambda: {"equivalent": True})
    monkeypatch.setattr(run_s4.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        run_s4,
        "_child_rss_mib",
        lambda pid: limits.child_rss_mib + 1.0,
    )
    monkeypatch.setattr(
        run_s4, "_terminate", lambda value: setattr(value, "stopped", True)
    )

    result = run_s4.run_s4(directory)

    assert result["classification"] == "rss_limit"
    assert result["resource_triggers"] == [
        "rss_limit",
        "worker_wall_limit",
        "total_wall_limit",
    ]
    assert result["returncode"] == -15
    assert result["worker_result"] is None
    assert result["outer_plan_sha256"] is None
    assert not (directory / "active-worker.json").exists()


def test_s4_supervisor_rejects_unauthorized_annual_run_before_output_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    directory = tmp_path / "s4"
    fixture = SimpleNamespace(annual_execution_authorized=False)
    monkeypatch.setattr(run_s4, "load_s4_fixture", lambda: fixture)
    monkeypatch.setattr(
        run_s4,
        "execution_context",
        lambda: pytest.fail("execution context must not be captured"),
    )
    monkeypatch.setattr(
        run_s4,
        "outer_equivalence_gate",
        lambda: pytest.fail("equivalence must not run before authorization"),
    )

    with pytest.raises(ValueError, match="annual execution is not authorized"):
        run_s4.run_s4(directory)

    assert not directory.exists()
