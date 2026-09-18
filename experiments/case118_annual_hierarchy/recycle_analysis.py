"""Independently reconstruct the pre-S3 worker-recycling comparison."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from statistics import median
from typing import Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.p0_fixture import (
    frozen_p0_policy,
    policy_sha256,
)
from experiments.case118_annual_hierarchy.reference.extract_s2_reference import (
    EXPECTED_OUTER_SHA256,
    HISTORICAL_SOURCE_FINGERPRINT,
    verify_tracked_reference,
)
from experiments.case118_annual_hierarchy.run_s0 import ROOT
from experiments.case118_annual_hierarchy.s2_fixture import load_s2_fixture
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)

EXPERIMENT_DIR = ROOT / "experiments/case118_annual_hierarchy"
DEFAULT_RESULTS_ROOT = EXPERIMENT_DIR / "results/recycle_comparison"
DEFAULT_COMPACT_PATH = EXPERIMENT_DIR / "RECYCLE_COMPARISON_RESULTS.json"
SCHEMA_VERSION = 1


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return cast(Mapping[str, object], value)


def _sequence(value: object, name: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    return cast(Sequence[object], value)


def _load_json(path: Path) -> Mapping[str, object]:
    return _mapping(json.loads(path.read_text()), path.name)


def _load_gzip_json(path: Path) -> Mapping[str, object]:
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return _mapping(json.load(stream), path.name)


def _value_sha256(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _controlling_attempt(window: Mapping[str, object]) -> Mapping[str, object]:
    executed = _mapping(window.get("executed_interval"), "executed interval")
    controlling_id = executed.get("controlling_attempt_id")
    matches = [
        _mapping(item, "attempt")
        for item in _sequence(window.get("attempts"), "attempts")
        if _mapping(item, "attempt").get("attempt_id") == controlling_id
    ]
    if len(matches) != 1:
        raise ValueError("window must contain exactly one controlling attempt")
    return matches[0]


def load_resource_samples(
    trajectory: Path, checkpoint: Mapping[str, object]
) -> tuple[Mapping[str, object], ...]:
    """Independently verify the checkpoint-bound immutable resource chain."""
    fixture = load_s2_fixture()
    policy = frozen_p0_policy()
    head = _mapping(checkpoint.get("resource_evidence"), "resource evidence")
    if head.get("completed_intervals") != checkpoint.get("completed_intervals"):
        raise ValueError("resource evidence interval mismatch")
    expected_chunks = head.get("chunk_count")
    expected_samples = head.get("sample_count")
    if not isinstance(expected_chunks, int) or expected_chunks <= 0:
        raise ValueError("resource evidence chunk count is invalid")
    if not isinstance(expected_samples, int) or expected_samples < 0:
        raise ValueError("resource evidence sample count is invalid")
    chunks: list[list[Mapping[str, object]]] = []
    intervals: list[int] = []
    current: Mapping[str, object] | None = head
    seen: set[str] = set()
    root = trajectory.resolve()
    while current is not None:
        relative = current.get("relative_path")
        if (
            not isinstance(relative, str)
            or not relative.startswith("resource-samples-")
            or not relative.endswith(".json")
            or Path(relative).name != relative
            or relative in seen
        ):
            raise ValueError("resource evidence path mismatch")
        seen.add(relative)
        path = (trajectory / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("resource evidence artifact is missing")
        if path.stat().st_size != current.get("bytes") or sha256_path(
            path
        ) != current.get("sha256"):
            raise ValueError("resource evidence integrity check failed")
        payload = _load_json(path)
        expected = {
            "schema_version": 1,
            "source_fingerprint": HISTORICAL_SOURCE_FINGERPRINT,
            "scenario_hash": fixture.scenario_hash,
            "policy_hash": policy_sha256(policy),
        }
        if any(payload.get(name) != value for name, value in expected.items()):
            raise ValueError("resource evidence provenance mismatch")
        interval = payload.get("completed_intervals")
        if not isinstance(interval, int) or interval < 0:
            raise ValueError("resource evidence interval is invalid")
        intervals.append(interval)
        records: list[Mapping[str, object]] = []
        for item in _sequence(payload.get("samples"), "resource samples"):
            record = _mapping(item, "resource sample")
            required = {
                "phase",
                "invocation",
                "iteration",
                "attempt_ordinal",
                "elapsed_seconds",
                "rss_bytes",
            }
            if set(record) != required:
                raise ValueError("resource sample fields mismatch")
            if (
                not isinstance(record["phase"], str)
                or not record["phase"]
                or int(cast(int, record["invocation"])) < 0
                or float(cast(float, record["elapsed_seconds"])) < 0
                or int(cast(int, record["rss_bytes"])) < 0
            ):
                raise ValueError("resource sample contains invalid values")
            records.append(record)
        chunks.append(records)
        previous = payload.get("previous")
        if previous is not None and not isinstance(previous, Mapping):
            raise ValueError("resource evidence previous link is malformed")
        current = cast(Mapping[str, object] | None, previous)
    if len(chunks) != expected_chunks:
        raise ValueError("resource evidence chunk count mismatch")
    ordered = list(reversed(intervals))
    if (
        not ordered
        or ordered[0] != 0
        or ordered[-1] != checkpoint["completed_intervals"]
        or any(
            later - earlier not in {0, 1}
            for earlier, later in zip(ordered, ordered[1:], strict=False)
        )
    ):
        raise ValueError("resource evidence interval chain is discontinuous")
    samples = tuple(record for chunk in reversed(chunks) for record in chunk)
    if len(samples) != expected_samples:
        raise ValueError("resource evidence sample count mismatch")
    return samples


def _invocation_records(
    directory: Path,
) -> tuple[tuple[Path, Mapping[str, object]], ...]:
    paths = sorted(
        (
            *directory.glob("supervision-*.json"),
            *directory.glob("interrupted-invocation-*.json"),
        ),
        key=lambda path: int(path.stem.rsplit("-", maxsplit=1)[1]),
    )
    records = tuple((path, _load_json(path)) for path in paths)
    invocations = [record.get("invocation") for _, record in records]
    if invocations != list(range(len(records))):
        raise ValueError("invocation record chain is not contiguous")
    return records


def _rss_summary(samples: Sequence[Mapping[str, object]]) -> Mapping[str, object]:
    by_invocation: dict[int, list[float]] = {}
    for sample in samples:
        if sample["phase"] != "after_release":
            continue
        invocation = int(cast(int, sample["invocation"]))
        by_invocation.setdefault(invocation, []).append(
            int(cast(int, sample["rss_bytes"])) / (1024.0**2)
        )
    result: dict[str, object] = {}
    for invocation, values in sorted(by_invocation.items()):
        late_count = 8 if len(values) >= 12 else min(4, len(values))
        late = values[-late_count:]
        result[str(invocation)] = {
            "sample_count": len(values),
            "late_sample_count": late_count,
            "late_min_mib": min(late),
            "late_median_mib": median(late),
            "late_max_mib": max(late),
            "late_iqr_mib": float(np.percentile(late, 75) - np.percentile(late, 25)),
            "final_4_mib": values[-4:],
            "final_8_mib": values[-8:],
        }
    return result


def _after_release_series(
    samples: Sequence[Mapping[str, object]],
) -> tuple[Mapping[str, object], ...]:
    """Return current RSS and elapsed time indexed by executed global interval."""
    records: list[Mapping[str, object]] = []
    seen: set[int] = set()
    for sample in samples:
        if sample["phase"] != "after_release":
            continue
        iteration = int(cast(int, sample["iteration"]))
        if iteration in seen:
            raise ValueError("after-release RSS contains a duplicate interval")
        seen.add(iteration)
        records.append(
            {
                "iteration": iteration,
                "invocation": int(cast(int, sample["invocation"])),
                "rss_mib": int(cast(int, sample["rss_bytes"])) / (1024.0**2),
                "elapsed_seconds": float(cast(float, sample["elapsed_seconds"])),
            }
        )
    records.sort(key=lambda item: int(cast(int, item["iteration"])))
    if [item["iteration"] for item in records] != list(range(len(records))):
        raise ValueError("after-release RSS is not a contiguous interval series")
    return tuple(records)


def _supervision_projection(
    invocation_records: Sequence[tuple[Path, Mapping[str, object]]],
) -> tuple[Mapping[str, object], ...]:
    """Retain bounded external-resource and provenance evidence per invocation."""
    projected: list[Mapping[str, object]] = []
    for path, record in invocation_records:
        if path.name.startswith("interrupted-invocation-"):
            projected.append(
                {
                    "invocation": record.get("invocation"),
                    "classification": record.get("classification"),
                    "wall_time_seconds": record.get("wall_time_seconds"),
                    "record_sha256": sha256_path(path),
                }
            )
            continue
        resource_policy = _mapping(
            record.get("resource_policy"), "supervision resource policy"
        )
        projected.append(
            {
                "invocation": record.get("invocation"),
                "classification": record.get("classification"),
                "completed_before": record.get("completed_before"),
                "completed_after": record.get("completed_after"),
                "first_sampled_rss_mib": record.get("first_sampled_rss_mib"),
                "peak_sampled_rss_mib": record.get("peak_sampled_rss_mib"),
                "final_sampled_rss_mib": record.get("final_sampled_rss_mib"),
                "restart_to_first_checkpoint_seconds": record.get(
                    "restart_to_first_checkpoint_seconds"
                ),
                "wall_time_seconds": record.get("wall_time_seconds"),
                "poll_seconds": resource_policy.get("poll_seconds"),
                "checkpoint_sha256_before": record.get("checkpoint_sha256_before"),
                "checkpoint_sha256_after": record.get("checkpoint_sha256_after"),
                "context_matches": record.get("context_matches"),
                "start_context": record.get("start_context"),
                "end_context": record.get("end_context"),
                "record_sha256": sha256_path(path),
            }
        )
    return tuple(projected)


def _trajectory_projection(
    windows: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    if not windows:
        raise ValueError("trajectory projection requires at least one window")
    storage_ids = list(_sequence(windows[0].get("storage_device_ids"), "storage IDs"))
    initial = list(_sequence(windows[0].get("initial_soc_mwh"), "initial SoC"))
    b: list[object] = []
    soc: list[object] = [initial]
    attempts: list[Mapping[str, object]] = []
    for iteration, window in enumerate(windows):
        if window.get("iteration") != iteration:
            raise ValueError("window trajectory is not a contiguous prefix")
        if (
            list(_sequence(window.get("storage_device_ids"), "storage IDs"))
            != storage_ids
        ):
            raise ValueError("storage identity changed within trajectory")
        attempt = _controlling_attempt(window)
        executed = _mapping(window.get("executed_interval"), "executed interval")
        b.append(executed.get("b_mw"))
        soc.append(window.get("post_step_soc_mwh"))
        attempts.append(
            {
                "iteration": iteration,
                "attempt_id": attempt.get("attempt_id"),
                "ordinal": attempt.get("ordinal"),
                "transformation": attempt.get("transformation"),
                "source_kind": attempt.get("source_kind"),
                "source_attempt_id": attempt.get("source_attempt_id"),
            }
        )
    return {
        "storage_device_ids": storage_ids,
        "executed_b_mw": b,
        "realized_soc_mwh": soc,
        "attempts": attempts,
    }


def _canonical_layout(value: object) -> object:
    if not isinstance(value, list):
        return value
    auxiliary = 0
    records: list[object] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            records.append(raw)
            continue
        record = dict(raw)
        if not bool(record.get("is_original_variable")):
            record["name"] = f"auxiliary_{auxiliary}"
            auxiliary += 1
        records.append(record)
    return records


def _canonical_solver_evidence(value: object) -> object:
    evidence = _mapping(value, "solver evidence")
    result = dict(evidence)
    result.pop("layout_signature", None)
    raw_before = result.pop("object_ids_before", None)
    raw_after = result.pop("object_ids_after", None)
    if raw_before is None and raw_after is None:
        return result
    before = _mapping(raw_before, "object IDs before")
    after = _mapping(raw_after, "object IDs after")
    if set(before) != set(after):
        raise ValueError("solver object-identity groups differ")
    result["object_identity"] = {
        name: {
            "count": len(_sequence(before[name], f"{name} object IDs")),
            "preserved": before[name] == after[name],
        }
        for name in sorted(before)
    }
    return result


def _warm_start_projection(
    windows: Sequence[Mapping[str, object]], boundaries: Sequence[int]
) -> Mapping[str, object]:
    result: dict[str, object] = {}
    for boundary in boundaries:
        if boundary >= len(windows):
            continue
        attempt = _controlling_attempt(windows[boundary])
        current_causal = _mapping(attempt.get("causal_source"), "causal source")
        if boundary == 0:
            preceding_id = None
            preceding_causal_id = None
            source_is_causal = bool(
                attempt.get("source_kind") == "generated_flat"
                and attempt.get("source_attempt_id") is None
            )
        else:
            preceding = _controlling_attempt(windows[boundary - 1])
            preceding_causal = _mapping(
                preceding.get("causal_source"), "preceding causal source"
            )
            preceding_id = preceding.get("attempt_id")
            preceding_causal_id = preceding_causal.get("attempt_id")
            source_is_causal = bool(
                attempt.get("source_attempt_id") == preceding_id
                and preceding_causal_id == preceding_id
            )
        layout = attempt.get("solver_x0_layout")
        evidence = _mapping(attempt.get("solver_evidence"), "solver evidence")
        result[str(boundary)] = {
            "transformation": attempt.get("transformation"),
            "source_kind": attempt.get("source_kind"),
            "source_attempt_id": attempt.get("source_attempt_id"),
            "preceding_attempt_id": preceding_id,
            "preceding_causal_attempt_id": preceding_causal_id,
            "causal_source_matches_preceding": source_is_causal,
            "assigned_start": attempt.get("assigned_start"),
            "solver_x0": attempt.get("solver_x0"),
            "solver_x0_layout": _canonical_layout(layout),
            "solver_evidence": _canonical_solver_evidence(evidence),
            "layout_signature": evidence.get("layout_signature"),
            "structural_signature": attempt.get("structural_signature"),
            "causal_source": current_causal,
        }
    return result


def analyze_arm(directory: Path) -> Mapping[str, object]:
    """Validate and project one complete or partial comparison arm."""
    from experiments.case118_annual_hierarchy.run_recycle_comparison import (
        ARM_BOUNDARIES,
        STUDY_STOP,
        verify_checkpoint,
    )

    arm = directory.name
    if arm not in ARM_BOUNDARIES:
        raise ValueError("unknown comparison arm directory")
    checkpoint_path = directory / "trajectory/checkpoint.json"
    checkpoint = verify_checkpoint(checkpoint_path)
    completed = int(cast(int, checkpoint["completed_intervals"]))
    if completed > STUDY_STOP:
        raise ValueError("comparison arm exceeds the study boundary")
    entries = _sequence(checkpoint.get("windows"), "checkpoint windows")
    windows = [
        _load_gzip_json(
            directory
            / "trajectory"
            / str(_mapping(item, "window entry")["relative_path"])
        )
        for item in entries
    ]
    samples = load_resource_samples(directory / "trajectory", checkpoint)
    invocation_records = _invocation_records(directory)
    records = [record for _, record in invocation_records]
    classifications = [record.get("classification") for record in records]
    complete = completed == STUDY_STOP and classifications[-1:] == ["study_complete"]
    if complete and list(range(completed)) != [
        window["iteration"] for window in windows
    ]:
        raise ValueError("completed arm does not contain exactly intervals 0..63")
    restart_boundaries = [
        int(cast(int, record["completed_after"]))
        for record in records
        if record.get("classification") == "planned_recycle"
    ]
    eligible_restarts = [
        boundary for boundary in ARM_BOUNDARIES[arm] if boundary <= completed
    ]
    if complete:
        restart_history_valid = restart_boundaries == list(ARM_BOUNDARIES[arm])
    else:
        restart_history_valid = bool(
            len(restart_boundaries) == len(set(restart_boundaries))
            and all(boundary in eligible_restarts for boundary in restart_boundaries)
            and restart_boundaries == sorted(restart_boundaries)
        )
    if not restart_history_valid:
        raise ValueError("planned restart history differs from arm schedule")
    comparison_boundaries = [
        boundary
        for boundary in (
            0,
            *sorted(
                {item for boundaries in ARM_BOUNDARIES.values() for item in boundaries}
            ),
        )
        if boundary < completed
    ]
    return {
        "arm": arm,
        "complete": complete,
        "completed_intervals": completed,
        "final_checkpoint_sha256": sha256_path(checkpoint_path),
        "outer_plan_sha256": checkpoint["outer_plan_sha256"],
        "trajectory": _trajectory_projection(windows),
        "warm_start": _warm_start_projection(windows, comparison_boundaries),
        "planned_restart_boundaries": restart_boundaries,
        "safe_boundary_rss": _rss_summary(samples),
        "after_release_series": _after_release_series(samples),
        "invocations": _supervision_projection(invocation_records),
        "invocation_record_sha256": {
            path.name: sha256_path(path) for path, _ in invocation_records
        },
        "classifications": classifications,
    }


def _max_abs(left: object, right: object) -> float:
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    if left_array.shape != right_array.shape:
        raise ValueError("numerical comparison shape mismatch")
    if not np.all(np.isfinite(left_array)) or not np.all(np.isfinite(right_array)):
        raise ValueError("numerical comparison contains nonfinite values")
    return float(np.max(np.abs(left_array - right_array), initial=0.0))


def _max_abs_and_normalized(left: object, right: object) -> Mapping[str, float]:
    right_array = np.asarray(right, dtype=float)
    absolute = _max_abs(left, right)
    scale = max(1.0, float(np.max(np.abs(right_array), initial=0.0)))
    return {"max_abs": absolute, "reference_scale": scale, "normalized": absolute / scale}


def _named_residuals(left: object, right: object, name: str) -> Mapping[str, object]:
    left_mapping = _mapping(left, f"left {name}")
    right_mapping = _mapping(right, f"right {name}")
    if set(left_mapping) != set(right_mapping):
        raise ValueError(f"{name} variable groups differ")
    return {
        key: _max_abs_and_normalized(left_mapping[key], right_mapping[key])
        for key in sorted(left_mapping)
    }


def causal_source_residuals(
    left: Mapping[str, object], right: Mapping[str, object]
) -> Mapping[str, object]:
    """Compare the numerical causal state used to construct a shifted start."""
    exact_fields = (
        "storage_device_ids",
        "attempt_id",
        "iteration",
        "ordinal",
        "role",
        "outer_plan_id",
        "global_interval_start",
        "global_interval_stop",
    )
    return {
        "identity_exact": all(left.get(name) == right.get(name) for name in exact_fields),
        "initial_soc_mwh": _named_residuals(
            left.get("initial_soc_mwh"), right.get("initial_soc_mwh"), "initial SoC"
        ),
        "first_soc_mwh": _max_abs_and_normalized(
            left.get("first_soc_mwh"), right.get("first_soc_mwh")
        ),
        "first_b_mw": _max_abs_and_normalized(
            left.get("first_b_mw"), right.get("first_b_mw")
        ),
        "solution_values": _named_residuals(
            left.get("solution_values"), right.get("solution_values"), "solution values"
        ),
    }


def _x0_coordinate_residuals(
    left_x0: object, right_x0: object, layout: object
) -> Mapping[str, object]:
    left = np.asarray(left_x0, dtype=float)
    right = np.asarray(right_x0, dtype=float)
    if left.shape != right.shape or left.ndim != 1:
        raise ValueError("solver x0 shape mismatch")
    if not np.all(np.isfinite(left)) or not np.all(np.isfinite(right)):
        raise ValueError("solver x0 contains nonfinite values")
    records = _sequence(layout, "solver x0 layout")
    masks = {
        "original": np.zeros(left.size, dtype=bool),
        "auxiliary": np.zeros(left.size, dtype=bool),
    }
    by_layout_entry: dict[str, object] = {}
    for item in records:
        record = _mapping(item, "solver x0 layout record")
        name = record.get("name")
        if not isinstance(name, str) or not name or name in by_layout_entry:
            raise ValueError("solver x0 layout names must be unique nonempty strings")
        start = int(cast(int, record["start"]))
        stop = int(cast(int, record["stop"]))
        if not 0 <= start <= stop <= left.size:
            raise ValueError("solver x0 layout coordinate range is invalid")
        kind = "original" if bool(record.get("is_original_variable")) else "auxiliary"
        masks[kind][start:stop] = True
        by_layout_entry[name] = {
            "kind": kind,
            "shape": record.get("shape"),
            "start": start,
            "stop": stop,
            **_max_abs_and_normalized(left[start:stop], right[start:stop]),
        }
    if np.any(masks["original"] & masks["auxiliary"]) or not np.all(
        masks["original"] | masks["auxiliary"]
    ):
        raise ValueError("solver x0 layout does not partition all coordinates")
    result: dict[str, object] = {
        "all": _max_abs_and_normalized(left, right),
        "by_layout_entry": by_layout_entry,
    }
    for kind, mask in masks.items():
        result[kind] = _max_abs_and_normalized(left[mask], right[mask])
        cast(dict[str, float], result[kind])["coordinate_count"] = int(mask.sum())
    return result


def trajectory_residuals(
    left: Mapping[str, object], right: Mapping[str, object]
) -> Mapping[str, object]:
    """Report raw ID-aligned trajectory residuals without an acceptance gate."""
    if left["storage_device_ids"] != right["storage_device_ids"]:
        raise ValueError("trajectory storage identities differ")
    return {
        "executed_b_mw_max_abs": _max_abs(
            left["executed_b_mw"], right["executed_b_mw"]
        ),
        "realized_soc_mwh_max_abs": _max_abs(
            left["realized_soc_mwh"], right["realized_soc_mwh"]
        ),
        "attempt_labels_exact": left["attempts"] == right["attempts"],
    }


def _trajectory_prefix(
    trajectory: Mapping[str, object], intervals: int
) -> Mapping[str, object]:
    return {
        "storage_device_ids": trajectory["storage_device_ids"],
        "executed_b_mw": _sequence(
            trajectory["executed_b_mw"], "executed storage power"
        )[:intervals],
        "realized_soc_mwh": _sequence(
            trajectory["realized_soc_mwh"], "realized SoC"
        )[: intervals + 1],
        "attempts": _sequence(trajectory["attempts"], "attempts")[:intervals],
    }


def matched_rss_residuals(
    arm_series: Sequence[Mapping[str, object]],
    never_series: Sequence[Mapping[str, object]],
) -> Mapping[str, object]:
    """Compare current RSS at identical global intervals."""
    arm = {int(cast(int, item["iteration"])): item for item in arm_series}
    never = {int(cast(int, item["iteration"])): item for item in never_series}
    if set(arm) != set(never):
        raise ValueError("matched RSS interval sets differ")
    differences = [
        {
            "iteration": iteration,
            "arm_rss_mib": float(cast(float, arm[iteration]["rss_mib"])),
            "never_rss_mib": float(cast(float, never[iteration]["rss_mib"])),
            "difference_mib": float(cast(float, arm[iteration]["rss_mib"]))
            - float(cast(float, never[iteration]["rss_mib"])),
        }
        for iteration in sorted(arm)
    ]
    return {
        "matched_interval_count": len(differences),
        "max_abs_difference_mib": max(
            (abs(float(item["difference_mib"])) for item in differences),
            default=0.0,
        ),
        "by_interval": differences,
    }


def _never_interval_durations(
    series: Sequence[Mapping[str, object]],
) -> Mapping[int, float]:
    result: dict[int, float] = {}
    previous_by_invocation: dict[int, float] = {}
    for item in series:
        iteration = int(cast(int, item["iteration"]))
        invocation = int(cast(int, item["invocation"]))
        elapsed = float(cast(float, item["elapsed_seconds"]))
        previous = previous_by_invocation.get(invocation, 0.0)
        if elapsed < previous:
            raise ValueError("after-release elapsed time decreased within invocation")
        result[iteration] = elapsed - previous
        previous_by_invocation[invocation] = elapsed
    return result


def _restart_timing_comparison(
    invocations: Sequence[Mapping[str, object]],
    never_durations: Mapping[int, float],
) -> tuple[Mapping[str, object], ...]:
    result: list[Mapping[str, object]] = []
    for record in invocations:
        boundary = record.get("completed_before")
        observed = record.get("restart_to_first_checkpoint_seconds")
        if not isinstance(boundary, int) or boundary == 0 or observed is None:
            continue
        if boundary not in never_durations:
            raise ValueError("restart boundary lacks a matched never interval")
        baseline = never_durations[boundary]
        observed_seconds = float(cast(float, observed))
        result.append(
            {
                "restart_boundary": boundary,
                "restart_to_first_checkpoint_seconds": observed_seconds,
                "matched_never_interval_seconds": baseline,
                "estimated_incremental_restart_seconds": observed_seconds - baseline,
                "poll_seconds": record.get("poll_seconds"),
            }
        )
    return tuple(result)


def actual_start_residuals(
    left: Mapping[str, object], right: Mapping[str, object]
) -> Mapping[str, object]:
    """Report raw actual-start residuals under cross-process normalization."""
    left_assigned = _mapping(left.get("assigned_start"), "left assigned start")
    right_assigned = _mapping(right.get("assigned_start"), "right assigned start")
    if set(left_assigned) != set(right_assigned):
        raise ValueError("assigned-start variable groups differ")
    assigned_residuals = {
        name: _max_abs_and_normalized(left_assigned[name], right_assigned[name])
        for name in sorted(left_assigned)
    }
    left_layout = left.get("solver_x0_layout")
    right_layout = right.get("solver_x0_layout")
    layout_exact = left_layout == right_layout
    if not layout_exact:
        raise ValueError("solver x0 layouts differ")
    return {
        "assigned_start_by_group": assigned_residuals,
        "assigned_start_max_abs": max(
            (float(item["max_abs"]) for item in assigned_residuals.values()),
            default=0.0,
        ),
        "solver_x0": _x0_coordinate_residuals(
            left.get("solver_x0"), right.get("solver_x0"), left_layout
        ),
        "solver_x0_layout_exact": layout_exact,
        "solver_evidence_exact": (
            left.get("solver_evidence") == right.get("solver_evidence")
        ),
        "structural_signature_exact": (
            left.get("structural_signature") == right.get("structural_signature")
        ),
        "causal_source": causal_source_residuals(
            _mapping(left.get("causal_source"), "left causal source"),
            _mapping(right.get("causal_source"), "right causal source"),
        ),
    }


def _s2_actual_starts() -> Mapping[str, object]:
    reference = verify_tracked_reference()
    invariants = _mapping(reference.get("invariants"), "S2 invariants")
    result: dict[str, object] = {}
    for item in _sequence(reference.get("tier_b"), "S2 Tier B"):
        record = _mapping(item, "S2 Tier B record")
        evidence = _mapping(record.get("evidence"), "S2 Tier B evidence")
        solver_evidence = _mapping(
            evidence.get("solver_evidence"), "S2 solver evidence"
        )
        result[str(record["boundary"])] = {
            "assigned_start": evidence.get("assigned_start"),
            "solver_x0": evidence.get("solver_x0"),
            "solver_x0_layout": _canonical_layout(evidence.get("solver_x0_layout")),
            "solver_evidence": _canonical_solver_evidence(solver_evidence),
            "structural_signature": invariants.get("structural_signature"),
            "causal_source": evidence.get("causal_source"),
        }
    return result


def _s2_projection(prefix_intervals: int = 64) -> Mapping[str, object]:
    reference = verify_tracked_reference()
    all_tier_a = _sequence(reference["tier_a"], "S2 Tier A")
    if not 0 < prefix_intervals <= len(all_tier_a):
        raise ValueError("S2 comparison prefix lies outside the tracked reference")
    tier_a = all_tier_a[:prefix_intervals]
    return {
        "storage_device_ids": reference["storage_device_ids"],
        "executed_b_mw": [
            _mapping(item, "Tier A record")["executed_b_mw"] for item in tier_a
        ],
        "realized_soc_mwh": [reference["initial_soc_mwh"]]
        + [_mapping(item, "Tier A record")["realized_soc_mwh"] for item in tier_a],
        "attempts": [
            {
                "iteration": _mapping(item, "Tier A record")["iteration"],
                "attempt_id": _mapping(item, "Tier A record")["controlling_attempt_id"],
                "ordinal": _mapping(item, "Tier A record")["controlling_ordinal"],
                "transformation": _mapping(item, "Tier A record")["transformation"],
                "source_kind": _mapping(item, "Tier A record")["source_kind"],
                "source_attempt_id": _mapping(item, "Tier A record")[
                    "source_attempt_id"
                ],
            }
            for item in tier_a
        ],
    }


def _compact_arm(analysis: Mapping[str, object]) -> Mapping[str, object]:
    warm_start = _mapping(analysis.get("warm_start"), "warm-start analysis")
    compact_warm_start: dict[str, object] = {}
    for boundary, value in warm_start.items():
        evidence = _mapping(value, f"warm-start boundary {boundary}")
        compact_warm_start[boundary] = {
            "transformation": evidence.get("transformation"),
            "source_kind": evidence.get("source_kind"),
            "source_attempt_id": evidence.get("source_attempt_id"),
            "preceding_attempt_id": evidence.get("preceding_attempt_id"),
            "preceding_causal_attempt_id": evidence.get("preceding_causal_attempt_id"),
            "causal_source_matches_preceding": evidence.get(
                "causal_source_matches_preceding"
            ),
            "assigned_start_sha256": _value_sha256(evidence.get("assigned_start")),
            "solver_x0_sha256": _value_sha256(evidence.get("solver_x0")),
            "solver_x0_layout_sha256": _value_sha256(evidence.get("solver_x0_layout")),
            "layout_signature": evidence.get("layout_signature"),
            "structural_signature_sha256": _value_sha256(
                evidence.get("structural_signature")
            ),
            "causal_source_sha256": _value_sha256(evidence.get("causal_source")),
        }
    return {
        "arm": analysis.get("arm"),
        "complete": analysis.get("complete"),
        "completed_intervals": analysis.get("completed_intervals"),
        "final_checkpoint_sha256": analysis.get("final_checkpoint_sha256"),
        "outer_plan_sha256": analysis.get("outer_plan_sha256"),
        "classifications": analysis.get("classifications"),
        "planned_restart_boundaries": analysis.get("planned_restart_boundaries"),
        "invocation_record_sha256": analysis.get("invocation_record_sha256"),
        "safe_boundary_rss": analysis.get("safe_boundary_rss"),
        "after_release_series": analysis.get("after_release_series"),
        "invocations": analysis.get("invocations"),
        "cumulative_wall_time_seconds": sum(
            [
                float(
                    cast(
                        float,
                        _mapping(item, "invocation").get("wall_time_seconds", 0.0),
                    )
                )
                for item in _sequence(analysis.get("invocations"), "invocations")
            ],
            start=0.0,
        ),
        "restart_timing": analysis.get("restart_timing"),
        "warm_start": compact_warm_start,
    }


def analyze_comparison(root: Path = DEFAULT_RESULTS_ROOT) -> Mapping[str, object]:
    """Reconstruct every available arm and report observational residuals."""
    from experiments.case118_annual_hierarchy.run_recycle_comparison import (
        ARM_ORDER,
        comparison_source_fingerprint,
    )

    detailed_arms = {
        arm: analyze_arm(root / arm)
        for arm in ARM_ORDER
        if (root / arm / "trajectory/checkpoint.json").is_file()
    }
    if not detailed_arms:
        raise ValueError("comparison analysis found no checkpointed arm")
    execution_complete = bool(
        tuple(detailed_arms) == ARM_ORDER
        and all(analysis["complete"] is True for analysis in detailed_arms.values())
    )
    residuals: dict[str, object] = {}
    start_residuals: dict[str, object] = {}
    memory_residuals: dict[str, object] = {}
    s2_starts = _s2_actual_starts()
    for arm, analysis in detailed_arms.items():
        arm_starts = _mapping(analysis["warm_start"], f"{arm} warm starts")
        start_residuals[f"{arm}_vs_s2"] = {
            boundary: actual_start_residuals(
                _mapping(evidence, f"{arm} start {boundary}"),
                _mapping(s2_starts[boundary], f"S2 start {boundary}"),
            )
            for boundary, evidence in arm_starts.items()
            if boundary in s2_starts
        }
    if "never" in detailed_arms:
        never_analysis = detailed_arms["never"]
        never = _mapping(never_analysis["trajectory"], "never trajectory")
        never_starts = _mapping(never_analysis["warm_start"], "never warm starts")
        never_series = tuple(
            _mapping(item, "never after-release record")
            for item in _sequence(
                never_analysis["after_release_series"], "never after-release series"
            )
        )
        never_durations = _never_interval_durations(never_series)
        residuals["never_vs_s2"] = trajectory_residuals(
            never,
            _s2_projection(int(cast(int, never_analysis["completed_intervals"]))),
        )
        for arm, analysis in detailed_arms.items():
            if arm == "never":
                continue
            arm_intervals = int(cast(int, analysis["completed_intervals"]))
            if arm_intervals > int(cast(int, never_analysis["completed_intervals"])):
                raise ValueError("recycled arm extends beyond the never reference")
            arm_series = tuple(
                _mapping(item, f"{arm} after-release record")
                for item in _sequence(
                    analysis["after_release_series"], f"{arm} after-release series"
                )
            )
            memory_residuals[f"{arm}_vs_never"] = matched_rss_residuals(
                arm_series, never_series[:arm_intervals]
            )
            cast(dict[str, object], analysis)["restart_timing"] = (
                _restart_timing_comparison(
                    tuple(
                        _mapping(item, f"{arm} invocation")
                        for item in _sequence(analysis["invocations"], "invocations")
                    ),
                    never_durations,
                )
            )
            residuals[f"{arm}_vs_never"] = trajectory_residuals(
                _mapping(analysis["trajectory"], f"{arm} trajectory"),
                _trajectory_prefix(never, arm_intervals),
            )
            arm_starts = _mapping(analysis["warm_start"], f"{arm} warm starts")
            planned = _sequence(
                analysis["planned_restart_boundaries"],
                f"{arm} planned restart boundaries",
            )
            start_residuals[f"{arm}_vs_never"] = {
                str(boundary): actual_start_residuals(
                    _mapping(arm_starts[str(boundary)], f"{arm} start {boundary}"),
                    _mapping(never_starts[str(boundary)], f"never start {boundary}"),
                )
                for boundary in planned
                if str(boundary) in arm_starts and str(boundary) in never_starts
            }
    arms = {arm: _compact_arm(analysis) for arm, analysis in detailed_arms.items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "observational_study": True,
        "execution_complete": execution_complete,
        "automatic_advancement_gate": False,
        "comparison_source_fingerprint": comparison_source_fingerprint(),
        "model_source_fingerprint": HISTORICAL_SOURCE_FINGERPRINT,
        "outer_plan_sha256": EXPECTED_OUTER_SHA256,
        "arms": arms,
        "trajectory_residuals": residuals,
        "actual_start_residuals": start_residuals,
        "matched_rss_comparisons": memory_residuals,
    }


def promote_compact_result(
    root: Path = DEFAULT_RESULTS_ROOT,
    destination: Path = DEFAULT_COMPACT_PATH,
) -> Mapping[str, object]:
    """Write the independently reconstructed compact result exactly once."""
    result = analyze_comparison(root)
    atomic_immutable_json(destination, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-root", type=Path, default=DEFAULT_RESULTS_ROOT)
    parser.add_argument("--promote", action="store_true")
    parser.add_argument("--destination", type=Path, default=DEFAULT_COMPACT_PATH)
    args = parser.parse_args()
    result = (
        promote_compact_result(args.results_root, args.destination)
        if args.promote
        else analyze_comparison(args.results_root)
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "analyze_arm",
    "analyze_comparison",
    "load_resource_samples",
    "promote_compact_result",
    "trajectory_residuals",
]
