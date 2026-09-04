"""Independent reconstruction for S4b shard and supervision artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path
import subprocess
import sys
from typing import Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.run_s0 import ROOT, _software_versions
from experiments.case118_annual_hierarchy.run_s4b import (
    QUALIFICATION_RUNS,
    SOURCE_FILES,
    _outer,
)
from experiments.case118_annual_hierarchy.s4b_execution import (
    audit_shard,
    merge_shard_summaries,
    qualification_registry,
    qualification_shard_entry,
    verify_shard_artifacts,
)
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    canonical_json,
    object_sha256,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)


SCHEMA_VERSION = 1
ANALYSIS_SOURCE_FILES = tuple(ROOT / item for item in SOURCE_FILES) + (
    Path(__file__).resolve(),
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def analysis_source_fingerprint() -> str:
    """Hash every tracked source used by independent S4b reconstruction."""
    digest = hashlib.sha256()
    for path in sorted(set(ANALYSIS_SOURCE_FILES), key=lambda item: str(item)):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def analysis_context() -> Mapping[str, object]:
    """Identify the analyzer separately from historical execution provenance."""
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    status = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return {
        "git_commit": commit,
        "git_clean": not bool(status.strip()),
        "analysis_source_fingerprint": analysis_source_fingerprint(),
        "platform": platform.platform(),
        "architecture": platform.machine(),
        "python": sys.version,
        "software_versions": _software_versions(),
    }


def validate_supervision(value: object) -> Mapping[str, object]:
    """Independently reconstruct one tree-aware supervisor classification."""
    record = _mapping(value, "S4b supervision")
    if (
        record.get("schema_version") != SCHEMA_VERSION
        or record.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
    ):
        raise ValueError("S4b supervision identity mismatch")
    requested = record.get("requested_shards")
    run_label = record.get("run_label")
    if (
        not isinstance(requested, list)
        or not 1 <= len(requested) <= 2
        or len(set(requested)) != len(requested)
        or record.get("requested_concurrency") != len(requested)
        or run_label not in QUALIFICATION_RUNS
        or not set(requested).issubset(set(QUALIFICATION_RUNS[str(run_label)]))
    ):
        raise ValueError("S4b supervision request registry mismatch")
    samples = record.get("resource_samples")
    if not isinstance(samples, list):
        raise ValueError("S4b supervision resource samples are missing")
    reconstructed_worker_peaks = {str(item): 0.0 for item in requested}
    reconstructed_aggregate_peak = 0.0
    reconstructed_concurrency = 0
    roots = _mapping(record.get("worker_root_pids"), "worker root PIDs")
    if set(roots) != set(requested):
        raise ValueError("S4b worker root registry mismatch")
    pid_to_shard = {str(pid): shard_id for shard_id, pid in roots.items()}
    worker_results = _mapping(record.get("worker_results"), "worker results")
    for sample_value in samples:
        sample = _mapping(sample_value, "resource sample")
        per_worker = _mapping(sample.get("per_worker"), "per-worker sample")
        active = sample.get("active_shards")
        if (
            not isinstance(active, list)
            or set(active) != {pid_to_shard[pid] for pid in per_worker}
            or len(active) != len(per_worker)
        ):
            raise ValueError("S4b active-shard resource sample mismatch")
        reconstructed_concurrency = max(reconstructed_concurrency, len(active))
        for name in ("supervisor_current_rss_mib", "supervisor_cpu_seconds"):
            own_value = sample.get(name)
            if (
                isinstance(own_value, bool)
                or not isinstance(own_value, (int, float))
                or not np.isfinite(float(own_value))
                or float(own_value) < 0.0
            ):
                raise ValueError("S4b supervisor resource sample is invalid")
        identity_union: set[tuple[object, ...]] = set()
        summed_rss = 0.0
        summed_cpu = 0.0
        for pid, usage_value in per_worker.items():
            usage = _mapping(usage_value, "worker usage")
            identities = usage.get("process_identities")
            if not isinstance(identities, list) or not identities:
                raise ValueError("S4b worker sample lacks process identities")
            rss = float(cast(float, usage["rss_mib"]))
            cpu = float(cast(float, usage["cpu_seconds"]))
            if not np.isfinite(rss) or rss < 0.0 or not np.isfinite(cpu) or cpu < 0.0:
                raise ValueError("S4b worker RSS sample is invalid")
            if pid not in pid_to_shard:
                raise ValueError("S4b resource PID is not a registered worker root")
            shard_id = pid_to_shard[pid]
            reconstructed_worker_peaks[shard_id] = max(
                reconstructed_worker_peaks[shard_id], rss
            )
            tuples = {tuple(_item) for _item in identities}
            if identity_union & tuples:
                raise ValueError("S4b worker process trees overlap")
            identity_union.update(tuples)
            summed_rss += rss
            summed_cpu += cpu
        aggregate = float(cast(float, sample["aggregate_rss_mib"]))
        aggregate_cpu = float(cast(float, sample["aggregate_cpu_seconds"]))
        aggregate_identities = {
            tuple(_item)
            for _item in cast(
                list[list[object]], sample.get("aggregate_process_identities")
            )
        }
        if (
            not np.isfinite(aggregate)
            or aggregate < 0.0
            or not np.isfinite(aggregate_cpu)
            or aggregate_cpu < 0.0
            or aggregate_identities != identity_union
            or aggregate != summed_rss
            or aggregate_cpu != summed_cpu
        ):
            raise ValueError("S4b aggregate RSS sample is invalid")
        reconstructed_aggregate_peak = max(reconstructed_aggregate_peak, aggregate)
    triggers = record.get("resource_triggers")
    returncodes = _mapping(record.get("returncodes"), "worker return codes")
    if not isinstance(triggers, list) or set(returncodes) != set(requested):
        raise ValueError("S4b supervision outcome registry mismatch")
    for trigger_value in triggers:
        trigger = _mapping(trigger_value, "resource trigger")
        kind = trigger.get("kind")
        rss = float(cast(float, trigger.get("rss_mib")))
        if kind == "per_worker_rss_limit":
            if trigger.get("shard_id") not in requested or rss <= 16_384.0:
                raise ValueError("invalid per-worker resource trigger")
        elif kind == "aggregate_rss_limit":
            if rss <= 24_576.0:
                raise ValueError("invalid aggregate resource trigger")
        else:
            raise ValueError("unsupported S4b resource trigger")
    artifact_error = record.get("artifact_error")
    exception_kind = record.get("supervisor_exception_kind")
    if exception_kind not in {None, "interruption", "failure"}:
        raise ValueError("S4b supervisor exception kind is invalid")
    if (exception_kind is None) != (record.get("supervisor_exception") is None):
        raise ValueError("S4b supervisor exception evidence is inconsistent")
    expected_classification = (
        "resource_limit"
        if triggers
        else "supervisor_interrupted"
        if exception_kind == "interruption"
        else "supervisor_failure"
        if exception_kind == "failure"
        else "artifact_failure"
        if artifact_error is not None
        else "worker_failure"
        if any(value != 0 for value in returncodes.values())
        else "accepted"
    )
    if record.get("classification") != expected_classification:
        raise ValueError("S4b supervision classification does not reconstruct")
    if record.get("peak_worker_rss_mib") != reconstructed_worker_peaks:
        raise ValueError("S4b per-worker RSS peaks do not reconstruct")
    if record.get("peak_aggregate_rss_mib") != reconstructed_aggregate_peak:
        raise ValueError("S4b aggregate RSS peak does not reconstruct")
    if record.get("maximum_observed_concurrency") != reconstructed_concurrency:
        raise ValueError("S4b observed concurrency does not reconstruct")
    authority = _mapping(record.get("authority"), "execution authority")
    context = _mapping(record.get("execution_context"), "execution context")
    if (
        authority.get("execution_commit") != context.get("git_commit")
        or authority.get("source_fingerprint") != context.get("source_fingerprint")
        or context.get("git_clean") is not True
    ):
        raise ValueError("S4b supervision execution provenance mismatch")
    if expected_classification == "accepted" and set(worker_results) != set(requested):
        raise ValueError("accepted S4b supervision lacks every worker result")
    if expected_classification == "accepted" and not samples:
        raise ValueError("accepted S4b supervision lacks resource samples")
    return record


def _scientific_attempt_projection(archive: Mapping[str, object]) -> object:
    """Remove only process-local/timing evidence before trajectory comparison."""
    value = json.loads(json.dumps(archive))
    for attempt in cast(list[dict[str, object]], value["attempts"]):
        evidence = attempt.get("solver_evidence")
        if isinstance(evidence, dict):
            evidence.pop("object_ids_before", None)
            evidence.pop("object_ids_after", None)
        audit = attempt.get("audit")
        if isinstance(audit, dict):
            for name in (
                "wall_time_seconds",
                "solver_setup_time_seconds",
                "solver_solve_time_seconds",
                "solver_num_iters",
            ):
                audit.pop(name, None)
    return value


def _maximum_numeric_difference(left: object, right: object) -> float:
    if isinstance(left, Mapping) and isinstance(right, Mapping):
        if set(left) != set(right):
            raise ValueError("S4b process-equivalence schemas differ")
        return max(
            (_maximum_numeric_difference(left[key], right[key]) for key in left),
            default=0.0,
        )
    if (
        isinstance(left, Sequence)
        and not isinstance(left, (str, bytes))
        and isinstance(right, Sequence)
        and not isinstance(right, (str, bytes))
    ):
        if len(left) != len(right):
            raise ValueError("S4b process-equivalence array shapes differ")
        return max(
            (
                _maximum_numeric_difference(a, b)
                for a, b in zip(left, right, strict=True)
            ),
            default=0.0,
        )
    if isinstance(left, (int, float)) and not isinstance(left, bool):
        if not isinstance(right, (int, float)) or isinstance(right, bool):
            raise ValueError("S4b process-equivalence scalar types differ")
        return abs(float(left) - float(right))
    if left != right:
        raise ValueError("S4b process-equivalence identities differ")
    return 0.0


def analyze_s4b(
    shard_directories: Sequence[Path],
    *,
    supervision_paths: Sequence[Path] = (),
    run_result_paths: Sequence[Path] = (),
) -> Mapping[str, object]:
    """Reconstruct shards, their deterministic merge, and supervision evidence."""
    outer = _outer()
    qualification = qualification_registry(outer)
    summaries: list[Mapping[str, object]] = []
    artifacts: dict[str, object] = {}
    workers: dict[str, Mapping[str, object]] = {}
    trajectories: dict[str, tuple[object, ...]] = {}
    for directory in shard_directories:
        worker_path = directory / "shard-result.json"
        worker = _mapping(json.loads(worker_path.read_text()), "worker result")
        child_cpu = worker.get("completed_child_cpu_seconds")
        if (
            isinstance(child_cpu, bool)
            or not isinstance(child_cpu, (int, float))
            or not np.isfinite(float(child_cpu))
            or float(child_cpu) < 0.0
        ):
            raise ValueError("S4b worker cumulative child CPU is invalid")
        shard_id = str(worker["shard_id"])
        _, shard = qualification_shard_entry(shard_id, outer)
        reconstructed = audit_shard(directory, shard=shard, outer=outer)
        for name, expected in reconstructed.items():
            if worker.get(name) != expected:
                raise ValueError(f"S4b retained worker summary mismatch: {name}")
        execution_mode = str(worker.get("execution_mode"))
        if execution_mode not in QUALIFICATION_RUNS:
            raise ValueError("S4b worker lacks a frozen qualification mode")
        summary = {**reconstructed, "execution_mode": execution_mode}
        key = f"{execution_mode}:{shard_id}"
        if key in workers:
            raise ValueError("duplicate S4b qualification worker result")
        summaries.append(summary)
        workers[key] = worker
        _, archives = verify_shard_artifacts(directory, shard=shard, outer=outer)
        trajectories[key] = tuple(
            _scientific_attempt_projection(item) for item in archives
        )
        artifacts[key] = {
            "result_path": str(worker_path),
            "result_sha256": sha256_path(worker_path),
            "checkpoint_sha256": reconstructed["checkpoint_sha256"],
            "window_chain_sha256": reconstructed["window_chain_sha256"],
            "completed_child_cpu_seconds": child_cpu,
        }
    supervision = [
        validate_supervision(json.loads(path.read_text())) for path in supervision_paths
    ]
    run_results = [
        _mapping(json.loads(path.read_text()), "one-process root result")
        for path in run_result_paths
    ]
    if len(run_results) != 1:
        raise ValueError("S4b analysis requires one one-process root result")
    one_process_root = run_results[0]
    one_process_workers = cast(
        Sequence[Mapping[str, object]], one_process_root.get("worker_results")
    )
    one_process_context = _mapping(
        one_process_root.get("execution_context"), "one-process execution context"
    )
    one_process_authority = _mapping(
        one_process_root.get("authority"), "one-process authority"
    )
    if (
        one_process_root.get("classification") != "accepted"
        or one_process_root.get("run_label") != "partitioned_one_process"
        or len(one_process_workers) != 2
        or any(
            workers.get(f"partitioned_one_process:{item.get('shard_id')}") != item
            for item in one_process_workers
        )
        or one_process_context.get("git_clean") is not True
        or one_process_authority.get("execution_commit")
        != one_process_context.get("git_commit")
        or one_process_authority.get("source_fingerprint")
        != one_process_context.get("source_fingerprint")
        or any(
            item.get("worker_pid") != one_process_root.get("worker_pid")
            for item in one_process_workers
        )
    ):
        raise ValueError("S4b one-process root does not bind its worker results")
    for record in supervision:
        mode = str(record["run_label"])
        roots = _mapping(record["worker_root_pids"], "supervised worker roots")
        for shard_id, embedded in _mapping(
            record["worker_results"], "supervised worker results"
        ).items():
            if workers.get(f"{mode}:{shard_id}") != embedded or _mapping(
                embedded, "embedded worker"
            ).get("worker_pid") != roots.get(shard_id):
                raise ValueError("S4b supervision worker result is not root-bound")
    expected_keys = {
        "ordinary:s4b-qualification-ordinary",
        *{
            f"{mode}:{shard_id}"
            for mode in (
                "partitioned_one_process",
                "partitioned_fresh_sequential",
                "partitioned_fresh_concurrent",
            )
            for shard_id in QUALIFICATION_RUNS[mode]
        },
    }
    complete = set(workers) == expected_keys and all(
        item["execution_complete"] is True for item in summaries
    )
    accepted_supervision_shapes = sorted(
        (str(item["run_label"]), tuple(cast(Sequence[str], item["requested_shards"])))
        for item in supervision
        if item["classification"] == "accepted"
    )
    expected_supervision_shapes = sorted(
        [
            ("ordinary", QUALIFICATION_RUNS["ordinary"]),
            (
                "partitioned_fresh_sequential",
                ("s4b-qualification-partition-a",),
            ),
            (
                "partitioned_fresh_sequential",
                ("s4b-qualification-partition-b",),
            ),
            (
                "partitioned_fresh_concurrent",
                QUALIFICATION_RUNS["partitioned_fresh_concurrent"],
            ),
        ]
    )
    run_matrix_complete = accepted_supervision_shapes == expected_supervision_shapes
    partition_registry = [
        _mapping(item, "qualification shard")
        for item in cast(Sequence[object], qualification["shards"])
        if "partition-" in str(_mapping(item, "qualification shard")["shard_id"])
    ]
    merged_by_mode = (
        {
            mode: merge_shard_summaries(
                [
                    {
                        key: value
                        for key, value in item.items()
                        if key != "execution_mode"
                    }
                    for item in summaries
                    if item["execution_mode"] == mode
                ],
                registry_shards=partition_registry,
            )
            for mode in (
                "partitioned_one_process",
                "partitioned_fresh_sequential",
                "partitioned_fresh_concurrent",
            )
        }
        if complete
        else {}
    )
    process_residuals = (
        {
            f"{mode}:{suffix}": _maximum_numeric_difference(
                trajectories[f"partitioned_one_process:{suffix}"],
                trajectories[f"{mode}:{suffix}"],
            )
            for mode in (
                "partitioned_fresh_sequential",
                "partitioned_fresh_concurrent",
            )
            for suffix in (
                "s4b-qualification-partition-a",
                "s4b-qualification-partition-b",
            )
        }
        if complete
        else {}
    )
    process_equivalent = bool(
        complete
        and all(value <= 1e-5 for value in process_residuals.values())
        and all(
            merged_by_mode[mode][name]
            == merged_by_mode["partitioned_one_process"][name]
            for mode in (
                "partitioned_fresh_sequential",
                "partitioned_fresh_concurrent",
            )
            for name in (
                "completed_intervals",
                "initial_state",
                "terminal_state",
                "all_independent_audits_agree",
            )
        )
    )
    if complete:
        ordinary_windows = cast(
            tuple[Mapping[str, object], ...],
            trajectories["ordinary:s4b-qualification-ordinary"],
        )
        partitioned_windows = cast(
            tuple[Mapping[str, object], ...],
            trajectories["partitioned_one_process:s4b-qualification-partition-a"]
            + trajectories["partitioned_one_process:s4b-qualification-partition-b"],
        )
        boundary_effect: Mapping[str, object] | None = {
            "maximum_first_action_difference_mw": max(
                _maximum_numeric_difference(
                    _mapping(left["executed_interval"], "ordinary action")["b_mw"],
                    _mapping(right["executed_interval"], "partitioned action")["b_mw"],
                )
                for left, right in zip(
                    ordinary_windows, partitioned_windows, strict=True
                )
            ),
            "maximum_realized_soc_difference_mwh": max(
                _maximum_numeric_difference(
                    left["post_step_soc_mwh"], right["post_step_soc_mwh"]
                )
                for left, right in zip(
                    ordinary_windows, partitioned_windows, strict=True
                )
            ),
            "ordinary_window_stops": [
                item["interval_stop"] for item in ordinary_windows
            ],
            "partitioned_window_stops": [
                item["interval_stop"] for item in partitioned_windows
            ],
            "window_structures_differ": any(
                left["interval_stop"] != right["interval_stop"]
                for left, right in zip(
                    ordinary_windows, partitioned_windows, strict=True
                )
            ),
            "interpretation": "descriptive_boundary_effect_not_equivalence_gate",
        }
    else:
        boundary_effect = None
    result = {
        "schema_version": SCHEMA_VERSION,
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "classification": "accepted" if complete else "partial",
        "execution_complete": complete,
        "accepted_for_s5": bool(
            complete
            and run_matrix_complete
            and process_equivalent
            and all(
                item["classification"] == "accepted"
                and item["all_independent_audits_agree"] is True
                for item in summaries
            )
            and all(
                item["all_independent_audits_agree"] is True
                for item in merged_by_mode.values()
            )
            and any(
                record["classification"] == "accepted"
                and record["run_label"] == "partitioned_fresh_concurrent"
                and record["requested_concurrency"] == 2
                and record["maximum_observed_concurrency"] == 2
                for record in supervision
            )
        ),
        "shard_artifacts": artifacts,
        "shard_summaries": summaries,
        "supervision_sha256": [object_sha256(record) for record in supervision],
        "merged_by_mode": merged_by_mode,
        "process_equivalent": process_equivalent,
        "run_evidence_matrix_complete": run_matrix_complete,
        "process_equivalence_maximum_absolute_residuals": process_residuals,
        "boundary_effect_characterization": boundary_effect,
        "analysis_context": analysis_context(),
    }
    return {**result, "analysis_sha256": object_sha256(result)}


def promote_completed(path: Path, result: Mapping[str, object]) -> None:
    """Promote only a complete independently accepted S4b qualification."""
    base = {key: value for key, value in result.items() if key != "analysis_sha256"}
    if (
        result.get("analysis_sha256") != object_sha256(base)
        or result.get("execution_complete") is not True
        or result.get("accepted_for_s5") is not True
        or result.get("classification") != "accepted"
    ):
        raise ValueError("partial or unaccepted S4b analysis cannot be promoted")
    atomic_immutable_json(path, result)
    if path.read_bytes() != canonical_json(result):
        raise RuntimeError("promoted S4b result is not canonical")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directories", type=Path, nargs="+")
    parser.add_argument("--supervision", type=Path, nargs="*", default=())
    parser.add_argument("--run-result", type=Path, nargs="*", default=())
    parser.add_argument("--promote", type=Path)
    args = parser.parse_args()
    result = analyze_s4b(
        args.directories,
        supervision_paths=args.supervision,
        run_result_paths=args.run_result,
    )
    if args.promote is not None:
        promote_completed(args.promote, result)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
