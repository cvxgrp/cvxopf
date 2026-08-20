"""Artifact-contract tests for the M17-S3 reproduction path."""

import json

import numpy as np
import pandas as pd
import pytest

from experiments.hierarchical_battery_resilience import analysis
from experiments.hierarchical_battery_resilience import reproduce
from experiments.hierarchical_battery_resilience.scenario import (
    load_frozen_scenario,
)


def test_json_conversion_preserves_shape_and_marks_unavailable_values():
    converted = reproduce._jsonable(
        {"values": np.array([[1.0, np.nan], [np.inf, -np.inf]])}
    )

    assert converted == {"values": [[1.0, None], [None, None]]}
    with pytest.raises(TypeError, match="cannot serialize"):
        reproduce._jsonable(object())


def test_gzip_write_is_readable_and_replaces_incomplete_artifact(tmp_path):
    path = tmp_path / "study.json.gz"
    path.write_bytes(b"incomplete")

    reproduce._write_gzip_json(path, {"completed": True})

    assert reproduce._read_gzip_json(path) == {"completed": True}
    assert not (tmp_path / ".study.json.gz.tmp").exists()

    text_path = tmp_path / "metadata.json"
    reproduce._atomic_write_text(text_path, '{"complete": true}\n')
    assert json.loads(text_path.read_text()) == {"complete": True}
    csv_path = tmp_path / "summary.csv"
    reproduce._atomic_write_csv(csv_path, pd.DataFrame({"value": [1.0]}))
    assert pd.read_csv(csv_path).loc[0, "value"] == 1.0
    assert not list(tmp_path.glob(".*.tmp"))


def test_artifact_validation_detects_drift(tmp_path):
    artifact = tmp_path / "trajectory_summary.csv"
    artifact.write_text("completed\nTrue\n")
    metadata = {
        "scenario_name": "test",
        "artifacts": {
            artifact.name: {
                "sha256": analysis._sha256(artifact),
                "bytes": artifact.stat().st_size,
            }
        }
    }
    (tmp_path / "metadata.json").write_text(json.dumps(metadata))
    (tmp_path / reproduce.CONTEXT_FILE).write_text(
        json.dumps({"scenario_name": "test"})
    )

    assert analysis.validate_artifacts(tmp_path) == metadata
    artifact.write_text("completed\nFalse\n")
    with pytest.raises(ValueError, match="size mismatch|hash mismatch"):
        analysis.validate_artifacts(tmp_path)


def test_resume_rejects_missing_or_mismatched_run_context(tmp_path):
    context = {"scenario_name": "frozen", "python": "3.13"}
    (tmp_path / "partial.json.gz").write_bytes(b"partial")
    with pytest.raises(ValueError, match="without matching run context"):
        reproduce._prepare_run_context(tmp_path, context, resume=True)

    (tmp_path / reproduce.CONTEXT_FILE).write_text(
        json.dumps({"scenario_name": "different", "python": "3.13"})
    )
    with pytest.raises(ValueError, match="context differs"):
        reproduce._prepare_run_context(tmp_path, context, resume=True)


def test_resume_rejects_readable_but_incomplete_or_rehashed_artifact(tmp_path):
    path = tmp_path / "endpoint_realization.json.gz"
    reproduce._write_gzip_json(path, {})
    metadata = {
        "artifacts": {
            path.name: {
                "sha256": analysis._sha256(path),
                "bytes": path.stat().st_size,
            }
        }
    }
    assert reproduce._reusable_payload(
        path,
        prior_metadata=metadata,
        validator=reproduce._valid_endpoint_payload,
    ) is None

    reproduce._write_gzip_json(path, {"altered": True})
    assert reproduce._reusable_payload(
        path,
        prior_metadata=metadata,
        validator=lambda payload: True,
    ) is None


def test_sequential_resume_schema_checks_policy_and_trajectory_counts():
    audit = {
        "status": "optimal",
        "outcome": "accepted",
        "accepted_primal": True,
        "missing_or_nonfinite_fields": [],
        "residuals": {},
        "wall_time_seconds": 1.0,
    }
    outer = {
        "outer_plan_id": "outer-000",
        "created_iteration": 0,
        "global_interval_start": 0,
        "global_interval_stop": 1,
        "local_boundary_indices": [0, 1],
        "global_boundary_indices": [0, 1],
        "storage_device_ids": ["battery"],
        "boundary_soc_mwh": [[1.0], [1.0]],
        "results": {},
        "audit": audit,
    }
    attempt = {
        "attempt_id": "ac-000-hard_equality",
        "attempt_kind": "controlling",
        "iteration": 0,
        "interval_start": 0,
        "interval_stop": 1,
        "outer_plan_id": "outer-000",
        "outer_local_boundary": 1,
        "outer_global_boundary": 1,
        "storage_device_ids": ["battery"],
        "window_diagnosis": "hard_target_met",
        "results": {},
        "audit": audit,
    }
    payload = {
        "artifact_schema_version": reproduce.ARTIFACT_SCHEMA_VERSION,
        "study": "sequential_execution",
        "outer_policy": "frozen",
        "inner_policy": "hard_equality",
        "completed": True,
        "termination_iteration": None,
        "termination_reason": None,
        "completed_intervals": 1,
        "completion_fraction": 1.0,
        "realized_soc_mwh": [[1.0], [1.0]],
        "executed_b_mw": [[0.0]],
        "trajectory_summary": {
            key: 0.0 for key in reproduce.TRAJECTORY_SUMMARY_KEYS
        },
        "outer_plans": {"outer-000": outer},
        "ac_attempts": [attempt],
        "executed_intervals": [
            {key: 0.0 for key in reproduce.EXECUTED_INTERVAL_KEYS}
        ],
    }

    assert reproduce._valid_sequential_payload(
        payload,
        outer_policy="frozen",
        inner_policy="hard_equality",
        horizon_steps=1,
    )
    assert not reproduce._valid_sequential_payload(
        payload,
        outer_policy="replan_every_step",
        inner_policy="hard_equality",
        horizon_steps=1,
    )
    payload["executed_intervals"] = []
    assert not reproduce._valid_sequential_payload(
        payload,
        outer_policy="frozen",
        inner_policy="hard_equality",
        horizon_steps=1,
    )


def test_run_context_identifies_git_state_and_source_tree():
    context = reproduce._run_context(load_frozen_scenario())

    assert len(context["git_commit"]) == 40
    assert isinstance(context["git_dirty"], bool)
    assert isinstance(context["git_status_porcelain"], list)
    fingerprints = context["source_fingerprints"]
    assert len(fingerprints["cvxopf_python_tree_sha256"]) == 64
    assert len(fingerprints["experiment_execution_sha256"]) == 64
    assert len(fingerprints["artifact_code_sha256"]) == 64
    assert set(fingerprints["files"]) == {
        "experiments/hierarchical_battery_resilience/scenario.py",
        "experiments/hierarchical_battery_resilience/manual_runner.py",
        "experiments/hierarchical_battery_resilience/reproduce.py",
        "experiments/hierarchical_battery_resilience/analysis.py",
    }
