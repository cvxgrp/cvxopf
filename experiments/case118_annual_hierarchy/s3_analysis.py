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
    atomic_json,
    load_verified_checkpoint,
    sha256_path,
)


DEFAULT_DIRECTORY = Path("experiments/case118_annual_hierarchy/results/s3_month_rated")
DEFAULT_DESTINATION = Path("experiments/case118_annual_hierarchy/S3_RESULTS.json")


def _read_gzip(path: Path) -> Mapping[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return cast(Mapping[str, object], json.load(stream))


def _supervision_records(directory: Path) -> tuple[Mapping[str, object], ...]:
    records = tuple(
        cast(Mapping[str, object], json.loads(path.read_text()))
        for path in sorted(directory.glob("supervision-*.json"))
    )
    expected_boundaries = (0, *S3_RESTART_BOUNDARIES, S3_HORIZON_STEPS)
    if len(records) != len(expected_boundaries) - 1:
        raise ValueError("S3 supervision registry does not contain 45 invocations")
    for index, record in enumerate(records):
        before = expected_boundaries[index]
        after = expected_boundaries[index + 1]
        expected_classification = (
            "study_complete" if after == S3_HORIZON_STEPS else "planned_recycle"
        )
        if record.get("invocation") != index:
            raise ValueError("S3 supervision invocation sequence mismatch")
        if record.get("completed_before") != before or record.get("completed_after") != after:
            raise ValueError("S3 supervision global-boundary sequence mismatch")
        if record.get("classification") != expected_classification:
            raise ValueError("S3 supervision classification sequence mismatch")
        if record.get("context_matches") is not True:
            raise ValueError("S3 supervision provenance mismatch")
    return records


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
    if len(metrics) != S3_HORIZON_STEPS:
        raise ValueError("S3 checkpoint does not contain all 720 intervals")
    storage_ids = tuple(str(unit.device_id) for unit in inputs.storage)
    throughput = np.sum(
        np.asarray([item["storage_throughput_mwh"] for item in metrics]), axis=0
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
        audits_equal
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
            "classification": record["classification"],
            "completed_before": record["completed_before"],
            "completed_after": record["completed_after"],
            "peak_sampled_rss_mib": record["peak_sampled_rss_mib"],
            "wall_time_seconds": record["wall_time_seconds"],
            "sha256": sha256_path(_supervision_path(directory, _integer(record["invocation"]))),
        }
        for record in supervisions
    ]
    return {
        "schema_version": 1,
        "observational_study": True,
        "execution_complete": True,
        "numerically_eligible_for_s4_review": numerically_eligible,
        "automatic_advancement_gate": False,
        "completed_intervals": len(metrics),
        "planned_restart_count": len(S3_RESTART_BOUNDARIES),
        "worker_invocation_count": len(supervisions),
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


def _supervision_path(directory: Path, invocation: int) -> Path:
    return directory / f"supervision-{invocation:03d}.json"


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
        atomic_json(args.destination, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["numerically_eligible_for_s4_review"] is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
