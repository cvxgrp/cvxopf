from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from experiments.case118_annual_hierarchy.p0_fixture import (
    frozen_p0_policy,
    policy_sha256,
)
from experiments.case118_annual_hierarchy import recycle_analysis
from experiments.case118_annual_hierarchy.recycle_analysis import (
    _canonical_layout,
    _canonical_solver_evidence,
    _compact_arm,
    _restart_timing_comparison,
    _s2_projection,
    _supervision_projection,
    _warm_start_projection,
    actual_start_residuals,
    causal_source_residuals,
    load_resource_samples,
    matched_rss_residuals,
    trajectory_residuals,
)
from experiments.case118_annual_hierarchy.reference.extract_s2_reference import (
    HISTORICAL_SOURCE_FINGERPRINT,
)
from experiments.case118_annual_hierarchy.s2_fixture import load_s2_fixture


def _trajectory() -> dict[str, object]:
    return {
        "storage_device_ids": ["storage_a", "storage_b"],
        "executed_b_mw": [[1.0, -2.0], [0.5, 0.25]],
        "realized_soc_mwh": [[5.0, 6.0], [4.0, 8.0], [3.5, 7.75]],
        "attempts": [
            {
                "iteration": 0,
                "attempt_id": "ac-000-00-primary_controlling",
                "ordinal": 0,
                "transformation": "flat",
                "source_kind": "generated_flat",
                "source_attempt_id": None,
            },
            {
                "iteration": 1,
                "attempt_id": "ac-001-00-primary_controlling",
                "ordinal": 0,
                "transformation": "shifted_preceding",
                "source_kind": "attempt",
                "source_attempt_id": "ac-000-00-primary_controlling",
            },
        ],
    }


def _causal_source() -> dict[str, object]:
    return {
        "attempt_id": "ac-015-00-primary_controlling",
        "iteration": 15,
        "ordinal": 0,
        "role": "primary_controlling",
        "outer_plan_id": "outer-000",
        "global_interval_start": 15,
        "global_interval_stop": 18,
        "storage_device_ids": ["storage_a", "storage_b"],
        "initial_soc_mwh": {"storage_a": 5.0, "storage_b": 6.0},
        "first_soc_mwh": [4.0, 8.0],
        "first_b_mw": [1.0, -2.0],
        "solution_values": {"Pg_0": [1.0, 2.0], "soc_0": [5.0, 6.0]},
    }


def test_trajectory_residuals_report_exact_agreement_without_a_gate():
    trajectory = _trajectory()

    result = trajectory_residuals(trajectory, deepcopy(trajectory))

    assert result == {
        "executed_b_mw_max_abs": 0.0,
        "realized_soc_mwh_max_abs": 0.0,
        "attempt_labels_exact": True,
    }
    assert "accepted" not in result
    assert "pass" not in result


def test_trajectory_residuals_detect_action_soc_and_source_changes():
    reference = _trajectory()
    changed = deepcopy(reference)
    changed["executed_b_mw"][1][0] += 0.25
    changed["realized_soc_mwh"][2][1] -= 0.5
    changed["attempts"][1]["source_attempt_id"] = "wrong-attempt"

    result = trajectory_residuals(changed, reference)

    assert result["executed_b_mw_max_abs"] == pytest.approx(0.25)
    assert result["realized_soc_mwh_max_abs"] == pytest.approx(0.5)
    assert result["attempt_labels_exact"] is False


def test_trajectory_residuals_reject_storage_identity_mismatch():
    left = _trajectory()
    right = deepcopy(left)
    right["storage_device_ids"] = ["storage_b", "storage_a"]

    with pytest.raises(ValueError, match="storage identities differ"):
        trajectory_residuals(left, right)


def test_trajectory_residuals_reject_shape_mismatch():
    left = _trajectory()
    right = deepcopy(left)
    right["executed_b_mw"] = [[1.0, -2.0]]

    with pytest.raises(ValueError, match="shape mismatch"):
        trajectory_residuals(left, right)


def test_trajectory_residuals_reject_nonfinite_values():
    left = _trajectory()
    right = deepcopy(left)
    right["realized_soc_mwh"][1][0] = np.nan

    with pytest.raises(ValueError, match="nonfinite"):
        trajectory_residuals(left, right)


def test_warm_start_projection_uses_the_preceding_archived_causal_source():
    preceding_id = "ac-015-00-primary_controlling"
    current_id = "ac-016-00-primary_controlling"
    windows = [
        {
            "executed_interval": {"controlling_attempt_id": preceding_id},
            "attempts": [
                {
                    "attempt_id": preceding_id,
                    "causal_source": {"attempt_id": preceding_id},
                }
            ],
        },
        {
            "executed_interval": {"controlling_attempt_id": current_id},
            "attempts": [
                {
                    "attempt_id": current_id,
                    "transformation": "shifted_preceding",
                    "source_kind": "attempt",
                    "source_attempt_id": preceding_id,
                    "assigned_start": {"soc_0": [1.0]},
                    "solver_x0": [1.0],
                    "solver_x0_layout": [{"name": "soc_0"}],
                    "solver_evidence": {"layout_signature": "layout"},
                    "structural_signature": {"variables": []},
                    "causal_source": {"attempt_id": current_id},
                }
            ],
        },
    ]

    result = _warm_start_projection(windows, [1])["1"]

    assert result["source_attempt_id"] == preceding_id
    assert result["preceding_attempt_id"] == preceding_id
    assert result["preceding_causal_attempt_id"] == preceding_id
    assert result["causal_source_matches_preceding"] is True
    assert result["causal_source"]["attempt_id"] == current_id


def test_s2_projection_supports_an_exact_partial_prefix():
    projection = _s2_projection(3)

    assert len(projection["executed_b_mw"]) == 3
    assert len(projection["realized_soc_mwh"]) == 4
    assert [attempt["iteration"] for attempt in projection["attempts"]] == [0, 1, 2]


def test_actual_start_residuals_normalize_process_local_structure():
    left_layout = [
        {
            "is_original_variable": True,
            "name": "Pg_0",
            "shape": [2],
            "start": 0,
            "stop": 2,
        },
        {
            "is_original_variable": False,
            "name": "var100",
            "shape": [1],
            "start": 2,
            "stop": 3,
        },
    ]
    right_layout = deepcopy(left_layout)
    right_layout[1]["name"] = "var999"
    left_evidence = {
        "layout_signature": "raw-left",
        "model_coordinate_count": 2,
        "auxiliary_coordinate_count": 1,
        "object_ids_before": {"variables": [10, 11]},
        "object_ids_after": {"variables": [10, 11]},
    }
    right_evidence = {
        "layout_signature": "raw-right",
        "model_coordinate_count": 2,
        "auxiliary_coordinate_count": 1,
        "object_ids_before": {"variables": [90, 91]},
        "object_ids_after": {"variables": [90, 91]},
    }
    left = {
        "assigned_start": {"Pg_0": [1.0, 2.0], "soc_0": [3.0]},
        "solver_x0": [1.0, 2.0, 3.0],
        "solver_x0_layout": _canonical_layout(left_layout),
        "solver_evidence": _canonical_solver_evidence(left_evidence),
        "structural_signature": {"variables": ["Pg_0", "soc_0"]},
        "causal_source": _causal_source(),
    }
    right = deepcopy(left)
    right["assigned_start"]["Pg_0"][1] += 0.25
    right["solver_x0"][2] -= 0.5
    right["solver_x0_layout"] = _canonical_layout(right_layout)
    right["solver_evidence"] = _canonical_solver_evidence(right_evidence)

    residuals = actual_start_residuals(left, right)

    assert residuals["assigned_start_by_group"]["Pg_0"]["max_abs"] == pytest.approx(0.25)
    assert residuals["assigned_start_by_group"]["Pg_0"]["normalized"] == pytest.approx(0.25 / 2.25)
    assert residuals["assigned_start_by_group"]["soc_0"]["max_abs"] == 0.0
    assert residuals["assigned_start_max_abs"] == pytest.approx(0.25)
    assert residuals["solver_x0"]["all"]["max_abs"] == pytest.approx(0.5)
    assert residuals["solver_x0"]["original"]["max_abs"] == 0.0
    assert residuals["solver_x0"]["auxiliary"]["max_abs"] == pytest.approx(0.5)
    assert residuals["solver_x0"]["by_layout_entry"]["Pg_0"] == {
        "kind": "original",
        "shape": [2],
        "start": 0,
        "stop": 2,
        "max_abs": 0.0,
        "reference_scale": 2.0,
        "normalized": 0.0,
    }
    auxiliary = residuals["solver_x0"]["by_layout_entry"]["auxiliary_0"]
    assert auxiliary["kind"] == "auxiliary"
    assert auxiliary["max_abs"] == pytest.approx(0.5)
    assert auxiliary["normalized"] == pytest.approx(0.5 / 2.5)
    assert residuals["solver_x0_layout_exact"] is True
    assert residuals["solver_evidence_exact"] is True
    assert residuals["structural_signature_exact"] is True
    assert "accepted" not in residuals


def test_actual_start_residuals_reject_nonfinite_values():
    start = {
        "assigned_start": {"soc_0": [1.0]},
        "solver_x0": [1.0],
        "solver_x0_layout": [],
        "solver_evidence": {},
        "structural_signature": {},
        "causal_source": _causal_source(),
    }
    changed = deepcopy(start)
    changed["solver_x0"][0] = np.nan

    with pytest.raises(ValueError, match="nonfinite"):
        actual_start_residuals(start, changed)


def test_causal_source_residuals_detect_each_numerical_channel():
    reference = _causal_source()
    changed = deepcopy(reference)
    changed["first_soc_mwh"][0] += 0.25
    changed["first_b_mw"][1] -= 0.5
    changed["solution_values"]["Pg_0"][1] += 1.0

    result = causal_source_residuals(changed, reference)

    assert result["identity_exact"] is True
    assert result["first_soc_mwh"]["max_abs"] == pytest.approx(0.25)
    assert result["first_b_mw"]["max_abs"] == pytest.approx(0.5)
    assert result["solution_values"]["Pg_0"]["max_abs"] == pytest.approx(1.0)

    changed["solution_values"]["Pg_0"][0] = np.nan
    with pytest.raises(ValueError, match="nonfinite"):
        causal_source_residuals(changed, reference)

    changed = deepcopy(reference)
    changed["first_b_mw"] = [1.0]
    with pytest.raises(ValueError, match="shape mismatch"):
        causal_source_residuals(changed, reference)


def test_matched_rss_residuals_compare_identical_global_intervals():
    never = [
        {"iteration": 0, "rss_mib": 100.0},
        {"iteration": 1, "rss_mib": 110.0},
    ]
    recycled = [
        {"iteration": 0, "rss_mib": 100.0},
        {"iteration": 1, "rss_mib": 90.0},
    ]

    result = matched_rss_residuals(recycled, never)

    assert result["matched_interval_count"] == 2
    assert result["max_abs_difference_mib"] == pytest.approx(20.0)
    assert result["by_interval"][1]["difference_mib"] == pytest.approx(-20.0)

    with pytest.raises(ValueError, match="interval sets differ"):
        matched_rss_residuals(recycled[:1], never)


def test_supervision_projection_retains_external_rss_context_and_polling(
    tmp_path: Path,
):
    path = tmp_path / "supervision-001.json"
    context = {
        "git_commit": "commit",
        "platform": "platform",
        "architecture": "arm64",
        "physical_memory_bytes": 123,
        "software_versions": {"ipopt": [3, 14, 19]},
    }
    record = {
        "invocation": 1,
        "classification": "study_complete",
        "completed_before": 16,
        "completed_after": 64,
        "first_sampled_rss_mib": 100.0,
        "peak_sampled_rss_mib": 200.0,
        "final_sampled_rss_mib": 150.0,
        "restart_to_first_checkpoint_seconds": 20.0,
        "wall_time_seconds": 40.0,
        "checkpoint_sha256_before": "before",
        "checkpoint_sha256_after": "after",
        "context_matches": True,
        "start_context": context,
        "end_context": context,
        "resource_policy": {"poll_seconds": 1.0},
    }
    path.write_text(json.dumps(record))

    projected = _supervision_projection(((path, record),))[0]

    assert projected["first_sampled_rss_mib"] == 100.0
    assert projected["peak_sampled_rss_mib"] == 200.0
    assert projected["final_sampled_rss_mib"] == 150.0
    assert projected["poll_seconds"] == 1.0
    assert projected["start_context"] == context
    assert projected["end_context"] == context


def test_restart_timing_is_baselined_against_the_matched_never_interval():
    records = (
        {
            "completed_before": 16,
            "restart_to_first_checkpoint_seconds": 25.0,
            "poll_seconds": 1.0,
        },
    )

    result = _restart_timing_comparison(records, {16: 20.0})

    assert result == (
        {
            "restart_boundary": 16,
            "restart_to_first_checkpoint_seconds": 25.0,
            "matched_never_interval_seconds": 20.0,
            "estimated_incremental_restart_seconds": 5.0,
            "poll_seconds": 1.0,
        },
    )


def test_resource_chain_reconstruction_accepts_bound_data_and_rejects_tampering(
    tmp_path: Path,
):
    fixture = load_s2_fixture()
    sample = {
        "phase": "recovered_outer_without_checkpoint",
        "invocation": 0,
        "iteration": None,
        "attempt_ordinal": None,
        "elapsed_seconds": 0.25,
        "rss_bytes": 1024,
    }
    payload = {
        "schema_version": 1,
        "source_fingerprint": HISTORICAL_SOURCE_FINGERPRINT,
        "scenario_hash": fixture.scenario_hash,
        "policy_hash": policy_sha256(frozen_p0_policy()),
        "completed_intervals": 0,
        "previous": None,
        "samples": [sample],
    }
    path = tmp_path / "resource-samples-test.json"
    path.write_text(json.dumps(payload))
    checkpoint = {
        "completed_intervals": 0,
        "resource_evidence": {
            "relative_path": path.name,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "completed_intervals": 0,
            "sample_count": 1,
            "chunk_count": 1,
        },
    }

    assert load_resource_samples(tmp_path, checkpoint) == (sample,)

    path.write_text(json.dumps({**payload, "completed_intervals": 1}))
    with pytest.raises(ValueError, match="integrity check failed"):
        load_resource_samples(tmp_path, checkpoint)


def test_compact_result_promotion_is_explicit_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    destination = tmp_path / "RECYCLE_COMPARISON_RESULTS.json"
    expected = {
        "schema_version": 1,
        "observational_study": True,
        "automatic_advancement_gate": False,
    }
    monkeypatch.setattr(recycle_analysis, "analyze_comparison", lambda root: expected)

    result = recycle_analysis.promote_compact_result(
        tmp_path / "ignored-results", destination
    )

    assert result == expected
    assert json.loads(destination.read_text()) == expected
    with pytest.raises(FileExistsError):
        recycle_analysis.promote_compact_result(
            tmp_path / "ignored-results", destination
        )
    assert json.loads(destination.read_text()) == expected


def test_compact_arm_hashes_warm_start_arrays_and_omits_raw_trajectory():
    detailed = {
        "arm": "recycle_16",
        "complete": False,
        "completed_intervals": 16,
        "final_checkpoint_sha256": "checkpoint",
        "outer_plan_sha256": "outer",
        "classifications": ["planned_recycle"],
        "invocation_record_sha256": {"supervision-000.json": "record"},
        "safe_boundary_rss": {"0": {"sample_count": 16}},
        "after_release_series": [],
        "invocations": [],
        "restart_timing": [],
        "trajectory": _trajectory(),
        "warm_start": {
            "16": {
                "transformation": "shifted_preceding",
                "source_kind": "attempt",
                "source_attempt_id": "preceding",
                "preceding_attempt_id": "preceding",
                "preceding_causal_attempt_id": "preceding",
                "causal_source_matches_preceding": True,
                "assigned_start": {"soc_0": [1.0]},
                "solver_x0": [1.0, 2.0],
                "solver_x0_layout": [{"name": "soc_0"}],
                "layout_signature": "layout",
                "structural_signature": {"variables": []},
                "causal_source": {"attempt_id": "current"},
            }
        },
    }

    compact = _compact_arm(detailed)

    assert "trajectory" not in compact
    evidence = compact["warm_start"]["16"]
    assert "assigned_start" not in evidence
    assert "solver_x0" not in evidence
    assert len(evidence["assigned_start_sha256"]) == 64
    assert len(evidence["solver_x0_sha256"]) == 64
    assert evidence["causal_source_matches_preceding"] is True
