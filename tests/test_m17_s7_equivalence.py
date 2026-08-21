"""Non-scientific infrastructure tests for the M17-S7 comparison runner."""

import gzip
import json

import pytest

from experiments.hierarchical_battery_resilience import s7_equivalence


def test_s7_reference_artifacts_match_tracked_integrity_metadata():
    checks = s7_equivalence.verify_reference_integrity()

    assert checks["scenario_manifest_sha256"]
    assert "s3/frozen__hard_equality.json.gz" in checks
    assert "s3b/causal_recovery.json.gz" in checks


@pytest.mark.parametrize("case_name", s7_equivalence.ALL_CASES)
def test_s7_case_materialization_uses_frozen_public_contract(case_name):
    inputs = s7_equivalence._inputs()
    policy = s7_equivalence._policy(case_name)

    assert inputs.horizon_steps == 96
    assert policy.ac_window_steps == 5
    assert policy.outer_policy in case_name
    assert policy.inner_terminal_policy in case_name
    expected_initialization = (
        "shifted_with_recovery"
        if case_name == s7_equivalence.S3B_CASE
        else "flat_only"
    )
    assert policy.initialization_policy == expected_initialization


def test_s7_s3_normalization_retains_manual_only_failure_diagnostic():
    path = (
        s7_equivalence.S3_DIRECTORY
        / "replan_every_step__hard_equality.json.gz"
    )
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        reference = json.load(stream)

    controlling = s7_equivalence._controlling_s3_attempts(reference)

    assert len(controlling) == 36
    assert len(reference["ac_attempts"]) == 37
    assert controlling[-1]["iteration"] == 35
    assert controlling[-1]["audit"]["outcome"] == "solver_certified_infeasible"
    assert reference["ac_attempts"][-1]["attempt_kind"] == "diagnostic"


def test_s7_array_comparison_rejects_shape_and_tolerance_drift():
    assert s7_equivalence._array_comparison("same", [1.0], [1.0]).passed
    assert not s7_equivalence._array_comparison(
        "shape", [1.0], [[1.0]]
    ).passed
    comparison = s7_equivalence._array_comparison(
        "numeric", [1.0], [1.0 + 2 * s7_equivalence.NUMERIC_ATOL]
    )
    assert not comparison.passed
    assert comparison.maximum_absolute_difference is not None


def test_s7_skipped_s3b_slot_does_not_require_an_audit():
    assert not s7_equivalence._expected_supplied_action(
        {"supplied_executed_action": False, "audit": None}
    )
    assert s7_equivalence._expected_supplied_action(
        {"audit": {"accepted_primal": True}}
    )


def test_s7_normalizes_reviewed_cross_schema_equivalences():
    assert s7_equivalence._outcome_class("accepted_soft") == "accepted"
    assert s7_equivalence._outcome_class("unusable_primal") == (
        "unusable_primal"
    )
    assert s7_equivalence._is_unavailable_scalar(None)
    assert s7_equivalence._is_unavailable_scalar(float("nan"))
    assert not s7_equivalence._is_unavailable_scalar(0.0)
    assert s7_equivalence._array_comparison("absent", None, None).passed
