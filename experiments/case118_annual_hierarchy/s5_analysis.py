"""Independent reconstruction and promotion for Case118 S5."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from typing import Mapping, Sequence, cast

import numpy as np

from experiments.case118_annual_hierarchy.run_s0 import ROOT, _software_versions
from experiments.case118_annual_hierarchy.run_s4b import _outer
from experiments.case118_annual_hierarchy.run_s5 import DEFAULT_OUTPUT_ROOT
from experiments.case118_annual_hierarchy.s4b_execution import (
    audit_shard,
    merge_shard_summaries,
    shard_entry,
    verify_shard_artifacts,
)
from experiments.case118_annual_hierarchy.s4b_manifest import (
    EXPECTED_MANIFEST_SHA256,
    canonical_json,
    object_sha256,
)
from experiments.case118_annual_hierarchy.s5_execution import (
    AGGREGATE_RSS_LIMIT_MIB,
    ANNUAL_SHARD_IDS,
    ANNUAL_WAVES,
    DEFAULT_NUMERICAL_AUTHORITY_PATH,
    PER_WORKER_RSS_LIMIT_MIB,
    SCHEMA_VERSION,
    SOURCE_FILES,
    annual_registry,
    load_numerical_authority,
    wave_index_for_request,
)
from experiments.case118_annual_hierarchy.streaming_schema import (
    atomic_immutable_json,
    sha256_path,
)
from experiments.case118_annual_hierarchy.s5_source_transition import (
    RECORD_NAME,
    historical_provenance_matches,
    load_transition,
    transition_context_authority_pairs,
    worker_source_matches,
)


ANALYSIS_SOURCE_FILES = tuple(ROOT / item for item in SOURCE_FILES) + (
    Path(__file__).resolve(),
)


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def analysis_source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(set(ANALYSIS_SOURCE_FILES), key=str):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def analysis_context() -> Mapping[str, object]:
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
        "software_versions": dict(_software_versions()),
    }


def _annual_registry_sha256() -> str:
    return str(annual_registry()["registry_sha256"])


def validate_supervision(value: object) -> Mapping[str, object]:
    """Independently reconstruct one annual wave supervision record."""
    record = _mapping(value, "S5 supervision")
    if record.get("schema_version") == 2:
        from experiments.case118_annual_hierarchy.s5_speculative_runtime import (
            validate_wave,
        )

        return validate_wave(record)
    requested = record.get("requested_shards")
    if (
        record.get("schema_version") != SCHEMA_VERSION
        or record.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
        or not isinstance(requested, list)
    ):
        raise ValueError("S5 supervision identity is invalid")
    wave_index = wave_index_for_request(cast(Sequence[str], requested))
    if (
        record.get("wave_index") != wave_index
        or record.get("frozen_wave") != list(ANNUAL_WAVES[wave_index])
        or record.get("requested_concurrency") != len(requested)
    ):
        raise ValueError("S5 supervision wave registry mismatch")
    roots = _mapping(record.get("worker_root_pids"), "S5 worker roots")
    returncodes = _mapping(record.get("returncodes"), "S5 return codes")
    results = _mapping(record.get("worker_results"), "S5 worker results")
    logs = _mapping(record.get("worker_logs"), "S5 worker logs")
    if (
        not set(roots) <= set(requested)
        or not set(returncodes) <= set(requested)
        or not set(results) <= set(requested)
        or not set(logs) <= set(requested)
        or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in returncodes.values()
        )
        or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in roots.values()
        )
    ):
        raise ValueError("S5 supervision process registry mismatch")
    samples = record.get("resource_samples")
    if not isinstance(samples, list):
        raise ValueError("S5 supervision resource samples are absent")
    peaks = {str(item): 0.0 for item in requested}
    aggregate_peak = 0.0
    concurrency = 0
    previous_elapsed = -1.0
    required_triggers: list[Mapping[str, object]] = []
    pid_to_shard = {str(pid): str(shard_id) for shard_id, pid in roots.items()}
    for raw in samples:
        if required_triggers:
            raise ValueError("S5 sampling continued after a resource stop")
        sample = _mapping(raw, "S5 resource sample")
        elapsed = float(cast(float, sample.get("elapsed_seconds")))
        per_worker = _mapping(sample.get("per_worker"), "S5 per-worker sample")
        active = sample.get("active_shards")
        if (
            not isinstance(active, list)
            or set(active) != {pid_to_shard[pid] for pid in per_worker}
            or not np.isfinite(elapsed)
            or elapsed < previous_elapsed
        ):
            raise ValueError("S5 active process registry mismatch")
        previous_elapsed = elapsed
        concurrency = max(concurrency, len(active))
        identities: set[tuple[object, ...]] = set()
        rss_total = 0.0
        cpu_total = 0.0
        for pid, raw_usage in per_worker.items():
            usage = _mapping(raw_usage, "S5 worker usage")
            if pid not in pid_to_shard:
                raise ValueError("S5 sampled PID is not a worker root")
            member_ids = {
                tuple(item)
                for item in cast(
                    Sequence[Sequence[object]], usage["process_identities"]
                )
            }
            if not member_ids or identities & member_ids:
                raise ValueError("S5 process-tree identities overlap or are empty")
            identities.update(member_ids)
            rss = float(cast(float, usage["rss_mib"]))
            cpu = float(cast(float, usage["cpu_seconds"]))
            if not np.isfinite(rss) or rss < 0 or not np.isfinite(cpu) or cpu < 0:
                raise ValueError("S5 process-tree resource value is invalid")
            peaks[pid_to_shard[pid]] = max(peaks[pid_to_shard[pid]], rss)
            rss_total += rss
            cpu_total += cpu
        aggregate_ids = {
            tuple(item)
            for item in cast(
                Sequence[Sequence[object]], sample["aggregate_process_identities"]
            )
        }
        aggregate_rss = float(cast(float, sample["aggregate_rss_mib"]))
        aggregate_cpu = float(cast(float, sample["aggregate_cpu_seconds"]))
        if (
            aggregate_ids != identities
            or aggregate_rss != rss_total
            or not np.isclose(aggregate_cpu, cpu_total, rtol=0.0, atol=1e-9)
        ):
            raise ValueError("S5 aggregate resource sample does not reconstruct")
        for name in ("supervisor_current_rss_mib", "supervisor_cpu_seconds"):
            value_at_sample = float(cast(float, sample[name]))
            if not np.isfinite(value_at_sample) or value_at_sample < 0:
                raise ValueError("S5 supervisor resource sample is invalid")
        aggregate_peak = max(aggregate_peak, aggregate_rss)
        for shard_id in requested:
            pid = str(roots.get(shard_id))
            if pid in per_worker:
                usage = _mapping(per_worker[pid], "S5 worker usage")
                rss = float(cast(float, usage["rss_mib"]))
                if rss > PER_WORKER_RSS_LIMIT_MIB:
                    required_triggers.append(
                        {
                            "kind": "per_worker_rss_limit",
                            "shard_id": shard_id,
                            "rss_mib": rss,
                        }
                    )
        if aggregate_rss > AGGREGATE_RSS_LIMIT_MIB:
            required_triggers.append(
                {"kind": "aggregate_rss_limit", "rss_mib": aggregate_rss}
            )
    triggers = record.get("resource_triggers")
    if triggers != required_triggers:
        raise ValueError(
            "S5 resource triggers do not reconstruct from retained samples"
        )
    exception_kind = record.get("supervisor_exception_kind")
    if exception_kind not in {None, "interruption", "failure", "launch_failure"}:
        raise ValueError("S5 supervisor exception kind is invalid")
    if (exception_kind is None) != (record.get("supervisor_exception") is None):
        raise ValueError("S5 supervisor exception evidence is inconsistent")
    expected = (
        "resource_limit"
        if triggers
        else "supervisor_interrupted"
        if exception_kind == "interruption"
        else "supervisor_failure"
        if exception_kind == "failure"
        else "worker_launch_failure"
        if exception_kind == "launch_failure"
        else "artifact_failure"
        if record.get("artifact_error") is not None
        else "worker_process_failure"
        if set(returncodes) != set(requested)
        or any(value != 0 for value in returncodes.values())
        else "accepted"
    )
    if record.get("classification") != expected:
        raise ValueError("S5 supervision classification does not reconstruct")
    if record.get("peak_worker_rss_mib") != peaks:
        raise ValueError("S5 worker RSS peaks do not reconstruct")
    if record.get("peak_aggregate_rss_mib") != aggregate_peak:
        raise ValueError("S5 aggregate RSS peak does not reconstruct")
    if record.get("maximum_observed_concurrency") != concurrency:
        raise ValueError("S5 concurrency does not reconstruct")
    authority = _mapping(record.get("authority"), "S5 authority")
    context = _mapping(record.get("execution_context"), "S5 execution context")
    if (
        authority.get("execution_commit") != context.get("git_commit")
        or authority.get("source_fingerprint") != context.get("source_fingerprint")
        or authority.get("annual_registry_sha256")
        != context.get("annual_registry_sha256")
        or context.get("git_clean") is not True
    ):
        raise ValueError("S5 supervision provenance does not match authority")
    if expected == "accepted" and (
        set(results) != set(requested) or set(logs) != set(requested) or not samples
    ):
        raise ValueError("accepted S5 supervision lacks required evidence")
    elapsed_critical = float(cast(float, record.get("elapsed_critical_path_seconds")))
    if not np.isfinite(elapsed_critical) or elapsed_critical < 0:
        raise ValueError("S5 supervision elapsed time is invalid")
    for shard_id, raw in results.items():
        worker = _mapping(raw, "S5 embedded worker")
        if (
            worker.get("shard_id") != shard_id
            or returncodes.get(shard_id) != 0
            or shard_id not in logs
            or worker.get("classification") != "accepted"
            or worker.get("execution_complete") is not True
            or worker.get("all_independent_audits_agree") is not True
            or worker.get("execution_context") != context
            or worker.get("execution_mode") != "annual"
        ):
            raise ValueError("S5 embedded worker provenance is invalid")
    return record


def _validate_continuations(
    output_root: Path,
    progress: Mapping[str, object],
    context: Mapping[str, object],
    authority: Mapping[str, object],
) -> list[Mapping[str, object]]:
    retained: list[Mapping[str, object]] = []
    transition = load_transition(output_root)
    allowed_pairs = transition_context_authority_pairs(transition) or (
        (context, authority),
    )
    registry = cast(Sequence[Mapping[str, object]], progress["reviewed_continuations"])
    actual_paths = sorted(output_root.glob("reviewed-continuation-*.json"))
    if [path.name for path in actual_paths] != [str(raw["path"]) for raw in registry]:
        raise ValueError("S5 reviewed-continuation registry is incomplete")
    for index, (raw, path) in enumerate(zip(registry, actual_paths, strict=True)):
        if path.parent != output_root or sha256_path(path) != raw.get("sha256"):
            raise ValueError("S5 reviewed-continuation artifact identity mismatch")
        value = _mapping(json.loads(path.read_text()), "S5 reviewed continuation")
        value_context = _mapping(
            value.get("execution_context"), "continuation execution context"
        )
        value_authority = _mapping(value.get("authority"), "continuation authority")
        if (
            value.get("classification") != "explicit_reviewed_continuation"
            or not any(
                value_context == pair_context and value_authority == pair_authority
                for pair_context, pair_authority in allowed_pairs
            )
            or not isinstance(value.get("next_wave"), int)
            or not isinstance(value.get("reviewed_source_sha256"), str)
        ):
            raise ValueError("S5 reviewed continuation payload is invalid")
        source = _mapping(value.get("reviewed_source"), "S5 reviewed source")
        if (
            object_sha256(source) != value["reviewed_source_sha256"]
            or not historical_provenance_matches(
                source, transition, context, authority, output_root=output_root
            )
            or source.get("next_wave") != value.get("next_wave")
            or source.get("reviewed_continuations") != list(registry[:index])
        ):
            raise ValueError("S5 reviewed continuation source does not reconstruct")
        for field in ("prior_supervision_sha256", "prior_root_outcome_sha256"):
            identity = value.get(field)
            if identity is not None and (
                not isinstance(identity, str) or len(identity) != 64
            ):
                raise ValueError("S5 reviewed continuation source identity is invalid")
        retained.append(value)
    return retained


def _validate_root_outcomes(
    output_root: Path,
    progress: Mapping[str, object],
    context: Mapping[str, object],
    authority: Mapping[str, object],
) -> list[Mapping[str, object]]:
    """Verify the immutable chain of retained root-level outcomes."""
    registry = cast(Sequence[Mapping[str, object]], progress.get("root_outcomes", ()))
    actual_paths = sorted(output_root.glob("root-outcome-*.json"))
    if [path.name for path in actual_paths] != [str(raw["path"]) for raw in registry]:
        raise ValueError("S5 root-outcome registry is incomplete")
    retained: list[Mapping[str, object]] = []
    transition = load_transition(output_root)
    prior_registry: list[Mapping[str, object]] = []
    for raw, path in zip(registry, actual_paths, strict=True):
        if path.parent != output_root or sha256_path(path) != raw.get("sha256"):
            raise ValueError("S5 root-outcome artifact identity mismatch")
        value = _mapping(json.loads(path.read_text()), "S5 root outcome")
        if (
            value.get("schema_version") != SCHEMA_VERSION
            or value.get("manifest_sha256") != EXPECTED_MANIFEST_SHA256
            or not historical_provenance_matches(
                value, transition, context, authority, output_root=output_root
            )
            or value.get("classification")
            not in {"partial", "supervisor_interrupted", "driver_failure"}
            or not isinstance(value.get("next_wave"), int)
            or value.get("root_outcomes") != prior_registry
            or raw.get("classification") != value.get("classification")
            or raw.get("next_wave") != value.get("next_wave")
        ):
            raise ValueError("S5 root-outcome payload is invalid")
        retained.append(value)
        prior_registry.append(dict(raw))
    return retained


def analyze_s5(
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    *,
    authority_path: Path = DEFAULT_NUMERICAL_AUTHORITY_PATH,
) -> Mapping[str, object]:
    """Independently reconstruct an S5 execution without modifying its artifacts.

    ``output_root`` contains the retained root, wave, and shard evidence;
    ``authority_path`` identifies the numerical authority that must match the
    recorded execution commit and source fingerprint. Validate that historical
    provenance, reconstruct shard audits and the deterministic annual merge,
    and check the supervision and reviewed-continuation history.

    Return a hash-bound analysis with scientific, lifecycle, and resource
    summaries. A consistent partial execution remains nonaccepted; complete
    shard coverage alone is insufficient without finalized root artifacts and
    verified lifecycle coverage. Inconsistent evidence raises ``ValueError``;
    missing required files and malformed JSON also propagate their read errors.

    Current analyzer provenance is recorded separately from execution provenance.
    This function neither resumes execution nor promotes results; publication is
    handled by ``promote_completed`` after repeating this reconstruction.
    """
    # Bind authority to the historical run, not the current analyzer checkout.
    context_path = output_root / "run-context.json"
    progress_path = output_root / "progress.json"
    initial_context = _mapping(json.loads(context_path.read_text()), "S5 run context")
    transition = load_transition(output_root)
    if transition is None:
        context = initial_context
    else:
        transition_contract = _mapping(
            transition.get("contract"), "source transition contract"
        )
        transition_execution = _mapping(
            transition_contract.get("continuation_execution"),
            "source transition execution",
        )
        context = _mapping(transition_execution.get("context"), "successor context")
    progress = _mapping(json.loads(progress_path.read_text()), "S5 progress")
    authority = load_numerical_authority(
        authority_path,
        expected_execution_commit=str(context["git_commit"]),
        expected_source_fingerprint=str(context["source_fingerprint"]),
    )
    execution_pairs = transition_context_authority_pairs(transition) or (
        (initial_context, authority),
    )
    if transition is not None:
        if (
            authority != transition["new_authority"]
            or initial_context != execution_pairs[-1][0]
        ):
            raise ValueError("S5 analysis transition authority/context mismatch")
    elif authority.get("source_version_contract_sha256") is not None:
        raise ValueError("S5 analysis lacks its source-version transition")
    if (
        not historical_provenance_matches(
            progress, transition, context, authority, output_root=output_root
        )
        or context.get("annual_registry_sha256") != annual_registry()["registry_sha256"]
        or context.get("git_clean") is not True
    ):
        raise ValueError("S5 root progress provenance mismatch")
    # Reconstruct wave outcomes and bind their contexts and worker logs to root.
    supervision_paths = sorted(output_root.glob("supervision-wave-*.json"))
    supervision = [
        validate_supervision(json.loads(path.read_text())) for path in supervision_paths
    ]
    for record in supervision:
        if record.get("schema_version") == 2:
            from experiments.case118_annual_hierarchy.s5_speculative_runtime import (
                validate_wave,
            )

            validate_wave(record, output_root)
        if not historical_provenance_matches(
            record, transition, context, authority, output_root=output_root
        ):
            raise ValueError("S5 wave authority or execution context differs from root")
        for raw in _mapping(record["worker_logs"], "S5 worker logs").values():
            log = _mapping(raw, "S5 worker log")
            path = output_root / str(log["path"])
            if path.parent != output_root or sha256_path(path) != log.get("sha256"):
                raise ValueError("S5 worker log identity mismatch")
    registered = cast(Sequence[Mapping[str, object]], progress["supervision_records"])
    actual_registry = [
        {
            "path": path.name,
            "sha256": sha256_path(path),
            "wave_index": item["wave_index"],
            "classification": item["classification"],
        }
        for path, item in zip(supervision_paths, supervision, strict=True)
    ]
    # Partial progress may lag a durably published wave, but may not invent one.
    if progress.get("classification") == "accepted":
        if list(registered) != actual_registry:
            raise ValueError("S5 root supervision registry mismatch")
    elif actual_registry[: len(registered)] != list(registered):
        raise ValueError("S5 partial root supervision registry is not a prefix")
    # Continuations must cite the actual terminal evidence of their reviewed prefix.
    root_outcomes = _validate_root_outcomes(output_root, progress, context, authority)
    continuations = _validate_continuations(output_root, progress, context, authority)
    supervision_hashes = {sha256_path(path) for path in supervision_paths}
    root_outcome_hashes = {
        str(item["sha256"])
        for item in cast(
            Sequence[Mapping[str, object]], progress.get("root_outcomes", ())
        )
    }
    for continuation in continuations:
        source = _mapping(continuation["reviewed_source"], "S5 reviewed source")
        source_supervision = cast(
            Sequence[Mapping[str, object]], source["supervision_records"]
        )
        source_outcomes = cast(
            Sequence[Mapping[str, object]], source.get("root_outcomes", ())
        )
        progress_outcomes = cast(
            Sequence[Mapping[str, object]], progress.get("root_outcomes", ())
        )
        if actual_registry[: len(source_supervision)] != list(
            source_supervision
        ) or list(progress_outcomes[: len(source_outcomes)]) != list(source_outcomes):
            raise ValueError("S5 continuation source registry does not reconstruct")
        prior_supervision = continuation.get("prior_supervision_sha256")
        prior_outcome = continuation.get("prior_root_outcome_sha256")
        if (
            prior_supervision is not None
            and prior_supervision not in supervision_hashes
        ):
            raise ValueError("S5 continuation cites an unavailable supervision record")
        if prior_outcome is not None and prior_outcome not in root_outcome_hashes:
            raise ValueError("S5 continuation cites an unavailable root outcome")
        if prior_supervision != (
            source_supervision[-1]["sha256"] if source_supervision else None
        ) or prior_outcome != (
            source_outcomes[-1]["sha256"] if source_outcomes else None
        ):
            raise ValueError(
                "S5 continuation does not cite its terminal source records"
            )
    # Reaudit each available shard against the frozen outer plan and archive chain.
    outer = _outer()
    # Audit even incomplete segments before reporting a cross-version partial run.
    # The prefix registry check binds old windows; full validation preserves the
    # acceptance, identity, and physical-state checks on every appended archive.
    if transition is not None:
        for checkpoint_path in sorted(output_root.glob("shard-*/checkpoint.json")):
            checkpoint = _mapping(
                json.loads(checkpoint_path.read_text()), "S5 checkpoint"
            )
            verify_shard_artifacts(
                checkpoint_path.parent,
                shard=shard_entry(str(checkpoint["shard_id"]))[1],
                outer=outer,
                expected_execution_registry_sha256=_annual_registry_sha256(),
                allowed_execution_modes=("annual",),
            )
    summaries: list[Mapping[str, object]] = []
    artifacts: dict[str, object] = {}
    for shard_id in ANNUAL_SHARD_IDS:
        directory = output_root / f"shard-{int(shard_id[-3:]):03d}"
        worker_path = directory / "shard-result.json"
        if not worker_path.is_file():
            continue
        worker = _mapping(json.loads(worker_path.read_text()), "S5 worker result")
        _, shard = shard_entry(shard_id)
        reconstructed = audit_shard(
            directory,
            shard=shard,
            outer=outer,
            expected_execution_registry_sha256=_annual_registry_sha256(),
            allowed_execution_modes=("annual",),
        )
        if any(worker.get(name) != value for name, value in reconstructed.items()):
            raise ValueError("S5 worker result differs from independent audit")
        worker_context = _mapping(
            worker.get("execution_context"), "worker execution context"
        )
        if (
            not any(
                worker_context == pair_context for pair_context, _ in execution_pairs
            )
            or worker.get("execution_mode") != "annual"
            or not worker_source_matches(directory, worker, worker_context, transition)
        ):
            raise ValueError("S5 worker execution provenance or mode mismatch")
        verify_shard_artifacts(
            directory,
            shard=shard,
            outer=outer,
            expected_execution_registry_sha256=_annual_registry_sha256(),
            allowed_execution_modes=("annual",),
        )
        for record in supervision:
            embedded = _mapping(record.get("worker_results"), "embedded workers").get(
                shard_id
            )
            if embedded is not None and embedded != worker:
                raise ValueError("S5 supervision embeds a different worker result")
        # Merge audit summaries, not worker payloads augmented with process metadata.
        summaries.append(reconstructed)
        artifacts[shard_id] = {
            "worker_result_sha256": sha256_path(worker_path),
            "checkpoint_sha256": reconstructed["checkpoint_sha256"],
            "window_chain_sha256": reconstructed["window_chain_sha256"],
        }
    completed_ids = [str(item["shard_id"]) for item in summaries]
    expected_completed = cast(Sequence[str], progress["completed_shards"])
    if progress.get("classification") == "accepted":
        if completed_ids != list(expected_completed):
            raise ValueError("S5 completed-shard root registry mismatch")
    elif not set(expected_completed) <= set(completed_ids):
        raise ValueError("S5 partial root claims an unavailable completed shard")
    complete = completed_ids == list(ANNUAL_SHARD_IDS)
    # Completed shards must respect the frozen wave order, including a partial pair.
    next_wave = progress.get("next_wave")
    if not isinstance(next_wave, int) or not 0 <= next_wave <= len(ANNUAL_WAVES):
        raise ValueError("S5 root next-wave coordinate is invalid")
    required_prefix = {
        shard_id for wave in ANNUAL_WAVES[:next_wave] for shard_id in wave
    }
    allowed = required_prefix | (
        set(ANNUAL_WAVES[next_wave]) if next_wave < len(ANNUAL_WAVES) else set()
    )
    if not required_prefix <= set(completed_ids) or not set(completed_ids) <= allowed:
        raise ValueError("S5 completed shards violate the frozen wave order")
    # Rebuild the annual merge only with full coverage, then check final publication.
    merged = (
        merge_shard_summaries(
            summaries,
            registry_shards=[shard_entry(item)[1] for item in ANNUAL_SHARD_IDS],
            expected_execution_registry_sha256=_annual_registry_sha256(),
            allowed_execution_source_fingerprints=(
                tuple(
                    str(pair_context["source_fingerprint"])
                    for pair_context, _ in execution_pairs
                )
                if transition is not None
                else None
            ),
        )
        if complete
        else None
    )
    if complete:
        merged_path = output_root / "merged-result.json"
        run_path = output_root / "run-result.json"
        merged_identity = _mapping(progress.get("merged_result"), "S5 merged identity")
        if (
            not merged_path.is_file()
            or not run_path.is_file()
            or merged_identity.get("path") != merged_path.name
            or merged_identity.get("sha256") != sha256_path(merged_path)
            or json.loads(merged_path.read_text()) != merged
            or json.loads(run_path.read_text()) != progress
            or progress.get("classification") != "accepted"
            or next_wave != len(ANNUAL_WAVES)
        ):
            raise ValueError("complete S5 root artifacts do not reconstruct")
    # A verified zero-exit peer still counts when its wave's other worker failed.
    # Require per-shard supervision coverage rather than only accepted wave labels.
    accepted_coverage = {
        wave_index: {
            shard_id
            for record in supervision
            if record["wave_index"] == wave_index
            for shard_id in _mapping(record["worker_results"], "completed workers")
            if shard_id in completed_ids
        }
        for wave_index in range(len(ANNUAL_WAVES))
    }
    lifecycle_complete = all(
        accepted_coverage[index] == set(wave) for index, wave in enumerate(ANNUAL_WAVES)
    )
    # Later progress after abnormal outcomes needs explicit reviewed authorization.
    abnormal = [
        record for record in supervision if record["classification"] != "accepted"
    ]
    for record in abnormal:
        record_index = supervision.index(record)
        if any(
            later["classification"] == "accepted"
            for later in supervision[record_index + 1 :]
        ) and not any(
            item.get("next_wave") == record["wave_index"]
            and item.get("prior_supervision_sha256")
            == sha256_path(supervision_paths[record_index])
            for item in continuations
        ):
            raise ValueError(
                "S5 execution advanced after an unauthorized abnormal outcome"
            )
    cited_root_outcomes = {
        item.get("prior_root_outcome_sha256") for item in continuations
    }
    root_registry = cast(
        Sequence[Mapping[str, object]], progress.get("root_outcomes", ())
    )
    uncited_root_outcomes = [
        item for item in root_registry if item.get("sha256") not in cited_root_outcomes
    ]
    if (progress.get("classification") == "accepted" and uncited_root_outcomes) or len(
        uncited_root_outcomes
    ) > 1:
        raise ValueError("S5 execution advanced after an unreviewed root outcome")
    # Aggregate observed RSS and elapsed wave time, not summed parallel worker time.
    resource_summary = {
        "maximum_worker_current_rss_mib": max(
            (
                float(cast(float, value))
                for record in supervision
                for value in _mapping(
                    record["peak_worker_rss_mib"], "worker peaks"
                ).values()
            ),
            default=0.0,
        ),
        "maximum_aggregate_current_rss_mib": max(
            (
                float(cast(float, record["peak_aggregate_rss_mib"]))
                for record in supervision
            ),
            default=0.0,
        ),
        "maximum_supervisor_current_rss_mib": max(
            (
                float(cast(float, sample["supervisor_current_rss_mib"]))
                for record in supervision
                for sample in cast(
                    Sequence[Mapping[str, object]], record["resource_samples"]
                )
            ),
            default=0.0,
        ),
        "total_supervisor_critical_path_seconds": sum(
            float(cast(float, record["elapsed_critical_path_seconds"]))
            for record in supervision
        ),
    }
    # Keep scientific completion, lifecycle coverage, and analyzer identity explicit.
    result = {
        "schema_version": SCHEMA_VERSION,
        "classification": "accepted" if complete and lifecycle_complete else "partial",
        "execution_complete": complete and lifecycle_complete,
        "accepted_for_s6": bool(
            complete
            and lifecycle_complete
            and merged is not None
            and merged["execution_complete"] is True
            and merged["all_independent_audits_agree"] is True
        ),
        "manifest_sha256": EXPECTED_MANIFEST_SHA256,
        "execution_context": context,
        "initial_execution_context": initial_context,
        "source_version_transition": _transition_summary(output_root, transition),
        "authority": authority,
        "shard_artifacts": artifacts,
        "supervision_artifacts": [
            {"path": path.name, "sha256": sha256_path(path)}
            for path in supervision_paths
        ],
        "wave_lifecycle": [
            {
                "wave_index": record["wave_index"],
                "requested_shards": record["requested_shards"],
                "classification": record["classification"],
                "maximum_observed_concurrency": record["maximum_observed_concurrency"],
                "peak_worker_rss_mib": record["peak_worker_rss_mib"],
                "peak_aggregate_rss_mib": record["peak_aggregate_rss_mib"],
                "elapsed_critical_path_seconds": record[
                    "elapsed_critical_path_seconds"
                ],
                "resource_triggers": record["resource_triggers"],
            }
            for record in supervision
        ],
        "reviewed_continuations": progress["reviewed_continuations"],
        "root_outcomes": progress.get("root_outcomes", []),
        "root_outcome_classifications": [
            item["classification"] for item in root_outcomes
        ],
        "completed_shards": completed_ids,
        "merged_result": merged,
        "resource_summary": resource_summary,
        "analysis_context": analysis_context(),
    }
    return {**result, "analysis_sha256": object_sha256(result)}


def _transition_summary(
    output_root: Path, transition: Mapping[str, object] | None
) -> Mapping[str, object] | None:
    """Retain the reviewed source chain without flattening its distinct events."""
    if transition is None:
        return None
    if transition.get("classification") == "applied_s5_speculative_policy_continuation":
        from experiments.case118_annual_hierarchy.s5_speculative_continuation import (
            RECORD_NAME as SPECULATIVE_RECORD,
        )

        contract = _mapping(transition["contract"], "speculative contract")
        candidates = [
            output_root / SPECULATIVE_RECORD,
            *sorted(output_root.glob("speculative-policy-source-transition-*.json")),
        ]
        matches = [
            path
            for path in candidates
            if path.is_file() and json.loads(path.read_text()) == transition
        ]
        if len(matches) != 1:
            raise ValueError(
                "speculative source summary lacks its exact retained record"
            )
        path = matches[0]
        return {
            "classification": transition["classification"],
            "latest_path": path.name,
            "latest_sha256": sha256_path(path),
            "contract_sha256": transition["contract_sha256"],
            "first_affected_interval": contract["first_affected_interval"],
            "policy": contract["policy"],
            "predecessor": _transition_summary(
                output_root,
                _mapping(transition["predecessor_transition"], "predecessor"),
            ),
        }
    if transition.get("classification") in {
        "applied_s5_window_retry_source_continuation",
        "applied_s5_recovery_audit_source_continuation",
    }:
        from experiments.case118_annual_hierarchy.s5_retry_transition import (
            AUDIT_SPEC,
            RECORD_NAME as RETRY_RECORD_NAME,
        )

        record_name = (
            AUDIT_SPEC.record_name
            if transition["classification"] == AUDIT_SPEC.record_classification
            else RETRY_RECORD_NAME
        )
        return {
            "classification": transition["classification"],
            "latest_path": record_name,
            "latest_sha256": sha256_path(output_root / record_name),
            "contract": transition["contract"],
            "published_utc": transition["published_utc"],
            "predecessor": _transition_summary(
                output_root,
                _mapping(
                    transition.get("predecessor_transition"), "predecessor transition"
                ),
            ),
        }
    if transition.get("classification") == (
        "applied_s5_interval_2448_operator_intervention"
    ):
        from experiments.case118_annual_hierarchy.s5_operator_intervention import (
            INTERVAL,
            RECORD_NAME as INTERVENTION_RECORD_NAME,
        )

        predecessor = _mapping(
            transition.get("predecessor_transition"), "predecessor transition"
        )
        base_contract = _mapping(
            predecessor.get("contract"), "base transition contract"
        )
        stopping = _mapping(
            base_contract.get("trusted_stopping_point"), "base stopping point"
        )
        return {
            "classification": transition["classification"],
            "latest_path": INTERVENTION_RECORD_NAME,
            "latest_sha256": sha256_path(output_root / INTERVENTION_RECORD_NAME),
            "base_path": RECORD_NAME,
            "base_sha256": sha256_path(output_root / RECORD_NAME),
            "contract": transition["contract"],
            "published_utc": transition["published_utc"],
            "preserved_intervals_before_base_transition": stopping[
                "completed_intervals"
            ],
            "operator_intervention_interval": INTERVAL,
            "operator_intervention_window_sha256": _mapping(
                transition.get("intervention_window"), "intervention window"
            )["sha256"],
        }
    contract = _mapping(transition.get("contract"), "source transition contract")
    stopping = _mapping(contract.get("trusted_stopping_point"), "stopping point")
    return {
        "classification": "applied_s5_source_version_transition",
        "path": RECORD_NAME,
        "sha256": sha256_path(output_root / RECORD_NAME),
        "contract": contract,
        "published_utc": transition["published_utc"],
        "preserved_intervals": stopping["completed_intervals"],
    }


def promote_completed(
    path: Path,
    result: Mapping[str, object],
    *,
    output_root: Path = DEFAULT_OUTPUT_ROOT,
    authority_path: Path = DEFAULT_NUMERICAL_AUTHORITY_PATH,
) -> None:
    if path.exists():
        raise FileExistsError(f"immutable S5 result already exists: {path}")
    base = {key: value for key, value in result.items() if key != "analysis_sha256"}
    if (
        result.get("analysis_sha256") != object_sha256(base)
        or result.get("classification") != "accepted"
        or result.get("execution_complete") is not True
        or result.get("accepted_for_s6") is not True
    ):
        raise ValueError("partial or unaccepted S5 analysis cannot be promoted")
    reconstructed = analyze_s5(output_root, authority_path=authority_path)
    if result != reconstructed:
        raise ValueError("S5 promotion payload differs from independent reconstruction")
    atomic_immutable_json(path, result)
    if path.read_bytes() != canonical_json(result):
        raise RuntimeError("promoted S5 result is not canonical")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument(
        "--authority", type=Path, default=DEFAULT_NUMERICAL_AUTHORITY_PATH
    )
    parser.add_argument("--promote", type=Path)
    args = parser.parse_args()
    result = analyze_s5(args.output_root.resolve(), authority_path=args.authority)
    if args.promote is not None:
        promote_completed(
            args.promote,
            result,
            output_root=args.output_root.resolve(),
            authority_path=args.authority,
        )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
