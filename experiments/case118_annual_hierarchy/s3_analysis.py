"""Independent archive reconstruction and compact promotion for S3."""

from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path
from typing import Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.p0_fixture import (
    frozen_p0_policy,
    policy_sha256,
)
from experiments.case118_annual_hierarchy.s2_analysis import (
    _integer,
    _interval_metrics,
    _number,
    _shifted_primary_statistics,
)
from experiments.case118_annual_hierarchy.s3_fixture import (
    S3_HORIZON_STEPS,
    S3_RESTART_BOUNDARIES,
    load_s3_fixture,
)
from experiments.case118_annual_hierarchy.streaming_archive import (
    load_verified_outer_plan_archive,
    outer_boundaries,
    residual_tolerances,
    result_dimensions,
)
from experiments.case118_annual_hierarchy.streaming_driver import (
    _load_resource_samples,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    load_verified_checkpoint,
    sha256_path,
)


DEFAULT_DIRECTORY = Path("experiments/case118_annual_hierarchy/results/s3_month_rated")
DEFAULT_DESTINATION = Path("experiments/case118_annual_hierarchy/S3_RESULTS.json")


def _read_gzip(path: Path) -> Mapping[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return cast(Mapping[str, object], json.load(stream))


ABNORMAL_CLASSIFICATIONS = frozenset(
    {
        "rss_limit",
        "invocation_wall_limit",
        "total_wall_limit",
        "checkpoint_stall_limit",
        "worker_failure",
        "artifact_failure",
        "provenance_mismatch",
        "reviewed_interruption",
    }
)


def _supervision_records(directory: Path) -> tuple[Mapping[str, object], ...]:
    """Validate the actual chronological S3 supervision chain."""
    paths = (
        *((path, "supervision") for path in directory.glob("supervision-*.json")),
        *(
            (path, "interrupted_invocation")
            for path in directory.glob("interrupted-invocation-*.json")
        ),
    )
    records = tuple(
        sorted(
            (
                {
                    **cast(Mapping[str, object], json.loads(path.read_text())),
                    "record_kind": kind,
                    "record_sha256": sha256_path(path),
                }
                for path, kind in paths
            ),
            key=lambda record: _integer(record["invocation"]),
        )
    )
    if not records:
        raise ValueError("S3 supervision registry is empty")
    current_boundary = 0
    previous_checkpoint: object = None
    consumed_authorizations: set[Path] = set()
    for index, record in enumerate(records):
        if record.get("invocation") != index:
            raise ValueError("S3 supervision invocation sequence mismatch")
        before = _integer(record["completed_before"])
        after = _integer(record["completed_after"])
        if before != current_boundary or not before <= after <= S3_HORIZON_STEPS:
            raise ValueError("S3 supervision global-boundary sequence mismatch")
        checkpoint_before = record.get("checkpoint_sha256_before")
        checkpoint_after = record.get("checkpoint_sha256_after")
        if index == 0 and before == 0:
            if checkpoint_before is not None:
                raise ValueError("S3 first invocation has unexpected starting checkpoint")
        elif not isinstance(checkpoint_before, str) or not checkpoint_before:
            raise ValueError("S3 supervision lacks its starting checkpoint identity")
        if not isinstance(checkpoint_after, str) or not checkpoint_after:
            raise ValueError("S3 supervision lacks its retained checkpoint identity")
        if index > 0 and checkpoint_before != previous_checkpoint:
            raise ValueError("S3 supervision checkpoint chain mismatch")
        classification = str(record.get("classification"))
        if (
            record["record_kind"] == "interrupted_invocation"
            and classification != "reviewed_interruption"
        ) or (
            record["record_kind"] == "supervision"
            and classification == "reviewed_interruption"
        ):
            raise ValueError("S3 lifecycle record kind and classification mismatch")
        if classification == "planned_recycle":
            later = [value for value in S3_RESTART_BOUNDARIES if value > before]
            if not later or after != later[0]:
                raise ValueError("S3 planned recycle is not the next global boundary")
            if record.get("context_matches") is not True:
                raise ValueError("S3 supervision provenance mismatch")
        elif classification == "study_complete":
            if after != S3_HORIZON_STEPS or index != len(records) - 1:
                raise ValueError("S3 study-complete record is not terminal at 720")
            if record.get("context_matches") is not True:
                raise ValueError("S3 supervision provenance mismatch")
        elif classification in ABNORMAL_CLASSIFICATIONS:
            if index < len(records) - 1:
                authorization_path = directory / f"reviewed-continuation-{index + 1:03d}.json"
                if not authorization_path.is_file():
                    raise ValueError("S3 abnormal outcome lacks reviewed continuation")
                authorization = cast(
                    Mapping[str, object], json.loads(authorization_path.read_text())
                )
                prior_path = directory / str(authorization.get("prior_record_path"))
                if (
                    authorization.get("next_invocation") != index + 1
                    or authorization.get("prior_record_kind")
                    != record["record_kind"]
                    or authorization.get("prior_invocation") != index
                    or authorization.get("prior_classification") != classification
                    or authorization.get("completed_intervals") != after
                    or authorization.get("checkpoint_sha256") != checkpoint_after
                    or authorization.get("execution_context")
                    != record.get("start_context")
                    or records[index + 1].get("start_context")
                    != authorization.get("execution_context")
                    or not prior_path.is_file()
                    or prior_path.name
                    != (
                        f"supervision-{index:03d}.json"
                        if record["record_kind"] == "supervision"
                        else f"interrupted-invocation-{index:03d}.json"
                    )
                    or authorization.get("prior_record_sha256")
                    != record["record_sha256"]
                ):
                    raise ValueError("S3 reviewed-continuation identity mismatch")
                record["reviewed_continuation"] = {
                    **authorization,
                    "sha256": sha256_path(authorization_path),
                }
                consumed_authorizations.add(authorization_path)
        else:
            raise ValueError("S3 supervision classification sequence mismatch")
        current_boundary = after
        previous_checkpoint = checkpoint_after
    if set(directory.glob("reviewed-continuation-*.json")) != consumed_authorizations:
        raise ValueError("S3 reviewed-continuation registry contains an orphan record")
    return records


def _execution_disposition(
    records: Sequence[Mapping[str, object]], completed_intervals: int
) -> tuple[bool, str, str]:
    """Separate trajectory completeness from its terminal supervision outcome."""
    terminal_outcome = str(records[-1]["classification"])
    complete = bool(
        terminal_outcome == "study_complete"
        and completed_intervals == S3_HORIZON_STEPS
    )
    return complete, "complete" if complete else "partial", terminal_outcome


def analyze_s3(
    directory: Path,
    *,
    source_fingerprint: str,
    scenario_hash: str,
) -> Mapping[str, object]:
    """Verify the complete S3 artifact tree and reconstruct realized summaries."""
    fixture = load_s3_fixture()
    inputs = fixture.inputs
    policy = frozen_p0_policy()
    if scenario_hash != fixture.scenario_hash:
        raise ValueError("S3 analysis scenario hash mismatch")
    trajectory = directory / "trajectory"
    outer_path = trajectory / "outer-plan.json.gz"
    checkpoint_path = trajectory / "checkpoint.json"
    outer_sha = sha256_path(outer_path)
    outer = load_verified_outer_plan_archive(
        outer_path,
        inputs=inputs,
        policy=policy,
        expected_solve_config_sha256=fixture.solve_config_sha256,
        expected_source_fingerprint=source_fingerprint,
        expected_scenario_hash=scenario_hash,
    )
    checkpoint = load_verified_checkpoint(
        checkpoint_path,
        expected_source_fingerprint=source_fingerprint,
        expected_scenario_hash=scenario_hash,
        expected_outer_plan_sha256=outer_sha,
        expected_policy_hash=policy_sha256(policy),
        expected_soc_tolerance_mwh=policy.tolerances.soc_recurrence_mwh_abs,
        expected_residual_tolerances=residual_tolerances(policy),
        expected_inner_terminal_policy=policy.inner_terminal_policy,
        expected_horizon_steps=S3_HORIZON_STEPS,
        expected_ac_window_steps=policy.ac_window_steps,
        expected_result_dimensions=result_dimensions(inputs),
        expected_delta_hours=inputs.delta,
        expected_outer_boundary_soc_mwh=outer_boundaries(outer),
    )
    supervisions = _supervision_records(directory)
    if any(record.get("outer_plan_sha256") != outer_sha for record in supervisions):
        raise ValueError("S3 supervision outer-plan identity mismatch")
    if any(
        cast(Mapping[str, object], record["start_context"]).get("source_fingerprint")
        != source_fingerprint
        for record in supervisions
    ):
        raise ValueError("S3 supervision source fingerprint mismatch")
    samples, _, _ = _load_resource_samples(
        trajectory,
        checkpoint,
        source_fingerprint=source_fingerprint,
        scenario_hash=scenario_hash,
        policy_hash=policy_sha256(policy),
    )
    entries = cast(Sequence[Mapping[str, object]], checkpoint["windows"])
    windows = [_read_gzip(trajectory / str(item["relative_path"])) for item in entries]
    metrics = [_interval_metrics(window, inputs) for window in windows]
    completed = len(metrics)
    if _integer(checkpoint["completed_intervals"]) != completed:
        raise ValueError("S3 checkpoint interval count mismatch")
    (
        execution_complete,
        trajectory_classification,
        terminal_supervision_classification,
    ) = _execution_disposition(
        supervisions,
        completed,
    )
    if _integer(supervisions[-1]["completed_after"]) != completed:
        raise ValueError("S3 supervision and checkpoint completion mismatch")
    storage_ids = tuple(str(unit.device_id) for unit in inputs.storage)
    throughput = (
        np.sum(
            np.asarray([item["storage_throughput_mwh"] for item in metrics]), axis=0
        )
        if metrics
        else np.zeros(len(storage_ids))
    )
    final_soc = np.asarray(checkpoint["realized_soc_mwh"], dtype=float)
    target = np.asarray([cast(float, unit.terminal_soc) for unit in inputs.storage])
    ordinals = [_integer(item["controlling_ordinal"]) for item in metrics]
    audits_equal = all(
        item["controlling_audit_reconstructed_and_equal"] is True for item in metrics
    )
    terminal_residual = float(np.max(np.abs(final_soc - target)))
    recurrence = max(
        (_number(item["soc_recurrence_residual_mwh_abs"]) for item in metrics),
        default=0.0,
    )
    load_service = max(
        (_number(item["fixed_load_service_residual_mw_abs"]) for item in metrics),
        default=0.0,
    )
    voltage = max(
        (_number(item["voltage_violation_pu"]) for item in metrics), default=0.0
    )
    thermal = max(
        (_number(item["thermal_residual_mva"]) for item in metrics), default=0.0
    )
    normalized_thermal = max(
        (_number(item["normalized_squared_thermal_residual"]) for item in metrics),
        default=0.0,
    )
    numerically_eligible = bool(
        execution_complete
        and audits_equal
        and terminal_residual <= policy.tolerances.terminal_soc_mwh_abs
        and recurrence <= policy.tolerances.soc_recurrence_mwh_abs
        and load_service
        <= policy.tolerances.ac_active_balance_pu_abs * _number(inputs.case["baseMVA"])
        and voltage <= policy.tolerances.voltage_bound_pu_abs
        and thermal <= policy.tolerances.branch_mva_abs
        and normalized_thermal
        <= policy.tolerances.branch_normalized_squared_residual
    )
    invocation_summaries = [
        {
            "invocation": record["invocation"],
            "record_kind": record["record_kind"],
            "classification": record["classification"],
            "completed_before": record["completed_before"],
            "completed_after": record["completed_after"],
            "peak_sampled_rss_mib": record.get("peak_sampled_rss_mib"),
            "wall_time_seconds": record["wall_time_seconds"],
            "sha256": record["record_sha256"],
            "reviewed_continuation": record.get("reviewed_continuation"),
        }
        for record in supervisions
    ]
    return {
        "schema_version": 1,
        "observational_study": True,
        "execution_complete": execution_complete,
        "trajectory_classification": trajectory_classification,
        "terminal_supervision_classification": terminal_supervision_classification,
        "numerically_eligible_for_s4_review": numerically_eligible,
        "automatic_advancement_gate": False,
        "completed_intervals": completed,
        "coverage_fraction": completed / S3_HORIZON_STEPS,
        "planned_restart_count": sum(
            record["classification"] == "planned_recycle" for record in supervisions
        ),
        "abnormal_stop_count": sum(
            str(record["classification"]) in ABNORMAL_CLASSIFICATIONS
            for record in supervisions
        ),
        "worker_invocation_count": len(supervisions),
        "supervision_outcome_count": sum(
            record["record_kind"] == "supervision" for record in supervisions
        ),
        "interrupted_lifecycle_count": sum(
            record["record_kind"] == "interrupted_invocation"
            for record in supervisions
        ),
        "reviewed_continuation_count": sum(
            record.get("reviewed_continuation") is not None
            for record in supervisions
        ),
        "storage_device_ids": list(storage_ids),
        "final_soc_mwh": final_soc.tolist(),
        "terminal_deviation_mwh": (final_soc - target).tolist(),
        "storage_throughput_mwh": throughput.tolist(),
        "controlling_ordinal_counts": {
            str(value): ordinals.count(value) for value in sorted(set(ordinals))
        },
        "recovery_window_count": sum(value != 0 for value in ordinals),
        **_shifted_primary_statistics(metrics),
        "generation_cost": sum(_number(item["generation_cost"]) for item in metrics),
        "storage_cycling_cost": sum(
            _number(item["storage_cycling_cost"]) for item in metrics
        ),
        "renewable_curtailment_mwh": sum(
            _number(item["renewable_curtailment_mwh"]) for item in metrics
        ),
        "active_loss_mwh": sum(_number(item["active_loss_mwh"]) for item in metrics),
        "maximum_terminal_deviation_mwh_abs": terminal_residual,
        "maximum_soc_recurrence_residual_mwh_abs": recurrence,
        "maximum_fixed_load_service_residual_mw_abs": load_service,
        "maximum_voltage_violation_pu": voltage,
        "maximum_thermal_residual_mva": thermal,
        "maximum_normalized_squared_thermal_residual": normalized_thermal,
        "cumulative_absolute_signpost_deviation_mwh": sum(
            _number(item["terminal_deviation_mwh_abs"]) for item in metrics
        ),
        "maximum_current_rss_mib": max(
            (sample.rss_bytes / (1024.0**2) for sample in samples), default=0.0
        ),
        "source_fingerprint": source_fingerprint,
        "scenario_hash": scenario_hash,
        "outer_plan_sha256": outer_sha,
        "checkpoint_sha256": sha256_path(checkpoint_path),
        "invocations": invocation_summaries,
    }


def _promote_completed_result(destination: Path, result: Mapping[str, object]) -> None:
    """Publish only the complete authoritative result, exactly once."""
    if result.get("execution_complete") is not True:
        raise ValueError("partial S3 analysis cannot be promoted")
    atomic_immutable_json(destination, result)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=DEFAULT_DIRECTORY)
    parser.add_argument("--source-fingerprint", required=True)
    parser.add_argument("--scenario-hash", required=True)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--destination", type=Path, default=DEFAULT_DESTINATION)
    args = parser.parse_args()
    result = analyze_s3(
        args.directory,
        source_fingerprint=args.source_fingerprint,
        scenario_hash=args.scenario_hash,
    )
    if args.promote:
        _promote_completed_result(args.destination, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["numerically_eligible_for_s4_review"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
